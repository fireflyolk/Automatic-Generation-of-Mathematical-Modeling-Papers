"""Validate declarative flowcharts and render them locally without executing input."""
from collections import deque
from dataclasses import dataclass
import re

from PySide6.QtCore import QBuffer, QIODevice, QPointF, QRectF, Qt
from PySide6.QtGui import QBrush, QColor, QFont, QFontMetrics, QImage, QPainter, QPen, QPolygonF


_ID = re.compile(r'^[a-z][a-z0-9_-]{0,31}$')
_SECTION_KEY = re.compile(r'^[a-z][a-z0-9_-]{0,63}$')
_NODE_KINDS = frozenset(('start', 'end', 'process', 'decision'))
_WIDTH = 1260
_NODE_WIDTH = 310
_NODE_HEIGHT = 112
_LEVEL_HEIGHT = 200


@dataclass(frozen=True)
class FlowchartNode:
    node_id: str
    label: str
    kind: str
    level: int
    column: int


@dataclass(frozen=True)
class FlowchartEdge:
    source: str
    target: str
    label: str


@dataclass(frozen=True)
class FlowchartSpec:
    section_key: str
    caption: str
    explanation: str
    nodes: tuple[FlowchartNode, ...]
    edges: tuple[FlowchartEdge, ...]


def parse_flowchart(value, allowed_section_keys) -> FlowchartSpec:
    """Accept only a bounded acyclic graph of plain labels and supported shapes."""
    fields = {'section_key', 'caption', 'explanation', 'nodes', 'edges'}
    if not isinstance(value, dict) or set(value) != fields:
        raise ValueError('流程图字段无效。')
    section_key = value['section_key']
    if (not isinstance(section_key, str) or _SECTION_KEY.fullmatch(section_key) is None
            or section_key not in allowed_section_keys):
        raise ValueError('流程图必须锚定到已确认的正文标题。')
    caption = _bounded_label(value['caption'], 6, 120, '流程图标题')
    explanation = _bounded_label(value['explanation'], 30, 1500, '流程图说明')
    raw_nodes, raw_edges = value['nodes'], value['edges']
    if not isinstance(raw_nodes, list) or not 4 <= len(raw_nodes) <= 12:
        raise ValueError('流程图节点数必须在 4 到 12 之间。')
    if not isinstance(raw_edges, list) or not 3 <= len(raw_edges) <= 24:
        raise ValueError('流程图连线数必须在 3 到 24 之间。')

    nodes_by_id = {}
    order = []
    for raw in raw_nodes:
        if not isinstance(raw, dict) or set(raw) != {'id', 'label', 'kind'}:
            raise ValueError('流程图节点字段无效。')
        node_id, kind = raw['id'], raw['kind']
        if not isinstance(node_id, str) or _ID.fullmatch(node_id) is None or node_id in nodes_by_id:
            raise ValueError('流程图节点标识无效或重复。')
        if kind not in _NODE_KINDS:
            raise ValueError('流程图节点形状不受支持。')
        nodes_by_id[node_id] = (kind, _bounded_label(raw['label'], 1, 80, '流程图节点标签'))
        order.append(node_id)
    starts = [key for key, (kind, _) in nodes_by_id.items() if kind == 'start']
    ends = [key for key, (kind, _) in nodes_by_id.items() if kind == 'end']
    if len(starts) != 1 or not ends:
        raise ValueError('流程图必须有一个起点和至少一个终点。')

    adjacency = {key: [] for key in order}
    reverse = {key: [] for key in order}
    indegree = {key: 0 for key in order}
    edge_keys = set()
    edges = []
    for raw in raw_edges:
        if not isinstance(raw, dict) or set(raw) != {'from', 'to', 'label'}:
            raise ValueError('流程图连线字段无效。')
        source, target = raw['from'], raw['to']
        if (not isinstance(source, str) or not isinstance(target, str)
                or source not in nodes_by_id or target not in nodes_by_id or source == target):
            raise ValueError('流程图连线端点无效。')
        key = (source, target)
        if key in edge_keys:
            raise ValueError('流程图包含重复连线。')
        edge_keys.add(key)
        label = _bounded_label(raw['label'], 0, 32, '流程图分支标签', allow_empty=True)
        adjacency[source].append(target)
        reverse[target].append(source)
        indegree[target] += 1
        edges.append(FlowchartEdge(source, target, label))
    if [key for key, degree in indegree.items() if degree == 0] != starts:
        raise ValueError('流程图必须从唯一指定的起点开始。')

    ready = deque(key for key in order if indegree[key] == 0)
    topological = []
    levels = {key: 0 for key in order}
    remaining = dict(indegree)
    while ready:
        current = ready.popleft()
        topological.append(current)
        for target in adjacency[current]:
            levels[target] = max(levels[target], levels[current] + 1)
            remaining[target] -= 1
            if remaining[target] == 0:
                ready.append(target)
    if len(topological) != len(order):
        raise ValueError('流程图不允许包含循环连线。')
    if max(levels.values()) > 7:
        raise ValueError('流程图纵向层级超过上限。')

    reachable = _reachable(starts[0], adjacency)
    reaches_end = _reachable_from_ends(ends, reverse)
    if reachable != set(order) or reaches_end != set(order):
        raise ValueError('流程图必须连通，且每个节点都能从起点到达某个终点。')
    grouped = {}
    for node_id in order:
        grouped.setdefault(levels[node_id], []).append(node_id)
    if any(len(items) > 3 for items in grouped.values()):
        raise ValueError('流程图每一层最多容纳 3 个节点。')
    nodes = tuple(
        FlowchartNode(node_id, nodes_by_id[node_id][1], nodes_by_id[node_id][0],
                      level, column)
        for level, ids in sorted(grouped.items())
        for column, node_id in enumerate(ids)
    )
    return FlowchartSpec(section_key, caption, explanation, nodes, tuple(edges))


def render_flowchart_png(spec: FlowchartSpec) -> bytes:
    """Render a validated flowchart into an in-memory PNG using Qt's raster painter."""
    if not isinstance(spec, FlowchartSpec):
        raise TypeError('A validated flowchart specification is required.')
    level_count = 1 + max(node.level for node in spec.nodes)
    height = 120 + level_count * _LEVEL_HEIGHT
    image = QImage(_WIDTH, height, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(QColor('#ffffff'))
    painter = QPainter(image)
    if not painter.isActive():
        raise RuntimeError('流程图绘制器无法启动。')
    try:
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
        title_font = QFont('Microsoft YaHei', 22, QFont.Weight.DemiBold)
        painter.setFont(title_font)
        painter.setPen(QColor('#17324d'))
        painter.drawText(QRectF(48, 20, _WIDTH - 96, 54), Qt.AlignmentFlag.AlignCenter, '流程图')
        positions = _positions(spec.nodes)
        _draw_edges(painter, spec.edges, positions)
        for node in spec.nodes:
            x, y = positions[node.node_id]
            _draw_node(painter, node, QRectF(x, y, _NODE_WIDTH, _NODE_HEIGHT))
    finally:
        painter.end()

    buffer = QBuffer()
    if not buffer.open(QIODevice.OpenModeFlag.WriteOnly):
        raise RuntimeError('流程图内存缓冲区无法打开。')
    try:
        if not image.save(buffer, 'PNG'):
            raise RuntimeError('流程图无法编码为 PNG。')
        result = bytes(buffer.data())
    finally:
        buffer.close()
    if not 8 <= len(result) <= 8 * 1024 * 1024:
        raise ValueError('本地流程图超出图片大小限制。')
    return result


def _bounded_label(value, minimum, maximum, name, *, allow_empty=False):
    if (not isinstance(value, str) or len(value) > maximum
            or (not allow_empty and not value.strip())
            or (allow_empty and len(value) < minimum)
            or any(ord(character) < 32 and character not in '\t' for character in value)):
        raise ValueError(f'{name}长度或字符无效。')
    return value.strip()


def _reachable(start, adjacency):
    seen, pending = set(), [start]
    while pending:
        current = pending.pop()
        if current in seen:
            continue
        seen.add(current)
        pending.extend(adjacency[current])
    return seen


def _reachable_from_ends(ends, reverse):
    seen, pending = set(), list(ends)
    while pending:
        current = pending.pop()
        if current in seen:
            continue
        seen.add(current)
        pending.extend(reverse[current])
    return seen


def _positions(nodes):
    grouped = {}
    for node in nodes:
        grouped.setdefault(node.level, []).append(node)
    positions = {}
    for level, items in grouped.items():
        cell_width = (_WIDTH - 96) / len(items)
        for column, node in enumerate(items):
            x = 48 + column * cell_width + (cell_width - _NODE_WIDTH) / 2
            y = 94 + level * _LEVEL_HEIGHT
            positions[node.node_id] = (x, y)
    return positions


def _draw_edges(painter, edges, positions):
    pen = QPen(QColor('#56718c'), 4, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap)
    painter.setPen(pen)
    painter.setBrush(QBrush(QColor('#56718c')))
    edge_font = QFont('Microsoft YaHei', 14)
    metrics = QFontMetrics(edge_font)
    for edge in edges:
        sx, sy = positions[edge.source]
        tx, ty = positions[edge.target]
        start = QPointF(sx + _NODE_WIDTH / 2, sy + _NODE_HEIGHT)
        end = QPointF(tx + _NODE_WIDTH / 2, ty - 12)
        painter.drawLine(start, end)
        _draw_arrow(painter, start, end)
        if edge.label:
            painter.setFont(edge_font)
            painter.setPen(QColor('#334c65'))
            mid_x = (start.x() + end.x()) / 2
            mid_y = (start.y() + end.y()) / 2
            label_rect = QRectF(mid_x - 100, mid_y - 24, 200, 30)
            painter.fillRect(label_rect.adjusted(-5, -2, 5, 2), QColor('#ffffff'))
            painter.drawText(label_rect, Qt.AlignmentFlag.AlignCenter, metrics.elidedText(
                edge.label, Qt.TextElideMode.ElideRight, 190))
            painter.setPen(pen)
            painter.setBrush(QBrush(QColor('#56718c')))


def _draw_arrow(painter, start, end):
    delta = end - start
    length = max(1.0, (delta.x() * delta.x() + delta.y() * delta.y()) ** 0.5)
    ux, uy = delta.x() / length, delta.y() / length
    tip = end
    base = QPointF(tip.x() - ux * 16, tip.y() - uy * 16)
    normal = QPointF(-uy * 8, ux * 8)
    painter.drawPolygon(QPolygonF([tip, base + normal, base - normal]))


def _draw_node(painter, node, rect):
    colors = {
        'start': ('#e4f3e8', '#39804a'),
        'end': ('#fce8e6', '#aa493b'),
        'process': ('#e8f1fb', '#3f6f9f'),
        'decision': ('#fff4d6', '#a17927'),
    }
    fill, border = colors[node.kind]
    painter.setPen(QPen(QColor(border), 4))
    painter.setBrush(QBrush(QColor(fill)))
    if node.kind == 'decision':
        polygon = QPolygonF([
            QPointF(rect.center().x(), rect.top()),
            QPointF(rect.right(), rect.center().y()),
            QPointF(rect.center().x(), rect.bottom()),
            QPointF(rect.left(), rect.center().y()),
        ])
        painter.drawPolygon(polygon)
        text_rect = rect.adjusted(56, 22, -56, -22)
    else:
        painter.drawRoundedRect(rect, 20, 20)
        text_rect = rect.adjusted(22, 18, -22, -18)
    font = QFont('Microsoft YaHei', 18, QFont.Weight.Medium)
    painter.setFont(font)
    painter.setPen(QColor('#1f2d3a'))
    text_flags = int(Qt.AlignmentFlag.AlignCenter) | int(Qt.TextFlag.TextWordWrap)
    painter.drawText(text_rect, text_flags, node.label)
