"""Visible previews arrive during transport without exposing hidden provider data."""
import json
import threading

import pytest
from unittest.mock import patch

from mathmodel_paper_studio.agents.deepseek_model_preferences import DeepSeekModelPreference
from mathmodel_paper_studio.agents.protocol import EventKind
from mathmodel_paper_studio.agents.providers.deepseek_api import DeepSeekApiClient, MODEL
from tests.unit.agents.providers.test_deepseek_api import (
    API_KEY, FakeConnection, FakeResponse, chat_response, make_context,
)


def packet(content=None, *, thought=None, reason=None):
    delta = {}
    if content is not None:
        delta['content'] = content
    if thought is not None:
        delta['reasoning_content'] = thought
    choice = {'index': 0, 'delta': delta}
    if reason is not None:
        choice['finish_reason'] = reason
    return ('data: ' + json.dumps({'choices': [choice]}) + '\n\n').encode('utf-8')


class SequencedResponse(FakeResponse):
    """Deliver separate packets and inspect progress before the next read."""

    def __init__(self, chunks, before_read=None):
        super().__init__(b'', content_type='text/event-stream')
        self.chunks = list(chunks)
        self.read_count = 0
        self.before_read = before_read

    def read1(self, _size=-1):
        if self.before_read is not None:
            self.before_read(self.read_count)
        self.read_count += 1
        return self.chunks.pop(0) if self.chunks else b''


def client_for(response, *, key_resolver=lambda: API_KEY, preference=None):
    connection = FakeConnection(response)
    client = DeepSeekApiClient(
        key_resolver=key_resolver, transport_factory=lambda *_args: connection,
        preference_resolver=lambda: preference,
    )
    client.authorize_chargeable_requests('run_1')
    return client


def visible(events):
    return ''.join(event.text for event in events if event.kind is EventKind.TEXT)


def test_local_probe_advertises_visible_streaming_without_network_requests():
    requests = []

    def unexpected_transport(*_args):
        requests.append(True)
        raise AssertionError('a local probe must not open transport')

    client = DeepSeekApiClient(
        key_resolver=lambda: API_KEY, transport_factory=unexpected_transport,
    )

    assert client.probe().capabilities.streaming is True
    assert client.capabilities().streaming is True
    assert requests == []


def test_preview_arrives_before_response_finishes_and_is_not_emitted_twice():
    events = []
    first = 'x' * 2400

    def before_read(index):
        if index == 1:
            assert first[:1000] in visible(events), 'preview must arrive before final SSE packet'

    response = SequencedResponse([
        packet(first, thought='HIDDEN_PROVIDER_REASONING'),
        packet(' zzz\n', reason='stop'), b'data: [DONE]\n\n',
    ], before_read)
    client = client_for(response)

    result = client.generate_with_progress(make_context(), 'request prompt', event_callback=events.append)

    assert result == first + ' zzz\n'
    assert visible(events) == result
    assert 'HIDDEN_PROVIDER_REASONING' not in repr(events)
    assert events[-1].kind is EventKind.COMPLETED


def test_preview_redacts_the_actual_request_key_even_across_stream_packets():
    request_key = 'unmarked-local-private-credential'
    resolved = []
    events = []

    def key_resolver():
        resolved.append(True)
        return request_key

    response = SequencedResponse([
        packet(request_key[:9]), packet(request_key[9:] + '\n'),
        packet(reason='stop'), b'data: [DONE]\n\n',
    ])
    client = client_for(response, key_resolver=key_resolver)

    with pytest.raises(ValueError):
        client.generate_with_progress(make_context(), 'request prompt', event_callback=events.append)

    assert len(resolved) == 1, 'preview must use the same key resolution as the actual request'
    assert request_key not in repr(events)
    assert request_key[:9] not in visible(events)
    assert '[已隐藏]' in visible(events)


def test_preview_keeps_prompt_and_problem_echoes_visible_across_packets():
    prompt = 'private-request-prompt-that-must-be-visible'
    context = make_context()
    context.authorized_problem = '用户题目原文需要在观察窗中保留'
    events = []
    response = SequencedResponse([
        packet(prompt[:10]), packet(prompt[10:] + '\n'),
        packet(context.authorized_problem[:4]),
        packet(context.authorized_problem[4:] + '\nzzz\n', reason='stop'),
        b'data: [DONE]\n\n',
    ])
    client = client_for(response)

    result = client.generate_with_progress(context, prompt, event_callback=events.append)

    assert result == prompt + '\n' + context.authorized_problem + '\nzzz\n'
    assert prompt in visible(events)
    assert context.authorized_problem in visible(events)
    assert 'zzz' in visible(events)
    assert '[已隐藏]' not in visible(events)


def test_length_retry_separates_discarded_preview_from_new_attempt():
    events = []
    old, new = 'x' * 2400 + '\n', 'z' * 2400 + '\n'
    connections = [
        FakeConnection(SequencedResponse([
            packet(old), packet(reason='length'), b'data: [DONE]\n\n',
        ])),
        FakeConnection(SequencedResponse([
            packet(new, reason='stop'), b'data: [DONE]\n\n',
        ])),
    ]
    client = DeepSeekApiClient(
        key_resolver=lambda: API_KEY,
        transport_factory=lambda *_args: connections.pop(0),
        preference_resolver=lambda: DeepSeekModelPreference(MODEL, 'none', 32768),
    )
    client.authorize_chargeable_requests('run_1')

    result = client.generate_with_progress(make_context(), 'request prompt', event_callback=events.append)

    retry_index = next(index for index, event in enumerate(events) if event.kind.value == 'retrying')
    assert visible(events[:retry_index]) == old
    assert visible(events[retry_index + 1:]) == new
    assert result == new
    assert sum(event.kind is EventKind.STARTED for event in events) == 2


@pytest.mark.parametrize('stop_kind', ['cancel', 'revoke'])
def test_no_preview_is_emitted_after_cancellation_or_revocation(stop_kind):
    events = []
    cancel_event = threading.Event()
    first = 'x' * 2400

    def before_read(index):
        if index == 1:
            assert visible(events)
            if stop_kind == 'cancel':
                cancel_event.set()
            else:
                client.revoke_chargeable_requests('run_1')

    response = SequencedResponse([
        packet(first), packet('SHOULD_NOT_APPEAR\n', reason='stop'), b'data: [DONE]\n\n',
    ], before_read)
    client = client_for(response)

    with pytest.raises(RuntimeError):
        client.generate_with_progress(
            make_context(), 'request prompt', cancel_event=cancel_event,
            event_callback=events.append,
        )

    assert 'SHOULD_NOT_APPEAR' not in visible(events)
    assert events[-1].kind in (EventKind.CANCELLED, EventKind.BLOCKED)


def test_preview_observer_failure_does_not_change_final_result():
    result = 'x' * 2400
    client = client_for(SequencedResponse([
        packet(result, reason='stop'), b'data: [DONE]\n\n',
    ]))

    def broken_observer(_event):
        raise RuntimeError('display failure')

    assert client.generate_with_progress(
        make_context(), 'request prompt', event_callback=broken_observer,
    ) == result


def test_non_streaming_compatibility_emits_safe_completed_response_once():
    events = []
    client = client_for(FakeResponse(chat_response('zzz\n')))

    assert client.generate_with_progress(
        make_context(), 'request prompt', event_callback=events.append,
    ) == 'zzz\n'
    assert visible(events) == 'zzz\n'


def test_many_small_deltas_are_batched_with_bounded_utf8_events():
    events = []
    content = '中' * 1500 + '\n'
    client = client_for(SequencedResponse([
        *(packet(character) for character in content),
        packet(reason='stop'), b'data: [DONE]\n\n',
    ]))

    with patch('mathmodel_paper_studio.agents.providers.deepseek_api.time.monotonic',
               return_value=1000.0):
        result = client.generate_with_progress(
            make_context(), 'request prompt', event_callback=events.append,
        )

    chunks = [event.text for event in events if event.kind is EventKind.TEXT]
    assert result == content
    assert ''.join(chunks) == content
    assert len(chunks) <= 5, 'one token must not produce one queued UI event'
    assert max(len(chunk.encode('utf-8')) for chunk in chunks) <= 2048


def test_length_retry_resets_redaction_carry_state_for_the_new_request():
    key = 'private-credential-ABCDEFGHIJ'
    prefix, valid_suffix = key[:8], key[8:]
    events = []
    connections = [
        FakeConnection(SequencedResponse([
            packet(prefix), packet(reason='length'), b'data: [DONE]\n\n',
        ])),
        FakeConnection(SequencedResponse([
            packet(valid_suffix + '\n', reason='stop'), b'data: [DONE]\n\n',
        ])),
    ]
    client = DeepSeekApiClient(
        key_resolver=lambda: key,
        transport_factory=lambda *_args: connections.pop(0),
        preference_resolver=lambda: DeepSeekModelPreference(MODEL, 'none', 32768),
    )
    client.authorize_chargeable_requests('run_1')

    result = client.generate_with_progress(make_context(), 'request prompt', event_callback=events.append)

    retry_index = next(index for index, event in enumerate(events) if event.kind is EventKind.RETRYING)
    assert result == valid_suffix + '\n'
    assert visible(events[retry_index + 1:]) == result
    assert prefix not in visible(events[:retry_index])


def test_redaction_failure_disables_preview_without_releasing_raw_output():
    events = []
    content = 'zzz\n'
    client = client_for(SequencedResponse([
        packet(content, reason='stop'), b'data: [DONE]\n\n',
    ]))

    with patch('mathmodel_paper_studio.agents.providers.deepseek_api._Redactor',
               side_effect=ValueError('redaction unavailable')):
        result = client.generate_with_progress(
            make_context(), 'request prompt', event_callback=events.append,
        )

    assert result == content
    assert visible(events) == ''
    assert events[-1].kind is EventKind.COMPLETED


def test_observer_failure_on_first_text_disables_later_preview_text_only():
    events = []
    content = 'x' * 7000
    client = client_for(SequencedResponse([
        packet(content, reason='stop'), b'data: [DONE]\n\n',
    ]))

    def observer(event):
        events.append(event)
        if event.kind is EventKind.TEXT:
            raise RuntimeError('UI delivery failed')

    assert client.generate_with_progress(
        make_context(), 'request prompt', event_callback=observer,
    ) == content
    assert sum(event.kind is EventKind.TEXT for event in events) == 1
    assert events[-1].kind is EventKind.COMPLETED
