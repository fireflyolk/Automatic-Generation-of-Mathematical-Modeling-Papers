"""Asynchronous project setup with read-only imports from local drives."""
from pathlib import Path

from PySide6.QtCore import QThreadPool, Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QDialog, QFileDialog, QFormLayout, QFrame, QHBoxLayout, QListWidget,
    QScrollArea, QVBoxLayout, QWidget,
)
from qfluentwidgets import (
    BodyLabel, CardWidget, CaptionLabel, FluentIcon, FluentSystemColor, LineEdit,
    PrimaryPushButton, ProgressBar, PushButton, Theme, qconfig, setCustomStyleSheet,
    TextEdit, TitleLabel,
)

from mathmodel_paper_studio.bootstrap.paths import validate_import_source_path
from mathmodel_paper_studio.inputs.docx_reader import read_docx
from mathmodel_paper_studio.inputs.pdf_reader import read_pdf
from mathmodel_paper_studio.inputs.profiling import ImportLimits, read_source_bounded
from mathmodel_paper_studio.projects.project_service import ProjectService
from mathmodel_paper_studio.ui.workers import FunctionWorker


_STATUS_SYSTEM_COLORS = {
    'success': FluentSystemColor.SUCCESS_FOREGROUND,
    'error': FluentSystemColor.CRITICAL_FOREGROUND,
}


def _status_color_pair(state):
    system_color = _STATUS_SYSTEM_COLORS.get(state)
    if system_color is not None:
        return tuple(system_color.color(theme).name() for theme in (Theme.LIGHT, Theme.DARK))
    if state != 'loading':
        return None
    accent = qconfig.get(qconfig.themeColor)
    hue, saturation, value, _ = accent.getHsvF()
    dark = QColor.fromHsvF(hue, min(saturation * 0.84, 1), 1)
    light = QColor.fromHsvF(hue if hue >= 0 else 0, saturation, min(value * 0.75, 1))
    return light.name(), dark.name()


def _set_status_state(label, state):
    label.setProperty('state', state)
    colors = _status_color_pair(state)
    if colors is None:
        setCustomStyleSheet(label, '', '')
        return
    light, dark = colors
    setCustomStyleSheet(label, f'QLabel {{ color: {light}; }}',
                        f'QLabel {{ color: {dark}; }}')


class ProjectWizard(QDialog):
    project_created = Signal(object)
    operation_failed = Signal(str)

    def __init__(self, service: ProjectService, parent=None, *, thread_pool=None):
        super().__init__(parent)
        if not isinstance(service, ProjectService):
            raise TypeError('新建项目需要 ProjectService。')
        self.service = service
        self.thread_pool = thread_pool or QThreadPool.globalInstance()
        self._source_files = ()
        self._template_path = None
        self._problem_document = None
        self._worker = None
        self._worker_signals = None
        self._retry_available = False
        self._input_error = None
        self.setWindowTitle('新建数模项目')
        self.setMinimumSize(480, 360)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(20, 16, 20, 16)
        outer.setSpacing(12)
        outer.addWidget(TitleLabel('新建项目', self))
        outer.addWidget(CaptionLabel('导入的题目、数据和模板会复制到 F: 项目目录；来源文件保持不变。', self))

        self.content_scroll_area = QScrollArea(self)
        self.content_scroll_area.setObjectName('projectWizardScrollArea')
        self.content_scroll_area.setWidgetResizable(True)
        self.content_scroll_area.setFrameShape(QFrame.Shape.NoFrame)
        self.content_scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        outer.addWidget(self.content_scroll_area, 1)

        content = QWidget(self.content_scroll_area)
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(2, 2, 8, 10)
        content_layout.setSpacing(12)

        project_card = CardWidget(content)
        project_layout = QVBoxLayout(project_card)
        project_layout.addWidget(TitleLabel('项目和题目', project_card))
        fields = QFormLayout()
        self.title_input = LineEdit(project_card)
        self.title_input.setAccessibleName('项目名称')
        self.title_input.setPlaceholderText('例如：2026 数学建模竞赛')
        self.problem_input = TextEdit(project_card)
        self.problem_input.setAccessibleName('题目内容')
        self.problem_input.setPlaceholderText('粘贴题目全文；导入时会保留为项目输入副本。')
        self.problem_input.setMinimumHeight(110)
        self.problem_input.setTabChangesFocus(True)
        problem_editor = QWidget(project_card)
        problem_editor_layout = QVBoxLayout(problem_editor)
        problem_editor_layout.setContentsMargins(0, 0, 0, 0)
        problem_editor_layout.setSpacing(6)
        problem_editor_layout.addWidget(self.problem_input)
        problem_docx_actions = QHBoxLayout()
        self.import_problem_docx_button = PushButton('从 DOCX/PDF 导入题目', problem_editor)
        self.import_problem_docx_button.setIcon(FluentIcon.DOCUMENT)
        self.import_problem_docx_button.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.import_problem_docx_button.setAccessibleName('从 DOCX 或文字版 PDF 导入题目内容')
        self.import_problem_docx_button.setToolTip(
            '支持本地磁盘上的 DOCX 和文字版 PDF；扫描件与图片不识别。')
        problem_docx_actions.addWidget(self.import_problem_docx_button, 0, Qt.AlignmentFlag.AlignLeft)
        problem_docx_actions.addStretch(1)
        problem_editor_layout.addLayout(problem_docx_actions)
        self.problem_docx_status = CaptionLabel(
            '可粘贴题目，或导入 DOCX/文字版 PDF；导入后请核对公式、表格和题目全文。', problem_editor)
        self.problem_docx_status.setObjectName('problemDocxImportStatus')
        self.problem_docx_status.setAccessibleName('题目文档导入状态')
        self.problem_docx_status.setTextFormat(Qt.TextFormat.PlainText)
        self.problem_docx_status.setWordWrap(True)
        problem_editor_layout.addWidget(self.problem_docx_status)
        fields.addRow(BodyLabel('项目名称', project_card), self.title_input)
        fields.addRow(BodyLabel('题目内容', project_card), problem_editor)
        project_layout.addLayout(fields)
        content_layout.addWidget(project_card)

        files_card = CardWidget(content)
        files_layout = QVBoxLayout(files_card)
        files_layout.addWidget(TitleLabel('数据与 Word 模板', files_card))
        source_help = CaptionLabel(
            '可从任意本地磁盘（如 C:、D:、F:）导入 CSV/XLSX/DOCX，可多选最多 32 个；DOCX 模板单独选择。未选模板时默认使用内置 B078 论文格式。源文件会复制到 F: 应用数据目录，原件保持不变。',
            files_card,
        )
        source_help.setWordWrap(True)
        files_layout.addWidget(source_help)
        file_actions = QHBoxLayout()
        self.add_source_button = PushButton('选择 CSV/XLSX/DOCX 数据', files_card)
        self.add_source_button.setIcon(FluentIcon.FOLDER)
        self.add_source_button.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.add_source_button.setAccessibleName('多选本地磁盘上的 CSV、XLSX 或 DOCX 数据文件')
        self.add_source_button.setToolTip('可在文件选择窗口中用 Ctrl 或 Shift 多选；支持 C:、D:、F: 等本地磁盘。')
        self.add_template_button = PushButton('选择 Word 模板', files_card)
        self.add_template_button.setIcon(FluentIcon.DOCUMENT)
        self.add_template_button.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.add_template_button.setAccessibleName('选择 Word 模板')
        file_actions.addWidget(self.add_source_button)
        file_actions.addWidget(self.add_template_button)
        file_actions.addStretch(1)
        files_layout.addLayout(file_actions)
        self.file_list = QListWidget(files_card)
        self.file_list.setAccessibleName('待导入文件')
        self.file_list.setMinimumHeight(100)
        files_layout.addWidget(self.file_list)
        self.remove_source_button = PushButton('移除选中数据文件', files_card)
        self.remove_source_button.setIcon(FluentIcon.DELETE)
        self.remove_source_button.setAccessibleName('移除选中数据文件')
        self.remove_source_button.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.remove_source_button.setEnabled(False)
        self.file_list.currentItemChanged.connect(lambda *_: self._update_remove_enabled())
        files_layout.addWidget(self.remove_source_button, 0, Qt.AlignmentFlag.AlignLeft)
        self.confirmation_label = CaptionLabel(
            '原文件只读；创建时会把所选材料复制到新项目的输入目录。',
            files_card,
        )
        self.confirmation_label.setWordWrap(True)
        files_layout.addWidget(self.confirmation_label)
        content_layout.addWidget(files_card)

        self.content_scroll_area.setWidget(content)

        self.progress_label = BodyLabel('正在后台复制材料并校验项目输入…', self)
        self.progress_label.setObjectName('wizardProgressLabel')
        self.progress_label.setAccessibleName('导入进度')
        self.progress_label.setWordWrap(True)
        self.progress_label.hide()
        outer.addWidget(self.progress_label)
        self.import_progress = ProgressBar(self)
        self.import_progress.setObjectName('wizardImportProgress')
        self.import_progress.setAccessibleName('材料导入进行中')
        self.import_progress.setRange(0, 0)
        self.import_progress.hide()
        outer.addWidget(self.import_progress)

        self.error_label = BodyLabel(
            '请填写项目名称、题目内容，并至少选择一个数据文件。',
            self,
        )
        self.error_label.setWordWrap(True)
        self.error_label.setObjectName('wizardError')
        self.error_label.setAccessibleName('项目导入状态')
        self.error_label.setProperty('error', True)
        _set_status_state(self.error_label, 'error')
        outer.addWidget(self.error_label)

        self.create_button = PrimaryPushButton('创建项目并导入', self)
        self.create_button.setIcon(FluentIcon.FOLDER_ADD)
        self.create_button.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.create_button.setAccessibleName('创建项目并导入')
        self.cancel_button = PushButton('取消', self)
        self.cancel_button.setIcon(FluentIcon.CANCEL)
        self.cancel_button.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.cancel_button.setAccessibleName('取消新建项目')
        buttons = QHBoxLayout()
        buttons.addStretch(1)
        buttons.addWidget(self.cancel_button)
        buttons.addWidget(self.create_button)
        outer.addLayout(buttons)

        self.add_source_button.clicked.connect(self._choose_sources)
        self.import_problem_docx_button.clicked.connect(self._choose_problem_docx)
        self.add_template_button.clicked.connect(self._choose_template)
        self.remove_source_button.clicked.connect(self._remove_selected)
        self.title_input.textChanged.connect(self._inputs_changed)
        self.problem_input.textChanged.connect(self._inputs_changed)
        self.create_button.clicked.connect(self._create)
        self.cancel_button.clicked.connect(self.reject)
        self.setTabOrder(self.title_input, self.problem_input)
        self.setTabOrder(self.problem_input, self.import_problem_docx_button)
        self.setTabOrder(self.import_problem_docx_button, self.add_source_button)
        self.setTabOrder(self.add_source_button, self.add_template_button)
        self.setTabOrder(self.add_template_button, self.file_list)
        self.setTabOrder(self.file_list, self.remove_source_button)
        self.setTabOrder(self.remove_source_button, self.cancel_button)
        self.setTabOrder(self.cancel_button, self.create_button)
        self._update_validity()

    def reject(self):
        if self._worker is not None:
            self.error_label.setText('正在后台导入材料，请等待当前任务完成。')
            _set_status_state(self.error_label, 'loading')
            return
        super().reject()

    def closeEvent(self, event):
        if self._worker is not None:
            self.error_label.setText('正在后台导入材料，请等待当前任务完成。')
            _set_status_state(self.error_label, 'loading')
            event.ignore()
            return
        super().closeEvent(event)

    @property
    def source_files(self) -> tuple[Path, ...]:
        return self._source_files

    @property
    def template_path(self) -> Path | None:
        return self._template_path

    def set_source_files(self, paths) -> None:
        try:
            candidates = tuple(validate_import_source_path(Path(item)) for item in paths)
            if len(candidates) > 32 or len({str(item).casefold() for item in candidates}) != len(candidates):
                raise ValueError('数据文件不能重复，单次最多选择 32 个。')
            if any(not item.is_file() or item.suffix.casefold() not in ('.csv', '.xlsx', '.docx')
                   for item in candidates):
                raise ValueError('数据文件必须是本地磁盘上的普通 CSV、XLSX 或 DOCX 文件。')
        except (OSError, TypeError, ValueError) as exc:
            self._retry_available = False
            self._set_error(f'文件范围无效：{exc}')
            self._source_files = ()
            self._refresh_file_list()
            return
        self._source_files = candidates
        self._retry_available = False
        self._input_error = None
        self._refresh_file_list()
        self._update_validity()

    def set_problem_docx(self, path) -> None:
        self.set_problem_document(path)

    def set_problem_document(self, path) -> None:
        try:
            candidate = validate_import_source_path(Path(path))
            extension = candidate.suffix.casefold()
            if not candidate.is_file() or extension not in ('.docx', '.pdf'):
                raise ValueError('题目文件必须是本地磁盘上的 DOCX 或文字版 PDF 文件。')
            content = read_source_bounded(candidate, ImportLimits())
            extracted = read_pdf(content) if extension == '.pdf' else read_docx(content)
        except (OSError, TypeError, ValueError) as exc:
            self.problem_docx_status.setText(f'题目文档导入失败：{str(exc)[:500]}')
            return
        self._problem_document = (candidate.name, content)
        self.problem_input.setPlainText(extracted.text)
        if extension == '.pdf':
            message = f'{extracted.page_count} 页文字，{extracted.character_count} 字符'
        else:
            message = f'正文和表格文字（{extracted.paragraph_count} 段，{extracted.table_count} 个表格）'
        self.problem_docx_status.setText(f'已从 {candidate.name} 导入{message}；'
                                        '原件会复制到项目，请核对公式、表格和题目全文。扫描图片不识别。')

    def _choose_problem_docx(self):
        path, _ = QFileDialog.getOpenFileName(
            self, '选择题目文档', str(self.service.paths.root),
            '题目文档 (*.docx *.pdf);;Word 文档 (*.docx);;PDF 文档 (*.pdf)')
        if path:
            self.set_problem_docx(path)

    def set_template_path(self, path) -> None:
        if path is None:
            self._template_path = None
            self._input_error = None
            self._retry_available = False
            self._refresh_file_list()
            self._update_validity()
            return
        try:
            candidate = validate_import_source_path(Path(path))
            if not candidate.is_file() or candidate.suffix.casefold() != '.docx':
                raise ValueError('Word 模板必须是本地磁盘上的 DOCX 文件。')
            if any(candidate.name.casefold() == source.name.casefold() for source in self._source_files):
                raise ValueError('模板文件不能与数据文件重名。')
        except (OSError, TypeError, ValueError) as exc:
            self._template_path = None
            self._input_error = None
            self._retry_available = False
            self._set_error(f'模板范围无效：{exc}')
            self._refresh_file_list()
            return
        self._template_path = candidate
        self._retry_available = False
        self._input_error = None
        self._refresh_file_list()
        self._update_validity()

    def _choose_sources(self):
        paths, _ = QFileDialog.getOpenFileNames(
            self, '选择 CSV/XLSX/DOCX 数据文件', str(self.service.paths.root),
            '数据文件 (*.csv *.xlsx *.docx)')
        if paths:
            self.set_source_files(paths)

    def _choose_template(self):
        path, _ = QFileDialog.getOpenFileName(
            self, '选择 Word 模板', str(self.service.paths.root), 'Word 文档 (*.docx)')
        if path:
            self.set_template_path(path)

    def _remove_selected(self):
        selected = self.file_list.currentItem()
        if selected is None:
            return
        value = selected.data(256)
        if value == 'template':
            self.set_template_path(None)
        else:
            self.set_source_files(tuple(item for item in self._source_files if str(item) != value))

    def _update_remove_enabled(self):
        self.remove_source_button.setEnabled(
            self._worker is None and self.file_list.currentItem() is not None)

    def _refresh_file_list(self):
        self.file_list.clear()
        for source in self._source_files:
            label = 'DOCX 数据' if source.suffix.casefold() == '.docx' else '数据'
            self.file_list.addItem(f'{label}：{source}')
            self.file_list.item(self.file_list.count() - 1).setData(256, str(source))
        if self._template_path is not None:
            self.file_list.addItem(f'Word 模板：{self._template_path}')
            self.file_list.item(self.file_list.count() - 1).setData(256, 'template')
        self._update_remove_enabled()

    def _set_error(self, message: str):
        self._input_error = message[:1000]
        self.error_label.setText(self._input_error)
        self.error_label.setProperty('error', True)
        _set_status_state(self.error_label, 'error')
        self.create_button.setEnabled(False)

    def _inputs_changed(self):
        self._retry_available = False
        self._update_validity()

    def _missing_fields(self):
        missing = []
        if not self.title_input.text().strip():
            missing.append('项目名称')
        if not self.problem_input.toPlainText().strip():
            missing.append('题目内容')
        if not self._source_files:
            missing.append('数据文件')
        return missing

    def _inputs_valid(self):
        return not self._missing_fields() and self._worker is None

    def _update_validity(self):
        valid = self._inputs_valid()
        self.create_button.setEnabled(valid)
        if self._worker is not None:
            return
        if self._retry_available:
            self.create_button.setText('重试导入')
            self.create_button.setAccessibleName('重试导入')
            return
        self.create_button.setText('创建项目并导入')
        self.create_button.setAccessibleName('创建项目并导入')
        if valid:
            self._input_error = None
            self.error_label.clear()
            self.error_label.setProperty('error', False)
            _set_status_state(self.error_label, 'neutral')
            return
        if self._input_error is not None:
            self.error_label.setText(self._input_error)
            self.error_label.setProperty('error', True)
            _set_status_state(self.error_label, 'error')
            return
        missing = '、'.join(self._missing_fields())
        self.error_label.setText(f'请补全必填项：{missing}。')
        self.error_label.setProperty('error', True)
        _set_status_state(self.error_label, 'error')

    def _create(self):
        self._update_validity()
        if not self.create_button.isEnabled():
            return
        title, problem = self.title_input.text().strip(), self.problem_input.toPlainText().strip()
        sources, template = self._source_files, self._template_path
        problem_document = self._problem_document
        self._retry_available = False
        self._input_error = None
        self.create_button.setText('正在导入…')
        self.create_button.setAccessibleName('正在导入，请稍候')
        self.create_button.setEnabled(False)
        self.add_source_button.setEnabled(False)
        self.import_problem_docx_button.setEnabled(False)
        self.add_template_button.setEnabled(False)
        self.remove_source_button.setEnabled(False)
        self.cancel_button.setEnabled(False)
        self.error_label.setText('正在导入项目，请稍候。')
        self.error_label.setProperty('error', False)
        _set_status_state(self.error_label, 'loading')
        self.progress_label.setText('正在 F: 工作区后台复制并校验材料…')
        self.progress_label.show()
        self.import_progress.show()
        self._worker = FunctionWorker(
            lambda cancel, emit: self.service.create(title, problem, sources, template=template,
                                                    problem_document=problem_document))
        self._worker_signals = self._worker.signals
        self._worker_signals.event_received.connect(self._import_event)
        self._worker_signals.succeeded.connect(self._created)
        self._worker_signals.failed.connect(self._failed)
        self._worker_signals.finished.connect(self._finished)
        self.thread_pool.start(self._worker)

    def _import_event(self, message: str):
        self.progress_label.setText(str(message)[:500])

    def _created(self, project):
        self.error_label.setText('项目创建成功，所选材料已导入。')
        self.error_label.setProperty('error', False)
        _set_status_state(self.error_label, 'success')
        self.project_created.emit(project)
        self.accept()

    def _failed(self, message: str):
        self._retry_available = True
        self._input_error = None
        self.error_label.setText(f'创建失败：{message}')
        self.error_label.setProperty('error', True)
        _set_status_state(self.error_label, 'error')
        self.create_button.setText('重试导入')
        self.create_button.setAccessibleName('重试导入')
        self.operation_failed.emit(message)

    def _finished(self):
        self._worker = None
        self.import_progress.hide()
        self.progress_label.hide()
        signals, self._worker_signals = self._worker_signals, None
        if signals is not None:
            signals.deleteLater()
        self.add_source_button.setEnabled(True)
        self.import_problem_docx_button.setEnabled(True)
        self.add_template_button.setEnabled(True)
        self.cancel_button.setEnabled(True)
        self._update_remove_enabled()
        self.create_button.setEnabled(self._inputs_valid())
        self.create_button.setText('重试导入' if self._retry_available else '创建项目并导入')
        self.create_button.setAccessibleName(self.create_button.text())
        if not self._retry_available and self.error_label.property('error') is not True:
            self._update_validity()
