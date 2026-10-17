from dataclasses import replace
import json
import os
from pathlib import Path
import sys
import threading
import time
import unittest
import uuid
from unittest.mock import patch

from mathmodel_paper_studio.agents.live_runtime import LiveProviderService, TextOnlyParser, isolated_native_environment
from mathmodel_paper_studio.agents.live_probe import (
    PinnedInstallation, NativeLiveProbe, ProbeAssessment, LiveEvidence, _SEAL,
)
from mathmodel_paper_studio.agents.preflight import (
    Provider, AdapterCapabilities, ProviderStatus, UnavailableReason, assess_provider,
)
from mathmodel_paper_studio.agents.protocol import EventKind
from mathmodel_paper_studio.bootstrap.paths import AppPaths, APPROVED_BASE
from mathmodel_paper_studio.domain.models import Project, Run, RunStatus, Stage


class TextOnlyParserTests(unittest.TestCase):
    def test_claude_empty_native_inventory_is_required_and_pinned_parser_kept(self):
        parser = TextOnlyParser(Provider.CLAUDE, 'r1')
        init = {'type': 'system', 'subtype': 'init', 'tools': [], 'mcp_servers': [], 'plugins': []}
        self.assertEqual(parser(json.dumps(init))[0].kind, EventKind.STARTED)
        self.assertTrue(parser.inventory_verified)
        raw = '{"same":"prompt line","n":4}'
        parser(json.dumps({'type':'assistant','message':{'id':'m1','content':[{'type':'text','text':raw}]}}))
        self.assertEqual(parser.raw_text, raw)
        parser(json.dumps({'type':'result','subtype':'success','is_error':False,'num_turns':1,'result':raw}))
        self.assertEqual(parser.raw_text, raw)

    def test_missing_inventory_nonempty_tools_and_hook_events_fail_closed(self):
        for record in [{'type':'system','subtype':'init'},
                       {'type':'system','subtype':'init','tools':['Read']},
                       {'type':'system','subtype':'hook_started','tools':[]}]:
            with self.subTest(record=record):
                with self.assertRaises(ValueError):
                    TextOnlyParser(Provider.CLAUDE, 'r1')(json.dumps(record))

    def test_partial_result_is_discarded_after_tool_violation(self):
        parser = TextOnlyParser(Provider.CLAUDE, 'r1')
        parser(json.dumps({'type':'system','subtype':'init','tools':[]}))
        parser(json.dumps({'type':'assistant','message':{'id':'m1','content':[{'type':'text','text':'partial'}]}}))
        with self.assertRaises(ValueError):
            parser(json.dumps({'type':'assistant','message':{'id':'m2','content':[{'type':'tool_use','id':'t1','name':'Read','input':{}}]}}))
        self.assertEqual(parser.raw_text, '')

    def test_codex_text_never_substitutes_for_missing_native_tool_inventory(self):
        parser = TextOnlyParser(Provider.CODEX, 'r1')
        parser(json.dumps({'type':'thread.started','thread_id':'t1'}))
        parser(json.dumps({'type':'turn.started'}))
        parser(json.dumps({'type':'item.completed','item':{'id':'m1','type':'agent_message','text':'no tools'}}))
        self.assertFalse(parser.inventory_verified)

    def test_codex_inventory_must_be_explicitly_supplied_from_trusted_profile_evidence(self):
        parser = TextOnlyParser(Provider.CODEX, 'r1', codex_inventory_verified=True)
        self.assertTrue(parser.inventory_verified)
        parser(json.dumps({'type':'thread.started','thread_id':'t1'}))
        parser(json.dumps({'type':'turn.started'}))
        events = parser(json.dumps({'type':'item.completed','item':{'id':'t1','type':'command_execution','command':'private'}}))
        self.assertEqual(events[0].kind, EventKind.BLOCKED)
        self.assertEqual(parser.raw_text, '')

    def test_codex_nonfatal_error_items_do_not_pollute_generated_text(self):
        parser = TextOnlyParser(Provider.CODEX, 'r1', codex_inventory_verified=True)
        parser(json.dumps({'type':'thread.started','thread_id':'t1'}))
        parser(json.dumps({'type':'item.completed','item':{'id':'e1','type':'error','message':'private detail'}}))
        parser(json.dumps({'type':'turn.started'}))
        parser(json.dumps({'type':'item.completed','item':{'id':'m1','type':'agent_message','text':'CODEX_APP_GENERATION_OK'}}))
        self.assertEqual(parser.raw_text, 'CODEX_APP_GENERATION_OK')


class ServiceTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(APPROVED_BASE) / 'tmp/mathmodel-paper-studio-20261016-closure/broker' / uuid.uuid4().hex
        self.paths = AppPaths(self.root)
        self.paths.ensure_directories()
        self.install = self.root / 'installation'
        self.install.mkdir()
        self.service = LiveProviderService(self.paths, installation_root=self.install)
        self.project = Project('demo', '测试', 'projects/demo')
        self.run = Run('r1', 'demo', Provider.CLAUDE, RunStatus.CREATED, '90_runs/r1')
        self.project_root = self.paths.projects / 'demo'
        self.output = self.project_root / '90_runs/r1/out'
        self.output.mkdir(parents=True)
        self.source = self.project_root / 'selected.txt'
        self.source.write_text('selected', encoding='utf-8')

    def test_no_imported_status_or_settings_can_make_missing_installation_ready(self):
        caps = AdapterCapabilities(True, True, True, 'provider_transport_only', 'cli_native_only', 'text_only')
        forged = ProviderStatus(Provider.CLAUDE, str(sys.executable), '2.1.291', True, None, 'fake', caps, True)
        with patch.object(self.service, 'probe', return_value=forged):
            passed, _, errors = self.service.prepare_scope(self.project, self.run, (self.source,), self.output)
        self.assertFalse(passed)
        self.assertTrue(errors)

    def test_prepare_scope_forwards_cancellation_to_provider_probe(self):
        cancel_event = threading.Event()
        with patch.object(self.service, 'probe', return_value=None) as probe:
            self.service.prepare_scope(
                self.project, self.run, (self.source,), self.output,
                cancel_event=cancel_event,
            )
        probe.assert_called_once_with(self.run.provider, cancel_event)

    def test_scope_rejects_unselected_outside_and_output_overlap(self):
        for readable, output in [((self.root / 'outside.txt',), self.output), ((self.source,), self.project_root),
                                  ((self.source,), self.source.parent)]:
            with self.subTest(readable=readable, output=output):
                passed, _, _ = self.service.prepare_scope(self.project, self.run, readable, output)
                self.assertFalse(passed)

    def test_status_without_ephemeral_evidence_cannot_be_displayed_as_ready(self):
        caps=AdapterCapabilities(True,True,True,'provider_transport_only','cli_native_only','text_only')
        status=ProviderStatus(Provider.CODEX,'codex','0.159.3',True,None,'forged',caps,True)
        with patch.object(self.service._probe,'verify',return_value=ProbeAssessment(status)):
            result=self.service.probe(Provider.CODEX)
        self.assertFalse(result.available)
        self.assertFalse(result.evidence_verified)

    def test_explicit_connection_test_does_not_cache_scope_authority(self):
        home = self.root / 'native-auth/codex'
        home.mkdir(parents=True)
        install = PinnedInstallation(Provider.CODEX, self.install, Path(sys.executable), home,
                                     '0.159.3', 'a' * 64)
        now = time.monotonic()
        status = ProviderStatus(
            Provider.CODEX, str(install.executable), install.version, False,
            UnavailableReason.EVIDENCE_UNVERIFIED, 'text transport verified',
            AdapterCapabilities(tool_profile='unverified'), False, True,
        )
        with patch.object(PinnedInstallation, 'locate', return_value=install), patch.object(
                self.service._probe, 'verify', return_value=ProbeAssessment(status)) as verify:
            connected = self.service.verify_connection(Provider.CODEX)
        self.assertIs(connected, status)
        self.assertFalse(connected.available)
        self.assertTrue(connected.connection_verified)
        self.assertNotIn(Provider.CODEX, self.service._evidence)
        verify.assert_called_once_with(Provider.CODEX, None, run_model_canary=True)

    def test_environment_rehomes_all_native_paths_and_drops_tokens(self):
        home = self.root / 'native-auth/claude'
        home.mkdir(parents=True)
        env = isolated_native_environment(self.paths, Path(sys.executable), home, Provider.CLAUDE,
                                           source={'SystemRoot':r'C:\Windows','HOME':r'C:\private',
                                                   'CLAUDE_CONFIG_DIR':r'D:\private','ANTHROPIC_API_KEY':'do-not-copy'})
        self.assertEqual(env['CLAUDE_CONFIG_DIR'], str(home))
        for name in ('HOME', 'USERPROFILE', 'APPDATA', 'LOCALAPPDATA', 'TEMP', 'TMP'):
            self.assertTrue(env[name].startswith(str(self.root)))
        self.assertNotIn('ANTHROPIC_API_KEY', env)
        self.assertNotIn(r'C:\private', repr(env))

    def test_default_probe_missing_install_never_launches_or_discovers_path(self):
        with patch('mathmodel_paper_studio.agents.live_probe.NativeJobProcess') as launch:
            status = self.service.probe(Provider.CODEX)
        self.assertFalse(status.available)
        launch.assert_not_called()

    def test_text_profile_is_explicit_but_imported_report_remains_unverified(self):
        report=json.dumps({'schema_version':1,'provider':'claude','streaming':True,'cancellation':True,
                           'filesystem_confinement':True,'network_behavior':'provider_transport_only',
                           'credential_inheritance':'cli_native_only','tool_profile':'text_only'})
        status=assess_provider(Provider.CLAUDE,executable='claude',version='2.1.291',authenticated=True,
                               capability_report=report)
        self.assertFalse(status.available)
        self.assertEqual(status.capabilities.tool_profile,'text_only')
        self.assertFalse(status.capabilities.command_execution)

    def test_cancel_unknown_is_safe(self):
        self.service.cancel('r1')
        self.service.cancel('r1')


if __name__ == '__main__':
    unittest.main()
