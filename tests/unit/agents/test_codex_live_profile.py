import json
import unittest

from mathmodel_paper_studio.agents.providers.codex_cli import (
    CODEX_DISABLED_FEATURES,
    CodexEventParser,
    codex_launch_arguments,
    codex_connection_canary_passed,
)


def wire(kind, **fields):
    return json.dumps({'type': kind, **fields}, ensure_ascii=False)


def canary_output(message='CODEX_CONNECTION_OK', item_type='agent_message'):
    records = (
        {'type': 'thread.started', 'thread_id': 'thread_probe'},
        {'type': 'turn.started'},
        {'type': 'item.completed', 'item': {'id': 'item_probe', 'type': item_type, 'text': message}},
        {'type': 'turn.completed', 'usage': {'input_tokens': 10, 'cached_input_tokens': 0, 'output_tokens': 1}},
    )
    return ''.join(wire(record.pop('type'), **record) + '\n' for record in map(dict, records))


def canary_output_with_reasoning():
    records = (
        {'type': 'thread.started', 'thread_id': 'thread_probe'},
        {'type': 'turn.started'},
        {'type': 'item.completed', 'item': {'id': 'reasoning_probe', 'type': 'reasoning', 'text': 'internal'}},
        {'type': 'item.completed', 'item': {'id': 'item_probe', 'type': 'agent_message', 'text': 'CODEX_CONNECTION_OK'}},
        {'type': 'turn.completed', 'usage': {'input_tokens': 10, 'cached_input_tokens': 0, 'output_tokens': 1}},
    )
    return ''.join(wire(record.pop('type'), **record) + '\n' for record in map(dict, records))


def canary_output_with_nonfatal_error():
    records = (
        {'type': 'thread.started', 'thread_id': 'thread_probe'},
        {'type': 'item.completed', 'item': {'id': 'error_probe', 'type': 'error', 'message': 'safe generic'}},
        {'type': 'turn.started'},
        {'type': 'item.completed', 'item': {'id': 'item_probe', 'type': 'agent_message', 'text': 'CODEX_CONNECTION_OK'}},
        {'type': 'turn.completed', 'usage': {'input_tokens': 10, 'cached_input_tokens': 0, 'output_tokens': 1}},
    )
    return ''.join(wire(record.pop('type'), **record) + '\n' for record in map(dict, records))


class CodexLockedProfileTests(unittest.TestCase):
    def test_nonfatal_startup_error_item_preceding_turn_is_normalized_safely(self):
        parser = CodexEventParser('r1')
        parser(wire('thread.started', thread_id='thread_probe'))
        events = parser(wire('item.completed', item={
            'id': 'error_probe', 'type': 'error', 'message': 'private provider detail',
        }))
        self.assertEqual(events[0].text, 'CLI 报告了非致命错误。')
        self.assertNotIn('private', repr(events))
        self.assertEqual(parser(wire('turn.started'))[0].kind.value, 'started')
        with self.assertRaises(ValueError):
            parser(wire('item.completed', item={
                'id': 'error_probe', 'type': 'error', 'message': 'duplicate',
            }))

    def test_launch_profile_disables_every_observed_codex_tool_feature(self):
        args = codex_launch_arguments()
        disabled = tuple(args[index + 1] for index, value in enumerate(args[:-1]) if value == '--disable')
        self.assertEqual(disabled, CODEX_DISABLED_FEATURES)
        self.assertIn(('--sandbox', 'read-only'), tuple(zip(args, args[1:])))
        self.assertIn('--ignore-user-config', args)
        self.assertIn('--ignore-rules', args)
        self.assertIn('code_mode', disabled)
        self.assertIn('code_mode_host', disabled)
        self.assertIn('code_mode_only', disabled)
        self.assertIn('shell_tool', disabled)
        self.assertNotIn('--dangerously-bypass-approvals-and-sandbox', args)

    def test_connection_canary_proves_text_response_only(self):
        self.assertTrue(codex_connection_canary_passed(canary_output(), exit_code=0))
        self.assertTrue(codex_connection_canary_passed(canary_output_with_reasoning(), exit_code=0))
        self.assertTrue(codex_connection_canary_passed(canary_output_with_nonfatal_error(), exit_code=0))
        self.assertFalse(codex_connection_canary_passed(canary_output('TOOL_NOT_AVAILABLE'), exit_code=0))
        self.assertFalse(codex_connection_canary_passed(canary_output('almost'), exit_code=0))
        self.assertFalse(codex_connection_canary_passed(canary_output(item_type='command_execution'), exit_code=0))
        self.assertFalse(codex_connection_canary_passed(canary_output(), exit_code=1))
        self.assertFalse(codex_connection_canary_passed(canary_output().replace('turn.completed', 'turn.failed'), exit_code=0))


if __name__ == '__main__':
    unittest.main()
