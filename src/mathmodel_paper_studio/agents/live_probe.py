"""Pinned CLI identity observations and ephemeral, application-owned evidence.

Baseline observations never authorize generation. A model saying it has no tools,
a CLI feature catalogue, or a stream without tool events is not a tool inventory or
a project-filesystem boundary proof.
"""
from dataclasses import dataclass, field
import hashlib
import json
import os
from pathlib import Path
import re
import queue
import tempfile
import threading
import time

from mathmodel_paper_studio.agents.preflight import AdapterCapabilities, Provider, ProviderStatus, UnavailableReason
from mathmodel_paper_studio.agents.providers.codex_cli import (
    codex_launch_arguments, codex_connection_canary_passed,
)
from mathmodel_paper_studio.agents.process_runner import ProcessLimits, _Output
from mathmodel_paper_studio.agents.windows_job import NativeJobProcess, NativeProcessError
from mathmodel_paper_studio.bootstrap.paths import AppPaths, validate_managed_path
from mathmodel_paper_studio.security.path_guard import executable_digest, native_executable

DEFAULT_INSTALLATION_ROOT = Path(r'F:\MathModelPaperStudio\downloads\mathmodel-paper-studio-live-cli-20261016')
_PINS = {
    Provider.CODEX: ('0.159.3', '57e1bdab42c0559a74558ca17e85d5c7893caa5f1e582f67e9ddd6d97fa705a7'),
    Provider.CLAUDE: ('2.1.291', '70052a17e06561a4563597f79570e81ccc0772474c3c6ac522aa5ccc71773013'),
}
_SEAL = object()
_CODEX_CANARY_PROMPT = (
    'Reply with exactly CODEX_CONNECTION_OK. Do not call any tools, run commands, '
    'or read or write files.'
)


@dataclass(frozen=True)
class PinnedInstallation:
    provider: Provider
    root: Path
    executable: Path
    native_home: Path
    version: str
    sha256: str

    @classmethod
    def locate(cls, root, provider):
        provider = Provider(provider)
        root = validate_managed_path(root)
        if provider not in _PINS:
            raise ValueError('所选服务不是受支持的原生 CLI。')
        executable = validate_managed_path(root / provider.value / (provider.value + '.exe'))
        if not executable.is_file():
            raise FileNotFoundError('F: 固定 CLI 尚未安装。')
        native_executable(executable)
        version, expected = _PINS[provider]
        if executable_digest(executable) != expected:
            raise ValueError('CLI 文件摘要与应用固定版本不一致。')
        home = validate_managed_path(root / 'native-auth' / provider.value)
        if not home.is_dir():
            raise ValueError('F: 原生 CLI 登录目录不存在，请先完成安装预检。')
        return cls(provider, root, executable, home, version, expected)

    @property
    def launch_arguments(self):
        if self.provider is Provider.CLAUDE:
            return ('-p', '--output-format', 'stream-json', '--verbose',
                    '--include-partial-messages', '--max-turns', '1', '--safe-mode',
                    '--restricted', '--tools', '', '--disallowedTools', '*',
                    '--strict-mcp-config', '--no-session-persistence', '--permission-prompts', 'none')
        return codex_launch_arguments()

    @property
    def profile_digest(self):
        payload = [self.provider.value, str(self.executable), self.sha256,
                   self.version, str(self.native_home), self.launch_arguments]
        return hashlib.sha256(json.dumps(payload, ensure_ascii=False).encode('utf-8')).hexdigest()

    def revalidate(self):
        current = type(self).locate(self.root, self.provider)
        if current != self or current.profile_digest != self.profile_digest:
            raise ValueError('CLI 身份或启动配置已变化。')


@dataclass(frozen=True)
class LiveEvidence:
    """In-memory authority; never deserialize this type from project/settings data."""
    installation: PinnedInstallation
    profile_digest: str
    issued: float
    expires: float
    tool_inventory: tuple[str, ...]
    hooks_disabled: bool
    cancellation_verified: bool
    canaries_verified: bool
    _seal: object = field(repr=False, compare=False)

    def require(self, installation):
        if (self._seal is not _SEAL or self.installation != installation
                or self.profile_digest != installation.profile_digest
                or not self.issued <= time.monotonic() < self.expires
                or self.expires - self.issued > 60 or self.tool_inventory != ()
                or not all((self.hooks_disabled, self.cancellation_verified, self.canaries_verified))):
            raise ValueError('缺少未过期的可信文本工具配置和边界证明。')
        installation.revalidate()


@dataclass(frozen=True)
class ProbeAssessment:
    status: ProviderStatus
    evidence: LiveEvidence | None = field(default=None, repr=False)


def isolated_native_environment(paths, executable, home, provider, source=None):
    """Use only new F-native homes; app never opens/copies native credentials."""
    from mathmodel_paper_studio.agents.process_runner import generated_environment
    provider = Provider(provider)
    home = validate_managed_path(home)
    env = generated_environment(paths, executable, os.environ if source is None else source)
    for key, value in {'HOME': home, 'USERPROFILE': home, 'APPDATA': home / 'AppData/Roaming',
                       'LOCALAPPDATA': home / 'AppData/Local', 'XDG_CONFIG_HOME': home / 'config',
                       'XDG_STATE_HOME': home / 'state',
                       'CODEX_HOME' if provider is Provider.CODEX else 'CLAUDE_CONFIG_DIR': home}.items():
        env[key] = str(validate_managed_path(value))
    env.update({'CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC': '1', 'DISABLE_AUTOUPDATER': '1',
                'DISABLE_TELEMETRY': '1', 'DISABLE_ERROR_REPORTING': '1'})
    return env


class NativeLiveProbe:
    """Verify pinned CLI identity and optionally send a fixed text canary.

    ``verify`` is local-only. The explicit connection test may send one fixed
    canary prompt without project data. A text reply never authorizes a run.
    """
    def __init__(self, paths: AppPaths, installation_root=DEFAULT_INSTALLATION_ROOT):
        self.paths = paths
        self.root = validate_managed_path(installation_root)

    def _capture(self, install, arguments, cancel_event=None, *, stdin=b'', wall_seconds=20, cwd=None):
        if not isinstance(stdin, bytes) or len(stdin) > 65536:
            raise ValueError('本机 CLI 输入超过预检上限。')
        limits = ProcessLimits(wall_seconds=wall_seconds, output_bytes=65536, queue_bytes=65536,
                               stdin_bytes=65536,
                               process_memory_bytes=1024**3, job_memory_bytes=1024**3,
                               active_processes=1)
        env = isolated_native_environment(self.paths, install.executable, install.native_home, install.provider)
        process, output = None, None
        buffers = {'stdout': bytearray(), 'stderr': bytearray()}
        eof = set()
        deadline = time.monotonic() + limits.wall_seconds
        try:
            install.revalidate()
            process_cwd = self.paths.temp if cwd is None else validate_managed_path(cwd)
            if not process_cwd.is_dir():
                raise ValueError('本机 CLI 工作目录不存在。')
            process = NativeJobProcess(install.executable, arguments, process_cwd, env, limits)
            output = _Output(process, limits)
            output.start(stdin)
            while len(eof) < 2 or process.poll() is None:
                if cancel_event is not None and cancel_event.is_set():
                    raise ValueError('预检已取消。')
                if time.monotonic() >= deadline or output.error:
                    raise ValueError('本地 CLI 预检超时或输出超过上限。')
                try:
                    channel, data = output.queue.get(timeout=.02)
                except queue.Empty:
                    continue
                if not data:
                    eof.add(channel)
                else:
                    buffers[channel].extend(data)
            code = process.poll()
            text = bytes(buffers['stdout']).decode('utf-8', errors='strict')
            if install.provider is Provider.CODEX and arguments == ('login', 'status'):
                text += bytes(buffers['stderr']).decode('utf-8', errors='strict')
            # Raw account metadata stays inside this local probe and is never persisted.
            return code, text
        finally:
            if output is not None:
                output.close()
            elif process is not None:
                process.close()
            for value in buffers.values():
                value.clear()

    def verify(self, provider, cancel_event=None, *, run_model_canary=False):
        provider = Provider(provider)
        install = None
        try:
            install = PinnedInstallation.locate(self.root, provider)
            if cancel_event is not None and cancel_event.is_set():
                raise ValueError('预检已取消。')
            code, text = self._capture(install, ('--version',), cancel_event)
            expected = re.escape(install.version)
            if code != 0 or not re.search(r'(?<![0-9.])' + expected + r'(?![0-9.])', text):
                return self._blocked(provider, install, UnavailableReason.VERSION_UNSUPPORTED,
                                     '本机 CLI 版本与应用固定版本不一致。')
            # Native status is local-only. Never inspect auth files or retain
            # account identifiers in status/UI/logs.
            arguments = ('login', 'status') if provider is Provider.CODEX else ('auth', 'status', '--json')
            code, text = self._capture(install, arguments, cancel_event)
            if provider is Provider.CLAUDE:
                try:
                    value = json.loads(text)
                    authenticated = value.get('loggedIn') if type(value) is dict and type(value.get('loggedIn')) is bool else None
                    if authenticated is True and code != 0:
                        authenticated = None
                except (ValueError, TypeError):
                    authenticated = None
            else:
                authenticated = (False if code == 1 and text.strip() == 'Not logged in' else
                                 True if code == 0 and text.startswith('Logged in using ') else None)
            text = ''
            if authenticated is None:
                return self._blocked(provider, install, UnavailableReason.AUTH_UNVERIFIED,
                                     '无法确认 F: 独立 CLI 的原生登录状态。')
            if authenticated is False:
                return self._blocked(provider, install, UnavailableReason.LOGIN_REQUIRED,
                                     'F: 独立 CLI 尚未验证登录；请由用户在该 CLI 完成原生登录。')
            if provider is Provider.CODEX:
                if not run_model_canary:
                    return self._blocked(provider, install, UnavailableReason.CAPABILITIES_UNVERIFIED,
                                         'Codex 本地版本和登录状态已检查；尚未发送固定文本连接请求。')
                with tempfile.TemporaryDirectory(prefix='codex-probe-', dir=self.paths.temp) as scratch:
                    code, canary = self._capture(
                        install, install.launch_arguments, cancel_event,
                        stdin=_CODEX_CANARY_PROMPT.encode('ascii'), wall_seconds=120,
                        cwd=Path(scratch))
                if not codex_connection_canary_passed(canary, exit_code=code):
                    return self._blocked(provider, install, UnavailableReason.CAPABILITIES_UNVERIFIED,
                                         'Codex 固定文本连接请求未得到预期回复；运行边界尚未验证，自动运行保持关闭。')
                status = ProviderStatus(
                    Provider.CODEX, str(install.executable), install.version, False,
                    UnavailableReason.EVIDENCE_UNVERIFIED,
                    'Codex 固定提示已收到完整文本回复；仅验证文本连通，未证明工具清单、项目文件隔离或代码执行边界。自动运行保持关闭。',
                    AdapterCapabilities(), False, True,
                )
                return ProbeAssessment(status)
            reason = UnavailableReason.CAPABILITIES_UNVERIFIED
            message = 'Claude 可请求 tools=[]，但 safe-mode 仍允许受管 hooks；未证明受管 hooks 和扩展关闭，暂不能生成。'
            return self._blocked(provider, install, reason, message)
        except FileNotFoundError:
            return self._blocked(provider, install, UnavailableReason.CLI_MISSING, '未找到 F: 固定版本 CLI。')
        except (OSError, ValueError, NativeProcessError):
            return self._blocked(provider, install, UnavailableReason.EVIDENCE_UNVERIFIED,
                                 '本地 CLI 身份、F: 路径或预检未通过；暂不能生成。')

    @staticmethod
    def _blocked(provider, install, reason, message):
        return ProbeAssessment(ProviderStatus(provider, None if install is None else str(install.executable),
                               None if install is None else install.version, False, reason,
                               message, AdapterCapabilities(tool_profile='text_only'), False))
