"""Strict path selections, not an operating-system sandbox."""
from pathlib import Path, PureWindowsPath
import os
import re
import stat
import hashlib

from mathmodel_paper_studio.bootstrap.paths import PathPolicyError, validate_managed_path


def selected_path(value, project_root, allowed, *, write=False) -> Path:
    path = validate_managed_path(value)
    root = validate_managed_path(project_root)
    if not root.is_dir() or not path.is_relative_to(root):
        raise PathPolicyError('路径离开本次项目范围。')
    selections = tuple(validate_managed_path(item) for item in allowed)
    if any(not item.is_relative_to(root) for item in selections):
        raise PathPolicyError('选定路径离开本次项目范围。')
    if write:
        accepted = any(item.is_dir() and path.is_relative_to(item) for item in selections)
    else:
        accepted = any(item.is_file() and path == item for item in selections)
    if not accepted:
        raise PathPolicyError('路径不在本次明确选定的读写范围内。')
    return path


def native_executable(value) -> Path:
    raw = os.fspath(value)
    if not isinstance(raw, str) or raw.startswith(('\\\\', '//')):
        raise PathPolicyError('CLI 必须使用本机绝对执行路径。')
    windows = PureWindowsPath(raw)
    if not windows.is_absolute() or not re.fullmatch(r'[A-Za-z]:', windows.drive):
        raise PathPolicyError('CLI 必须使用本机盘符绝对路径。')
    for part in raw.replace('\\', '/')[3:].split('/'):
        if (part in ('', '.', '..') or part.endswith((' ', '.'))
                or re.match(r'^(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\.|$)', part, re.I)
                or any(ord(ch) < 32 or ch in '<>:"|?*' for ch in part)):
            raise PathPolicyError('CLI 路径包含不允许的路径段。')
    path = Path(windows)
    for ancestor in (*reversed(path.parents), path):
        try:
            info = ancestor.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT:
            raise PathPolicyError('CLI 路径不能经过符号链接或目录联接。')
    if path.suffix.casefold() != '.exe' or not path.is_file():
        raise PathPolicyError('只能启动本机现有的原生 .exe，不能启动脚本或命令壳。')
    with path.open('rb') as source:
        if source.read(2) != b'MZ':
            raise PathPolicyError('执行文件不是原生 Windows 映像。')
    return path


def executable_digest(value) -> str:
    path = native_executable(value)
    before = path.stat()
    digest = hashlib.sha256()
    with path.open('rb') as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b''):
            digest.update(chunk)
    after = path.stat()
    if (before.st_size, before.st_mtime_ns, before.st_ino) != (after.st_size, after.st_mtime_ns, after.st_ino):
        raise PathPolicyError('执行文件在检查过程中发生变化。')
    return digest.hexdigest()
