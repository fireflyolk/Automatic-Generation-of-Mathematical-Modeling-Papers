import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import ANY, patch

from PySide6.QtWidgets import QApplication

from mathmodel_paper_studio.agents.preflight import (
    AdapterCapabilities, Provider, ProviderStatus, UnavailableReason,
)
from mathmodel_paper_studio.agents.live_probe import ProbeAssessment
from mathmodel_paper_studio.agents.providers.deepseek_api import DeepSeekApiClient
from mathmodel_paper_studio.agents.live_runtime import LiveProviderService
from mathmodel_paper_studio.bootstrap.paths import AppPaths
from mathmodel_paper_studio.domain.models import Run, RunStatus
from mathmodel_paper_studio.ui.main_window import MainWindow

ROOT = Path(r'F:\Codex另一个工作的地方\tmp\mathmodel-paper-studio-fluent-20261017\task5-tests')
APP = None


class FakeConsentClient:
    def __init__(self):
        self.authorized = []
        self.revoked = []

    def authorize_chargeable_requests(self, run_id):
        self.authorized.append(run_id)

    def revoke_chargeable_requests(self, run_id):
        self.revoked.append(run_id)


class FakeWorkflow:
    def __init__(self, client, start_state=None, resume_state=None):
        self.provider_client = client
        self.start_state = start_state or SimpleNamespace(status=RunStatus.PAUSED,
                                                          stage_records=(), diagnostics=())
        self.resume_state = resume_state or SimpleNamespace(status=RunStatus.COMPLETED,
                                                            stage_records=(), diagnostics=())

    def start(self, *_args, **_kwargs):
        if isinstance(self.start_state, Exception):
            raise self.start_state
        return self.start_state

    def resume(self, *_args, **_kwargs):
        if isinstance(self.resume_state, Exception):
            raise self.resume_state
        return self.resume_state


class FakeRunRepository:
    def add(self, _run):
        return None


class DeferredThreadPool:
    def __init__(self):
        self.workers = []

    def start(self, worker):
        self.workers.append(worker)

    def run_all(self):
        workers, self.workers = self.workers, []
        for worker in workers:
            worker.run()


class FakeCodexProbe:
    def __init__(self, status):
        self.status = status
        self.providers = []

    def verify(self, provider, cancel_event):
        self.providers.append(provider)
        self.cancel_event = cancel_event
        return ProbeAssessment(self.status)


class MainWindowDeepSeekConsentTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        global APP
        ROOT.mkdir(parents=True, exist_ok=True)
        APP = QApplication.instance() or QApplication([])

    def test_default_window_routes_codex_and_deepseek_to_separate_clients(self):
        temporary = tempfile.TemporaryDirectory(dir=ROOT)
        self.addCleanup(temporary.cleanup)
        window = MainWindow(AppPaths(Path(temporary.name) / 'app'))
        self.addCleanup(window.close)

        registry = window.workflow.provider_client
        self.assertIsInstance(registry.for_provider(Provider.CODEX), LiveProviderService)
        self.assertIs(window.codex_probe, registry.for_provider(Provider.CODEX))
        self.assertIsInstance(registry.for_provider(Provider.DEEPSEEK), DeepSeekApiClient)
        self.assertIsNot(registry.for_provider(Provider.CODEX),
                         registry.for_provider(Provider.DEEPSEEK))

    def test_default_codex_button_uses_the_workflow_runtime_and_reports_text_only(self):
        temporary = tempfile.TemporaryDirectory(dir=ROOT)
        self.addCleanup(temporary.cleanup)
        pool = DeferredThreadPool()
        window = MainWindow(AppPaths(Path(temporary.name) / 'app'), thread_pool=pool)
        self.addCleanup(window.close)
        runtime = window.workflow.provider_client.for_provider(Provider.CODEX)
        status = ProviderStatus(
            Provider.CODEX, r'F:\codex\codex.exe', '0.159.3', False,
            UnavailableReason.EVIDENCE_UNVERIFIED,
            '固定文本连接已验证；项目范围尚未验证。',
            AdapterCapabilities(tool_profile='unverified'), False, True,
        )

        with patch.object(runtime._probe, 'verify', return_value=ProbeAssessment(status)) as verify:
            window.settings_view.codex_test_button.click()
            pool.run_all()

        verify.assert_called_once_with(Provider.CODEX, ANY, run_model_canary=True)
        self.assertIs(window.codex_probe, runtime)
        self.assertTrue(window.settings_view.selected_status.connection_verified)
        self.assertFalse(window.settings_view.selected_status.available)
        self.assertIn('这只证明文本连通', window.settings_view.connection_label.text())
        self.assertIn('自动运行保持关闭', window.settings_view.connection_label.text())

    def test_codex_connection_check_is_explicit_and_opens_only_after_verified_result(self):
        temporary = tempfile.TemporaryDirectory(dir=ROOT)
        self.addCleanup(temporary.cleanup)
        status = ProviderStatus(
            Provider.CODEX, r'F:\codex\codex.exe', '0.159.3', False,
            UnavailableReason.CAPABILITIES_UNVERIFIED,
            'Codex 静态能力及隔离预检仍未通过。',
            AdapterCapabilities(tool_profile='text_only'), False,
        )
        probe = FakeCodexProbe(status)
        pool = DeferredThreadPool()
        window = MainWindow(AppPaths(Path(temporary.name) / 'app'),
                            codex_probe=probe, thread_pool=pool)
        self.addCleanup(window.close)

        self.assertIn('本机 CLI 检查尚未执行', window.settings_view.connection_label.text())
        self.assertEqual([], probe.providers)
        window.settings_view.codex_test_button.click()
        self.assertIn('固定提示验证 Codex 连接', window.settings_view.connection_label.text())
        pool.run_all()

        self.assertEqual([Provider.CODEX], probe.providers)
        self.assertIn('固定文本连通请求未通过', window.settings_view.connection_label.text())
        self.assertFalse(window.settings_view.selected_status.available)
        self.assertIn('Codex 静态能力及隔离预检仍未通过', window.settings_view.status_label.text())

    def make_window(self, *, fail_to_start=False, start_state=None, resume_state=None):
        temporary = tempfile.TemporaryDirectory(dir=ROOT)
        self.addCleanup(temporary.cleanup)
        client = FakeConsentClient()
        workflow = FakeWorkflow(client, start_state=start_state, resume_state=resume_state)
        window = MainWindow(AppPaths(Path(temporary.name) / 'app'), workflow_engine=workflow)
        self.addCleanup(window.close)
        window.runs = FakeRunRepository()
        panel = window.run_panel
        panel.operation = None
        panel.resume_operation = None

        def start_operation(operation, *, resume_operation=None, cancel_operation=None):
            if fail_to_start:
                raise RuntimeError('worker already active')
            panel.operation = operation
            panel.resume_operation = resume_operation

        panel.start_operation = start_operation
        window._project = SimpleNamespace(project_id='project_1')
        window._sources = ()
        errors = []
        window._show_error = errors.append
        run = Run('run_1', 'project_1', Provider.DEEPSEEK, RunStatus.CREATED, '90_runs/run_1')
        return window, client, workflow, run, errors

    def test_charge_consent_remains_for_same_run_after_pause_then_revokes_after_resume(self):
        window, client, _workflow, run, _errors = self.make_window()

        window._start_run(run, (), ())
        self.assertIn('DeepSeek', window.run_panel.current_agent_label.text())
        state = window.run_panel.operation(lambda: False, lambda _message: None)

        self.assertIs(state.status, RunStatus.PAUSED)
        self.assertEqual(['run_1'], client.authorized)
        self.assertEqual([], client.revoked)

        resumed = window.run_panel.resume_operation(lambda: False, lambda _message: None)

        self.assertIs(resumed.status, RunStatus.COMPLETED)
        self.assertEqual(['run_1'], client.revoked)

    def test_synchronous_worker_start_failure_revokes_charge_consent(self):
        window, client, _workflow, run, errors = self.make_window(fail_to_start=True)

        window._start_run(run, (), ())

        self.assertEqual(['run_1'], client.authorized)
        self.assertEqual(['run_1'], client.revoked)
        self.assertTrue(errors)

    def test_resume_exception_revokes_charge_consent(self):
        window, client, _workflow, run, _errors = self.make_window(
            resume_state=RuntimeError('resume failed'))

        window._start_run(run, (), ())
        paused = window.run_panel.operation(lambda: False, lambda _message: None)
        self.assertIs(paused.status, RunStatus.PAUSED)
        self.assertEqual([], client.revoked)

        with self.assertRaisesRegex(RuntimeError, 'resume failed'):
            window.run_panel.resume_operation(lambda: False, lambda _message: None)

        self.assertEqual(['run_1'], client.revoked)

    def test_workflow_failure_revokes_charge_consent_for_the_same_run(self):
        window, client, _workflow, run, _errors = self.make_window(
            start_state=RuntimeError('workflow failed'))

        window._start_run(run, (), ())
        with self.assertRaisesRegex(RuntimeError, 'workflow failed'):
            window.run_panel.operation(lambda: False, lambda _message: None)

        self.assertEqual(['run_1'], client.authorized)
        self.assertEqual(['run_1'], client.revoked)

    def test_paused_deepseek_run_blocks_replacement_until_resolved(self):
        window, client, _workflow, run, errors = self.make_window()

        window._start_run(run, (), ())
        paused = window.run_panel.operation(lambda: False, lambda _message: None)
        self.assertIs(paused.status, RunStatus.PAUSED)
        window.run_panel.set_run_state(RunStatus.PAUSED)
        resume_operation = window.run_panel.resume_operation
        next_run = Run('run_2', 'project_1', Provider.CODEX, RunStatus.CREATED, '90_runs/run_2')

        window._start_run(next_run, (), ())

        self.assertEqual(['run_1'], client.authorized)
        self.assertEqual([], client.revoked)
        self.assertIs(window.run_panel.resume_operation, resume_operation)
        self.assertEqual('run_1', window._active_run.run_id)
        self.assertTrue(errors)

    def test_closing_window_revokes_paused_deepseek_consent(self):
        window, client, _workflow, run, _errors = self.make_window()

        window._start_run(run, (), ())
        paused = window.run_panel.operation(lambda: False, lambda _message: None)
        self.assertIs(paused.status, RunStatus.PAUSED)
        window.run_panel.set_run_state(RunStatus.PAUSED)
        window.run_panel._worker = object()
        cancel_calls = []

        def cancel_active_worker():
            cancel_calls.append(True)
            window.run_panel._worker = None

        window.run_panel.cancel = cancel_active_worker

        window.close()

        self.assertEqual(['run_1'], client.revoked)
        self.assertEqual([True], cancel_calls)


if __name__ == '__main__':
    unittest.main()
