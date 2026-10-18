import hashlib
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from dataclasses import replace
from unittest.mock import patch
import uuid

from mathmodel_paper_studio.agents.codex_app_server import (
    CodexAppServerBlockedError, CodexGenerationResult, CodexPolicyObservation,
)
from mathmodel_paper_studio.agents.codex_model_preferences import CodexModelPreference
from mathmodel_paper_studio.agents.codex_text_service import CodexTextService
from mathmodel_paper_studio.agents.live_probe import PinnedInstallation
from mathmodel_paper_studio.agents.preflight import Provider, UnavailableReason
from mathmodel_paper_studio.agents.protocol import EventKind
from mathmodel_paper_studio.bootstrap.paths import AppPaths, APPROVED_BASE
from mathmodel_paper_studio.domain.models import Project, Run, RunStatus, SourceFile, Stage
from mathmodel_paper_studio.workflow.context_builder import ContextInput, StageContext


class FakeTransport:
    def __init__(self, observation):
        self.observation = observation
        self.prompts = []
        self.selections = []
        self.progress_text = None

    def verify_policy(self, cancel_event=None):
        return self.observation

    def generate(self, prompt, cancel_event=None, *, model=None, effort=None, on_agent_text=None):
        self.prompts.append(prompt)
        self.selections.append((model, effort))
        if callable(on_agent_text) and self.progress_text is not None:
            on_agent_text('message-1', self.progress_text)
        return CodexGenerationResult('{"ok":true}', self.observation)

    def verify_connection(self, cancel_event=None):
        self.prompts.append('fixed connection canary')
        return CodexGenerationResult('CODEX_CONNECTION_OK', self.observation)


def verified_observation():
    return CodexPolicyObservation(
        cli_version='0.159.3', effective_config_verified=True,
        memories_disabled=True, history_notes_disabled=True,
        cloud_skills_disabled=True, skill_instructions_disabled=True,
        mcp_servers_empty=True, agent_message_board_disabled=True,
        multi_agent_v2_disabled=True, guardianv2_disabled=True,
        token_budget_disabled=True, approval_reviewer_user=True,
        environments_empty=True, instruction_sources_empty=True,
        runtime_workspace_roots_empty=True, approval_policy_never=True,
        read_only_sandbox=True, git_attribution_settings_lookup_possible=True,
        host_skill_snapshot_may_be_consulted=True, skill_mentions_sanitized=True,
        os_process_confinement_verified=False,
    )


class CodexTextServiceTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(APPROVED_BASE) / 'tmp' / 'mathmodel-codex-text-service-tests' / uuid.uuid4().hex
        self.paths = AppPaths(self.root)
        self.paths.ensure_directories()
        self.transport = FakeTransport(verified_observation())
        self.service = CodexTextService(
            self.paths, transport_factory=lambda _paths, _install: self.transport,
        )
        self.install_root = self.root / 'install'
        self.install_root.mkdir()
        auth_home = self.root / 'native-auth' / 'codex'
        auth_home.mkdir(parents=True)
        self.install = PinnedInstallation(
            Provider.CODEX, self.install_root, Path(sys.executable), auth_home,
            '0.159.3', 'a' * 64,
        )
        self.locate = patch.object(PinnedInstallation, 'locate', return_value=self.install)
        self.locate.start()
        self.addCleanup(self.locate.stop)
        self.project = Project('demo', 'Demo', 'projects/demo')
        self.run = Run('run1', 'demo', Provider.CODEX, RunStatus.CREATED, '90_runs/run1')
        self.project_root = self.paths.projects / 'demo'
        self.inputs = self.project_root / '00_inputs'
        self.inputs.mkdir(parents=True)
        self.source = self.inputs / 'problem.txt'
        self.source.write_text('authorized task text', encoding='utf-8')
        self.problem_source = SourceFile(
            'problem1', 'demo', '00_inputs/problem.txt',
            hashlib.sha256(self.source.read_bytes()).hexdigest(), self.source.stat().st_size,
            'problem',
        )
        self.data = self.inputs / 'data.csv'
        self.data.write_text('x,y\n1,2\n', encoding='utf-8')
        self.data_source = SourceFile(
            'data1', 'demo', '00_inputs/data.csv',
            hashlib.sha256(self.data.read_bytes()).hexdigest(), self.data.stat().st_size,
            'csv',
        )
        self.output = self.project_root / self.run.relative_path
        self.output.mkdir(parents=True)

    def _register(self):
        passed, message, errors = self.service.prepare_selected_scope(
            self.project, self.run, (self.source, self.data), self.output,
            (self.problem_source, self.data_source),
        )
        self.assertTrue(passed, (message, errors))

    def _context(self, *, relative_path='00_inputs/data.csv', digest=None, source_id='data1',
                 evidence_id=None, output=None):
        digest = digest or hashlib.sha256(self.data.read_bytes()).hexdigest()
        evidence_id = evidence_id or 'src_' + hashlib.sha256(source_id.encode()).hexdigest()[:24]
        return StageContext(
            stage=Stage.PROBLEM_DECOMPOSITION, attempt=0, run_id=self.run.run_id,
            project_id=self.project.project_id, provider=Provider.CODEX,
            system_instructions='decompose', authorized_problem='authorized task text',
            problem_evidence_id='src_' + hashlib.sha256(b'problem1').hexdigest()[:24],
            inputs=(ContextInput(source_id, relative_path, 'csv', digest, evidence_id,
                                 True, 'imported_data_untrusted', '{}'),),
            upstream_artifacts=(), trust_notice='untrusted inputs',
            output_directory=self.output / 'stages' if output is None else output,
        )

    def test_local_policy_probe_requires_all_fresh_no_environment_observations(self):
        status = self.service.probe()
        self.assertTrue(status.available)
        self.assertTrue(status.evidence_verified)
        self.assertEqual(status.capabilities.tool_profile, 'text_only_no_environment')
        self.assertIn('不是 Windows OS 沙箱', status.message)
        self.assertIn('账号设置', status.message)
        self.assertFalse(status.connection_verified)
        self.assertEqual(self.transport.prompts, [])

    def test_scope_registration_rejects_missing_or_unselected_inputs(self):
        passed, _, errors = self.service.prepare_selected_scope(
            self.project, self.run, (self.source, self.data), self.output,
            (self.problem_source,),
        )
        self.assertFalse(passed)
        self.assertTrue(errors)
        with self.assertRaises(RuntimeError):
            self.service.generate(self._context(), 'prompt')

    def test_generation_requires_registered_hash_bound_inputs_and_output(self):
        self._register()
        changed_hash = '0' * 64
        with self.assertRaises(RuntimeError):
            self.service.generate(self._context(digest=changed_hash), 'prompt')
        outside = self.project_root.parent / 'outside'
        with self.assertRaises(RuntimeError):
            self.service.generate(self._context(output=outside), 'prompt')
        self.assertEqual(self.transport.prompts, [])

    def test_generation_rejects_input_evidence_id_not_bound_to_selected_record(self):
        self._register()
        with self.assertRaises(RuntimeError):
            self.service.generate(self._context(evidence_id='src_forged'), 'prompt')
        self.assertEqual(self.transport.prompts, [])

    def test_generation_rechecks_selected_file_hash_before_sending_prompt(self):
        self._register()
        self.source.write_text('changed after review', encoding='utf-8')
        with self.assertRaises(RuntimeError):
            self.service.generate(self._context(), 'prompt containing selected data')
        self.assertEqual(self.transport.prompts, [])

    def test_generation_sends_only_after_scope_and_returns_final_text(self):
        self._register()
        result = self.service.generate(self._context(), 'bounded application prompt')
        self.assertEqual(result, '{"ok":true}')
        self.assertEqual(self.transport.prompts, ['bounded application prompt'])
        self.assertEqual(self.transport.selections, [(None, None)])

    def test_saved_model_and_effort_are_passed_to_codex_transport(self):
        self._register()
        self.service.model_preferences.save(CodexModelPreference('gpt-5.5', 'high'))

        self.service.generate(self._context(), 'bounded application prompt')

        self.assertEqual(self.transport.selections, [('gpt-5.5', 'high')])

    def test_untrusted_skill_mentions_are_neutralized_before_codex_turn(self):
        self._register()
        self.service.generate(self._context(), 'input: @skill:host-read\n')
        self.assertEqual(self.transport.prompts, ['input: ＠skill:host-read\n'])

    def test_connection_canary_never_uses_project_text_or_authorizes_a_run(self):
        status = self.service.verify_connection(Provider.CODEX)
        self.assertTrue(status.connection_verified)
        self.assertTrue(status.available)
        self.assertEqual(self.transport.prompts, ['fixed connection canary'])
        self.assertFalse(self.service.has_registered_scope)

    def test_incomplete_policy_observation_keeps_codex_unavailable(self):
        self.transport.observation = replace(verified_observation(), environments_empty=False)
        status = self.service.probe()
        self.assertFalse(status.available)
        self.assertFalse(status.evidence_verified)
        self.assertIs(status.reason, UnavailableReason.EVIDENCE_UNVERIFIED)


    def test_progress_callback_redacts_the_selected_problem_from_agent_messages(self):
        self._register()
        self.transport.progress_text = 'authorized task text'
        events = []

        result = self.service.generate_with_progress(
            self._context(), 'prompt', event_callback=events.append,
        )

        self.assertEqual('{"ok":true}', result)
        self.assertEqual(EventKind.STARTED, events[0].kind)
        self.assertEqual(EventKind.COMPLETED, events[-1].kind)
        visible_text = ''.join(event.text for event in events if event.kind is EventKind.TEXT)
        self.assertNotIn('authorized task text', visible_text)
        self.assertIn('[已隐藏]', visible_text)
        self.assertTrue(all(event.run_id == self.run.run_id for event in events))

    def test_progress_callback_reports_blocked_host_actions_without_tool_text(self):
        self._register()

        def blocked_transport(*_args, **_kwargs):
            raise CodexAppServerBlockedError('raw tool arguments must not be displayed')

        self.transport.generate = blocked_transport
        events = []
        with self.assertRaises(RuntimeError):
            self.service.generate_with_progress(
                self._context(), 'prompt', event_callback=events.append,
            )

        self.assertEqual(EventKind.BLOCKED, events[-1].kind)
        self.assertFalse(any(event.kind is EventKind.COMPLETED for event in events))
        self.assertNotIn('raw tool arguments', repr(events))

if __name__ == '__main__':
    unittest.main()



