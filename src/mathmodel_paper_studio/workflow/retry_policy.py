from dataclasses import dataclass

from mathmodel_paper_studio.domain.models import Stage, StageStatus


@dataclass(frozen=True)
class RetryPolicy:
    max_repair_attempts: int = 2
    retryable_stages: tuple[Stage, ...] = (Stage.CALCULATION, Stage.RESULT_VALIDATION)

    def __post_init__(self):
        if type(self.max_repair_attempts) is not int or not 0 <= self.max_repair_attempts <= 2:
            raise ValueError('每个修复周期最多允许两次自动修复。')
        stages = tuple(Stage(item) for item in self.retryable_stages)
        if len(stages) != len(set(stages)) or any(
                item not in (Stage.CALCULATION, Stage.RESULT_VALIDATION) for item in stages):
            raise ValueError('只有计算和结果验证阶段允许自动修复。')
        object.__setattr__(self, 'retryable_stages', stages)

    def next_attempt(self, stage: Stage, attempt: int) -> int | None:
        stage = Stage(stage)
        if type(attempt) is not int or not 0 <= attempt <= 2:
            raise ValueError('修复次数必须在 0–2 之间。')
        if stage not in self.retryable_stages or attempt >= self.max_repair_attempts:
            return None
        return attempt + 1
