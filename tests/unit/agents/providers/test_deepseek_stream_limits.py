"""Streaming envelopes have a different byte budget from the final answer."""
import io
import json

import pytest

from mathmodel_paper_studio.agents.providers.deepseek_api import (
    DeepSeekApiClient, _MAX_STREAM_RESPONSE_BYTES,
)


def event(delta, finish_reason=None):
    payload = {
        'id': '00000000-0000-0000-0000-000000000000',
        'object': 'chat.completion.chunk',
        'created': 1792497600,
        'model': 'deepseek-flash',
        'system_fingerprint': '0' * 32,
        'choices': [{'index': 0, 'delta': delta, 'finish_reason': finish_reason,
                     'logprobs': None}],
    }
    if finish_reason is not None:
        payload['usage'] = {'prompt_tokens': 100, 'completion_tokens': 8192,
                            'total_tokens': 8292}
    return b'data: ' + json.dumps(payload, separators=(',', ':')).encode('utf-8') + b'\n\n'


def test_valid_thinking_stream_accepts_envelopes_over_two_mebibytes():
    # Real-provider structure: each token repeats roughly 300 bytes of metadata.
    # The ignored reasoning and final answer remain small despite a large wire body.
    body = (event({'content': None, 'reasoning_content': 'r'}) * 8192
            + event({'content': '{"ok":true}', 'reasoning_content': None})
            + event({'content': '', 'reasoning_content': None}, 'stop')
            + b'data: [DONE]\n\n')

    assert len(body) > 2 * 1024 * 1024
    assert DeepSeekApiClient._extract_stream_content(io.BytesIO(body)) == '{"ok":true}'


def test_max_thinking_stream_accepts_full_budget_of_small_reasoning_deltas():
    # 128K tokens repeat ~300-byte envelopes, above the old 32 MiB wire limit.
    body = (event({'content': None, 'reasoning_content': 'r'}) * 131072
            + event({'content': '{"ok":true}'}, 'stop') + b'data: [DONE]\n\n')

    assert len(body) > 32 * 1024 * 1024
    assert DeepSeekApiClient._extract_stream_content(io.BytesIO(body)) == '{"ok":true}'


def test_stream_wire_limit_still_rejects_excessive_heartbeat_bytes():
    # Small comment lines still cannot consume unbounded transport bytes.
    heartbeat = b':' + b'x' * 32767 + b'\n\n'
    body = (heartbeat * (_MAX_STREAM_RESPONSE_BYTES // len(heartbeat) + 1)
            + event({'content': 'ok'}, 'stop') + b'data: [DONE]\n\n')
    stream = io.BytesIO(body)

    with pytest.raises(ValueError) as raised:
        DeepSeekApiClient._extract_stream_content(stream)

    assert raised.value.diagnostic_code == 'invalid_response'
    assert stream.tell() > _MAX_STREAM_RESPONSE_BYTES


def test_stream_answer_remains_limited_to_one_mebibyte():
    body = (event({'content': 'x' * 16384}) * 65
            + event({}, 'stop') + b'data: [DONE]\n\n')

    with pytest.raises(ValueError) as raised:
        DeepSeekApiClient._extract_stream_content(io.BytesIO(body))

    assert raised.value.diagnostic_code == 'invalid_response'
