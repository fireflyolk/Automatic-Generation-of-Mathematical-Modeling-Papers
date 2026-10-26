from dataclasses import replace
from pathlib import Path
import json
import os
import sys
import threading
import time
import unittest
import uuid

from mathmodel_paper_studio.bootstrap.paths import APPROVED_BASE, AppPaths
from mathmodel_paper_studio.domain.models import ScopeManifest, Stage
from mathmodel_paper_studio.agents.preflight import Provider, ProviderStatus, AdapterCapabilities
from mathmodel_paper_studio.agents.protocol import AgentRequest, AgentEvent, EventKind
from mathmodel_paper_studio.agents.process_runner import ProcessRunner, ProcessLimits, native_cli_environment, generated_environment
from mathmodel_paper_studio.agents.windows_job import NativeJobProcess, process_alive
from mathmodel_paper_studio.security.scope_manifest import build_scope
from mathmodel_paper_studio.security.sandbox_gate import ScopeGate, ProbeResult

FIXTURE = Path(__file__).resolve().parents[2] / 'fixtures/cli/trusted_lifecycle.py'


class ProcessRunnerTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(APPROVED_BASE) / 'tmp/mathmodel-paper-studio-20261015/task3' / uuid.uuid4().hex
        self.paths = AppPaths(self.root)
        self.paths.ensure_directories()
        self.project = self.paths.projects / 'demo'
        (self.project / 'out').mkdir(parents=True)
        self.scope = build_scope(self.root, ScopeManifest('s1', 'r1', Provider.CLAUDE, 'projects/demo',
                                (), ('out',), (Stage.PAPER_DRAFTING,), True), Path(sys.executable), 'fake1', 'restricted_read_write')
        caps = AdapterCapabilities(True, True, True, 'provider_transport_only', 'cli_native_only', 'restricted_read_write')
        status = ProviderStatus(Provider.CLAUDE, str(self.scope.executable), 'fake1', True, None, 'fake fixture only', caps, True)
        # Application-owned fake probe evidence exercises control flow only.
        self.gate = ScopeGate(trusted_probe=lambda scope: ProbeResult(status, True, True, True, True))
        self.grant = self.gate.authorize(self.scope)
        self.request = AgentRequest(self.scope, Stage.PAPER_DRAFTING, 'fixture prompt')

    def parser(self, line):
        value = json.loads(line)
        return (AgentEvent('r1', value['kind'], value.get('text', '')),)

    def configure_launch(self, mode):
        arguments = ('-I', '-B', str(FIXTURE), mode, str(self.project))
        self.scope = replace(self.scope, launch_arguments=arguments)
        self.grant = self.gate.authorize(self.scope)
        self.request = AgentRequest(self.scope, Stage.PAPER_DRAFTING, 'fixture prompt')
        return arguments

    def run_fixture(self, mode, limits=None):
        self.configure_launch(mode)
        runner = ProcessRunner(self.gate, self.paths, limits or ProcessLimits())
        events = list(runner.stream(self.request, self.grant, ('-I', '-B', str(FIXTURE), mode, str(self.project)), self.parser))
        return events, runner

    def test_limits_finite_and_validated(self):
        for kwargs in [{'wall_seconds': float('inf')}, {'wall_seconds': 0}, {'queue_bytes': 0},
                       {'line_bytes': -1}, {'max_events': True}, {'process_memory_bytes': 0}]:
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                ProcessLimits(**kwargs)

    def test_unicode_events_success_and_nonzero_exit_overrides_completed(self):
        events, runner = self.run_fixture('success')
        self.assertEqual([event.kind.value for event in events], ['text', 'completed'])
        self.assertEqual(events[0].text, '你好')
        events, _ = self.run_fixture('nonzero')
        self.assertEqual(events[-1].kind.value, 'failed')
        self.assertNotIn('completed', [event.kind.value for event in events])

    def test_timeout_output_overflow_and_malformed_protocol_terminate(self):
        for mode, limits, reason in [('timeout', ProcessLimits(wall_seconds=.3), '超时'),
                                      ('output', ProcessLimits(output_bytes=8192, line_bytes=4096), '上限'),
                                      ('malformed', ProcessLimits(wall_seconds=5), '格式')]:
            with self.subTest(mode=mode):
                started = time.monotonic()
                events, _ = self.run_fixture(mode, limits)
                self.assertEqual(events[-1].kind.value, 'failed')
                self.assertIn(reason, events[-1].text)
                self.assertLess(time.monotonic() - started, 4)

    def test_cross_chunk_secrets_and_external_paths_never_reach_events_or_diagnostics(self):
        events, runner = self.run_fixture('redaction')
        text = repr(events) + runner.diagnostic('r1')
        self.assertNotIn('sk-fixture-secret', text)
        self.assertNotIn('C:\\outside', text)
        self.assertIn('[已隐藏]', text)

    def test_explicit_environment_excludes_tokens_proxies_arbitrary_paths_and_child_auth(self):
        source = {'SystemRoot': 'C:\\Windows', 'HOME': 'Z:\\native-login', 'USERPROFILE': 'Z:\\native-login',
                  'APPDATA': 'Z:\\native-login', 'LOCALAPPDATA': 'Z:\\native-login',
                  'PATH': 'untrusted', 'PYTHONPATH': 'untrusted', 'OPENAI_API_KEY': 'fixture-secret', 'HTTP_PROXY': 'fixture-secret'}
        env = native_cli_environment(self.paths, self.scope.executable, source)
        self.assertEqual(env['HOME'], source['HOME'])
        for name in ('OPENAI_API_KEY', 'HTTP_PROXY', 'PYTHONPATH'):
            self.assertNotIn(name, env)
        self.assertNotIn('untrusted', env['PATH'])
        child = generated_environment(self.paths, self.scope.executable, source)
        for name in ('HOME', 'USERPROFILE', 'APPDATA', 'LOCALAPPDATA', 'OPENAI_API_KEY', 'HTTP_PROXY'):
            self.assertNotIn(name, child)
        for name in ('TEMP', 'TMP', 'TMPDIR', 'PYTHONPYCACHEPREFIX'):
            self.assertTrue(env[name].startswith(str(self.root)))

    def wait_descendants(self):
        deadline = time.monotonic() + 4
        paths = [self.project / name for name in ('child.pid', 'grandchild.pid')]
        while time.monotonic() < deadline:
            if all(path.exists() for path in paths):
                return [int(path.read_text(encoding='ascii')) for path in paths]
            time.sleep(.02)
        self.fail('trusted fake descendants did not start')

    def test_real_parent_grandchild_cancel_and_idempotent_cleanup(self):
        self.configure_launch('tree')
        runner = ProcessRunner(self.gate, self.paths, ProcessLimits(wall_seconds=8))
        events = []
        def consume():
            events.extend(runner.stream(self.request, self.grant, ('-I', '-B', str(FIXTURE), 'tree', str(self.project)), self.parser))
        worker = threading.Thread(target=consume)
        worker.start()
        pids = self.wait_descendants()
        try:
            runner.cancel('r1')
            runner.cancel('r1')
            worker.join(3)
            self.assertFalse(worker.is_alive())
            self.assertEqual(events[-1].kind.value, 'cancelled')
            self.assertTrue(all(not process_alive(pid) for pid in pids))
        finally:
            runner.cancel('r1')
            worker.join(3)

    def test_real_timeout_kills_descendants_even_after_root_exits_holding_pipes(self):
        started = time.monotonic()
        events, _ = self.run_fixture('rootexit', ProcessLimits(wall_seconds=.8))
        pids = self.wait_descendants()
        self.assertEqual(events[-1].kind.value, 'failed')
        self.assertLess(time.monotonic() - started, 4)
        self.assertTrue(all(not process_alive(pid) for pid in pids))

    def test_terminal_event_is_delivered_only_after_entire_job_cleanup(self):
        self.configure_launch('closedpipes')
        runner = ProcessRunner(self.gate, self.paths, ProcessLimits(wall_seconds=5))
        stream = runner.stream(self.request, self.grant, ('-I', '-B', str(FIXTURE), 'closedpipes', str(self.project)), self.parser)
        try:
            event = next(stream)
            pids = self.wait_descendants()
            self.assertEqual(event.kind.value, 'completed')
            self.assertTrue(all(not process_alive(pid) for pid in pids))
        finally:
            stream.close()

    def test_large_stdin_to_nonreading_process_still_obeys_timeout(self):
        self.configure_launch('timeout')
        runner = ProcessRunner(self.gate, self.paths, ProcessLimits(wall_seconds=.3, stdin_bytes=1024 * 1024))
        started = time.monotonic()
        events = list(runner.stream(self.request, self.grant, ('-I', '-B', str(FIXTURE), 'timeout', str(self.project)), self.parser,
                                    stdin=b'x' * (1024 * 1024)))
        self.assertLess(time.monotonic() - started, 3)
        self.assertIn('超时', events[-1].text)

    def test_event_and_queue_limits_fail_closed(self):
        for mode, limits in [('success', ProcessLimits(max_events=1)), ('output', ProcessLimits(queue_bytes=1))]:
            with self.subTest(mode=mode):
                events, _ = self.run_fixture(mode, limits)
                self.assertEqual(events[-1].kind.value, 'failed')

    def test_split_event_secret_and_path_suffixes_are_redacted(self):
        events, runner = self.run_fixture('splitredaction')
        text = repr(events) + runner.diagnostic('r1')
        for piece in ('sensitive-tail', 'private-filename.txt'):
            self.assertNotIn(piece, text)

    def test_stderr_diagnostics_never_retain_account_or_input_content(self):
        events, runner = self.run_fixture('private-diagnostic')
        diagnostic = runner.diagnostic('r1')
        self.assertNotIn('opaque-account-identifier', diagnostic)
        self.assertNotIn('copied-input-cell', diagnostic)
        self.assertIn('[已隐藏]', diagnostic)

    def test_launch_arguments_cannot_change_after_scope_probe(self):
        self.configure_launch('success')
        runner = ProcessRunner(self.gate, self.paths)
        with self.assertRaisesRegex(ValueError, '参数'):
            list(runner.stream(self.request, self.grant, ('--add-dir', str(self.root)), self.parser))

    def redacted_events(self, fragments, *, literals=()):
        from mathmodel_paper_studio.agents.process_runner import _Redactor
        parser = lambda line: (AgentEvent('r1', 'text', line),)
        runner = ProcessRunner(self.gate, self.paths)
        redactor = _Redactor('fixture prompt', literals)
        state = {'terminal': None, 'events': 0}
        events = []
        for fragment in fragments:
            events.extend(runner._parse_events(self.request, fragment, parser, state, redactor))
        events.extend(runner._parse_events(self.request, '', lambda line: (AgentEvent('r1', 'completed'),), state, redactor))
        if state['terminal'] is not None:
            events.append(state['terminal'])
        return events

    def test_split_sensitive_markers_never_reach_concatenated_forwarded_text(self):
        cases = [('s', 'k-live-secret-1234567890'), ('github_pat', '_live-secret-1234567890'),
                 ('Bear', 'er opaque-secret-1234567890'), ('C:', '\\outside\\private.txt')]
        for first, second in cases:
            with self.subTest(marker=first):
                text = ''.join(event.text for event in self.redacted_events((first, second)))
                self.assertNotIn(first + second, text)
                self.assertIn('[已隐藏]', text)

    def test_sensitive_markers_redact_at_every_character_boundary(self):
        for value in ('sk-live-secret-1234567890', 'github_pat_live-secret-1234567890',
                      'Bearer opaque-secret-1234567890', 'C:\\outside\\private.txt'):
            for boundary in range(1, len(value)):
                with self.subTest(value_kind=value[:2], boundary=boundary):
                    text = ''.join(event.text for event in self.redacted_events((value[:boundary], value[boundary:])))
                    self.assertNotIn(value, text)
        text = ''.join(event.text for event in self.redacted_events(tuple('sk-live-secret-1234567890')))
        self.assertNotIn('sk-live-secret-1234567890', text)

    def test_prefix_candidates_preserve_ordinary_text_and_known_literal_redaction(self):
        from mathmodel_paper_studio.agents.process_runner import _Redactor
        bounded = _Redactor('fixture prompt', ())
        for fragment in tuple('github_pat_live-secret-1234567890 ') * 20:
            bounded.clean(fragment)
            self.assertTrue(all(len(value) <= bounded.candidate_bound for value in bounded.candidates.values()))
        self.assertLessEqual(bounded.candidate_bound, 256)
        fragments = ('This is ', 'a safe ', 'analysis', ' for Git', 'Hub and ', 'Bearer', 's C:', ' theory.')
        events = self.redacted_events(fragments)
        self.assertEqual(''.join(event.text for event in events), ''.join(fragments))
        safe_tail = self.redacted_events(('plain safe C:',))
        self.assertEqual(''.join(event.text for event in safe_tail if event.kind.value == 'text'), 'plain safe C:')
        text = ''.join(event.text for event in self.redacted_events(('known-', 'secret ', 'safe ending'), literals=('known-secret',)))
        self.assertNotIn('known-secret', text)
        self.assertIn('safe ending', text)

    def test_known_secret_prefix_at_text_end_is_released_when_it_does_not_match(self):
        from mathmodel_paper_studio.agents.process_runner import _Redactor
        redactor = _Redactor('', ('test-secret',))

        visible = redactor.clean('prompt') + redactor.clean('', final=True)

        self.assertEqual('prompt', visible)


    def test_long_known_literal_prefix_is_bounded_by_literal_size_and_redacted(self):
        from mathmodel_paper_studio.agents.process_runner import _Redactor
        literal = 'A' * 300 + 'Z'
        first = literal[:257]
        rest = literal[257:]

        for redactor in (_Redactor(literal, ()), _Redactor('', (literal,))):
            with self.subTest(prompt=literal in redactor.hidden):
                self.assertEqual('', redactor.clean(first))
                self.assertLessEqual(max(map(len, redactor.candidates.values()), default=0),
                                     redactor.literal_candidate_bound)
                self.assertEqual('[已隐藏]', redactor.clean(rest, final=True))


    def test_long_candidate_release_is_split_to_event_limit(self):
        mismatch_literal = 'A' * 65535 + 'Z'
        mismatch_events = self.redacted_events((mismatch_literal[:-1], '12'), literals=(mismatch_literal,))
        mismatch_text = ''.join(event.text for event in mismatch_events)
        self.assertEqual(mismatch_literal[:-1] + '12', mismatch_text)
        self.assertTrue(all(len(event.text.encode('utf-8')) <= 65536 for event in mismatch_events))

        terminal_literal = '中' * 30000 + 'Z'
        terminal_events = self.redacted_events(('中' * 20000, '中' * 10000), literals=(terminal_literal,))
        terminal_text = ''.join(event.text for event in terminal_events)
        self.assertEqual(terminal_literal[:-1], terminal_text)
        self.assertTrue(all(len(event.text.encode('utf-8')) <= 65536 for event in terminal_events))


    def test_long_text_flush_does_not_duplicate_nontext_status_events(self):
        from mathmodel_paper_studio.agents.process_runner import ProcessRunner, _Redactor
        literal = 'A' * 65535 + 'Z'
        runner = ProcessRunner(self.gate, self.paths)
        redactor = _Redactor('', (literal,))
        state = {'terminal': None, 'events': 0}
        first = runner._parse_events(
            self.request, literal[:-1], lambda _: (AgentEvent('r1', 'text', literal[:-1]),), state, redactor,
        )
        events = list(first)
        released = runner._parse_events(
            self.request, '12',
            lambda _: (AgentEvent('r1', EventKind.TOOL_STARTED, '12', 'item1'),), state, redactor,
        )
        events.extend(released)

        self.assertEqual(1, sum(event.kind is EventKind.TOOL_STARTED for event in events))
        self.assertEqual(literal[:-1] + '12', ''.join(event.text for event in events))
        self.assertTrue(all(len(event.text.encode('utf-8')) <= 65536 for event in events))


    def test_whitespace_separated_token_password_markers_are_redacted_across_events(self):
        cases = [('token ', '=opaque-secret-1234567890 '), ('password\t', ':opaque-secret-1234567890 '),
                 ('api_key \t', '=opaque-secret-1234567890 '), ('apikey\t', ':opaque-secret-1234567890 ')]
        for first, second in cases:
            with self.subTest(label=first):
                text = ''.join(event.text for event in self.redacted_events((first, second)))
                self.assertNotIn('opaque-secret-1234567890', text)
                self.assertIn('[已隐藏]', text)

    def test_assignment_separator_and_value_whitespace_remain_redacted_at_all_splits(self):
        for value in ('token \t = \t opaque-secret-1234567890 ',
                      'password\t : \topaque-secret-1234567890 ',
                      'Bearer \t opaque-secret-1234567890 '):
            for boundary in range(1, len(value)):
                with self.subTest(label=value[:5], boundary=boundary):
                    text = ''.join(event.text for event in self.redacted_events((value[:boundary], value[boundary:])))
                    self.assertNotIn('opaque-secret-1234567890', text)
        text = ''.join(event.text for event in self.redacted_events(tuple('password \t: \topaque-secret-1234567890 ')))
        self.assertNotIn('opaque-secret-1234567890', text)

    def test_partial_whitespace_marker_is_bounded_and_safe_text_flushes_exactly(self):
        from mathmodel_paper_studio.agents.process_runner import _Redactor
        fragments = ('discussion token ', '\t', 'counts and password\t', ' rules remain safe')
        self.assertEqual(''.join(event.text for event in self.redacted_events(fragments)), ''.join(fragments))
        safe_tail = ('a safe token ', '\t')
        self.assertEqual(''.join(event.text for event in self.redacted_events(safe_tail) if event.kind.value == 'text'), ''.join(safe_tail))
        redactor = _Redactor('fixture prompt', ())
        with self.assertRaises(ValueError):
            redactor.clean('token' + ' ' * 65536)
        self.assertTrue(all(len(value) <= 256 for value in redactor.candidates.values()))
