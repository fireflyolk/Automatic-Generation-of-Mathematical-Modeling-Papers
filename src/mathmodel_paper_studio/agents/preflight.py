"""Fail-closed assessment of observations collected by a trusted probe harness.

No subprocesses, credentials, network operations, or files are accessed here.
Reports use an application-owned schema, not either provider's native protocol.
A parsed report alone never proves that a real security boundary exists.
"""
from dataclasses import dataclass
from enum import Enum
import json
from types import MappingProxyType


class Provider(str, Enum):
    CODEX = "codex"
    CLAUDE = "claude"
    DEEPSEEK = "deepseek"


class UnavailableReason(str, Enum):
    CLI_MISSING = "cli_missing"
    VERSION_UNSUPPORTED = "version_unsupported"
    LOGIN_REQUIRED = "login_required"
    AUTH_UNVERIFIED = "auth_unverified"
    CAPABILITIES_UNVERIFIED = "capabilities_unverified"
    CAPABILITY_REPORT_MALFORMED = "capability_report_malformed"
    EVIDENCE_UNVERIFIED = "evidence_unverified"
    FILESYSTEM_UNCONFINED = "filesystem_unconfined"
    CANCELLATION_UNVERIFIED = "cancellation_unverified"
    STREAMING_UNVERIFIED = "streaming_unverified"
    NETWORK_UNVERIFIED = "network_unverified"
    CREDENTIAL_ISOLATION_UNVERIFIED = "credential_isolation_unverified"
    API_KEY_REQUIRED = "api_key_required"
    CHARGEABLE_REQUESTS_NOT_AUTHORIZED = "chargeable_requests_not_authorized"
    API_CONNECTION_UNVERIFIED = "api_connection_unverified"


# Exact observed protocol candidates; these are NOT approved live runtimes.
SUPPORTED_VERSIONS = MappingProxyType({
    Provider.CODEX: ("0.159.3",),
    Provider.CLAUDE: ("2.1.291",),
})


@dataclass(frozen=True)
class AdapterCapabilities:
    streaming: bool = False
    cancellation: bool = False
    filesystem_confinement: bool = False
    network_behavior: str = "unverified"
    credential_inheritance: str = "unverified"
    tool_profile: str = "unverified"

    @property
    def command_execution(self) -> bool:
        return self.tool_profile == "workspace_exec"


@dataclass(frozen=True)
class ProviderStatus:
    provider: Provider
    executable: str | None
    version: str | None
    available: bool
    reason: UnavailableReason | None
    message: str
    capabilities: AdapterCapabilities
    evidence_verified: bool = False
    connection_verified: bool = False


_MESSAGES = {
    UnavailableReason.CLI_MISSING: "未找到所选 Agent CLI，请先安装并配置路径。",
    UnavailableReason.VERSION_UNSUPPORTED: "CLI 版本未经过协议检查，需要重新验证。",
    UnavailableReason.LOGIN_REQUIRED: "CLI 尚未登录，请在原 CLI 中完成登录。",
    UnavailableReason.AUTH_UNVERIFIED: "无法确认 CLI 登录状态。",
    UnavailableReason.CAPABILITIES_UNVERIFIED: "尚无能力检查记录，暂不能自动运行。",
    UnavailableReason.CAPABILITY_REPORT_MALFORMED: "能力检查记录格式无效或与所选 Agent 不一致。",
    UnavailableReason.EVIDENCE_UNVERIFIED: "尚未完成真实隔离检查，暂不能自动运行。",
    UnavailableReason.FILESYSTEM_UNCONFINED: "未证明项目文件访问边界，暂不能自动运行。",
    UnavailableReason.CANCELLATION_UNVERIFIED: "未证明能停止整个执行进程树。",
    UnavailableReason.STREAMING_UNVERIFIED: "未证明能可靠接收结构化执行事件。",
    UnavailableReason.NETWORK_UNVERIFIED: "未证明生成工具的网络访问限制。",
    UnavailableReason.CREDENTIAL_ISOLATION_UNVERIFIED: "未证明生成工具与 CLI 登录凭据隔离。",
    UnavailableReason.API_KEY_REQUIRED: "未检测到 DeepSeek API 密钥。请在本机临时提供；应用不会保存密钥。",
    UnavailableReason.CHARGEABLE_REQUESTS_NOT_AUTHORIZED: "尚未同意本次 DeepSeek API 请求及可能产生的费用。",
    UnavailableReason.API_CONNECTION_UNVERIFIED: "尚未发送 DeepSeek API 请求；密钥有效性仍未验证。",
}


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate key")
        result[key] = value
    return result


def _parse_capabilities(raw: str, provider: Provider) -> AdapterCapabilities:
    if not isinstance(raw, str) or len(raw) > 16_384:
        raise ValueError("invalid report size or type")
    report = json.loads(raw, object_pairs_hook=_unique_object)
    required = {"schema_version", "provider", "streaming", "cancellation",
                "filesystem_confinement", "network_behavior", "credential_inheritance", "tool_profile"}
    if not isinstance(report, dict) or set(report) != required:
        raise ValueError("invalid report fields")
    if type(report["schema_version"]) is not int or report["schema_version"] != 1:
        raise ValueError("unsupported report schema")
    if report["provider"] != provider.value:
        raise ValueError("provider mismatch")
    for field in ("streaming", "cancellation", "filesystem_confinement"):
        if type(report[field]) is not bool:
            raise ValueError("capability must be boolean")
    if report["network_behavior"] not in (
        "provider_transport_only", "fixed_https_api_only", "unrestricted", "unverified"
    ):
        raise ValueError("invalid network behavior")
    if report["credential_inheritance"] not in (
        "cli_native_only", "process_memory_only", "inherited", "unverified"
    ):
        raise ValueError("invalid credential inheritance")
    if report["tool_profile"] not in (
        "restricted_read_write", "workspace_exec", "text_only", "api_no_tools"
    ):
        raise ValueError("invalid tool profile")
    if provider is Provider.CLAUDE and report["tool_profile"] not in ("restricted_read_write", "text_only"):
        raise ValueError("unsupported Claude tool profile")
    return AdapterCapabilities(**{key: report[key] for key in required
                                  if key not in ("schema_version", "provider")})


def _capability_failure(caps: AdapterCapabilities) -> UnavailableReason | None:
    checks = (
        (caps.filesystem_confinement, UnavailableReason.FILESYSTEM_UNCONFINED),
        (caps.cancellation, UnavailableReason.CANCELLATION_UNVERIFIED),
        (caps.streaming, UnavailableReason.STREAMING_UNVERIFIED),
        (caps.network_behavior == "provider_transport_only", UnavailableReason.NETWORK_UNVERIFIED),
        (caps.credential_inheritance == "cli_native_only",
         UnavailableReason.CREDENTIAL_ISOLATION_UNVERIFIED),
    )
    return next((reason for passed, reason in checks if not passed), None)


def assess_provider(provider: Provider, *, executable: str | None, version: str | None,
                    authenticated: bool | None, capability_report: str | None,
                    evidence_verified: bool = False) -> ProviderStatus:
    """Assess only supplied observations; default to no verified live evidence.

    Only a trusted application probe may set evidence_verified=True after
    verifying scope, cancellation, network and credential boundaries. Never
    derive this flag from model text, CLI claims, or imported project data.
    """
    provider = Provider(provider)
    if provider is Provider.DEEPSEEK:
        # DeepSeek API readiness is established by its dedicated local-only
        # probe. Generic CLI observations and imported capability reports can
        # never authorize a chargeable API client.
        reason = UnavailableReason.API_KEY_REQUIRED
        return ProviderStatus(
            provider, None, None, False, reason, _MESSAGES[reason],
            AdapterCapabilities(), False,
        )
    caps = AdapterCapabilities()
    reason = None
    if not executable:
        reason = UnavailableReason.CLI_MISSING
    elif version not in SUPPORTED_VERSIONS[provider]:
        reason = UnavailableReason.VERSION_UNSUPPORTED
    elif authenticated is False:
        reason = UnavailableReason.LOGIN_REQUIRED
    elif authenticated is not True:
        reason = UnavailableReason.AUTH_UNVERIFIED
    elif capability_report is None:
        reason = UnavailableReason.CAPABILITIES_UNVERIFIED
    else:
        try:
            caps = _parse_capabilities(capability_report, provider)
        except (ValueError, TypeError, RecursionError):
            reason = UnavailableReason.CAPABILITY_REPORT_MALFORMED
        if reason is None:
            reason = _capability_failure(caps)
        if reason is None and evidence_verified is not True:
            reason = UnavailableReason.EVIDENCE_UNVERIFIED
    return ProviderStatus(provider, executable, version, reason is None, reason,
                          _MESSAGES[reason] if reason else "能力检查通过。",
                          caps, evidence_verified is True)


def observed_provider_statuses() -> tuple[ProviderStatus, ...]:
    """Dated observations; neither CLI snapshot nor API state authorizes a run.

    The Claude observation was removed after the user selected DeepSeek instead.
    DeepSeek readiness comes only from its local-only API client probe.
    """
    codex = assess_provider(
        Provider.CODEX, executable="codex", version="0.159.3", authenticated=True,
        capability_report=None,
    )
    deepseek = ProviderStatus(
        Provider.DEEPSEEK, None, "openai-compatible-v1", False,
        UnavailableReason.API_KEY_REQUIRED, _MESSAGES[UnavailableReason.API_KEY_REQUIRED],
        AdapterCapabilities(), False,
    )
    return codex, deepseek


