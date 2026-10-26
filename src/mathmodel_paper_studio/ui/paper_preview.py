"""Bounded, read-only view of paper sections accepted during one live run."""
from html import escape

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QDialog, QHBoxLayout, QLabel, QPushButton, QTextBrowser, QVBoxLayout

from mathmodel_paper_studio.domain.models import Stage
from mathmodel_paper_studio.workflow.progress import ProgressKind, RunProgressEvent

_MAX_SECTIONS = 64
_SECTION_TITLES = {
    'abstract': '摘要', 'problem_restatement': '问题重述', 'problem_analysis': '问题分析',
    'assumptions': '模型假设', 'notation': '符号说明', 'model': '模型建立',
    'solution': '模型求解与分析', 'results': '结果与讨论', 'verification': '模型检验',
    'sensitivity_analysis': '灵敏度分析', 'robustness_check': '稳健性分析',
    'computational_complexity': '算法复杂度', 'conclusion': '结论与模型评价',
    'references': '参考文献', 'appendix': '附录',
}


class PaperPreviewDialog(QDialog):
    """Show validated section snapshots without writing a partial DOCX."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._run_id = ''
        self._last_sequence = 0
        self._attempt = None
        self._sections = {}
        self._document_title = ''
        self._keywords = ''
        self.setObjectName('livePaperPreviewDialog')
        self.setWindowTitle('实时论文 Word 预览')
        self.setModal(False)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, False)
        self.setWindowFlag(Qt.WindowType.WindowContextHelpButtonHint, False)
        self.resize(1050, 760)
        self.setMinimumSize(600, 420)

        layout = QVBoxLayout(self)
        self.status_label = QLabel('等待论文撰写阶段开始。', self)
        self.status_label.setObjectName('livePaperPreviewStatus')
        self.status_label.setTextFormat(Qt.TextFormat.PlainText)
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)
        self.notice_label = QLabel(
            '实时预览只显示通过单批结构校验的正文；整篇论文和 Word 页面质量完成验收前，不能视为最终论文。',
            self,
        )
        self.notice_label.setObjectName('livePaperPreviewNotice')
        self.notice_label.setTextFormat(Qt.TextFormat.PlainText)
        self.notice_label.setWordWrap(True)
        layout.addWidget(self.notice_label)
        self.text_view = QTextBrowser(self)
        self.text_view.setObjectName('livePaperPreviewText')
        self.text_view.setAccessibleName('实时论文正文预览')
        self.text_view.setOpenLinks(False)
        self.text_view.setOpenExternalLinks(False)
        self.text_view.setPlaceholderText('等待第一批通过结构校验的论文内容……')
        layout.addWidget(self.text_view, 1)

        actions = QHBoxLayout()
        actions.addStretch(1)
        self.copy_button = QPushButton('复制当前预览', self)
        self.copy_button.setObjectName('copyLivePaperPreview')
        self.copy_button.setAutoDefault(False)
        actions.addWidget(self.copy_button)
        layout.addLayout(actions)
        self.copy_button.clicked.connect(self._copy_preview)

    def reset_for_run(self, run_id):
        run_id = str(run_id)
        if run_id == self._run_id:
            return
        self._run_id = run_id
        self._last_sequence = 0
        self._attempt = None
        self._sections = {}
        self._document_title = ''
        self._keywords = ''
        self.status_label.setText('等待论文撰写阶段开始。')
        self.text_view.clear()

    def open_preview(self):
        self.show()
        self.raise_()
        self.activateWindow()

    def append_event(self, event):
        if (not isinstance(event, RunProgressEvent) or event.run_id != self._run_id
                or event.sequence <= self._last_sequence):
            return False
        self._last_sequence = event.sequence

        if event.kind is ProgressKind.STAGE_STARTED and event.stage is Stage.PAPER_DRAFTING:
            if self._attempt is not None and event.attempt != self._attempt:
                self._sections = {}
                self._document_title = ''
                self._keywords = ''
                self._render()
                self.status_label.setText('新一轮论文撰写开始，上一轮未验收正文已从预览移除。')
            self._attempt = event.attempt
            if not self._sections:
                self.status_label.setText('论文撰写已开始，等待第一批内容通过结构校验。')
            self.open_preview()
        elif event.kind is ProgressKind.STAGE_PROGRESS and event.stage is Stage.PAPER_DRAFTING:
            self.status_label.setText(f'论文撰写进度：{event.title}')
        elif event.kind is ProgressKind.PAPER_PREVIEW_UPDATED:
            if event.item_id == 'paper_meta':
                self._document_title = event.title
                self._keywords = event.detail
            elif event.item_id == 'paper_summary':
                self.status_label.setText(f'{event.title}；整篇论文尚未通过最终验收。')
            elif event.item_id:
                if event.item_id not in self._sections and len(self._sections) >= _MAX_SECTIONS:
                    self.status_label.setText('章节数超过预览上限；其余内容仍按工作流继续生成。')
                else:
                    heading = event.title.strip() or _SECTION_TITLES.get(event.item_id, event.item_id)
                    self._sections[event.item_id] = (heading, event.detail)
                    self._render()
                    self.status_label.setText(
                        f'已实时预览 {len(self._sections)} 个已校验章节；整篇论文仍在生成和验收中。'
                    )
                    self.open_preview()
        elif event.kind is ProgressKind.STAGE_COMPLETED and event.stage is Stage.PAPER_DRAFTING:
            self.status_label.setText('各章节已合并并写入 Word；正在进行 Word 质量检查。')
        elif event.kind is ProgressKind.ARTIFACT_CREATED and event.artifact is not None:
            if event.artifact.kind == 'paper':
                self.status_label.setText(f'Word 草稿已写入：{event.artifact.relative_path}')
        elif event.kind is ProgressKind.STAGE_STARTED and event.stage is Stage.DOCX_QA:
            self.status_label.setText('Word 质量检查正在进行。')
        elif event.kind is ProgressKind.STAGE_COMPLETED and event.stage is Stage.DOCX_QA:
            self.status_label.setText('Word 质量检查阶段已结束；请以“产物审核”显示的结果为准。')
        elif event.kind in (ProgressKind.RUN_PAUSED, ProgressKind.STAGE_PAUSED):
            self.status_label.setText('运行已暂停；窗口保留已通过单批校验的章节预览。')
        elif event.kind in (ProgressKind.RUN_FAILED, ProgressKind.STAGE_FAILED):
            self.status_label.setText('本次运行失败；窗口内容仅为已通过单批校验的预览。')
        elif event.kind in (ProgressKind.RUN_CANCELLED, ProgressKind.STAGE_CANCELLED):
            self.status_label.setText('本次运行已取消；未验收内容不会标记为最终论文。')
        elif event.kind is ProgressKind.RUN_COMPLETED:
            self.status_label.setText('工作流已结束；最终验收状态请查看“产物审核”。')
        return True

    def _render(self):
        parts = ['<html><body style="font-family: Microsoft YaHei, sans-serif; line-height: 1.7;">']
        if self._document_title:
            parts.append(f'<h1 style="text-align:center">{escape(self._document_title)}</h1>')
        if self._keywords:
            parts.append(f'<p><b>关键词：</b>{escape(self._keywords)}</p>')
        for _key, (heading, body) in self._sections.items():
            parts.append(f'<h2>{escape(heading)}</h2>')
            for block in body.split('\n\n'):
                block = block.strip()
                if not block:
                    continue
                if block.startswith('【小节】'):
                    parts.append(f'<h3>{escape(block[len("【小节】"):])}</h3>')
                else:
                    parts.append(f'<p>{escape(block)}</p>')
        parts.append('</body></html>')
        self.text_view.setHtml(''.join(parts))

    def _copy_preview(self):
        QApplication.clipboard().setText(self.text_view.toPlainText())
