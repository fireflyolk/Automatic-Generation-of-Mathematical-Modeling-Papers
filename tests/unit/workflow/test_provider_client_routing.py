"""Provider routing is selected per run and keeps the provider clients isolated."""
import json
import unittest
from types import SimpleNamespace

from mathmodel_paper_studio.agents.preflight import AdapterCapabilities, Provider, ProviderStatus
from mathmodel_paper_studio.domain.models import Stage, StageStatus
from mathmodel_paper_studio.agents.providers.deepseek_api import _SafeClientError
from mathmodel_paper_studio.workflow.live_engine import LiveWorkflowEngine, _stage_failure_diagnostic
from mathmodel_paper_studio.workflow.provider_clients import ProviderClientRegistry


class ProviderClientRoutingTests(unittest.TestCase):
    def test_stage_diagnostics_expose_only_allowlisted_provider_failure_codes(self):
        safe_error = _SafeClientError('private response body', diagnostic_code='http_402')

        self.assertEqual(
            _stage_failure_diagnostic(safe_error),
            'DeepSeek API 拒绝请求（HTTP 402）；请检查账户额度和服务状态。',
        )
        self.assertNotIn('private response body', _stage_failure_diagnostic(safe_error))

        content_filter_error = _SafeClientError(
            'private response body', diagnostic_code='content_filtered')
        self.assertEqual(
            _stage_failure_diagnostic(content_filter_error),
            'DeepSeek 服务商因内容过滤未完成生成；部分结果已丢弃。',
        )

        unsafe_error = _SafeClientError('private response body', diagnostic_code='private response body')
        self.assertEqual(
            _stage_failure_diagnostic(unsafe_error),
            'DeepSeek API 请求未完成；服务商响应未保存。',
        )

    def test_registry_routes_codex_and_deepseek_independently(self):
        codex = object()
        deepseek = object()
        registry = ProviderClientRegistry({
            Provider.CODEX: codex,
            Provider.DEEPSEEK: deepseek,
        })

        self.assertIs(registry.for_provider(Provider.CODEX), codex)
        self.assertIs(registry.for_provider(Provider.DEEPSEEK), deepseek)

    def test_live_engine_resolves_client_for_the_run_provider(self):
        codex = object()
        deepseek = object()
        engine = LiveWorkflowEngine.__new__(LiveWorkflowEngine)
        engine.provider_client = ProviderClientRegistry({
            Provider.CODEX: codex,
            Provider.DEEPSEEK: deepseek,
        })

        self.assertIs(engine._client_for(Provider.CODEX), codex)
        self.assertIs(engine._client_for(Provider.DEEPSEEK), deepseek)

    def test_live_engine_sends_each_stage_request_to_the_selected_provider(self):
        class Client:
            def __init__(self, provider, response):
                self.provider = provider
                self.response = response
                self.calls = []
                self.status = ProviderStatus(
                    provider, 'fixture', 'test', True, None, 'fixture status',
                    AdapterCapabilities(streaming=True, cancellation=True,
                                        filesystem_confinement=True,
                                        network_behavior='provider_transport_only',
                                        credential_inheritance='process_memory_only',
                                        tool_profile='api_no_tools' if provider is Provider.DEEPSEEK else 'text_only'),
                    provider is Provider.CODEX,
                )

            def probe(self):
                return self.status

            def prepare_scope(self, context, prompt):
                return SimpleNamespace(provider='DeepSeek', stage=context.stage.value,
                                       billable=True, tools_enabled=False)

            def generate(self, context, prompt, cancel_event=None):
                self.calls.append((context.stage, prompt))
                return self.response

        codex = Client(Provider.CODEX, '{"subproblems":["Codex result"]}')
        deepseek = Client(Provider.DEEPSEEK, '{"subproblems":["DeepSeek result"]}')
        engine = LiveWorkflowEngine.__new__(LiveWorkflowEngine)
        engine.provider_client = ProviderClientRegistry({
            Provider.CODEX: codex,
            Provider.DEEPSEEK: deepseek,
        })
        engine.deterministic_seed = 0
        engine.provider_status = None
        engine.status_getter = None
        engine.computation_runner = SimpleNamespace(run=lambda *args, **kwargs: None)
        for provider, client, expected in (
                (Provider.CODEX, codex, 'Codex result'),
                (Provider.DEEPSEEK, deepseek, 'DeepSeek result')):
            preflight = SimpleNamespace(provider=provider, stage=Stage.PREFLIGHT, attempt=0)
            preflight_result = engine._preflight(preflight)
            self.assertEqual(StageStatus.COMPLETED, preflight_result.status)
            saved_status = json.loads(preflight_result.outputs[0].content.decode('utf-8'))
            self.assertEqual(provider.value, saved_status['provider'])
            context = SimpleNamespace(
                provider=provider, stage=Stage.PROBLEM_DECOMPOSITION,
                upstream_artifacts=(), authorized_problem='fixture problem',
                problem_evidence_id='problem-evidence', inputs=(),
                system_instructions='', trust_notice='', paper_standard_id=None,
                paper_standard_version=None, paper_standard_sha256=None,
            )
            self.assertEqual({'subproblems': [expected]},
                             engine._request(context, {'subproblems'}, {}))
            self.assertEqual(1, len(client.calls))
            self.assertEqual(Stage.PROBLEM_DECOMPOSITION, client.calls[0][0])

    def test_missing_provider_is_rejected_without_falling_back(self):
        codex = object()
        registry = ProviderClientRegistry({Provider.CODEX: codex})

        with self.assertRaises(ValueError):
            registry.for_provider(Provider.DEEPSEEK)

    def test_registry_rejects_unknown_provider_names(self):
        with self.assertRaises((TypeError, ValueError)):
            ProviderClientRegistry({"not-a-provider": object()})

    def test_registry_uses_an_immutable_copy_of_the_client_map(self):
        codex = object()
        source = {Provider.CODEX: codex}
        registry = ProviderClientRegistry(source)
        source.clear()

        self.assertIs(registry.for_provider(Provider.CODEX), codex)
        with self.assertRaises(TypeError):
            registry._clients[Provider.DEEPSEEK] = object()


if __name__ == "__main__":
    unittest.main()
