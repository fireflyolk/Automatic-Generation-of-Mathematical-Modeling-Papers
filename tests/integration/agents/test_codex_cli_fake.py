from dataclasses import replace
from pathlib import Path
import sys
import threading
import time
import unittest
import uuid

from mathmodel_paper_studio.bootstrap.paths import APPROVED_BASE, AppPaths
from mathmodel_paper_studio.domain.models import ScopeManifest, Stage
from mathmodel_paper_studio.agents.preflight import Provider, ProviderStatus, AdapterCapabilities
from mathmodel_paper_studio.agents.protocol import AgentRequest, EventKind
from mathmodel_paper_studio.agents.process_runner import ProcessRunner, ProcessLimits
from mathmodel_paper_studio.agents.providers.codex_cli import CodexCliAdapter, CodexEventParser
from mathmodel_paper_studio.security.scope_manifest import build_scope
from mathmodel_paper_studio.security.sandbox_gate import ScopeGate, ProbeResult

FIXTURE = Path(__file__).resolve().parents[2] / 'fixtures/cli/task4_codex_jsonl.py'


class CodexFakeProcessTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(APPROVED_BASE) / 'tmp/mathmodel-paper-studio-20261015/task4' / uuid.uuid4().hex
        self.paths = AppPaths(self.root)
        self.paths.ensure_directories()
        self.project = self.paths.projects / 'demo'
        (self.project / 'out').mkdir(parents=True)
        self.scope = build_scope(self.root, ScopeManifest('s1', 'r1', Provider.CODEX, 'projects/demo',
                                 (), ('out',), (Stage.PAPER_DRAFTING,), True), Path(sys.executable),
                                 '0.159.3', 'restricted_read_write')
        caps = AdapterCapabilities(True, True, True, 'provider_transport_only', 'cli_native_only', 'restricted_read_write')
        status = ProviderStatus(Provider.CODEX, str(self.scope.executable), '0.159.3', True, None, 'test fake only', caps, True)
        # Synthetic application callback authorizes this known test script only.
        # No fake probe/status is ever injected into a production adapter.
        self.gate = ScopeGate(trusted_probe=lambda scope: ProbeResult(status, True, True, True, True))

    def configure(self, mode, limits=None):
        arguments = ('-I', '-B', str(FIXTURE), mode)
        scope = replace(self.scope, launch_arguments=arguments)
        request = AgentRequest(scope, Stage.PAPER_DRAFTING, 'private fixture prompt')
        grant = self.gate.authorize(scope)
        runner = ProcessRunner(self.gate, self.paths, limits or ProcessLimits(wall_seconds=5))
        stream = runner.stream(request, grant, arguments, CodexEventParser('r1'))
        return request, runner, stream

    def test_partial_utf8_crlf_and_complete_final_json_without_newline(self):
        for mode in ('success', 'final_no_newline'):
            with self.subTest(mode=mode):
                _, _, stream = self.configure(mode)
                events = list(stream)
                self.assertEqual(events[-1].kind, EventKind.COMPLETED)
                self.assertEqual(''.join(event.text for event in events if event.kind == EventKind.TEXT), '中文结果')
                self.assertEqual(sum(event.kind == EventKind.COMPLETED for event in events), 1)

    def test_nonzero_exit_missing_result_duplicate_terminal_and_truncated_eof_fail(self):
        for mode in ('nonzero', 'missing_terminal', 'double_terminal', 'truncated', 'invalid_utf8'):
            with self.subTest(mode=mode):
                _, runner, stream = self.configure(mode)
                events = list(stream)
                self.assertEqual(events[-1].kind, EventKind.FAILED)
                self.assertFalse(any(event.kind == EventKind.COMPLETED for event in events))
                self.assertNotIn('opaque-account', repr(events) + runner.diagnostic('r1'))
                self.assertNotIn('private-prompt', runner.diagnostic('r1'))

    def test_timeout_is_distinct_from_protocol_failure(self):
        _, _, stream = self.configure('timeout', ProcessLimits(wall_seconds=.3))
        started = time.monotonic()
        events = list(stream)
        self.assertEqual(events[-1].kind, EventKind.FAILED)
        self.assertIn('超时', events[-1].text)
        self.assertLess(time.monotonic() - started, 3)

    def test_cancel_is_confirmed_by_real_fake_process_cleanup(self):
        _, runner, stream = self.configure('cancel')
        events = []
        ready = threading.Event()

        def consume():
            for event in stream:
                events.append(event)
                if event.kind == EventKind.TEXT:
                    ready.set()

        worker = threading.Thread(target=consume)
        worker.start()
        try:
            self.assertTrue(ready.wait(3))
            runner.cancel('r1')
            runner.cancel('r1')
            worker.join(3)
            self.assertFalse(worker.is_alive())
            self.assertEqual(events[-1].kind, EventKind.CANCELLED)
            self.assertIn('取消', events[-1].text)
        finally:
            runner.cancel('r1')
            worker.join(3)

    def test_profile_mismatch_blocks_and_fake_grant_does_not_enable_adapter(self):
        request, _, stream = self.configure('tool')
        self.assertEqual(list(stream)[-1].kind, EventKind.BLOCKED)
        adapter = CodexCliAdapter(executable=str(self.scope.executable), version='0.159.3')
        self.assertFalse(adapter.probe().available)
        self.assertEqual(list(adapter.stream(request))[-1].kind, EventKind.BLOCKED)
