import hashlib
import tempfile
import threading
import time
import unittest
from pathlib import Path

from PySide6.QtCore import QThreadPool, QTimer, Qt
from PySide6.QtWidgets import QApplication
from PySide6.QtTest import QSignalSpy, QTest

from mathmodel_paper_studio.agents.preflight import Provider
from mathmodel_paper_studio.bootstrap.paths import AppPaths
from mathmodel_paper_studio.paper.docx_qa import DocxQaReport
from mathmodel_paper_studio.paper.template_inspector import inspect_template
from mathmodel_paper_studio.paper.template_mapping import TemplateMapping
from mathmodel_paper_studio.domain.models import Project, Run, RunStatus, SourceFile
from mathmodel_paper_studio.ui.main_window import MainWindow
from mathmodel_paper_studio.ui.run_panel import RunPanel
from mathmodel_paper_studio.ui.workers import FunctionWorker
from tests.fixtures.docx.fixture_factory import write_fixture

ROOT = Path(r'F:\Codex另一个工作的地方\tmp\mathmodel-paper-studio-fluent-20261017\task5-tests')
APP = None


def wait_for_signal(spy, timeout_ms):
    deadline = time.monotonic() + timeout_ms / 1000
    while spy.count() == 0 and time.monotonic() < deadline:
        QTest.qWait(10)
    return spy.count() > 0


class RunControlsIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        global APP
        ROOT.mkdir(parents=True, exist_ok=True)
        APP = QApplication.instance() or QApplication([])

    def test_run_scope_preflight_runs_off_ui_thread_and_keeps_unverified_provider_blocked(self):
        temp = tempfile.TemporaryDirectory(dir=ROOT)
        self.addCleanup(temp.cleanup)
        paths = AppPaths(Path(temp.name) / 'app')
        paths.ensure_directories()
        project_id = 'a' * 32
        project = Project(project_id, 'async review', f'projects/{project_id}')
        project_root = paths.projects / project_id
        input_dir = project_root / '00_inputs'
        input_dir.mkdir(parents=True)
        (project_root / '90_runs').mkdir()
        (input_dir / 'problem-v0001.txt').write_text('problem', encoding='utf-8')
        source = SourceFile('b' * 32, project_id, '00_inputs/problem-v0001.txt', '0' * 64, 0, 'problem')
        scope_threads = []
        dialogs = []

        class Projects:
            def list_all(self):
                return (project,)

            def get(self, requested):
                return project if requested == project_id else None

        class ProbeWindow(MainWindow):
            def _load_sources(self, requested, root, cancel_event=None, emit_event=None):
                return (source,)

            def _read_small_text(self, file_path, limit):
                return 'problem'

            def _load_data_summary(self, root, cancel_event=None):
                return 'async summary'

        def verify_scope(_project, _run, _readable, _output):
            scope_threads.append(threading.get_ident())
            return True, 'scope verified', ()

        pool = QThreadPool()
        window = ProbeWindow(paths, project_repository=Projects(), run_repository=object(),
                             project_service=object(), workflow_engine=object(),
                             scope_verifier=verify_scope, thread_pool=pool)
        self.addCleanup(pool.waitForDone, 15000)
        self.addCleanup(window.close)
        deadline = time.monotonic() + 5
        while window.data_summary.toPlainText() != 'async summary' and time.monotonic() < deadline:
            QTest.qWait(10)

        def close_review_when_ready():
            if window._review_dialog is None:
                QTimer.singleShot(10, close_review_when_ready)
                return
            dialogs.append(window._review_dialog)
            window._review_dialog.reject()

        QTimer.singleShot(10, close_review_when_ready)
        window._review_run()
        deadline = time.monotonic() + 20
        while not dialogs and time.monotonic() < deadline:
            QTest.qWait(10)

        self.assertTrue(dialogs)
        self.assertTrue(scope_threads)
        self.assertNotEqual(threading.get_ident(), scope_threads[0])
        self.assertTrue(dialogs[0].model.scope_verified)
        self.assertFalse(dialogs[0].start_button.isEnabled())

    def test_project_selection_hashes_inputs_off_the_ui_thread(self):
        temp = tempfile.TemporaryDirectory(dir=ROOT)
        self.addCleanup(temp.cleanup)
        paths = AppPaths(Path(temp.name) / 'app')
        paths.ensure_directories()
        project_id = 'a' * 32
        project = Project(project_id, 'async load', f'projects/{project_id}')
        (paths.projects / project_id).mkdir(parents=True, exist_ok=True)
        source = SourceFile('b' * 32, project_id, '00_inputs/problem-v0001.txt', '0' * 64, 0, 'problem')
        loader_threads = []

        class Projects:
            def list_all(self):
                return (project,)

            def get(self, requested):
                return project if requested == project_id else None

        class ProbeWindow(MainWindow):
            def _load_sources(self, requested, project_root, cancel_event=None, emit_event=None):
                loader_threads.append(threading.get_ident())
                return (source,)

            def _read_small_text(self, path, limit):
                loader_threads.append(threading.get_ident())
                return 'problem'

            def _load_data_summary(self, project_root, cancel_event=None):
                return 'async summary'

        pool = QThreadPool()
        window = ProbeWindow(paths, project_repository=Projects(), run_repository=object(),
                             project_service=object(), workflow_engine=object(), thread_pool=pool)
        self.addCleanup(pool.waitForDone, 10000)
        self.addCleanup(window.close)
        deadline = time.monotonic() + 5
        while window.data_summary.toPlainText() != 'async summary' and time.monotonic() < deadline:
            QTest.qWait(10)
        self.assertEqual('async summary', window.data_summary.toPlainText())
        self.assertTrue(loader_threads)
        self.assertTrue(all(thread_id != threading.get_ident() for thread_id in loader_threads))

    def test_artifact_worker_verifies_docx_hash_and_runs_template_bound_structural_qa(self):
        temp = tempfile.TemporaryDirectory(dir=ROOT)
        self.addCleanup(temp.cleanup)
        project_root = Path(temp.name) / 'projects' / ('a' * 32)
        template_dir = project_root / '00_inputs'
        review_dir = project_root / '70_review'
        run_dir = project_root / '90_runs' / ('c' * 32)
        template_dir.mkdir(parents=True)
        review_dir.mkdir()
        run_dir.mkdir(parents=True)
        template = write_fixture(template_dir / 'template.docx')
        inspection = inspect_template(template)
        heading = next(item for item in inspection.anchors if item.kind == 'heading')
        TemplateMapping.from_selection(inspection, {'abstract': heading.anchor_id}).save(project_root)
        paper = run_dir / 'paper.docx'
        paper.write_bytes(template.read_bytes())
        content = paper.read_bytes()
        artifact = type('Artifact', (), {
            'relative_path': f'90_runs/{"c" * 32}/paper.docx', 'kind': 'paper',
            'size_bytes': len(content), 'sha256': hashlib.sha256(content).hexdigest(),
        })()
        state = type('State', (), {'artifacts': (artifact,)})()

        class Runner:
            _raise_if_cancelled = staticmethod(MainWindow._raise_if_cancelled)
            _latest_template_mapping_version = staticmethod(MainWindow._latest_template_mapping_version)
            _inspect_run_artifacts = MainWindow._inspect_run_artifacts

        review = Runner()._inspect_run_artifacts(
            state, project_root, template, threading.Event(), lambda _event: None)

        self.assertEqual(1, len(review.artifacts))
        self.assertTrue(review.artifacts[0][3])
        self.assertEqual(paper, review.paper_path)
        self.assertIsNotNone(review.qa_report)
        self.assertIn('{{section:abstract}}', review.qa_report.unresolved_placeholders)

    def test_docx_review_summary_exposes_unsupported_template_citations_math_and_failed_checks(self):
        report = DocxQaReport(
            package_valid=True, structural_status='failed', missing_sections=('abstract',),
            pending_citations=('source citation',), pending_reviews=('model result',),
        )
        summary = MainWindow._format_docx_qa_summary(report, ('\u6a21\u677f\u4e0d\u53d7\u652f\u6301',))

        self.assertIn('\u7ed3\u6784\u68c0\u67e5\u672a\u901a\u8fc7', summary)
        self.assertIn('\u5f85\u8865\u5f15\u7528', summary)
        self.assertIn('\u5f85\u4eba\u5de5\u6838\u9a8c', summary)
        self.assertIn('\u6a21\u677f\u4e0d\u53d7\u652f\u6301', summary)
        self.assertIn('\u6570\u5b66\u7ed3\u8bba', summary)
        self.assertIn('\u672a\u6267\u884c', summary)

    def test_worker_streams_bounded_events_and_fake_provider_result(self):
        panel = RunPanel()
        self.addCleanup(panel.close)
        panel.show()
        completed = QSignalSpy(panel.operation_succeeded)
        events = QSignalSpy(panel.event_received)
        panel.start_operation(lambda cancel, emit: (emit('预检阶段完成'), 'fake-result')[1])

        self.assertTrue(wait_for_signal(completed, 5000))
        self.assertEqual('fake-result', completed.at(0)[0])
        self.assertEqual('预检阶段完成', events.at(0)[0])
        self.assertIn('预检阶段完成', panel.event_view.toPlainText())

    def test_cancel_button_sets_worker_cancellation_signal(self):
        panel = RunPanel()
        self.addCleanup(panel.close)
        panel.show()
        entered = threading.Event()
        completed = QSignalSpy(panel.operation_succeeded)

        def wait_for_cancel(cancel, emit):
            entered.set()
            while not cancel.wait(0.005):
                emit('正在等待取消')
            return 'cancelled'

        panel.start_operation(wait_for_cancel)
        deadline = time.monotonic() + 2
        while not entered.is_set() and time.monotonic() < deadline:
            QTest.qWait(5)
        self.assertTrue(entered.is_set())
        QTest.mouseClick(panel.cancel_button, Qt.MouseButton.LeftButton)
        self.assertTrue(wait_for_signal(completed, 5000))
        self.assertEqual('cancelled', completed.at(0)[0])

    def test_paused_run_can_resume_or_cancel_and_chinese_failure_is_shown(self):
        panel = RunPanel()
        self.addCleanup(panel.close)
        panel.set_run_id('run1')
        panel.set_run_state(RunStatus.PAUSED, '缺少可信运行环境，已暂停。')
        self.assertTrue(panel.resume_button.isEnabled())
        resumed = QSignalSpy(panel.resume_requested)
        QTest.mouseClick(panel.resume_button, Qt.MouseButton.LeftButton)
        self.assertEqual(1, resumed.count())
        self.assertEqual('run1', resumed.at(0)[0])

        panel.set_run_id('run1')
        cancelled = QSignalSpy(panel.cancel_requested)
        QTest.mouseClick(panel.cancel_button, Qt.MouseButton.LeftButton)
        self.assertEqual(1, cancelled.count())
        self.assertIn('已暂停', panel.status_label.text())

        failed = QSignalSpy(panel.operation_failed)
        panel.start_operation(lambda cancel, emit: (_ for _ in ()).throw(ValueError('导入文件格式无效。')))
        self.assertTrue(wait_for_signal(failed, 5000))
        self.assertIn('导入文件格式无效', failed.at(0)[0])

    def test_run_panel_has_agent_progress_state_and_artifact_route_guidance(self):
        panel = RunPanel()
        self.addCleanup(panel.close)

        panel.set_provider(Provider.DEEPSEEK)
        panel.set_run_state(RunStatus.RUNNING)

        self.assertIn('DeepSeek', panel.current_agent_label.text())
        self.assertIn('运行中', panel.status_label.text())
        self.assertEqual('取消运行', panel.cancel_button.text())
        self.assertEqual('继续运行', panel.resume_button.text())
        self.assertEqual(0, panel.progress_bar.minimum())
        self.assertEqual(0, panel.progress_bar.maximum())
        self.assertTrue(panel.cancel_button.isEnabled())
        self.assertIn('项目与数据', panel.artifact_access_label.text())

        panel.set_run_state(RunStatus.PAUSED, '运行已暂停。')
        self.assertEqual(9, panel.progress_bar.maximum())
        self.assertTrue(panel.resume_button.isEnabled())

    def test_run_agent_label_stays_with_retained_run_after_settings_change(self):
        temp = tempfile.TemporaryDirectory(dir=ROOT)
        self.addCleanup(temp.cleanup)
        pool = QThreadPool()
        self.addCleanup(pool.waitForDone, 5000)
        window = MainWindow(AppPaths(Path(temp.name) / 'app'), thread_pool=pool)
        self.addCleanup(window.close)

        self.assertIn('Codex', window.run_panel.current_agent_label.text())
        window.settings_view.provider_combo.setCurrentIndex(1)
        self.assertIn('DeepSeek', window.run_panel.current_agent_label.text())

        run_id = 'c' * 32
        run = Run(run_id, 'a' * 32, Provider.DEEPSEEK, RunStatus.COMPLETED,
                  f'90_runs/{run_id}')
        window._active_run = run
        window.run_panel.set_run_id(run.run_id)
        window.run_panel.set_provider(run.provider)
        completed = QSignalSpy(window.run_panel.operation_succeeded)
        window.run_panel.start_operation(lambda _cancel, _emit: RunStatus.COMPLETED)

        self.assertTrue(wait_for_signal(completed, 5000))
        deadline = time.monotonic() + 2
        while window.run_panel.current_status is not RunStatus.COMPLETED and time.monotonic() < deadline:
            QTest.qWait(5)
        self.assertIs(window.run_panel.current_status, RunStatus.COMPLETED)
        self.assertIs(window._active_run, run)

        window.settings_view.provider_combo.setCurrentIndex(0)

        self.assertIn('DeepSeek', window.run_panel.current_agent_label.text())

    def test_run_page_artifact_action_opens_the_existing_project_review(self):
        temp = tempfile.TemporaryDirectory(dir=ROOT)
        self.addCleanup(temp.cleanup)
        pool = QThreadPool()
        self.addCleanup(pool.waitForDone, 5000)
        window = MainWindow(AppPaths(Path(temp.name) / 'app'), thread_pool=pool)
        self.addCleanup(window.close)
        window.settings_view.provider_combo.setCurrentIndex(1)
        self.assertIn('DeepSeek', window.run_panel.current_agent_label.text())
        window.workspace.setCurrentWidget(window.run_panel)
        started = threading.Event()
        completed = QSignalSpy(window.run_panel.operation_succeeded)

        def wait_until_cancelled(cancel, _emit):
            started.set()
            cancel.wait(5)
            return RunStatus.CANCELLED

        window.run_panel.start_operation(wait_until_cancelled)
        deadline = time.monotonic() + 2
        while not started.is_set() and time.monotonic() < deadline:
            QTest.qWait(5)
        self.assertTrue(started.is_set())
        active_worker = window.run_panel.active_worker

        QTest.mouseClick(window.run_panel.artifact_button, Qt.MouseButton.LeftButton)

        self.assertIs(window.workspace.currentWidget(), window.project_page)
        self.assertIs(window.run_panel.active_worker, active_worker)
        self.assertIs(window.run_panel.current_status, RunStatus.RUNNING)
        QTest.mouseClick(window.run_panel.cancel_button, Qt.MouseButton.LeftButton)
        self.assertTrue(wait_for_signal(completed, 5000))


if __name__ == '__main__':
    unittest.main()
