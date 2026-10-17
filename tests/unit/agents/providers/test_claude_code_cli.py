"""Claude stream-json contract tests; production launch stays disabled."""
import json
import unittest
from unittest.mock import patch

from mathmodel_paper_studio.agents.preflight import UnavailableReason
from mathmodel_paper_studio.agents.protocol import EventKind


def _api():
    from mathmodel_paper_studio.agents.providers.claude_code_cli import (
        ClaudeCodeCliAdapter, ClaudeEventParser, MAX_TURNS, PROTOCOL_VERSION,
        build_claude_arguments,
    )
    return ClaudeCodeCliAdapter, ClaudeEventParser, MAX_TURNS, PROTOCOL_VERSION, build_claude_arguments


def wire(kind, **fields):
    return json.dumps({'type': kind, **fields}, ensure_ascii=False)


def init(parser, *, tools=('Read', 'Write'), session_id='session_1'):
    fields = {'subtype': 'init', 'session_id': session_id}
    if tools is not None:
        fields['tools'] = list(tools)
    return parser(wire('system', **fields))


def result(parser, *, is_error=False, subtype='success', **fields):
    return parser(wire('result', subtype=subtype, is_error=is_error, num_turns=2,
                       session_id='session_1', **fields))


def stream_record(parser, event_type, **fields):
    return parser(wire('stream_event', session_id='session_1',
                       event={'type': event_type, **fields}))


def begin_stream_message(parser, message_id='msg1'):
    return stream_record(parser, 'message_start',
                         message={'id': message_id, 'type': 'message', 'role': 'assistant', 'content': []})


def end_stream_message(parser):
    stream_record(parser, 'message_delta', delta={'stop_reason': 'end_turn', 'stop_sequence': None})
    return stream_record(parser, 'message_stop')


class ClaudeProfileTests(unittest.TestCase):
    def test_exact_restricted_headless_argv_has_bounded_turn_count(self):
        _, _, maximum, _, build = _api()
        self.assertEqual(maximum, 8)
        self.assertEqual(build(), ('-p', '--output-format', 'stream-json', '--verbose',
                                   '--include-partial-messages', '--max-turns', '8', '--restricted',
                                   '--tools', 'Read,Write', '--disallowedTools', 'mcp__*',
                                   '--no-session-persistence', '--permission-prompts', 'none'))
        for value in (True, 0, -1, 9, '8'):
            with self.subTest(value=value), self.assertRaises(ValueError):
                build(value)
        self.assertFalse(any('--add-dir' in item or 'dangerously-skip-permissions' in item
                             or item == '--permission-mode' for item in build()))

    def test_version_is_pinned_and_session_id_is_opt_in_bounded_and_opaque(self):
        _, parser_type, _, version, _ = _api()
        self.assertEqual(version, '2.1.291')
        with self.assertRaises(ValueError):
            parser_type('run1', version='2.1.292')
        parser = parser_type('run1')
        self.assertEqual(init(parser)[0].kind, EventKind.STARTED)
        self.assertIsNone(parser.session_id)
        parser = parser_type('run1', preserve_session_id=True)
        init(parser)
        self.assertEqual(parser.session_id, 'session_1')
        self.assertNotIn('session_1', repr(parser))
        for unsafe in ('account@example.com', 'F:/private/path', 'x' * 65, True):
            with self.subTest(value=repr(unsafe)), self.assertRaises(ValueError):
                init(parser_type('run1'), session_id=unsafe)

    def test_disabled_probe_reports_only_unverified_auth_and_false_capabilities(self):
        adapter_type, _, _, _, _ = _api()
        cases = [(None, None, UnavailableReason.CLI_MISSING),
                 ('claude.exe', '2.1.292', UnavailableReason.VERSION_UNSUPPORTED),
                 ('claude.exe', '2.1.291', UnavailableReason.AUTH_UNVERIFIED)]
        for executable, version, expected in cases:
            with self.subTest(expected=expected), patch('subprocess.Popen', side_effect=AssertionError('must not launch')):
                adapter = adapter_type(executable=executable, version=version)
                status = adapter.probe()
                self.assertFalse(status.available)
                self.assertEqual(status.reason, expected)
                self.assertFalse(status.evidence_verified)
                self.assertFalse(adapter.capabilities().streaming)
                self.assertFalse(adapter.capabilities().filesystem_confinement)
                self.assertFalse(adapter.capabilities().command_execution)


class ClaudeEventParserTests(unittest.TestCase):

    def test_assistant_error_waits_for_and_matches_one_final_result(self):
        _, parser_type, _, _, _ = _api()
        for code, expected in (('authentication_failed', '登录状态无效'),
                               ('billing_error', '账单'), ('rate_limit', '限流')):
            with self.subTest(code=code):
                parser = parser_type('run1')
                init(parser)
                self.assertEqual(parser(wire('assistant', error=code)), ())
                terminal = result(parser, is_error=True, errors=[code])[0]
                self.assertEqual(terminal.kind, EventKind.FAILED)
                self.assertIn(expected, terminal.text)

        parser = parser_type('run1')
        init(parser)
        parser(wire('assistant', error='authentication_failed'))
        with self.assertRaises(ValueError):
            result(parser)

        parser = parser_type('run1')
        init(parser)
        parser(wire('assistant', error='authentication_failed'))
        with self.assertRaises(ValueError):
            parser(wire('assistant', error='authentication_failed'))

        parser = parser_type('run1')
        init(parser)
        parser(wire('assistant', error='authentication_failed'))
        with self.assertRaises(ValueError):
            result(parser, is_error=True, errors=['server_error'])

    def test_text_deduplication_is_per_message_and_block_with_result_fallback(self):
        _, parser_type, _, _, _ = _api()
        parser = parser_type('run1')
        init(parser)
        begin_stream_message(parser, 'msg_streamed')
        stream_record(parser, 'content_block_start', index=0, content_block={'type': 'text', 'text': ''})
        streamed = stream_record(parser, 'content_block_delta', index=0,
                                 delta={'type': 'text_delta', 'text': '先行流文本'})
        stream_record(parser, 'content_block_stop', index=0)
        end_stream_message(parser)
        duplicate_snapshot = parser(wire('assistant', message={'id': 'msg_streamed', 'content': [
            {'type': 'text', 'text': '先行流文本'}]}))
        later_assistant_only = parser(wire('assistant', message={'id': 'msg_assistant_only', 'content': [
            {'type': 'text', 'text': '后续独立消息'}]}))
        repeated_snapshot = parser(wire('assistant', message={'id': 'msg_assistant_only', 'content': [
            {'type': 'text', 'text': '后续独立消息'}]}))
        terminal = result(parser, result='后续独立消息')
        self.assertEqual(streamed[0].text, '先行流文本')
        self.assertEqual(duplicate_snapshot, ())
        self.assertEqual([event.text for event in later_assistant_only], ['后续独立消息'])
        self.assertEqual(repeated_snapshot, ())
        self.assertEqual([event.kind for event in terminal], [EventKind.COMPLETED])

        fallback = parser_type('run1')
        init(fallback)
        self.assertEqual([event.text for event in result(fallback, result='仅最终结果回退')], ['仅最终结果回退', ''])

    def test_result_reconciliation_preserves_distinct_final_answer_after_streamed_message(self):
        _, parser_type, _, _, _ = _api()
        parser = parser_type('run1')
        init(parser)
        begin_stream_message(parser, 'msg_streamed_A')
        stream_record(parser, 'content_block_start', index=0,
                      content_block={'type': 'text', 'text': ''})
        streamed = stream_record(parser, 'content_block_delta', index=0,
                                 delta={'type': 'text_delta', 'text': '流式答案A '})
        stream_record(parser, 'content_block_stop', index=0)
        end_stream_message(parser)

        final = result(parser, result='最终答案B')
        emitted = [event.text for event in streamed + final if event.kind is EventKind.TEXT]
        self.assertEqual(emitted, ['流式答案A ', '最终答案B'])
        self.assertEqual([event.kind for event in final], [EventKind.TEXT, EventKind.COMPLETED])

    def test_result_reconciliation_emits_only_suffix_after_streamed_prefix(self):
        _, parser_type, _, _, _ = _api()
        parser = parser_type('run1')
        init(parser)
        begin_stream_message(parser, 'msg_prefix')
        stream_record(parser, 'content_block_start', index=0,
                      content_block={'type': 'text', 'text': ''})
        streamed = stream_record(parser, 'content_block_delta', index=0,
                                 delta={'type': 'text_delta', 'text': '答案前缀'})
        stream_record(parser, 'content_block_stop', index=0)
        end_stream_message(parser)

        final = result(parser, result='答案前缀以及新增后缀')
        emitted = [event.text for event in streamed + final if event.kind is EventKind.TEXT]
        self.assertEqual(emitted, ['答案前缀', '以及新增后缀'])
        self.assertEqual(''.join(emitted), '答案前缀以及新增后缀')
        self.assertEqual([event.kind for event in final], [EventKind.TEXT, EventKind.COMPLETED])

    def test_result_reconciliation_suppresses_prefix_inside_single_emitted_delta(self):
        _, parser_type, _, _, _ = _api()
        parser = parser_type('run1')
        init(parser)
        begin_stream_message(parser, 'msg_single_delta_prefix')
        stream_record(parser, 'content_block_start', index=0,
                      content_block={'type': 'text', 'text': ''})
        streamed = stream_record(parser, 'content_block_delta', index=0,
                                 delta={'type': 'text_delta', 'text': 'ABCDEF'})
        stream_record(parser, 'content_block_stop', index=0)
        end_stream_message(parser)

        final = result(parser, result='ABC')
        emitted = [event.text for event in streamed + final if event.kind is EventKind.TEXT]
        self.assertEqual(emitted, ['ABCDEF'])
        self.assertEqual([event.kind for event in final], [EventKind.COMPLETED])
    def test_result_reconciliation_preserves_distinct_final_substring_of_prior_message(self):
        _, parser_type, _, _, _ = _api()
        parser = parser_type('run1')
        init(parser)
        begin_stream_message(parser, 'msg_substring')
        stream_record(parser, 'content_block_start', index=0,
                      content_block={'type': 'text', 'text': ''})
        streamed = stream_record(parser, 'content_block_delta', index=0,
                                 delta={'type': 'text_delta', 'text': '方案一：42'})
        stream_record(parser, 'content_block_stop', index=0)
        end_stream_message(parser)

        final = result(parser, result='42')
        emitted = [event.text for event in streamed + final if event.kind is EventKind.TEXT]
        self.assertEqual(emitted, ['方案一：42', '42'])

    def test_result_reconciliation_recognizes_emitted_prefix_after_tail_truncation(self):
        _, parser_type, _, _, _ = _api()
        parser = parser_type('run1')
        init(parser)
        begin_stream_message(parser, 'msg_truncated_tail')
        stream_record(parser, 'content_block_start', index=0,
                      content_block={'type': 'text', 'text': ''})
        first = stream_record(parser, 'content_block_delta', index=0,
                              delta={'type': 'text_delta', 'text': 'A' * 60000})
        second = stream_record(parser, 'content_block_delta', index=0,
                               delta={'type': 'text_delta', 'text': 'B' * 10000})
        stream_record(parser, 'content_block_stop', index=0)
        end_stream_message(parser)

        final = result(parser, result='A' * 60000)
        final_text = [event for event in final if event.kind is EventKind.TEXT]
        self.assertEqual((len(first[0].text), len(second[0].text)), (60000, 10000))
        self.assertEqual(len(final_text), 0)
        self.assertEqual([event.kind for event in final], [EventKind.COMPLETED])

    def test_result_reconciliation_keeps_distinct_answer_with_incidental_overlap(self):
        _, parser_type, _, _, _ = _api()
        parser = parser_type('run1')
        init(parser)
        begin_stream_message(parser, 'msg_incidental_overlap')
        stream_record(parser, 'content_block_start', index=0,
                      content_block={'type': 'text', 'text': ''})
        streamed = stream_record(parser, 'content_block_delta', index=0,
                                 delta={'type': 'text_delta', 'text': '流式答案A!'})
        stream_record(parser, 'content_block_stop', index=0)
        end_stream_message(parser)

        final = result(parser, result='!独立答案B')
        emitted = [event.text for event in streamed + final if event.kind is EventKind.TEXT]
        self.assertEqual(emitted, ['流式答案A!', '!独立答案B'])

    def test_tool_use_id_rejects_reuse_across_blocks_and_messages(self):
        _, parser_type, _, _, _ = _api()
        for second_name in ('Read', 'Write'):
            with self.subTest(origin='block', tool_name=second_name):
                parser = parser_type('run1')
                init(parser)
                begin_stream_message(parser, 'msg_two_blocks')
                stream_record(parser, 'content_block_start', index=0,
                              content_block={'type': 'tool_use', 'id': 'toolu_shared',
                                             'name': 'Read', 'input': {}})
                stream_record(parser, 'content_block_stop', index=0)
                with self.assertRaises(ValueError):
                    stream_record(parser, 'content_block_start', index=1,
                                  content_block={'type': 'tool_use', 'id': 'toolu_shared',
                                                 'name': second_name, 'input': {}})

            with self.subTest(origin='message', tool_name=second_name):
                parser = parser_type('run1')
                init(parser)
                begin_stream_message(parser, 'msg_first')
                stream_record(parser, 'content_block_start', index=0,
                              content_block={'type': 'tool_use', 'id': 'toolu_shared',
                                             'name': 'Read', 'input': {}})
                stream_record(parser, 'content_block_stop', index=0)
                end_stream_message(parser)
                begin_stream_message(parser, 'msg_second')
                with self.assertRaises(ValueError):
                    stream_record(parser, 'content_block_start', index=0,
                                  content_block={'type': 'tool_use', 'id': 'toolu_shared',
                                                 'name': second_name, 'input': {}})

    def test_tool_use_id_deduplicates_same_origin_stream_snapshot_once(self):
        _, parser_type, _, _, _ = _api()
        parser = parser_type('run1')
        init(parser)
        begin_stream_message(parser, 'msg_tool_snapshot')
        streamed = stream_record(parser, 'content_block_start', index=0,
                                 content_block={'type': 'tool_use', 'id': 'toolu_same_origin',
                                                'name': 'Read', 'input': {}})
        stream_record(parser, 'content_block_stop', index=0)
        end_stream_message(parser)
        snapshot = parser(wire('assistant', message={'id': 'msg_tool_snapshot', 'content': [
            {'type': 'tool_use', 'id': 'toolu_same_origin', 'name': 'Read', 'input': {}}]}))
        self.assertEqual([event.kind for event in streamed], [EventKind.TOOL_STARTED])
        self.assertEqual(snapshot, ())

    def test_stream_lifecycle_rejects_unopened_mismatched_duplicate_and_dangling_transitions(self):
        _, parser_type, _, _, _ = _api()
        start_message = wire('stream_event', event={'type': 'message_start', 'message': {'id': 'msg1'}})
        text_start = wire('stream_event', event={'type': 'content_block_start', 'index': 0,
                            'content_block': {'type': 'text', 'text': ''}})
        text_delta = wire('stream_event', event={'type': 'content_block_delta', 'index': 0,
                            'delta': {'type': 'text_delta', 'text': 'x'}})
        tool_delta = wire('stream_event', event={'type': 'content_block_delta', 'index': 0,
                            'delta': {'type': 'input_json_delta', 'partial_json': '{}'}})
        message_delta = wire('stream_event', event={'type': 'message_delta', 'delta': {'stop_reason': 'end_turn'}})
        message_stop = wire('stream_event', event={'type': 'message_stop'})
        tool_start = wire('stream_event', event={'type': 'content_block_start', 'index': 0,
                            'content_block': {'type': 'tool_use', 'id': 'toolu_1', 'name': 'Read', 'input': {}}})
        cases = {
            'block before message start': [text_start],
            'delta before block start': [start_message, text_delta],
            'duplicate block start': [start_message, text_start, text_start],
            'delta type mismatches text block': [start_message, text_start, tool_delta],
            'delta type mismatches tool block': [start_message, tool_start, text_delta],
            'stop for unopened index': [start_message, wire('stream_event', event={'type': 'content_block_stop', 'index': 7})],
            'message delta while block open': [start_message, text_start, message_delta],
            'message stop before message delta': [start_message, message_stop],
            'duplicate message delta': [start_message, message_delta, message_delta],
            'block after message delta': [start_message, message_delta, text_start],
            'successful result with message open': [start_message, wire('result', subtype='success', is_error=False,
                num_turns=1, session_id='session_1')],
            'successful result with block open': [start_message, text_start, wire('result', subtype='success',
                is_error=False, num_turns=1, session_id='session_1')],
            'duplicate message stop': [start_message, message_delta, message_stop, message_stop],
        }
        for name, records in cases.items():
            with self.subTest(sequence=name):
                parser = parser_type('run1')
                init(parser)
                with self.assertRaises(ValueError):
                    for record in records:
                        parser(record)

    def test_partial_text_streams_incrementally_and_message_stop_is_not_final(self):
        _, parser_type, _, _, _ = _api()
        parser = parser_type('run1')
        self.assertEqual(init(parser)[0].kind, EventKind.STARTED)
        begin_stream_message(parser, 'msg1')
        stream_record(parser, 'content_block_start', index=0, content_block={'type': 'text', 'text': ''})
        first = stream_record(parser, 'content_block_delta', index=0,
                              delta={'type': 'text_delta', 'text': '中文'})
        stream_record(parser, 'content_block_stop', index=0)
        stopped = end_stream_message(parser)
        duplicate_snapshot = parser(wire('assistant', message={'id': 'msg1', 'content': [
            {'type': 'text', 'text': '中文'}]}))
        self.assertEqual(first[0].text, '中文')
        self.assertEqual(stopped, ())
        self.assertEqual(duplicate_snapshot, ())
        self.assertEqual(result(parser)[0].kind, EventKind.COMPLETED)

    def test_final_assistant_text_is_used_when_partial_stream_is_absent(self):
        _, parser_type, _, _, _ = _api()
        parser = parser_type('run1')
        init(parser)
        events = parser(wire('assistant', session_id='session_1', message={'id': 'msg_final', 'content': [
            {'type': 'text', 'text': '最终答案'}]}))
        self.assertEqual([(event.kind, event.text) for event in events], [(EventKind.TEXT, '最终答案')])
        final = result(parser, result='最终答案')
        self.assertEqual([event.kind for event in final], [EventKind.COMPLETED])

    def test_result_text_is_final_fallback_only_when_no_other_text_source_exists(self):
        _, parser_type, _, _, _ = _api()
        parser = parser_type('run1')
        init(parser)
        events = result(parser, result='only-result answer')
        self.assertEqual([(event.kind, event.text) for event in events], [
            (EventKind.TEXT, 'only-result answer'), (EventKind.COMPLETED, '')])

    def test_only_read_write_tools_are_normalized_without_inputs_or_results(self):
        _, parser_type, _, _, _ = _api()
        parser = parser_type('run1')
        init(parser)
        begin_stream_message(parser, 'msg_tool')
        start = stream_record(parser, 'content_block_start', index=0,
            content_block={'type': 'tool_use', 'id': 'toolu_1', 'name': 'Read', 'input': {'path': 'secret'}})
        stream_record(parser, 'content_block_delta', index=0,
            delta={'type': 'input_json_delta', 'partial_json': '{"path":"secret"}'})
        stream_record(parser, 'content_block_stop', index=0)
        end_stream_message(parser)
        finished = parser(wire('user', message={'content': [{'type': 'tool_result',
            'tool_use_id': 'toolu_1', 'content': 'secret tool output', 'is_error': False}]}))
        self.assertEqual([item.kind for item in start + finished], [EventKind.TOOL_STARTED, EventKind.TOOL_FINISHED])
        self.assertNotIn('secret', repr(start + finished))
        self.assertNotIn('secret', repr(parser))

    def test_forbidden_command_mcp_or_unknown_tool_fails_closed_without_payload(self):
        _, parser_type, _, _, _ = _api()
        for tool_name in ('Bash', 'mcp__private__tool', 'UnknownTool'):
            with self.subTest(tool=tool_name):
                parser = parser_type('run1')
                init(parser)
                begin_stream_message(parser, 'msg_forbidden')
                events = stream_record(parser, 'content_block_start', index=0,
                    content_block={'type': 'tool_use', 'id': 'toolu_2', 'name': tool_name,
                                   'input': {'command': 'private payload'}})
                self.assertEqual(events[0].kind, EventKind.BLOCKED)
                self.assertNotIn(tool_name, events[0].text)
                self.assertNotIn('private', repr(events))

    def test_init_advertised_tool_mismatch_blocks_and_api_retry_is_not_auth_proof(self):
        _, parser_type, _, _, _ = _api()
        parser = parser_type('run1')
        event = parser(wire('system', subtype='api_retry', attempt=1, error='private'))
        self.assertEqual(event, ())
        parser = parser_type('run1')
        events = init(parser, tools=('Read', 'Write', 'Bash'))
        self.assertEqual(events[0].kind, EventKind.BLOCKED)
        self.assertNotIn('Bash', events[0].text)

    def test_typed_auth_billing_rate_permission_and_interruption_errors_are_distinct(self):
        _, parser_type, _, _, _ = _api()
        cases = [
            (lambda p: (p(wire('assistant', error='authentication_failed')),
                        result(p, is_error=True, errors=['authentication_failed']))[1], '登录状态无效'),
            (lambda p: (p(wire('assistant', error='billing_error')),
                        result(p, is_error=True, errors=['billing_error']))[1], '账单'),
            (lambda p: (p(wire('rate_limit_event', rate_limit_info={'status': 'rejected'})),
                        result(p, is_error=True, errors=['rate_limit']))[1], '限流'),
            (lambda p: result(p, is_error=True, permission_denials=[{'tool_name': 'Write'}]), '权限被拒绝'),
            (lambda p: result(p, is_error=True, terminal_reason='aborted_tools'), '中断'),
        ]
        for emit_error, expected in cases:
            with self.subTest(expected=expected):
                parser = parser_type('run1')
                init(parser)
                event = emit_error(parser)[0]
                self.assertEqual(event.kind, EventKind.FAILED)
                self.assertIn(expected, event.text)
                self.assertNotIn('Write', event.text)

    def test_only_explicit_error_types_are_classified_and_success_conflicts_fail(self):
        _, parser_type, _, _, _ = _api()
        parser = parser_type('run1')
        init(parser)
        event = result(parser, is_error=True, subtype='success', errors=['private account text'])[0]
        self.assertEqual(event.kind, EventKind.FAILED)
        self.assertEqual(event.text, 'CLI 执行失败。')
        parser = parser_type('run1')
        init(parser)
        with self.assertRaises(ValueError):
            result(parser, is_error=False, permission_denials=[{'tool_name': 'Write'}])

    def test_rate_limit_warning_is_not_a_terminal_failure(self):
        _, parser_type, _, _, _ = _api()
        parser = parser_type('run1')
        init(parser)
        self.assertEqual(parser(wire('rate_limit_event', rate_limit_info={'status': 'allowed_warning'})), ())
        self.assertEqual(result(parser)[0].kind, EventKind.COMPLETED)

    def test_rejected_rate_limit_waits_for_typed_final_result(self):
        _, parser_type, _, _, _ = _api()
        parser = parser_type('run1')
        init(parser)
        self.assertEqual(parser(wire('rate_limit_event', rate_limit_info={'status': 'rejected'})), ())
        final = result(parser, is_error=True, errors=['rate_limit'])[0]
        self.assertEqual(final.kind, EventKind.FAILED)
        self.assertIn('限流', final.text)

    def test_assistant_record_without_message_or_typed_error_is_rejected(self):
        _, parser_type, _, _, _ = _api()
        parser = parser_type('run1')
        init(parser)
        with self.assertRaises(ValueError):
            parser(wire('assistant'))

    def test_untyped_permission_denial_payload_is_rejected(self):
        _, parser_type, _, _, _ = _api()
        parser = parser_type('run1')
        init(parser)
        with self.assertRaises(ValueError):
            result(parser, is_error=True, permission_denials=['opaque untyped data'])

    def test_result_is_the_only_completion_and_duplicate_or_post_terminal_records_fail(self):
        _, parser_type, _, _, _ = _api()
        parser = parser_type('run1')
        init(parser)
        begin_stream_message(parser, 'msg_empty')
        self.assertEqual(end_stream_message(parser), ())
        self.assertEqual(result(parser)[0].kind, EventKind.COMPLETED)
        with self.assertRaises(ValueError):
            result(parser)

    def test_malformed_duplicates_nonfinite_wrong_fields_and_bounds_poison_parser(self):
        _, parser_type, _, _, _ = _api()
        invalid = ['{"type":"system","type":"result"}', '{', '[]', 'null',
                   '{"type":"system","subtype":"api_retry","n":NaN}',
                   wire('system', subtype='api_retry', payload='汉' * 40000)]
        for raw in invalid:
            with self.subTest(prefix=ascii(raw[:32])):
                parser = parser_type('run1')
                with self.assertRaises(ValueError):
                    parser(raw)
                with self.assertRaises(ValueError):
                    init(parser)

    def test_invalid_result_types_sequence_and_nested_depth_are_rejected(self):
        _, parser_type, _, _, _ = _api()
        cases = [wire('result', subtype='success', is_error=0, num_turns=1),
                 wire('result', subtype='success', is_error=False, num_turns=True),
                 wire('rate_limit_event', rate_limit_info={'status': 'maybe'}),
                 wire('assistant', error={'message': 'auth'}),
                 wire('stream_event', parent_tool_use_id='nested_agent', event={'type': 'message_stop'})]
        for raw in cases:
            with self.subTest(raw=raw[:60]), self.assertRaises(ValueError):
                parser_type('run1')(raw)
        parser = parser_type('run1')
        init(parser)
        with self.assertRaises(ValueError):
            init(parser)
        deeply_nested = wire('system', subtype='api_retry', extra={'x': [[[[[[[[[[[[[[[[[[0]]]]]]]]]]]]]]]]]]})
        with self.assertRaises(ValueError):
            parser_type('run1')(deeply_nested)

    def test_reused_open_tool_block_index_is_rejected(self):
        _, parser_type, _, _, _ = _api()
        parser = parser_type('run1')
        init(parser)
        begin_stream_message(parser, 'msg_tools')
        stream_record(parser, 'content_block_start', index=0,
            content_block={'type': 'tool_use', 'id': 'toolu_1', 'name': 'Read', 'input': {}})
        with self.assertRaises(ValueError):
            stream_record(parser, 'content_block_start', index=0,
                content_block={'type': 'tool_use', 'id': 'toolu_2', 'name': 'Write', 'input': {}})

    def test_record_count_and_aggregate_jsonl_bytes_are_bounded(self):
        _, parser_type, _, _, _ = _api()
        parser = parser_type('run1', max_records=2)
        parser(wire('system', subtype='api_retry', attempt=1))
        parser(wire('system', subtype='api_retry', attempt=2))
        with self.assertRaises(ValueError):
            parser(wire('system', subtype='api_retry', attempt=3))
        parser = parser_type('run1')
        line = wire('system', subtype='api_retry', attempt=1, padding='x' * 60000)
        self.assertLessEqual(len(line.encode('utf-8')), 65536)
        with self.assertRaises(ValueError):
            for _ in range(150):
                parser(line)


if __name__ == '__main__':
    unittest.main()
