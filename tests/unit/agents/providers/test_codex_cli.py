import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from mathmodel_paper_studio.agents.preflight import Provider, UnavailableReason
from mathmodel_paper_studio.agents.protocol import EventKind
from mathmodel_paper_studio.agents.providers.codex_cli import CodexCliAdapter, CodexEventParser


def wire(kind, **fields):
    return json.dumps({'type': kind, **fields}, ensure_ascii=False)


def started(**kwargs):
    parser = CodexEventParser('r1', **kwargs)
    parser(wire('thread.started', thread_id='thread_1'))
    parser(wire('turn.started'))
    return parser


USAGE = {'input_tokens': 10, 'cached_input_tokens': 0, 'output_tokens': 3,
         'reasoning_output_tokens': 1}


class CodexParserTests(unittest.TestCase):
    def test_exact_version_binding_and_single_turn_start(self):
        with self.assertRaises(ValueError):
            CodexEventParser('r1', version='0.159.4')
        parser = CodexEventParser('r1')
        self.assertEqual(parser(wire('thread.started', thread_id='thread_1')), ())
        events = parser(wire('turn.started'))
        self.assertEqual(events[0].kind, EventKind.STARTED)
        with self.assertRaises(ValueError):
            parser(wire('turn.started'))

    def test_agent_message_snapshots_emit_only_new_text_and_item_is_not_turn_end(self):
        parser = started()
        events = []
        for kind, text in [('item.started', ''), ('item.updated', '你'),
                           ('item.updated', '你好'), ('item.completed', '你好')]:
            events.extend(parser(wire(kind, item={'id': 'item_0', 'type': 'agent_message', 'text': text})))
        self.assertEqual(''.join(event.text for event in events), '你好')
        self.assertFalse(any(event.kind == EventKind.COMPLETED for event in events))
        self.assertEqual(parser(wire('turn.completed', usage=USAGE))[0].kind, EventKind.COMPLETED)

    def test_completed_only_agent_item_and_optional_metadata_are_supported(self):
        parser = started()
        events = parser(wire('item.completed', item={'id': 'item_0', 'type': 'agent_message',
                                                    'text': '结果'}, metadata={'safe': [1, True, None]}))
        self.assertEqual(events[0].text, '结果')
        self.assertEqual(parser(wire('turn.completed', usage={**USAGE, 'cache_write_input_tokens': 0}))[0].text, '')

    def test_duplicate_conflicting_terminal_records_and_post_terminal_events_fail(self):
        for final in ('turn.completed', 'turn.failed', 'error', 'turn.started'):
            with self.subTest(final=final):
                parser = started()
                parser(wire('turn.completed', usage=USAGE))
                with self.assertRaises(ValueError):
                    parser(wire(final, usage=USAGE, error={'message': 'private'}, message='private'))

    def test_failure_text_is_generic_and_item_error_is_nonfatal(self):
        parser = started()
        events = parser(wire('item.completed', item={'id': 'error_1', 'type': 'error', 'message': 'private account detail'}))
        self.assertEqual(events[0].kind, EventKind.TEXT)
        self.assertNotIn('private', events[0].text)
        events = parser(wire('turn.failed', error={'message': 'authentication_failed private'}))
        self.assertEqual(events[0].kind, EventKind.FAILED)
        self.assertEqual(events[0].text, 'CLI 执行失败。')
        parser = CodexEventParser('r1')
        self.assertEqual(parser(wire('error', message='account=private'))[0].text, 'CLI 执行失败。')

    def test_command_file_and_unknown_tool_activity_is_blocked_without_content(self):
        for item in ({'id': 'item_1', 'type': 'command_execution', 'command': 'private command',
                      'aggregated_output': 'private output', 'status': 'completed', 'exit_code': 0},
                     {'id': 'item_1', 'type': 'file_change', 'changes': [{'path': 'private path', 'kind': 'add'}], 'status': 'completed'},
                     {'id': 'item_1', 'type': 'mcp_tool_call', 'arguments': {'secret': 'private'}}):
            with self.subTest(kind=item['type']):
                events = started()(wire('item.completed', item=item))
                self.assertEqual(events[0].kind, EventKind.BLOCKED)
                self.assertNotIn('private', repr(events))

    def test_only_opaque_bounded_thread_identifier_can_be_retained_explicitly(self):
        parser = started()
        self.assertIsNone(parser.resume_thread_id)
        parser = started(preserve_thread_id=True)
        self.assertEqual(parser.resume_thread_id, 'thread_1')
        self.assertNotIn('thread_1', repr(parser))
        for value in ('account@example.com', 'a' * 65, 'F:/private/file', True):
            with self.subTest(value_type=type(value).__name__), self.assertRaises(ValueError):
                CodexEventParser('r1')(wire('thread.started', thread_id=value))

    def test_duplicate_nonfinite_depth_size_count_and_utf8_limits(self):
        invalid = ['{"type":"turn.started","type":"error"}',
                   '{"type":"turn.started","extra":NaN}', '{"type":"turn.started","extra":1e999}',
                   '{"type":"turn.started","extra":' + '[' * 18 + '0' + ']' * 18 + '}',
                   wire('turn.started', extra='x' * 65536), '[]', 'null', '{',
                   wire('turn.started', extra='\x00'), '"\ud800"']
        for raw in invalid:
            with self.subTest(prefix=ascii(raw[:30])), self.assertRaises(ValueError):
                CodexEventParser('r1')(raw)
        parser = started(max_records=3)
        parser(wire('item.completed', item={'id': 'item_0', 'type': 'agent_message', 'text': ''}))
        with self.assertRaises(ValueError):
            parser(wire('turn.completed', usage=USAGE))

    def test_wrong_fields_types_sequence_and_usage_are_rejected(self):
        records = [wire('item.completed', item=[]), wire('item.completed', item={'id': 3, 'type': 'agent_message', 'text': ''}),
                   wire('item.completed', item={'id': 'i1', 'type': 'agent_message', 'text': False}),
                   wire('turn.completed', usage={}), wire('turn.completed', usage={**USAGE, 'input_tokens': True}),
                   wire('turn.completed', usage={**USAGE, 'cached_input_tokens': -1}),
                   wire('turn.failed', error={'message': None}), wire('error', message=[]), wire('unknown')]
        for raw in records:
            with self.subTest(raw=raw[:50]), self.assertRaises(ValueError):
                started()(raw)
        with self.assertRaises(ValueError):
            CodexEventParser('r1')(wire('turn.completed', usage=USAGE))
        parser = started()
        parser(wire('item.completed', item={'id': 'i1', 'type': 'agent_message', 'text': 'result'}))
        with self.assertRaises(ValueError):
            parser(wire('item.completed', item={'id': 'i1', 'type': 'agent_message', 'text': 'result'}))
        parser = started()
        parser(wire('item.started', item={'id': 'i1', 'type': 'agent_message', 'text': 'abc'}))
        with self.assertRaises(ValueError):
            parser(wire('item.updated', item={'id': 'i1', 'type': 'agent_message', 'text': 'changed'}))
        with self.assertRaises(ValueError):
            parser(wire('turn.completed', usage=USAGE))


    def test_invalid_input_poisoning_prevents_recovery_or_later_success(self):
        parser = started(preserve_thread_id=True)
        with self.assertRaises(ValueError):
            parser('{')
        with self.assertRaises(ValueError):
            parser(wire('turn.completed', usage=USAGE))
        self.assertIsNone(parser.resume_thread_id)

    def test_metadata_keys_are_bounded_and_cannot_contain_controls(self):
        for key in ('bad\x00key', 'bad\nkey', 'x' * 129):
            with self.subTest(key=ascii(key)), self.assertRaises(ValueError):
                started()(wire('item.completed', item={'id': 'i1', 'type': 'agent_message', 'text': ''}, **{key: 1}))

    def test_total_bytes_items_nodes_and_multibyte_line_bounds(self):
        parser = started()
        large = wire('item.completed', item={'id': 'i1', 'type': 'agent_message', 'text': '汉' * 30000})
        with self.assertRaises(ValueError):
            parser(large)
        with self.assertRaises(ValueError):
            started()(wire('turn.completed', usage=USAGE, metadata=[0] * 4096))
        parser = started()
        for number in range(1024):
            parser(wire('item.completed', item={'id': 'i' + str(number), 'type': 'agent_message', 'text': ''}))
        with self.assertRaises(ValueError):
            parser(wire('item.completed', item={'id': 'overflow', 'type': 'agent_message', 'text': ''}))
        parser = started()
        parser(wire('item.started', item={'id': 'i1', 'type': 'agent_message', 'text': ''}))
        record = wire('item.updated', item={'id': 'i1', 'type': 'agent_message', 'text': ''}, padding='x' * 63000)
        with self.assertRaises(ValueError):
            for _ in range(140):
                parser(record)

    def test_reasoning_is_not_forwarded_and_constructor_arguments_are_strict(self):
        parser = started()
        self.assertEqual(parser(wire('item.completed', item={'id': 'i1', 'type': 'reasoning', 'text': 'private reasoning'})), ())
        for kwargs in ({'max_records': True}, {'max_records': 0}, {'max_records': 4097}, {'preserve_thread_id': 1}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                CodexEventParser('r1', **kwargs)
        with self.assertRaises(ValueError):
            CodexCliAdapter(executable='private\npath')

class CodexDisabledAdapterTests(unittest.TestCase):
    def test_missing_unsupported_and_unverified_health_never_claim_ready_or_auth(self):
        cases = [(None, None, UnavailableReason.CLI_MISSING),
                 ('codex.exe', '0.159.4', UnavailableReason.VERSION_UNSUPPORTED),
                 ('codex.exe', '0.159.3', UnavailableReason.AUTH_UNVERIFIED)]
        for executable, version, reason in cases:
            with self.subTest(reason=reason):
                adapter = CodexCliAdapter(executable=executable, version=version)
                with patch('subprocess.Popen', side_effect=AssertionError('no CLI')):
                    status = adapter.probe()
                self.assertFalse(status.available)
                self.assertEqual(status.reason, reason)
                self.assertFalse(status.evidence_verified)
                self.assertFalse(adapter.capabilities().streaming)
                self.assertFalse(adapter.capabilities().command_execution)
                self.assertNotIn('通过', status.message)

    def test_production_stream_cannot_be_enabled_by_fake_hints_or_scope(self):
        request = SimpleNamespace(run_id='r1', scope=SimpleNamespace(metadata=SimpleNamespace(provider=Provider.CODEX)))
        adapter = CodexCliAdapter(executable='codex.exe', version='0.159.3')
        with patch('mathmodel_paper_studio.agents.process_runner.NativeJobProcess', side_effect=AssertionError('no CLI')):
            events = list(adapter.stream(request))
            adapter.cancel('r1')
        self.assertEqual(events[-1].kind, EventKind.FAILED)
        self.assertIn('请求', events[-1].text)
        request.scope.metadata.provider = Provider.CLAUDE
        self.assertEqual(list(adapter.stream(request))[-1].kind, EventKind.FAILED)


