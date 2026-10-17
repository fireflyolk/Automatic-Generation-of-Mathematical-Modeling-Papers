"""Immutable provider-client routing for one selected workflow run."""
from collections.abc import Mapping
from types import MappingProxyType

from mathmodel_paper_studio.agents.preflight import Provider


class ProviderClientRegistry:
    """Resolve a client by the run's provider without cross-provider fallback."""

    def __init__(self, clients):
        if not isinstance(clients, Mapping) or not clients:
            raise TypeError('服务商客户端必须是非空映射。')
        normalized = {}
        for provider_value, client in clients.items():
            try:
                provider = Provider(provider_value)
            except (TypeError, ValueError):
                raise ValueError('服务商客户端映射包含未知服务商。') from None
            if provider not in (Provider.CODEX, Provider.DEEPSEEK):
                raise ValueError('当前应用仅接入 Codex 与 DeepSeek。')
            if provider in normalized or client is None:
                raise ValueError('服务商客户端重复或为空。')
            normalized[provider] = client
        self._clients = MappingProxyType(dict(normalized))

    @property
    def providers(self):
        return tuple(self._clients)

    def for_provider(self, provider):
        try:
            key = Provider(provider)
        except (TypeError, ValueError):
            raise ValueError('运行指定了未知服务商。') from None
        try:
            return self._clients[key]
        except KeyError:
            raise ValueError('所选服务商没有已配置的客户端；不会回退到其他服务商。') from None
