from dataclasses import dataclass, replace
from enum import Enum
import re

from mathmodel_paper_studio.agents.preflight import Provider
from mathmodel_paper_studio.domain.models import (
    Run, RunStatus, Stage, StageRecord, StageStatus, identifier, relative_path, sha256_digest,
)

WORKFLOW_STAGES = (
    Stage.PREFLIGHT, Stage.DATA_PROFILE, Stage.PROBLEM_DECOMPOSITION, Stage.MODEL_PROPOSAL,
    Stage.CODE_GENERATION, Stage.CALCULATION, Stage.RESULT_VALIDATION,
    Stage.PAPER_DRAFTING, Stage.DOCX_QA,
)


class PauseReason(str, Enum):
    MISSING_DATA = 'missing_data'
    AMBIGUOUS_DATA = 'ambiguous_data'
    UNSUPPORTED_TEMPLATE = 'unsupported_template'
    PREFLIGHT_FAILED = 'preflight_failed'
    ADAPTER_UNAVAILABLE = 'adapter_unavailable'
    AUTH_REQUIRED = 'auth_required'
    QUOTA_EXHAUSTED = 'quota_exhausted'
    VALIDATION_FAILED = 'validation_failed'
    RETRY_LIMIT = 'retry_limit'
    OUTPUT_CONFLICT = 'output_conflict'
    CHECKPOINT_UNCERTAIN = 'checkpoint_uncertain'
    INPUT_CHANGED = 'input_changed'


_OUTPUT_NAME = re.compile(r'^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$')
_RESERVED_OUTPUT = re.compile(r'^(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\.|$)', re.I)
_ALLOWED_KINDS = frozenset(('stage_output', 'script', 'table', 'figure', 'validation', 'paper'))
_MAX_STAGE_OUTPUT_BYTES = 16 * 1024 * 1024


@dataclass(frozen=True)
class StageOutput:
    name: str
    content: bytes
    kind: str = 'stage_output'

    def __post_init__(self):
        if (not isinstance(self.name, str) or not _OUTPUT_NAME.fullmatch(self.name)
                or _RESERVED_OUTPUT.match(self.name)):
            raise ValueError('阶段产物名称必须是简单文件名。')
        if self.name in ('.', '..') or self.name.endswith(('.', ' ')):
            raise ValueError('阶段产物名称无效。')
        if not isinstance(self.content, bytes) or len(self.content) > _MAX_STAGE_OUTPUT_BYTES:
            raise ValueError('阶段产物必须为不超过 16 MiB 的字节。')
        if self.kind not in _ALLOWED_KINDS:
            raise ValueError('阶段产物类型无效。')


@dataclass(frozen=True)
class ArtifactDigest:
    relative_path: str
    sha256: str
    evidence_id: str
    kind: str
    stage: Stage
    attempt: int
    delivery: int
    size_bytes: int

    def __post_init__(self):
        relative_path(self.relative_path)
        sha256_digest(self.sha256)
        identifier(self.evidence_id)
        object.__setattr__(self, 'stage', Stage(self.stage))
        if self.kind not in _ALLOWED_KINDS:
            raise ValueError('阶段产物类型无效。')
        if type(self.attempt) is not int or not 0 <= self.attempt <= 2:
            raise ValueError('修复次数必须在 0–2 之间。')
        if type(self.delivery) is not int or self.delivery < 1:
            raise ValueError('阶段投递序号必须为正整数。')
        if type(self.size_bytes) is not int or self.size_bytes < 0:
            raise ValueError('产物大小必须为非负整数。')


@dataclass(frozen=True)
class StageResult:
    stage: Stage
    attempt: int
    status: StageStatus
    outputs: tuple[StageOutput, ...] = ()
    diagnostic: str = ''
    pause_reason: PauseReason | None = None

    def __post_init__(self):
        object.__setattr__(self, 'stage', Stage(self.stage))
        object.__setattr__(self, 'status', StageStatus(self.status))
        if self.status not in (StageStatus.COMPLETED, StageStatus.PAUSED,
                               StageStatus.FAILED, StageStatus.CANCELLED):
            raise ValueError('阶段执行结果不能是 pending 或 running。')
        if type(self.attempt) is not int or not 0 <= self.attempt <= 2:
            raise ValueError('修复次数必须在 0–2 之间。')
        if not isinstance(self.outputs, (tuple, list)):
            raise ValueError('阶段产物必须是列表或元组。')
        outputs = tuple(self.outputs)
        if any(not isinstance(item, StageOutput) for item in outputs):
            raise TypeError('阶段产物类型无效。')
        if len({item.name.casefold() for item in outputs}) != len(outputs):
            raise ValueError('同一阶段投递中不能重复产物名称。')
        object.__setattr__(self, 'outputs', outputs)
        if not isinstance(self.diagnostic, str) or len(self.diagnostic) > 4000 or '\x00' in self.diagnostic:
            raise ValueError('阶段诊断无效或过长。')
        if self.pause_reason is not None:
            object.__setattr__(self, 'pause_reason', PauseReason(self.pause_reason))
        if self.status is StageStatus.PAUSED and self.pause_reason is None:
            object.__setattr__(self, 'pause_reason', PauseReason.ADAPTER_UNAVAILABLE)
        if self.status is not StageStatus.PAUSED and self.pause_reason is not None:
            raise ValueError('只有暂停阶段可以声明暂停原因。')


@dataclass(frozen=True)
class WorkflowState:
    run_id: str
    project_id: str
    provider: Provider
    status: RunStatus
    next_stage: Stage | None
    next_attempt: int = 0
    generation: int = 0
    repair_cycle: int = 0
    stage_records: tuple[StageRecord, ...] = ()
    artifacts: tuple[ArtifactDigest, ...] = ()
    pause_reason: PauseReason | None = None
    diagnostics: tuple[str, ...] = ()

    def __post_init__(self):
        identifier(self.run_id)
        identifier(self.project_id)
        object.__setattr__(self, 'provider', Provider(self.provider))
        object.__setattr__(self, 'status', RunStatus(self.status))
        if self.next_stage is not None:
            object.__setattr__(self, 'next_stage', Stage(self.next_stage))
        if type(self.next_attempt) is not int or not 0 <= self.next_attempt <= 3:
            raise ValueError('下一次尝试必须在 0–3 之间。')
        if type(self.generation) is not int or self.generation < 0:
            raise ValueError('检查点代数必须为非负整数。')
        if type(self.repair_cycle) is not int or self.repair_cycle < 0:
            raise ValueError('修复周期必须为非负整数。')
        if not isinstance(self.stage_records, (tuple, list)) or not isinstance(self.artifacts, (tuple, list)):
            raise ValueError('阶段记录和产物必须是列表或元组。')
        records, artifacts = tuple(self.stage_records), tuple(self.artifacts)
        if any(not isinstance(item, StageRecord) or item.run_id != self.run_id for item in records):
            raise ValueError('阶段记录与运行不匹配。')
        if any(item.status not in (StageStatus.COMPLETED, StageStatus.PAUSED,
                                   StageStatus.FAILED, StageStatus.CANCELLED) for item in records):
            raise ValueError('已提交阶段记录不能处于 pending 或 running。')
        if any(not isinstance(item, ArtifactDigest) for item in artifacts):
            raise ValueError('阶段产物摘要类型无效。')
        if len(records) > 4096 or len(artifacts) > 4096:
            raise ValueError('阶段历史或工件清单超过上限。')
        if len({item.relative_path for item in artifacts}) != len(artifacts):
            raise ValueError('阶段产物相对路径重复。')
        if any(item.relative_path.split('/')[:2] != ['90_runs', self.run_id]
               for item in artifacts):
            raise ValueError('阶段产物必须位于本次运行目录。')
        object.__setattr__(self, 'stage_records', records)
        object.__setattr__(self, 'artifacts', artifacts)
        if self.pause_reason is not None:
            object.__setattr__(self, 'pause_reason', PauseReason(self.pause_reason))
        if self.status is RunStatus.PAUSED and self.pause_reason is None:
            raise ValueError('暂停运行必须记录暂停原因。')
        if self.status is not RunStatus.PAUSED and self.pause_reason is not None:
            raise ValueError('非暂停运行不能携带暂停原因。')
        if not isinstance(self.diagnostics, (tuple, list)) or len(self.diagnostics) > 256:
            raise ValueError('诊断集合无效或超过上限。')
        diagnostics = tuple(self.diagnostics)
        if any(not isinstance(item, str) or len(item) > 4000 or '\x00' in item for item in diagnostics):
            raise ValueError('运行诊断无效或过长。')
        object.__setattr__(self, 'diagnostics', diagnostics)
        if self.status is RunStatus.COMPLETED and self.next_stage is not None:
            raise ValueError('已完成运行不能保留下一阶段。')
        if self.status not in (RunStatus.COMPLETED, RunStatus.FAILED, RunStatus.CANCELLED) and self.next_stage is None:
            raise ValueError('未终止运行必须保留下一阶段。')

    @classmethod
    def initial(cls, run: Run) -> 'WorkflowState':
        if not isinstance(run, Run) or run.status is not RunStatus.CREATED:
            raise ValueError('新工作流必须从 created 运行记录开始。')
        return cls(run.run_id, run.project_id, run.provider, RunStatus.CREATED, WORKFLOW_STAGES[0])

    def start(self) -> 'WorkflowState':
        if self.status is not RunStatus.CREATED:
            raise ValueError('只有新建运行可以启动。')
        return replace(self, status=RunStatus.RUNNING, generation=self.generation + 1)

    def record(self, result: StageResult, artifacts=(), retry_policy=None) -> 'WorkflowState':
        if self.status is not RunStatus.RUNNING or self.next_stage is None:
            raise ValueError('当前运行不能接收阶段结果。')
        if not isinstance(result, StageResult):
            raise TypeError('阶段结果类型无效。')
        if result.stage is not self.next_stage or result.attempt != self.next_attempt:
            raise ValueError('阶段结果与当前有序阶段或尝试次数不匹配。')
        refs = tuple(artifacts)
        if any(not isinstance(item, ArtifactDigest) or item.stage is not result.stage
               or item.attempt != result.attempt for item in refs):
            raise ValueError('阶段产物摘要与当前结果不匹配。')
        if any(item.relative_path in {old.relative_path for old in self.artifacts} for item in refs):
            raise ValueError('阶段产物不能覆盖已记录路径。')
        evidence_ids = tuple(item.evidence_id for item in refs)
        if len(set(evidence_ids)) != len(evidence_ids):
            raise ValueError('同一阶段不能重复记录证据标识。')
        record = StageRecord(self.run_id, result.stage, result.status, result.attempt, evidence_ids)
        records = self.stage_records + (record,)
        all_artifacts = self.artifacts + refs
        diagnostics = (self.diagnostics + ((result.diagnostic,) if result.diagnostic else ()))[-256:]
        next_stage, next_attempt = self.next_stage, self.next_attempt
        reason = result.pause_reason
        status = self.status
        if result.status is StageStatus.COMPLETED:
            index = WORKFLOW_STAGES.index(result.stage)
            next_stage = WORKFLOW_STAGES[index + 1] if index + 1 < len(WORKFLOW_STAGES) else None
            next_attempt = 0
            status = RunStatus.COMPLETED if next_stage is None else RunStatus.RUNNING
            reason = None
        elif result.status is StageStatus.PAUSED:
            status, reason = RunStatus.PAUSED, result.pause_reason
        elif result.status is StageStatus.CANCELLED:
            status, reason = RunStatus.CANCELLED, None
        else:
            from mathmodel_paper_studio.workflow.retry_policy import RetryPolicy
            policy = retry_policy or RetryPolicy()
            retry = policy.next_attempt(result.stage, result.attempt)
            if retry is not None:
                next_attempt = retry
                status, reason = RunStatus.RUNNING, None
            elif result.stage in policy.retryable_stages and result.attempt >= policy.max_repair_attempts:
                next_attempt = policy.max_repair_attempts + 1
                status, reason = RunStatus.PAUSED, PauseReason.RETRY_LIMIT
                if not result.diagnostic:
                    diagnostics = (diagnostics + ('自动修复次数已达上限，需要人工选择恢复或取消。',))[-256:]
            else:
                status, reason = RunStatus.FAILED, None
        return replace(self, status=status, next_stage=next_stage, next_attempt=next_attempt,
                       generation=self.generation + 1, stage_records=records,
                       artifacts=all_artifacts, pause_reason=reason, diagnostics=diagnostics)

    def resume(self) -> 'WorkflowState':
        if self.status is not RunStatus.PAUSED:
            raise ValueError('只有暂停运行可以恢复。')
        attempt = self.next_attempt
        cycle = self.repair_cycle
        diagnostics = self.diagnostics
        if self.pause_reason is PauseReason.RETRY_LIMIT:
            attempt, cycle = 0, cycle + 1
            diagnostics = (diagnostics + ('用户选择恢复；启动新的、有上限的修复周期。',))[-256:]
        return replace(self, status=RunStatus.RUNNING, next_attempt=attempt,
                       generation=self.generation + 1, repair_cycle=cycle,
                       pause_reason=None, diagnostics=diagnostics)

    def cancel(self) -> 'WorkflowState':
        if self.status not in (RunStatus.CREATED, RunStatus.RUNNING, RunStatus.PAUSED):
            raise ValueError('当前运行不能取消。')
        return replace(self, status=RunStatus.CANCELLED, generation=self.generation + 1,
                       pause_reason=None)

    def to_dict(self) -> dict:
        return {
            'run_id': self.run_id, 'project_id': self.project_id, 'provider': self.provider.value,
            'status': self.status.value, 'next_stage': None if self.next_stage is None else self.next_stage.value,
            'next_attempt': self.next_attempt, 'generation': self.generation, 'repair_cycle': self.repair_cycle,
            'pause_reason': None if self.pause_reason is None else self.pause_reason.value,
            'diagnostics': list(self.diagnostics),
            'stage_records': [
                {'run_id': item.run_id, 'stage': item.stage.value, 'status': item.status.value,
                 'attempt': item.attempt, 'evidence_ids': list(item.evidence_ids)}
                for item in self.stage_records
            ],
            'artifacts': [
                {'relative_path': item.relative_path, 'sha256': item.sha256, 'evidence_id': item.evidence_id,
                 'kind': item.kind, 'stage': item.stage.value, 'attempt': item.attempt,
                 'delivery': item.delivery, 'size_bytes': item.size_bytes}
                for item in self.artifacts
            ],
        }

    @classmethod
    def from_dict(cls, value: dict) -> 'WorkflowState':
        expected = {'run_id', 'project_id', 'provider', 'status', 'next_stage', 'next_attempt',
                    'generation', 'repair_cycle', 'pause_reason', 'diagnostics', 'stage_records', 'artifacts'}
        if not isinstance(value, dict) or set(value) != expected:
            raise ValueError('检查点工作流状态字段无效。')
        if (not isinstance(value['stage_records'], list) or len(value['stage_records']) > 4096
                or not isinstance(value['artifacts'], list) or len(value['artifacts']) > 4096
                or not isinstance(value['diagnostics'], list)):
            raise ValueError('检查点阶段历史、工件或诊断类型无效。')
        record_fields = {'run_id', 'stage', 'status', 'attempt', 'evidence_ids'}
        if any(not isinstance(item, dict) or set(item) != record_fields
               or not isinstance(item['evidence_ids'], list) for item in value['stage_records']):
            raise ValueError('检查点阶段记录字段无效。')
        artifact_fields = {'relative_path', 'sha256', 'evidence_id', 'kind', 'stage',
                           'attempt', 'delivery', 'size_bytes'}
        if any(not isinstance(item, dict) or set(item) != artifact_fields
               for item in value['artifacts']):
            raise ValueError('检查点工件记录字段无效。')
        records = tuple(StageRecord(
            item['run_id'], item['stage'], item['status'], item['attempt'], tuple(item['evidence_ids'])
        ) for item in value['stage_records'])
        artifacts = tuple(ArtifactDigest(
            item['relative_path'], item['sha256'], item['evidence_id'], item['kind'], item['stage'],
            item['attempt'], item['delivery'], item['size_bytes'],
        ) for item in value['artifacts'])
        return cls(
            value['run_id'], value['project_id'], value['provider'], value['status'], value['next_stage'],
            value['next_attempt'], value['generation'], value['repair_cycle'], records, artifacts,
            value['pause_reason'], tuple(value['diagnostics']),
        )
