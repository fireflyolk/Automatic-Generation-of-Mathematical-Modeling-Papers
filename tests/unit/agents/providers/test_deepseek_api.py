import json
import socket
import threading
import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from mathmodel_paper_studio.agents.preflight import Provider, UnavailableReason
from mathmodel_paper_studio.agents.providers.deepseek_api import (
    DeepSeekApiClient,
    ENDPOINT,
    MODEL,
    _AbortSignal,
    _CancellableHTTPSConnection,
)


API_KEY = 'test-secret-that-must-never-escape'


class FakeResponse:
    def __init__(self, body, status=200):
        self.body = body if isinstance(body, bytes) else body.encode('utf-8')
        self.status = status
        self.closed = False

    def read(self, size=-1):
        if size < 0:
            return self.body
        return self.body[:size]

    def close(self):
        self.closed = True


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


def chat_response(content='{"ok": true}', *, extra_message=None):
    message = {'role': 'assistant', 'content': content}
    if extra_message:
        message.update(extra_message)
    return json.dumps({'id': 'chatcmpl-test', 'choices': [{'index': 0, 'message': message,
                                                            'finish_reason': 'stop'}]},
                      separators=(',', ':'))


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
        self.assertFalse(payload['stream'])
        self.assertNotIn('tools', payload)
        self.assertNotIn('tool_choice', payload)
        self.assertEqual(headers['Authorization'], 'Bearer ' + API_KEY)
        self.assertEqual(headers['Content-Type'], 'application/json')
        self.assertTrue(connection.closed)

    def test_redirect_is_rejected_without_following_location(self):
        connection = FakeConnection(FakeResponse('', status=302))
        client, _ = self.make_client(connection)

        with self.assertRaisesRegex(ValueError, '未完成') as raised:
            client.generate(make_context(), 'prompt')

        self.assertNotIn(API_KEY, str(raised.exception))
        self.assertTrue(connection.closed)

    def test_non_success_status_and_transport_exception_are_sanitized(self):
        for connection in (FakeConnection(FakeResponse(API_KEY, status=401)),
                           _ExplodingConnection()):
            client, _ = self.make_client(connection)
            with self.subTest(connection=type(connection).__name__), self.assertRaises(ValueError) as raised:
                client.generate(make_context(), 'prompt')
            self.assertNotIn(API_KEY, str(raised.exception))
            self.assertNotIn(API_KEY, repr(client))

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
        self.assertIn('00_inputs/data.csv', scope.selected_inputs)
        self.assertIn('题目', ' '.join(scope.data_categories))
        self.assertFalse(scope.tools_enabled)
        self.assertTrue(scope.billable)
        self.assertNotIn('secret prompt body', repr(scope))


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
                with patch('mathmodel_paper_studio.agents.providers.deepseek_api._REQUEST_TIMEOUT_SECONDS', 0.05):
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
