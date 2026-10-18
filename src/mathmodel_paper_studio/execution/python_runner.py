"""Run-scoped artifacts and fail-closed generated-Python orchestration.

Windows Job Objects bound process lifetime/resources, not filesystem or network
access. This module deliberately has no process launch path until an independently
verified Python sandbox exists.
"""
from dataclasses import asdict, dataclass
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import tempfile
import threading
from uuid import uuid4

from mathmodel_paper_studio.bootstrap.paths import AppPaths, validate_managed_path
from mathmodel_paper_studio.domain.models import (
    ArtifactEvidence, Project, Run, StageStatus, relative_path as validate_relative_path,
)
from mathmodel_paper_studio.execution.limits import ComputationLimits


_BLOCKED_REASON = '未证明本地 Python 的文件访问边界和网络隔离，暂不能执行生成代码。'
_CANCELLED_REASON = '计算已在启动前取消；没有执行生成代码。'


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(128 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def _evidence(layout, relative_path: str, kind: str, path: Path) -> ArtifactEvidence:
    digest = _sha256_file(path)
    project_relative = f'{layout.run.relative_path}/{relative_path}'
    evidence_id = 'ev_' + hashlib.sha256(
        f'{layout.run.run_id}\0{project_relative}\0{digest}'.encode('utf-8')
    ).hexdigest()[:24]
    return ArtifactEvidence(evidence_id, layout.run.run_id, project_relative, digest, kind, True)


def _atomic_create(target: Path, data: bytes) -> None:
    if not isinstance(data, bytes) or len(data) > 16 * 1024 ** 2:
        raise ValueError('运行工件写入数据类型无效或超过 16 MiB。')
    parent = validate_managed_path(target.parent)
    parent.mkdir(parents=True, exist_ok=True)
    validate_managed_path(target)
    if target.exists():
        raise FileExistsError(target)
    temporary = parent / f'.{target.name}.{uuid4().hex}.tmp'
    validate_managed_path(temporary)
    try:
        with temporary.open('xb') as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        validate_managed_path(temporary)
        validate_managed_path(target)
        if target.exists():
            raise FileExistsError(target)
        temporary.rename(target)
        validate_managed_path(target)
    except BaseException:
        if temporary.exists():
            validate_managed_path(temporary)
            temporary.unlink()
        raise


@dataclass(frozen=True)
class RunArtifactLayout:
    """A new run's private, F:-only source/result/figure directories."""

    paths: AppPaths
    project: Project
    run: Run

    def __post_init__(self):
        if not isinstance(self.paths, AppPaths) or not isinstance(self.project, Project) or not isinstance(self.run, Run):
            raise TypeError('运行工件布局需要 AppPaths、Project 和 Run。')
        if self.run.project_id != self.project.project_id:
            raise ValueError('运行与项目标识不匹配。')
        expected_project = validate_managed_path(self.paths.root / self.project.relative_path)
        expected_run = validate_managed_path(expected_project / self.run.relative_path)
        if self.run.relative_path != f'90_runs/{self.run.run_id}':
            raise ValueError('运行工件必须位于固定 90_runs/<run-id> 目录。')
        if expected_project.drive.lower() != 'f:' or expected_run.drive.lower() != 'f:':
            raise ValueError('运行工件必须保存在 F:。')
        for directory in (expected_project, expected_run,
                          expected_run / '30_src', expected_run / '40_results',
                          expected_run / '50_figures'):
            validate_managed_path(directory)
            if not directory.is_dir():
                raise ValueError('运行工件目录缺失或不是目录。')

    @classmethod
    def create(cls, paths: AppPaths, project: Project, run: Run) -> 'RunArtifactLayout':
        if not isinstance(paths, AppPaths) or not isinstance(project, Project) or not isinstance(run, Run):
            raise TypeError('运行工件布局需要 AppPaths、Project 和 Run。')
        if run.project_id != project.project_id or run.relative_path != f'90_runs/{run.run_id}':
            raise ValueError('运行与项目不匹配或运行目录不符合固定格式。')
        paths.ensure_directories()
        project_root = validate_managed_path(paths.root / project.relative_path)
        if not project_root.is_dir():
            raise ValueError('项目目录不存在，不能创建运行工件。')
        runs_root = validate_managed_path(project_root / '90_runs')
        if not runs_root.is_dir():
            raise ValueError('项目 90_runs 目录不存在。')
        run_root = validate_managed_path(project_root / run.relative_path)
        if run_root.exists():
            raise FileExistsError('该运行目录已存在，不能覆盖旧结果。')

        stage = Path(tempfile.mkdtemp(prefix='.run-stage-', dir=runs_root))
        finalized = False
        try:
            for name in ('30_src', '40_results', '50_figures'):
                child = validate_managed_path(stage / name)
                child.mkdir()
                validate_managed_path(child)
            validate_managed_path(stage)
            validate_managed_path(run_root)
            if run_root.exists():
                raise FileExistsError('该运行目录已存在，不能覆盖旧结果。')
            stage.rename(run_root)
            finalized = True
            for name in ('30_src', '40_results', '50_figures'):
                validate_managed_path(run_root / name)
            return cls(paths, project, run)
        except BaseException:
            if not finalized and stage.exists():
                validate_managed_path(stage)
                shutil.rmtree(stage)
            raise

    @property
    def project_root(self) -> Path:
        return self.paths.root / self.project.relative_path

    @property
    def root(self) -> Path:
        return self.project_root / self.run.relative_path

    @property
    def source_dir(self) -> Path:
        return self.root / '30_src'

    @property
    def results_dir(self) -> Path:
        return self.root / '40_results'

    @property
    def figures_dir(self) -> Path:
        return self.root / '50_figures'

    def path_for(self, relative_path: str) -> Path:
        if not isinstance(relative_path, str):
            raise ValueError('运行工件路径必须为字符串。')

        validate_relative_path(relative_path)
        path = validate_managed_path(self.root.joinpath(*PurePosixPath(relative_path).parts))
        resolved_root = self.root.resolve(strict=False)
        if not path.resolve(strict=False).is_relative_to(resolved_root):
            raise ValueError('工件路径离开本次运行目录。')
        return path

    def project_relative(self, run_relative_path: str) -> str:
        self.path_for(run_relative_path)
        return f'{self.run.relative_path}/{run_relative_path}'


@dataclass(frozen=True)
class ComputationOutcome:
    stage_status: StageStatus
    execution_started: bool
    blocked_reason: str
    deterministic_seed: int
    script_path: Path | None
    summary_path: Path
    script_evidence: ArtifactEvidence | None
    summary_evidence: ArtifactEvidence
    command: tuple[str, ...] = ()
    interpreter: str | None = None
    dependency_fingerprint: str | None = None
    exit_code: int | None = None
    stdout_path: Path | None = None
    stderr_path: Path | None = None
    artifacts: tuple[ArtifactEvidence, ...] = ()


class PythonRunner:
    """Persist generated source and delegate execution only to a verified backend."""

    def __init__(self, limits: ComputationLimits = ComputationLimits(), *, executor=None):
        if not isinstance(limits, ComputationLimits):
            raise TypeError('计算限制类型无效。')
        if executor is not None and not callable(getattr(executor, 'execute', None)):
            raise TypeError('计算执行后端接口无效。')
        self.limits = limits
        self.executor = executor

    def available(self) -> tuple[bool, str]:
        if self.executor is None:
            return False, _BLOCKED_REASON
        checker = getattr(self.executor, 'available', None)
        if not callable(checker):
            return False, '计算执行后端未提供预检。'
        result = checker()
        if (not isinstance(result, tuple) or len(result) != 2 or type(result[0]) is not bool
                or not isinstance(result[1], str)):
            return False, '计算执行后端预检响应无效。'
        return result

    def run(self, layout: RunArtifactLayout, source: str, *, input_files: tuple[Path, ...] = (),
            deterministic_seed: int = 0, attempt: int = 0,
            cancel_event: threading.Event | None = None,
            expected_outputs: tuple = (), input_hashes: tuple[str, ...] | None = None) -> ComputationOutcome:
        if not isinstance(layout, RunArtifactLayout):
            raise TypeError('计算请求必须使用经过检查的运行工件布局。')
        if type(attempt) is not int or attempt < 0:
            raise ValueError('计算尝试编号必须是非负整数。')
        if not isinstance(input_files, tuple) or any(not isinstance(path, Path) for path in input_files):
            raise TypeError('计算输入必须是 Path 元组。')
        if input_hashes is not None and (
                not isinstance(input_hashes, tuple) or len(input_hashes) != len(input_files)
                or any(not isinstance(digest, str) or len(digest) != 64
                       or any(character not in '0123456789abcdefABCDEF' for character in digest)
                       for digest in input_hashes)):
            raise ValueError('计算输入哈希清单无效。')
        if not isinstance(expected_outputs, tuple):
            raise TypeError('计算输出计划必须是不可变元组。')
        project_root = validate_managed_path(layout.paths.root / layout.project.relative_path)
        resolved_project = project_root.resolve(strict=False)
        for input_path in input_files:
            managed_input = validate_managed_path(input_path)
            if not managed_input.resolve(strict=False).is_relative_to(resolved_project):
                raise ValueError('计算输入必须位于当前项目目录。')
        if (type(deterministic_seed) is not int or not 0 <= deterministic_seed <= 2**32 - 1):
            raise ValueError('随机种子必须是 32 位非负整数。')
        if cancel_event is not None and not isinstance(cancel_event, threading.Event):
            raise TypeError('取消信号类型无效。')
        if not isinstance(source, str) or '\0' in source:
            raise ValueError('生成脚本必须是无 NUL 字符的文本。')
        if not source or len(source) > self.limits.max_source_bytes:
            raise ValueError('生成脚本为空或超过源文件上限。')
        source_bytes = source.encode('utf-8')
        if len(source_bytes) > self.limits.max_source_bytes:
            raise ValueError('生成脚本为空或超过源文件上限。')

        cancelled = cancel_event is not None and cancel_event.is_set()
        summary_version = attempt + 1
        summary_relative = f'40_results/execution-v{summary_version:04d}.json'
        summary_path = layout.path_for(summary_relative)
        if summary_path.exists():
            raise FileExistsError('该运行已有执行记录，不能追加或覆盖。')
        script_path = None
        script_evidence = None
        status = StageStatus.CANCELLED if cancelled else StageStatus.PAUSED
        reason = _CANCELLED_REASON if cancelled else _BLOCKED_REASON
        if not cancelled:
            script_name = 'generated.py' if attempt == 0 else f'generated-v{summary_version:04d}.py'
            script_path = layout.path_for(f'30_src/{script_name}')
            if script_path.exists():
                raise FileExistsError('该运行已有生成脚本，不能覆盖。')
            _atomic_create(script_path, source_bytes)
            script_evidence = _evidence(
                layout, script_path.relative_to(layout.root).as_posix(), 'script', script_path,
            )


        status = StageStatus.CANCELLED if cancelled else StageStatus.PAUSED
        reason = _CANCELLED_REASON if cancelled else _BLOCKED_REASON
        execution_started = False
        command = ()
        interpreter = None
        dependency_fingerprint = None
        exit_code = None
        artifacts = ()
        if not cancelled and self.executor is not None:
            from mathmodel_paper_studio.execution.result_validation import ArtifactExpectation
            if (not expected_outputs or any(not isinstance(item, ArtifactExpectation)
                                            for item in expected_outputs)):
                status = StageStatus.PAUSED
                reason = '没有可供隔离运行器验证的输出计划。'
            else:
                result = self.executor.execute(
                    layout, script_path, input_files, expected_outputs,
                    memory_mib=self.limits.memory_bytes // (1024 * 1024),
                    wall_seconds=self.limits.wall_seconds,
                    artifact_bytes=self.limits.artifact_bytes,
                    cancel_event=cancel_event,
                    expected_input_hashes=input_hashes,
                )
                status = StageStatus(result.stage_status)
                execution_started = result.execution_started is True
                reason = result.blocked_reason
                command = tuple(result.command)
                interpreter = result.interpreter
                dependency_fingerprint = result.dependency_fingerprint
                exit_code = result.exit_code
                artifacts = tuple(result.artifacts)
        summary = {
            'schema_version': 1,
            'run_id': layout.run.run_id,
            'stage_status': status.value,
            'reason': reason,
            'execution_started': execution_started,
            'deterministic_seed': deterministic_seed,
            'script': None if script_evidence is None else {
                'evidence_id': script_evidence.evidence_id,
                'relative_path': script_evidence.relative_path,
                'sha256': script_evidence.sha256,
            },
            'interpreter': interpreter,
            'dependency_fingerprint': dependency_fingerprint,
            'command': list(command),
            'exit_code': exit_code,
            'stdout_path': None,
            'stderr_path': None,
            'resource_limits': asdict(self.limits),
        }
        _atomic_create(summary_path, json.dumps(summary, ensure_ascii=False, sort_keys=True,
                                                 separators=(',', ':')).encode('utf-8'))
        summary_evidence = _evidence(layout, summary_relative, 'validation', summary_path)
        return ComputationOutcome(status, execution_started, reason, deterministic_seed, script_path, summary_path,
                                  script_evidence, summary_evidence, command, interpreter,
                                  dependency_fingerprint, exit_code, None, None, artifacts)





