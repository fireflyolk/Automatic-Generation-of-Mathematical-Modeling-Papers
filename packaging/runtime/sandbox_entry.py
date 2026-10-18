"""Trusted guest-side launcher; generated source runs as a separate child process."""
import os
from pathlib import Path
import subprocess
import sys


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
    result = subprocess.run(command, cwd=output_dir, env=environment, shell=False,
                            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL, check=False)
    return result.returncode


if __name__ == '__main__':
    raise SystemExit(main(sys.argv[1:]))
