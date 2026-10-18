"""Immutable metadata records. Large content and credentials never belong here."""
from dataclasses import dataclass, replace
from enum import Enum
from pathlib import PurePosixPath
import re
from types import MappingProxyType

from mathmodel_paper_studio.agents.preflight import Provider, ProviderStatus

_IDENTIFIER = re.compile(r'^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$')
_SHA256 = re.compile(r'^[0-9a-f]{64}$')
_RESERVED = re.compile(r'^(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\.|$)', re.I)


def identifier(value: str) -> str:
    if not isinstance(value, str) or not _IDENTIFIER.fullmatch(value):
        raise ValueError('标识符应为 1–64 位字母、数字、下划线或短横线。')
    return value


def relative_path(value: str) -> str:
    if not isinstance(value, str) or not value or len(value) > 1024:
        raise ValueError('相对路径不能为空或过长。')
    if PurePosixPath(value).is_absolute() or '\\' in value:
        raise ValueError('必须使用项目内以 / 分隔的相对路径。')
    for part in value.split('/'):
        if (part in ('', '.', '..') or part.endswith((' ', '.')) or _RESERVED.match(part)
                or any(ord(c) < 32 or c in '<>:"|?*' for c in part)):
            raise ValueError('相对路径包含不允许的路径段。')
    return value


def canonical_directory_path(value: str, prefix: str, record_id: str | None = None) -> str:
    """App-owned project/run directories use fixed lowercase ASCII names."""
    relative_path(value)
    parts = value.split('/')
    if (prefix not in ('projects', '90_runs') or len(parts) != 2 or parts[0] != prefix
            or re.fullmatch(r'[a-z0-9][a-z0-9_-]{0,63}', parts[1]) is None):
        raise ValueError('项目和运行目录必须为固定前缀及小写 ASCII 标识符。')
    if record_id is not None and parts[1] != record_id:
        raise ValueError('目录标识符必须与记录的小写 ASCII 标识符一致。')
    return value

def sha256_digest(value: str) -> str:
    if not isinstance(value, str) or not _SHA256.fullmatch(value):
        raise ValueError('SHA-256 必须为 64 位小写十六进制字符串。')
    return value


class RunStatus(str, Enum):
    CREATED = 'created'
    RUNNING = 'running'
    PAUSED = 'paused'
    COMPLETED = 'completed'
    FAILED = 'failed'
    CANCELLED = 'cancelled'


class StageStatus(str, Enum):
    PENDING = 'pending'
    RUNNING = 'running'
    PAUSED = 'paused'
    COMPLETED = 'completed'
    FAILED = 'failed'
    CANCELLED = 'cancelled'


class Stage(str, Enum):
    PREFLIGHT = 'preflight'
    DATA_PROFILE = 'data_profile'
    PROBLEM_DECOMPOSITION = 'problem_decomposition'
    MODEL_PROPOSAL = 'model_proposal'
    CODE_GENERATION = 'code_generation'
    CALCULATION = 'calculation'
    RESULT_VALIDATION = 'result_validation'
    PAPER_DRAFTING = 'paper_drafting'
    DOCX_QA = 'docx_qa'


_RUN_TRANSITIONS = MappingProxyType({
    RunStatus.CREATED: frozenset((RunStatus.RUNNING, RunStatus.CANCELLED)),
    RunStatus.RUNNING: frozenset((RunStatus.PAUSED, RunStatus.COMPLETED, RunStatus.FAILED, RunStatus.CANCELLED)),
    RunStatus.PAUSED: frozenset((RunStatus.RUNNING, RunStatus.CANCELLED)),
})
_STAGE_TRANSITIONS = MappingProxyType({
    StageStatus.PENDING: frozenset((StageStatus.RUNNING, StageStatus.CANCELLED)),
    StageStatus.RUNNING: frozenset((StageStatus.PAUSED, StageStatus.COMPLETED, StageStatus.FAILED, StageStatus.CANCELLED)),
    StageStatus.PAUSED: frozenset((StageStatus.RUNNING, StageStatus.CANCELLED)),
})


@dataclass(frozen=True)
class Project:
    project_id: str
    title: str
    relative_path: str

    def __post_init__(self):
        identifier(self.project_id)
        if (not isinstance(self.title, str) or not self.title.strip() or len(self.title) > 200
                or any(ord(c) < 32 for c in self.title)):
            raise ValueError('项目标题应为 1–200 个可见字符。')
        canonical_directory_path(self.relative_path, 'projects', self.project_id)


@dataclass(frozen=True)
class SourceFile:
    source_id: str
    project_id: str
    relative_path: str
    sha256: str
    size_bytes: int
    kind: str

    def __post_init__(self):
        identifier(self.source_id)
        identifier(self.project_id)
        relative_path(self.relative_path)
        sha256_digest(self.sha256)
        if type(self.size_bytes) is not int or self.size_bytes < 0:
            raise ValueError('文件大小必须是非负整数。')
        if self.kind not in ('csv', 'xlsx', 'docx', 'docx_data', 'problem'):
            raise ValueError('不支持的输入类型。')


@dataclass(frozen=True)
class Run:
    run_id: str
    project_id: str
    provider: Provider
    status: RunStatus
    relative_path: str
    checkpoint_sha256: str | None = None

    def __post_init__(self):
        identifier(self.run_id)
        identifier(self.project_id)
        object.__setattr__(self, 'provider', Provider(self.provider))
        object.__setattr__(self, 'status', RunStatus(self.status))
        canonical_directory_path(self.relative_path, '90_runs', self.run_id)
        if self.checkpoint_sha256 is not None:
            sha256_digest(self.checkpoint_sha256)

    def transition(self, status: RunStatus, *, checkpoint_sha256: str | None = None) -> 'Run':
        target = RunStatus(status)
        if target not in _RUN_TRANSITIONS.get(self.status, frozenset()):
            raise ValueError(f'不允许的运行状态转换：{self.status.value} → {target.value}')
        return replace(self, status=target, checkpoint_sha256=(
            self.checkpoint_sha256 if checkpoint_sha256 is None else checkpoint_sha256))


@dataclass(frozen=True)
class StageRecord:
    run_id: str
    stage: Stage
    status: StageStatus
    attempt: int = 0
    evidence_ids: tuple[str, ...] = ()

    def __post_init__(self):
        identifier(self.run_id)
        object.__setattr__(self, 'stage', Stage(self.stage))
        object.__setattr__(self, 'status', StageStatus(self.status))
        if type(self.attempt) is not int or not 0 <= self.attempt <= 2:
            raise ValueError('阶段修复次数必须在 0–2 之间。')
        if not isinstance(self.evidence_ids, (tuple, list)):
            raise ValueError('证据标识符必须为列表或元组。')
        evidence = tuple(self.evidence_ids)
        for value in evidence:
            identifier(value)
        if len(evidence) != len(set(evidence)):
            raise ValueError('证据标识符不能重复。')
        object.__setattr__(self, 'evidence_ids', evidence)

    def transition(self, status: StageStatus) -> 'StageRecord':
        target = StageStatus(status)
        if target not in _STAGE_TRANSITIONS.get(self.status, frozenset()):
            raise ValueError(f'不允许的阶段状态转换：{self.status.value} → {target.value}')
        return replace(self, status=target)


@dataclass(frozen=True)
class ScopeManifest:
    """Persistable relative scope metadata; never a proof of confinement."""
    scope_id: str
    run_id: str
    provider: Provider
    project_relative_path: str
    readable_inputs: tuple[str, ...]
    writable_directories: tuple[str, ...]
    stages: tuple[Stage, ...]
    external_processing: bool

    def __post_init__(self):
        identifier(self.scope_id)
        identifier(self.run_id)
        object.__setattr__(self, 'provider', Provider(self.provider))
        canonical_directory_path(self.project_relative_path, 'projects')
        for field in ('readable_inputs', 'writable_directories'):
            if not isinstance(getattr(self, field), (tuple, list)):
                raise ValueError('范围路径必须为列表或元组。')
            values = tuple(getattr(self, field))
            for value in values:
                relative_path(value)
            if len(values) != len(set(values)):
                raise ValueError('范围路径不能重复。')
            object.__setattr__(self, field, values)
        if not isinstance(self.stages, (tuple, list)):
            raise ValueError('运行阶段必须为列表或元组。')
        stages = tuple(Stage(value) for value in self.stages)
        if not stages or len(stages) != len(set(stages)):
            raise ValueError('运行阶段不能为空或重复。')
        if type(self.external_processing) is not bool:
            raise ValueError('外部处理说明状态必须为布尔值。')
        object.__setattr__(self, 'stages', stages)


@dataclass(frozen=True)
class ArtifactEvidence:
    evidence_id: str
    run_id: str
    relative_path: str
    sha256: str
    kind: str
    verified: bool = False

    def __post_init__(self):
        identifier(self.evidence_id)
        identifier(self.run_id)
        relative_path(self.relative_path)
        sha256_digest(self.sha256)
        if self.kind not in ('script', 'table', 'figure', 'validation', 'paper', 'profile'):
            raise ValueError('不支持的证据类型。')
        if type(self.verified) is not bool:
            raise ValueError('证据验证状态必须为布尔值。')
