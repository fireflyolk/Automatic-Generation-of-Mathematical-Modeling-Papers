import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from PySide6.QtCore import QThreadPool, Qt
from PySide6.QtGui import QTextCursor
from PySide6.QtTest import QSignalSpy, QTest
from PySide6.QtWidgets import QApplication, QDialog

from mathmodel_paper_studio.bootstrap.paths import AppPaths
from mathmodel_paper_studio.domain.models import SourceFile
from mathmodel_paper_studio.projects.manifest import latest_manifest
from mathmodel_paper_studio.projects.project_service import ProjectService
from mathmodel_paper_studio.storage.database import Database
from mathmodel_paper_studio.storage.repositories import ProjectRepository, RunRepository
from mathmodel_paper_studio.ui.main_window import MainWindow
from mathmodel_paper_studio.ui.project_editor import ProjectEditorDialog

ROOT = Path(r'F:\Codex另一个工作的地方\tmp\mathmodel-paper-studio-project-edit-20261018\project-editor')
APP = None


def wait_for_signal(spy, timeout_ms=15000):
    deadline = time.monotonic() + timeout_ms / 1000
    while spy.count() == 0 and time.monotonic() < deadline:
        QTest.qWait(10)
    return spy.count() > 0


class ProjectEditorDialogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        global APP
        ROOT.mkdir(parents=True, exist_ok=True)
        APP = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=ROOT)
        self.pool = QThreadPool()
        self.pool.setMaxThreadCount(1)
        self.paths = AppPaths(Path(self.temp.name) / 'app')
        self.paths.ensure_directories()
        database = Database(self.paths)
        database.migrate()
        self.repository = ProjectRepository(database)
        self.service = ProjectService(self.paths, self.repository)
        self.source = Path(self.temp.name) / 'first.csv'
        self.source.write_text('x,y\n1,2\n', encoding='utf-8')
        self.replacement = Path(self.temp.name) / 'replacement.csv'
        self.replacement.write_text('x,y\n3,4\n', encoding='utf-8')
        self.project = self.service.create('26国赛b题', '原题内容', [self.source])
        self.project_root = self.paths.root / self.project.relative_path
        _version, _path, manifest = latest_manifest(self.project_root)
        self.sources = tuple(SourceFile(**item['record']) for item in manifest['sources'])
        self.dialogs = []
        self.addCleanup(self._cleanup)

    def _cleanup(self):
        for dialog in self.dialogs:
            dialog.close()
        self.pool.waitForDone()
        APP.processEvents()
        self.temp.cleanup()

    def _dialog(self):
        dialog = ProjectEditorDialog(
            self.service, self.project, self.project_root, self.sources,
            '原题内容', revision=1, thread_pool=self.pool,
        )
        self.dialogs.append(dialog)
        return dialog

    def test_text_and_file_actions_support_undo_and_redo(self):
        dialog = self._dialog()
        dialog.show()
        dialog.problem_editor.setFocus()
        dialog.problem_editor.moveCursor(QTextCursor.MoveOperation.End)
        dialog.problem_editor.insertPlainText(' 增补')
        QTest.keyClick(dialog.problem_editor, Qt.Key.Key_Z, Qt.KeyboardModifier.ControlModifier)
        self.assertEqual('原题内容', dialog.problem_editor.toPlainText())
        QTest.keyClick(dialog.problem_editor, Qt.Key.Key_Y, Qt.KeyboardModifier.ControlModifier)
        self.assertEqual('原题内容 增补', dialog.problem_editor.toPlainText())

        with patch('mathmodel_paper_studio.ui.project_editor.QFileDialog.getOpenFileNames',
                   return_value=([str(self.replacement)], '数据文件')):
            QTest.mouseClick(dialog.add_file_button, Qt.MouseButton.LeftButton)
        self.assertEqual(2, len(dialog.source_files))
        QTest.mouseClick(dialog.undo_button, Qt.MouseButton.LeftButton)
        self.assertEqual((self.project_root / self.sources[1].relative_path,), dialog.source_files)
        QTest.mouseClick(dialog.redo_button, Qt.MouseButton.LeftButton)
        self.assertEqual(2, len(dialog.source_files))

        dialog.file_list.setCurrentRow(1)
        with patch('mathmodel_paper_studio.ui.project_editor.QFileDialog.getOpenFileName',
                   return_value=(str(self.source), '数据文件')):
            QTest.mouseClick(dialog.replace_file_button, Qt.MouseButton.LeftButton)
        self.assertEqual(self.source.resolve(), dialog.source_files[1])
        QTest.mouseClick(dialog.undo_button, Qt.MouseButton.LeftButton)
        self.assertEqual(self.replacement.resolve(), dialog.source_files[1])

        dialog.file_list.setCurrentRow(1)
        QTest.mouseClick(dialog.remove_file_button, Qt.MouseButton.LeftButton)
        self.assertEqual(1, len(dialog.source_files))
        QTest.mouseClick(dialog.undo_button, Qt.MouseButton.LeftButton)
        self.assertEqual(2, len(dialog.source_files))

    def test_cancel_discards_edits_and_save_commits_new_revision(self):
        dialog = self._dialog()
        dialog.problem_editor.setPlainText('修改后的题目内容')
        with patch('mathmodel_paper_studio.ui.project_editor.QFileDialog.getOpenFileNames',
                   return_value=([str(self.replacement)], '数据文件')):
            QTest.mouseClick(dialog.add_file_button, Qt.MouseButton.LeftButton)
        original_manifest = self.project_root / '00_inputs/manifest-v0001.json'
        original_manifest_bytes = original_manifest.read_bytes()
        QTest.mouseClick(dialog.cancel_button, Qt.MouseButton.LeftButton)
        self.assertEqual(QDialog.DialogCode.Rejected, dialog.result())
        self.assertFalse((self.project_root / '00_inputs/manifest-v0002.json').exists())
        self.assertEqual(original_manifest_bytes, original_manifest.read_bytes())

        dialog = self._dialog()
        dialog.problem_editor.setPlainText('修改后的题目内容')
        with patch('mathmodel_paper_studio.ui.project_editor.QFileDialog.getOpenFileNames',
                   return_value=([str(self.replacement)], '数据文件')):
            QTest.mouseClick(dialog.add_file_button, Qt.MouseButton.LeftButton)
        saved = QSignalSpy(dialog.revision_saved)
        QTest.mouseClick(dialog.save_button, Qt.MouseButton.LeftButton)
        worker_signals = dialog._worker_signals
        failures = QSignalSpy(worker_signals.failed)
        completed = wait_for_signal(saved, 30000)
        self.assertTrue(completed,
                        f'{dialog.status_label.text()} worker={dialog._worker!r} '
                        f'active={self.pool.activeThreadCount()} failures={failures.count()}')
        self.assertEqual([2], saved.at(0))
        self.assertEqual(QDialog.DialogCode.Accepted, dialog.result())
        self.assertEqual(self.source.read_bytes(),
                         (self.project_root / '00_inputs/source-0001-v0001.csv').read_bytes())
        self.assertEqual(self.replacement.read_bytes(),
                         (self.project_root / '00_inputs/source-0002-v0002.csv').read_bytes())
        self.assertEqual('修改后的题目内容',
                         (self.project_root / '00_inputs/problem-v0002.txt').read_text('utf-8'))

    def test_escape_cannot_close_editor_while_save_is_running(self):
        dialog = self._dialog()
        dialog.show()
        dialog.problem_editor.setPlainText('延迟保存的题目')
        release_save = threading.Event()
        original_update = self.service.update_inputs

        def delayed_update(*args, **kwargs):
            release_save.wait(5)
            return original_update(*args, **kwargs)

        saved = QSignalSpy(dialog.revision_saved)
        with patch.object(self.service, 'update_inputs', side_effect=delayed_update):
            QTest.mouseClick(dialog.save_button, Qt.MouseButton.LeftButton)
            self.assertIsNotNone(dialog._worker)
            QTest.keyClick(dialog, Qt.Key.Key_Escape)
            self.assertEqual(0, dialog.result())
            self.assertTrue(dialog.isVisible())
            release_save.set()
            self.assertTrue(wait_for_signal(saved, 30000), dialog.status_label.text())

        self.assertEqual(QDialog.DialogCode.Accepted, dialog.result())
        self.assertEqual('延迟保存的题目',
                         (self.project_root / '00_inputs/problem-v0002.txt').read_text('utf-8'))

    def test_main_window_loads_latest_revision_and_enables_editor(self):
        self.assertEqual(2, self.service.update_inputs(
            self.project, '新版题目', [self.replacement], expected_revision=1))
        window = MainWindow(
            self.paths, project_repository=self.repository,
            run_repository=RunRepository(Database(self.paths)), project_service=self.service,
            workflow_engine=object(), thread_pool=self.pool,
        )
        self.addCleanup(window.close)
        window.show()
        deadline = time.monotonic() + 30
        while window._source_revision != 2 and time.monotonic() < deadline:
            QTest.qWait(10)
        self.assertEqual(2, window._source_revision)
        self.assertEqual('新版题目', window.problem_view.toPlainText())
        self.assertIn('v0002', window.data_summary.toPlainText())
        self.assertTrue(window.edit_project_button.isVisible())
        self.assertTrue(window.edit_project_button.isEnabled())
        with patch('mathmodel_paper_studio.ui.main_window.ProjectEditorDialog') as editor:
            editor.return_value.exec.return_value = QDialog.DialogCode.Rejected
            QTest.mouseClick(window.edit_project_button, Qt.MouseButton.LeftButton)
            editor.assert_called_once()
            self.assertEqual(2, editor.call_args.kwargs['revision'])
