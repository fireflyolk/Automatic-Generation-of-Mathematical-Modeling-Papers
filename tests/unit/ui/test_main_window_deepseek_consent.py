import tempfile
import unittest
import hashlib
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import ANY, patch

from PySide6.QtWidgets import QApplication

from mathmodel_paper_studio.agents.preflight import (
    AdapterCapabilities, Provider, ProviderStatus, UnavailableReason,
)
from mathmodel_paper_studio.agents.deepseek_model_preferences import DeepSeekModelPreference
from mathmodel_paper_studio.agents.providers.deepseek_api import DeepSeekApiClient
from mathmodel_paper_studio.bootstrap.paths import AppPaths
from mathmodel_paper_studio.domain.models import Project, Run, RunStatus, SourceFile, Stage
from mathmodel_paper_studio.ui.main_window import MainWindow
from mathmodel_paper_studio.workflow.progress import ProgressKind
from mathmodel_paper_studio.workflow.stages import ArtifactDigest

ROOT = Path(r'F:\Codex另一个工作的地方\tmp\mathmodel-paper-studio-run-sidebar\ui-deepseek-consent')
APP = None


class FakeConsentClient:
    def __init__(self):
        self.authorized = []
        self.preferences = []
        self.revoked = []

    def authorize_chargeable_requests(self, run_id, *, preference=None):
        self.authorized.append(run_id)
        self.preferences.append(preference)

    def revoke_chargeable_requests(self, run_id):
        self.revoked.append(run_id)


class FakeWorkflow:
    def __init__(self, client, start_state=None, resume_state=None):
        self.provider_client = client
        self.start_state = start_state or SimpleNamespace(status=RunStatus.PAUSED,
                                                          stage_records=(), diagnostics=())
        self.resume_state = resume_state or SimpleNamespace(status=RunStatus.COMPLETED,
                                                            stage_records=(), diagnostics=())
        self.calls = []

    def start(self, *_args, **_kwargs):
        self.calls.append(('start', _kwargs))
        if isinstance(self.start_state, Exception):
            raise self.start_state
        return self.start_state

    def resume(self, *_args, **_kwargs):
        self.calls.append(('resume', _kwargs))
        if isinstance(self.resume_state, Exception):
            raise self.resume_state
        return self.resume_state

    def cancel(self, *_args, **_kwargs):
        self.calls.append(('cancel', _kwargs))
        return RunStatus.CANCELLED


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
    def __init__(self, status, scope=(True, '范围通过', ())):
        self.status = status
        self.providers = []
        self.scope_calls = []
        self.scope_result = scope

    def probe(self, provider, cancel_event=None):
        self.providers.append(provider)
        self.cancel_event = cancel_event
        return self.status

    def verify_connection(self, provider, cancel_event=None):
        self.providers.append(provider)
        self.cancel_event = cancel_event
        return self.status

    def prepare_selected_scope(self, project, run, readable, planned_output,
                               selected_sources, cancel_event=None):
        self.scope_calls.append((project, run, tuple(readable), planned_output,
                                 tuple(selected_sources), cancel_event))
        return self.scope_result


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
        from mathmodel_paper_studio.agents.codex_text_service import CodexTextService
        self.assertIsInstance(registry.for_provider(Provider.CODEX), CodexTextService)
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
        window.settings_view.provider_combo.setCurrentIndex(0)
        runtime = window.workflow.provider_client.for_provider(Provider.CODEX)
        status = ProviderStatus(
            Provider.CODEX, r'F:\codex\codex.exe', '0.159.3', True,
            None, '本机 no-environment 策略已验证。',
            AdapterCapabilities(True, True, True, 'provider_transport_only',
                                'cli_native_only', 'text_only_no_environment'), True, True,
        )

        with patch.object(runtime, 'verify_connection', return_value=status) as verify:
            window.settings_view.codex_test_button.click()
            pool.run_all()

        verify.assert_called_once_with(Provider.CODEX, ANY)
        self.assertIs(window.codex_probe, runtime)
        self.assertTrue(window.settings_view.selected_status.connection_verified)
        self.assertTrue(window.settings_view.selected_status.available)
        self.assertIn('本机无执行环境策略已通过', window.settings_view.connection_label.text())
        self.assertIn('没有发送题目或文件', window.settings_view.connection_label.text())

    def test_codex_connection_check_is_explicit_and_opens_only_after_verified_result(self):
        temporary = tempfile.TemporaryDirectory(dir=ROOT)
        self.addCleanup(temporary.cleanup)
        status = ProviderStatus(
            Provider.CODEX, r'F:\codex\codex.exe', '0.159.3', False,
            UnavailableReason.EVIDENCE_UNVERIFIED,
            'Codex 静态能力及隔离预检仍未通过。',
            AdapterCapabilities(tool_profile='text_only_no_environment'), False,
        )
        probe = FakeCodexProbe(status)
        pool = DeferredThreadPool()
        window = MainWindow(AppPaths(Path(temporary.name) / 'app'),
                            codex_probe=probe, thread_pool=pool)
        self.addCleanup(window.close)
        window.settings_view.provider_combo.setCurrentIndex(0)

        self.assertIn('本机 CLI 检查尚未执行', window.settings_view.connection_label.text())
        self.assertEqual([], probe.providers)
        window.settings_view.codex_test_button.click()
        self.assertIn('固定提示验证 Codex 连接', window.settings_view.connection_label.text())
        pool.run_all()

        self.assertEqual([Provider.CODEX], probe.providers)
        self.assertIn('本机策略或固定 canary 验证未通过', window.settings_view.connection_label.text())
        self.assertFalse(window.settings_view.selected_status.available)
        self.assertIn('Codex 静态能力及隔离预检仍未通过', window.settings_view.status_label.text())

    def test_codex_review_freshly_probes_local_policy_and_registers_exact_selected_sources(self):
        temporary = tempfile.TemporaryDirectory(dir=ROOT)
        self.addCleanup(temporary.cleanup)
        app_paths = AppPaths(Path(temporary.name) / 'app')
        project = Project('demo', 'Demo', 'projects/demo')
        project_root = app_paths.projects / 'demo'
        inputs = project_root / '00_inputs'
        inputs.mkdir(parents=True)
        problem_path = inputs / 'problem.txt'
        problem_path.write_text('authorized task', encoding='utf-8')
        data_path = inputs / 'data.csv'
        data_path.write_text('x,y\n1,2\n', encoding='utf-8')
        sources = tuple(
            SourceFile(source_id, 'demo', f'00_inputs/{name}',
                       hashlib.sha256(path.read_bytes()).hexdigest(), path.stat().st_size, kind)
            for source_id, name, path, kind in (
                ('problem1', 'problem.txt', problem_path, 'problem'),
                ('data1', 'data.csv', data_path, 'csv'),
            )
        )
        run = Run('run1', 'demo', Provider.CODEX, RunStatus.CREATED, '90_runs/run1')
        fresh_status = ProviderStatus(
            Provider.CODEX, r'F:\codex\codex.exe', '0.159.3', True, None,
            '本机 no-environment 策略已验证。',
            AdapterCapabilities(True, True, True, 'provider_transport_only',
                                'cli_native_only', 'text_only_no_environment'), True,
        )
        stale_status = ProviderStatus(
            Provider.CODEX, None, None, False, UnavailableReason.CLI_MISSING,
            '旧状态', AdapterCapabilities(), False,
        )
        runtime = FakeCodexProbe(fresh_status)
        window = MainWindow(app_paths, codex_probe=runtime)
        self.addCleanup(window.close)

        result = window._prepare_run_review(
            project, run, (problem_path, data_path), ('problem1', 'data1'), stale_status,
            None, lambda _event: None, sources,
        )
        reviewed_run, _readable, _selected_ids, model = result

        self.assertIs(reviewed_run, run)
        self.assertEqual([Provider.CODEX], runtime.providers)
        self.assertEqual(1, len(runtime.scope_calls))
        self.assertEqual(sources, runtime.scope_calls[0][4])
        self.assertTrue(model.provider_ready)
        self.assertTrue(model.scope_verified)
        self.assertIn('本机无执行环境策略已验证', model.summary_text)

    def test_codex_review_stays_blocked_when_fresh_policy_or_scope_fails(self):
        temporary = tempfile.TemporaryDirectory(dir=ROOT)
        self.addCleanup(temporary.cleanup)
        app_paths = AppPaths(Path(temporary.name) / 'app')
        project = Project('demo', 'Demo', 'projects/demo')
        project_root = app_paths.projects / 'demo'
        inputs = project_root / '00_inputs'
        inputs.mkdir(parents=True)
        source_path = inputs / 'problem.txt'
        source_path.write_text('authorized task', encoding='utf-8')
        source = SourceFile('problem1', 'demo', '00_inputs/problem.txt',
                            hashlib.sha256(source_path.read_bytes()).hexdigest(),
                            source_path.stat().st_size, 'problem')
        run = Run('run1', 'demo', Provider.CODEX, RunStatus.CREATED, '90_runs/run1')
        failed_status = ProviderStatus(
            Provider.CODEX, r'F:\codex\codex.exe', '0.159.3', False,
            UnavailableReason.EVIDENCE_UNVERIFIED, '本次策略回读未通过。',
            AdapterCapabilities(tool_profile='text_only_no_environment'), False,
        )
        runtime = FakeCodexProbe(failed_status, scope=(False, '所选文件校验失败', ('哈希变化',)))
        window = MainWindow(app_paths, codex_probe=runtime)
        self.addCleanup(window.close)

        result = window._prepare_run_review(
            project, run, (source_path,), ('problem1',), failed_status,
            None, lambda _event: None, (source,),
        )
        self.assertFalse(result[-1].provider_ready)
        self.assertFalse(result[-1].scope_verified)
        self.assertFalse(result[-1].can_start)

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
            panel.cancel_operation = cancel_operation

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

    def test_reviewed_model_preference_is_bound_to_the_authorized_run(self):
        window, client, _workflow, run, _errors = self.make_window()
        preference = DeepSeekModelPreference('deepseek-v4-pro', 'max')

        window._start_run(run, (), (), deepseek_model_preference=preference)

        self.assertEqual(['run_1'], client.authorized)
        self.assertEqual([preference], client.preferences)

    def test_start_resume_and_cancel_share_one_run_progress_reporter(self):
        window, _client, workflow, run, _errors = self.make_window(
            resume_state=SimpleNamespace(status=RunStatus.PAUSED,
                                         stage_records=(), diagnostics=()),
        )

        window._start_run(run, (), ())
        reporter = window.run_panel.progress_reporter
        started = window.run_panel.operation(lambda: False, lambda _message: None)
        self.assertIs(started.status, RunStatus.PAUSED)
        self.assertIs(workflow.calls[-1][1]['progress'], reporter)

        resumed = window.run_panel.resume_operation(lambda: False, lambda _message: None)
        self.assertIs(resumed.status, RunStatus.PAUSED)
        self.assertIs(workflow.calls[-1][1]['progress'], reporter)

        cancelled = window.run_panel.cancel_operation(lambda: False, lambda _message: None)
        self.assertIs(cancelled, RunStatus.CANCELLED)
        self.assertIs(workflow.calls[-1][1]['progress'], reporter)

    def test_artifact_audit_result_updates_the_in_memory_run_timeline(self):
        window, _client, _workflow, run, _errors = self.make_window()
        window._start_run(run, (), ())
        artifact = ArtifactDigest(
            relative_path='90_runs/run_1/analysis.csv', sha256='a' * 64,
            evidence_id='e' * 32, kind='table', stage=Stage.CALCULATION,
            attempt=0, delivery=1, size_bytes=24,
        )
        window.run_panel.progress_reporter.emit(
            ProgressKind.ARTIFACT_CREATED, '计算产物已保存',
            stage=artifact.stage, attempt=artifact.attempt,
            item_id=artifact.evidence_id, artifact=artifact,
        )
        window._artifact_review_generation = 1
        window._artifact_review_evidence_by_token[1] = (artifact,)
        window._artifact_review_context_by_token[1] = (
            'project_1', 'run_1', window.run_panel.progress_reporter,
        )
        result = SimpleNamespace(
            artifacts=(('table', artifact.relative_path, None, True),),
            paper_path=None, qa_report=None, review_items=(),
        )

        window._artifact_review_succeeded(1, result)

        self.assertIn('已通过', window.run_panel.run_timeline.artifact_list.item(0).text())
        self.assertEqual(2, window.run_panel.run_timeline.event_count)
        self.assertIn('artifact_reviewed',
                      window.run_panel.run_timeline.detail_view.toPlainText())

    def test_stale_artifact_review_cannot_write_into_a_new_run_timeline(self):
        window, _client, _workflow, run, _errors = self.make_window()
        window._start_run(run, (), ())
        old_reporter = window.run_panel.progress_reporter
        artifact = ArtifactDigest(
            relative_path='90_runs/run_1/analysis.csv', sha256='a' * 64,
            evidence_id='e' * 32, kind='table', stage=Stage.CALCULATION,
            attempt=0, delivery=1, size_bytes=24,
        )
        window._artifact_review_generation = 1
        window._artifact_review_evidence_by_token[1] = (artifact,)
        window._artifact_review_context_by_token = {
            1: ('project_1', 'run_1', old_reporter),
        }
        new_reporter = window.run_panel.begin_progress_run('run_2')
        new_reporter.emit(ProgressKind.RUN_STARTED, '新运行开始')
        result = SimpleNamespace(
            artifacts=(('table', artifact.relative_path, None, True),),
            paper_path=None, qa_report=None, review_items=(),
        )

        window._artifact_review_succeeded(1, result)

        self.assertEqual(['run_started'], [item.kind.value for item in window.run_panel.run_timeline.events])
        self.assertEqual(0, window.run_panel.run_timeline.artifact_list.count())

    def test_stale_artifact_review_failure_cannot_write_into_a_new_run_timeline(self):
        window, _client, _workflow, run, _errors = self.make_window()
        window._start_run(run, (), ())
        old_reporter = window.run_panel.progress_reporter
        artifact = ArtifactDigest(
            relative_path='90_runs/run_1/analysis.csv', sha256='a' * 64,
            evidence_id='f' * 32, kind='table', stage=Stage.CALCULATION,
            attempt=0, delivery=1, size_bytes=24,
        )
        window._artifact_review_generation = 1
        window._artifact_review_evidence_by_token[1] = (artifact,)
        window._artifact_review_context_by_token = {
            1: ('project_1', 'run_1', old_reporter),
        }
        new_reporter = window.run_panel.begin_progress_run('run_2')
        new_reporter.emit(ProgressKind.RUN_STARTED, '新运行开始')

        window._artifact_review_failed(1, 'old run review failed')

        self.assertEqual(['run_started'], [item.kind.value for item in window.run_panel.run_timeline.events])
        self.assertEqual(0, window.run_panel.run_timeline.artifact_list.count())

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
