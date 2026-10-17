import hashlib
import json
import os
from pathlib import Path
import re
from uuid import uuid4

from mathmodel_paper_studio.bootstrap.paths import validate_managed_path
from mathmodel_paper_studio.domain.models import Run
from mathmodel_paper_studio.execution.python_runner import RunArtifactLayout
from mathmodel_paper_studio.storage.repositories import RecordConflictError, RunRepository
from mathmodel_paper_studio.workflow.stages import WorkflowState

_CHECKPOINT_NAME = re.compile(r'^checkpoint-v([0-9]{6})\.json$')
_MAX_CHECKPOINT_BYTES = 16 * 1024 * 1024
_MAX_CHECKPOINT_FILES = 1024
_MAX_SCAN_BYTES = 256 * 1024 * 1024


class CheckpointRecoveryError(RuntimeError):
    """The persisted state is uncertain; callers must pause rather than replay."""


class CheckpointConflictError(RuntimeError):
    pass


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('duplicate checkpoint key')
        result[key] = value
    return result


def _reject_constant(value):
    raise ValueError('non-finite checkpoint value')


def _atomic_create(path: Path, payload: bytes) -> None:
    parent = validate_managed_path(path.parent)
    if not parent.is_dir() or path.exists():
        raise FileExistsError(path)
    temporary = parent / f'.{path.name}.{uuid4().hex}.tmp'
    validate_managed_path(temporary)
    try:
        with temporary.open('xb') as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        validate_managed_path(temporary)
        validate_managed_path(path)
        if path.exists():
            raise FileExistsError(path)
        temporary.rename(path)
        validate_managed_path(path)
    except BaseException:
        if temporary.exists():
            validate_managed_path(temporary)
            temporary.unlink()
        raise


def _hash_file(path: Path, byte_limit: int) -> str:
    digest = hashlib.sha256()
    total = 0
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(128 * 1024), b''):
            total += len(block)
            if total > byte_limit:
                raise CheckpointRecoveryError('检查点或工件超过读取上限。')
            digest.update(block)
    return digest.hexdigest()


class CheckpointStore:
    def __init__(self, layout: RunArtifactLayout, repository: RunRepository):
        if not isinstance(layout, RunArtifactLayout) or not isinstance(repository, RunRepository):
            raise TypeError('检查点存储需要运行目录和 RunRepository。')
        if layout.paths.root != repository.database.paths.root:
            raise ValueError('检查点和 SQLite 必须使用同一 F: 应用目录。')
        self.layout = layout
        self.repository = repository

    def persist(self, state: WorkflowState, current_run: Run) -> str:
        if not isinstance(state, WorkflowState) or not isinstance(current_run, Run):
            raise TypeError('检查点提交需要工作流状态和 Run 记录。')
        if (state.run_id, state.project_id, state.provider) != (
                current_run.run_id, current_run.project_id, current_run.provider):
            raise ValueError('检查点状态与 SQLite Run 记录不匹配。')
        if state.generation < 1:
            raise ValueError('首次检查点代数必须从 1 开始。')
        payload = json.dumps({'schema_version': 1, 'state': state.to_dict()},
                             ensure_ascii=False, sort_keys=True, separators=(',', ':'),
                             allow_nan=False).encode('utf-8')
        if len(payload) > _MAX_CHECKPOINT_BYTES:
            raise ValueError('检查点超过 16 MiB 上限。')
        relative = f'checkpoint-v{state.generation:06d}.json'
        path = self.layout.path_for(relative)
        try:
            _atomic_create(path, payload)
        except FileExistsError as exc:
            raise CheckpointConflictError('检查点版本已存在，拒绝覆盖旧状态。') from exc
        digest = hashlib.sha256(payload).hexdigest()
        try:
            self.repository.commit_checkpoint(
                current_run.run_id, digest, expected_status=current_run.status,
                expected_checkpoint_sha256=current_run.checkpoint_sha256,
                target_status=state.status,
            )
        except RecordConflictError:
            # The unreferenced version is intentionally retained. Recovery follows only the SQLite hash.
            raise
        return digest

    def recover(self, run: Run) -> WorkflowState:
        if not isinstance(run, Run) or run.run_id != self.layout.run.run_id:
            raise CheckpointRecoveryError('检查点运行身份不匹配。')
        if run.checkpoint_sha256 is None:
            raise CheckpointRecoveryError('SQLite 没有已提交的检查点摘要。')
        matches = []
        try:
            paths = tuple(self.layout.root.glob('checkpoint-v*.json'))
        except OSError as exc:
            raise CheckpointRecoveryError('无法扫描检查点目录。') from exc
        if len(paths) > _MAX_CHECKPOINT_FILES:
            raise CheckpointRecoveryError('检查点版本数量超过上限。')
        scanned = 0
        for candidate in paths:
            try:
                validate_managed_path(candidate)
                match = _CHECKPOINT_NAME.fullmatch(candidate.name)
                if match is None or candidate.is_symlink() or not candidate.is_file():
                    raise CheckpointRecoveryError('检查点目录包含不安全或未知条目。')
                size = candidate.stat().st_size
                scanned += size
                if size > _MAX_CHECKPOINT_BYTES or scanned > _MAX_SCAN_BYTES:
                    raise CheckpointRecoveryError('检查点扫描超过字节上限。')
                if _hash_file(candidate, _MAX_CHECKPOINT_BYTES) == run.checkpoint_sha256:
                    matches.append((candidate, int(match.group(1))))
            except CheckpointRecoveryError:
                raise
            except (OSError, ValueError) as exc:
                raise CheckpointRecoveryError('检查点路径或内容无法安全读取。') from exc
        if len(matches) != 1:
            raise CheckpointRecoveryError('SQLite 摘要对应的检查点缺失或不唯一。')
        path, version = matches[0]
        try:
            payload = path.read_bytes()
            document = json.loads(payload.decode('utf-8'), object_pairs_hook=_unique_object,
                                  parse_constant=_reject_constant)
            if (not isinstance(document, dict) or set(document) != {'schema_version', 'state'}
                    or type(document['schema_version']) is not int or document['schema_version'] != 1):
                raise ValueError('unsupported checkpoint schema')
            state = WorkflowState.from_dict(document['state'])
        except (OSError, UnicodeError, ValueError, TypeError, KeyError, RecursionError) as exc:
            raise CheckpointRecoveryError('检查点结构损坏，不能恢复或重放阶段。') from exc
        if (state.generation != version or state.run_id != run.run_id
                or state.project_id != run.project_id or state.provider is not run.provider
                or state.status is not run.status):
            raise CheckpointRecoveryError('检查点与 SQLite 的运行状态或版本指针不一致。')
        self._verify_artifacts(state)
        return state

    def _verify_artifacts(self, state: WorkflowState) -> None:
        run_prefix = self.layout.run.relative_path + '/'
        for item in state.artifacts:
            if not item.relative_path.startswith(run_prefix):
                raise CheckpointRecoveryError('检查点引用了运行目录之外的工件。')
            run_relative = item.relative_path[len(run_prefix):]
            try:
                path = self.layout.path_for(run_relative)
                validate_managed_path(path)
                if path.is_symlink() or not path.is_file() or path.stat().st_size != item.size_bytes:
                    raise ValueError('artifact is missing or changed')
                if _hash_file(path, 64 * 1024 * 1024) != item.sha256:
                    raise ValueError('artifact hash mismatch')
            except (OSError, ValueError) as exc:
                raise CheckpointRecoveryError('已完成阶段工件缺失或哈希变化，工作流保持暂停。') from exc
