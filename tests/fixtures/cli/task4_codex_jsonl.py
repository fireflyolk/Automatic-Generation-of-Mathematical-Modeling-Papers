"""Test-only deterministic JSONL writer; never used by the production adapter."""
import json
import os
import sys
import time

mode = sys.argv[1]
usage = {'input_tokens': 1, 'cached_input_tokens': 0, 'output_tokens': 1}


def send(value, *, newline=True, split=False):
    data = (json.dumps(value, ensure_ascii=False) + ('\r\n' if newline else '')).encode('utf-8')
    if split:
        for byte in data:
            os.write(1, bytes((byte,)))
            time.sleep(.0002)
    else:
        os.write(1, data)


sys.stdin.buffer.read()
send({'type': 'thread.started', 'thread_id': 'thread_fake'})
send({'type': 'turn.started'})
if mode in ('timeout', 'cancel'):
    send({'type': 'item.completed', 'item': {'id': 'item_0', 'type': 'agent_message', 'text': '已启动'}})
    time.sleep(30)
elif mode == 'tool':
    send({'type': 'item.completed', 'item': {'id': 'tool_0', 'type': 'command_execution',
          'command': 'fixture only', 'aggregated_output': '', 'status': 'completed', 'exit_code': 0}})
else:
    send({'type': 'item.completed', 'item': {'id': 'item_0', 'type': 'agent_message', 'text': '中文结果'}}, split=True)
    if mode == 'truncated':
        os.write(1, b'{"type":')
    elif mode == 'invalid_utf8':
        os.write(1, b'\xe4\xb8')
    elif mode != 'missing_terminal':
        send({'type': 'turn.completed', 'usage': usage}, newline=mode != 'final_no_newline')
        if mode == 'double_terminal':
            send({'type': 'turn.failed', 'error': {'message': 'private failure'}})
    if mode == 'nonzero':
        os.write(2, b'opaque-account private-prompt private-path')
        sys.exit(7)
