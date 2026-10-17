"""Version-bound Claude stream-json parser with production launch disabled.

Provider records are untrusted. This parser reports visible text and safe tool
state only; it never proves authentication or filesystem isolation. Task0's
restricted Read/Write observation is historical and cannot authorize a run.
"""
import hashlib
import json
import math
import re

from mathmodel_paper_studio.agents.preflight import (
    AdapterCapabilities, Provider, ProviderStatus, assess_provider,
)
from mathmodel_paper_studio.agents.protocol import AgentEvent, AgentRequest, EventKind
from mathmodel_paper_studio.domain.models import identifier

PROTOCOL_VERSION = '2.1.291'
MAX_TURNS = 8
_MAX_LINE_BYTES = 65536
_MAX_RUN_BYTES = 8 * 1024 * 1024
_MAX_RECORDS = 4096
_MAX_ITEMS = 1024
_MAX_TEXT_CHUNKS = _MAX_RECORDS
_SESSION_ID = re.compile(r'[A-Za-z0-9_-]{1,64}\Z')
_TOOL_NAMES = frozenset(('Read', 'Write'))
_ERROR_MESSAGES = {
    'authentication_failed': 'CLI 登录状态无效，请在原 CLI 中完成登录。',
    'billing_error': '账户额度或账单异常，请检查原 CLI 账户状态。',
    'rate_limit': '请求限流，请稍后重试。',
    'permission': '文件或工具权限被拒绝。',
    'interrupted': 'CLI 执行中断，未完成。',
}
_TYPED_ERRORS = frozenset(('authentication_failed', 'billing_error', 'rate_limit',
                           'invalid_request', 'server_error', 'unknown'))


def build_claude_arguments(max_turns: int = MAX_TURNS) -> tuple[str, ...]:
    """Build only the Task0-observed restricted Read/Write profile.

    The headless flags select the documented stream-json protocol. This tuple
    is not live authority: the adapter remains disabled until a fresh trusted
    probe verifies this exact executable, version, settings and project scope.
    """
    if type(max_turns) is not int or not 1 <= max_turns <= MAX_TURNS:
        raise ValueError('本次执行轮数必须是 1–8 之间的整数。')
    return ('-p', '--output-format', 'stream-json', '--verbose',
            '--include-partial-messages', '--max-turns', str(max_turns),
            '--restricted', '--tools', 'Read,Write', '--disallowedTools', 'mcp__*',
            '--no-session-persistence', '--permission-prompts', 'none')


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


def _string(value, *, maximum=_MAX_LINE_BYTES):
    if not isinstance(value, str) or '\0' in value or len(value.encode('utf-8')) > maximum:
        raise _invalid()
    return value


def _opaque_session(value):
    if not isinstance(value, str) or not _SESSION_ID.fullmatch(value):
        raise _invalid()
    return value


class ClaudeEventParser:
    """Callable line parser for Task3 ProcessRunner; one version-bound run."""
    def __init__(self, run_id: str, *, version: str = PROTOCOL_VERSION,
                 preserve_session_id: bool = False, max_records: int = _MAX_RECORDS):
        self.run_id = identifier(run_id)
        if version != PROTOCOL_VERSION or type(preserve_session_id) is not bool:
            raise ValueError('CLI 版本或会话保留配置未经支持。')
        if type(max_records) is not int or not 1 <= max_records <= _MAX_RECORDS:
            raise ValueError('CLI 事件数量上限无效。')
        self._preserve_session_id = preserve_session_id
        self._session_id = None
        self._observed_session_id = None
        self._max_records = max_records
        self._records = self._bytes = 0
        self._initialized = self._terminal = False
        self._rate_limit_rejected = False
        self._pending_error_code = None
        self._assistant_message_ids = set()
        self._stream_message_ids = set()
        self._stream_message_id = None
        self._stream_blocks = {}
        self._next_block_index = 0
        self._message_delta_seen = False
        self._text_block_sources = {}
        self._visible_text_chunks = ()
        self._visible_text_bytes = 0
        self._visible_text_messages = {}
        self._tool_ids = {}
        self._finished_tools = set()

    @property
    def session_id(self) -> str | None:
        """Optional bounded opaque in-memory ID; it does not enable CLI resume."""
        return self._session_id

    def __repr__(self):
        return f'<ClaudeEventParser run_id={self.run_id!r} initialized={self._initialized} terminal={self._terminal}>'

    def __call__(self, line: str) -> tuple[AgentEvent, ...]:
        if self._terminal or not isinstance(line, str):
            self._poison()
            raise _invalid()
        try:
            size = len(line.encode('utf-8'))
            self._records += 1
            self._bytes += size
            if size > _MAX_LINE_BYTES or self._bytes > _MAX_RUN_BYTES or self._records > self._max_records:
                raise _invalid()
            value = json.loads(line, object_pairs_hook=_unique_object, parse_constant=_nonfinite)
            _bounded_value(value)
            if type(value) is not dict or not isinstance(value.get('type'), str):
                raise _invalid()
            return self._event(value)
        except (ValueError, TypeError, RecursionError, UnicodeError, OverflowError):
            self._poison()
            raise _invalid() from None

    def _poison(self):
        self._terminal = True
        self._rate_limit_rejected = False
        self._pending_error_code = None
        self._assistant_message_ids = set()
        self._stream_message_ids = set()
        self._stream_message_id = None
        self._stream_blocks = {}
        self._next_block_index = 0
        self._message_delta_seen = False
        self._text_block_sources = {}
        self._visible_text_chunks = ()
        self._visible_text_bytes = 0
        self._visible_text_messages = {}
        self._session_id = self._observed_session_id = None
        self._tool_ids = {}
        self._finished_tools = set()

    def _emit(self, kind, text='', item_id=None):
        return (AgentEvent(self.run_id, kind, text, item_id),)

    def _end(self, kind, text=''):
        self._terminal = True
        self._rate_limit_rejected = False
        self._pending_error_code = None
        self._tool_ids = {}
        self._finished_tools = set()
        self._assistant_message_ids = set()
        self._stream_message_ids = set()
        self._stream_message_id = None
        self._stream_blocks = {}
        self._next_block_index = 0
        self._message_delta_seen = False
        self._text_block_sources = {}
        self._visible_text_chunks = ()
        self._visible_text_bytes = 0
        self._visible_text_messages = {}
        self._observed_session_id = None
        if kind is not EventKind.COMPLETED:
            self._session_id = None
        return self._emit(kind, text)

    def _text_event(self, value, message_id=None):
        if not value:
            return ()
        encoded = value.encode('utf-8')
        total = self._visible_text_bytes + len(encoded)
        if total > _MAX_RUN_BYTES or len(self._visible_text_chunks) >= _MAX_TEXT_CHUNKS:
            raise _invalid()

        next_chunks = (*self._visible_text_chunks, encoded)
        next_messages = self._visible_text_messages
        if message_id is not None:
            if (message_id not in self._visible_text_messages and
                    len(self._visible_text_messages) >= _MAX_ITEMS):
                raise _invalid()
            message_chunks = self._visible_text_messages.get(message_id, ())
            next_messages = {
                **self._visible_text_messages,
                message_id: (*message_chunks, encoded),
            }

        self._visible_text_chunks = next_chunks
        self._visible_text_bytes = total
        self._visible_text_messages = next_messages
        return self._emit(EventKind.TEXT, value)

    @staticmethod
    def _sequence_starts_with(chunks, value):
        offset = 0
        for chunk in chunks:
            if offset == len(value):
                return True
            end = min(offset + len(chunk), len(value))
            if chunk[:end - offset] != value[offset:end]:
                return False
            offset = end
        return offset == len(value)

    @staticmethod
    def _complete_sequence_prefix_length(chunks, value):
        offset = 0
        for chunk in chunks:
            end = offset + len(chunk)
            if end > len(value) or value[offset:end] != chunk:
                return 0
            offset = end
        return offset

    def _reconcile_result_text(self, value):
        if not value:
            return ''
        encoded = value.encode('utf-8')
        if self._sequence_starts_with(self._visible_text_chunks, encoded):
            return ''
        message_chunks = tuple(self._visible_text_messages.values())
        if any(self._sequence_starts_with(chunks, encoded) for chunks in message_chunks):
            return ''

        matched = self._complete_sequence_prefix_length(self._visible_text_chunks, encoded)
        for chunks in message_chunks:
            matched = max(matched, self._complete_sequence_prefix_length(chunks, encoded))
        if matched:
            return encoded[matched:].decode('utf-8')
        return value
    def _event(self, value):
        kind = value['type']
        if self._pending_error_code is not None and kind != 'result':
            raise _invalid()
        if kind == 'system':
            return self._system(value)
        if not self._initialized:
            raise _invalid()
        if kind == 'assistant':
            return self._assistant(value)
        if kind == 'user':
            return self._user(value)
        if kind == 'stream_event':
            return self._stream(value)
        if kind == 'rate_limit_event':
            return self._rate_limit(value)
        if kind == 'result':
            return self._result(value)
        raise _invalid()

    def _session(self, value):
        if value is None:
            return
        session_id = _opaque_session(value)
        if self._observed_session_id is not None and self._observed_session_id != session_id:
            raise _invalid()
        self._observed_session_id = session_id
        if self._preserve_session_id:
            self._session_id = session_id

    def _system(self, value):
        subtype = _string(value.get('subtype'), maximum=64)
        if subtype == 'api_retry':
            attempt = value.get('attempt')
            if attempt is not None and (type(attempt) is not int or not 1 <= attempt <= 100):
                raise _invalid()
            return ()
        if subtype != 'init' or self._initialized:
            raise _invalid()
        self._session(value.get('session_id'))
        tools = value.get('tools')
        if tools is not None:
            if type(tools) is not list or len(tools) > 32 or any(not isinstance(tool, str) for tool in tools):
                raise _invalid()
            if len(tools) != len(set(tools)):
                raise _invalid()
            if frozenset(tools) != _TOOL_NAMES:
                self._initialized = True
                return self._end(EventKind.BLOCKED, 'CLI 工具配置与已批准范围不一致，已停止。')
        self._initialized = True
        return self._emit(EventKind.STARTED)

    def _assistant(self, value):
        error = value.get('error')
        if error is not None:
            if self._pending_error_code is not None or self._stream_message_id is not None:
                raise _invalid()
            code = _string(error, maximum=64)
            self._pending_error_code = code if code in _TYPED_ERRORS else 'unknown'
            return ()
        if self._stream_message_id is not None:
            raise _invalid()
        self._session(value.get('session_id'))
        message = value.get('message')
        if type(message) is not dict or type(message.get('content')) is not list:
            raise _invalid()
        message_id = identifier(message.get('id'))
        if message_id not in self._assistant_message_ids:
            if len(self._assistant_message_ids) >= _MAX_ITEMS:
                raise _invalid()
            self._assistant_message_ids = self._assistant_message_ids | {message_id}
        if len(message['content']) > _MAX_ITEMS:
            raise _invalid()
        events = []
        for index, block in enumerate(message['content']):
            if type(block) is not dict:
                raise _invalid()
            block_type = _string(block.get('type'), maximum=64)
            if block_type == 'tool_use':
                tool_events = self._tool_start(block, message_id, index)
                events.extend(tool_events)
                if self._terminal:
                    return tuple(events)
            elif block_type in ('text', 'thinking', 'redacted_thinking'):
                payload = block.get('text')
                if payload is not None:
                    payload = _string(payload)
                    if block_type == 'text' and payload:
                        key = (message_id, index)
                        if key not in self._text_block_sources:
                            if len(self._text_block_sources) >= _MAX_ITEMS:
                                raise _invalid()
                            self._text_block_sources = {**self._text_block_sources, key: 'assistant'}
                            events.extend(self._text_event(payload, message_id))
            else:
                return self._end(EventKind.BLOCKED, 'CLI 工具配置与已批准范围不一致，已停止。')
        return tuple(events)

    def _stream(self, value):
        self._session(value.get('session_id'))
        parent_id = value.get('parent_tool_use_id')
        if parent_id is not None:
            _opaque_session(parent_id)
            return self._end(EventKind.BLOCKED, 'CLI 出现未经批准的嵌套 Agent，已停止。')
        event = value.get('event')
        if type(event) is not dict:
            raise _invalid()
        event_type = _string(event.get('type'), maximum=64)
        if event_type == 'message_start':
            message = event.get('message')
            if (self._stream_message_id is not None or type(message) is not dict
                    or self._message_delta_seen):
                raise _invalid()
            message_id = identifier(message.get('id'))
            if message_id in self._stream_message_ids or len(self._stream_message_ids) >= _MAX_ITEMS:
                raise _invalid()
            self._stream_message_ids = self._stream_message_ids | {message_id}
            self._stream_message_id = message_id
            self._stream_blocks = {}
            self._next_block_index = 0
            self._message_delta_seen = False
            return ()
        if event_type == 'message_delta':
            if (self._stream_message_id is None or self._message_delta_seen
                    or self._stream_blocks or type(event.get('delta')) is not dict):
                raise _invalid()
            self._message_delta_seen = True
            return ()
        if event_type == 'message_stop':
            if (self._stream_message_id is None or self._stream_blocks
                    or not self._message_delta_seen):
                raise _invalid()
            self._stream_message_id = None
            self._next_block_index = 0
            self._message_delta_seen = False
            return ()
        if event_type in ('content_block_start', 'content_block_delta', 'content_block_stop'):
            if self._stream_message_id is None or self._message_delta_seen:
                raise _invalid()
            if event_type == 'content_block_start':
                return self._stream_block_start(event)
            if event_type == 'content_block_delta':
                return self._stream_block_delta(event)
            return self._stream_block_stop(event)
        raise _invalid()

    def _stream_block_start(self, event):
        index = self._index(event.get('index'))
        if index != self._next_block_index or index in self._stream_blocks:
            raise _invalid()
        block = event.get('content_block')
        if type(block) is not dict:
            raise _invalid()
        block_type = _string(block.get('type'), maximum=64)
        if block_type == 'tool_use':
            events = self._tool_start(block, self._stream_message_id, index)
            if self._terminal:
                return events
            block_data = (block_type, identifier(block.get('id')))
        elif block_type in ('text', 'thinking', 'redacted_thinking'):
            payload_key = 'text' if block_type == 'text' else 'thinking'
            if payload_key in block:
                _string(block[payload_key])
            block_data = (block_type, None)
            events = ()
        else:
            return self._end(EventKind.BLOCKED, 'CLI 工具配置与已批准范围不一致，已停止。')
        if len(self._stream_blocks) >= _MAX_ITEMS:
            raise _invalid()
        self._stream_blocks = {**self._stream_blocks, index: block_data}
        self._next_block_index += 1
        return events

    def _stream_block_delta(self, event):
        index = self._index(event.get('index'))
        block = self._stream_blocks.get(index)
        delta = event.get('delta')
        if block is None or type(delta) is not dict:
            raise _invalid()
        block_type = block[0]
        delta_type = _string(delta.get('type'), maximum=64)
        expected = {
            'text': frozenset(('text_delta',)),
            'tool_use': frozenset(('input_json_delta',)),
            'thinking': frozenset(('thinking_delta', 'signature_delta')),
            'redacted_thinking': frozenset(),
        }
        if delta_type not in expected[block_type]:
            raise _invalid()
        if delta_type == 'text_delta':
            text = _string(delta.get('text'))
            key = (self._stream_message_id, index)
            source = self._text_block_sources.get(key)
            if source == 'assistant':
                return ()
            if source is None:
                if len(self._text_block_sources) >= _MAX_ITEMS:
                    raise _invalid()
                self._text_block_sources = {**self._text_block_sources, key: 'stream'}
            return self._text_event(text, self._stream_message_id)
        if delta_type == 'input_json_delta':
            _string(delta.get('partial_json'))
            return ()
        if delta_type == 'thinking_delta':
            _string(delta.get('thinking'))
            return ()
        _string(delta.get('signature'))
        return ()

    def _stream_block_stop(self, event):
        index = self._index(event.get('index'))
        if index not in self._stream_blocks:
            raise _invalid()
        self._stream_blocks = {key: value for key, value in self._stream_blocks.items() if key != index}
        return ()

    @staticmethod
    def _index(value):
        if type(value) is not int or not 0 <= value <= 65535:
            raise _invalid()
        return value

    def _tool_start(self, block, message_id, index):
        tool_id = identifier(block.get('id'))
        name = _string(block.get('name'), maximum=64)
        if name not in _TOOL_NAMES:
            return self._end(EventKind.BLOCKED, 'CLI 工具配置与已批准范围不一致，已停止。')
        if tool_id in self._finished_tools:
            raise _invalid()
        origin = (message_id, index, 'tool_use')
        previous = self._tool_ids.get(tool_id)
        if previous is not None:
            if previous != (name, origin):
                raise _invalid()
            return ()
        if len(self._tool_ids) + len(self._finished_tools) >= _MAX_ITEMS:
            raise _invalid()
        self._tool_ids = {**self._tool_ids, tool_id: (name, origin)}
        # Input is validated for size by the JSON bounds, then discarded.
        if 'input' in block:
            _bounded_value(block['input'])
        return self._emit(EventKind.TOOL_STARTED, name, tool_id)

    def _user(self, value):
        if self._stream_message_id is not None:
            raise _invalid()
        self._session(value.get('session_id'))
        message = value.get('message')
        if type(message) is not dict or type(message.get('content')) is not list:
            raise _invalid()
        events = []
        for block in message['content']:
            if type(block) is not dict:
                raise _invalid()
            block_type = _string(block.get('type'), maximum=64)
            if block_type == 'text':
                _string(block.get('text'))
                continue
            if block_type != 'tool_result':
                return self._end(EventKind.BLOCKED, 'CLI 返回了未经批准的工具数据，已停止。')
            tool_id = identifier(block.get('tool_use_id'))
            failed = block.get('is_error', False)
            if type(failed) is not bool or tool_id not in self._tool_ids:
                raise _invalid()
            self._tool_ids = {key: name for key, name in self._tool_ids.items() if key != tool_id}
            self._finished_tools = self._finished_tools | {tool_id}
            events.append(AgentEvent(self.run_id, EventKind.TOOL_FINISHED,
                                     '工具返回失败。' if failed else '工具已返回。', tool_id))
        return tuple(events)

    def _rate_limit(self, value):
        info = value.get('rate_limit_info')
        if type(info) is not dict:
            raise _invalid()
        status = _string(info.get('status'), maximum=64)
        if status == 'allowed_warning':
            return ()
        if status == 'rejected':
            self._rate_limit_rejected = True
            return ()
        raise _invalid()

    def _result(self, value):
        if self._stream_message_id is not None or self._stream_blocks:
            raise _invalid()
        self._session(value.get('session_id'))
        subtype = _string(value.get('subtype'), maximum=64)
        is_error = value.get('is_error')
        turns = value.get('num_turns')
        if type(is_error) is not bool or type(turns) is not int or not 0 <= turns <= MAX_TURNS:
            raise _invalid()
        denials = value.get('permission_denials', [])
        errors = value.get('errors', [])
        if type(denials) is not list or len(denials) > 32 or type(errors) is not list or len(errors) > 32:
            raise _invalid()
        if any(type(denial) is not dict for denial in denials):
            raise _invalid()
        for denial in denials:
            tool_name = denial.get('tool_name')
            tool_id = denial.get('tool_use_id')
            if tool_name is not None and (not isinstance(tool_name, str) or tool_name not in _TOOL_NAMES):
                raise _invalid()
            if tool_id is not None:
                identifier(tool_id)
        if any(type(error) is not str for error in errors) or len(errors) != len(set(errors)):
            raise _invalid()
        status = value.get('api_error_status')
        if status is not None and (type(status) is not int or not 100 <= status <= 599):
            raise _invalid()
        terminal_reason = value.get('terminal_reason')
        if terminal_reason is not None:
            _string(terminal_reason, maximum=64)
        if not is_error:
            if (self._pending_error_code is not None or denials or errors or self._rate_limit_rejected
                    or terminal_reason in ('aborted_streaming', 'aborted_tools') or subtype != 'success'):
                raise _invalid()
            if self._tool_ids:
                raise _invalid()
            final_text = value.get('result')
            if final_text is not None:
                final_text = _string(final_text)
            text_delta = self._reconcile_result_text(final_text or '')
            text_events = self._text_event(text_delta)
            return (*text_events, *self._end(EventKind.COMPLETED))
        message = self._classify_error(denials, errors, status, terminal_reason,
                                       self._rate_limit_rejected, self._pending_error_code)
        return self._end(EventKind.FAILED, message)

    @staticmethod
    def _classify_error(denials, errors, status, terminal_reason, rejected_limit, pending_code):
        signals = {error for error in errors if error in _TYPED_ERRORS}
        if len(signals) > 1:
            raise _invalid()
        if denials:
            signals.add('permission')
        if rejected_limit:
            signals.add('rate_limit')
        if terminal_reason in ('aborted_streaming', 'aborted_tools'):
            signals.add('interrupted')
        status_kind = {401: 'authentication_failed', 402: 'billing_error', 429: 'rate_limit'}.get(status)
        if status_kind is not None:
            signals.add(status_kind)
        if pending_code in _TYPED_ERRORS:
            signals.add(pending_code)
        if len(signals) > 1:
            raise _invalid()
        code = next(iter(signals), None)
        return _ERROR_MESSAGES.get(code, 'CLI 执行失败。')

class ClaudeCodeCliAdapter:
    """Production stays unavailable until fresh exact-scope evidence exists."""
    def __init__(self, *, executable: str | None = None, version: str | None = None):
        for value, bound in ((executable, 4096), (version, 100)):
            if value is not None and (not isinstance(value, str) or not value or len(value) > bound
                                      or any(ord(char) < 32 for char in value)):
                raise ValueError('CLI 路径或版本提示无效。')
        self._executable, self._version = executable, version

    def probe(self) -> ProviderStatus:
        # Display hints only: no PATH, version, login-store or auth inspection.
        return assess_provider(Provider.CLAUDE, executable=self._executable, version=self._version,
                               authenticated=None, capability_report=None)

    def capabilities(self) -> AdapterCapabilities:
        return AdapterCapabilities()

    def stream(self, request: AgentRequest):
        run_id = identifier(getattr(request, 'run_id', 'invalid'))
        if not isinstance(request, AgentRequest) or request.scope.metadata.provider is not Provider.CLAUDE:
            yield AgentEvent(run_id, EventKind.FAILED, '运行请求与所选 Agent 不一致。')
            return
        if request.scope.version != PROTOCOL_VERSION:
            yield AgentEvent(run_id, EventKind.FAILED, 'CLI 版本未经过协议检查，需要重新验证。')
            return
        yield AgentEvent(run_id, EventKind.BLOCKED,
                         'Claude CLI 尚无本次精确范围的可信实时检查，暂不能自动运行。')

    def cancel(self, run_id: str) -> None:
        # No production provider process can be active in this implementation.
        identifier(run_id)
