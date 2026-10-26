"""Shared resource limits for long-form paper generation and Word review."""

MAX_PAPER_EQUATIONS = 128
MAX_BATCH_EQUATIONS = 64


def equation_budget(already_committed):
    """Describe the remaining document capacity without discarding formulas."""
    if type(already_committed) is not int or not 0 <= already_committed <= MAX_PAPER_EQUATIONS:
        raise ValueError('已合并的公式数量无效或超过整篇上限。')
    remaining = MAX_PAPER_EQUATIONS - already_committed
    return {
        'document_maximum': MAX_PAPER_EQUATIONS,
        'already_committed': already_committed,
        'remaining_for_document': remaining,
        'maximum_for_batch': min(MAX_BATCH_EQUATIONS, remaining),
    }
