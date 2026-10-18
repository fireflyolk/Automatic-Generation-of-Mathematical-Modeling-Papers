"""Windows Sandbox process control for untrusted generated Python."""
from dataclasses import dataclass
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
from uuid import UUID, uuid4
from xml.etree.ElementTree import Element, SubElement, tostring

from mathmodel_paper_studio.bootstrap.paths import validate_managed_path
from mathmodel_paper_studio.domain.models import StageStatus
from mathmodel_paper_studio.execution.result_validation import ArtifactExpectation


GUEST_RUNTIME = r'C:\MMPS\runtime'
GUEST_INPUT = r'C:\MMPS\input'
GUEST_OUTPUT = r'C:\MMPS\output'


class SandboxExecutionError(RuntimeError):
    """The sandbox was unavailable, failed to start, or violated its output contract."""


@dataclass(frozen=True)
class MappedFolder:
    host_path: Path
    guest_path: str
    read_only: bool

    def __post_init__(self):
        if not isinstance(self.host_path, Path) or type(self.read_only) is not bool:
            raise ValueError('Sandbox 映射目录配置无效。')
        if self.guest_path not in (GUEST_INPUT, GUEST_RUNTIME, GUEST_OUTPUT):
            raise ValueError('Sandbox 来宾目录不在固定映射范围内。')
        if self.guest_path in (GUEST_INPUT, GUEST_RUNTIME) and not self.read_only:
            raise ValueError('输入和运行时映射必须只读。')
        if self.guest_path == GUEST_OUTPUT and self.read_only:
            raise ValueError('唯一的输出映射必须可写。')


@dataclass(frozen=True)
class SandboxExecutionResult:
    stage_status: StageStatus
    execution_started: bool
    blocked_reason: str
    command: tuple[str, ...] = ()
    interpreter: str | None = None
    dependency_fingerprint: str | None = None
    exit_code: int | None = None
    artifacts: tuple = ()


def build_sandbox_configuration(*, memory_mib: int = 2048,
                                 mapped_folders: tuple[MappedFolder, ...] = ()) -> str:
    """Build the fail-closed WSB XML accepted by Windows Sandbox."""
    if type(memory_mib) is not int or not 2048 <= memory_mib <= 16 * 1024:
        raise ValueError('Windows Sandbox 内存必须在 2048–16384 MiB 范围内。')
    if not isinstance(mapped_folders, tuple) or any(not isinstance(item, MappedFolder)
                                                    for item in mapped_folders):
        raise ValueError('Sandbox 映射目录列表无效。')
    paths = [item.guest_path.casefold() for item in mapped_folders]
    if len(paths) != len(set(paths)) or set(paths) != {
            GUEST_INPUT.casefold(), GUEST_RUNTIME.casefold(), GUEST_OUTPUT.casefold()}:
        if mapped_folders:
            raise ValueError('必须且只能映射输入、运行时和本次输出目录。')

    root = Element('Configuration')
    for tag, value in (
        ('Networking', 'Disable'),
        ('vGPU', 'Disable'),
        ('MemoryInMB', str(memory_mib)),
        ('ClipboardRedirection', 'Disable'),
        ('PrinterRedirection', 'Disable'),
        ('AudioInput', 'Disable'),
        ('VideoInput', 'Disable'),
    ):
        SubElement(root, tag).text = value
    if mapped_folders:
        folders = SubElement(root, 'MappedFolders')
        for mapping in mapped_folders:
            host = validate_managed_path(mapping.host_path)
            if not host.is_dir():
                raise ValueError('Sandbox 映射源必须是存在的 F: 目录。')
            folder = SubElement(folders, 'MappedFolder')
            SubElement(folder, 'HostFolder').text = str(host)
            SubElement(folder, 'SandboxFolder').text = mapping.guest_path
            SubElement(folder, 'ReadOnly').text = 'true' if mapping.read_only else 'false'
    return tostring(root, encoding='unicode', short_empty_elements=True)


def guest_command(python_exe: str = GUEST_RUNTIME + r'\python.exe',
                  entry_script: str = GUEST_RUNTIME + r'\sandbox_entry.py',
                  generated_script: str = GUEST_INPUT + r'\generated.py',
                  input_dir: str = GUEST_INPUT,
                  output_dir: str = GUEST_OUTPUT) -> str:
    """Return a command containing only fixed, space-free guest mapping paths."""
    expected = (GUEST_RUNTIME + r'\python.exe', GUEST_RUNTIME + r'\sandbox_entry.py',
                GUEST_INPUT + r'\generated.py', GUEST_INPUT, GUEST_OUTPUT)
    actual = (python_exe, entry_script, generated_script, input_dir, output_dir)
    if actual != expected:
        raise ValueError('Sandbox 命令只能访问固定的输入、运行时和输出映射。')
    return ' '.join(actual)


def _parse_cli_json(result: subprocess.CompletedProcess) -> dict:
    try:
        value = json.loads(result.stdout)
    except (json.JSONDecodeError, TypeError) as exc:
        raise SandboxExecutionError('Windows Sandbox 命令行返回了无效响应。') from exc
    if not isinstance(value, dict):
        raise SandboxExecutionError('Windows Sandbox 命令行返回了无效响应。')
    return value


def _sandbox_ids(cli: Path) -> set[str]:
    result = subprocess.run([str(cli), 'list', '--raw'], capture_output=True, text=True,
                            encoding='utf-8', errors='replace', timeout=10, check=False,
                            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    if result.returncode != 0:
        raise SandboxExecutionError('无法检查 Windows Sandbox 会话状态。')
    value = _parse_cli_json(result)
    environments = value.get('WindowsSandboxEnvironments')
    if not isinstance(environments, list):
        raise SandboxExecutionError('Windows Sandbox 会话列表格式无效。')
    return {str(item.get('Id', item.get('id', ''))) if isinstance(item, dict) else str(item)
            for item in environments if item}


def find_sandbox_cli() -> Path | None:
    local_app_data = os.environ.get('LOCALAPPDATA')
    if local_app_data:
        candidate = Path(local_app_data) / 'Microsoft' / 'WindowsApps' / 'wsb.exe'
        if candidate.is_file():
            return candidate
    candidate = Path(r'C:\Windows\System32\wsb.exe')
    return candidate if candidate.is_file() else None


def resolve_computation_runtime_root() -> Path:
    """Resolve the explicitly configured source runtime or bundled runtime."""
    configured = os.environ.get('MMPS_COMPUTATION_RUNTIME_ROOT', '').strip()
    if configured:
        candidate = Path(configured)
    else:
        package_root = Path(getattr(sys, '_MEIPASS', Path(sys.executable).parent))
        candidate = package_root / 'computation-runtime'
    return validate_managed_path(candidate)


def _sha256(path: Path) -> str:
    import hashlib
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(128 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


class WindowsSandboxExecutor:
    """Run one generated script in a fresh, offline Windows Sandbox session."""

    def __init__(self, runtime_root: Path, *, cli_path: Path | None = None,
                 startup_timeout: float = 90.0, poll_interval: float = 0.2):
        if not isinstance(runtime_root, Path):
            raise TypeError('Sandbox Python 运行时路径必须是 Path。')
        self.runtime_root = validate_managed_path(runtime_root)
        self.cli_path = cli_path or find_sandbox_cli()
        if self.cli_path is not None and not isinstance(self.cli_path, Path):
            raise TypeError('Windows Sandbox CLI 路径必须是 Path。')
        if (not isinstance(startup_timeout, (int, float)) or isinstance(startup_timeout, bool)
                or not 1 <= startup_timeout <= 300):
            raise ValueError('Sandbox 启动时限无效。')
        if not isinstance(poll_interval, (int, float)) or not 0.05 <= poll_interval <= 2:
            raise ValueError('Sandbox 轮询间隔无效。')
        self.startup_timeout = float(startup_timeout)
        self.poll_interval = float(poll_interval)

    def available(self) -> tuple[bool, str]:
        if self.cli_path is None or not self.cli_path.is_file():
            return False, '未找到 Windows Sandbox 命令行组件。'
        required_files = (self.runtime_root / 'python.exe', self.runtime_root / 'sandbox_entry.py',
                          self.runtime_root / 'runtime-manifest.json')
        if (any(not path.is_file() for path in required_files)
                or not (self.runtime_root / 'Lib' / 'site-packages').is_dir()):
            return False, '数模计算运行时未完整安装。'
        try:
            validate_managed_path(self.runtime_root)
            if self.cli_path.drive.casefold() == 'f:':
                validate_managed_path(self.cli_path)
            else:
                local_app_data = os.environ.get('LOCALAPPDATA')
                app_alias = (Path(local_app_data) / 'Microsoft' / 'WindowsApps' / 'wsb.exe'
                             if local_app_data else None)
                if app_alias is None or self.cli_path.resolve() != app_alias.resolve():
                    return False, 'Windows Sandbox CLI 路径不受信任。'
        except (ValueError, OSError):
            return False, 'Sandbox 运行时不在受信任的本地路径。'
        try:
            manifest = json.loads((self.runtime_root / 'runtime-manifest.json').read_text('utf-8'))
            if not isinstance(manifest, dict):
                return False, '数模运行时清单格式无效。'
            packages = manifest.get('packages', {})
            files = manifest.get('files', {})
            required_packages = {'numpy', 'pandas', 'scipy', 'matplotlib', 'python-docx',
                                 'openpyxl', 'pypdf'}
            if (manifest.get('schema_version') != 1 or manifest.get('python_version', '').split('.')[:2]
                    != ['3', '13'] or manifest.get('architecture') != 'x86_64'
                    or not required_packages.issubset(packages)
                    or not isinstance(manifest.get('lock_sha256'), str)
                    or len(manifest['lock_sha256']) != 64):
                return False, '数模运行时版本或锁定依赖不完整。'
            for relative, expected in files.items():
                if relative not in ('python.exe', 'python3.dll', 'python313.dll', 'sandbox_entry.py'):
                    return False, '数模运行时指纹清单无效。'
                path = validate_managed_path(self.runtime_root / relative)
                if not path.is_file() or _sha256(path) != expected:
                    return False, '数模运行时文件指纹与清单不一致。'
            if set(files) != {'python.exe', 'python3.dll', 'python313.dll', 'sandbox_entry.py'}:
                return False, '数模运行时指纹清单不完整。'
        except (OSError, ValueError, json.JSONDecodeError, TypeError):
            return False, '数模运行时清单无法读取或校验。'
        return True, 'Windows Sandbox 与离线计算运行时已就绪。'

    @staticmethod
    def _write_recovery_record(temp_root: Path, session_id: str, work_root: Path) -> Path:
        recovery_root = validate_managed_path(temp_root / 'sandbox-recovery')
        recovery_root.mkdir(parents=True, exist_ok=True)
        validate_managed_path(recovery_root)
        record_path = validate_managed_path(recovery_root / f'{session_id}.json')
        payload = {'schema_version': 1, 'session_id': session_id, 'work_root': str(work_root)}
        temporary = recovery_root / f'.{session_id}.{uuid4().hex}.tmp'
        with temporary.open('x', encoding='utf-8', newline='\n') as stream:
            json.dump(payload, stream, ensure_ascii=False, sort_keys=True)
            stream.write('\n')
            stream.flush()
            os.fsync(stream.fileno())
        validate_managed_path(temporary)
        temporary.rename(record_path)
        return validate_managed_path(record_path)

    @staticmethod
    def _remove_recovery_record(record_path: Path | None) -> None:
        if record_path is None or not record_path.exists():
            return
        record = validate_managed_path(record_path)
        if not record.is_file() or record.is_symlink():
            raise SandboxExecutionError('Sandbox 恢复记录路径无效。')
        record.unlink()

    def _recover_pending_sessions(self, temp_root: Path) -> bool:
        recovery_root = validate_managed_path(temp_root / 'sandbox-recovery')
        if not recovery_root.exists():
            return True
        if not recovery_root.is_dir():
            return False
        for record_path in sorted(recovery_root.glob('*.json')):
            try:
                record_path = validate_managed_path(record_path)
                if not record_path.is_file() or record_path.is_symlink() or record_path.stat().st_size > 4096:
                    return False
                payload = json.loads(record_path.read_text('utf-8'))
                session_id = payload.get('session_id')
                if (not isinstance(payload, dict) or payload.get('schema_version') != 1
                        or not isinstance(session_id, str) or str(UUID(session_id)) != session_id):
                    return False
                work_root = validate_managed_path(Path(payload.get('work_root', '')))
                if (work_root.parent.resolve(strict=False) != validate_managed_path(temp_root).resolve(strict=False)
                        or not work_root.name.startswith('mmps-sandbox-')):
                    return False
                if not self._stop(self.cli_path, session_id):
                    return False
                if work_root.exists():
                    self._remove_work_root(work_root)
                self._remove_recovery_record(record_path)
            except (OSError, ValueError, TypeError, json.JSONDecodeError, SandboxExecutionError):
                return False
        return True

    def execute(self, layout, script_path: Path, input_files: tuple[Path, ...],
                expected_outputs: tuple[ArtifactExpectation, ...], *,
                memory_mib: int, wall_seconds: float, artifact_bytes: int,
                cancel_event=None,
                expected_input_hashes: tuple[str, ...] | None = None) -> SandboxExecutionResult:
        from mathmodel_paper_studio.execution.result_validation import ResultValidator

        available, reason = self.available()
        if not available:
            return SandboxExecutionResult(StageStatus.PAUSED, False, reason)
        if not expected_outputs or any(not isinstance(item, ArtifactExpectation) for item in expected_outputs):
            return SandboxExecutionResult(StageStatus.PAUSED, False, '没有有效的计算输出计划。')
        if len(expected_outputs) > 16 or len({Path(item.relative_path).name.casefold()
                                               for item in expected_outputs}) != len(expected_outputs):
            return SandboxExecutionResult(StageStatus.PAUSED, False, '计算输出计划无效。')
        if not isinstance(input_files, tuple) or not input_files:
            return SandboxExecutionResult(StageStatus.PAUSED, False, '缺少已选择的计算输入。')
        if expected_input_hashes is not None and (
                not isinstance(expected_input_hashes, tuple)
                or len(expected_input_hashes) != len(input_files)
                or any(not isinstance(digest, str) or len(digest) != 64
                       or any(character not in '0123456789abcdefABCDEF' for character in digest)
                       for digest in expected_input_hashes)):
            return SandboxExecutionResult(StageStatus.PAUSED, False, '计算输入哈希清单无效。')
        if type(artifact_bytes) is not int or artifact_bytes <= 0:
            return SandboxExecutionResult(StageStatus.PAUSED, False, '计算产物大小限制无效。')
        if cancel_event is not None and cancel_event.is_set():
            return SandboxExecutionResult(StageStatus.CANCELLED, False, '计算已取消。')
        if not self._recover_pending_sessions(layout.paths.temp):
            return SandboxExecutionResult(
                StageStatus.PAUSED, False,
                '存在无法确认关闭的旧 Sandbox；已保留其恢复记录，暂不启动新计算。',
            )

        work_root = Path(tempfile.mkdtemp(prefix='mmps-sandbox-', dir=layout.paths.temp))
        work_root = validate_managed_path(work_root)
        input_root, output_root = work_root / 'input', work_root / 'output'
        input_root.mkdir()
        output_root.mkdir()
        validate_managed_path(input_root)
        validate_managed_path(output_root)
        before_hashes = {}
        cli = self.cli_path
        session_id = str(uuid4())
        process = None
        started = False
        launched = False
        execution_started = False
        safe_to_cleanup = True
        shutdown_uncertain = False
        recovery_record = None
        command = guest_command()
        fingerprint = _sha256(self.runtime_root / 'runtime-manifest.json')

        def stop_session() -> bool:
            nonlocal launched, safe_to_cleanup, shutdown_uncertain
            try:
                stopped = self._stop(cli, session_id)
            except (OSError, subprocess.SubprocessError, SandboxExecutionError):
                stopped = False
            launched = False
            if stopped:
                return True
            safe_to_cleanup = False
            shutdown_uncertain = True
            return False

        def shutdown_warning() -> str:
            return (f'无法确认 Windows Sandbox 会话 {session_id} 已终止；'
                    f'已保留运行目录 {work_root} 和恢复记录 {recovery_record}。')

        try:
            for index, input_file in enumerate(input_files):
                source = validate_managed_path(input_file)
                if not source.is_file() or source.is_symlink():
                    raise SandboxExecutionError('所选计算输入不是本地普通文件。')
                target = input_root / source.name
                if target.exists():
                    raise SandboxExecutionError('所选输入文件名重复，无法创建隔离快照。')
                before_hashes[source] = _sha256(source)
                if (expected_input_hashes is not None
                        and before_hashes[source].casefold() != expected_input_hashes[index].casefold()):
                    raise SandboxExecutionError('所选计算输入与已审核的文件版本不一致。')
                shutil.copyfile(source, target)
                if _sha256(source) != before_hashes[source] or _sha256(target) != before_hashes[source]:
                    raise SandboxExecutionError('所选输入在快照过程中发生变化。')
                target.chmod(0o444)
            generated = input_root / 'generated.py'
            if generated.exists():
                raise SandboxExecutionError('计算输入占用了保留脚本名。')
            shutil.copyfile(script_path, generated)
            generated.chmod(0o444)
            mappings = (
                MappedFolder(input_root, GUEST_INPUT, True),
                MappedFolder(self.runtime_root, GUEST_RUNTIME, True),
                MappedFolder(output_root, GUEST_OUTPUT, False),
            )
            config = build_sandbox_configuration(memory_mib=memory_mib,
                                                 mapped_folders=mappings)
            recovery_record = self._write_recovery_record(layout.paths.temp, session_id, work_root)
            # A timed-out or partially successful start may still have created the session.
            launched = True
            start = subprocess.run([str(cli), 'start', '--id', session_id, '--config', config],
                                   capture_output=True, text=True, encoding='utf-8',
                                   errors='replace', timeout=30, check=False,
                                   creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            if start.returncode != 0:
                raise SandboxExecutionError('Windows Sandbox 启动失败。')
            deadline = time.monotonic() + self.startup_timeout
            while time.monotonic() < deadline:
                if cancel_event is not None and cancel_event.is_set():
                    stopped = stop_session()
                    reason = '计算已取消。' if stopped else shutdown_warning()
                    return SandboxExecutionResult(StageStatus.CANCELLED, False, reason)
                if session_id in _sandbox_ids(cli):
                    started = True
                    break
                time.sleep(self.poll_interval)
            if not started:
                raise SandboxExecutionError('Windows Sandbox 未能在时限内就绪。')
            if cancel_event is not None and cancel_event.is_set():
                stopped = stop_session()
                reason = '计算已取消。' if stopped else shutdown_warning()
                return SandboxExecutionResult(StageStatus.CANCELLED, False, reason)
            process = subprocess.Popen(
                [str(cli), 'exec', '--id', session_id, '--command', command,
                 '--working-directory', GUEST_OUTPUT, '--run-as', 'System', '--raw'],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding='utf-8',
                errors='replace', creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0),
            )
            execution_started = True
            deadline = time.monotonic() + float(wall_seconds)
            while process.poll() is None:
                if cancel_event is not None and cancel_event.is_set():
                    stopped = stop_session()
                    process.kill()
                    process.wait(timeout=5)
                    reason = '计算已取消。' if stopped else shutdown_warning()
                    return SandboxExecutionResult(StageStatus.CANCELLED, execution_started, reason,
                                                  ('wsb', 'exec'), 'sandbox:python.exe', fingerprint)
                total, unexpected = self._inspect_output(output_root, expected_outputs)
                if unexpected or total > min(256 * 1024 ** 2, len(expected_outputs) * artifact_bytes):
                    stopped = stop_session()
                    process.kill()
                    process.wait(timeout=5)
                    if not stopped:
                        raise SandboxExecutionError(shutdown_warning())
                    raise SandboxExecutionError('Sandbox 输出目录超出文件或大小限制。')
                if time.monotonic() >= deadline:
                    stopped = stop_session()
                    process.kill()
                    process.wait(timeout=5)
                    reason = ('计算超过设置的时限，Sandbox 已停止。' if stopped
                              else '计算超过设置的时限。' + shutdown_warning())
                    return SandboxExecutionResult(StageStatus.PAUSED, True, reason,
                                                  ('wsb', 'exec'), 'sandbox:python.exe', fingerprint)
                time.sleep(self.poll_interval)
            stdout, _stderr = process.communicate(timeout=5)
            if process.returncode != 0:
                stopped = stop_session()
                reason = ('沙盒中的计算程序返回失败状态。' if stopped
                          else '沙盒中的计算程序返回失败状态；' + shutdown_warning())
                return SandboxExecutionResult(StageStatus.PAUSED, True, reason,
                                              ('wsb', 'exec'), 'sandbox:python.exe', fingerprint,
                                              process.returncode)
            exit_code = self._guest_exit_code(stdout)
            if exit_code != 0:
                stopped = stop_session()
                reason = ('沙盒中的计算程序返回失败状态。' if stopped
                          else '沙盒中的计算程序返回失败状态；' + shutdown_warning())
                return SandboxExecutionResult(StageStatus.PAUSED, True, reason,
                                              ('wsb', 'exec'), 'sandbox:python.exe', fingerprint,
                                              exit_code)
            if not stop_session():
                return SandboxExecutionResult(StageStatus.PAUSED, True,
                                              shutdown_warning() + ' 结果未登记。',
                                              ('wsb', 'exec'), 'sandbox:python.exe', fingerprint, 0)
            started = False
            total, unexpected = self._inspect_output(output_root, expected_outputs)
            if unexpected or total > min(256 * 1024 ** 2, len(expected_outputs) * artifact_bytes):
                raise SandboxExecutionError('Sandbox 输出目录超出文件或大小限制。')
            for spec in expected_outputs:
                source = output_root / Path(spec.relative_path).name
                if not source.is_file():
                    continue
                target = layout.path_for(spec.relative_path)
                target.parent.mkdir(parents=True, exist_ok=True)
                validate_managed_path(target.parent)
                self._bounded_copy(source, target, artifact_bytes)
            report = ResultValidator(layout).validate(expected_outputs)
            if report.stage_status is not StageStatus.COMPLETED:
                return SandboxExecutionResult(StageStatus.PAUSED, True,
                                              '计算已运行，但输出没有通过 CSV/PNG 结构校验。',
                                              ('wsb', 'exec'), 'sandbox:python.exe', fingerprint, 0)
            return SandboxExecutionResult(StageStatus.COMPLETED, True, '', ('wsb', 'exec'),
                                          'sandbox:python.exe', fingerprint, 0, report.evidence)
        except (OSError, subprocess.SubprocessError, SandboxExecutionError, ValueError):
            if launched:
                stop_session()
            reason = ('Windows Sandbox 启动或输出检查失败，已安全暂停。'
                      if not shutdown_uncertain else shutdown_warning())
            return SandboxExecutionResult(StageStatus.PAUSED, execution_started,
                                          reason,
                                          ('wsb', 'exec') if execution_started else (),
                                          'sandbox:python.exe' if execution_started else None,
                                          fingerprint if execution_started else None)
        finally:
            if launched:
                stop_session()
            if process is not None and process.poll() is None:
                process.kill()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    pass
            if safe_to_cleanup:
                try:
                    validate_managed_path(work_root)
                    self._remove_work_root(work_root)
                    self._remove_recovery_record(recovery_record)
                except (OSError, SandboxExecutionError, ValueError):
                    # A leftover record lets the next run retry host-side cleanup.
                    pass

    @staticmethod
    def _guest_exit_code(stdout: str) -> int:
        try:
            value = json.loads(stdout)
        except (TypeError, json.JSONDecodeError):
            return 0 if not stdout.strip() else -1
        if not isinstance(value, dict):
            return -1
        candidates = (value.get('ExitCode'), value.get('exitCode'), value.get('ExitStatus'))
        for candidate in candidates:
            if type(candidate) is int:
                return candidate
        result = value.get('Result')
        if isinstance(result, dict):
            for key in ('ExitCode', 'exitCode', 'ExitStatus'):
                if type(result.get(key)) is int:
                    return result[key]
        return 0 if value.get('Success') is True else -1

    @staticmethod
    def _inspect_output(output_root: Path, expected_outputs: tuple[ArtifactExpectation, ...]):
        allowed = {Path(item.relative_path).name.casefold() for item in expected_outputs}
        total = 0
        unexpected = False
        for child in output_root.iterdir():
            if child.is_symlink() or not child.is_file() or child.name.casefold() not in allowed:
                unexpected = True
                continue
            try:
                total += child.stat().st_size
            except OSError:
                unexpected = True
        return total, unexpected

    @staticmethod
    def _bounded_copy(source: Path, target: Path, byte_limit: int):
        if target.exists():
            raise SandboxExecutionError('本次运行的结果文件已存在，不能覆盖。')
        temporary = target.parent / f'.{target.name}.{uuid4().hex}.tmp'
        total = 0
        try:
            with source.open('rb') as reader, temporary.open('xb') as writer:
                while chunk := reader.read(128 * 1024):
                    total += len(chunk)
                    if total > byte_limit:
                        raise SandboxExecutionError('Sandbox 结果文件超过单文件大小限制。')
                    writer.write(chunk)
                writer.flush()
                os.fsync(writer.fileno())
            validate_managed_path(temporary)
            if target.exists():
                raise SandboxExecutionError('本次运行的结果文件已存在，不能覆盖。')
            temporary.rename(target)
            validate_managed_path(target)
        except BaseException:
            if temporary.exists():
                validate_managed_path(temporary)
                temporary.unlink()
            raise

    @staticmethod
    def _remove_work_root(work_root: Path) -> None:
        """Clear read-only attributes from host snapshots and guest-created results."""
        root = validate_managed_path(work_root)
        for directory, names, files in os.walk(root, topdown=False, followlinks=False):
            parent = validate_managed_path(Path(directory))
            for name in files:
                item = validate_managed_path(parent / name)
                if item.is_file() and not item.is_symlink():
                    item.chmod(0o600)
            for name in names:
                item = validate_managed_path(parent / name)
                if item.is_dir() and not item.is_symlink():
                    item.chmod(0o700)
        shutil.rmtree(root)

    @staticmethod
    def _stop(cli: Path, session_id: str) -> bool:
        for attempt in range(2):
            try:
                subprocess.run([str(cli), 'stop', '--id', session_id, '--raw'],
                               capture_output=True, text=True, encoding='utf-8', errors='replace',
                               timeout=30, check=False,
                               creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            except (OSError, subprocess.SubprocessError):
                pass
            try:
                if session_id not in _sandbox_ids(cli):
                    return True
            except (OSError, subprocess.SubprocessError, SandboxExecutionError):
                return False
            if attempt == 0:
                time.sleep(0.2)
        return False
