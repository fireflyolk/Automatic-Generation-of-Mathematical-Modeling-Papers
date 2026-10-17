"""Local process fixtures only; these tests are not live provider readiness proof."""
from dataclasses import replace
import hashlib
import json
from pathlib import Path
import sys
import threading
import time
import unittest
import uuid
from unittest.mock import patch, PropertyMock

from mathmodel_paper_studio.agents.live_probe import (
    NativeLiveProbe, PinnedInstallation, LiveEvidence, ProbeAssessment, _SEAL,
)
from mathmodel_paper_studio.agents.live_runtime import LiveProviderService
from mathmodel_paper_studio.agents.preflight import Provider, ProviderStatus, AdapterCapabilities, UnavailableReason
from mathmodel_paper_studio.bootstrap.paths import AppPaths, APPROVED_BASE
from mathmodel_paper_studio.domain.models import Project, Run, RunStatus, Stage
from mathmodel_paper_studio.security.path_guard import executable_digest
from mathmodel_paper_studio.security.sandbox_gate import GateBlocked, ProbeResult
from mathmodel_paper_studio.workflow.context_builder import StageContext


class _FixtureBase(unittest.TestCase):
    def setUp(self):
        self.root = Path(APPROVED_BASE) / 'tmp/mathmodel-paper-studio-20261016-closure/broker' / uuid.uuid4().hex
        self.paths = AppPaths(self.root)
        self.paths.ensure_directories()
        self.home = self.root / 'native-auth/claude'
        self.home.mkdir(parents=True)

    def install(self, provider=Provider.CLAUDE):
        return PinnedInstallation(provider, self.root, Path(sys._base_executable), self.home,
                                  '2.1.291' if provider is Provider.CLAUDE else '0.159.3',
                                  executable_digest(sys._base_executable))



class NativeBaselineTests(_FixtureBase):
    def test_baseline_only_version_and_local_auth_status_no_login_or_model(self):
        install = self.install()
        probe = NativeLiveProbe(self.paths, self.root)
        captures = [(0, '2.1.291 (Claude Code)'), (0, '{"loggedIn":true,"email":"never-return"}')]
        with patch.object(PinnedInstallation, 'locate', return_value=install), patch.object(probe, '_capture', side_effect=captures) as capture:
            assessment = probe.verify(Provider.CLAUDE)
        self.assertFalse(assessment.status.available)
        self.assertIsNone(assessment.evidence)
        self.assertIn('hooks', assessment.status.message)
        self.assertNotIn('never-return', repr(assessment))
        self.assertEqual([call.args[1] for call in capture.call_args_list], [('--version',), ('auth','status','--json')])

    def test_missing_login_unknown_auth_and_version_mismatch_remain_blocked(self):
        install = self.install()
        for captures, reason in [([(0,'2.1.291'),(1,'{"loggedIn":false}')], UnavailableReason.LOGIN_REQUIRED),
                                 ([(0,'2.1.291'),(0,'garbled')], UnavailableReason.AUTH_UNVERIFIED),
                                 ([(0,'99.0.0')], UnavailableReason.VERSION_UNSUPPORTED)]:
            probe = NativeLiveProbe(self.paths, self.root)
            with self.subTest(captures=captures), patch.object(PinnedInstallation,'locate',return_value=install), patch.object(probe,'_capture',side_effect=captures):
                assessment = probe.verify(Provider.CLAUDE)
                self.assertFalse(assessment.status.available)
                self.assertEqual(assessment.status.reason, reason)

    def test_codex_local_status_without_canary_remains_unverified(self):
        install = self.install(Provider.CODEX)
        probe = NativeLiveProbe(self.paths, self.root)
        with patch.object(PinnedInstallation,'locate',return_value=install), patch.object(
                probe,'_capture',side_effect=[(0,'codex-cli 0.159.3'),(0,'Logged in using ChatGPT')]):
            assessment = probe.verify(Provider.CODEX)
        self.assertFalse(assessment.status.available)
        self.assertFalse(assessment.status.connection_verified)
        self.assertIn('尚未发送固定文本连接请求', assessment.status.message)
        self.assertIsNone(assessment.evidence)

    def test_explicit_codex_connection_probe_marks_only_text_transport_verified(self):
        install = self.install(Provider.CODEX)
        probe = NativeLiveProbe(self.paths, self.root)
        from mathmodel_paper_studio.agents.providers.codex_cli import codex_launch_arguments
        canary = '\n'.join((
            json.dumps({'type':'thread.started','thread_id':'thread_probe'}),
            json.dumps({'type':'turn.started'}),
            json.dumps({'type':'item.completed','item':{'id':'i1','type':'agent_message','text':'CODEX_CONNECTION_OK'}}),
            json.dumps({'type':'turn.completed','usage':{'input_tokens':10,'cached_input_tokens':0,'output_tokens':1}}),
        ))
        captures = [(0,'codex-cli 0.159.3'),(0,'Logged in using ChatGPT'),(0,canary)]
        with patch.object(PinnedInstallation,'locate',return_value=install), patch.object(
                probe,'_capture',side_effect=captures) as capture:
            assessment = probe.verify(Provider.CODEX, run_model_canary=True)
        self.assertFalse(assessment.status.available)
        self.assertFalse(assessment.status.evidence_verified)
        self.assertTrue(assessment.status.connection_verified)
        self.assertEqual(assessment.status.capabilities.tool_profile, 'unverified')
        self.assertIsNone(assessment.evidence)
        self.assertEqual(capture.call_args_list[-1].args[1], codex_launch_arguments())
        self.assertIn('仅验证文本连通', assessment.status.message)

    def test_codex_tool_event_or_wrong_canary_never_mints_live_evidence(self):
        install = self.install(Provider.CODEX)
        probe = NativeLiveProbe(self.paths, self.root)
        bad_canary = json.dumps({'type':'thread.started','thread_id':'thread_probe'}) + '\n' + json.dumps({'type':'turn.started'}) + '\n' + json.dumps(
            {'type':'item.completed','item':{'id':'i1','type':'command_execution','command':'private'}})
        captures = [(0,'codex-cli 0.159.3'),(0,'Logged in using ChatGPT'),(0,bad_canary)]
        with patch.object(PinnedInstallation,'locate',return_value=install), patch.object(
                probe,'_capture',side_effect=captures):
            assessment = probe.verify(Provider.CODEX, run_model_canary=True)
        self.assertFalse(assessment.status.available)
        self.assertFalse(assessment.status.evidence_verified)
        self.assertFalse(assessment.status.connection_verified)
        self.assertIsNone(assessment.evidence)

    def test_native_capture_real_local_process_with_bounded_stderr(self):
        install=self.install()
        probe=NativeLiveProbe(self.paths,self.root)
        arguments=('-I','-B','-c',"import sys;print('local version');sys.stderr.write('opaque private metadata')")
        with patch.object(PinnedInstallation,'locate',return_value=install):
            code,text=probe._capture(install,arguments)
        self.assertEqual(code,0)
        self.assertEqual(text.strip(),'local version')
        self.assertNotIn('opaque',text)

    def test_native_capture_cancel_terminates_local_process(self):
        install=self.install()
        probe=NativeLiveProbe(self.paths,self.root)
        event=threading.Event()
        timer=threading.Timer(.2,event.set)
        timer.start()
        started=time.monotonic()
        try:
            with patch.object(PinnedInstallation,'locate',return_value=install),self.assertRaisesRegex(ValueError,'取消'):
                probe._capture(install,('-I','-B','-c','import time;time.sleep(10)'),event)
            self.assertLess(time.monotonic()-started,3)
        finally:
            timer.cancel()

    def test_pinned_installation_rejects_absent_or_unpinned_image(self):
        with self.assertRaises(FileNotFoundError):
            PinnedInstallation.locate(self.root,Provider.CODEX)
        binary=self.root/'codex/codex.exe'
        binary.parent.mkdir()
        binary.write_bytes(b'MZ'+b'not-an-approved-cli')
        with self.assertRaisesRegex(ValueError,'摘要'):
            PinnedInstallation.locate(self.root,Provider.CODEX)

    def test_unsealed_stale_or_changed_profile_cannot_authorize(self):
        install = self.install()
        now = time.monotonic()
        evidence = LiveEvidence(install, install.profile_digest, now, now+20, (), True, True, True, _SEAL)
        for altered in [replace(evidence,_seal=object()), replace(evidence,expires=now-1),
                        replace(evidence,profile_digest='0'*64), replace(evidence,tool_inventory=('Read',)),
                        replace(evidence,hooks_disabled=False),replace(evidence,cancellation_verified=False),
                        replace(evidence,canaries_verified=False)]:
            with self.subTest(altered=altered), self.assertRaises(ValueError):
                altered.require(install)


class RealLocalProcessContractTests(_FixtureBase):
    def setUp(self):
        super().setUp()
        self.service = LiveProviderService(self.paths, installation_root=self.root)
        self.project = Project('demo','测试','projects/demo')
        self.run = Run('r1','demo',Provider.CLAUDE,RunStatus.CREATED,'90_runs/r1')
        self.output = self.paths.projects / 'demo/90_runs/r1/out'
        self.output.mkdir(parents=True)
        self.source = self.paths.projects / 'demo/selected.txt'
        self.source.write_text('selected',encoding='utf-8')
        self.context = StageContext(Stage.PAPER_DRAFTING,0,'r1','demo',Provider.CLAUDE,
                                    'application instructions',None,None,(),(),'untrusted imported data',self.output)

    def authority(self, arguments):
        install = self.install()
        now = time.monotonic()
        evidence = LiveEvidence(install, install.profile_digest,now,now+60,(),True,True,True,_SEAL)
        caps = AdapterCapabilities(True,True,True,'provider_transport_only','cli_native_only','text_only')
        status = ProviderStatus(Provider.CLAUDE,str(install.executable),install.version,True,None,'local fixture only',caps,True)
        return install, evidence, status

    def run_local(self, text, exit_code=0, suffix='', cancel_event=None):
        records = [{'type':'system','subtype':'init','tools':[],'mcp_servers':[]},
                   {'type':'assistant','message':{'id':'m1','content':[{'type':'text','text':text}]}},
                   {'type':'result','subtype':'success','is_error':False,'num_turns':1,'result':text}]
        program = 'import sys;sys.stdout.write(' + repr('\n'.join(json.dumps(item) for item in records)+'\n') + ');sys.stdout.flush();' + suffix + 'sys.exit(' + str(exit_code) + ')'
        arguments = ('-I','-B','-c',program)
        # A local Python protocol fixture deliberately replaces installation
        # identity and probe in memory. It does not mint persisted readiness.
        with patch.object(PinnedInstallation,'launch_arguments',new_callable=PropertyMock,return_value=arguments):
            install,evidence,status = self.authority(arguments)
            with patch.object(PinnedInstallation,'locate',return_value=install), patch.object(self.service._probe,'verify',return_value=ProbeAssessment(status,evidence)):
                passed,_,errors = self.service.prepare_scope(self.project,self.run,(self.source,),self.output)
                self.assertTrue(passed,errors)
                scope_result = ProbeResult(status, True, True, True, True)
                with patch.object(self.service, '_probe_exact_scope', return_value=scope_result):
                    return self.service.generate(self.context,'prompt line',cancel_event)

    def test_generation_requires_an_independent_exact_scope_probe(self):
        install, evidence, status = self.authority(())
        with patch.object(PinnedInstallation, 'locate', return_value=install), patch.object(
                self.service._probe, 'verify', return_value=ProbeAssessment(status, evidence)):
            self.assertTrue(self.service.prepare_scope(
                self.project, self.run, (self.source,), self.output)[0])
            with patch('mathmodel_paper_studio.agents.live_runtime.ProcessRunner') as runner:
                with self.assertRaisesRegex(GateBlocked, '独立'):
                    self.service.generate(self.context, 'prompt line')
                runner.assert_not_called()

    def test_real_job_clean_exit_returns_raw_json_before_display_redaction(self):
        text = '{"preserve":"prompt line","n":4}'
        self.assertEqual(self.run_local(text), text)

    def test_real_nonzero_exit_discards_provisional_text_and_completed(self):
        with self.assertRaisesRegex(GateBlocked,'丢弃'):
            self.run_local('{"partial":true}',7)

    def test_real_trailing_malformed_line_discards_provisional_text(self):
        with self.assertRaisesRegex(GateBlocked,'丢弃'):
            self.run_local('partial',suffix="sys.stdout.write('{bad\\n');sys.stdout.flush();")

    def test_changed_input_after_scope_registration_is_rejected_before_launch(self):
        install = self.install()
        now=time.monotonic()
        evidence=LiveEvidence(install,install.profile_digest,now,now+60,(),True,True,True,_SEAL)
        caps=AdapterCapabilities(True,True,True,'provider_transport_only','cli_native_only','text_only')
        status=ProviderStatus(Provider.CLAUDE,str(install.executable),install.version,True,None,'fixture',caps,True)
        with patch.object(PinnedInstallation,'locate',return_value=install), patch.object(self.service._probe,'verify',return_value=ProbeAssessment(status,evidence)):
            self.assertTrue(self.service.prepare_scope(self.project,self.run,(self.source,),self.output)[0])
            self.source.write_text('changed',encoding='utf-8')
            with patch('mathmodel_paper_studio.agents.process_runner.NativeJobProcess') as launch, self.assertRaisesRegex(GateBlocked,'快照'):
                self.service.generate(self.context,'prompt')
            launch.assert_not_called()

    def test_cancel_revokes_scope_registration(self):
        self.service.cancel('r1')
        with self.assertRaisesRegex(GateBlocked,'尚未注册'):
            self.service.generate(self.context,'prompt')

    def test_real_cancel_discards_partial_text_and_stops_job(self):
        cancel_event=threading.Event()
        timer=threading.Timer(.3,cancel_event.set)
        timer.start()
        started=time.monotonic()
        try:
            with self.assertRaises(GateBlocked):
                self.run_local('partial',suffix='import time;time.sleep(10);',cancel_event=cancel_event)
            self.assertLess(time.monotonic()-started,3)
        finally:
            timer.cancel()

    def test_cancelled_before_generation_never_launches(self):
        install=self.install()
        now=time.monotonic()
        evidence=LiveEvidence(install,install.profile_digest,now,now+60,(),True,True,True,_SEAL)
        caps=AdapterCapabilities(True,True,True,'provider_transport_only','cli_native_only','text_only')
        status=ProviderStatus(Provider.CLAUDE,str(install.executable),install.version,True,None,'fixture',caps,True)
        with patch.object(PinnedInstallation,'locate',return_value=install),patch.object(self.service._probe,'verify',return_value=ProbeAssessment(status,evidence)):
            self.assertTrue(self.service.prepare_scope(self.project,self.run,(self.source,),self.output)[0])
            event=threading.Event()
            event.set()
            with patch('mathmodel_paper_studio.agents.process_runner.NativeJobProcess') as launch,self.assertRaisesRegex(GateBlocked,'取消'):
                self.service.generate(self.context,'prompt',event)
            launch.assert_not_called()

    def test_registered_scope_does_not_transfer_to_other_run_or_output(self):
        install=self.install()
        now=time.monotonic()
        evidence=LiveEvidence(install,install.profile_digest,now,now+60,(),True,True,True,_SEAL)
        caps=AdapterCapabilities(True,True,True,'provider_transport_only','cli_native_only','text_only')
        status=ProviderStatus(Provider.CLAUDE,str(install.executable),install.version,True,None,'fixture',caps,True)
        with patch.object(PinnedInstallation,'locate',return_value=install),patch.object(self.service._probe,'verify',return_value=ProbeAssessment(status,evidence)):
            self.assertTrue(self.service.prepare_scope(self.project,self.run,(self.source,),self.output)[0])
            for context in (replace(self.context,run_id='r2'),replace(self.context,output_directory=self.paths.temp),
                            replace(self.context,provider=Provider.CODEX),replace(self.context,output_directory=None)):
                with self.subTest(context=context),self.assertRaises(GateBlocked):
                    self.service.generate(context,'prompt')
