"""Responsive run status, bounded events, and cancel/resume controls."""
from PySide6.QtCore import QThreadPool, Qt, Signal
from PySide6.QtWidgets import (
    QHBoxLayout, QListWidget, QPlainTextEdit, QProgressBar, QScrollArea,
    QVBoxLayout, QWidget,
)
from qfluentwidgets import (
    BodyLabel, CaptionLabel, CardWidget, PrimaryPushButton, PushButton, SubtitleLabel, TitleLabel,
)

from mathmodel_paper_studio.agents.preflight import Provider
from mathmodel_paper_studio.domain.models import RunStatus
from mathmodel_paper_studio.domain.models import StageStatus
from mathmodel_paper_studio.ui.workers import FunctionWorker
from mathmodel_paper_studio.workflow.stages import WORKFLOW_STAGES

_STATUS_TEXT = {
    RunStatus.CREATED: '等待审核', RunStatus.RUNNING: '运行中', RunStatus.PAUSED: '已暂停',
    RunStatus.COMPLETED: '已完成', RunStatus.FAILED: '运行失败', RunStatus.CANCELLED: '已取消',
}
_PROVIDER_TEXT = {Provider.CODEX: 'Codex', Provider.DEEPSEEK: 'DeepSeek API'}
_STAGE_TEXT = {
    'preflight': '运行预检', 'data_profile': '数据概况',
    'problem_decomposition': '问题拆解', 'model_proposal': '模型方案',
    'code_generation': '代码生成', 'calculation': '计算与分析',
    'result_validation': '结果验证', 'paper_drafting': '论文撰写', 'docx_qa': 'Word 质量检查',
}
_STAGE_STATUS_TEXT = {
    StageStatus.PENDING: '等待中', StageStatus.RUNNING: '运行中',
    StageStatus.PAUSED: '已暂停', StageStatus.COMPLETED: '已完成',
    StageStatus.FAILED: '失败', StageStatus.CANCELLED: '已取消',
}


class RunPanel(QWidget):
    operation_succeeded = Signal(object)
    operation_failed = Signal(str)
    event_received = Signal(str)
    resume_requested = Signal(str)
    cancel_requested = Signal(str)
    artifact_access_requested = Signal()

    def __init__(self, parent=None, *, thread_pool=None):
        super().__init__(parent)
        self.thread_pool = thread_pool or QThreadPool.globalInstance()
        self.run_id = ''
        self._worker = None
        self._worker_signals = None
        self._current_status = RunStatus.CREATED
        self._completed_stages = 0
        self._stage_total = len(WORKFLOW_STAGES)
        self._resume_operation = None
        self._cancel_operation = None
        self.setObjectName('runPanel')
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(14)

        self.content_scroll_area = QScrollArea(self)
        self.content_scroll_area.setObjectName('runContentScrollArea')
        self.content_scroll_area.setAccessibleName('运行阶段和事件详情')
        self.content_scroll_area.setWidgetResizable(True)
        self.content_scroll_area.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff,
        )
        content = QWidget(self.content_scroll_area)
        content.setMinimumWidth(0)
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(0, 0, 8, 0)
        content_layout.setSpacing(14)

        summary_card = CardWidget(content)
        summary_card.setObjectName('runSummaryCard')
        summary_layout = QVBoxLayout(summary_card)
        summary_layout.addWidget(TitleLabel('运行与审核', summary_card))
        self.current_agent_label = BodyLabel('当前 Agent：尚未选择', summary_card)
        self.current_agent_label.setObjectName('currentRunAgent')
        summary_layout.addWidget(self.current_agent_label)
        self.status_label = SubtitleLabel('尚未选择运行。', summary_card)
        self.status_label.setObjectName('runStatus')
        self.status_label.setWordWrap(True)
        summary_layout.addWidget(self.status_label)
        self.current_stage_label = CaptionLabel('当前阶段：等待开始', summary_card)
        self.current_stage_label.setObjectName('currentRunStage')
        summary_layout.addWidget(self.current_stage_label)
        self.progress_label = CaptionLabel('阶段进度：尚未开始', summary_card)
        self.progress_label.setObjectName('runProgressLabel')
        summary_layout.addWidget(self.progress_label)
        self.progress_bar = QProgressBar(summary_card)
        self.progress_bar.setObjectName('runProgress')
        self.progress_bar.setTextVisible(False)
        self.progress_bar.setRange(0, self._stage_total)
        self.progress_bar.setValue(0)
        summary_layout.addWidget(self.progress_bar)
        content_layout.addWidget(summary_card)

        stages_card = CardWidget(content)
        stages_layout = QVBoxLayout(stages_card)
        stages_layout.addWidget(TitleLabel('阶段记录', stages_card))
        self.stage_list = QListWidget()
        self.stage_list.setObjectName('runStageList')
        self.stage_list.setAccessibleName('运行阶段记录')
        self.stage_list.setMinimumHeight(90)
        stages_layout.addWidget(self.stage_list)
        content_layout.addWidget(stages_card, 1)

        events_card = CardWidget(content)
        events_layout = QVBoxLayout(events_card)
        events_layout.addWidget(TitleLabel('运行事件', events_card))
        self.event_view = QPlainTextEdit()
        self.event_view.setObjectName('runEventView')
        self.event_view.setAccessibleName('运行事件和诊断')
        self.event_view.setReadOnly(True)
        self.event_view.document().setMaximumBlockCount(1024)
        self.event_view.setPlaceholderText('阶段事件和诊断会显示在这里。')
        events_layout.addWidget(self.event_view, 1)
        content_layout.addWidget(events_card, 1)
        self.content_scroll_area.setWidget(content)
        layout.addWidget(self.content_scroll_area, 1)

        artifact_row = QHBoxLayout()
        self.artifact_access_label = CaptionLabel(
            '运行产物和 Word 结构审核位于“项目与数据”页面。', self,
        )
        self.artifact_access_label.setWordWrap(True)
        artifact_row.addWidget(self.artifact_access_label, 1)
        self.artifact_button = PushButton('查看产物审核', self)
        self.artifact_button.setObjectName('artifactReviewButton')
        self.artifact_button.setAccessibleName('查看运行产物审核')
        artifact_row.addWidget(self.artifact_button)
        layout.addLayout(artifact_row)

        actions = QHBoxLayout()
        self.cancel_button = PushButton('取消运行', self)
        self.cancel_button.setObjectName('cancelRunButton')
        self.cancel_button.setAccessibleName('取消运行')
        self.resume_button = PrimaryPushButton('继续运行', self)
        self.resume_button.setObjectName('resumeRunButton')
        self.resume_button.setAccessibleName('继续运行')
        self.cancel_button.setEnabled(False)
        self.resume_button.setEnabled(False)
        actions.addStretch(1)
        actions.addWidget(self.cancel_button)
        actions.addWidget(self.resume_button)
        layout.addLayout(actions)
        self.artifact_button.clicked.connect(self.artifact_access_requested.emit)
        self.cancel_button.clicked.connect(self.cancel)
        self.resume_button.clicked.connect(self.resume)

    @property
    def active_worker(self):
        return self._worker

    @property
    def current_status(self):
        return self._current_status

    def set_run_id(self, run_id: str):
        self.run_id = str(run_id)

    def set_provider(self, provider):
        try:
            provider = Provider(provider)
        except (TypeError, ValueError):
            label = '未支持的 Agent'
        else:
            label = _PROVIDER_TEXT.get(provider, '未支持的 Agent')
        self.current_agent_label.setText(f'当前 Agent：{label}')

    def set_run_state(self, status, message: str = ''):
        status = RunStatus(status)
        self._current_status = status
        self.status_label.setText(message or _STATUS_TEXT[status])
        if status is RunStatus.RUNNING:
            self.progress_bar.setRange(0, 0)
            self.progress_label.setText('阶段进度：后台任务正在处理')
            self.current_stage_label.setText('当前阶段：后台运行中')
        else:
            self.progress_bar.setRange(0, self._stage_total)
            value = self._stage_total if status is RunStatus.COMPLETED else self._completed_stages
            self.progress_bar.setValue(value)
            self.progress_label.setText(f'阶段进度：{value}/{self._stage_total} 个阶段完成')
        if status is RunStatus.PAUSED:
            self.current_stage_label.setText('当前阶段：已暂停')
        elif status in (RunStatus.COMPLETED, RunStatus.FAILED, RunStatus.CANCELLED):
            self.current_stage_label.setText('当前阶段：无（本次运行已结束）')
        self._refresh_controls()

    def _refresh_controls(self):
        active = self._worker is not None
        self.cancel_button.setEnabled(
            self._current_status in (RunStatus.CREATED, RunStatus.RUNNING, RunStatus.PAUSED)
            and '正在取消' not in self.status_label.text())
        self.resume_button.setEnabled(not active and self._current_status is RunStatus.PAUSED)

    def set_workflow_state(self, state):
        self.set_run_id(state.run_id)
        self.stage_list.clear()
        for record in state.stage_records:
            stage = _STAGE_TEXT.get(record.stage.value, record.stage.value)
            status = _STAGE_STATUS_TEXT.get(record.status, record.status.value)
            self.stage_list.addItem(f'{stage}：{status}（尝试 {record.attempt + 1}）')
        self._completed_stages = len({
            record.stage for record in state.stage_records
            if record.status is StageStatus.COMPLETED
        })
        if state.next_stage is not None:
            stage = _STAGE_TEXT.get(state.next_stage.value, state.next_stage.value)
            self.stage_list.addItem(f'下一阶段：{stage}')
        detail = state.diagnostics[-1] if state.diagnostics else ''
        if state.pause_reason is not None:
            detail = detail or f'暂停原因：{state.pause_reason.value}'
        self.set_run_state(state.status, _STATUS_TEXT[state.status] + (f'：{detail}' if detail else ''))
        if state.next_stage is not None:
            stage = _STAGE_TEXT.get(state.next_stage.value, state.next_stage.value)
            self.current_stage_label.setText(f'当前阶段：{stage}')
        else:
            self.current_stage_label.setText('当前阶段：无（本次运行已结束）')

    def start_operation(self, operation, *, resume_operation=None, cancel_operation=None):
        if self._worker is not None:
            raise RuntimeError('已有后台任务正在运行。')
        if not callable(operation):
            raise TypeError('运行任务必须是可调用对象。')
        self._resume_operation = resume_operation
        self._cancel_operation = cancel_operation
        self._launch(operation)

    def _launch(self, operation):
        self._worker = FunctionWorker(operation)
        self._worker_signals = self._worker.signals
        self._worker_signals.event_received.connect(self._on_event)
        self._worker_signals.succeeded.connect(self._on_success)
        self._worker_signals.failed.connect(self._on_failure)
        self._worker_signals.finished.connect(self._on_finished)
        self.set_run_state(RunStatus.RUNNING)
        self.thread_pool.start(self._worker)

    def _on_event(self, text: str):
        self.event_view.appendPlainText(text)
        self.event_received.emit(text)

    def _on_success(self, result):
        self.operation_succeeded.emit(result)
        if hasattr(result, 'stage_records') and hasattr(result, 'status'):
            self.set_workflow_state(result)
        elif isinstance(result, RunStatus):
            self.set_run_state(result)
        elif self._worker is not None and self._worker.cancel_event.is_set():
            self.set_run_state(RunStatus.CANCELLED, '运行已取消。')
        else:
            self.set_run_state(RunStatus.COMPLETED, '后台任务已完成。')

    def _on_failure(self, message: str):
        self.set_run_state(RunStatus.FAILED, f'运行失败：{message}')
        self.operation_failed.emit(message)

    def _on_finished(self):
        self._worker = None
        signals, self._worker_signals = self._worker_signals, None
        if signals is not None:
            signals.deleteLater()
        self._refresh_controls()

    def cancel(self):
        if self._worker is not None:
            self._worker.cancel()
            self.status_label.setText('正在取消；当前阶段收到取消信号后会停止。')
            self.cancel_button.setEnabled(False)
            return
        if self.run_id:
            if self._cancel_operation is not None:
                self._launch(self._cancel_operation)
            else:
                self.cancel_requested.emit(self.run_id)

    def resume(self):
        if self._worker is not None:
            return
        if self._resume_operation is not None:
            self._launch(self._resume_operation)
        elif self.run_id:
            self.resume_requested.emit(self.run_id)
