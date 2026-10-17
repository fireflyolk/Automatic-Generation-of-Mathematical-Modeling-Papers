"""Ephemeral authority issued only through an application-owned fresh probe.

A trusted callback must actually exercise exact selections, not merely cwd/root.
No provider parser, model text, persisted status or imported JSON is an authority.
This module deliberately contains no production probe or generated-code bypass.
"""
from dataclasses import dataclass, field
import math
import secrets
import threading
import time
from typing import Callable

from mathmodel_paper_studio.agents.preflight import ProviderStatus
from mathmodel_paper_studio.security.path_guard import executable_digest
from mathmodel_paper_studio.security.scope_manifest import RuntimeScope


class GateBlocked(RuntimeError):
    pass


@dataclass(frozen=True)
class ProbeResult:
    status: ProviderStatus
    exact_selections_enforced: bool
    outside_read_denied: bool
    outside_write_denied: bool
    selected_inputs_readonly: bool


@dataclass(frozen=True, eq=False)
class TrustedGrant:
    run_id: str
    _nonce: str = field(repr=False)
    _digest: str = field(repr=False)
    _expires: float = field(repr=False)


class ScopeGate:
    def __init__(self, *, trusted_probe: Callable[[RuntimeScope], ProbeResult] | None = None,
                 lifetime_seconds: float = 60):
        if isinstance(lifetime_seconds, bool) or not math.isfinite(lifetime_seconds) or not 0 < lifetime_seconds <= 60:
            raise ValueError('范围证明有效期必须在 0–60 秒之间。')
        self._probe = trusted_probe
        self._lifetime = lifetime_seconds
        self._grants = {}
        self._lock = threading.Lock()

    def authorize(self, scope: RuntimeScope, observation=None, *, purpose='provider_file_tools') -> TrustedGrant:
        if purpose not in ('provider_file_tools', 'provider_text_only'):
            raise GateBlocked('未证明本地 Python 的文件访问边界和网络隔离，暂不能执行生成代码。')
        if observation is not None or self._probe is None:
            raise GateBlocked('缺少本次选定范围的可信实时检查，历史状态和导入数据不能授权。')
        if (purpose == 'provider_text_only') != (scope.tool_profile == 'text_only'):
            raise GateBlocked('运行用途与工具配置不一致。')
        self._validate_scope(scope)
        digest = scope.digest
        started = time.monotonic()
        result = self._probe(scope)
        self._validate_result(scope, result)
        self._validate_scope(scope)
        if scope.digest != digest or time.monotonic() - started >= self._lifetime:
            raise GateBlocked('范围检查已过期或范围发生变化。')
        grant = TrustedGrant(scope.metadata.run_id, secrets.token_hex(32), digest, started + self._lifetime)
        with self._lock:
            self._grants = {key: item for key, item in self._grants.items() if item._expires > time.monotonic()}
            self._grants[grant._nonce] = grant
        return grant

    def _validate_result(self, scope, result):
        if not isinstance(result, ProbeResult):
            raise GateBlocked('实时检查未返回可信的能力结果。')
        status = result.status
        if (not isinstance(status, ProviderStatus) or status.available is not True or status.evidence_verified is not True
                or status.provider != scope.metadata.provider or status.version != scope.version
                or status.executable != str(scope.executable)):
            raise GateBlocked('实时 CLI 身份、版本或登录能力检查失败。')
        caps = status.capabilities
        checks = [(caps.filesystem_confinement is True, '项目文件访问边界'),
                  (caps.streaming is True, '结构化事件'), (caps.cancellation is True, '整棵进程树取消'),
                  (caps.network_behavior == 'provider_transport_only', '网络工具限制'),
                  (caps.credential_inheritance == 'cli_native_only', '原生登录凭据隔离'),
                  (caps.tool_profile == scope.tool_profile and scope.tool_profile in ('restricted_read_write', 'text_only'), '精确工具配置'),
                  (result.exact_selections_enforced is True, '选定输入和输出目录的精确权限'),
                  (result.outside_read_denied is True, '范围外读取拒绝'),
                  (result.outside_write_denied is True, '范围外写入拒绝'),
                  (result.selected_inputs_readonly is True, '选定输入只读保护')]
        for passed, name in checks:
            if not passed:
                raise GateBlocked('未证明' + name + '，暂不能自动运行。')

    def _validate_scope(self, scope):
        if not isinstance(scope, RuntimeScope):
            raise GateBlocked('运行范围类型无效。')
        try:
            scope.validate_paths()
            if executable_digest(scope.executable) != scope.executable_sha256:
                raise ValueError('identity changed')
        except (ValueError, OSError) as exc:
            raise GateBlocked('执行文件或项目路径已变化，请重新检查范围。') from exc

    def require(self, grant: TrustedGrant, scope: RuntimeScope) -> None:
        with self._lock:
            valid = (isinstance(grant, TrustedGrant) and self._grants.get(grant._nonce) is grant
                     and grant._expires > time.monotonic() and grant._digest == scope.digest)
        if not valid:
            raise GateBlocked('运行范围证明无效、已过期或与本次任务不符。')
        self._validate_scope(scope)

    def revoke(self, grant: TrustedGrant) -> None:
        with self._lock:
            if isinstance(grant, TrustedGrant):
                self._grants.pop(grant._nonce, None)
