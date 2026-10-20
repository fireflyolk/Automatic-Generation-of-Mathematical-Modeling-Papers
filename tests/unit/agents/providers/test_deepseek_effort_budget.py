"""Reasoning budgets must leave space for a complete, bounded final answer."""
import json

import pytest

from mathmodel_paper_studio.agents.deepseek_model_preferences import DeepSeekModelPreference
from mathmodel_paper_studio.agents.providers.deepseek_api import DeepSeekApiClient, MODEL
from tests.unit.agents.providers.test_deepseek_api import (
    API_KEY, FakeConnection, FakeResponse, chat_response, make_context, server_sent_events,
)


@pytest.mark.parametrize('effort, expected', [
    ('none', 8192), ('low', 32768), ('high', 32768), ('max', 32768),
])
def test_thinking_uses_authorized_headroom_instead_of_fixed_eight_k(effort, expected):
    connection = FakeConnection()
    client = DeepSeekApiClient(
        key_resolver=lambda: API_KEY, transport_factory=lambda *args: connection,
        preference_resolver=lambda: DeepSeekModelPreference(MODEL, effort, 32768),
    )
    client.authorize_chargeable_requests('run_1')

    client.generate(make_context(), 'prompt')

    assert json.loads(connection.request_args[2])['max_tokens'] == expected


@pytest.mark.parametrize('effort, expected', [
    ('none', 8192), ('low', 65536), ('high', 65536), ('max', 131072),
])
def test_current_catalog_allows_provider_recommended_thinking_budgets(effort, expected):
    connection = FakeConnection()
    client = DeepSeekApiClient(
        key_resolver=lambda: API_KEY, transport_factory=lambda *args: connection,
        preference_resolver=lambda: DeepSeekModelPreference(MODEL, effort, 131072),
    )
    client.authorize_chargeable_requests('run_1')

    client.generate(make_context(), 'prompt')

    assert json.loads(connection.request_args[2])['max_tokens'] == expected


def test_thinking_length_retry_grows_to_frozen_ceiling_and_discards_partial_answer():
    truncated = server_sent_events(
        {'choices': [{'index': 0, 'delta': {'content': '{"partial":'}}]},
        {'choices': [{'index': 0, 'delta': {}, 'finish_reason': 'length'}]}, '[DONE]',
    )
    connections = [
        FakeConnection(FakeResponse(truncated, content_type='text/event-stream')),
        FakeConnection(FakeResponse(chat_response('{"ok": true}'))),
    ]
    seen = []
    preference = [DeepSeekModelPreference(MODEL, 'high', 131072)]

    def transport(*args):
        connection = connections.pop(0)
        seen.append(connection)
        preference[0] = DeepSeekModelPreference(MODEL, 'none', 8192)
        return connection

    client = DeepSeekApiClient(
        key_resolver=lambda: API_KEY, transport_factory=transport,
        preference_resolver=lambda: preference[0],
    )
    client.authorize_chargeable_requests('run_1')

    assert client.generate(make_context(), 'prompt') == '{"ok": true}'
    bodies = [json.loads(connection.request_args[2]) for connection in seen]
    assert [body['max_tokens'] for body in bodies] == [65536, 131072]
    assert [body['reasoning_effort'] for body in bodies] == ['high', 'high']
