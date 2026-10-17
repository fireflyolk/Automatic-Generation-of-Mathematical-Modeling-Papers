"""Task3 native Job lifecycle contracts using a fixed F: Python fixture only."""
from dataclasses import replace
from pathlib import Path
import sys
import threading
import time
import unittest
import uuid
from unittest.mock import patch

from mathmodel_paper_studio.agents.preflight import AdapterCapabilities, Provider, ProviderStatus
from mathmodel_paper_studio.agents.process_runner import ProcessLimits, ProcessRunner
from mathmodel_paper_studio.agents.protocol import AgentRequest, EventKind
from mathmodel_paper_studio.agents.providers.claude_code_cli import (
    ClaudeCodeCliAdapter, ClaudeEventParser,
)
from mathmodel_paper_studio.bootstrap.paths import APPROVED_BASE, AppPaths
from mathmodel_paper_studio.domain.models import ScopeManifest, Stage
from mathmodel_paper_studio.security.sandbox_gate import ProbeResult, ScopeGate
from mathmodel_paper_studio.security.scope_manifest import build_scope

FIXTURE = Path(__file__).resolve().parents[2] / 'fixtures/cli/task5_claude_jsonl.py'


class ClaudeFakeProcessTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(APPROVED_BASE) / 'tmp/mathmodel-paper-studio-20261015/task5' / uuid.uuid4().hex
        self.paths = AppPaths(self.root)
        self.paths.ensure_directories()
        project = self.paths.projects / 'demo'
        (project / 'out').mkdir(parents=True)
        base = build_scope(self.root, ScopeManifest('s1', 'r1', Provider.CLAUDE, 'projects/demo',
                          (), ('out',), (Stage.PAPER_DRAFTING,), True), Path(sys.executable),
                          '2.1.291', 'restricted_read_write')
        self.caps = AdapterCapabilities(True, True, True, 'provider_transport_only',
                                        'cli_native_only', 'restricted_read_write')
        self.status = ProviderStatus(Provider.CLAUDE, str(base.executable), '2.1.291', True,
                                     None, 'synthetic test grant only', self.caps, True)
        self.gate = ScopeGate(trusted_probe=lambda scope: ProbeResult(self.status, True, True, True, True))
        self.base_scope = base

    def configure(self, mode, limits=None):
        args = ('-I', '-B', str(FIXTURE), mode)
        scope = replace(self.base_scope, launch_arguments=args)
        request = AgentRequest(scope, Stage.PAPER_DRAFTING, 'private fixture prompt')
        grant = self.gate.authorize(scope)
        runner = ProcessRunner(self.gate, self.paths, limits or ProcessLimits(wall_seconds=5))
        stream = runner.stream(request, grant, args, ClaudeEventParser('r1'))
        return request, grant, runner, stream

    def test_jsonl_success_final_record_without_newline_and_partial_utf8(self):
        for mode in ('success', 'final_no_newline'):
            with self.subTest(mode=mode):
                _, _, _, stream = self.configure(mode)
                events = list(stream)
                self.assertEqual(events[-1].kind, EventKind.COMPLETED)
                self.assertEqual(''.join(event.text for event in events if event.kind is EventKind.TEXT), '中文结果')
                self.assertEqual(sum(event.kind is EventKind.COMPLETED for event in events), 1)

    def test_tool_events_are_normalized_and_payloads_never_reach_events_or_diagnostics(self):
        _, _, runner, stream = self.configure('tool')
        events = list(stream)
        self.assertEqual([event.kind for event in events], [EventKind.STARTED, EventKind.TOOL_STARTED,
                                                            EventKind.TOOL_FINISHED, EventKind.TEXT,
                                                            EventKind.COMPLETED])
        self.assertNotIn('private-path', repr(events) + runner.diagnostic('r1'))
        self.assertNotIn('private-tool-result', repr(events) + runner.diagnostic('r1'))

    def test_unsupported_tool_blocks_without_echoing_tool_payload(self):
        _, _, _, stream = self.configure('forbidden_tool')
        events = list(stream)
        self.assertEqual(events[-1].kind, EventKind.BLOCKED)
        self.assertNotIn('Bash', repr(events))
        self.assertNotIn('private-command', repr(events))

    def test_nonzero_missing_duplicate_truncated_and_invalid_utf8_never_complete(self):
        for mode in ('nonzero', 'missing_terminal', 'double_terminal', 'truncated', 'invalid_utf8'):
            with self.subTest(mode=mode):
                _, _, runner, stream = self.configure(mode)
                events = list(stream)
                self.assertEqual(events[-1].kind, EventKind.FAILED)
                self.assertFalse(any(event.kind is EventKind.COMPLETED for event in events))
                self.assertNotIn('private fixture prompt', repr(events) + runner.diagnostic('r1'))

    def test_typed_auth_billing_and_rate_errors_survive_runner_cleanup(self):
        cases = [('auth', '登录状态无效'), ('billing', '账单'), ('rate_limit', '限流')]
        for mode, expected in cases:
            with self.subTest(mode=mode):
                _, _, _, stream = self.configure(mode)
                event = list(stream)[-1]
                self.assertEqual(event.kind, EventKind.FAILED)
                self.assertIn(expected, event.text)

    def test_timeout_is_distinct_and_stops_the_fake_process(self):
        _, _, _, stream = self.configure('timeout', ProcessLimits(wall_seconds=.3))
        started = time.monotonic()
        events = list(stream)
        self.assertEqual(events[-1].kind, EventKind.FAILED)
        self.assertIn('超时', events[-1].text)
        self.assertLess(time.monotonic() - started, 3)

    def test_cancel_terminates_fake_process_and_remains_distinct(self):
        _, _, runner, stream = self.configure('cancel')
        events = []
        ready = threading.Event()

        def consume():
            for event in stream:
                events.append(event)
                if event.kind is EventKind.TEXT:
                    ready.set()

        worker = threading.Thread(target=consume)
        worker.start()
        try:
            self.assertTrue(ready.wait(3))
            runner.cancel('r1')
            worker.join(3)
            self.assertFalse(worker.is_alive())
            self.assertEqual(events[-1].kind, EventKind.CANCELLED)
            self.assertIn('取消', events[-1].text)
        finally:
            runner.cancel('r1')
            worker.join(3)

    def test_fake_grant_cannot_enable_production_adapter(self):
        request, grant, _, _ = self.configure('success')
        self.assertIsNotNone(grant)
        adapter = ClaudeCodeCliAdapter(executable=str(request.scope.executable), version='2.1.291')
        with patch('mathmodel_paper_studio.agents.windows_job.NativeJobProcess',
                   side_effect=AssertionError('production adapter must not launch')):
            events = list(adapter.stream(request))
            adapter.cancel('r1')
        self.assertEqual(events[-1].kind, EventKind.BLOCKED)
        self.assertFalse(adapter.probe().available)
