"""Explicit finite budgets for calculation artifacts and a future trusted runner."""
from dataclasses import dataclass
import math


@dataclass(frozen=True)
class ComputationLimits:
    wall_seconds: float = 300.0
    memory_bytes: int = 1024 * 1024 * 1024
    stdout_bytes: int = 4 * 1024 * 1024
    stderr_bytes: int = 1024 * 1024
    artifact_bytes: int = 64 * 1024 * 1024
    validation_summary_bytes: int = 4 * 1024 * 1024
    max_artifacts: int = 128
    max_rows: int = 1_000_000
    max_columns: int = 512
    max_source_bytes: int = 2 * 1024 * 1024

    def __post_init__(self):
        try:
            finite_wall = (not isinstance(self.wall_seconds, bool)
                           and isinstance(self.wall_seconds, (int, float))
                           and math.isfinite(float(self.wall_seconds)))
        except (OverflowError, TypeError, ValueError):
            finite_wall = False
        if not finite_wall or not 0 < self.wall_seconds <= 3600:
            raise ValueError('计算时限必须为有限正数且不超过一小时。')
        bounds = {
            'memory_bytes': 16 * 1024 ** 3,
            'stdout_bytes': 64 * 1024 ** 2,
            'stderr_bytes': 16 * 1024 ** 2,
            'artifact_bytes': 256 * 1024 ** 2,
            'validation_summary_bytes': 16 * 1024 ** 2,
            'max_artifacts': 4096,
            'max_rows': 10_000_000,
            'max_columns': 4096,
            'max_source_bytes': 8 * 1024 ** 2,
        }
        for name, maximum in bounds.items():
            value = getattr(self, name)
            if type(value) is not int or not 0 < value <= maximum:
                raise ValueError(f'{name} 必须为范围内的有限正整数。')

