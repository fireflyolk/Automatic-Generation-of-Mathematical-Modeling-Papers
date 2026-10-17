"""Native Windows lifecycle/resource control. This is NOT a security sandbox.

JOB_LIST assigns before the initial thread runs; HANDLE_LIST limits inheritance.
Only the controller owns the job. No ordinary-process fallback is available.
Caller stops/joins I/O workers before close(); reads poll rather than block.
"""
import ctypes as c
from ctypes import wintypes as w
import os
import subprocess
import threading
import time

from mathmodel_paper_studio.bootstrap.paths import validate_managed_path
from mathmodel_paper_studio.security.path_guard import native_executable

SIZE_T = c.c_size_t
HANDLE = w.HANDLE


class SecurityAttributes(c.Structure):
    _fields_ = [('length', w.DWORD), ('descriptor', c.c_void_p), ('inherit', w.BOOL)]


class StartupInfo(c.Structure):
    _fields_ = [('cb', w.DWORD), ('reserved', w.LPWSTR), ('desktop', w.LPWSTR), ('title', w.LPWSTR),
                ('x', w.DWORD), ('y', w.DWORD), ('xsize', w.DWORD), ('ysize', w.DWORD),
                ('xchars', w.DWORD), ('ychars', w.DWORD), ('fill', w.DWORD), ('flags', w.DWORD),
                ('show', w.WORD), ('reserved_size', w.WORD), ('reserved_bytes', c.c_void_p),
                ('stdin', HANDLE), ('stdout', HANDLE), ('stderr', HANDLE)]


class StartupInfoEx(c.Structure):
    _fields_ = [('startup', StartupInfo), ('attributes', c.c_void_p)]


class ProcessInformation(c.Structure):
    _fields_ = [('process', HANDLE), ('thread', HANDLE), ('pid', w.DWORD), ('tid', w.DWORD)]


class BasicLimits(c.Structure):
    _fields_ = [('process_time', c.c_longlong), ('job_time', c.c_longlong), ('flags', w.DWORD),
                ('min_working_set', SIZE_T), ('max_working_set', SIZE_T), ('active_processes', w.DWORD),
                ('affinity', SIZE_T), ('priority', w.DWORD), ('scheduling', w.DWORD)]


class Accounting(c.Structure):
    _fields_ = [(name, c.c_longlong) for name in ('user', 'kernel', 'period_user', 'period_kernel')] + [(name, w.DWORD) for name in ('faults', 'total', 'active', 'terminated')]


class IoCounters(c.Structure):
    _fields_ = [(name, c.c_ulonglong) for name in ('read_ops', 'write_ops', 'other_ops', 'read_bytes', 'write_bytes', 'other_bytes')]


class ExtendedLimits(c.Structure):
    _fields_ = [('basic', BasicLimits), ('io', IoCounters), ('process_memory', SIZE_T),
                ('job_memory', SIZE_T), ('peak_process_memory', SIZE_T), ('peak_job_memory', SIZE_T)]


class NativeProcessError(RuntimeError):
    pass


def _api():
    if os.name != 'nt':
        raise NativeProcessError('此执行器需要 Windows Job Object。')
    api = c.WinDLL('kernel32', use_last_error=True)
    signatures = {
        'CreateJobObjectW': ([c.c_void_p, w.LPCWSTR], HANDLE),
        'SetInformationJobObject': ([HANDLE, c.c_int, c.c_void_p, w.DWORD], w.BOOL),
        'InitializeProcThreadAttributeList': ([c.c_void_p, w.DWORD, w.DWORD, c.POINTER(SIZE_T)], w.BOOL),
        'UpdateProcThreadAttribute': ([c.c_void_p, w.DWORD, SIZE_T, c.c_void_p, SIZE_T, c.c_void_p, c.c_void_p], w.BOOL),
        'DeleteProcThreadAttributeList': ([c.c_void_p], None),
        'QueryInformationJobObject': ([HANDLE, c.c_int, c.c_void_p, w.DWORD, c.c_void_p], w.BOOL),
        'CreatePipe': ([c.POINTER(HANDLE), c.POINTER(HANDLE), c.POINTER(SecurityAttributes), w.DWORD], w.BOOL),
        'SetHandleInformation': ([HANDLE, w.DWORD, w.DWORD], w.BOOL),
        'CreateProcessW': ([w.LPCWSTR, w.LPWSTR, c.c_void_p, c.c_void_p, w.BOOL, w.DWORD, c.c_void_p,
                            w.LPCWSTR, c.POINTER(StartupInfoEx), c.POINTER(ProcessInformation)], w.BOOL),
        'CloseHandle': ([HANDLE], w.BOOL),
        'TerminateJobObject': ([HANDLE, w.UINT], w.BOOL),
        'WaitForSingleObject': ([HANDLE, w.DWORD], w.DWORD),
        'GetExitCodeProcess': ([HANDLE, c.POINTER(w.DWORD)], w.BOOL),
        'PeekNamedPipe': ([HANDLE, c.c_void_p, w.DWORD, c.c_void_p, c.POINTER(w.DWORD), c.c_void_p], w.BOOL),
        'ReadFile': ([HANDLE, c.c_void_p, w.DWORD, c.POINTER(w.DWORD), c.c_void_p], w.BOOL),
        'WriteFile': ([HANDLE, c.c_void_p, w.DWORD, c.POINTER(w.DWORD), c.c_void_p], w.BOOL),
        'OpenProcess': ([w.DWORD, w.BOOL, w.DWORD], HANDLE),
        'OpenThread': ([w.DWORD, w.BOOL, w.DWORD], HANDLE),
        'CancelSynchronousIo': ([HANDLE], w.BOOL),
        'IsProcessInJob': ([HANDLE, HANDLE, c.POINTER(w.BOOL)], w.BOOL),
    }
    for name, (arguments, result) in signatures.items():
        function = getattr(api, name)
        function.argtypes, function.restype = arguments, result
    return api


def _check(result, operation):
    if not result:
        error = c.get_last_error()
        raise NativeProcessError(f'{operation}失败（Windows 错误 {error}）。')
    return result


def validate_argv(arguments):
    if not isinstance(arguments, (tuple, list)) or len(arguments) > 256:
        raise ValueError('启动参数必须为有界字符串序列。')
    if any(not isinstance(value, str) or '\0' in value for value in arguments):
        raise ValueError('启动参数类型或字符无效。')
    if sum(len(value) + 1 for value in arguments) > 24000:
        raise ValueError('启动参数超过长度上限。')
    return tuple(arguments)


def environment_block(environment):
    if not isinstance(environment, dict) or not environment or len(environment) > 64:
        raise ValueError('必须传入明确的最小环境。')
    for key, value in environment.items():
        if not isinstance(key, str) or not key or '=' in key or '\0' in key or not isinstance(value, str) or '\0' in value:
            raise ValueError('进程环境格式无效。')
    if len({key.casefold() for key in environment}) != len(environment):
        raise ValueError('进程环境变量不能重复。')
    block = '\0'.join(f'{key}={value}' for key, value in sorted(environment.items(), key=lambda pair: pair[0].casefold())) + '\0\0'
    if len(block) > 32767:
        raise ValueError('进程环境超过长度上限。')
    return c.create_unicode_buffer(block)


class NativeJobProcess:
    def __init__(self, executable, arguments, cwd, environment, limits):
        self.api = _api()
        self._lock = threading.RLock()
        self._handles = {}
        self._terminated = False
        self.pid = None
        executable = native_executable(executable)
        cwd = validate_managed_path(cwd)
        if not cwd.is_dir():
            raise ValueError('进程工作目录不存在。')
        arguments = validate_argv(arguments)
        command = subprocess.list2cmdline([str(executable), *arguments])
        if len(command) >= 32767:
            raise ValueError('进程命令行超过上限。')
        block = environment_block(environment)
        try:
            self._create_job(limits)
            child_handles = self._create_pipes()
            self._create_process(executable, command, cwd, block, child_handles)
        except BaseException:
            self.close()
            raise
        finally:
            for name in ('stdin_child', 'stdout_child', 'stderr_child'):
                self._close_handle(name)

    def _create_job(self, limits):
        job = _check(self.api.CreateJobObjectW(None, None), '创建 Job')
        self._handles['job'] = job
        info = ExtendedLimits()
        info.basic.flags = 0x2000 | 0x0100 | 0x0200 | 0x0008
        info.basic.active_processes = limits.active_processes
        info.process_memory = limits.process_memory_bytes
        info.job_memory = limits.job_memory_bytes
        _check(self.api.SetInformationJobObject(job, 9, c.byref(info), c.sizeof(info)), '设置 Job 限制')

    def _create_pipes(self):
        attributes = SecurityAttributes(c.sizeof(SecurityAttributes), None, True)
        for channel in ('stdin', 'stdout', 'stderr'):
            read, write = HANDLE(), HANDLE()
            _check(self.api.CreatePipe(c.byref(read), c.byref(write), c.byref(attributes), 4096), '创建标准管道')
            parent, child = (write.value, read.value) if channel == 'stdin' else (read.value, write.value)
            self._handles[channel] = parent
            self._handles[channel + '_child'] = child
            _check(self.api.SetHandleInformation(parent, 1, 0), '禁止继承控制管道')
        return (self._handles['stdin_child'], self._handles['stdout_child'], self._handles['stderr_child'])

    def _create_process(self, executable, command, cwd, block, child_handles):
        size = SIZE_T()
        self.api.InitializeProcThreadAttributeList(None, 2, 0, c.byref(size))
        error = c.get_last_error()
        if error != 122 or size.value == 0:
            raise NativeProcessError(f'查询创建属性失败（Windows 错误 {error}）。')
        attributes = c.create_string_buffer(size.value)
        _check(self.api.InitializeProcThreadAttributeList(attributes, 2, 0, c.byref(size)), '初始化创建属性')
        jobs = (HANDLE * 1)(self._handles['job'])
        inherited = (HANDLE * 3)(*child_handles)
        try:
            _check(self.api.UpdateProcThreadAttribute(attributes, 0, 0x0002000D, jobs, c.sizeof(jobs), None, None), '设置原子 Job 关联')
            _check(self.api.UpdateProcThreadAttribute(attributes, 0, 0x00020002, inherited, c.sizeof(inherited), None, None), '设置继承句柄白名单')
            startup = StartupInfoEx()
            startup.startup.cb = c.sizeof(startup)
            startup.startup.flags = 0x100
            startup.startup.stdin, startup.startup.stdout, startup.startup.stderr = child_handles
            startup.attributes = c.cast(attributes, c.c_void_p)
            process = ProcessInformation()
            _check(self.api.CreateProcessW(str(executable), c.create_unicode_buffer(command), None, None, True,
                                           0x00080000 | 0x00000400 | 0x08000000, block, str(cwd),
                                           c.byref(startup), c.byref(process)), '受控创建进程')
            self._handles['process'] = process.process
            self._handles['thread'] = process.thread
            self.pid = process.pid
            member = w.BOOL()
            _check(self.api.IsProcessInJob(process.process, self._handles['job'], c.byref(member)), '检查 Job 关联')
            if not member.value:
                raise NativeProcessError('进程未加入指定 Job，已停止。')
            self._close_handle('thread')
        finally:
            self.api.DeleteProcThreadAttributeList(attributes)

    def read(self, channel, size=4096):
        handle = self._handles[channel]
        available = w.DWORD()
        if not self.api.PeekNamedPipe(handle, None, 0, None, c.byref(available), None):
            error = c.get_last_error()
            if error == 109:
                return b''
            raise NativeProcessError(f'读取管道状态失败（Windows 错误 {error}）。')
        if not available.value:
            return None
        data = c.create_string_buffer(min(size, available.value))
        count = w.DWORD()
        _check(self.api.ReadFile(handle, data, len(data), c.byref(count), None), '读取输出')
        return data.raw[:count.value]

    def write_stdin(self, data):
        for offset in range(0, len(data), 4096):
            count = w.DWORD()
            chunk = data[offset:offset + 4096]
            _check(self.api.WriteFile(self._handles['stdin'], chunk, len(chunk), c.byref(count), None), '写入标准输入')
            if count.value != len(chunk):
                raise NativeProcessError('标准输入未完整写入。')

    def finish_stdin(self):
        with self._lock:
            self._close_handle('stdin')

    def poll(self):
        with self._lock:
            result = self.api.WaitForSingleObject(self._handles['process'], 0)
            if result == 0x102:
                return None
            if result != 0:
                _check(False, '等待进程')
            code = w.DWORD()
            _check(self.api.GetExitCodeProcess(self._handles['process'], c.byref(code)), '获取退出状态')
            return code.value

    def terminate(self):
        with self._lock:
            if not self._terminated and 'job' in self._handles:
                try:
                    self._retain_members()
                finally:
                    _check(self.api.TerminateJobObject(self._handles['job'], 1), '停止整个 Job')
                    self._terminated = True

    def _retain_members(self):
        # Retain synchronization handles before termination: accounting can reach
        # zero slightly before every process object becomes signaled.
        data = c.create_string_buffer(8 + 64 * c.sizeof(SIZE_T))
        _check(self.api.QueryInformationJobObject(self._handles['job'], 3, data, len(data), None), '取得 Job 进程列表')
        count = c.cast(c.addressof(data) + 4, c.POINTER(w.DWORD)).contents.value
        if count > 64:
            raise NativeProcessError('Job 进程列表超过配置上限。')
        pids = (SIZE_T * count).from_address(c.addressof(data) + 8)
        for pid in pids:
            handle = self.api.OpenProcess(0x100000, False, pid)
            if not handle:
                error = c.get_last_error()
                if error == 87:
                    continue
                raise NativeProcessError(f'保留进程停止句柄失败（Windows 错误 {error}）。')
            self._handles['member_' + str(pid)] = handle

    def cancel_writer(self, worker):
        if worker.is_alive() and worker.native_id:
            handle = _check(self.api.OpenThread(1, False, worker.native_id), '打开输入线程')
            try:
                if not self.api.CancelSynchronousIo(handle):
                    error = c.get_last_error()
                    if error != 1168:
                        raise NativeProcessError(f'停止输入失败（Windows 错误 {error}）。')
            finally:
                _check(self.api.CloseHandle(handle), '关闭输入线程句柄')

    def _close_handle(self, name):
        handle = self._handles.pop(name, None)
        if handle is not None:
            _check(self.api.CloseHandle(handle), '关闭进程资源')

    def close(self):
        with self._lock:
            error = None
            try:
                self.terminate()
                if 'job' in self._handles:
                    deadline = time.monotonic() + 2
                    while True:
                        accounting = Accounting()
                        _check(self.api.QueryInformationJobObject(self._handles['job'], 1, c.byref(accounting), c.sizeof(accounting), None), '确认整棵 Job 停止')
                        if accounting.active == 0:
                            break
                        if time.monotonic() >= deadline:
                            raise NativeProcessError('整个 Job 停止未在清理时限内确认。')
                        time.sleep(.01)
                deadline = time.monotonic() + 2
                for name, handle in self._handles.items():
                    if name == 'process' or name.startswith('member_'):
                        milliseconds = max(0, int((deadline - time.monotonic()) * 1000))
                        if self.api.WaitForSingleObject(handle, milliseconds) != 0:
                            raise NativeProcessError('进程树停止信号未确认。')
            except NativeProcessError as exc:
                error = exc
            finally:
                for name in tuple(self._handles):
                    try:
                        self._close_handle(name)
                    except NativeProcessError as exc:
                        error = error or exc
            if error is not None:
                raise error


def process_alive(pid):
    api = _api()
    handle = api.OpenProcess(0x100000, False, pid)
    if not handle:
        error = c.get_last_error()
        if error == 87:
            return False
        raise NativeProcessError(f'检查进程失败（Windows 错误 {error}）。')
    try:
        result = api.WaitForSingleObject(handle, 0)
        if result not in (0, 0x102):
            _check(False, '检查进程状态')
        return result == 0x102
    finally:
        _check(api.CloseHandle(handle), '关闭检查句柄')

