"""Keep optional unsupported diagrams from discarding validated paper prose."""
from dataclasses import dataclass
import re

from mathmodel_paper_studio.paper.flowchart_renderer import parse_flowchart


_SECTION_KEY = re.compile(r'^[a-z][a-z0-9_-]{0,63}$')
_REASONS = {
    'node_count': '节点数超出 4–12 的绘图范围',
    'cycle': '包含当前绘图器不支持的循环连线',
    'depth': '纵向层级超出绘图范围',
    'layout': '同层节点过多，无法按当前版式绘制',
    'invalid_graph': '结构、锚点或标签未通过本地绘图校验',
}
_KNOWN_ERRORS = {
    '流程图节点数必须在 4 到 12 之间。': 'node_count',
    '流程图不允许包含循环连线。': 'cycle',
    '流程图纵向层级超过上限。': 'depth',
    '流程图每一层最多容纳 3 个节点。': 'layout',
}


@dataclass(frozen=True)
class FlowchartOmission:
    section_key: str
    reason_code: str

    def __post_init__(self):
        if (not isinstance(self.section_key, str)
                or _SECTION_KEY.fullmatch(self.section_key) is None
                or self.reason_code not in _REASONS):
            raise ValueError('流程图待修订记录无效。')

    @property
    def review_note(self):
        return f'流程图未插入：{_REASONS[self.reason_code]}；需人工补图，本节正文已保留。'


def flowchart_request_constraints():
    return {
        'nodes': '4–12 nodes; prefer 4–8; exactly one start and at least one end',
        'edges': '3–24 unique edges; existing distinct endpoints only',
        'layout': 'A connected directed acyclic graph; no self-loops or back edges; '
                  'at most 8 levels and 3 nodes per level; every node reaches an end',
        'labels': 'Node labels 1–80 characters; edge labels 0–32 characters; '
                  'caption at most 120 characters; explanation 30–1500 characters',
        'iteration': 'Describe refinement in a forward process node instead of drawing a loop',
        'required': 'Every requested flowchart must be delivered. If a first version exceeds '
                    'these limits, simplify it (fewer nodes or levels) until it fits; '
                    'do not return an empty flowcharts list.',
    }


def supported_optional_flowcharts(value, *, expected_count, allowed_section_keys,
                                 fallback_section_key):
    """Copy a well-bounded response, filtering single unsupported optional graphs.

    Envelope/count and scientific-content validation remain the caller's job.
    Only parser-owned errors are turned into bounded application review notes;
    unsupported graph data is never passed to the renderer.
    """
    if not isinstance(value, dict):
        return value, ()
    charts = value.get('flowcharts')
    if (not isinstance(charts, list) or len(charts) > 12
            or len(charts) > expected_count):
        return value, ()
    accepted, omissions = [], []
    for chart in charts:
        try:
            parse_flowchart(chart, allowed_section_keys)
        except (ValueError, TypeError, KeyError) as exc:
            key = chart.get('section_key') if isinstance(chart, dict) else None
            if not isinstance(key, str) or key not in allowed_section_keys:
                key = fallback_section_key
            reason = _KNOWN_ERRORS.get(str(exc), 'invalid_graph')
            omissions.append(FlowchartOmission(key, reason))
        else:
            accepted.append(chart)
    return {**value, 'flowcharts': accepted}, tuple(omissions)
