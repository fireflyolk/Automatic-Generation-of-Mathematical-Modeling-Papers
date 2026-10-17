"""Runtime scope binds persisted selections to an actual executable identity."""
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path

from mathmodel_paper_studio.bootstrap.paths import validate_managed_path
from mathmodel_paper_studio.domain.models import ScopeManifest
from mathmodel_paper_studio.security.path_guard import selected_path, native_executable, executable_digest

EXTERNAL_PROCESSING_NOTICE = '本次选定的题目、项目文件和生成内容可能由所选 CLI 发送给对应服务商处理；文件范围不等同于精确网络请求载荷。'


@dataclass(frozen=True)
class RuntimeScope:
    metadata: ScopeManifest
    application_root: Path
    executable: Path
    version: str
    executable_sha256: str
    tool_profile: str
    notice: str = EXTERNAL_PROCESSING_NOTICE
    launch_arguments: tuple[str, ...] = ()

    def __post_init__(self):
        if not isinstance(self.metadata, ScopeManifest):
            raise ValueError('范围元数据类型无效。')
        if not self.metadata.external_processing or self.notice != EXTERNAL_PROCESSING_NOTICE:
            raise ValueError('必须展示完整外部处理说明。')
        if self.tool_profile not in ('restricted_read_write', 'workspace_exec', 'text_only'):
            raise ValueError('工具配置未经批准。')
        if not isinstance(self.version, str) or not self.version or len(self.version) > 100 or any(ord(c) < 32 for c in self.version):
            raise ValueError('CLI 版本信息无效。')
        object.__setattr__(self, 'application_root', validate_managed_path(self.application_root))
        object.__setattr__(self, 'executable', native_executable(self.executable))
        if self.executable_sha256 != executable_digest(self.executable):
            raise ValueError('执行文件摘要与当前文件不一致。')
        from mathmodel_paper_studio.agents.windows_job import validate_argv
        arguments = validate_argv(self.launch_arguments)
        denied = ('--add-dir', '--dangerously-skip-permissions', '--allow-dangerously-skip-permissions',
                  '--settings', '--mcp-config', '--permission-mode', '--allowedtools')
        if any(value.casefold().split('=')[0] in denied for value in arguments):
            raise ValueError('启动配置包含扩大目录、工具或权限的选项。')
        for index, value in enumerate(arguments):
            if value.casefold() == '--tools' and (index + 1 == len(arguments) or arguments[index + 1] != ('' if self.tool_profile == 'text_only' else 'Read,Write')):
                raise ValueError('启动工具配置超出批准的 Read/Write 范围。')
            if value.casefold().startswith('--tools=') and value != ('--tools=' if self.tool_profile == 'text_only' else '--tools=Read,Write'):
                raise ValueError('启动工具配置超出批准范围。')
        object.__setattr__(self, 'launch_arguments', arguments)
        self.validate_paths()

    @property
    def project_root(self) -> Path:
        return validate_managed_path(self.application_root / self.metadata.project_relative_path)

    @property
    def readable_inputs(self) -> tuple[Path, ...]:
        return tuple(validate_managed_path(self.project_root / value) for value in self.metadata.readable_inputs)

    @property
    def writable_directories(self) -> tuple[Path, ...]:
        return tuple(validate_managed_path(self.project_root / value) for value in self.metadata.writable_directories)

    @property
    def digest(self) -> str:
        payload = {'provider': self.metadata.provider.value, 'run_id': self.metadata.run_id,
                   'scope_id': self.metadata.scope_id, 'application_root': str(self.application_root),
                   'project_root': str(self.project_root), 'read': [str(p) for p in self.readable_inputs],
                   'write': [str(p) for p in self.writable_directories], 'stages': [s.value for s in self.metadata.stages],
                   'exe': str(self.executable), 'sha256': self.executable_sha256, 'version': self.version,
                   'tool_profile': self.tool_profile, 'notice': self.notice, 'argv': self.launch_arguments}
        return hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode('utf-8')).hexdigest()

    def validate_paths(self) -> None:
        if not self.project_root.is_dir():
            raise ValueError('项目根目录不存在。')
        for path in self.readable_inputs:
            selected_path(path, self.project_root, (path,))
        for path in self.writable_directories:
            if not path.is_dir() or path == self.project_root:
                raise ValueError('输出必须为现有的明确子目录。')
            selected_path(path, self.project_root, (path,), write=True)
            if any(source.is_relative_to(path) for source in self.readable_inputs):
                raise ValueError('只读输入不能包含在可写输出目录中。')

    def check_read(self, value) -> Path:
        return selected_path(value, self.project_root, self.readable_inputs)

    def check_write(self, value) -> Path:
        return selected_path(value, self.project_root, self.writable_directories, write=True)


def build_scope(application_root, metadata, executable, version, tool_profile, *, launch_arguments=()) -> RuntimeScope:
    return RuntimeScope(metadata, Path(application_root), Path(executable), version,
                        executable_digest(executable), tool_profile, launch_arguments=launch_arguments)
