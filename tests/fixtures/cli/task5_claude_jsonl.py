"""Fixed local-only Claude-like NDJSON process fixture; never calls a provider."""
import json
import os
import sys
import time


def emit(value, *, newline=True, split_utf8=False):
    raw = json.dumps(value, ensure_ascii=False, separators=(',', ':')).encode('utf-8')
    suffix = b'\n' if newline else b''
    if split_utf8:
        marker = '中'.encode('utf-8')
        offset = raw.index(marker) + 1
        os.write(1, raw[:offset])
        time.sleep(.02)
        os.write(1, raw[offset:] + suffix)
    else:
        os.write(1, raw + suffix)


def system_init():
    emit({'type': 'system', 'subtype': 'init', 'session_id': 'session_fixture_1',
          'tools': ['Read', 'Write']})


def text_delta(value='中文结果', *, split=False):
    emit({'type': 'stream_event', 'session_id': 'session_fixture_1',
          'event': {'type': 'message_start', 'message': {'id': 'msg_fixture_text',
                    'type': 'message', 'role': 'assistant', 'content': []}}})
    emit({'type': 'stream_event', 'session_id': 'session_fixture_1',
          'event': {'type': 'content_block_start', 'index': 0,
                    'content_block': {'type': 'text', 'text': ''}}})
    emit({'type': 'stream_event', 'session_id': 'session_fixture_1',
          'event': {'type': 'content_block_delta', 'index': 0,
                    'delta': {'type': 'text_delta', 'text': value}}}, split_utf8=split)
    emit({'type': 'stream_event', 'session_id': 'session_fixture_1',
          'event': {'type': 'content_block_stop', 'index': 0}})
    emit({'type': 'stream_event', 'session_id': 'session_fixture_1',
          'event': {'type': 'message_delta', 'delta': {'stop_reason': 'end_turn', 'stop_sequence': None}}})
    emit({'type': 'stream_event', 'session_id': 'session_fixture_1', 'event': {'type': 'message_stop'}})


def success(*, newline=True):
    return {'type': 'result', 'subtype': 'success', 'is_error': False,
            'num_turns': 2, 'session_id': 'session_fixture_1', 'result': '中文结果'}


def main():
    mode = sys.argv[1]
    if mode in ('success', 'final_no_newline', 'nonzero', 'double_terminal',
                'cancel', 'timeout', 'tool', 'auth', 'billing', 'rate_limit', 'missing_terminal',
                'truncated', 'invalid_utf8', 'forbidden_tool'):
        system_init()
    if mode == 'missing_terminal':
        return 0
    if mode == 'truncated':
        os.write(1, b'{"type":"result"')
        return 0
    if mode == 'invalid_utf8':
        os.write(1, b'\xff\n')
        return 0
    if mode in ('auth', 'billing', 'rate_limit'):
        code = {'auth': 'authentication_failed', 'billing': 'billing_error',
                'rate_limit': 'rate_limit'}[mode]
        emit({'type': 'assistant', 'error': code})
        emit({'type': 'result', 'subtype': 'success', 'is_error': True, 'num_turns': 2,
              'session_id': 'session_fixture_1', 'errors': [code]})
        return 0
    if mode in ('cancel', 'timeout'):
        text_delta('ready')
        time.sleep(10)
        return 0
    if mode == 'forbidden_tool':
        emit({'type': 'stream_event', 'event': {'type': 'message_start',
              'message': {'id': 'msg_fixture_forbidden'}}})
        emit({'type': 'stream_event', 'event': {'type': 'content_block_start', 'index': 0,
              'content_block': {'type': 'tool_use', 'id': 'toolu_2', 'name': 'Bash',
                                'input': {'command': 'private-command'}}}})
        return 0
    if mode == 'tool':
        emit({'type': 'stream_event', 'event': {'type': 'message_start',
              'message': {'id': 'msg_fixture_tool'}}})
        emit({'type': 'stream_event', 'event': {'type': 'content_block_start', 'index': 0,
              'content_block': {'type': 'tool_use', 'id': 'toolu_1', 'name': 'Read',
                                'input': {'file_path': 'private-path'}}}})
        emit({'type': 'stream_event', 'event': {'type': 'content_block_delta', 'index': 0,
              'delta': {'type': 'input_json_delta', 'partial_json': '{"file_path":"private-path"}'}}})
        emit({'type': 'stream_event', 'event': {'type': 'content_block_stop', 'index': 0}})
        emit({'type': 'stream_event', 'event': {'type': 'message_delta',
              'delta': {'stop_reason': 'tool_use', 'stop_sequence': None}}})
        emit({'type': 'stream_event', 'event': {'type': 'message_stop'}})
        emit({'type': 'user', 'message': {'content': [{'type': 'tool_result',
              'tool_use_id': 'toolu_1', 'content': 'private-tool-result', 'is_error': False}]}})
        emit(success())
        return 0
    text_delta(split=True)
    if mode == 'truncated':
        return 0
    final_newline = mode != 'final_no_newline'
    emit(success(), newline=final_newline)
    if mode == 'double_terminal':
        emit(success())
    if mode == 'nonzero':
        return 7
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
