"""Worker-backed DOCX inspection and explicit template-anchor mapping."""
from pathlib import Path

from PySide6.QtCore import QThreadPool, Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QComboBox, QFileDialog, QFormLayout, QFrame, QHBoxLayout, QScrollArea,
    QVBoxLayout, QWidget,
)
from qfluentwidgets import (
    BodyLabel, CaptionLabel, CardWidget, FluentIcon, FluentSystemColor,
    PrimaryPushButton, ProgressBar, PushButton, Theme, TitleLabel, qconfig,
    setCustomStyleSheet,
)

from mathmodel_paper_studio.bootstrap.paths import validate_managed_path
from mathmodel_paper_studio.paper.template_inspector import (
    TemplateInspection, UnsupportedTemplateError, inspect_template,
)
from mathmodel_paper_studio.paper.template_mapping import TemplateMapping
from mathmodel_paper_studio.ui.workers import FunctionWorker

_SECTIONS = (
    ('abstract', '摘要'), ('problem_restatement', '问题重述'), ('assumptions', '模型假设'),
    ('notation', '符号说明'), ('model', '模型建立'), ('solution', '模型求解'),
    ('verification', '模型检验'), ('conclusion', '结论'), ('references', '参考文献'),
)
_STATUS_SYSTEM_COLORS = {
    'success': FluentSystemColor.SUCCESS_FOREGROUND,
    'warning': FluentSystemColor.CAUTION_FOREGROUND,
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


def _set_status(label, state, text):
    label.setText(text)
    label.setProperty('state', state)
    colors = _status_color_pair(state)
    if colors is None:
        setCustomStyleSheet(label, '', '')
        return
    light, dark = colors
    setCustomStyleSheet(label, f'QLabel {{ color: {light}; }}',
                        f'QLabel {{ color: {dark}; }}')


class TemplateMappingView(QWidget):
    project_creation_requested = Signal()
    mapping_saved = Signal(object, object)
    inspection_loaded = Signal(object)
    operation_failed = Signal(str)

    def __init__(self, parent=None, *, thread_pool=None):
        super().__init__(parent)
        self.thread_pool = thread_pool or QThreadPool.globalInstance()
        self.project_root = None
        self.template_path = None
        self.inspection = None
        self._worker = None
        self._worker_signals = None
        self._active_operation = None
        self._generation = 0
        self._pending_template = None
        self._combos = {}
        outer = QVBoxLayout(self)
        outer.setContentsMargins(20, 16, 20, 16)
        outer.setSpacing(12)
        outer.addWidget(TitleLabel('Word 模板映射', self))
        outer.addWidget(CaptionLabel(
            '选择当前项目中已导入的 DOCX 模板，再逐项确认论文内容对应的章节。', self,
        ))

        self.page_scroll_area = QScrollArea(self)
        self.page_scroll_area.setObjectName('templateMappingPageScrollArea')
        self.page_scroll_area.setWidgetResizable(True)
        self.page_scroll_area.setFrameShape(QFrame.Shape.NoFrame)
        self.page_scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        outer.addWidget(self.page_scroll_area, 1)

        content = QWidget(self.page_scroll_area)
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(2, 2, 8, 8)
        content_layout.setSpacing(12)

        template_card = CardWidget(content)
        template_layout = QVBoxLayout(template_card)
        template_layout.addWidget(TitleLabel('模板状态与选择', template_card))
        template_layout.addWidget(CaptionLabel(
            '只允许使用当前 F: 项目目录内已导入的 DOCX；来源模板以只读方式检查。',
            template_card,
        ))
        self.template_label = BodyLabel('尚未选择模板', template_card)
        self.template_label.setObjectName('templateMappingSelectedPath')
        self.template_label.setAccessibleName('当前 Word 模板路径')
        self.template_label.setWordWrap(True)
        template_layout.addWidget(self.template_label)
        template_actions = QHBoxLayout()
        self.choose_button = PushButton('选择模板', template_card)
        self.choose_button.setIcon(FluentIcon.FOLDER)
        self.choose_button.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.choose_button.setObjectName('chooseTemplateButton')
        self.choose_button.setAccessibleName('选择项目中的 Word 模板')
        template_actions.addWidget(self.choose_button, 0, Qt.AlignmentFlag.AlignLeft)
        template_actions.addStretch(1)
        template_layout.addLayout(template_actions)
        self.template_status_label = BodyLabel('尚未选择模板。', template_card)
        self.template_status_label.setObjectName('templateMappingStatus')
        self.template_status_label.setAccessibleName('模板检查状态')
        self.template_status_label.setWordWrap(True)
        template_layout.addWidget(self.template_status_label)
        self.operation_progress = ProgressBar(template_card)
        self.operation_progress.setObjectName('templateOperationProgress')
        self.operation_progress.setAccessibleName('Word 模板操作进行中')
        self.operation_progress.setRange(0, 0)
        self.operation_progress.hide()
        template_layout.addWidget(self.operation_progress)
        _set_status(self.template_status_label, 'neutral', self.template_status_label.text())
        # Keep the existing status attribute available to callers and tests.
        self.error_label = self.template_status_label
        content_layout.addWidget(template_card)

        mapping_card = CardWidget(content)
        mapping_layout = QVBoxLayout(mapping_card)
        mapping_layout.addWidget(TitleLabel('章节映射', mapping_card))
        mapping_layout.addWidget(CaptionLabel(
            '每个模板锚点只能分配给一个论文部分；不需要的项目可保留为“不映射”。',
            mapping_card,
        ))
        self.mapping_empty_label = BodyLabel(
            '检查完成后，模板标题与表格会显示在这里。', mapping_card,
        )
        self.mapping_empty_label.setWordWrap(True)
        mapping_layout.addWidget(self.mapping_empty_label)

        self.mapping_scroll_area = QScrollArea(mapping_card)
        self.mapping_scroll_area.setObjectName('templateMappingChoicesScrollArea')
        self.mapping_scroll_area.setWidgetResizable(True)
        self.mapping_scroll_area.setFrameShape(QFrame.Shape.NoFrame)
        self.mapping_scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.mapping_scroll_area.setMinimumHeight(180)
        self.mapping_scroll_area.setMaximumHeight(360)
        choices = QWidget(self.mapping_scroll_area)
        choices_layout = QVBoxLayout(choices)
        choices_layout.setContentsMargins(2, 2, 8, 8)
        self.form = QFormLayout()
        self.form.setContentsMargins(0, 0, 0, 0)
        self.form.setVerticalSpacing(10)
        choices_layout.addLayout(self.form)
        choices_layout.addStretch(1)
        self.mapping_scroll_area.setWidget(choices)
        self.mapping_scroll_area.hide()
        mapping_layout.addWidget(self.mapping_scroll_area)
        content_layout.addWidget(mapping_card)

        unsupported_card = CardWidget(content)
        unsupported_layout = QVBoxLayout(unsupported_card)
        unsupported_layout.addWidget(TitleLabel('暂不支持的模板内容', unsupported_card))
        self.unsupported_label = BodyLabel(
            '文字框、嵌入对象、altChunk、ActiveX，以及其他无法安全保留的 Word 结构暂不支持。'
            '请先在 Word 中移除这些内容，再重新导入；检查过程不会修改原模板。',
            unsupported_card,
        )
        self.unsupported_label.setObjectName('templateMappingUnsupportedExplanation')
        self.unsupported_label.setWordWrap(True)
        unsupported_layout.addWidget(self.unsupported_label)
        content_layout.addWidget(unsupported_card)

        save_card = CardWidget(content)
        save_layout = QVBoxLayout(save_card)
        save_layout.addWidget(TitleLabel('保存映射', save_card))
        save_actions = QHBoxLayout()
        self.save_button = PrimaryPushButton('保存模板映射', save_card)
        self.save_button.setIcon(FluentIcon.SAVE)
        self.save_button.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.save_button.setObjectName('saveTemplateMappingButton')
        self.save_button.setAccessibleName('保存模板映射到当前项目')
        self.save_button.setEnabled(False)
        save_actions.addWidget(self.save_button, 0, Qt.AlignmentFlag.AlignLeft)
        save_actions.addStretch(1)
        save_layout.addLayout(save_actions)
        self.save_status_label = BodyLabel(
            '完成至少一项章节映射后，可将新版本保存到当前 F: 项目。', save_card,
        )
        self.save_status_label.setObjectName('templateMappingSaveStatus')
        self.save_status_label.setAccessibleName('映射保存状态')
        self.save_status_label.setWordWrap(True)
        _set_status(self.save_status_label, 'neutral', self.save_status_label.text())
        save_layout.addWidget(self.save_status_label)
        content_layout.addWidget(save_card)
        content_layout.addStretch(1)
        self.page_scroll_area.setWidget(content)

        self.choose_button.clicked.connect(self._choose_template)
        self.save_button.clicked.connect(self._save)

    def set_project_root(self, project_root):
        candidate_root = validate_managed_path(Path(project_root))
        if candidate_root == self.project_root:
            return
        self.project_root = candidate_root
        self._generation += 1
        self._pending_template = None
        self._reset_template_state()
        if self._worker is not None:
            _set_status(self.template_status_label, 'loading', '项目已切换；旧模板检查结果将被丢弃。')

    def _clear_mapping_choices(self):
        while self.form.rowCount():
            self.form.removeRow(0)
        self._combos = {}
        self.mapping_empty_label.show()
        self.mapping_scroll_area.hide()

    def _reset_template_state(self):
        self.template_path = None
        self.inspection = None
        self._clear_mapping_choices()
        self.template_label.setText('尚未选择模板')
        _set_status(self.template_status_label, 'neutral', '尚未选择模板。')
        _set_status(self.save_status_label, 'neutral',
                    '完成至少一项章节映射后，可将新版本保存到当前 F: 项目。')
        if self._worker is None:
            self.operation_progress.hide()
        else:
            self.operation_progress.show()
        self.save_button.setEnabled(False)
        self._update_save()

    def set_template_path(self, path):
        try:
            candidate = Path(path)
            if (self.project_root is None or not candidate.is_absolute()
                    or not candidate.is_relative_to(self.project_root)):
                raise ValueError('\u6a21\u677f\u5fc5\u987b\u662f\u5f53\u524d F: \u9879\u76ee\u4e2d\u5df2\u5bfc\u5165\u7684\u6587\u4ef6\u3002')
            candidate = validate_managed_path(candidate)
            resolved_root = self.project_root.resolve(strict=False)
            resolved_candidate = candidate.resolve(strict=False)
            if not resolved_candidate.is_relative_to(resolved_root):
                raise ValueError('\u89e3\u6790\u540e\u7684\u6a21\u677f\u8def\u5f84\u5fc5\u987b\u4ecd\u4f4d\u4e8e\u5f53\u524d\u9879\u76ee\u76ee\u5f55\u5185\u3002')
            if candidate.suffix.casefold() != '.docx':
                raise ValueError('\u6a21\u677f\u5fc5\u987b\u662f\u5f53\u524d\u9879\u76ee\u4e2d\u7684 DOCX \u6587\u4ef6\u3002')
        except (OSError, RuntimeError, TypeError, ValueError) as exc:
            _set_status(self.template_status_label, 'error', f'\u6a21\u677f\u8303\u56f4\u65e0\u6548\uff1a{exc}')
            self.operation_failed.emit(self.error_label.text())
            return
        if candidate == self.template_path and (
                self.inspection is not None
                or (self._active_operation == 'inspection' and self._pending_template is None)):
            return
        self._generation += 1
        generation = self._generation
        self.template_path = candidate
        self.inspection = None
        self._clear_mapping_choices()
        self.template_label.setText(str(candidate))
        self.save_button.setEnabled(False)
        _set_status(self.save_status_label, 'loading', '\u8bf7\u7b49\u5f85\u6a21\u677f\u68c0\u67e5\u5b8c\u6210\u3002')
        if self._worker is not None:
            self._pending_template = (candidate, generation)
            _set_status(self.template_status_label, 'loading', '\u6b63\u5728\u7b49\u5f85\u4e0a\u4e00\u9879\u6a21\u677f\u64cd\u4f5c\u7ed3\u675f\uff0c\u968f\u540e\u7acb\u5373\u68c0\u67e5\u65b0\u9009\u7684\u6a21\u677f\u3002')
            return
        self._start_inspection(candidate, generation)

    def _start_inspection(self, candidate, generation):
        _set_status(self.template_status_label, 'loading', '\u6b63\u5728\u540e\u53f0\u68c0\u67e5 Word \u6a21\u677f\u2026')
        self._start_worker(lambda cancel, emit: self._inspect_template(candidate),
                           self._inspection_ready, operation_name='inspection',
                           generation=generation)

    @staticmethod
    def _inspect_template(candidate):
        try:
            return inspect_template(candidate)
        except UnsupportedTemplateError as exc:
            raise ValueError(
                '\u6a21\u677f\u7ed3\u6784\u6682\u4e0d\u652f\u6301\uff1a\u6587\u4ef6\u5305\u542b\u6587\u5b57\u6846\u3001'
                '\u5d4c\u5165\u5bf9\u8c61\u3001altChunk\u3001ActiveX \u6216\u5176\u4ed6\u65e0\u6cd5\u5b89\u5168\u4fdd\u7559\u7684\n'
                f'Word \u7ed3\u6784\uff1a{exc}\u3002\u539f\u6a21\u677f\u672a\u4fee\u6539\u3002'
            ) from exc

    def _start_worker(self, operation, success_slot, *, operation_name, generation=None):
        if self._worker is not None:
            raise RuntimeError('\u5df2\u6709\u6a21\u677f\u4efb\u52a1\u6b63\u5728\u8fd0\u884c\u3002')
        worker_generation = self._generation if generation is None else generation
        self._active_operation = operation_name
        self._worker = FunctionWorker(operation)
        self._worker_signals = self._worker.signals
        self._worker_signals.succeeded.connect(
            lambda result, token=worker_generation: success_slot(result, token)
        )
        self._worker_signals.failed.connect(
            lambda message, token=worker_generation: self._failed(message, token)
        )
        self._worker_signals.finished.connect(self._finished)
        self.choose_button.setEnabled(False)
        self.save_button.setEnabled(False)
        for combo in self._combos.values():
            combo.setEnabled(False)
        self.operation_progress.show()
        if operation_name == 'save':
            _set_status(self.save_status_label, 'loading', self.save_status_label.text())
        self.thread_pool.start(self._worker)

    def _choose_template(self):
        if self.project_root is None:
            _set_status(self.template_status_label, 'neutral', '请在随后打开的新建项目窗口中选择 Word 模板。')
            self.project_creation_requested.emit()
            return
        path, _ = QFileDialog.getOpenFileName(self, '选择项目中的 Word 模板',
                                              str(self.project_root / '00_inputs'),
                                              'Word 文档 (*.docx)')
        if path:
            self.set_template_path(path)

    def _inspection_ready(self, inspection, generation):
        if generation != self._generation:
            return
        if not isinstance(inspection, TemplateInspection):
            self._failed('模板检查返回了无效结果。', generation)
            return
        self.inspection = inspection
        while self.form.rowCount():
            self.form.removeRow(0)
        self._combos = {}
        headings = [item for item in inspection.anchors if item.kind == 'heading']
        tables = [item for item in inspection.anchors if item.kind == 'table']
        for key, label in _SECTIONS:
            combo = QComboBox()
            combo.setAccessibleName(f'{label} 对应的模板锚点')
            combo.addItem('不映射', None)
            for anchor in headings:
                combo.addItem(f'{anchor.label}（{anchor.style_id or "普通标题"}）', anchor.anchor_id)
            self.form.addRow(label, combo)
            self._combos[key] = combo
            combo.currentIndexChanged.connect(self._update_save)
        if tables:
            combo = QComboBox()
            combo.setAccessibleName('结果表格对应的模板锚点')
            combo.addItem('不映射', None)
            for anchor in tables:
                combo.addItem(f'表格：{anchor.label}', anchor.anchor_id)
            self.form.addRow('结果表格', combo)
            self._combos['results_table'] = combo
            combo.currentIndexChanged.connect(self._update_save)
        self.mapping_empty_label.hide()
        self.mapping_scroll_area.show()
        _set_status(self.template_status_label, 'success',
                    f'检查完成：{len(headings)} 个标题锚点、{len(tables)} 个表格锚点；请逐项确认映射。')
        _set_status(self.save_status_label, 'neutral', '至少选择一个锚点后，即可保存当前映射版本。')
        self.inspection_loaded.emit(inspection)
        self._update_save()

    def _selection(self):
        return {key: combo.currentData() for key, combo in self._combos.items()
                if combo.currentData() is not None}

    def _update_save(self, *_):
        self.save_button.setEnabled(self.inspection is not None and self.project_root is not None
                                    and bool(self._selection()) and self._worker is None)

    def _save(self):
        if self.inspection is None or self.project_root is None:
            return
        try:
            mapping = TemplateMapping.from_selection(self.inspection, self._selection())
        except ValueError as exc:
            _set_status(self.save_status_label, 'error', f'\u6620\u5c04\u65e0\u6548\uff1a{exc}')
            return
        root = self.project_root
        self.save_button.setEnabled(False)

        def save_mapping(cancel, emit):
            for version in range(1, 10000):
                if cancel.is_set():
                    raise InterruptedError('\u6a21\u677f\u6620\u5c04\u4fdd\u5b58\u5df2\u53d6\u6d88\u3002')
                try:
                    return mapping.save(root, version=version), mapping
                except FileExistsError:
                    continue
            raise ValueError('\u6620\u5c04\u7248\u672c\u7a7a\u95f4\u5df2\u7528\u5b8c\u3002')

        _set_status(self.save_status_label, 'loading', '\u6b63\u5728 F: \u9879\u76ee\u76ee\u5f55\u4e2d\u4fdd\u5b58\u65b0\u6620\u5c04\u7248\u672c\u2026')
        self._start_worker(save_mapping, self._mapping_ready, operation_name='save')

    def _mapping_ready(self, result, generation):
        path, mapping = result
        if generation == self._generation:
            _set_status(self.save_status_label, 'success', f'模板映射已保存：{path.name}')
        self.mapping_saved.emit(path, mapping)

    def _failed(self, message, generation=None):
        if generation is not None and generation != self._generation:
            return
        if self._active_operation == 'inspection':
            if '结构暂不支持' in message:
                status = message
            else:
                status = f'模板检查失败：{message}'
            _set_status(self.template_status_label, 'error', status)
        else:
            status = f'模板映射保存失败：{message}'
            _set_status(self.save_status_label, 'error', status)
        self.operation_failed.emit(status)

    def _finished(self):
        self._worker = None
        self._active_operation = None
        signals, self._worker_signals = self._worker_signals, None
        if signals is not None:
            signals.deleteLater()
        self.operation_progress.hide()
        self.choose_button.setEnabled(True)
        for combo in self._combos.values():
            combo.setEnabled(self.inspection is not None and self.project_root is not None)
        pending, self._pending_template = self._pending_template, None
        if pending is not None:
            candidate, generation = pending
            if generation == self._generation and self.project_root is not None:
                self._start_inspection(candidate, generation)
                return
        self._update_save()
