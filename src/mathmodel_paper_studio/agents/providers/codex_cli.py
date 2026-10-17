"""Codex rust-v0.159.3 JSONL contract and constrained launch arguments.

Executable identity and native login are checked by ``NativeLiveProbe``. A text
canary proves only transport; this module never discovers auth or a tool inventory.
"""
from dataclasses import dataclass
import json
import math

from mathmodel_paper_studio.agents.preflight import (
    AdapterCapabilities, Provider, ProviderStatus, assess_provider,
)
from mathmodel_paper_studio.agents.protocol import AgentEvent, AgentRequest, EventKind
from mathmodel_paper_studio.domain.models import identifier

PROTOCOL_VERSION = '0.159.3'
_MAX_LINE_BYTES = 65536
_MAX_RUN_BYTES = 8 * 1024 * 1024
_MAX_RECORDS = 4096
_MAX_ITEMS = 1024

CODEX_DISABLED_FEATURES = (
    'apps', 'auth_elicitation', 'browser_use', 'browser_use_external',
    'browser_use_full_cdp_access', 'code_mode', 'code_mode_host', 'code_mode_only',
    'compaction_image_budget',
    'computer_use', 'content_item_kinds', 'daemon_auto_start', 'fast_mode',
    'goals', 'guardian_approval', 'guardian_reuse_parent_compaction', 'hooks',
    'image_generation', 'in_app_browser', 'in_app_chat', 'in_app_dictation',
    'in_app_local_automation', 'in_app_updates', 'mentions_v2', 'multi_agent',
    'plugin_sharing', 'plugins', 'realtime_conversation', 'remote_plugin',
    'shell_snapshot', 'shell_tool', 'skill_mcp_dependency_install',
    'skill_search', 'sleep_tool', 'tool_call_mcp_elicitation', 'tool_suggest',
    'unbounded_connection_retries', 'unified_exec', 'unified_exec_tty',
    'view_image', 'workspace_dependencies', 'worktrees', 'write_stdin_approval',
)
def codex_disable_arguments():
    return tuple(argument for feature in CODEX_DISABLED_FEATURES
                 for argument in ('--disable', feature))


def codex_launch_arguments():
    return (
        'exec', '--json', '--color', 'never', '--sandbox', 'read-only',
        '--ephemeral', '--ignore-user-config', '--ignore-rules',
        '--skip-git-repo-check', *codex_disable_arguments(), '-',
    )


def _invalid():
    return ValueError('CLI 事件格式无效或不完整。')


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise _invalid()
        result[key] = value
    return result


def _nonfinite(_value):
    raise _invalid()


def _bounded_value(value, depth=0, budget=None):
    """Bound optional provider metadata too, without application-report schemas."""
    if budget is None:
        budget = [4096]
    budget[0] -= 1
    if depth > 16 or budget[0] < 0:
        raise _invalid()
    if isinstance(value, str):
        if '\0' in value or len(value.encode('utf-8')) > _MAX_LINE_BYTES:
            raise _invalid()
    elif type(value) is dict:
        for key, child in value.items():
            if not isinstance(key, str) or len(key) > 128 or any(ord(char) < 32 for char in key):
                raise _invalid()
            _bounded_value(child, depth + 1, budget)
    elif type(value) is list:
        for child in value:
            _bounded_value(child, depth + 1, budget)
    elif type(value) is float:
        if not math.isfinite(value):
            raise _invalid()
    elif value is not None and type(value) not in (bool, int):
        raise _invalid()


def _text(value):
    if not isinstance(value, str):
        raise _invalid()
    return value


@dataclass(frozen=True)
class _Item:
    kind: str
    text: str
    completed: bool


class CodexEventParser:
    """One version-bound thread/turn; callable passed to Task3 ProcessRunner.

    ProcessRunner owns incremental byte framing/UTF-8 EOF validation, bounded
    drains, redaction, cancellation, timeout and exit-code confirmation. A
    COMPLETED returned here is provisional until that runner confirms exit 0.
    No event, including an opaque thread ID, is scope or readiness evidence.
    """
    def __init__(self, run_id: str, *, version: str = PROTOCOL_VERSION,
                 preserve_thread_id: bool = False, max_records: int = _MAX_RECORDS):
        self.run_id = identifier(run_id)
        if version != PROTOCOL_VERSION or type(preserve_thread_id) is not bool:
            raise ValueError('CLI 版本或会话保留配置未经支持。')
        if type(max_records) is not int or not 1 <= max_records <= _MAX_RECORDS:
            raise ValueError('CLI 事件数量上限无效。')
        self._preserve_thread_id = preserve_thread_id
        self._resume_thread_id = None
        self._max_records = max_records
        self._records = self._bytes = 0
        self._thread_seen = self._turn_started = self._terminal = False
        self._items = {}

    @property
    def resume_thread_id(self) -> str | None:
        """In-memory opaque ID only, when explicitly requested by trusted code.

        This is not native resume authorization. The disabled production adapter
        never enables retention, stores this identifier, or issues a resume argv.
        """
        return self._resume_thread_id

    def __call__(self, line: str) -> tuple[AgentEvent, ...]:
        if self._terminal or not isinstance(line, str):
            self._terminal = True
            self._resume_thread_id = None
            self._items = {}
            raise _invalid()
        try:
            size = len(line.encode('utf-8'))
            self._records += 1
            self._bytes += size
            if (size > _MAX_LINE_BYTES or self._bytes > _MAX_RUN_BYTES
                    or self._records > self._max_records):
                raise _invalid()
            value = json.loads(line, object_pairs_hook=_unique_object, parse_constant=_nonfinite)
            _bounded_value(value)
            if type(value) is not dict or not isinstance(value.get('type'), str):
                raise _invalid()
            return self._event(value)
        except (ValueError, TypeError, RecursionError, UnicodeError, OverflowError):
            self._terminal = True
            self._resume_thread_id = None
            self._items = {}
            raise _invalid() from None

    def _emit(self, kind, text='', item_id=None):
        return (AgentEvent(self.run_id, kind, text, item_id),)

    def _end(self, kind, text=''):
        self._terminal = True
        self._items = {}
        if kind is not EventKind.COMPLETED:
            self._resume_thread_id = None
        return self._emit(kind, text)

    def _event(self, value):
        kind = value['type']
        if kind == 'error':
            _text(value.get('message'))
            return self._end(EventKind.FAILED, 'CLI 执行失败。')
        if kind == 'thread.started':
            if self._thread_seen or self._turn_started:
                raise _invalid()
            thread_id = identifier(value.get('thread_id'))
            self._thread_seen = True
            if self._preserve_thread_id:
                self._resume_thread_id = thread_id
            return ()
        if kind == 'turn.started':
            if not self._thread_seen or self._turn_started:
                raise _invalid()
            self._turn_started = True
            return self._emit(EventKind.STARTED)
        if kind == 'turn.failed':
            error = value.get('error')
            if type(error) is not dict:
                raise _invalid()
            _text(error.get('message'))
            # The tagged protocol supplies an untrusted free-text message only;
            # do not guess login/billing/permission failure from model text.
            return self._end(EventKind.FAILED, 'CLI 执行失败。')
        if not self._turn_started:
            if kind == 'item.completed':
                item = value.get('item')
                if type(item) is dict and item.get('type') == 'error':
                    item_id = identifier(item.get('id'))
                    _text(item.get('message'))
                    if item_id in self._items or len(self._items) >= _MAX_ITEMS:
                        raise _invalid()
                    self._items = {**self._items, item_id: _Item('error', '', True)}
                    return self._emit(EventKind.TEXT, 'CLI 报告了非致命错误。', item_id)
            raise _invalid()
        if kind == 'turn.completed':
            self._usage(value.get('usage'))
            if any(not item.completed for item in self._items.values()):
                raise _invalid()
            return self._end(EventKind.COMPLETED)
        if kind in ('item.started', 'item.updated', 'item.completed'):
            return self._item(kind, value.get('item'))
        raise _invalid()

    @staticmethod
    def _usage(value):
        required = {'input_tokens', 'cached_input_tokens', 'output_tokens'}
        if type(value) is not dict or not required.issubset(value):
            raise _invalid()
        # Optional cache_write_input_tokens and reasoning counters are accepted
        # when present; no counts or model/account metadata are retained.
        if any(type(count) is not int or not 0 <= count <= 2 ** 63 - 1 for count in value.values()):
            raise _invalid()

    def _item(self, phase, value):
        if type(value) is not dict:
            raise _invalid()
        item_id = identifier(value.get('id'))
        kind = _text(value.get('type'))
        if kind not in ('agent_message', 'reasoning', 'error'):
            # Command/file/MCP/web/unknown activity cannot fit a verified Codex
            # profile: no such live profile was approved. Never forward payloads.
            return self._end(EventKind.BLOCKED, 'CLI 工具配置与已批准范围不一致，已停止。')
        text = _text(value.get('message') if kind == 'error' else value.get('text'))
        previous = self._items.get(item_id)
        if previous is not None:
            if previous.completed or previous.kind != kind or phase == 'item.started':
                raise _invalid()
            if not text.startswith(previous.text):
                raise _invalid()
        elif phase == 'item.updated' or len(self._items) >= _MAX_ITEMS:
            raise _invalid()
        delta = text[len(previous.text):] if previous else text
        # Retain bounded snapshots for deduplication only, never raw tool data.
        self._items = {**self._items, item_id: _Item(kind, text if kind == 'agent_message' else '', phase == 'item.completed')}
        if kind == 'agent_message' and delta:
            return self._emit(EventKind.TEXT, delta, item_id)
        if kind == 'error' and phase == 'item.completed':
            return self._emit(EventKind.TEXT, 'CLI 报告了非致命错误。', item_id)
        return ()


def codex_connection_canary_passed(output, *, exit_code):
    """Accept one exact text response without observed execution events.

    This proves only that the fixed request received a text response. It is not
    a tool inventory or a project-filesystem isolation proof.
    """
    if (not isinstance(output, str) or len(output.encode('utf-8')) > 65536
            or type(exit_code) is not int or exit_code != 0):
        return False
    parser = CodexEventParser('r_probe')
    terminal_count = 0
    messages = {}
    try:
        for line in output.splitlines():
            if not line or len(line.encode('utf-8')) > _MAX_LINE_BYTES:
                return False
            record = json.loads(line, object_pairs_hook=_unique_object, parse_constant=_nonfinite)
            if type(record) is not dict:
                return False
            events = parser(line)
            if any(event.kind in (EventKind.BLOCKED, EventKind.FAILED, EventKind.CANCELLED)
                   for event in events):
                return False
            if record.get('type') == 'turn.completed':
                terminal_count += 1
            if record.get('type') in ('item.started', 'item.updated', 'item.completed'):
                item = record.get('item')
                if type(item) is not dict or item.get('type') not in ('agent_message', 'reasoning', 'error'):
                    return False
                if record.get('type') == 'item.completed' and item.get('type') == 'agent_message':
                    item_id = identifier(item.get('id'))
                    message = item.get('text')
                    if not isinstance(message, str) or len(message) > 64:
                        return False
                    messages[item_id] = message
        return terminal_count == 1 and len(messages) == 1 and tuple(messages.values()) == ('CODEX_CONNECTION_OK',)
    except (ValueError, TypeError, RecursionError, UnicodeError):
        return False


class CodexCliAdapter:
    """Unavailable until a future reviewed executable + exact-scope live probe.

    Hints are display observations, never health/auth proof. This class does not
    search PATH, read HOME/auth/config stores, run version/login commands, change
    native environment or construct subprocess argv. AppServer/API fallback and
    permission bypass are intentionally absent. Fake parsers/grants cannot turn
    this production class into a launch path.
    """
    def __init__(self, *, executable: str | None = None, version: str | None = None):
        for value, bound in ((executable, 4096), (version, 100)):
            if value is not None and (not isinstance(value, str) or not value or len(value) > bound
                                      or any(ord(char) < 32 for char in value)):
                raise ValueError('CLI 路径或版本提示无效。')
        self._executable, self._version = executable, version

    def probe(self) -> ProviderStatus:
        return assess_provider(Provider.CODEX, executable=self._executable, version=self._version,
                               authenticated=None, capability_report=None)

    def capabilities(self) -> AdapterCapabilities:
        return AdapterCapabilities()

    def stream(self, request: AgentRequest):
        run_id = identifier(getattr(request, 'run_id', 'invalid'))
        if not isinstance(request, AgentRequest) or request.scope.metadata.provider is not Provider.CODEX:
            yield AgentEvent(run_id, EventKind.FAILED, '运行请求与所选 Agent 不一致。')
            return
        if request.scope.version != PROTOCOL_VERSION:
            yield AgentEvent(run_id, EventKind.FAILED, 'CLI 版本未经过协议检查，需要重新验证。')
            return
        yield AgentEvent(run_id, EventKind.BLOCKED,
                         'Codex 尚无本次精确范围的可信实时检查，暂不能自动运行。')

    def cancel(self, run_id: str) -> None:
        # No production process can be active; keep cancellation idempotent.
        identifier(run_id)


