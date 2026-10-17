"""F:-only application paths; imported before any optional GUI/data library."""
from dataclasses import dataclass
import os
from pathlib import Path, PureWindowsPath
import re
import stat

APPROVED_BASE = r'F:\Codex另一个工作的地方'
DEFAULT_ROOT = APPROVED_BASE + r'\mathmodel-paper-studio-data'
_RESERVED = re.compile(r'^(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\.|$)', re.I)


class PathPolicyError(ValueError):
    """A path cannot be safely used as app-managed storage."""


def _lexical_path(value: str | os.PathLike) -> Path:
    raw = os.fspath(value)
    if not isinstance(raw, str) or not raw or raw.startswith(('\\\\', '//')):
        raise PathPolicyError('存储路径必须是 F: 盘的本地绝对路径。')
    windows = PureWindowsPath(raw)
    if not windows.is_absolute() or windows.drive.casefold() != 'f:':
        raise PathPolicyError('存储路径必须是 F: 盘的本地绝对路径。')
    components = raw.replace('\\', '/')[3:].split('/')
    for part in components:
        if (part in ('', '.', '..') or part.endswith((' ', '.')) or _RESERVED.match(part)
                or any(ord(c) < 32 or c in '<>:"|?*' for c in part)):
            raise PathPolicyError('存储路径包含不允许的路径段。')
    base_parts = tuple(p.casefold() for p in PureWindowsPath(APPROVED_BASE).parts)
    path_parts = tuple(p.casefold() for p in windows.parts)
    if path_parts[:len(base_parts)] != base_parts or len(path_parts) <= len(base_parts):
        raise PathPolicyError('存储路径必须位于批准的 F: 工作目录内。')
    if os.name != 'nt':
        raise PathPolicyError('此版本需要 Windows F: 本地磁盘，不能使用其他平台的路径回退。')
    return Path(windows)


def validate_managed_path(value: str | os.PathLike) -> Path:
    """Reject aliases and escapes before resolving or creating a managed path."""
    path = _lexical_path(value)
    try:
        for ancestor in (*reversed(path.parents), path):
            if ancestor.exists() or ancestor.is_symlink():
                info = ancestor.lstat()
                if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT:
                    raise PathPolicyError('存储路径经过符号链接或目录联接，请选择真实目录。')
        resolved = path.resolve(strict=False)
    except (OSError, RuntimeError) as exc:
        raise PathPolicyError('无法检查存储路径，请确认 F: 可访问。') from exc
    if not resolved.is_relative_to(Path(APPROVED_BASE).resolve(strict=False)):
        raise PathPolicyError('存储路径解析后离开批准的 F: 工作目录。')
    return path


@dataclass(frozen=True)
class AppPaths:
    root: Path = Path(DEFAULT_ROOT)

    def __post_init__(self):
        root = validate_managed_path(self.root)
        if root.exists() and not root.is_dir():
            raise PathPolicyError('应用存储根路径已被文件占用，请选择新目录。')
        object.__setattr__(self, 'root', root)
        self.validate()

    @property
    def state(self) -> Path:
        return self.root / 'state'

    @property
    def database(self) -> Path:
        return self.state / 'studio.sqlite3'

    @property
    def projects(self) -> Path:
        return self.root / 'projects'

    @property
    def logs(self) -> Path:
        return self.root / 'logs'

    @property
    def cache(self) -> Path:
        return self.root / 'cache'

    @property
    def temp(self) -> Path:
        return self.root / 'tmp'

    @property
    def build(self) -> Path:
        return self.root / 'build'

    @property
    def tests(self) -> Path:
        return self.root / 'tests'

    def validate(self) -> None:
        for path in (self.root, self.state, self.database, self.projects, self.logs,
                     self.cache, self.temp, self.build, self.tests):
            validate_managed_path(path)
            if path != self.database and path.exists() and not path.is_dir():
                raise PathPolicyError('应用目录被同名文件占用。')
            if path == self.database and path.exists() and not path.is_file():
                raise PathPolicyError('数据库路径被目录占用。')

    def ensure_directories(self) -> None:
        self.validate()
        if not Path('F:/').is_dir():
            raise PathPolicyError('F: 盘不可访问，请连接磁盘后重试。')
        for path in (self.root, self.state, self.projects, self.logs, self.cache,
                     self.temp, self.build, self.tests):
            validate_managed_path(path)
            path.mkdir(parents=True, exist_ok=True)
            validate_managed_path(path)

    def configure_environment(self) -> dict[str, Path]:
        self.ensure_directories()
        variables = {
            'TEMP': self.temp, 'TMP': self.temp, 'TMPDIR': self.temp,
            'PYTHONPYCACHEPREFIX': self.cache / 'pycache',
            'PIP_CACHE_DIR': self.cache / 'pip', 'UV_CACHE_DIR': self.cache / 'uv',
            'MPLCONFIGDIR': self.cache / 'matplotlib',
            'QT_SHADER_CACHE_PATH': self.cache / 'qt-shaders',
            'QML_DISK_CACHE_PATH': self.cache / 'qml',
            'XDG_CACHE_HOME': self.cache / 'xdg',
        }
        for path in variables.values():
            validate_managed_path(path)
            path.mkdir(parents=True, exist_ok=True)
        for name, path in variables.items():
            os.environ[name] = str(path)
        # tempfile caches its first selection separately from the environment.
        import tempfile
        tempfile.tempdir = str(self.temp)
        # This controls later imports in this process, after Python startup.
        import sys
        sys.pycache_prefix = str(variables['PYTHONPYCACHEPREFIX'])
        return variables
