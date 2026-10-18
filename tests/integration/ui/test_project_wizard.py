import hashlib
import tempfile
import time
import unittest
from unittest.mock import patch
from pathlib import Path
from threading import Event

from PySide6.QtCore import QThreadPool, Qt
from PySide6.QtWidgets import QApplication
from PySide6.QtTest import QSignalSpy, QTest

from mathmodel_paper_studio.bootstrap.paths import AppPaths
from mathmodel_paper_studio.projects.project_service import ProjectService
from mathmodel_paper_studio.storage.database import Database
from mathmodel_paper_studio.storage.repositories import ProjectRepository
from mathmodel_paper_studio.ui.project_wizard import ProjectWizard
from tests.fixtures.docx.fixture_factory import write_fixture
from tests.fixtures.pdf.fixture_factory import pdf_bytes

ROOT = Path(r'F:\Codex另一个工作的地方\tmp\mathmodel-paper-studio-fluent-20261017\project-wizard')
APP = None


def wait_for_signal(spy, timeout_ms):
    deadline = time.monotonic() + timeout_ms / 1000
    while spy.count() == 0 and time.monotonic() < deadline:
        QTest.qWait(10)
    return spy.count() > 0


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


class _SlowProjectService(ProjectService):
    def __init__(self, paths, repository):
        super().__init__(paths, repository)
        self.started = Event()
        self.release = Event()

    def create(self, *args, **kwargs):
        self.started.set()
        if not self.release.wait(10):
            raise TimeoutError('test import gate timed out')
        return super().create(*args, **kwargs)


class ProjectWizardIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        global APP
        ROOT.mkdir(parents=True, exist_ok=True)
        APP = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=ROOT)
        self.thread_pool = QThreadPool()
        self.thread_pool.setMaxThreadCount(1)
        self._wizards = []
        self.addCleanup(self._cleanup_test_resources)
        self.paths = AppPaths(Path(self.temp.name) / 'app')
        self.paths.ensure_directories()
        database = Database(self.paths)
        database.migrate()
        self.repository = ProjectRepository(database)
        self.service = ProjectService(self.paths, self.repository)
        self.csv = Path(self.temp.name) / 'observations.csv'
        self.csv.write_text('time,value\n1,2\n', encoding='utf-8')
        self.template = write_fixture(Path(self.temp.name) / 'template.docx')
        self.docx_data = write_fixture(Path(self.temp.name) / 'observations.docx')
        self.docx_data2 = write_fixture(Path(self.temp.name) / 'notes.docx')

    def _cleanup_test_resources(self):
        finished = self.thread_pool.waitForDone(15000)
        if not finished:
            self.thread_pool.waitForDone()
        APP.processEvents()
        for wizard in reversed(self._wizards):
            wizard.close()
        APP.processEvents()
        self.temp.cleanup()
        self.assertTrue(finished, '项目向导测试线程超时；已等待线程结束并关闭窗口后再清理临时目录。')

    def _make_wizard(self, service=None):
        wizard = ProjectWizard(service or self.service, thread_pool=self.thread_pool)
        self.assertIs(wizard.thread_pool, self.thread_pool)
        self._wizards.append(wizard)
        return wizard

    def test_wizard_import_runs_off_ui_thread_and_preserves_original_inputs(self):
        original_hashes = {path: sha256(path) for path in (self.csv, self.template)}
        wizard = self._make_wizard()
        wizard.title_input.setText('数模项目')
        wizard.problem_input.setPlainText('拟合给定数据并解释模型。')
        wizard.set_source_files((self.csv,))
        wizard.set_template_path(self.template)
        created = QSignalSpy(wizard.project_created)
        failures = QSignalSpy(wizard.operation_failed)
        wizard.show()
        QTest.mouseClick(wizard.create_button, Qt.MouseButton.LeftButton)
        self.assertFalse(wizard.cancel_button.isEnabled())
        self.assertTrue(wait_for_signal(created, 60000),
                        wizard.error_label.text() + (failures.at(0)[0] if failures.count() else ''))

        project = created.at(0)[0]
        project_root = self.paths.root / project.relative_path
        self.assertEqual(project, self.repository.get(project.project_id))
        self.assertTrue((project_root / '00_inputs/problem-v0001.txt').is_file())
        self.assertTrue((project_root / '00_inputs/source-0001-v0001.csv').is_file())
        self.assertTrue((project_root / '00_inputs/source-0002-v0001.docx').is_file())
        self.assertEqual(original_hashes, {path: sha256(path) for path in original_hashes})

    def test_validation_error_names_missing_fields_and_blocks_import(self):
        wizard = self._make_wizard()
        wizard.show()

        self.assertFalse(wizard.create_button.isEnabled())
        validation = wizard.error_label.text()
        self.assertIn('项目名称', validation)
        self.assertIn('题目内容', validation)
        self.assertIn('数据文件', validation)
        self.assertEqual((), self.repository.list_all())

    def test_docx_can_be_selected_as_data_without_becoming_the_word_template(self):
        wizard = self._make_wizard()

        wizard.set_source_files((self.docx_data,))

        self.assertEqual((self.docx_data,), wizard.source_files)
        self.assertIsNone(wizard.template_path)
        self.assertIn('DOCX', wizard.add_source_button.text().upper())
        self.assertNotIn('范围无效', wizard.error_label.text())

    def test_two_docx_data_files_can_be_selected_together(self):
        wizard = self._make_wizard()

        wizard.set_source_files((self.docx_data, self.docx_data2))

        self.assertEqual((self.docx_data, self.docx_data2), wizard.source_files)
        self.assertEqual(2, wizard.file_list.count())
        self.assertIsNone(wizard.template_path)

    def test_question_docx_button_extracts_paragraphs_and_tables_into_problem(self):
        wizard = self._make_wizard()
        original_hash = sha256(self.docx_data)
        wizard.show()

        with patch('mathmodel_paper_studio.ui.project_wizard.QFileDialog.getOpenFileName',
                   return_value=(str(self.docx_data), 'Word 文档 (*.docx)')):
            QTest.mouseClick(wizard.import_problem_docx_button, Qt.MouseButton.LeftButton)

        problem = wizard.problem_input.toPlainText()
        self.assertIn('摘要', problem)
        self.assertIn('结果', problem)
        self.assertIn('指标', problem)
        self.assertIn('数值', problem)
        self.assertIn(self.docx_data.name, wizard.problem_docx_status.text())
        self.assertEqual((), wizard.source_files)
        wizard.title_input.setText('DOCX 题目导入测试')
        wizard.set_source_files((self.csv,))
        created = QSignalSpy(wizard.project_created)
        QTest.mouseClick(wizard.create_button, Qt.MouseButton.LeftButton)
        self.assertTrue(wait_for_signal(created, 60000), wizard.error_label.text())
        project = created.at(0)[0]
        stored_problem = (self.paths.root / project.relative_path / '00_inputs' /
                          'problem-v0001.txt').read_text(encoding='utf-8')
        self.assertIn('摘要', stored_problem)
        self.assertIn('指标', stored_problem)
        self.assertEqual(original_hash, sha256(self.docx_data))

    def test_question_pdf_chooser_imports_chinese_text_and_preserves_original_in_project(self):
        pdf = Path(self.temp.name) / '题目.PDF'
        pdf.write_bytes(pdf_bytes(compress=True))
        original = pdf.read_bytes()
        wizard = self._make_wizard()
        wizard.title_input.setText('PDF 题目导入')
        wizard.set_source_files((self.csv,))
        wizard.show()

        with patch('mathmodel_paper_studio.ui.project_wizard.QFileDialog.getOpenFileName',
                   return_value=(str(pdf), '题目文档 (*.docx *.pdf)')):
            QTest.mouseClick(wizard.import_problem_docx_button, Qt.MouseButton.LeftButton)

        self.assertIn('数学建模题目：优化运输成本', wizard.problem_input.toPlainText())
        self.assertIn('附件数据见表一', wizard.problem_input.toPlainText())
        self.assertIn('2 页', wizard.problem_docx_status.text())
        self.assertEqual((self.csv,), wizard.source_files)
        created = QSignalSpy(wizard.project_created)
        QTest.mouseClick(wizard.create_button, Qt.MouseButton.LeftButton)
        self.assertTrue(wait_for_signal(created, 60000), wizard.error_label.text())
        project_root = self.paths.root / created.at(0)[0].relative_path
        self.assertIn('优化运输成本', (project_root / '00_inputs/problem-v0001.txt').read_text('utf-8'))
        self.assertEqual(original, (project_root / '00_inputs/problem-original-v0001.pdf').read_bytes())
        self.assertEqual(original, pdf.read_bytes())

    def test_failed_pdf_import_keeps_existing_problem_and_original_snapshot(self):
        wizard = self._make_wizard()
        wizard.problem_input.setPlainText('现有题目')
        for name, content, reason in (
            ('scan.pdf', pdf_bytes(pages=('',)), '文字|文本'),
            ('locked.pdf', pdf_bytes(password='test-password'), '加密'),
            ('broken.pdf', b'%PDF-1.7\nbroken', '损坏|无效'),
        ):
            path = Path(self.temp.name) / name
            path.write_bytes(content)
            with self.subTest(name=name):
                with patch('mathmodel_paper_studio.ui.project_wizard.QFileDialog.getOpenFileName',
                           return_value=(str(path), '题目文档 (*.docx *.pdf)')):
                    QTest.mouseClick(wizard.import_problem_docx_button, Qt.MouseButton.LeftButton)
                self.assertEqual('现有题目', wizard.problem_input.toPlainText())
                self.assertRegex(wizard.problem_docx_status.text(), reason)

    def test_import_progress_is_visible_and_actions_lock_while_worker_runs(self):
        slow_service = _SlowProjectService(self.paths, self.repository)
        self.addCleanup(slow_service.release.set)
        wizard = self._make_wizard(slow_service)
        wizard.title_input.setText('Progress project')
        wizard.problem_input.setPlainText('Problem statement')
        wizard.set_source_files((self.csv,))
        created = QSignalSpy(wizard.project_created)
        wizard.show()

        QTest.mouseClick(wizard.create_button, Qt.MouseButton.LeftButton)
        deadline = time.monotonic() + 5
        while not slow_service.started.is_set() and time.monotonic() < deadline:
            QTest.qWait(10)

        self.assertTrue(slow_service.started.is_set(), wizard.error_label.text())
        self.assertTrue(wizard.import_progress.isVisible())
        self.assertTrue(wizard.progress_label.isVisible())
        self.assertFalse(wizard.create_button.isEnabled())
        self.assertFalse(wizard.cancel_button.isEnabled())

        slow_service.release.set()
        self.assertTrue(wait_for_signal(created, 60000), wizard.error_label.text())

    def test_failed_import_shows_retry_and_successfully_retries(self):
        original_create = self.service.create
        attempts = 0

        def fail_once(*args, **kwargs):
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                raise ValueError('simulated import failure')
            return original_create(*args, **kwargs)

        self.service.create = fail_once
        wizard = self._make_wizard()
        wizard.title_input.setText('Retry project')
        wizard.problem_input.setPlainText('Problem statement')
        wizard.set_source_files((self.csv,))
        failures = QSignalSpy(wizard.operation_failed)
        created = QSignalSpy(wizard.project_created)
        wizard.show()

        QTest.mouseClick(wizard.create_button, Qt.MouseButton.LeftButton)
        self.assertTrue(wait_for_signal(failures, 5000))
        deadline = time.monotonic() + 5
        while wizard._worker is not None and time.monotonic() < deadline:
            QTest.qWait(10)

        self.assertIn('创建失败', wizard.error_label.text())
        self.assertEqual('重试导入', wizard.create_button.text())
        self.assertTrue(wizard.create_button.isEnabled())

        QTest.mouseClick(wizard.create_button, Qt.MouseButton.LeftButton)
        self.assertTrue(wait_for_signal(created, 60000), wizard.error_label.text())
        self.assertEqual(2, attempts)
        self.assertEqual(1, len(self.repository.list_all()))

    def test_invalid_file_scope_shows_chinese_error_and_does_not_start_import(self):
        wizard = self._make_wizard()
        wizard.set_source_files((Path(r'\\server\share\secret.csv'),))
        wizard.title_input.setText('不应创建')
        wizard.problem_input.setPlainText('题目')
        self.assertIn('本地磁盘', wizard.error_label.text())
        self.assertFalse(wizard.create_button.isEnabled())
        self.assertEqual((), self.repository.list_all())

    def test_wizard_keeps_controls_reachable_in_a_narrow_window(self):
        wizard = self._make_wizard()
        wizard.resize(520, 420)
        wizard.show()
        QTest.qWait(20)

        self.assertIsNotNone(wizard.content_scroll_area)
        self.assertTrue(wizard.content_scroll_area.widgetResizable())
        self.assertTrue(wizard.content_scroll_area.verticalScrollBar().isVisible())
        self.assertTrue(wizard.title_input.isVisible())
        self.assertTrue(wizard.create_button.isVisible())
        wizard.activateWindow()
        wizard.title_input.setFocus()
        QTest.keyClick(wizard.title_input, Qt.Key.Key_Tab)
        self.assertTrue(wizard.problem_input.hasFocus())
        QTest.keyClick(wizard.problem_input, Qt.Key.Key_Tab)
        self.assertTrue(wizard.import_problem_docx_button.hasFocus())
        QTest.keyClick(wizard.import_problem_docx_button, Qt.Key.Key_Tab)
        self.assertTrue(wizard.add_source_button.hasFocus())


if __name__ == '__main__':
    unittest.main()
