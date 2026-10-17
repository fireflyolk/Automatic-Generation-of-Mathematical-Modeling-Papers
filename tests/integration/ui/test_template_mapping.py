"""Run template mapping UI contracts outside the collecting Qt process."""
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(r'F:\Codex另一个工作的地方\tmp\mathmodel-paper-studio-fluent-20261017\template-mapping')
_CHILD_FLAG = 'MATHMODEL_TEMPLATE_MAPPING_CHILD'


if os.environ.get(_CHILD_FLAG) == '1':
    import hashlib
    from unittest.mock import patch

    from PySide6.QtCore import Qt
    from PySide6.QtTest import QSignalSpy, QTest
    from PySide6.QtWidgets import QApplication, QFileDialog, QScrollArea

    from mathmodel_paper_studio.paper.template_inspector import (
        UnsupportedTemplateError,
        inspect_template,
    )
    from mathmodel_paper_studio.paper.template_mapping import TemplateMapping
    from mathmodel_paper_studio.ui.template_mapping_view import TemplateMappingView
    from tests.fixtures.docx.fixture_factory import (
        DOCUMENT_XML, unsupported_text_box, write_fixture,
    )

    APP = None

    class _ImmediatePool:
        """Execute the worker inline while retaining the production signal path."""

        def start(self, worker):
            worker.run()

    class _DeferredPool:
        """Keep worker completion under test control to exercise project switches."""

        def __init__(self):
            self.workers = []

        def start(self, worker):
            self.workers.append(worker)

    class TemplateMappingViewTests(unittest.TestCase):
        @classmethod
        def setUpClass(cls):
            global APP
            ROOT.mkdir(parents=True, exist_ok=True)
            APP = QApplication.instance() or QApplication([])

        def setUp(self):
            self.temporary = tempfile.TemporaryDirectory(dir=ROOT)
            self.addCleanup(self.temporary.cleanup)
            base = Path(self.temporary.name)
            self.project_root = base / 'project'
            self.inputs = self.project_root / '00_inputs'
            (self.project_root / '70_review').mkdir(parents=True)
            self.inputs.mkdir()
            self.template = write_fixture(self.inputs / 'template.docx')
            self.view = TemplateMappingView(thread_pool=_ImmediatePool())
            self.view.set_project_root(self.project_root)
            self.addCleanup(self.view.close)

        def test_template_selection_uses_project_guard_and_never_changes_source(self):
            original_hash = self._digest(self.template)
            failed = QSignalSpy(self.view.operation_failed)
            outside = Path(self.temporary.name) / 'outside.docx'
            write_fixture(outside)

            self.view.set_template_path(outside)

            self.assertEqual(1, failed.count())
            self.assertIn('项目', self.view.template_status_label.text())
            self.assertIsNone(self.view.inspection)

            with patch.object(QFileDialog, 'getOpenFileName',
                              return_value=(str(self.template), 'Word 文档 (*.docx)')):
                QTest.mouseClick(self.view.choose_button, Qt.MouseButton.LeftButton)

            self.assertEqual(self.template, self.view.template_path)
            self.assertIsNotNone(self.view.inspection)
            self.assertIn('检查完成', self.view.template_status_label.text())
            self.assertEqual(original_hash, self._digest(self.template))

        def test_mapping_uses_combo_current_data_and_saves_with_feedback(self):
            original_hash = self._digest(self.template)
            self.view.set_template_path(self.template)
            self.assertIsNotNone(self.view.inspection)
            self.assertIsInstance(self.view.mapping_scroll_area, QScrollArea)

            abstract = next(item for item in self.view.inspection.anchors
                            if item.kind == 'heading' and item.label == '摘要')
            combo = self.view._combos['abstract']
            combo.setCurrentIndex(combo.findData(abstract.anchor_id))

            self.assertEqual(abstract.anchor_id, combo.currentData())
            self.assertEqual(abstract.anchor_id, self.view._selection()['abstract'])
            self.assertTrue(self.view.save_button.isEnabled())

            saved = QSignalSpy(self.view.mapping_saved)
            QTest.mouseClick(self.view.save_button, Qt.MouseButton.LeftButton)

            self.assertEqual(1, saved.count())
            mapping_path = saved.at(0)[0]
            mapping = TemplateMapping.load(self.project_root, version=1)
            self.assertEqual(mapping_path.name, 'template-mapping-v0001.json')
            self.assertEqual(abstract.anchor_id, mapping.bindings[0].anchor_id)
            self.assertIn('已保存', self.view.save_status_label.text())
            self.assertEqual(original_hash, self._digest(self.template))

        def test_unsupported_structure_has_a_clear_explanation_without_source_mutation(self):
            unsupported = write_fixture(
                self.inputs / 'unsupported.docx',
                document_xml=unsupported_text_box(),
            )
            original_hash = self._digest(unsupported)

            self.view.set_template_path(unsupported)

            self.assertIn('不支持', self.view.unsupported_label.text())
            self.assertIn('文字框', self.view.unsupported_label.text())
            self.assertIn('结构暂不支持', self.view.template_status_label.text())
            self.assertFalse(self.view.save_button.isEnabled())
            self.assertEqual(original_hash, self._digest(unsupported))
            with self.assertRaises(UnsupportedTemplateError):
                inspect_template(unsupported)

        def test_switching_projects_discards_stale_inspection_and_queues_new_template(self):
            pool = _DeferredPool()
            view = TemplateMappingView(thread_pool=pool)
            self.addCleanup(view.close)
            first_root, first_template = self._project_fixture('first-project', 'first.docx')
            second_root, second_template = self._project_fixture('second-project', 'second.docx')

            view.set_project_root(first_root)
            view.set_template_path(first_template)
            stale_worker = pool.workers[0]
            first_inspection = inspect_template(first_template)
            self.assertEqual({}, view._combos)
            self.assertIsNone(view.inspection)

            view.set_project_root(second_root)
            self.assertIsNone(view.template_path)
            self.assertIsNone(view.inspection)
            self.assertEqual({}, view._combos)
            self.assertFalse(view.save_button.isEnabled())
            view.set_template_path(second_template)

            self.assertEqual(second_template, view.template_path)
            self.assertIsNone(view.inspection)
            self.assertFalse(view.save_button.isEnabled())
            self.assertIn('等待', view.template_status_label.text())
            stale_worker.run()

            self.assertIsNone(view.inspection)
            self.assertEqual(second_template, view.template_path)
            self.assertFalse(view.save_button.isEnabled())
            self.assertEqual(2, len(pool.workers))
            pool.workers[1].run()

            self.assertEqual(second_template, view.template_path)
            self.assertEqual(inspect_template(second_template).template_sha256,
                             view.inspection.template_sha256)
            self.assertNotEqual(first_inspection.template_sha256,
                                view.inspection.template_sha256)
            first_anchor = first_inspection.anchors[0]
            second_anchor = view.inspection.anchors[0]
            self.assertNotEqual(first_anchor.anchor_id, second_anchor.anchor_id)
            self.assertGreater(view._combos['abstract'].findData(second_anchor.anchor_id), 0)
            self.assertEqual(-1, view._combos['abstract'].findData(first_anchor.anchor_id))
            self.assertFalse(view.save_button.isEnabled())

        def test_switching_projects_clears_a_completed_mapping_and_disables_save(self):
            first_root, first_template = self._project_fixture('completed-project', 'first.docx')
            second_root, _second_template = self._project_fixture('empty-project', 'second.docx')
            self.view.set_project_root(first_root)
            self.view.set_template_path(first_template)
            self.view._combos['abstract'].setCurrentIndex(1)
            self.assertTrue(self.view.save_button.isEnabled())

            self.view.set_project_root(second_root)

            self.assertIsNone(self.view.template_path)
            self.assertIsNone(self.view.inspection)
            self.assertEqual({}, self.view._combos)
            self.assertEqual(0, self.view.form.rowCount())
            self.assertFalse(self.view.save_button.isEnabled())
            self.assertFalse(self.view.mapping_empty_label.isHidden())
            self.assertTrue(self.view.mapping_scroll_area.isHidden())
            self.assertEqual('尚未选择模板', self.view.template_label.text())

        def test_path_with_dotdot_escape_is_rejected_before_worker_start(self):
            pool = _DeferredPool()
            view = TemplateMappingView(thread_pool=pool)
            self.addCleanup(view.close)
            view.set_project_root(self.project_root)
            outside = write_fixture(Path(self.temporary.name) / 'outside.docx')
            escape = self.inputs / '..' / '..' / outside.name
            self.assertTrue(escape.is_relative_to(self.project_root))

            view.set_template_path(escape)

            self.assertIn('范围无效', view.template_status_label.text())
            self.assertIsNone(view.template_path)
            self.assertEqual([], pool.workers)

        def test_symlink_escape_is_rejected_when_windows_allows_link_creation(self):
            pool = _DeferredPool()
            view = TemplateMappingView(thread_pool=pool)
            self.addCleanup(view.close)
            view.set_project_root(self.project_root)
            outside = write_fixture(Path(self.temporary.name) / 'outside-link-target.docx')
            link = self.inputs / 'linked-template.docx'
            try:
                link.symlink_to(outside)
            except (OSError, NotImplementedError) as exc:
                self.skipTest(f'Windows symlink creation is unavailable: {exc}')

            view.set_template_path(link)

            self.assertIn('范围无效', view.template_status_label.text())
            self.assertIsNone(view.template_path)
            self.assertEqual([], pool.workers)

        def _project_fixture(self, directory, filename):
            project = Path(self.temporary.name) / directory
            inputs = project / '00_inputs'
            inputs.mkdir(parents=True)
            (project / '70_review').mkdir()
            document_xml = DOCUMENT_XML.replace('摘要', f'摘要-{directory}')
            return project, write_fixture(inputs / filename, document_xml=document_xml)

        @staticmethod
        def _digest(path):
            return hashlib.sha256(path.read_bytes()).hexdigest()

else:
    class TemplateMappingViewProcessTests(unittest.TestCase):
        def test_template_mapping_contracts_in_isolated_qt_process(self):
            ROOT.mkdir(parents=True, exist_ok=True)
            environment = dict(os.environ)
            environment.update({
                _CHILD_FLAG: '1',
                'QT_QPA_PLATFORM': 'offscreen',
                'PYTHONPATH': os.pathsep.join((
                    str(Path(__file__).resolve().parents[3]),
                    str(Path(__file__).resolve().parents[3] / 'src'),
                )),
                'TEMP': str(ROOT), 'TMP': str(ROOT), 'TMPDIR': str(ROOT),
                'PYTHONPYCACHEPREFIX': str(ROOT / 'pycache'),
                'PYTHONIOENCODING': 'utf-8',
                'XDG_CACHE_HOME': str(ROOT / 'qt-cache'),
                'PIP_CACHE_DIR': str(ROOT / 'pip-cache'),
            })
            result = subprocess.run(
                [sys.executable, str(Path(__file__).resolve())],
                cwd=Path(__file__).resolve().parents[3],
                env=environment, capture_output=True, text=True,
                encoding='utf-8', errors='replace',
                timeout=30, check=False,
            )
            self.assertEqual(0, result.returncode, result.stdout + result.stderr)
            self.assertIn('Ran 7 tests', result.stderr)


if __name__ == '__main__':
    unittest.main()
