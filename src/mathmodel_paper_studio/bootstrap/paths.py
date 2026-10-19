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


def _absolute_local_path(value: str | os.PathLike) -> Path:
    raw = os.fspath(value)
    if not isinstance(raw, str) or not raw or raw.startswith(('\\\\', '//')):
        raise PathPolicyError('源文件必须是本地磁盘上的绝对路径（例如 C:\\...）。')
    windows = PureWindowsPath(raw)
    if not windows.is_absolute() or not re.fullmatch(r'[a-z]:', windows.drive, re.I):
        raise PathPolicyError('源文件必须是本地磁盘上的绝对路径（例如 C:\\...）。')
    components = windows.parts[1:]
    for part in components:
        if (part in ('', '.', '..') or part.endswith((' ', '.')) or _RESERVED.match(part)
                or any(ord(c) < 32 or c in '<>:"|?*' for c in part)):
            raise PathPolicyError('源文件路径包含不允许的路径段。')
    if os.name != 'nt':
        raise PathPolicyError('此版本需要 Windows 本地磁盘路径。')
    return Path(windows)


def _reject_reparse_components(path: Path, label: str) -> None:
    """Check existing components once; a missing parent means deeper ones cannot exist."""
    try:
        for ancestor in (*reversed(path.parents), path):
            try:
                info = ancestor.lstat()
            except FileNotFoundError:
                break
            if (stat.S_ISLNK(info.st_mode)
                    or getattr(info, 'st_file_attributes', 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT):
                raise PathPolicyError(f'{label}路径经过符号链接或目录联接，请选择真实路径。')
    except (OSError, RuntimeError) as exc:
        raise PathPolicyError(f'无法检查{label}路径，请确认本地磁盘可访问。') from exc


def validate_import_source_path(value: str | os.PathLike) -> Path:
    """Validate a read-only source path on any local drive, outside app storage if needed."""
    path = _absolute_local_path(value)
    _reject_reparse_components(path, '源文件')
    return path


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
    """Reject lexical escapes and reparse-point aliases before using managed paths."""
    path = _lexical_path(value)
    _reject_reparse_components(path, '存储')
    return path


def validate_application_resource_path(value: str | os.PathLike) -> Path:
    """Allow read-only installed resources; writable app storage stays F:-only."""
    path = _absolute_local_path(value)
    if path.drive.casefold() == 'f:':
        return validate_managed_path(path)
    local_app_data = os.environ.get('LOCALAPPDATA', '')
    installation = _absolute_local_path(local_app_data) / 'Programs' / 'MathModelPaperStudio'
    if (path.drive.casefold() != 'c:' or installation.drive.casefold() != 'c:'
            or not path.is_relative_to(installation)):
        raise PathPolicyError('应用资源必须位于批准的 C: 安装目录或 F: 工作目录内。')
    _reject_reparse_components(path, '安装资源')
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
