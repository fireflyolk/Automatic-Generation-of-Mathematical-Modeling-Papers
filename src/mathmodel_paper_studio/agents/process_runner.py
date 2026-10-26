"""Bounded worker-thread JSONL supervision behind a fresh scope grant.

Run stream() on an application worker, never on the Qt thread. This supervisor
adds lifecycle/resource controls, not filesystem or network confinement.
"""
from dataclasses import dataclass, fields, replace
import math
import os
from pathlib import Path
import queue
import re
import threading
import time
from typing import Callable, Iterable

from mathmodel_paper_studio.agents.protocol import AgentRequest, AgentEvent, EventKind, TERMINAL_KINDS
from mathmodel_paper_studio.agents.windows_job import NativeJobProcess, NativeProcessError, validate_argv
from mathmodel_paper_studio.bootstrap.paths import AppPaths, validate_managed_path
from mathmodel_paper_studio.security.path_guard import native_executable
from mathmodel_paper_studio.security.sandbox_gate import ScopeGate, TrustedGrant


@dataclass(frozen=True)
class ProcessLimits:
    wall_seconds: float = 120
    output_bytes: int = 8 * 1024 * 1024
    queue_bytes: int = 256 * 1024
    line_bytes: int = 65536
    event_bytes: int = 65536
    max_events: int = 4096
    diagnostic_bytes: int = 8192
    stdin_bytes: int = 65536
    process_memory_bytes: int = 512 * 1024 * 1024
    job_memory_bytes: int = 1024 * 1024 * 1024
    active_processes: int = 16

    def __post_init__(self):
        if isinstance(self.wall_seconds, bool) or not isinstance(self.wall_seconds, (int, float)) or not math.isfinite(self.wall_seconds) or not 0 < self.wall_seconds <= 3600:
            raise ValueError('执行时限必须为有限正数且不超过一小时。')
        for field in fields(self):
            if field.name != 'wall_seconds':
                value = getattr(self, field.name)
                if type(value) is not int or not 0 < value <= 16 * 1024 ** 3:
                    raise ValueError('进程和输出限制必须为有限正整数。')
        bounds = {'output_bytes': 64 * 1024 ** 2, 'queue_bytes': 1024 ** 2, 'line_bytes': 65536,
                  'event_bytes': 65536, 'max_events': 10000, 'diagnostic_bytes': 65536,
                  'stdin_bytes': 1024 ** 2, 'active_processes': 64}
        if any(getattr(self, name) > maximum for name, maximum in bounds.items()):
            raise ValueError('输出、事件或进程数量限制过大。')
        if self.process_memory_bytes > self.job_memory_bytes:
            raise ValueError('单进程内存限制不能超过整个 Job 的限制。')


def _environment(paths, executable, source, login):
    paths.validate()
    executable = native_executable(executable)
    result = {}
    for name in (('SystemRoot', 'WINDIR', 'HOME', 'USERPROFILE', 'APPDATA', 'LOCALAPPDATA') if login else ('SystemRoot', 'WINDIR')):
        value = source.get(name)
        if isinstance(value, str) and value and '\0' not in value:
            result[name] = value
    result['PATH'] = str(executable.parent)
    result.update({'TEMP': str(paths.temp), 'TMP': str(paths.temp), 'TMPDIR': str(paths.temp),
                   'PYTHONPYCACHEPREFIX': str(paths.cache / 'pycache'), 'PYTHONDONTWRITEBYTECODE': '1',
                   'PYTHONIOENCODING': 'utf-8', 'PYTHONUTF8': '1',
                   'XDG_CACHE_HOME': str(paths.cache / 'xdg'), 'PIP_CACHE_DIR': str(paths.cache / 'pip'),
                   'UV_CACHE_DIR': str(paths.cache / 'uv'), 'MPLCONFIGDIR': str(paths.cache / 'matplotlib')})
    for name in ('TEMP', 'TMP', 'TMPDIR', 'PYTHONPYCACHEPREFIX', 'XDG_CACHE_HOME', 'PIP_CACHE_DIR', 'UV_CACHE_DIR', 'MPLCONFIGDIR'):
        validate_managed_path(result[name])
    return result


def native_cli_environment(paths: AppPaths, executable: Path, source=None) -> dict[str, str]:
    """Preserve native login discovery paths, never credential/token contents."""
    return _environment(paths, executable, os.environ if source is None else source, True)


def generated_environment(paths: AppPaths, executable: Path, source=None) -> dict[str, str]:
    """Environment policy only; generated execution remains blocked by ScopeGate."""
    return _environment(paths, executable, os.environ if source is None else source, False)


class _Redactor:
    def __init__(self, prompt, secrets, *, candidate_bound=256):
        hidden = {part for value in (prompt, *secrets) for part in (value, *value.splitlines()) if part}
        self.hidden = tuple(sorted(hidden, key=len, reverse=True))
        self.continuation = {}
        self.candidates = {}
        markers = ('sk-', 'sk_', 'ghp-', 'ghp_', 'github_pat-', 'github_pat_',
                   'AKIA-', 'AKIA_', 'Bearer ', 'token=', 'token:',
                   'password=', 'password:', 'api_key=', 'api_key:', 'api-key=', 'api-key:', 'apikey=', 'apikey:', '\\\\')
        markers += tuple(letter + separator for letter in 'ABCDEFGHIJKLMNOPQRSTUVWXYZ' for separator in (':\\', ':/'))
        self.prefixes = frozenset(marker[:length].casefold() for marker in markers for length in range(1, len(marker)))
        if type(candidate_bound) is not int or not 1 <= candidate_bound <= 256:
            raise ValueError('invalid candidate bound')
        self.candidate_bound = candidate_bound
        self.fixed_prefix_bound = max(map(len, self.prefixes))
        # A known prompt or credential can be longer than the generic marker
        # carry bound. Matching its own prefix is still bounded by the
        # validated literal size (prompts and provider keys have input limits).
        self.literal_candidate_bound = min(max(map(len, self.hidden), default=0), 1024 * 1024) + self.fixed_prefix_bound
        self.partial_marker = re.compile(r'(?i)\b(?:token|password|api[_-]?key)\s*$')
        self.pattern = re.compile(r'(?i)(?:\b(?:sk|ghp|github_pat|AKIA)[-_][A-Za-z0-9_-]*|\b(?:bearer\s+)[A-Za-z0-9._-]*|\b(?:token|password|api[_-]?key)\s*[:=]\s*[^\s,;]*|[A-Z]:[\\/][^\s"<>|]*|\\\\[^\s"<>|]+)')

    def clean(self, value, channel='events', *, final=False):
        value = self.candidates.pop(channel, '') + value
        continuation = self.continuation.pop(channel, None)
        if continuation:
            body, awaiting_value = continuation
            whitespace = re.match(r'\s*', value).end() if awaiting_value else 0
            count = whitespace + re.match(body, value[whitespace:]).end()
            if count == len(value):
                self.continuation[channel] = (body, awaiting_value and count == whitespace)
            value = '[已隐藏]' + value[count:]
        for hidden in self.hidden:
            value = value.replace(hidden, '[已隐藏]')
        # Delay a possible trailing literal prefix until another chunk confirms
        # the full secret; mismatches then release the original safe text.
        value = self._redact_pattern(value, channel)
        return value if final else self._carry_candidate(value, channel)

    def _redact_pattern(self, value, channel):
        matches = tuple(self.pattern.finditer(value))
        if matches and matches[-1].end() == len(value):
            fragment = matches[-1].group()
            assignment = re.match(r'(?i)\b(?:token|password|api[_-]?key)\s*[:=]\s*', fragment)
            bearer = re.match(r'(?i)\bbearer\s+', fragment)
            if assignment:
                state = (r'[^\s,;]*', assignment.end() == len(fragment))
            elif bearer:
                state = (r'[A-Za-z0-9._-]*', bearer.end() == len(fragment))
            elif re.match(r'(?i)\b(?:sk|ghp|github_pat|AKIA)[-_]', fragment):
                state = (r'[A-Za-z0-9_-]*', False)
            else:
                state = (r'[^\s"<>|]*', False)
            self.continuation[channel] = state
        return self.pattern.sub('[已隐藏]', value)

    def _carry_candidate(self, value, channel):
        partial = self.partial_marker.search(value)
        keep = max(0, partial.start() - 1) if partial else None
        for count in range(min(len(value), self.fixed_prefix_bound), 0, -1):
            if value[-count:].casefold() in self.prefixes:
                candidate_start = max(0, len(value) - count - 1)
                keep = candidate_start if keep is None else min(keep, candidate_start)
                break
        for hidden in self.hidden:
            maximum = min(len(value), len(hidden) - 1)
            count = next((n for n in range(maximum, 0, -1) if value.endswith(hidden[:n])), 0)
            if count:
                candidate_start = len(value) - count
                keep = candidate_start if keep is None else min(keep, candidate_start)
        if keep is not None:
            candidate = value[keep:]
            if len(candidate) > max(self.candidate_bound, self.literal_candidate_bound):
                # Fail closed instead of retaining unbounded whitespace or
                # releasing a possible sensitive label before its separator.
                raise ValueError('redaction candidate exceeds bound')
            self.candidates[channel] = candidate
            value = value[:keep]
        return value


def _utf8_chunks(value, byte_limit):
    """Split sanitized text without breaking Unicode characters or event bounds."""
    if not value:
        return ()
    chunks = []
    start = size = 0
    for index, character in enumerate(value):
        character_size = len(character.encode('utf-8'))
        if character_size > byte_limit:
            raise ValueError('event byte limit is too small for text encoding')
        if size and size + character_size > byte_limit:
            chunks.append(value[start:index])
            start, size = index, 0
        size += character_size
    chunks.append(value[start:])
    return tuple(chunks)


class _Output:
    def __init__(self, process, limits):
        self.process, self.limits = process, limits
        self.chunk_size = min(4096, limits.queue_bytes)
        self.queue = queue.Queue(maxsize=max(1, limits.queue_bytes // self.chunk_size))
        self.stop = threading.Event()
        self.failed = threading.Event()
        self.error = None
        self.total = 0
        self.lock = threading.Lock()
        self.workers = []

    def fail(self, message):
        with self.lock:
            if self.error is None:
                self.error = message
        self.failed.set()

    def start(self, stdin):
        for channel in ('stdout', 'stderr'):
            worker = threading.Thread(target=self.read, args=(channel,), daemon=True)
            self.workers.append(worker)
            worker.start()
        writer = threading.Thread(target=self.write, args=(stdin,), daemon=True)
        self.workers.append(writer)
        writer.start()

    def write(self, data):
        try:
            self.process.write_stdin(data)
        except NativeProcessError:
            if not self.stop.is_set():
                self.fail('CLI 标准输入写入失败。')
        finally:
            self.process.finish_stdin()

    def read(self, channel):
        try:
            while not self.stop.is_set():
                data = self.process.read(channel, self.chunk_size)
                if data is None:
                    self.stop.wait(.01)
                    continue
                with self.lock:
                    self.total += len(data)
                    overflow = self.total > self.limits.output_bytes
                if overflow:
                    self.fail('CLI 输出超过本次上限，已停止。')
                    return
                try:
                    self.queue.put_nowait((channel, data))
                except queue.Full:
                    self.fail('CLI 输出队列超过上限，已停止。')
                    return
                if data == b'':
                    return
        except NativeProcessError:
            self.fail('CLI 输出读取失败。')

    def close(self):
        self.stop.set()
        try:
            self.process.terminate()
            writer = self.workers[-1] if self.workers else None
            if writer is not None:
                writer.join(.15)
                if writer.is_alive():
                    self.process.cancel_writer(writer)
            for worker in self.workers:
                worker.join(.5)
            if any(worker.is_alive() for worker in self.workers):
                raise NativeProcessError('CLI 管道未在清理时限内停止。')
        finally:
            self.process.close()


class ProcessRunner:
    def __init__(self, gate: ScopeGate, paths: AppPaths, limits: ProcessLimits = ProcessLimits(), *,
                 environment_factory=None):
        self.gate, self.paths, self.limits = gate, paths, limits
        self._environment_factory = environment_factory or native_cli_environment
        self._lock = threading.Lock()
        self._active = {}
        self._diagnostics = {}

    def cancel(self, run_id: str) -> None:
        with self._lock:
            signal = self._active.get(run_id)
            if signal is not None:
                signal.set()

    def diagnostic(self, run_id: str) -> str:
        with self._lock:
            return self._diagnostics.get(run_id, '')

    def stream(self, request: AgentRequest, grant: TrustedGrant, arguments: tuple[str, ...],
               parse_line: Callable[[str], Iterable[AgentEvent]], *, stdin: bytes | None = None,
               redact_literals: tuple[str, ...] = ()) -> Iterable[AgentEvent]:
        """The trusted adapter owns argv/parser; model content belongs in stdin.

        No raw stdout, stderr, argv, prompt or environment is persisted here.
        Terminal events are withheld until clean process exit and drained pipes.
        Closing this iterator also terminates its complete job.
        """
        if type(request) is not AgentRequest or type(self.limits) is not ProcessLimits:
            raise ValueError('请求或进程限制类型无效。')
        self.gate.require(grant, request.scope)
        if validate_argv(arguments) != request.scope.launch_arguments:
            raise ValueError('启动参数与经过实时检查的范围不一致。')
        data = request.prompt.encode('utf-8') if stdin is None else stdin
        if not isinstance(data, bytes) or len(data) > self.limits.stdin_bytes:
            raise ValueError('标准输入超过本次上限。')
        if not isinstance(redact_literals, tuple) or len(redact_literals) > 64 or any(not isinstance(s, str) or len(s) > 65536 for s in redact_literals):
            raise ValueError('脱敏规则无效或过长。')
        signal = threading.Event()
        with self._lock:
            if request.run_id in self._active or len(self._active) >= 8:
                raise ValueError('运行标识符已占用或并发任务达到上限。')
            self._active[request.run_id] = signal
        process, output, terminal = None, None, None
        diagnostic = ''
        redactor = _Redactor(request.prompt, redact_literals, candidate_bound=min(256, self.limits.line_bytes, self.limits.event_bytes))
        try:
            environment = self._environment_factory(self.paths, request.scope.executable)
            self.gate.require(grant, request.scope)
            process = NativeJobProcess(request.scope.executable, arguments, request.scope.project_root, environment, self.limits)
            output = _Output(process, self.limits)
            output.start(data)
            state = {'buffers': {'stdout': b'', 'stderr': b''}, 'eof': set(), 'terminal': None, 'events': 0}
            deadline = time.monotonic() + self.limits.wall_seconds
            for event, diagnostic in self._consume(request, process, output, signal, deadline, state, parse_line, redactor):
                if event.kind in TERMINAL_KINDS:
                    terminal = event
                else:
                    yield event
        except NativeProcessError:
            terminal = AgentEvent(request.run_id, EventKind.FAILED, 'CLI 受控进程启动或清理失败，已停止。')
        finally:
            try:
                if output is not None:
                    output.close()
                elif process is not None:
                    process.close()
            except NativeProcessError:
                terminal = AgentEvent(request.run_id, EventKind.FAILED, 'CLI 进程资源未能完整清理，运行失败。')
            finally:
                with self._lock:
                    self._active.pop(request.run_id, None)
                    self._diagnostics[request.run_id] = diagnostic
                    while len(self._diagnostics) > 16:
                        self._diagnostics.pop(next(iter(self._diagnostics)))
        if terminal is not None:
            yield terminal

    def _consume(self, request, process, output, signal, deadline, state, parse_line, redactor):
        diagnostic = ''
        diagnostic_bytes = 0
        while True:
            failure = ('已取消。' if signal.is_set() else
                       '执行超时，已停止。' if time.monotonic() >= deadline else output.error)
            if failure:
                process.terminate()
                yield AgentEvent(request.run_id, EventKind.CANCELLED if signal.is_set() else EventKind.FAILED, failure), diagnostic
                return
            try:
                channel, data = output.queue.get(timeout=.02)
            except queue.Empty:
                if len(state['eof']) == 2 and process.poll() is not None:
                    break
                continue
            buffer = state['buffers'][channel] + data
            if not data:
                state['eof'].add(channel)
            lines = buffer.split(b'\n')
            tail = lines.pop()
            if not data and tail:
                lines.append(tail)
                tail = b''
            state['buffers'][channel] = tail
            if len(tail) > self.limits.line_bytes or any(len(line) > self.limits.line_bytes for line in lines):
                output.fail('CLI 事件行超过上限，已停止。')
                continue
            for line in lines:
                try:
                    text = line.rstrip(b'\r').decode('utf-8', errors='strict')
                    if channel == 'stderr':
                        diagnostic_bytes += len(line) + 1
                        diagnostic = ('[已隐藏] stderr 原文（' + str(diagnostic_bytes) + ' 字节）。')[:self.limits.diagnostic_bytes // 4]
                        continue
                    for event in self._parse_events(request, text, parse_line, state, redactor):
                        yield event, diagnostic
                except Exception:
                    output.fail('CLI 事件格式无效或不完整，已停止。')
                    break
        if signal.is_set() or time.monotonic() >= deadline:
            yield AgentEvent(request.run_id, EventKind.CANCELLED if signal.is_set() else EventKind.FAILED,
                             '已取消。' if signal.is_set() else '执行超时，已停止。'), diagnostic
        elif process.poll() != 0:
            yield AgentEvent(request.run_id, EventKind.FAILED, 'CLI 执行失败。'), diagnostic
        elif state['terminal'] is None:
            yield AgentEvent(request.run_id, EventKind.FAILED, 'CLI 事件格式无效或缺少结束事件。'), diagnostic
        else:
            yield state['terminal'], diagnostic

    def _parse_events(self, request, text, parse_line, state, redactor):
        for value in parse_line(text):
            if type(value) is not AgentEvent or value.run_id != request.run_id or state['terminal'] is not None:
                raise ValueError('invalid event sequence')
            event = AgentEvent(value.run_id, value.kind, value.text, value.item_id)
            state['events'] += 1
            if state['events'] > self.limits.max_events or len(event.text.encode('utf-8')) > self.limits.event_bytes:
                raise ValueError('event limit')
            terminal = event.kind in TERMINAL_KINDS
            sanitized = redactor.clean(event.text, final=terminal)
            if terminal:
                if sanitized and (not event.text or len(sanitized.encode('utf-8')) > self.limits.event_bytes):
                    # Flush withheld text in bounded TEXT events rather than
                    # exceeding AgentEvent's protocol limit on a terminal event.
                    chunks = _utf8_chunks(sanitized, self.limits.event_bytes)
                    state['events'] += len(chunks)
                    if state['events'] > self.limits.max_events:
                        raise ValueError('event limit')
                    for chunk in chunks:
                        yield AgentEvent(event.run_id, EventKind.TEXT, chunk)
                    sanitized = ''
                event = replace(event, text=sanitized)
                state['terminal'] = event
            else:
                chunks = _utf8_chunks(sanitized, self.limits.event_bytes)
                if len(chunks) > 1:
                    if event.kind is EventKind.TEXT:
                        state['events'] += len(chunks) - 1
                        status_event = None
                    else:
                        # A body flush must not duplicate tool/status events.
                        state['events'] += len(chunks)
                        status_event = replace(event, text='')
                    if state['events'] > self.limits.max_events:
                        raise ValueError('event limit')
                    for chunk in chunks:
                        if status_event is None:
                            yield replace(event, text=chunk)
                        else:
                            yield AgentEvent(event.run_id, EventKind.TEXT, chunk, event.item_id)
                    if status_event is not None:
                        yield status_event
                else:
                    yield replace(event, text=sanitized)

