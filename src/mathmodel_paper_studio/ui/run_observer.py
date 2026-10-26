"""Modeless, bounded observation of public draft output for one workflow run."""
from PySide6.QtCore import Qt
from PySide6.QtGui import QFontDatabase, QTextCursor
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QDialog, QHBoxLayout, QLabel, QPlainTextEdit,
    QPushButton, QVBoxLayout,
)

from mathmodel_paper_studio.domain.models import identifier
from mathmodel_paper_studio.workflow.progress import RunProgressEvent


MAX_OBSERVER_CHARACTERS = 262144
_TRUNCATION_NOTICE = '[较早内容已移出观察窗口；仅保留最近的观察内容]\n'
_STAGE_TEXT = {
    'preflight': '运行预检', 'data_profile': '数据概况',
    'problem_decomposition': '问题拆解', 'model_proposal': '模型方案',
    'code_generation': '代码生成', 'calculation': '计算与分析',
    'result_validation': '结果验证', 'paper_drafting': '论文撰写',
    'docx_qa': 'Word 质量检查',
}
_TERMINAL_TEXT = {
    'stage_completed': '阶段完成', 'stage_paused': '阶段已暂停',
    'stage_failed': '阶段失败', 'stage_cancelled': '阶段已取消',
    'run_completed': '运行完成', 'run_paused': '运行已暂停',
    'run_failed': '运行失败', 'run_cancelled': '运行已取消',
    'agent_cancelled': '请求已取消；未完成草稿已丢弃',
    'agent_failed': '请求失败；未通过校验的草稿已丢弃',
    'agent_blocked': '请求已阻止；未通过校验的草稿已丢弃',
}


class RunObserverDialog(QDialog):
    """Show display-only public output; never start or alter a workflow."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._run_id = ''
        self._last_sequence = 0
        self._stage = ''
        self._has_content = False
        self.setObjectName('runObserverDialog')
        self.setWindowTitle('运行观察窗口')
        self.setModal(False)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, False)
        self.setWindowFlag(Qt.WindowType.WindowContextHelpButtonHint, False)
        self.resize(900, 620)
        self.setMinimumSize(420, 300)

        layout = QVBoxLayout(self)
        self.status_label = QLabel('等待运行。', self)
        self.status_label.setObjectName('runObserverStatus')
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)
        self.draft_notice = QLabel(
            '尚未校验：实时正文和代码仅供观察；请以产物审核通过的结果为准。', self,
        )
        self.draft_notice.setObjectName('runObserverDraftNotice')
        self.draft_notice.setWordWrap(True)
        layout.addWidget(self.draft_notice)
        self.text_view = QPlainTextEdit(self)
        self.text_view.setObjectName('runObserverText')
        self.text_view.setAccessibleName('实时正文和代码（尚未校验）')
        self.text_view.setReadOnly(True)
        self.text_view.setFont(QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont))
        self.text_view.setPlaceholderText('等待 Agent 输出正文或代码；等待期间不会显示内部推理。')
        layout.addWidget(self.text_view, 1)

        actions = QHBoxLayout()
        self.follow_checkbox = QCheckBox('跟随滚动', self)
        self.follow_checkbox.setObjectName('runObserverFollow')
        self.follow_checkbox.setChecked(True)
        actions.addWidget(self.follow_checkbox)
        actions.addStretch(1)
        self.copy_button = QPushButton('复制', self)
        self.copy_button.setObjectName('copyRunObserver')
        self.copy_button.setAutoDefault(False)
        self.clear_button = QPushButton('清空观察内容', self)
        self.clear_button.setObjectName('clearRunObserver')
        self.clear_button.setAutoDefault(False)
        actions.addWidget(self.copy_button)
        actions.addWidget(self.clear_button)
        layout.addLayout(actions)
        self.follow_checkbox.toggled.connect(self._follow_changed)
        self.copy_button.clicked.connect(self._copy_text)
        self.clear_button.clicked.connect(self.clear_view)

    @property
    def run_id(self):
        return self._run_id

    def reset_for_run(self, run_id):
        run_id = identifier(run_id)
        if run_id == self._run_id:
            return
        self._run_id = run_id
        self._last_sequence = 0
        self._stage = ''
        self._has_content = False
        self.text_view.clear()
        self.status_label.setText('等待运行输出正文或代码。')

    def append_event(self, event):
        if not isinstance(event, RunProgressEvent) or event.run_id != self._run_id:
            return False
        if event.sequence <= self._last_sequence:
            return False
        self._last_sequence = event.sequence
        if event.stage is not None:
            self._stage = _STAGE_TEXT.get(event.stage.value, event.stage.value)
        kind = event.kind.value
        if kind == 'stage_started':
            attempt = f' · 第 {event.attempt + 1} 次' if event.attempt is not None else ''
            self._mark(f'{self._stage}{attempt}')
            self._has_content = False
            self._set_status('阶段开始，等待正文输出。')
        elif kind == 'agent_message':
            self._append_text(event.detail)
            self._has_content = self._has_content or bool(event.detail)
            self._set_status('正在生成正文和代码（尚未校验）。')
        elif kind in {'agent_request_started', 'agent_waiting', 'agent_retrying', 'agent_completed'}:
            self._handle_agent_state(kind)
        elif kind == 'run_started':
            self._mark('运行已开始或恢复')
            self._set_status('等待正文输出。')
        elif kind in _TERMINAL_TEXT:
            label = _TERMINAL_TEXT[kind]
            self._mark(label)
            self._set_status(label)
        elif kind == 'events_omitted':
            self._mark('部分事件未能显示；当前观察内容可能不连续。')
        return True

    def clear_view(self):
        """Clear only this display; preserve the run and accepted event sequence."""
        self.text_view.clear()
        self._has_content = False
        self._set_status('观察内容已清空，继续接收新输出。')

    def _handle_agent_state(self, kind):
        if kind == 'agent_request_started':
            self._has_content = False
            self._mark('请求开始 · 尚未校验')
            self._set_status('等待正文输出。')
        elif kind == 'agent_waiting':
            self._set_status('正在生成正文和代码（尚未校验）。' if self._has_content
                             else '等待正文输出；Agent 正在处理请求。')
        elif kind == 'agent_retrying':
            self._has_content = False
            self._mark('自动重试：上一请求草稿已丢弃；下一请求将重新生成。')
            self._set_status('自动重试，等待新正文输出。')
        elif kind == 'agent_completed':
            self._mark('请求输出结束，等待校验')
            self._set_status('正文输出结束，等待校验。')

    def _set_status(self, text):
        self.status_label.setText(f'当前阶段：{self._stage} · {text}' if self._stage else text)

    def _mark(self, text):
        separator = '\n\n' if not self.text_view.document().isEmpty() else ''
        self._append_text(f'{separator}[{text}]\n')

    def _append_text(self, text):
        if not text:
            return
        bar = self.text_view.verticalScrollBar()
        previous_scroll = bar.value()
        cursor = QTextCursor(self.text_view.document())
        cursor.movePosition(QTextCursor.MoveOperation.End)
        cursor.insertText(text)
        if self.text_view.document().characterCount() - 1 > MAX_OBSERVER_CHARACTERS:
            # Leave headroom so each streamed fragment does not rebuild the document.
            retained = self.text_view.toPlainText()
            tail_size = MAX_OBSERVER_CHARACTERS * 3 // 4 - len(_TRUNCATION_NOTICE)
            self.text_view.setPlainText(_TRUNCATION_NOTICE + retained[-tail_size:])
        if self.follow_checkbox.isChecked():
            bar.setValue(bar.maximum())
        else:
            bar.setValue(min(previous_scroll, bar.maximum()))

    def _follow_changed(self, checked):
        if checked:
            bar = self.text_view.verticalScrollBar()
            bar.setValue(bar.maximum())

    def _copy_text(self):
        QApplication.clipboard().setText(self.text_view.toPlainText())
