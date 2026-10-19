"""Trusted guest-side launcher; generated source runs as a separate child process."""
import os
from pathlib import Path
import re
import subprocess
import sys
import threading
import json


_MAX_DIAGNOSTIC_BYTES = 64 * 1024
_MAX_RESULT_BYTES = 4096
_GUEST_RESULT_FILENAME = '.mmps-execution-result.json'
_ERROR_TYPES = frozenset({
    'AssertionError', 'AttributeError', 'FileNotFoundError', 'ImportError',
    'IndexError', 'KeyError', 'MemoryError', 'ModuleNotFoundError', 'NameError',
    'OverflowError', 'PermissionError', 'RuntimeError', 'SyntaxError', 'TypeError',
    'UnboundLocalError', 'ValueError', 'ZeroDivisionError',
})
_TRACEBACK_LINE = re.compile(r'File "(?:.*[/\\])?generated\.py", line ([1-9][0-9]{0,7})(?:,|\s|$)')
_IDENTIFIER = re.compile(r"name '([A-Za-z_][A-Za-z0-9_]{0,127})' is not defined")


def _failure_summary(stderr: bytes) -> dict | None:
    if not isinstance(stderr, bytes) or not stderr:
        return None
    text = stderr[-_MAX_DIAGNOSTIC_BYTES:].decode('utf-8', errors='replace')
    error_type = None
    name = None
    for line in reversed(text.splitlines()):
        candidate = re.match(r'^([A-Za-z][A-Za-z0-9_]*)(?::|$)', line.strip())
        if candidate:
            error_type = candidate.group(1)
            if error_type == 'NameError':
                match = _IDENTIFIER.search(line)
                name = match.group(1) if match else None
            break
    if error_type not in _ERROR_TYPES:
        return None
    line_number = None
    for line in reversed(text.splitlines()):
        match = _TRACEBACK_LINE.search(line)
        if match:
            line_number = int(match.group(1))
            break
    if line_number is None:
        return None
    return {'error_type': error_type, 'line': line_number, 'name': name}


def _guest_result(exit_code: int, stderr: bytes, _stdout: bytes = b'') -> dict:
    return {'ExitCode': int(exit_code),
            'Failure': _failure_summary(stderr) if exit_code != 0 else None}


def _write_guest_result(output_dir: Path, exit_code: int, stderr: bytes) -> None:
    """Return bounded child status through the writable folder mapping.

    Windows Sandbox's CLI does not forward process I/O, so stdout cannot carry
    this result back to the host. Generated code is confined to the same output
    mapping; this reserved file is replaced after the child has exited.
    """
    if not isinstance(output_dir, Path) or not output_dir.is_dir():
        raise ValueError('Sandbox output mapping is unavailable.')
    target = output_dir / _GUEST_RESULT_FILENAME
    if target.exists() or target.is_symlink():
        if target.is_dir() and not target.is_symlink():
            raise ValueError('Sandbox result path is not a regular file.')
        target.unlink()
    payload = json.dumps(_guest_result(exit_code, stderr), separators=(',', ':')).encode('utf-8')
    if len(payload) > _MAX_RESULT_BYTES:
        raise ValueError('Sandbox result summary exceeded its size limit.')
    with target.open('xb') as stream:
        stream.write(payload)
        stream.flush()
        os.fsync(stream.fileno())


def _drain_bounded(stream, tail: bytearray) -> None:
    while True:
        chunk = stream.read(4096)
        if not chunk:
            return
        tail.extend(chunk)
        if len(tail) > _MAX_DIAGNOSTIC_BYTES:
            del tail[:-_MAX_DIAGNOSTIC_BYTES]


def main(arguments: list[str]) -> int:
    if len(arguments) != 3:
        return 64
    script, input_dir, output_dir = (Path(value) for value in arguments)
    if not script.is_file() or not input_dir.is_dir() or not output_dir.is_dir():
        return 65
    runtime = Path(sys.executable).resolve().parent
    scratch = Path(r'C:\MMPS\localtmp')
    scratch.mkdir(parents=True, exist_ok=True)
    environment = {
        'SYSTEMROOT': os.environ.get('SYSTEMROOT', r'C:\Windows'),
        'WINDIR': os.environ.get('WINDIR', r'C:\Windows'),
        'PATH': os.pathsep.join((str(runtime), str(runtime / 'DLLs'), r'C:\Windows\System32')),
        'PYTHONNOUSERSITE': '1',
        'PYTHONDONTWRITEBYTECODE': '1',
        'PYTHONUTF8': '1',
        'PYTHONPATH': str(runtime / 'Lib' / 'site-packages'),
        'MPLBACKEND': 'Agg',
        'MPLCONFIGDIR': str(scratch / 'matplotlib'),
        'TEMP': str(scratch),
        'TMP': str(scratch),
        'TMPDIR': str(scratch),
    }
    command = [sys.executable, '-s', '-B', str(script), str(input_dir), str(output_dir)]
    result = subprocess.Popen(command, cwd=output_dir, env=environment, shell=False,
                              stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                              stderr=subprocess.PIPE)
    stderr_tail = bytearray()
    reader = threading.Thread(target=_drain_bounded, args=(result.stderr, stderr_tail), daemon=True)
    reader.start()
    exit_code = result.wait()
    reader.join(timeout=5)
    _write_guest_result(output_dir, exit_code, bytes(stderr_tail))
    return 0


if __name__ == '__main__':
    raise SystemExit(main(sys.argv[1:]))
