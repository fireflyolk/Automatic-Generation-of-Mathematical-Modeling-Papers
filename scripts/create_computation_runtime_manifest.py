"""Verify the isolated runtime lock and record its package/file fingerprint."""
import argparse
import hashlib
import json
from pathlib import Path
import platform
import sys

from verify_f_drive_outputs import verify_lock
from mathmodel_paper_studio.bootstrap.paths import validate_managed_path


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(128 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def create_manifest(runtime_root: Path, lock_path: Path) -> dict:
    runtime_root = validate_managed_path(runtime_root)
    lock_path = validate_managed_path(lock_path)
    if platform.python_version_tuple()[:2] != ('3', '13') or platform.machine().casefold() not in (
            'amd64', 'x86_64'):
        raise ValueError('隔离运行时必须为 CPython 3.13 x64。')
    locked = verify_lock(lock_path, require_hashes=True)
    packages = {
        item['name'].lower().replace('_', '-'): item['version']
        for item in locked
    }
    required = {'numpy', 'pandas', 'scipy', 'matplotlib', 'python-docx', 'openpyxl', 'pypdf'}
    if not required.issubset(packages):
        raise ValueError('运行时锁缺少数模工作流必需包。')
    files = {}
    for relative in ('python.exe', 'python3.dll', 'python313.dll', 'sandbox_entry.py'):
        path = validate_managed_path(runtime_root / relative)
        if not path.is_file():
            raise ValueError('隔离运行时缺少必需文件。')
        files[relative] = sha256(path)
    return {
        'schema_version': 1,
        'python_version': platform.python_version(),
        'architecture': 'x86_64',
        'lock_sha256': sha256(lock_path),
        'packages': packages,
        'files': files,
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('--runtime-root', required=True)
    parser.add_argument('--lock', required=True)
    parser.add_argument('--output', required=True)
    args = parser.parse_args(argv)
    runtime_root = validate_managed_path(Path(args.runtime_root))
    output = validate_managed_path(Path(args.output))
    payload = create_manifest(runtime_root, Path(args.lock))
    with output.open('x', encoding='utf-8', newline='\n') as stream:
        json.dump(payload, stream, sort_keys=True, separators=(',', ':'))
        stream.write('\n')
    print(json.dumps({'status': 'ok', 'runtime': str(runtime_root)}))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
