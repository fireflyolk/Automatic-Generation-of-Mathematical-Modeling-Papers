"""Bounded, in-memory view of safe workflow progress events."""
from collections import deque

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox, QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QPlainTextEdit,
    QVBoxLayout, QWidget,
)
from qfluentwidgets import CaptionLabel, CardWidget, SubtitleLabel

from mathmodel_paper_studio.domain.models import identifier
from mathmodel_paper_studio.workflow.progress import ProgressKind, RunProgressEvent


MAX_TIMELINE_EVENTS = 2048
_FILTERS = (
    ('全部事件', 'all'),
    ('阶段', 'stage'),
    ('Agent', 'agent'),
    ('产物', 'artifact'),
    ('校验与错误', 'validation_error'),
)
_REVIEW_STATUS = {
    'pending': '待审核',
    'passed': '已通过',
    'warning': '需人工复核',
    'failed': '未通过',
}
_STAGE_TEXT = {
    'preflight': '运行预检', 'data_profile': '数据概况',
    'problem_decomposition': '问题拆解', 'model_proposal': '模型方案',
    'code_generation': '代码生成', 'calculation': '计算与分析',
    'result_validation': '结果验证', 'paper_drafting': '论文撰写',
    'docx_qa': 'Word 质量检查',
}


class RunTimelineWidget(QWidget):
    """Display run events and artifact review outcomes without persisting them."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName('runTimeline')
        self.setAccessibleName('Agent 实时运行过程')
        self._run_id = ''
        self._events = deque(maxlen=MAX_TIMELINE_EVENTS)
        self._artifact_rows = {}

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)
        header = QHBoxLayout()
        title = SubtitleLabel('Agent 运行过程', self)
        title.setObjectName('runTimelineTitle')
        header.addWidget(title, 1)
        self.filter_combo = QComboBox(self)
        self.filter_combo.setObjectName('runTimelineFilter')
        self.filter_combo.setAccessibleName('运行事件筛选')
        for label, value in _FILTERS:
            self.filter_combo.addItem(label, value)
        header.addWidget(self.filter_combo)
        layout.addLayout(header)

        self.event_list = QListWidget(self)
        self.event_list.setObjectName('runTimelineEventList')
        self.event_list.setAccessibleName('Agent 实时事件列表')
        self.event_list.setUniformItemSizes(True)
        layout.addWidget(self.event_list, 3)

        self.detail_view = QPlainTextEdit(self)
        self.detail_view.setObjectName('runTimelineDetail')
        self.detail_view.setAccessibleName('所选运行事件详情')
        self.detail_view.setReadOnly(True)
        self.detail_view.setMinimumHeight(72)
        self.detail_view.setPlaceholderText('选择事件查看完整安全详情。')
        layout.addWidget(self.detail_view, 1)

        artifact_card = CardWidget(self)
        artifact_layout = QVBoxLayout(artifact_card)
        artifact_layout.setContentsMargins(8, 8, 8, 8)
        artifact_layout.addWidget(CaptionLabel('本次运行产物与审核状态', artifact_card))
        self.artifact_list = QListWidget(artifact_card)
        self.artifact_list.setObjectName('runTimelineArtifacts')
        self.artifact_list.setAccessibleName('运行产物和审核状态')
        self.artifact_list.setMaximumHeight(150)
        artifact_layout.addWidget(self.artifact_list)
        layout.addWidget(artifact_card)

        self.filter_combo.currentIndexChanged.connect(self._rebuild_event_list)
        self.event_list.currentItemChanged.connect(self._show_selected_detail)

    @property
    def run_id(self):
        return self._run_id

    @property
    def event_count(self):
        return len(self._events)

    @property
    def events(self):
        """Return the retained in-memory events in delivery order."""
        return tuple(self._events)

    def reset_for_run(self, run_id):
        self._run_id = identifier(run_id)
        self._events.clear()
        self._artifact_rows.clear()
        self.event_list.clear()
        self.detail_view.clear()
        self.artifact_list.clear()

    def append_event(self, event):
        if not isinstance(event, RunProgressEvent) or event.run_id != self._run_id:
            return False
        if self._events and event.sequence <= self._events[-1].sequence:
            return False

        bar = self.event_list.verticalScrollBar()
        follow_tail = bar.value() >= bar.maximum() - 1
        evicted_sequence = (self._events[0].sequence
                            if len(self._events) == MAX_TIMELINE_EVENTS else None)
        self._events.append(event)
        if evicted_sequence is not None:
            self._remove_visible_sequence(evicted_sequence)
        if self._matches_filter(event):
            self.event_list.addItem(self._make_item(event))
        if follow_tail:
            self.event_list.scrollToBottom()
            if self.event_list.count():
                self.event_list.setCurrentRow(self.event_list.count() - 1)
        else:
            bar.setValue(min(bar.value(), bar.maximum()))
        if event.kind is ProgressKind.ARTIFACT_CREATED and event.artifact is not None:
            self._upsert_artifact(event.artifact.evidence_id, event.artifact.relative_path,
                                  event.artifact.kind, 'pending', '等待产物校验')
        return True

    def set_artifact_review_status(self, evidence_id, status, summary=''):
        evidence_id = identifier(evidence_id)
        status = str(status).casefold()
        if status not in _REVIEW_STATUS:
            raise ValueError('产物审核状态无效。')
        current = self._artifact_rows.get(evidence_id)
        if current is None:
            return False
        relative_path, kind, _old_status, _old_summary = current
        self._upsert_artifact(evidence_id, relative_path, kind, status, str(summary)[:1000])
        return True

    def _upsert_artifact(self, evidence_id, relative_path, kind, status, summary):
        self._artifact_rows[evidence_id] = (relative_path, kind, status, summary)
        display = f'{kind.upper()}  {relative_path}  ·  {_REVIEW_STATUS[status]}'
        if summary:
            display += f'  ·  {summary}'
        for row in range(self.artifact_list.count()):
            item = self.artifact_list.item(row)
            if item.data(Qt.ItemDataRole.UserRole) == evidence_id:
                item.setText(display)
                item.setToolTip(display)
                return
        item = QListWidgetItem(display)
        item.setData(Qt.ItemDataRole.UserRole, evidence_id)
        item.setToolTip(display)
        self.artifact_list.addItem(item)

    def _remove_visible_sequence(self, sequence):
        for row in range(self.event_list.count()):
            if self.event_list.item(row).data(Qt.ItemDataRole.UserRole) == sequence:
                self.event_list.takeItem(row)
                return

    def _make_item(self, event):
        stage = (f' · {_STAGE_TEXT.get(event.stage.value, event.stage.value)}'
                 if event.stage is not None else '')
        attempt = f' · 第 {event.attempt + 1} 次' if event.attempt is not None else ''
        time_text = event.occurred_at_utc.astimezone().strftime('%H:%M:%S')
        item = QListWidgetItem(f'{time_text}  #{event.sequence}{stage}{attempt}  {event.title}')
        item.setData(Qt.ItemDataRole.UserRole, event.sequence)
        item.setToolTip(event.title)
        return item

    def _matches_filter(self, event):
        selected = self.filter_combo.currentData()
        if selected == 'all':
            return True
        if selected == 'stage':
            return event.kind in {
                ProgressKind.RUN_STARTED, ProgressKind.RUN_PAUSED,
                ProgressKind.RUN_CANCELLED, ProgressKind.RUN_FAILED,
                ProgressKind.RUN_COMPLETED, ProgressKind.STAGE_STARTED,
                ProgressKind.STAGE_PROGRESS, ProgressKind.PAPER_PREVIEW_UPDATED,
                ProgressKind.STAGE_COMPLETED,
                ProgressKind.STAGE_PAUSED, ProgressKind.STAGE_FAILED,
                ProgressKind.STAGE_CANCELLED,
            }
        if selected == 'agent':
            return event.kind in {
                ProgressKind.AGENT_REQUEST_STARTED, ProgressKind.AGENT_WAITING,
                ProgressKind.AGENT_RETRYING,
                ProgressKind.AGENT_MESSAGE, ProgressKind.AGENT_COMPLETED,
                ProgressKind.AGENT_CANCELLED, ProgressKind.AGENT_FAILED,
                ProgressKind.AGENT_BLOCKED,
            }
        if selected == 'artifact':
            return event.kind in {ProgressKind.ARTIFACT_CREATED, ProgressKind.ARTIFACT_REVIEWED}
        if selected == 'validation_error':
            return event.kind in {
                ProgressKind.VALIDATION_STARTED, ProgressKind.VALIDATION_PASSED,
                ProgressKind.VALIDATION_FAILED, ProgressKind.RUN_FAILED,
                ProgressKind.STAGE_FAILED, ProgressKind.AGENT_FAILED,
                ProgressKind.AGENT_BLOCKED, ProgressKind.EVENTS_OMITTED,
            }
        return False

    def _rebuild_event_list(self, *_args):
        current = self.event_list.currentItem()
        selected_sequence = (None if current is None
                             else current.data(Qt.ItemDataRole.UserRole))
        self.event_list.clear()
        selected_row = -1
        for event in self._events:
            if not self._matches_filter(event):
                continue
            item = self._make_item(event)
            self.event_list.addItem(item)
            if item.data(Qt.ItemDataRole.UserRole) == selected_sequence:
                selected_row = self.event_list.count() - 1
        if selected_row >= 0:
            self.event_list.setCurrentRow(selected_row)
        elif self.event_list.count():
            self.event_list.setCurrentRow(0)
        else:
            self.detail_view.clear()

    def _show_selected_detail(self, current, _previous=None):
        if current is None:
            self.detail_view.clear()
            return
        sequence = current.data(Qt.ItemDataRole.UserRole)
        event = next((item for item in self._events if item.sequence == sequence), None)
        if event is None:
            self.detail_view.clear()
            return
        details = [f'#{event.sequence} · {event.occurred_at_utc.isoformat()} · {event.kind.value}']
        if event.stage is not None:
            details.append(f'阶段：{_STAGE_TEXT.get(event.stage.value, event.stage.value)}')
        if event.attempt is not None:
            details.append(f'尝试：{event.attempt + 1}')
        if event.item_id is not None:
            details.append(f'事件项：{event.item_id}')
        details.append(event.title)
        if event.detail:
            details.append(event.detail)
        if event.artifact is not None:
            artifact = event.artifact
            details.append(
                f'产物：{artifact.relative_path} · {artifact.kind} · '
                f'{artifact.size_bytes} bytes · SHA-256 {artifact.sha256}'
            )
        self.detail_view.setPlainText('\n'.join(details))
