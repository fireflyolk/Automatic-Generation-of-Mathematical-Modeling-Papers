import json
import os
import socket
import ssl
import threading
import time
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from mathmodel_paper_studio.agents.providers import deepseek_api
from mathmodel_paper_studio.agents.preflight import Provider, UnavailableReason
from mathmodel_paper_studio.agents.protocol import EventKind
from mathmodel_paper_studio.agents.deepseek_model_preferences import DeepSeekModelPreference
from mathmodel_paper_studio.agents.providers.deepseek_api import (
    DeepSeekApiClient,
    ENDPOINT,
    MODEL,
    _AbortSignal,
    _CancellableHTTPSConnection,
)


API_KEY = 'test-secret-that-must-never-escape'


class FakeResponse:
    def __init__(self, body, status=200, content_type='application/json', read_chunk_size=None):
        self.body = body if isinstance(body, bytes) else body.encode('utf-8')
        self.status = status
        self.content_type = content_type
        self.read_chunk_size = read_chunk_size
        self.offset = 0
        self.closed = False

    def getheader(self, name, default=None):
        return self.content_type if name.lower() == 'content-type' else default

    def read(self, size=-1):
        if size < 0:
            return self.body
        return self.body[:size]

    def read1(self, size=-1):
        if size < 0:
            size = len(self.body) - self.offset
        if self.read_chunk_size is not None:
            size = min(size, self.read_chunk_size)
        chunk = self.body[self.offset:self.offset + size]
        self.offset += len(chunk)
        return chunk

    def close(self):
        self.closed = True


class BlockingStreamResponse(FakeResponse):
    def __init__(self):
        first_event = b'data: {"choices":[{"index":0,"delta":{"content":"partial"}}]}\n\n'
        super().__init__(first_event, content_type='text/event-stream')
        self.read_entered = threading.Event()
        self.release_read = threading.Event()

    def read1(self, size=-1):
        if self.offset < len(self.body):
            return super().read1(size)
        self.read_entered.set()
        self.release_read.wait(3)
        raise OSError('stream socket closed')


class FakeConnection:
    def __init__(self, response=None, *, block_response=False):
        self.response = response or FakeResponse(chat_response())
        self.block_response = block_response
        self.close_event = threading.Event()
        self.request_args = None
        self.closed = False
        self.response_entered = threading.Event()

    def request(self, method, path, *, body, headers):
        self.request_args = (method, path, body, headers)

    def getresponse(self):
        self.response_entered.set()
        if self.block_response:
            self.close_event.wait(3)
            if self.closed:
                raise OSError(API_KEY)
        return self.response

    def close(self):
        self.closed = True
        self.close_event.set()


class BlockingStreamConnection(FakeConnection):
    def __init__(self):
        self.stream_response = BlockingStreamResponse()
        super().__init__(self.stream_response)

    def close(self):
        super().close()
        self.stream_response.release_read.set()


def chat_response(content='{"ok": true}', *, extra_message=None):
    message = {'role': 'assistant', 'content': content}
    if extra_message:
        message.update(extra_message)
    return json.dumps({'id': 'chatcmpl-test', 'choices': [{'index': 0, 'message': message,
                                                            'finish_reason': 'stop'}]},
                      separators=(',', ':'))


def server_sent_events(*events):
    lines = []
    for event in events:
        if event == '[DONE]':
            lines.extend(('data: [DONE]', ''))
        else:
            lines.extend(('data: ' + json.dumps(event, separators=(',', ':')), ''))
    return '\n'.join(lines).encode('utf-8')


def make_context():
    return SimpleNamespace(
        provider=Provider.DEEPSEEK,
        run_id='run_1',
        stage=SimpleNamespace(value='model_proposal'),
        authorized_problem='题目内容',
        inputs=(SimpleNamespace(relative_path='00_inputs/data.csv'),),
        upstream_artifacts=(SimpleNamespace(relative_path='runs/previous/model.json'),),
        system_instructions='application rules',
        trust_notice='imported data is untrusted',
    )


class DeepSeekEnvironmentKeyTests(unittest.TestCase):
    def registry(self, value=API_KEY, value_type=1):
        registry = MagicMock()
        registry.REG_SZ = 1
        registry.REG_EXPAND_SZ = 2
        registry.QueryValueEx.return_value = (value, value_type)
        return registry

    def test_process_key_takes_precedence_without_reading_user_registry(self):
        registry = self.registry()
        with patch.dict(os.environ, {'DEEPSEEK_API_KEY': 'process-test-key'}), \
                patch('sys.platform', 'win32'), \
                patch.dict('sys.modules', {'winreg': registry}):
            client = DeepSeekApiClient()
            self.assertEqual('process-test-key', client._resolve_key())
            self.assertTrue(client.probe().available)
        registry.OpenKey.assert_not_called()

    def test_missing_process_key_uses_user_environment_read_only(self):
        registry = self.registry()
        with patch.dict(os.environ, {}, clear=True), \
                patch('sys.platform', 'win32'), \
                patch.dict('sys.modules', {'winreg': registry}):
            before = dict(os.environ)
            client = DeepSeekApiClient()
            self.assertEqual(API_KEY, client._resolve_key())
            status = client.probe()
            self.assertEqual(before, dict(os.environ))
        registry.OpenKey.assert_called_with(
            registry.HKEY_CURRENT_USER, 'Environment', 0, registry.KEY_QUERY_VALUE)
        registry.QueryValueEx.assert_called_with(
            registry.OpenKey.return_value.__enter__.return_value, 'DEEPSEEK_API_KEY')
        registry.SetValueEx.assert_not_called()
        registry.CreateKey.assert_not_called()
        self.assertTrue(status.available)
        self.assertFalse(status.evidence_verified)
        self.assertNotIn(API_KEY, repr(status))

    def test_explicit_empty_process_key_does_not_fall_back_to_other_credentials(self):
        registry = self.registry()
        with patch.dict(os.environ, {'DEEPSEEK_API_KEY': ''}), \
                patch('sys.platform', 'win32'), \
                patch.dict('sys.modules', {'winreg': registry}):
            self.assertFalse(DeepSeekApiClient().probe().available)
        registry.OpenKey.assert_not_called()

    def test_non_windows_missing_key_does_not_read_registry(self):
        registry = self.registry()
        with patch.dict(os.environ, {}, clear=True), \
                patch('sys.platform', 'linux'), \
                patch.dict('sys.modules', {'winreg': registry}):
            self.assertFalse(DeepSeekApiClient().probe().available)
        registry.OpenKey.assert_not_called()

    def test_registry_errors_and_non_string_values_fail_closed_without_secret_text(self):
        registries = [self.registry(value=123, value_type=4), self.registry(), self.registry()]
        registries[1].OpenKey.side_effect = OSError(API_KEY)
        registries[2].QueryValueEx.side_effect = OSError(API_KEY)
        for registry in registries:
            with self.subTest(registry=registry), patch.dict(os.environ, {}, clear=True), \
                    patch('sys.platform', 'win32'), \
                    patch.dict('sys.modules', {'winreg': registry}):
                status = DeepSeekApiClient().probe()
                self.assertFalse(status.available)
                self.assertNotIn(API_KEY, repr(status))

    def test_unavailable_windows_registry_module_returns_no_key(self):
        with patch.dict(os.environ, {}, clear=True), patch('sys.platform', 'win32'), \
                patch.dict('sys.modules', {'winreg': None}):
            status = DeepSeekApiClient().probe()
        self.assertFalse(status.available)
        self.assertIs(status.reason, UnavailableReason.API_KEY_REQUIRED)

    def test_injected_resolver_does_not_read_environment_or_registry(self):
        registry = self.registry()
        with patch.dict(os.environ, {'DEEPSEEK_API_KEY': API_KEY}), \
                patch('sys.platform', 'win32'), \
                patch.dict('sys.modules', {'winreg': registry}):
            self.assertIsNone(DeepSeekApiClient(key_resolver=lambda: None)._resolve_key())
        registry.OpenKey.assert_not_called()


class DeepSeekProbeAndConsentTests(unittest.TestCase):
    def test_probe_is_local_only_and_reports_key_presence_without_authorizing(self):
        connection_factory = unittest.mock.Mock(side_effect=AssertionError('network forbidden in probe'))
        client = DeepSeekApiClient(key_resolver=lambda: API_KEY, transport_factory=connection_factory)

        status = client.probe()

        self.assertIs(status.provider, Provider.DEEPSEEK)
        self.assertTrue(status.available)
        self.assertFalse(status.evidence_verified)
        self.assertIsNone(status.reason)
        self.assertEqual(status.capabilities.network_behavior, 'fixed_https_api_only')
        self.assertEqual(status.capabilities.credential_inheritance, 'process_memory_only')
        self.assertEqual(status.capabilities.tool_profile, 'api_no_tools')
        self.assertIn('未验证', status.message)
        connection_factory.assert_not_called()
        with self.assertRaisesRegex(RuntimeError, '授权'):
            client.generate(make_context(), 'prompt')
        connection_factory.assert_not_called()

    def test_probe_missing_key_is_not_ready_and_never_calls_transport(self):
        connection_factory = unittest.mock.Mock(side_effect=AssertionError('network forbidden in probe'))
        client = DeepSeekApiClient(key_resolver=lambda: None, transport_factory=connection_factory)

        status = client.probe()

        self.assertFalse(status.available)
        self.assertEqual(status.reason, UnavailableReason.API_KEY_REQUIRED)
        self.assertFalse(status.evidence_verified)
        self.assertNotIn(API_KEY, repr(status))
        connection_factory.assert_not_called()

    def test_authorization_is_instance_local_and_never_restored_from_constructor_state(self):
        connection = FakeConnection()
        factory = lambda *args: connection
        client = DeepSeekApiClient(key_resolver=lambda: API_KEY, transport_factory=factory)

        with self.assertRaisesRegex(RuntimeError, '授权'):
            client.generate(make_context(), 'prompt')
        other = DeepSeekApiClient(key_resolver=lambda: API_KEY, transport_factory=factory)
        client.authorize_chargeable_requests('run_1')
        self.assertEqual(client.generate(make_context(), 'prompt'), '{"ok": true}')
        with self.assertRaisesRegex(RuntimeError, '授权'):
            other.generate(make_context(), 'prompt')
        second_context = make_context()
        second_context.run_id = 'run_2'
        with self.assertRaisesRegex(RuntimeError, '授权'):
            client.generate(second_context, 'prompt')
        client.revoke_chargeable_requests('run_1')
        with self.assertRaisesRegex(RuntimeError, '授权'):
            client.generate(make_context(), 'prompt')

    def test_consent_is_bound_to_one_validated_run_and_only_one_run_can_be_authorized(self):
        client = DeepSeekApiClient(key_resolver=lambda: API_KEY)
        with self.assertRaises(ValueError):
            client.authorize_chargeable_requests('../run')
        client.authorize_chargeable_requests('run_1')
        with self.assertRaisesRegex(RuntimeError, '授权'):
            client.authorize_chargeable_requests('run_2')
        with self.assertRaisesRegex(ValueError, '授权'):
            client.revoke_chargeable_requests('run_2')
        client.revoke_chargeable_requests('run_1')
        client.authorize_chargeable_requests('run_2')

    def test_run_authorization_snapshots_model_preference_until_consent_is_revoked(self):
        selected = [DeepSeekModelPreference('deepseek-flash', 'high')]
        connections = []

        def factory(*args):
            connection = FakeConnection()
            connections.append(connection)
            return connection

        client = DeepSeekApiClient(
            key_resolver=lambda: API_KEY,
            transport_factory=factory,
            preference_resolver=lambda: selected[0],
        )
        client.authorize_chargeable_requests('run_1')
        self.assertEqual(selected[0], client.authorized_model_preference('run_1'))
        self.assertIsNone(client.authorized_model_preference('run_2'))
        selected[0] = DeepSeekModelPreference('deepseek-v4-pro', 'max')

        client.generate(make_context(), 'prompt')
        sent = json.loads(connections[0].request_args[2].decode('utf-8'))
        self.assertEqual(sent['model'], 'deepseek-flash')
        self.assertEqual(sent['reasoning_effort'], 'high')

        client.revoke_chargeable_requests('run_1')
        self.assertIsNone(client.authorized_model_preference('run_1'))
        client.authorize_chargeable_requests('run_2')
        context = make_context()
        context.run_id = 'run_2'
        client.generate(context, 'prompt')
        sent = json.loads(connections[1].request_args[2].decode('utf-8'))
        self.assertEqual(sent['model'], 'deepseek-v4-pro')
        self.assertEqual(sent['reasoning_effort'], 'max')

    def test_probe_key_resolver_failure_is_generic(self):
        client = DeepSeekApiClient(key_resolver=lambda: (_ for _ in ()).throw(RuntimeError(API_KEY)),
                                   transport_factory=lambda *args: self.fail('network forbidden'))

        status = client.probe()

        self.assertFalse(status.available)
        self.assertNotIn(API_KEY, repr(status))
        self.assertNotIn(API_KEY, status.message)


class DeepSeekRequestTests(unittest.TestCase):
    def make_client(self, connection):
        seen = []

        def factory(host, port, timeout, context):
            seen.append((host, port, timeout, context))
            return connection

        client = DeepSeekApiClient(key_resolver=lambda: API_KEY, transport_factory=factory)
        client.authorize_chargeable_requests('run_1')
        return client, seen

    def test_model_catalog_uses_authenticated_bounded_get_without_prompt_data(self):
        catalog = json.dumps({
            'object': 'list',
            'data': [
                {'id': 'deepseek-flash', 'object': 'model', 'owned_by': 'deepseek',
                 'name': 'DeepSeek Flash', 'input_modalities': ['text', 'image'],
                 'output_modalities': ['text'],
                 'effort': {'supported_levels': ['low', 'high', 'max'], 'default_level': 'high'}},
                {'id': 'deepseek-v4-pro', 'object': 'model', 'owned_by': 'deepseek',
                 'name': 'DeepSeek Pro', 'input_modalities': ['text'],
                 'output_modalities': ['text'],
                 'effort': {'supported_levels': ['low', 'high', 'max'], 'default_level': 'high'}},
                {'id': 'image-only', 'object': 'model', 'owned_by': 'deepseek',
                 'name': 'Image only', 'input_modalities': ['image'], 'output_modalities': ['text']},
            ],
        })
        connection = FakeConnection(FakeResponse(catalog))
        seen = []
        client = DeepSeekApiClient(key_resolver=lambda: API_KEY,
                                   transport_factory=lambda *args: seen.append(args) or connection)

        models = client.list_models()

        self.assertEqual([model.id for model in models], ['deepseek-flash', 'deepseek-v4-pro'])
        self.assertEqual(models[0].display_name, 'DeepSeek Flash')
        self.assertEqual(models[0].supported_efforts, ('low', 'high', 'max'))
        self.assertEqual(deepseek_api._REQUEST_TIMEOUT_SECONDS, 45.0)
        self.assertEqual(seen[0][2], deepseek_api._REQUEST_TIMEOUT_SECONDS)
        method, path, body, headers = connection.request_args
        self.assertEqual((method, path), ('GET', '/models'))
        self.assertIsNone(body)
        self.assertEqual(headers['Authorization'], 'Bearer ' + API_KEY)
        self.assertNotIn('题目', repr(connection.request_args))
        self.assertTrue(connection.closed)

    def test_model_catalog_fails_closed_without_key_or_for_unsafe_catalog(self):
        transport = unittest.mock.Mock(side_effect=AssertionError('no key must not connect'))
        client = DeepSeekApiClient(key_resolver=lambda: None, transport_factory=transport)
        with self.assertRaisesRegex(ValueError, 'DEEPSEEK_API_KEY'):
            client.list_models()
        transport.assert_not_called()

        malformed = FakeConnection(FakeResponse('{"object":"list","data":[],"data":[]}'))
        client = DeepSeekApiClient(key_resolver=lambda: API_KEY,
                                   transport_factory=lambda *args: malformed)
        with self.assertRaises(ValueError):
            client.list_models()
        self.assertTrue(malformed.closed)

    def test_fixed_https_request_has_bounded_context_and_no_tools_or_redirect_following(self):
        connection = FakeConnection()
        client, seen = self.make_client(connection)

        result = client.generate(make_context(), 'untrusted prompt')

        self.assertEqual(result, '{"ok": true}')
        self.assertEqual(len(seen), 1)
        host, port, timeout, tls_context = seen[0]
        self.assertEqual((host, port), ('api.deepseek.com', 443))
        self.assertGreater(timeout, 0)
        self.assertEqual(tls_context.verify_mode, 2)  # ssl.CERT_REQUIRED
        self.assertTrue(tls_context.check_hostname)
        method, path, raw_body, headers = connection.request_args
        self.assertEqual((method, path), ('POST', '/chat/completions'))
        self.assertEqual(ENDPOINT, 'https://api.deepseek.com/chat/completions')
        payload = json.loads(raw_body.decode('utf-8'))
        self.assertEqual(payload['model'], MODEL)
        self.assertEqual(payload['reasoning_effort'], 'high')
        self.assertTrue(payload['stream'])
        self.assertNotIn('tools', payload)
        self.assertNotIn('tool_choice', payload)
        self.assertEqual(headers['Authorization'], 'Bearer ' + API_KEY)
        self.assertEqual(headers['Content-Type'], 'application/json')
        self.assertEqual(headers['Accept'], 'text/event-stream')
        self.assertTrue(connection.closed)

    def test_generation_streams_final_content_but_discards_reasoning_content(self):
        response_body = server_sent_events(
            {'choices': [{'index': 0, 'delta': {'role': 'assistant',
                                                   'reasoning_content': 'private reasoning'}}]},
            {'choices': [{'index': 0, 'delta': {'content': '{"ok": '}}]},
            {'choices': [{'index': 0, 'delta': {'content': 'true}'},
                         'finish_reason': 'stop'}]},
            '[DONE]',
        )
        connection = FakeConnection(FakeResponse(
            response_body, content_type='text/event-stream', read_chunk_size=11,
        ))
        client, _ = self.make_client(connection)

        result = client.generate(make_context(), 'prompt')

        self.assertEqual(result, '{"ok": true}')
        self.assertNotIn('private reasoning', result)
        self.assertTrue(connection.closed)

    def test_cancel_event_interrupts_stream_read_and_discards_partial_content(self):
        connection = BlockingStreamConnection()
        client, _ = self.make_client(connection)
        cancelled = threading.Event()
        errors = []

        def invoke():
            try:
                client.generate(make_context(), 'prompt', cancelled)
            except Exception as exc:
                errors.append(exc)

        worker = threading.Thread(target=invoke)
        worker.start()
        self.assertTrue(connection.stream_response.read_entered.wait(2))
        cancelled.set()
        worker.join(2)

        self.assertFalse(worker.is_alive())
        self.assertTrue(connection.closed)
        self.assertEqual(len(errors), 1)
        self.assertIn('取消', str(errors[0]))
        self.assertNotIn('partial', str(errors[0]))

    def test_streaming_rejects_content_truncated_by_output_limit(self):
        response_body = server_sent_events(
            {'choices': [{'index': 0, 'delta': {'content': '{"partial":'}}]},
            {'choices': [{'index': 0, 'delta': {}, 'finish_reason': 'length'}]},
            '[DONE]',
        )

        with self.assertRaises(ValueError) as raised:
            DeepSeekApiClient._extract_stream_content(
                FakeResponse(response_body, content_type='text/event-stream'))

        self.assertEqual(raised.exception.diagnostic_code, 'output_limit')

    def test_json_response_rejects_content_truncated_by_output_limit(self):
        response_body = json.dumps({
            'choices': [{'message': {'role': 'assistant', 'content': '{"partial":'},
                         'finish_reason': 'length'}],
        })

        with self.assertRaises(ValueError) as raised:
            DeepSeekApiClient._extract_content(response_body.encode('utf-8'))

        self.assertEqual(raised.exception.diagnostic_code, 'output_limit')

    def test_generation_retries_once_with_expanded_output_limit_after_length(self):
        truncated = server_sent_events(
            {'choices': [{'index': 0, 'delta': {'content': '{"partial":'}}]},
            {'choices': [{'index': 0, 'delta': {}, 'finish_reason': 'length'}]},
            '[DONE]',
        )
        connections = [
            FakeConnection(FakeResponse(truncated, content_type='text/event-stream')),
            FakeConnection(FakeResponse(chat_response('{"ok": true}'))),
        ]
        requests = []

        def transport(*args):
            connection = connections.pop(0)
            requests.append(connection)
            return connection

        client = DeepSeekApiClient(
            key_resolver=lambda: API_KEY,
            transport_factory=transport,
            preference_resolver=lambda: DeepSeekModelPreference(
                MODEL, 'none', deepseek_api.DEFAULT_DEEPSEEK_MAX_OUTPUT_TOKENS * 2),
        )
        client.authorize_chargeable_requests('run_1')

        result = client.generate(make_context(), 'prompt')

        self.assertEqual(result, '{"ok": true}')
        self.assertEqual([
            json.loads(connection.request_args[2].decode('utf-8'))['max_tokens']
            for connection in requests
        ], [deepseek_api.DEFAULT_DEEPSEEK_MAX_OUTPUT_TOKENS,
            deepseek_api.DEFAULT_DEEPSEEK_MAX_OUTPUT_TOKENS * 2])

    def test_generation_retry_stays_within_selected_model_output_ceiling(self):
        truncated = server_sent_events(
            {'choices': [{'index': 0, 'delta': {'content': '{"partial":'}}]},
            {'choices': [{'index': 0, 'delta': {}, 'finish_reason': 'length'}]},
            '[DONE]',
        )
        connections = [
            FakeConnection(FakeResponse(truncated, content_type='text/event-stream')),
            FakeConnection(FakeResponse(chat_response('{"ok": true}'))),
        ]
        requests = []

        def transport(*args):
            connection = connections.pop(0)
            requests.append(connection)
            return connection

        model_ceiling = 12_288
        client = DeepSeekApiClient(
            key_resolver=lambda: API_KEY,
            transport_factory=transport,
            preference_resolver=lambda: DeepSeekModelPreference(
                MODEL, 'none', model_ceiling),
        )
        client.authorize_chargeable_requests('run_1')

        self.assertEqual(client.generate(make_context(), 'prompt'), '{"ok": true}')
        limits = [
            json.loads(connection.request_args[2].decode('utf-8'))['max_tokens']
            for connection in requests
        ]
        self.assertEqual(limits, [deepseek_api.DEFAULT_DEEPSEEK_MAX_OUTPUT_TOKENS,
                                  model_ceiling])
        self.assertLessEqual(max(limits), model_ceiling)

    def test_generation_does_not_retry_when_model_ceiling_is_initial_limit(self):
        truncated = server_sent_events(
            {'choices': [{'index': 0, 'delta': {'content': '{"partial":'}}]},
            {'choices': [{'index': 0, 'delta': {}, 'finish_reason': 'length'}]},
            '[DONE]',
        )
        connection = FakeConnection(FakeResponse(truncated, content_type='text/event-stream'))
        transport = unittest.mock.Mock(return_value=connection)
        model_ceiling = deepseek_api.DEFAULT_DEEPSEEK_MAX_OUTPUT_TOKENS
        client = DeepSeekApiClient(
            key_resolver=lambda: API_KEY,
            transport_factory=transport,
            preference_resolver=lambda: DeepSeekModelPreference(
                MODEL, 'max', model_ceiling),
        )
        client.authorize_chargeable_requests('run_1')

        with self.assertRaises(ValueError) as raised:
            client.generate(make_context(), 'prompt')

        self.assertEqual(raised.exception.diagnostic_code, 'output_limit')
        body = json.loads(connection.request_args[2].decode('utf-8'))
        self.assertEqual(body['max_tokens'], model_ceiling)
        transport.assert_called_once()

    def test_generation_does_not_retry_other_provider_finish_reasons(self):
        response_body = server_sent_events(
            {'choices': [{'index': 0, 'delta': {'content': 'partial'}}]},
            {'choices': [{'index': 0, 'delta': {}, 'finish_reason': 'content_filter'}]},
            '[DONE]',
        )
        connection = FakeConnection(FakeResponse(
            response_body, content_type='text/event-stream'))
        client, _ = self.make_client(connection)

        with self.assertRaises(ValueError) as raised:
            client.generate(make_context(), 'prompt')

        self.assertEqual(raised.exception.diagnostic_code, 'content_filtered')
        self.assertTrue(connection.closed)

    def test_api_key_in_serialized_model_field_is_blocked_before_transport(self):
        connection = FakeConnection()
        transport = unittest.mock.Mock(return_value=connection)
        client = DeepSeekApiClient(
            key_resolver=lambda: API_KEY,
            transport_factory=transport,
            preference_resolver=lambda: DeepSeekModelPreference(API_KEY, 'high'),
        )
        client.authorize_chargeable_requests('run_1')

        with self.assertRaises(ValueError) as raised:
            client.generate(make_context(), 'prompt')

        self.assertNotIn(API_KEY, str(raised.exception))
        self.assertIsNone(connection.request_args)
        transport.assert_not_called()

    def test_generation_uses_its_dedicated_longer_deadline(self):
        connection = FakeConnection()
        client, seen = self.make_client(connection)

        self.assertGreaterEqual(deepseek_api._GENERATION_TIMEOUT_SECONDS, 300)
        self.assertGreater(deepseek_api._GENERATION_TIMEOUT_SECONDS,
                           deepseek_api._REQUEST_TIMEOUT_SECONDS)
        client.generate(make_context(), 'prompt')

        self.assertEqual(seen[0][2], deepseek_api._GENERATION_TIMEOUT_SECONDS)

    def test_default_https_connection_receives_generation_timeout_and_deadline(self):
        client = DeepSeekApiClient(key_resolver=lambda: API_KEY)
        deadline = time.monotonic() + deepseek_api._GENERATION_TIMEOUT_SECONDS

        connection = client._new_connection(
            ssl.create_default_context(), _AbortSignal(), deadline, 'run_1',
            timeout_seconds=deepseek_api._GENERATION_TIMEOUT_SECONDS,
        )
        try:
            self.assertIsInstance(connection, _CancellableHTTPSConnection)
            self.assertEqual(connection.timeout, deepseek_api._GENERATION_TIMEOUT_SECONDS)
            self.assertEqual(connection._deadline, deadline)
        finally:
            connection.close()

    def test_redirect_is_rejected_without_following_location(self):
        connection = FakeConnection(FakeResponse('', status=302))
        client, _ = self.make_client(connection)

        with self.assertRaisesRegex(ValueError, '未完成') as raised:
            client.generate(make_context(), 'prompt')

        self.assertNotIn(API_KEY, str(raised.exception))
        self.assertTrue(connection.closed)

    def test_non_success_status_and_transport_exception_are_sanitized(self):
        failures = ((FakeConnection(FakeResponse(API_KEY, status=401)), 'http_401'),
                    (_ExplodingConnection(), 'request_failed'))
        for connection, expected_code in failures:
            client, _ = self.make_client(connection)
            with self.subTest(connection=type(connection).__name__), self.assertRaises(ValueError) as raised:
                client.generate(make_context(), 'prompt')
            self.assertNotIn(API_KEY, str(raised.exception))
            self.assertNotIn(API_KEY, repr(client))
            self.assertEqual(raised.exception.diagnostic_code, expected_code)

    def test_duplicate_keys_nonfinite_values_and_invalid_response_shapes_are_rejected(self):
        bodies = [
            '{"choices":[],"choices":[]}',
            '{"choices":[{"message":{"content":"x"}}],"extra":NaN}',
            json.dumps({'choices': [{'message': {'content': '', 'tool_calls': []}}]}),
            json.dumps({'choices': [{'message': {'content': 'x'}, 'finish_reason': 'tool_calls'}]}),
            json.dumps({'choices': [{'message': {'role': 'assistant', 'content': 'x'}}],
                               'metadata': {'tool_calls': []}}),
            json.dumps({'choices': [{'message': {'role': 'user', 'content': 'x'}}]}),
            '[]',
        ]
        for body in bodies:
            connection = FakeConnection(FakeResponse(body))
            client, _ = self.make_client(connection)
            with self.subTest(body=body[:60]), self.assertRaises(ValueError) as raised:
                client.generate(make_context(), 'prompt')
            self.assertNotIn(API_KEY, str(raised.exception))

    def test_request_and_response_limits_are_enforced(self):
        connection = FakeConnection()
        client, _ = self.make_client(connection)
        with self.assertRaises(ValueError):
            client.generate(make_context(), 'x' * 1_000_000)
        self.assertIsNone(connection.request_args)

        oversized = FakeConnection(FakeResponse('x' * (2 * 1024 * 1024 + 1)))
        client, _ = self.make_client(oversized)
        with self.assertRaises(ValueError):
            client.generate(make_context(), 'prompt')

    def test_invalid_header_key_is_rejected_without_echoing_value(self):
        connection = FakeConnection()
        client = DeepSeekApiClient(key_resolver=lambda: API_KEY + '\r\nX-Injected: yes',
                                   transport_factory=lambda *args: connection)
        client.authorize_chargeable_requests('run_1')

        with self.assertRaises(ValueError) as raised:
            client.generate(make_context(), 'prompt')

        self.assertNotIn(API_KEY, str(raised.exception))
        self.assertIsNone(connection.request_args)

    def test_api_key_echoed_by_server_response_is_not_returned(self):
        connection = FakeConnection(FakeResponse(chat_response(API_KEY)))
        client, _ = self.make_client(connection)

        with self.assertRaisesRegex(ValueError, '凭据') as raised:
            client.generate(make_context(), 'prompt')

        self.assertNotIn(API_KEY, str(raised.exception))

    def test_key_accidentally_present_in_prompt_is_never_sent_in_body(self):
        connection = FakeConnection()
        client, _ = self.make_client(connection)

        with self.assertRaisesRegex(ValueError, '凭据') as raised:
            client.generate(make_context(), 'user prompt contains ' + API_KEY)

        self.assertNotIn(API_KEY, str(raised.exception))
        self.assertIsNone(connection.request_args)

    def test_prepare_scope_reports_categories_and_selected_names_without_contents(self):
        client = DeepSeekApiClient(key_resolver=lambda: None)

        scope = client.prepare_scope(make_context(), 'secret prompt body')

        self.assertEqual(scope.provider, 'DeepSeek')
        self.assertEqual(scope.endpoint, ENDPOINT)
        self.assertEqual(scope.model, MODEL)
        self.assertIn('推理强度：high', scope.notice)
        self.assertIn('00_inputs/data.csv', scope.selected_inputs)
        self.assertIn('题目', ' '.join(scope.data_categories))
        self.assertFalse(scope.tools_enabled)
        self.assertTrue(scope.billable)
        self.assertNotIn('secret prompt body', repr(scope))


    def test_deepseek_progress_drops_cancelled_or_invalid_responses(self):
        cancelled = threading.Event()
        cancelled.set()
        client, _ = self.make_client(FakeConnection())
        cancelled_events = []
        with self.assertRaises(RuntimeError):
            client.generate_with_progress(
                make_context(), 'prompt', cancel_event=cancelled,
                event_callback=cancelled_events.append,
            )
        self.assertFalse(any(event.kind in (EventKind.TEXT, EventKind.COMPLETED)
                             for event in cancelled_events))
        self.assertEqual(EventKind.CANCELLED, cancelled_events[-1].kind)

        invalid = FakeConnection(FakeResponse('{"choices":[]}'))
        client, _ = self.make_client(invalid)
        invalid_events = []
        with self.assertRaises(ValueError):
            client.generate_with_progress(
                make_context(), 'prompt', event_callback=invalid_events.append,
            )
        self.assertTrue(any(event.kind is EventKind.STARTED for event in invalid_events))
        self.assertFalse(any(event.kind in (EventKind.TEXT, EventKind.COMPLETED)
                             for event in invalid_events))
        self.assertEqual(EventKind.FAILED, invalid_events[-1].kind)
        self.assertNotIn(API_KEY, repr(invalid_events))

    def test_deepseek_progress_redacts_prompt_echo_and_waits_only_after_send(self):
        connection = FakeConnection(FakeResponse(chat_response('prompt')))
        client, _ = self.make_client(connection)
        events = []
        sent_at_wait = []

        def sink(event):
            if event.kind is EventKind.STARTED:
                sent_at_wait.append(connection.request_args is not None)
            events.append(event)

        client.generate_with_progress(make_context(), 'prompt', event_callback=sink)

        visible = ''.join(event.text for event in events if event.kind is EventKind.TEXT)
        self.assertNotEqual('prompt', visible)
        self.assertIn('[已隐藏]', visible)
        self.assertEqual([True], sent_at_wait)
        self.assertEqual(EventKind.COMPLETED, events[-1].kind)

class DeepSeekCancellationTests(unittest.TestCase):
    def test_revoke_during_tls_handshake_closes_published_tls_socket(self):
        handshake_started = threading.Event()
        tls_closed = threading.Event()

        class FakeRawSocket:
            def settimeout(self, _value):
                return None

            def connect(self, _address):
                return None

            def close(self):
                return None

        class FakeTLSSocket:
            def settimeout(self, _value):
                return None

            def do_handshake(self):
                handshake_started.set()
                if not tls_closed.wait(2):
                    raise TimeoutError('handshake did not close')
                raise OSError('socket closed')

            def shutdown(self, _how):
                tls_closed.set()

            def close(self):
                tls_closed.set()

        class FakeTLSContext:
            def wrap_socket(self, raw_socket, *, server_hostname, do_handshake_on_connect):
                self.raw_socket = raw_socket
                self.server_hostname = server_hostname
                self.do_handshake_on_connect = do_handshake_on_connect
                return tls_socket

        tls_socket = FakeTLSSocket()
        context = FakeTLSContext()
        abort = _AbortSignal()
        connection = _CancellableHTTPSConnection(
            'api.deepseek.com', 443, timeout=2, context=context,
            abort_event=abort, deadline=time.monotonic() + 2,
            is_authorized=lambda: True, state_lock=threading.RLock(),
        )
        result = []

        def connect():
            try:
                connection.connect()
            except Exception as exc:
                result.append(exc)

        with patch(
                'mathmodel_paper_studio.agents.providers.deepseek_api._resolve_addresses',
                return_value=[(socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP,
                               '', ('127.0.0.1', 443))]), \
                patch('mathmodel_paper_studio.agents.providers.deepseek_api.socket.socket',
                      return_value=FakeRawSocket()):
            thread = threading.Thread(target=connect)
            thread.start()
            try:
                self.assertTrue(handshake_started.wait(1))
                self.assertIs(connection.sock, tls_socket)
                self.assertFalse(context.do_handshake_on_connect)
                abort.set()
                DeepSeekApiClient._close_connection(connection)
                thread.join(0.5)
                self.assertFalse(thread.is_alive(), 'revocation must interrupt TLS handshake')
                self.assertTrue(tls_closed.is_set())
                self.assertEqual(len(result), 1)
            finally:
                tls_closed.set()
                thread.join(2)
        self.assertFalse(thread.is_alive())

    def test_revoke_is_not_blocked_by_an_in_progress_socket_send(self):
        send_started = threading.Event()
        send_closed = threading.Event()
        client = DeepSeekApiClient(key_resolver=lambda: API_KEY)
        client.authorize_chargeable_requests('run_1')

        class BlockingSocket:
            def settimeout(self, _value):
                return None

            def sendall(self, _data):
                send_started.set()
                if not send_closed.wait(2):
                    raise TimeoutError('send did not close')
                raise OSError('socket closed')

            def shutdown(self, _how):
                send_closed.set()

            def close(self):
                send_closed.set()

        abort = _AbortSignal()
        connection = _CancellableHTTPSConnection(
            'api.deepseek.com', 443, timeout=2, context=None,
            abort_event=abort, deadline=time.monotonic() + 2,
            is_authorized=lambda: client._is_authorized('run_1'), state_lock=client._lock,
        )
        connection.sock = BlockingSocket()
        with client._lock:
            client._active_run_id = 'run_1'
            client._active_abort_event = abort
            client._active_connection = connection
        send_result = []

        def send():
            try:
                connection.send(b'payload')
            except Exception as exc:
                send_result.append(exc)

        sender = threading.Thread(target=send)
        sender.start()
        self.assertTrue(send_started.wait(1))
        revoker = threading.Thread(target=client.revoke_chargeable_requests, args=('run_1',))
        revoker.start()
        try:
            revoker.join(0.5)
            self.assertFalse(revoker.is_alive(), 'revocation must not wait for blocking sendall')
            sender.join(0.5)
            self.assertFalse(sender.is_alive(), 'revocation must close the blocking socket send')
            self.assertTrue(send_closed.is_set())
            self.assertEqual(len(send_result), 1)
        finally:
            send_closed.set()
            sender.join(2)
            revoker.join(2)
        self.assertFalse(sender.is_alive())
        self.assertFalse(revoker.is_alive())

    def test_revoke_during_dns_resolution_aborts_before_tcp_socket_creation(self):
        dns_started = threading.Event()
        release_dns = threading.Event()
        result = []
        client = DeepSeekApiClient(key_resolver=lambda: API_KEY)
        client.authorize_chargeable_requests('run_1')

        def blocked_resolver(*_args, **_kwargs):
            dns_started.set()
            release_dns.wait(2)
            return [(socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP,
                     '', ('127.0.0.1', 443))]

        def invoke():
            try:
                client.generate(make_context(), 'prompt')
            except Exception as exc:
                result.append(exc)

        with patch('socket.getaddrinfo', side_effect=blocked_resolver), \
                patch('socket.socket') as socket_factory:
            thread = threading.Thread(target=invoke)
            thread.start()
            try:
                self.assertTrue(dns_started.wait(1))
                client.revoke_chargeable_requests('run_1')
                thread.join(0.5)
                self.assertFalse(thread.is_alive(), 'revocation must interrupt DNS wait promptly')
                self.assertFalse(socket_factory.called, 'revoked DNS result must never open a TCP socket')
                self.assertEqual(len(result), 1)
                self.assertNotIn(API_KEY, str(result[0]))
            finally:
                release_dns.set()
                thread.join(2)
        self.assertFalse(thread.is_alive())

    def test_cancel_event_closes_active_connection_and_discards_result(self):
        connection = FakeConnection(block_response=True)
        client = DeepSeekApiClient(key_resolver=lambda: API_KEY,
                                   transport_factory=lambda *args: connection)
        client.authorize_chargeable_requests('run_1')
        cancelled = threading.Event()
        result = []

        def invoke():
            try:
                client.generate(make_context(), 'prompt', cancel_event=cancelled)
            except Exception as exc:
                result.append(exc)

        thread = threading.Thread(target=invoke)
        thread.start()
        self.assertTrue(connection.response_entered.wait(1))
        cancelled.set()
        thread.join(2)

        self.assertFalse(thread.is_alive())
        self.assertTrue(connection.closed)
        self.assertEqual(len(result), 1)
        self.assertNotIn(API_KEY, str(result[0]))
        self.assertIn('取消', str(result[0]))

    def test_only_one_api_call_can_be_active_for_a_run(self):
        connection = FakeConnection(block_response=True)
        client = DeepSeekApiClient(key_resolver=lambda: API_KEY,
                                   transport_factory=lambda *args: connection)
        client.authorize_chargeable_requests('run_1')
        result = []

        def invoke():
            try:
                client.generate(make_context(), 'prompt')
            except Exception as exc:
                result.append(exc)

        thread = threading.Thread(target=invoke)
        thread.start()
        self.assertTrue(connection.response_entered.wait(1))
        with self.assertRaisesRegex(RuntimeError, '正在运行'):
            client.generate(make_context(), 'second prompt')
        client.revoke_chargeable_requests('run_1')
        thread.join(2)

        self.assertFalse(thread.is_alive())
        self.assertTrue(connection.closed)
        self.assertEqual(len(result), 1)
        self.assertNotIn(API_KEY, str(result[0]))

    def test_wall_timeout_closes_connection_and_drops_partial_result(self):
        connection = FakeConnection(block_response=True)
        client = DeepSeekApiClient(key_resolver=lambda: API_KEY,
                                   transport_factory=lambda *args: connection)
        client.authorize_chargeable_requests('run_1')
        result = []

        def invoke():
            try:
                with patch('mathmodel_paper_studio.agents.providers.deepseek_api._GENERATION_TIMEOUT_SECONDS', 0.05):
                    client.generate(make_context(), 'prompt')
            except Exception as exc:
                result.append(exc)

        thread = threading.Thread(target=invoke)
        thread.start()
        thread.join(2)

        self.assertFalse(thread.is_alive())
        self.assertTrue(connection.closed)
        self.assertEqual(len(result), 1)
        self.assertIn('超时', str(result[0]))
        self.assertNotIn(API_KEY, str(result[0]))


class _ExplodingConnection(FakeConnection):
    def request(self, method, path, *, body, headers):
        raise RuntimeError('private endpoint detail ' + API_KEY)


if __name__ == '__main__':
    unittest.main()


