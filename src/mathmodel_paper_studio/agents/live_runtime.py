"""Text-only provider broker. All native execution requires ephemeral live proof.

Selected host snapshots reach the provider through stdin. Provider processes are
never authorized to run shell, file, MCP, web, model extension or execution tools.
Current native policy observability is incomplete and defaults stay blocked.
"""
from dataclasses import dataclass, field, replace
import hashlib
import json
from pathlib import Path
import threading
import time

from mathmodel_paper_studio.agents.live_probe import (
    DEFAULT_INSTALLATION_ROOT, LiveEvidence, NativeLiveProbe, PinnedInstallation,
    isolated_native_environment,
)
from mathmodel_paper_studio.agents.preflight import Provider, ProviderStatus, UnavailableReason
from mathmodel_paper_studio.agents.process_runner import ProcessRunner, ProcessLimits
from mathmodel_paper_studio.agents.protocol import AgentRequest, EventKind, TERMINAL_KINDS
from mathmodel_paper_studio.agents.providers.codex_cli import CodexEventParser
from mathmodel_paper_studio.agents.providers.claude_code_cli import ClaudeEventParser
from mathmodel_paper_studio.bootstrap.paths import AppPaths, validate_managed_path
from mathmodel_paper_studio.domain.models import Project, Run, ScopeManifest, Stage, identifier
from mathmodel_paper_studio.security.scope_manifest import build_scope
from mathmodel_paper_studio.security.sandbox_gate import ScopeGate, GateBlocked
from mathmodel_paper_studio.workflow.context_builder import StageContext

_MAX_SNAPSHOT_BYTES = 16 * 1024 * 1024
_MAX_RESULT_BYTES = 4 * 1024 * 1024


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('CLI 事件键重复。')
        result[key] = value
    return result


class TextOnlyParser:
    """Normalize events after the trusted profile probe establishes no tools."""
    def __init__(self, provider, run_id, *, codex_inventory_verified=False):
        self.provider = Provider(provider)
        if type(codex_inventory_verified) is not bool:
            raise ValueError('Codex 零工具预检状态无效。')
        self.parser = (CodexEventParser if self.provider is Provider.CODEX else ClaudeEventParser)(run_id)
        self.inventory_verified = self.provider is Provider.CODEX and codex_inventory_verified
        self._chunks = ()
        self._bytes = 0
        self._poisoned = False

    @property
    def raw_text(self):
        return ''.join(self._chunks)

    def discard(self):
        self._chunks = ()
        self._bytes = 0
        self._poisoned = True

    def __call__(self, line):
        try:
            if self._poisoned or len(line.encode('utf-8')) > 65536:
                raise ValueError('CLI 事件无效。')
            value = json.loads(line, object_pairs_hook=_unique)
            if self.provider is Provider.CLAUDE:
                value = self._claude(value)
                line = json.dumps(value, ensure_ascii=False, separators=(',', ':'), allow_nan=False)
            events = tuple(self.parser(line))
            item = value.get('item') if type(value) is dict else None
            if (self.provider is Provider.CODEX
                    and value.get('type') in ('item.started', 'item.updated', 'item.completed')
                    and type(item) is dict and item.get('type') == 'error'):
                return ()
            for event in events:
                if event.kind in (EventKind.TOOL_STARTED, EventKind.TOOL_FINISHED):
                    raise ValueError('文本配置出现工具事件。')
                if event.kind in (EventKind.FAILED, EventKind.CANCELLED, EventKind.BLOCKED):
                    self.discard()
                if event.kind is EventKind.TEXT:
                    self._bytes += len(event.text.encode('utf-8'))
                    if self._bytes > _MAX_RESULT_BYTES:
                        raise ValueError('生成文本超过上限。')
                    self._chunks = (*self._chunks, event.text)
            return events
        except (ValueError, TypeError, RecursionError, UnicodeError):
            self.discard()
            raise ValueError('文本 CLI 事件、工具清单或扩展配置无效。') from None

    def _claude(self, value):
        if type(value) is not dict:
            raise ValueError('invalid event')
        if value.get('type') == 'system' and value.get('subtype') == 'init':
            if self.inventory_verified or value.get('tools') != []:
                raise ValueError('zero-tool inventory absent')
            for key in ('mcp_servers', 'plugins'):
                if key in value and value[key] != []:
                    raise ValueError('native extension active')
            self.inventory_verified = True
            return {key: child for key, child in value.items() if key != 'tools'}
        if value.get('type') == 'system' and str(value.get('subtype', '')).startswith('hook'):
            raise ValueError('hook event active')
        if not self.inventory_verified:
            raise ValueError('native init absent')
        # Recursively inspect *types*, never matching user/model text strings.
        def check(child, depth=0):
            if depth > 16:
                raise ValueError('event nesting')
            if type(child) is dict:
                if child.get('type') in ('tool_use', 'tool_result', 'input_json_delta', 'server_tool_use'):
                    raise ValueError('tool content active')
                for item in child.values():
                    check(item, depth + 1)
            elif type(child) is list:
                for item in child:
                    check(item, depth + 1)
        check(value)
        return value


@dataclass(frozen=True)
class _RegisteredScope:
    project: Project
    run: Run
    readable: tuple[Path, ...]
    snapshots: tuple[tuple[Path, int, str], ...]
    output: Path
    installation: PinnedInstallation
    evidence: LiveEvidence = field(repr=False)
    status: ProviderStatus


class LiveProviderService:
    def __init__(self, paths: AppPaths, *, installation_root=DEFAULT_INSTALLATION_ROOT):
        if type(paths) is not AppPaths:
            raise TypeError('文本 CLI 服务需要 AppPaths。')
        paths.validate()
        self.paths = paths
        self.installation_root = validate_managed_path(installation_root)
        self._probe = NativeLiveProbe(paths, self.installation_root)
        self._evidence = {}
        self._registered = {}
        self._active = {}
        self._lock = threading.RLock()

    def probe(self, provider, cancel_event=None):
        provider = Provider(provider)
        cached = self._cached_status(provider)
        if cached is not None:
            return cached
        assessment = self._probe.verify(provider, cancel_event)
        return self._record_assessment(provider, assessment)

    def verify_connection(self, provider, cancel_event=None):
        provider = Provider(provider)
        if provider is not Provider.CODEX:
            return self.probe(provider, cancel_event)
        assessment = self._probe.verify(provider, cancel_event, run_model_canary=True)
        return self._record_assessment(provider, assessment)

    def _cached_status(self, provider):
        with self._lock:
            record = self._evidence.get(provider)
        if record is None:
            return None
        evidence, status = record
        try:
            install = PinnedInstallation.locate(self.installation_root, provider)
            evidence.require(install)
        except (OSError, ValueError):
            with self._lock:
                self._evidence.pop(provider, None)
            return None
        return status

    def _record_assessment(self, provider, assessment):
        if assessment.evidence is None and assessment.status.available:
            return replace(assessment.status, available=False, evidence_verified=False,
                           reason=UnavailableReason.EVIDENCE_UNVERIFIED,
                           message='缺少本次原生 CLI 的可信实时边界证明。')
        with self._lock:
            self._evidence.pop(provider, None)
            if assessment.evidence is not None:
                install = PinnedInstallation.locate(self.installation_root, provider)
                assessment.evidence.require(install)
                status = assessment.status
                if (not status.available or not status.evidence_verified
                        or status.provider != provider or status.executable != str(install.executable)
                        or status.version != install.version or status.capabilities.tool_profile != 'text_only'):
                    raise GateBlocked('实时预检身份或工具配置不一致。')
                self._evidence[provider] = (assessment.evidence, status)
        return assessment.status

    @staticmethod
    def _snapshot(path):
        path = validate_managed_path(path)
        if not path.is_file() or path.stat().st_size > _MAX_SNAPSHOT_BYTES:
            raise ValueError('已选输入不存在或超过文本预检大小上限。')
        before = path.stat()
        digest = hashlib.sha256()
        with path.open('rb') as source:
            for chunk in iter(lambda: source.read(65536), b''):
                digest.update(chunk)
        after = path.stat()
        if (before.st_size, before.st_mtime_ns, before.st_ino) != (after.st_size, after.st_mtime_ns, after.st_ino):
            raise ValueError('已选输入在检查过程中变化。')
        return path, after.st_size, digest.hexdigest()

    def prepare_scope(self, project, run, readable, planned_output, cancel_event=None):
        try:
            if type(project) is not Project or type(run) is not Run or run.project_id != project.project_id:
                raise ValueError('项目与运行记录归属不一致。')
            if type(readable) is not tuple or len(readable) > 256 or len(set(readable)) != len(readable):
                raise ValueError('已选输入集合无效、重复或过多。')
            root = validate_managed_path(self.paths.root / project.relative_path)
            output = validate_managed_path(planned_output)
            run_root = validate_managed_path(root / run.relative_path)
            if not root.is_dir() or not output.is_dir() or not output.is_relative_to(run_root):
                raise ValueError('输出必须为本次运行内现有的明确目录。')
            snapshots = tuple(self._snapshot(path) for path in readable)
            if any(not path.is_relative_to(root) or path.is_relative_to(output) for path, _, _ in snapshots):
                raise ValueError('输入必须来自当前项目且位于可写输出之外。')
            self.probe(run.provider, cancel_event)
            with self._lock:
                record = self._evidence.get(run.provider)
            if record is None:
                raise GateBlocked('缺少本次 CLI 的实时范围与运行边界证明；文本连接验证不能授权生成。')
            evidence, status = record
            install = PinnedInstallation.locate(self.installation_root, run.provider)
            evidence.require(install)
            registration = _RegisteredScope(project, run, tuple(item[0] for item in snapshots),
                                            snapshots, output, install, evidence, status)
            with self._lock:
                self._registered[run.run_id] = registration
            return True, '本次文本输入范围检查通过。', ()
        except (OSError, ValueError, GateBlocked) as exc:
            if isinstance(run, Run):
                with self._lock:
                    self._registered.pop(run.run_id, None)
            return False, '本次文本生成范围检查未通过。', (str(exc),)

    def _fresh_registration(self, context):
        if type(context) is not StageContext:
            raise ValueError('阶段上下文类型无效。')
        with self._lock:
            registration = self._registered.get(context.run_id)
        if (registration is None or registration.project.project_id != context.project_id
                or registration.run.provider != context.provider):
            raise GateBlocked('本次运行尚未注册可信文本输入范围。')
        registration.evidence.require(registration.installation)
        root = validate_managed_path(self.paths.root / registration.project.relative_path)
        if context.output_directory is None:
            raise GateBlocked('阶段上下文缺少明确输出目录。')
        output = validate_managed_path(context.output_directory)
        if not output.is_dir() or not output.is_relative_to(registration.output):
            raise GateBlocked('阶段输出目录离开本次授权范围。')
        if tuple(self._snapshot(path) for path in registration.readable) != registration.snapshots:
            raise GateBlocked('已选输入快照变化，请重新检查。')
        expected = {path.relative_to(root).as_posix(): digest for path, _, digest in registration.snapshots}
        if any(expected.get(item.relative_path) != item.sha256 for item in context.inputs):
            raise GateBlocked('阶段上下文引用了未选输入或过期快照。')
        return registration, root, output

    def generate(self, context, prompt, cancel_event=None):
        registration, root, output = self._fresh_registration(context)
        if cancel_event is not None and cancel_event.is_set():
            raise GateBlocked('本次生成已取消。')
        install = registration.installation
        metadata = ScopeManifest('live_' + hashlib.sha256(context.run_id.encode('utf-8')).hexdigest()[:24], context.run_id, context.provider,
                                 registration.project.relative_path,
                                 tuple(path.relative_to(root).as_posix() for path in registration.readable),
                                 (output.relative_to(root).as_posix(),), (context.stage,), True)
        scope = build_scope(self.paths.root, metadata, install.executable, install.version,
                            'text_only', launch_arguments=install.launch_arguments)
        def trusted_probe(current):
            self._fresh_registration(context)
            if current.digest != scope.digest:
                raise GateBlocked('本次运行范围发生变化。')
            return self._probe_exact_scope(current, context, registration)
        gate = ScopeGate(trusted_probe=trusted_probe)
        grant = gate.authorize(scope, purpose='provider_text_only')
        parser = TextOnlyParser(
            context.provider, context.run_id,
            codex_inventory_verified=(
                context.provider is Provider.CODEX
                and registration.evidence.tool_inventory == ()
                and registration.status.evidence_verified is True
                and registration.status.capabilities.tool_profile == 'text_only'
            ),
        )
        environment = lambda paths, exe: isolated_native_environment(paths, exe, install.native_home, context.provider)
        runner = ProcessRunner(gate, self.paths, ProcessLimits(stdin_bytes=1024**2, wall_seconds=120,
                               process_memory_bytes=1024**3, job_memory_bytes=1024**3, active_processes=1),
                               environment_factory=environment)
        request = AgentRequest(scope, context.stage, prompt)
        stop = threading.Event()
        monitor = None
        def watch():
            while not stop.wait(.02):
                if cancel_event.is_set():
                    runner.cancel(context.run_id)
                    return
        with self._lock:
            if context.run_id in self._active:
                raise GateBlocked('同一运行已有活动文本生成。')
            self._active[context.run_id] = runner
        try:
            if cancel_event is not None:
                monitor = threading.Thread(target=watch, daemon=True)
                monitor.start()
            terminal = None
            for event in runner.stream(request, grant, install.launch_arguments, parser):
                if event.kind in TERMINAL_KINDS:
                    terminal = event.kind
            if terminal is not EventKind.COMPLETED or not parser.inventory_verified:
                raise GateBlocked('文本 CLI 未完成可信零工具生成，已丢弃部分结果。')
            if cancel_event is not None and cancel_event.is_set():
                raise GateBlocked('本次生成已取消，已丢弃结果。')
            # ProcessRunner confirms clean exit + complete Job cleanup *before*
            # returning COMPLETED. Raw parser TEXT bypasses display redaction
            # solely for bounded artifact validation, never for UI/logging.
            result = parser.raw_text
            if not result.strip():
                raise GateBlocked('文本 CLI 返回空结果。')
            return result
        finally:
            parser.discard()
            stop.set()
            if monitor is not None:
                monitor.join(.2)
            runner.cancel(context.run_id)
            gate.revoke(grant)
            with self._lock:
                self._active.pop(context.run_id, None)

    def _probe_exact_scope(self, _scope, _context, _registration):
        raise GateBlocked('缺少独立的精确文件范围、范围外访问拒绝和输入只读实时探测。')

    def cancel(self, run_id):
        identifier(run_id)
        with self._lock:
            runner = self._active.get(run_id)
            self._registered.pop(run_id, None)
        if runner is not None:
            runner.cancel(run_id)
