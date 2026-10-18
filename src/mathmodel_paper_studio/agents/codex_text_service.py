"""Codex text-only service with an in-memory, hash-bound selected-input scope.

The Codex process is not an OS sandbox. The model receives only selected text,
and its app-server session must prove that no workspace execution tools or
workspace roots are available before any turn is sent.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
from pathlib import Path, PurePosixPath
import threading

from mathmodel_paper_studio.agents.codex_app_server import (
    CodexAppServer,
    CodexAppServerBlockedError,
    CodexAppServerError,
)
from mathmodel_paper_studio.agents.codex_model_preferences import (
    CodexModelPreferenceError,
    CodexModelPreferenceStore,
)
from mathmodel_paper_studio.agents.live_probe import (
    DEFAULT_INSTALLATION_ROOT,
    PinnedInstallation,
)
from mathmodel_paper_studio.agents.preflight import (
    AdapterCapabilities,
    Provider,
    ProviderStatus,
    UnavailableReason,
)
from mathmodel_paper_studio.agents.process_runner import _Redactor
from mathmodel_paper_studio.agents.protocol import AgentEvent, EventKind
from mathmodel_paper_studio.bootstrap.paths import AppPaths, validate_managed_path
from mathmodel_paper_studio.domain.models import Project, Run, SourceFile
from mathmodel_paper_studio.workflow.context_builder import ContextInput, StageContext


_MAX_SOURCES = 256
_MAX_FILE_BYTES = 16 * 1024 * 1024
_MAX_TOTAL_BYTES = 64 * 1024 * 1024
_MAX_PROMPT_BYTES = 1024 * 1024
_MAX_RESULT_BYTES = 8 * 1024 * 1024


class CodexScopeError(RuntimeError):
    """A prompt did not match the user's current, locally verified scope."""


class CodexScopeBlocked(CodexScopeError):
    """Codex attempted a host action outside the configured text-only policy."""


@dataclass(frozen=True)
class _Snapshot:
    source: SourceFile
    path: Path
    sha256: str
    size_bytes: int


@dataclass(frozen=True)
class _Registration:
    project: Project
    run: Run
    project_root: Path
    output_root: Path
    snapshots: tuple[_Snapshot, ...]


class CodexTextService:
    """Fresh no-environment preflight and consent-bound text generation."""

    def __init__(self, paths: AppPaths, *, installation_root=DEFAULT_INSTALLATION_ROOT,
                 transport_factory=None):
        if not isinstance(paths, AppPaths):
            raise TypeError('Codex 服务需要经过路径校验的 AppPaths。')
        self.paths = paths
        self.installation_root = validate_managed_path(installation_root)
        self.transport_factory = transport_factory or CodexAppServer
        self.model_preferences = CodexModelPreferenceStore(paths)
        self._lock = threading.RLock()
        self._registration: _Registration | None = None

    @property
    def has_registered_scope(self) -> bool:
        with self._lock:
            return self._registration is not None

    def _install(self) -> PinnedInstallation:
        install = PinnedInstallation.locate(self.installation_root, Provider.CODEX)
        install.revalidate()
        return install

    def _transport(self, install):
        return self.transport_factory(self.paths, install)

    @staticmethod
    def _capabilities():
        # filesystem_confinement means the model has no project filesystem or
        # execution tools. This does not claim an OS/AppContainer sandbox.
        return AdapterCapabilities(
            streaming=True,
            cancellation=True,
            filesystem_confinement=True,
            network_behavior='provider_transport_only',
            credential_inheritance='cli_native_only',
            tool_profile='text_only_no_environment',
        )

    @staticmethod
    def _status(install, *, available, reason=None, message=None,
                evidence_verified=False, connection_verified=False):
        return ProviderStatus(
            provider=Provider.CODEX,
            executable=str(install.executable) if install is not None else None,
            version=install.version if install is not None else None,
            available=available,
            reason=reason,
            message=message or ('Codex 无执行环境策略已验证。' if available
                                else 'Codex 本机策略检查未通过；本次运行保持关闭。'),
            capabilities=CodexTextService._capabilities() if evidence_verified else AdapterCapabilities(),
            evidence_verified=evidence_verified,
            connection_verified=connection_verified,
        )

    def _locate_or_block(self):
        try:
            return self._install(), None
        except FileNotFoundError:
            return None, self._status(
                None, available=False, reason=UnavailableReason.CLI_MISSING,
                message='F: 固定版本 Codex CLI 尚未安装；本次运行保持关闭。',
            )
        except (OSError, ValueError, AttributeError):
            return None, self._status(
                None, available=False, reason=UnavailableReason.EVIDENCE_UNVERIFIED,
                message='Codex 固定版本或 F: 登录目录未通过本机校验；本次运行保持关闭。',
            )

    @staticmethod
    def _verified_observation(observation):
        return getattr(observation, 'verified', False) is True

    def probe(self, provider=Provider.CODEX, cancel_event=None) -> ProviderStatus:
        """Check pinned identity and a fresh local no-turn policy observation."""
        if Provider(provider) is not Provider.CODEX:
            return self._status(None, available=False,
                                reason=UnavailableReason.CAPABILITIES_UNVERIFIED,
                                message='此服务只处理 Codex。')
        install, blocked = self._locate_or_block()
        if blocked:
            return blocked
        try:
            observation = self._transport(install).verify_policy(cancel_event)
            if not self._verified_observation(observation):
                raise CodexAppServerError('Incomplete Codex policy observation.')
            return self._status(
                install, available=True, evidence_verified=True,
                message=(
                    'Codex 本机策略已验证：模型没有项目文件、执行或 MCP 工具，'
                    '每轮无工作区执行环境。CLI 进程本身不是 Windows OS 沙箱；'
                    'Codex 可能另行请求同一服务的账号设置。尚未发送项目内容。'
                ),
            )
        except (OSError, ValueError, RuntimeError, AttributeError):
            return self._status(
                install, available=False, reason=UnavailableReason.EVIDENCE_UNVERIFIED,
                message='Codex 无执行环境策略未完整通过；本次运行保持关闭，未发送项目内容。',
            )

    def verify_connection(self, provider=Provider.CODEX, cancel_event=None) -> ProviderStatus:
        """Send only the transport's fixed canary and require its exact reply."""
        if Provider(provider) is not Provider.CODEX:
            return self._status(None, available=False,
                                reason=UnavailableReason.CAPABILITIES_UNVERIFIED,
                                message='此连接检查只支持 Codex。')
        install, blocked = self._locate_or_block()
        if blocked:
            return blocked
        try:
            result = self._transport(install).verify_connection(cancel_event)
            if (not self._verified_observation(result.observation)
                    or result.text.strip() != 'CODEX_CONNECTION_OK'):
                raise CodexAppServerError('Codex fixed canary did not pass.')
            return self._status(
                install, available=True, evidence_verified=True, connection_verified=True,
                message=('Codex 固定文本连接验证通过；没有发送题目或文件。'
                         '模型无项目工作区工具，但 CLI 进程本身不是 Windows OS 沙箱。'),
            )
        except (OSError, ValueError, RuntimeError, AttributeError):
            return self._status(
                install, available=False, reason=UnavailableReason.API_CONNECTION_UNVERIFIED,
                message='Codex 固定文本连接验证未通过；没有发送题目或文件。',
            )

    def list_models(self, cancel_event=None):
        """Read local Codex choices without registering or sending project content."""
        install, blocked = self._locate_or_block()
        if blocked is not None:
            raise CodexAppServerError(blocked.message)
        return self._transport(install).list_models(cancel_event)

    def prepare_selected_scope(self, project: Project, run: Run, readable,
                               planned_output, selected_sources, cancel_event=None):
        """Register exact selected files in memory; no data is sent by this call."""
        try:
            if cancel_event is not None and cancel_event.is_set():
                raise CodexScopeError('范围检查已取消。')
            if (not isinstance(project, Project) or not isinstance(run, Run)
                    or run.provider is not Provider.CODEX or run.project_id != project.project_id):
                raise CodexScopeError('项目或运行归属不匹配。')
            project_root = validate_managed_path(self.paths.root / project.relative_path)
            if not project_root.is_dir():
                raise CodexScopeError('项目目录不存在。')
            output_root = validate_managed_path(project_root / run.relative_path)
            if (validate_managed_path(Path(planned_output)) != output_root
                    or not output_root.is_relative_to(project_root)):
                raise CodexScopeError('运行输出目录超出当前项目。')
            sources = tuple(selected_sources)
            readable_paths = tuple(validate_managed_path(Path(item)) for item in readable)
            if (not sources or len(sources) != len(readable_paths)
                    or len(sources) > _MAX_SOURCES
                    or any(not isinstance(source, SourceFile) for source in sources)):
                raise CodexScopeError('已选输入清单为空或格式无效。')
            seen_ids, seen_paths, snapshots, total = set(), set(), [], 0
            for source, path in zip(sources, readable_paths, strict=True):
                if (source.project_id != project.project_id or source.source_id in seen_ids
                        or source.relative_path.casefold() in seen_paths
                        or not source.relative_path.startswith('00_inputs/')):
                    raise CodexScopeError('所选输入不属于当前项目。')
                expected = validate_managed_path(
                    project_root.joinpath(*PurePosixPath(source.relative_path).parts)
                )
                if path != expected:
                    raise CodexScopeError('所选文件路径与项目记录不一致。')
                try:
                    validate_managed_path(path)
                    resolved = path.resolve(strict=True)
                    if not resolved.is_relative_to(project_root.resolve(strict=True)):
                        raise CodexScopeError('所选输入离开当前项目。')
                    info = path.stat()
                    if not path.is_file() or info.st_size > _MAX_FILE_BYTES:
                        raise CodexScopeError('所选输入不是受限大小的普通文件。')
                    digest = hashlib.sha256()
                    size = 0
                    with path.open('rb') as stream:
                        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
                            size += len(chunk)
                            if size > _MAX_FILE_BYTES:
                                raise CodexScopeError('所选输入超过单文件上限。')
                            digest.update(chunk)
                except (OSError, RuntimeError) as exc:
                    raise CodexScopeError('无法读取所选项目输入。') from exc
                actual = digest.hexdigest()
                if size != source.size_bytes or actual != source.sha256:
                    raise CodexScopeError('所选输入与项目记录的哈希不一致。')
                total += size
                if total > _MAX_TOTAL_BYTES:
                    raise CodexScopeError('所选文件总量超过本地范围上限。')
                snapshots.append(_Snapshot(source, path, actual, size))
                seen_ids.add(source.source_id)
                seen_paths.add(source.relative_path.casefold())
            problems = [item for item in snapshots if item.source.kind == 'problem']
            if len(problems) != 1:
                raise CodexScopeError('必须明确选择一个题目文件。')
            registration = _Registration(project, run, project_root, output_root, tuple(snapshots))
            with self._lock:
                self._registration = registration
            return True, (
                '本机范围检查通过：Codex 仅会收到审核列表中的题目、所选数据摘要与阶段文本；'
                '没有文件附件、项目执行或工作区文件工具。CLI 进程本身不受 OS 沙箱隔离。'
            ), ()
        except (OSError, TypeError, ValueError, RuntimeError) as exc:
            with self._lock:
                self._registration = None
            return False, 'Codex 项目文件范围检查未通过；尚未发送项目内容。', (str(exc)[:300],)

    def prepare_scope(self, project, run, readable, planned_output, cancel_event=None,
                      selected_sources=None):
        """Compatibility entrypoint; callers must supply project source records."""
        if selected_sources is None:
            return False, 'Codex 范围检查缺少已选文件记录。', ('selected_sources is required',)
        return self.prepare_selected_scope(project, run, readable, planned_output,
                                           selected_sources, cancel_event)

    def _fresh_registration(self, context: StageContext):
        if (not isinstance(context, StageContext) or context.provider is not Provider.CODEX):
            raise CodexScopeError('阶段上下文不是 Codex 请求。')
        with self._lock:
            registration = self._registration
        if (registration is None or context.run_id != registration.run.run_id
                or context.project_id != registration.project.project_id):
            raise CodexScopeError('本次运行尚未注册所选文件范围。')
        if context.output_directory is None:
            raise CodexScopeError('阶段上下文缺少输出目录。')
        output = validate_managed_path(Path(context.output_directory))
        if output != registration.output_root and not output.is_relative_to(registration.output_root):
            raise CodexScopeError('阶段输出目录超出已授权运行目录。')
        if output.exists() and not output.resolve(strict=True).is_relative_to(
                registration.output_root.resolve(strict=False)):
            raise CodexScopeError('阶段输出目录解析后离开已授权运行目录。')
        for item in registration.snapshots:
            try:
                validate_managed_path(item.path)
                info = item.path.stat()
                digest = hashlib.sha256()
                size = 0
                with item.path.open('rb') as stream:
                    for chunk in iter(lambda: stream.read(1024 * 1024), b''):
                        size += len(chunk)
                        if size > _MAX_FILE_BYTES:
                            raise CodexScopeError('已选文件大小发生变化。')
                        digest.update(chunk)
            except OSError as exc:
                raise CodexScopeError('已选文件不可读；请重新检查范围。') from exc
            if size != item.size_bytes or digest.hexdigest() != item.sha256 or info.st_size != size:
                raise CodexScopeError('已选文件内容已变化；请重新检查范围。')
        approved = {item.source.source_id: item for item in registration.snapshots}
        for item in context.inputs:
            if not isinstance(item, ContextInput):
                raise CodexScopeError('阶段输入引用格式无效。')
            expected = approved.get(item.source_id)
            expected_evidence_id = (
                None if expected is None else 'src_' + hashlib.sha256(
                    expected.source.source_id.encode('utf-8')).hexdigest()[:24]
            )
            if (expected is None
                    or (expected.source.relative_path, expected.sha256, expected.source.kind)
                    != (item.relative_path, item.sha256, item.kind)
                    or item.evidence_id != expected_evidence_id
                    or item.untrusted_data is not True
                    or item.trust_label != 'imported_data_untrusted'):
                raise CodexScopeError('阶段上下文引用了未选或已变化的输入。')
        problem_records = [item for item in registration.snapshots if item.source.kind == 'problem']
        if context.authorized_problem is not None:
            if len(problem_records) != 1:
                raise CodexScopeError('授权题目记录缺失。')
            problem = problem_records[0]
            evidence_id = 'src_' + hashlib.sha256(problem.source.source_id.encode('utf-8')).hexdigest()[:24]
            if context.problem_evidence_id != evidence_id:
                raise CodexScopeError('题目证据标识与已选文件不匹配。')
            try:
                content = problem.path.read_bytes()
                decoded = content.decode('utf-8')
            except (OSError, UnicodeError) as exc:
                raise CodexScopeError('已选题目无法读取为 UTF-8 文本。') from exc
            if decoded != context.authorized_problem:
                raise CodexScopeError('阶段题目文本与已选文件不一致。')
        elif context.problem_evidence_id is not None:
            raise CodexScopeError('阶段上下文题目证据字段不一致。')
        return registration

    def generate(self, context: StageContext, prompt: str, cancel_event=None) -> str:
        return self._generate(context, prompt, cancel_event)

    def generate_with_progress(self, context: StageContext, prompt: str, cancel_event=None,
                              *, event_callback) -> str:
        if not callable(event_callback):
            raise CodexScopeError('Codex 进度回调无效。')
        run_id = getattr(context, 'run_id', None)
        if not isinstance(run_id, str):
            raise CodexScopeError('Codex 运行标识无效。')
        self._emit_progress_event(event_callback, run_id, EventKind.STARTED)
        redactor = _Redactor(
            prompt.replace('@', '＠'),
            (prompt, getattr(context, 'authorized_problem', '') or ''),
        )
        observed = []

        def on_agent_text(item_id, delta):
            if not isinstance(delta, str):
                return
            observed.append(delta)
            safe = redactor.clean(delta, 'agent-message', final=False)
            self._emit_agent_text(event_callback, run_id, item_id, safe)

        try:
            result = self._generate(context, prompt, cancel_event, on_agent_text=on_agent_text)
            if not observed:
                self._emit_agent_text(event_callback, run_id, None,
                                      redactor.clean(result, 'agent-message', final=False))
            tail = redactor.clean('', 'agent-message', final=True)
            self._emit_agent_text(event_callback, run_id, None, tail)
            self._emit_progress_event(event_callback, run_id, EventKind.COMPLETED)
            return result
        except CodexScopeBlocked:
            self._emit_progress_event(event_callback, run_id, EventKind.BLOCKED)
            raise
        except Exception:
            kind = (EventKind.CANCELLED if cancel_event is not None
                    and callable(getattr(cancel_event, 'is_set', None)) and cancel_event.is_set()
                    else EventKind.FAILED)
            self._emit_progress_event(event_callback, run_id, kind)
            raise

    @staticmethod
    def _emit_progress_event(callback, run_id, kind, text='', item_id=None):
        try:
            callback(AgentEvent(run_id, kind, text=text, item_id=item_id))
        except Exception:
            return

    @classmethod
    def _emit_agent_text(cls, callback, run_id, item_id, text):
        remaining = text
        while remaining:
            encoded = remaining.encode('utf-8')
            if len(encoded) <= 64 * 1024:
                chunk, remaining = remaining, ''
            else:
                chunk = encoded[:64 * 1024].decode('utf-8', errors='ignore')
                remaining = encoded[len(chunk.encode('utf-8')):].decode('utf-8')
            safe_item_id = item_id if isinstance(item_id, str) else None
            try:
                callback(AgentEvent(run_id, EventKind.TEXT, text=chunk,
                                    item_id=safe_item_id))
            except ValueError:
                try:
                    callback(AgentEvent(run_id, EventKind.TEXT, text=chunk))
                except Exception:
                    return
            except Exception:
                return

    def _generate(self, context: StageContext, prompt: str, cancel_event=None,
                  *, on_agent_text=None) -> str:
        if (not isinstance(prompt, str) or not prompt.strip() or '\0' in prompt
                or len(prompt.encode('utf-8')) > _MAX_PROMPT_BYTES):
            raise CodexScopeError('Codex 文本请求为空或超过上限。')
        if cancel_event is not None and cancel_event.is_set():
            raise CodexScopeError('Codex 文本请求已取消。')
        self._fresh_registration(context)
        install, blocked = self._locate_or_block()
        if blocked is not None:
            raise CodexScopeError('Codex 固定版本本机校验失败；文本未发送。')
        try:
            preference = self.model_preferences.load()
        except CodexModelPreferenceError as exc:
            raise CodexScopeError('Codex 模型设置无效；本次文本未发送，请在设置中修复。') from exc
        safe_prompt = prompt.replace('@', '＠')
        try:
            transport = self._transport(install)
            observer = {'on_agent_text': on_agent_text} if on_agent_text is not None else {}
            if preference is None:
                result = transport.generate(safe_prompt, cancel_event, **observer)
            else:
                result = transport.generate(
                    safe_prompt, cancel_event, model=preference.model, effort=preference.effort,
                    **observer,
                )
        except CodexAppServerBlockedError as exc:
            raise CodexScopeBlocked('Codex 尝试了未授权的主机操作，文本结果已丢弃。') from exc
        except (OSError, ValueError, RuntimeError) as exc:
            raise CodexScopeError('Codex 无执行环境文本请求未通过；结果已丢弃。') from exc
        if (not self._verified_observation(result.observation)
                or not isinstance(result.text, str) or not result.text.strip()
                or len(result.text.encode('utf-8')) > _MAX_RESULT_BYTES):
            raise CodexScopeError('Codex 会话策略变化或返回文本无效；结果已丢弃。')
        return result.text
