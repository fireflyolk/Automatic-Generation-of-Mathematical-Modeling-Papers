"""Accessibility and responsive UI contracts run outside the collecting Qt process."""
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(r'F:\Codex另一个工作的地方\tmp\mathmodel-paper-studio-run-sidebar\ui-accessibility')
_CHILD_FLAG = 'MATHMODEL_FLUENT_ACCESSIBILITY_CHILD'


if os.environ.get(_CHILD_FLAG) == '1':
    from types import SimpleNamespace

    from PySide6.QtCore import QPoint, QRect, QThreadPool, Qt
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication, QScrollArea
    from qfluentwidgets import FluentSystemColor, Theme

    from mathmodel_paper_studio.agents.preflight import Provider
    from mathmodel_paper_studio.bootstrap.paths import AppPaths
    from mathmodel_paper_studio.domain.models import Stage
    from mathmodel_paper_studio.projects.project_service import ProjectService
    from mathmodel_paper_studio.storage.database import Database
    from mathmodel_paper_studio.storage.repositories import ProjectRepository
    from mathmodel_paper_studio.ui.main_window import MainWindow
    from mathmodel_paper_studio.ui.project_wizard import ProjectWizard
    from mathmodel_paper_studio.ui.run_review import RunReviewDialog, RunReviewModel
    from mathmodel_paper_studio.ui.run_panel import RunPanel
    from mathmodel_paper_studio.ui.template_mapping_view import TemplateMappingView
    from tests.fixtures.docx.fixture_factory import write_fixture
    import unittest

    APP = None

    class _DeferredPool:
        def __init__(self):
            self.workers = []

        def start(self, worker):
            self.workers.append(worker)

    class _Projects:
        def list_all(self):
            return ()

        def get(self, _project_id):
            return None

    class FluentAccessibilityResponsiveTests(unittest.TestCase):
        @classmethod
        def setUpClass(cls):
            global APP
            ROOT.mkdir(parents=True, exist_ok=True)
            APP = QApplication.instance() or QApplication([])

        @classmethod
        def tearDownClass(cls):
            APP.closeAllWindows()
            APP.processEvents()
            QThreadPool.globalInstance().waitForDone(5000)
            APP.processEvents()

        def setUp(self):
            self.temporary = tempfile.TemporaryDirectory(dir=ROOT)
            self.addCleanup(self.temporary.cleanup)
            self.base = Path(self.temporary.name)
            self.paths = AppPaths(self.base / 'app')
            self.paths.ensure_directories()
            database = Database(self.paths)
            database.migrate()
            self.repository = ProjectRepository(database)
            self.service = ProjectService(self.paths, self.repository)
            self.csv = self.base / 'observations.csv'
            self.csv.write_text('time,value\n1,2\n', encoding='utf-8')

        def test_project_wizard_keyboard_order_and_focus_are_predictable(self):
            wizard = ProjectWizard(self.service)
            self.addCleanup(wizard.close)
            wizard.resize(520, 420)
            wizard.title_input.setText('键盘导航项目')
            wizard.problem_input.setPlainText('检验键盘导航和可见状态。')
            wizard.set_source_files((self.csv,))
            wizard.file_list.setCurrentRow(0)
            wizard.show()
            wizard.activateWindow()
            QTest.qWait(20)

            order = (
                wizard.title_input, wizard.problem_input,
                wizard.import_problem_docx_button,
                wizard.add_source_button, wizard.add_template_button,
                wizard.file_list, wizard.remove_source_button,
                wizard.cancel_button, wizard.create_button,
            )
            self.assertTrue(all(widget.focusPolicy() != Qt.FocusPolicy.NoFocus for widget in order))
            order[0].setFocus()
            for expected in order[1:]:
                QTest.keyClick(order[0] if expected is order[1] else previous,
                               Qt.Key.Key_Tab)
                self.assertTrue(expected.hasFocus(), f'键盘 Tab 未到达 {expected.accessibleName() or expected.objectName()}')
                previous = expected
            self.assertTrue(wizard.create_button.isVisible())
            self.assertTrue(wizard.remove_source_button.isEnabled())

        def test_wizard_empty_remove_and_loading_feedback_are_accessible(self):
            pool = _DeferredPool()
            wizard = ProjectWizard(self.service, thread_pool=pool)
            self.addCleanup(wizard.close)
            wizard.resize(520, 420)
            wizard.show()
            QTest.qWait(20)

            self.assertFalse(wizard.remove_source_button.isEnabled())
            self.assertEqual('项目导入状态', wizard.error_label.accessibleName())
            self.assertEqual('error', wizard.error_label.property('state'))
            self.assertTrue(wizard.error_label.isVisible())

            wizard.title_input.setText('导入状态项目')
            wizard.problem_input.setPlainText('检查后台导入的状态反馈。')
            wizard.set_source_files((self.csv,))
            wizard.file_list.setCurrentRow(0)
            self.assertTrue(wizard.remove_source_button.isEnabled())
            QTest.mouseClick(wizard.create_button, Qt.MouseButton.LeftButton)

            self.assertTrue(wizard.import_progress.isVisible())
            self.assertTrue(wizard.progress_label.isVisible())
            self.assertEqual('loading', wizard.error_label.property('state'))
            self.assertEqual('正在导入，请稍候', wizard.create_button.accessibleName())
            self.assertFalse(wizard.create_button.isEnabled())
            self.assertFalse(wizard.cancel_button.isEnabled())
            self.assertFalse(wizard.add_source_button.isEnabled())
            self.assertFalse(wizard.remove_source_button.isEnabled())
            self.assertTrue(pool.workers)
            worker = pool.workers[0]
            worker.cancel()
            worker.signals.failed.emit('测试结束清理')
            worker.signals.finished.emit()
            APP.processEvents()

        def test_template_error_loading_and_success_feedback_expose_state(self):
            root = self.base / 'project'
            inputs = root / '00_inputs'
            inputs.mkdir(parents=True)
            (root / '70_review').mkdir()
            template = write_fixture(inputs / 'template.docx')
            outside = write_fixture(self.base / 'outside.docx')
            pool = _DeferredPool()
            view = TemplateMappingView(thread_pool=pool)
            self.addCleanup(view.close)
            view.set_project_root(root)
            view.show()

            view.set_template_path(outside)
            self.assertTrue(view.template_status_label.isVisible())
            self.assertEqual('error', view.template_status_label.property('state'))
            self.assertEqual('模板检查状态', view.template_status_label.accessibleName())
            self.assertIn('范围无效', view.template_status_label.text())
            self.assertIn(FluentSystemColor.CRITICAL_FOREGROUND.color(Theme.LIGHT).name(),
                          view.template_status_label.property('lightCustomQss'))
            self.assertIn(FluentSystemColor.CRITICAL_FOREGROUND.color(Theme.DARK).name(),
                          view.template_status_label.property('darkCustomQss'))

            view.set_template_path(template)
            self.assertTrue(view.operation_progress.isVisible())
            self.assertEqual('loading', view.template_status_label.property('state'))
            self.assertFalse(view.choose_button.isEnabled())
            self.assertRegex(view.template_status_label.property('lightCustomQss'),
                             r'color: #[0-9a-fA-F]{6}')
            self.assertRegex(view.template_status_label.property('darkCustomQss'),
                             r'color: #[0-9a-fA-F]{6}')
            pool.workers.pop(0).run()
            APP.processEvents()
            self.assertEqual('success', view.template_status_label.property('state'))
            self.assertIn('检查完成', view.template_status_label.text())
            self.assertIn(FluentSystemColor.SUCCESS_FOREGROUND.color(Theme.LIGHT).name(),
                          view.template_status_label.property('lightCustomQss'))
            self.assertIn(FluentSystemColor.SUCCESS_FOREGROUND.color(Theme.DARK).name(),
                          view.template_status_label.property('darkCustomQss'))
            self.assertTrue(view.choose_button.isEnabled())

            anchor = next(item for item in view.inspection.anchors if item.kind == 'heading')
            combo = view._combos['abstract']
            combo.setCurrentIndex(combo.findData(anchor.anchor_id))
            self.assertTrue(view.save_button.isEnabled())
            QTest.mouseClick(view.save_button, Qt.MouseButton.LeftButton)
            self.assertEqual('loading', view.save_status_label.property('state'))
            self.assertTrue(view.operation_progress.isVisible())
            self.assertFalse(view.save_button.isEnabled())
            self.assertTrue(all(not item.isEnabled() for item in view._combos.values()))
            pool.workers.pop(0).run()
            APP.processEvents()
            self.assertEqual('success', view.save_status_label.property('state'),
                             view.save_status_label.text())
            self.assertIn('已保存', view.save_status_label.text())
            self.assertTrue(view.save_button.isEnabled())
            self.assertTrue(all(item.isEnabled() for item in view._combos.values()))

        def test_1024x720_window_and_review_actions_have_no_horizontal_overflow(self):
            window = MainWindow(
                self.paths, project_repository=_Projects(),
                project_service=self.service, workflow_engine=object(),
                run_repository=object(), thread_pool=_DeferredPool(),
            )
            self.addCleanup(window.close)
            window.resize(1024, 720)
            window.show()
            QTest.qWait(30)
            self.assertEqual((1024, 720), (window.width(), window.height()))
            self.assertLessEqual(window.minimumSizeHint().width(), 1024)

            for page in (window.project_page, window.template_view,
                         window.run_panel, window.settings_view):
                window.workspace.setCurrentWidget(page)
                APP.processEvents()
                self.assertLessEqual(page.width(), window.stackedWidget.width())

            window.workspace.setCurrentWidget(window.settings_view)
            APP.processEvents()
            settings_scroll = window.settings_view.findChild(
                QScrollArea, 'settingsScrollArea',
            )
            self.assertIsNotNone(settings_scroll)
            self.assertEqual(0, settings_scroll.horizontalScrollBar().maximum())
            settings_scroll.verticalScrollBar().setValue(
                settings_scroll.verticalScrollBar().maximum(),
            )
            APP.processEvents()
            settings_viewport = settings_scroll.viewport()
            codex_action_rect = QRect(
                window.settings_view.codex_test_button.mapTo(settings_viewport, QPoint(0, 0)),
                window.settings_view.codex_test_button.size(),
            )
            self.assertTrue(settings_viewport.rect().contains(codex_action_rect))

            window.workspace.setCurrentWidget(window.project_page)
            APP.processEvents()
            self.assertEqual(0, window.project_details_scroll.horizontalScrollBar().maximum())
            self.assertTrue(window.new_project_button.isVisible())
            window.workspace.setCurrentWidget(window.template_view)
            APP.processEvents()
            self.assertEqual(0, window.template_view.page_scroll_area.horizontalScrollBar().maximum())
            window.template_view.page_scroll_area.verticalScrollBar().setValue(
                window.template_view.page_scroll_area.verticalScrollBar().maximum())
            APP.processEvents()
            self.assertTrue(window.template_view.save_button.isVisible())
            viewport = window.template_view.page_scroll_area.viewport()
            button_rect = QRect(window.template_view.save_button.mapTo(viewport, QPoint(0, 0)),
                                window.template_view.save_button.size())
            self.assertTrue(viewport.rect().contains(button_rect),
                            f'模板保存主操作被裁切：{button_rect!r} / {viewport.rect()!r}')
            window.workspace.setCurrentWidget(window.run_panel)
            APP.processEvents()
            for button in (window.run_panel.artifact_button,
                           window.run_panel.cancel_button,
                           window.run_panel.resume_button):
                self.assertTrue(button.isVisible())
                rect = QRect(button.mapTo(window.run_panel, QPoint(0, 0)), button.size())
                self.assertTrue(window.run_panel.rect().contains(rect),
                                f'{button.accessibleName()} 超出运行页：{rect!r}')

            project_root = self.base / 'review-project'
            project_root.mkdir()
            readable = project_root / 'input.csv'
            readable.write_text('x\n1\n', encoding='utf-8')
            writable = project_root / 'output'
            writable.mkdir()
            status = window.settings_view.selected_status
            model = RunReviewModel(
                status, project_root, (readable,), (writable,),
                (Stage.PREFLIGHT,), True, '文件范围已通过本次检查。',
            )
            dialog = RunReviewDialog(model, window)
            self.addCleanup(dialog.close)
            dialog.resize(dialog.minimumWidth(), dialog.minimumHeight())
            dialog.show()
            dialog.activateWindow()
            self.assertTrue(QTest.qWaitForWindowActive(dialog))
            APP.processEvents()
            self.assertEqual(0, dialog.summary_view.horizontalScrollBar().maximum())
            self.assertEqual(Qt.FocusPolicy.StrongFocus, dialog.start_button.focusPolicy())
            dialog.edit_scope_button.setFocus()
            self.assertTrue(dialog.edit_scope_button.hasFocus())
            self.assertTrue(dialog.change_agent_button.isVisible())
            QTest.keyClick(dialog.edit_scope_button, Qt.Key.Key_Tab)
            self.assertTrue(dialog.change_agent_button.hasFocus())
            QTest.keyClick(dialog.change_agent_button, Qt.Key.Key_Tab)
            self.assertTrue(dialog.cancel_button.hasFocus())
            for button in (dialog.edit_scope_button, dialog.change_agent_button,
                           dialog.cancel_button, dialog.start_button):
                rect = QRect(button.mapTo(dialog, QPoint(0, 0)), button.size())
                self.assertTrue(dialog.rect().contains(rect),
                                f'{button.accessibleName()} 超出审核对话框：{rect!r}')

        def test_run_panel_keeps_actions_fixed_while_details_scroll(self):
            panel = RunPanel(thread_pool=_DeferredPool())
            self.addCleanup(panel.close)
            panel.show()
            panel.setGeometry(20, 20, 720, 420)
            QTest.qWait(20)

            scroll = panel.content_scroll_area
            self.assertTrue(scroll.isVisible())
            self.assertEqual(0, scroll.horizontalScrollBar().maximum())
            self.assertGreater(scroll.verticalScrollBar().maximum(), 0)

            actions = (panel.artifact_button, panel.cancel_button, panel.resume_button)
            action_rects = tuple(
                QRect(button.mapTo(panel, QPoint(0, 0)), button.size())
                for button in actions
            )
            self.assertTrue(all(button.isVisible() for button in actions))
            self.assertTrue(all(panel.rect().contains(rect) for rect in action_rects))
            footer_y = min(rect.top() for rect in action_rects)
            self.assertGreaterEqual(footer_y, scroll.geometry().bottom())

            scroll.verticalScrollBar().setValue(scroll.verticalScrollBar().maximum())
            APP.processEvents()
            after_scroll = tuple(
                QRect(button.mapTo(panel, QPoint(0, 0)), button.size())
                for button in actions
            )
            self.assertEqual(action_rects, after_scroll)

        def test_sidebar_collapses_below_minimum_width_without_hiding_actions(self):
            panel = RunPanel(thread_pool=_DeferredPool())
            self.addCleanup(panel.close)
            panel.resize(720, 420)
            panel.show()
            QTest.qWait(20)

            self.assertFalse(panel.run_timeline.isVisible())
            self.assertTrue(panel.timeline_toggle_button.isVisible())
            actions = (panel.artifact_button, panel.cancel_button, panel.resume_button)
            self.assertTrue(all(button.isVisible() for button in actions))
            self.assertTrue(all(
                panel.rect().contains(QRect(button.mapTo(panel, QPoint(0, 0)), button.size()))
                for button in actions
            ))

            QTest.mouseClick(panel.timeline_toggle_button, Qt.MouseButton.LeftButton)
            QTest.qWait(20)
            self.assertTrue(panel.run_timeline.isVisible())

else:
    class FluentAccessibilityResponsiveProcessTests(unittest.TestCase):
        def test_accessibility_and_responsive_contracts_in_isolated_qt_process(self):
            ROOT.mkdir(parents=True, exist_ok=True)
            environment = dict(os.environ)
            environment.update({
                _CHILD_FLAG: '1', 'QT_QPA_PLATFORM': 'offscreen',
                'PYTHONPATH': os.pathsep.join((
                    str(Path(__file__).resolve().parents[3]),
                    str(Path(__file__).resolve().parents[3] / 'src'),
                )),
                'TEMP': str(ROOT), 'TMP': str(ROOT), 'TMPDIR': str(ROOT),
                'PYTHONPYCACHEPREFIX': str(ROOT / 'pycache'),
                'PYTHONIOENCODING': 'utf-8', 'XDG_CACHE_HOME': str(ROOT / 'qt-cache'),
                'XDG_CONFIG_HOME': str(ROOT / 'xdg-config'), 'APPDATA': str(ROOT / 'appdata'),
                'LOCALAPPDATA': str(ROOT / 'localappdata'), 'PIP_CACHE_DIR': str(ROOT / 'pip-cache'),
            })
            test_names = (
                'test_project_wizard_keyboard_order_and_focus_are_predictable',
                'test_wizard_empty_remove_and_loading_feedback_are_accessible',
                'test_template_error_loading_and_success_feedback_expose_state',
                'test_1024x720_window_and_review_actions_have_no_horizontal_overflow',
                'test_run_panel_keeps_actions_fixed_while_details_scroll',
                'test_sidebar_collapses_below_minimum_width_without_hiding_actions',
            )
            for test_name in test_names:
                with self.subTest(test_name=test_name):
                    result = subprocess.run(
                        [sys.executable, str(Path(__file__).resolve()),
                         f'FluentAccessibilityResponsiveTests.{test_name}'],
                        cwd=Path(__file__).resolve().parents[3], env=environment,
                        capture_output=True, text=True, encoding='utf-8', errors='replace',
                        timeout=90, check=False,
                    )
                    self.assertEqual(
                        0, result.returncode, result.stdout + result.stderr,
                    )
                    self.assertIn('Ran 1 test', result.stderr)


if __name__ == '__main__':
    unittest.main()
