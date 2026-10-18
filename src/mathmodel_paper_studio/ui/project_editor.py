"""Edit a project's question and versioned data inputs without overwriting history."""
from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QKeySequence, QShortcut, QUndoCommand, QUndoStack
from PySide6.QtWidgets import QDialog, QFileDialog, QListWidget, QListWidgetItem, QPlainTextEdit, QVBoxLayout
from qfluentwidgets import (
    BodyLabel, CardWidget, CaptionLabel, FluentIcon, PrimaryPushButton,
    PushButton, TitleLabel,
)

from mathmodel_paper_studio.bootstrap.paths import validate_import_source_path, validate_managed_path
from mathmodel_paper_studio.domain.models import Project, SourceFile
from mathmodel_paper_studio.inputs.import_service import MAX_PROBLEM_TEXT_BYTES
from mathmodel_paper_studio.ui.workers import FunctionWorker

_DATA_KINDS = frozenset(('csv', 'xlsx', 'docx_data'))
_DATA_EXTENSIONS = frozenset(('.csv', '.xlsx', '.docx'))


class _SourcePathsCommand(QUndoCommand):
    def __init__(self, dialog, before, after, label):
        super().__init__(label)
        self._dialog = dialog
        self._before = tuple(before)
        self._after = tuple(after)

    def undo(self):
        self._dialog._set_source_paths(self._before)

    def redo(self):
        self._dialog._set_source_paths(self._after)


class ProjectEditorDialog(QDialog):
    revision_saved = Signal(int)

    def __init__(self, service, project: Project, project_root: Path, sources,
                 problem: str, *, revision: int, thread_pool=None, parent=None):
        super().__init__(parent)
        if not isinstance(project, Project):
            raise TypeError('项目编辑窗口需要 Project 记录。')
        self.service = service
        self.project = project
        self.project_root = validate_managed_path(project_root)
        self.revision = revision
        self.thread_pool = thread_pool
        self._worker = None
        self._worker_signals = None
        self._last_edit_target = 'problem'
        self._source_stack = QUndoStack(self)
        self._template_path = None
        data_paths = []
        for source in sources:
            if not isinstance(source, SourceFile) or source.project_id != project.project_id:
                raise ValueError('项目输入记录无效。')
            path = validate_managed_path(self.project_root / source.relative_path)
            if not path.is_relative_to(self.project_root):
                raise ValueError('项目输入超出项目目录。')
            if source.kind == 'docx':
                if self._template_path is not None:
                    raise ValueError('项目包含多个 Word 模板，无法编辑。')
                self._template_path = path
            elif source.kind in _DATA_KINDS:
                data_paths.append(path)
        if len(data_paths) > 32:
            raise ValueError('项目数据文件超过 32 个。')
        self._original_problem = problem
        self._original_source_paths = tuple(data_paths)
        self._source_paths = tuple(data_paths)
        self.setWindowTitle('编辑题目与数据')
        self.resize(900, 700)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 18, 22, 18)
        layout.setSpacing(12)
        layout.addWidget(TitleLabel(f'编辑项目：{project.title}', self))
        help_label = BodyLabel(
            '题目文字可直接修改；数据通过增删或重新选择文件替换。保存会生成新版本，已有运行记录和旧输入保留。',
            self,
        )
        help_label.setWordWrap(True)
        layout.addWidget(help_label)

        problem_card = CardWidget(self)
        problem_layout = QVBoxLayout(problem_card)
        problem_layout.addWidget(TitleLabel('竞赛题目文本', problem_card))
        self.problem_editor = QPlainTextEdit(problem_card)
        self.problem_editor.setObjectName('projectProblemEditor')
        self.problem_editor.setAccessibleName('可编辑的竞赛题目文本')
        self.problem_editor.setLineWrapMode(QPlainTextEdit.LineWrapMode.WidgetWidth)
        self.problem_editor.setPlainText(problem)
        self.problem_editor.textChanged.connect(self._problem_changed)
        problem_layout.addWidget(self.problem_editor)
        layout.addWidget(problem_card, 2)

        data_card = CardWidget(self)
        data_layout = QVBoxLayout(data_card)
        data_layout.addWidget(TitleLabel('数据文件', data_card))
        self.file_list = QListWidget(data_card)
        self.file_list.setObjectName('projectEditDataFiles')
        self.file_list.setAccessibleName('可编辑的数据文件清单')
        self.file_list.setSelectionMode(QListWidget.SelectionMode.ExtendedSelection)
        self.file_list.setMinimumHeight(150)
        self.file_list.currentRowChanged.connect(self._refresh_file_actions)
        data_layout.addWidget(self.file_list)
        file_actions = QVBoxLayout()
        file_row = QVBoxLayout()
        self.add_file_button = PushButton('添加数据文件', data_card)
        self.add_file_button.setObjectName('addProjectDataFile')
        self.add_file_button.setAccessibleName('添加 CSV、XLSX 或 DOCX 数据文件')
        self.add_file_button.setIcon(FluentIcon.FOLDER)
        self.replace_file_button = PushButton('替换选中数据文件', data_card)
        self.replace_file_button.setObjectName('replaceProjectDataFile')
        self.replace_file_button.setAccessibleName('重新选择文件替换当前数据')
        self.remove_file_button = PushButton('移除选中数据文件', data_card)
        self.remove_file_button.setObjectName('removeProjectDataFile')
        self.remove_file_button.setAccessibleName('移除选中的数据文件')
        self.add_file_button.clicked.connect(self._choose_add_files)
        self.replace_file_button.clicked.connect(self._choose_replacement)
        self.remove_file_button.clicked.connect(self._remove_selected)
        file_row.addWidget(self.add_file_button)
        file_row.addWidget(self.replace_file_button)
        file_row.addWidget(self.remove_file_button)
        file_actions.addLayout(file_row)
        data_layout.addLayout(file_actions)
        layout.addWidget(data_card, 2)

        self.status_label = CaptionLabel('修改尚未保存。', self)
        self.status_label.setObjectName('projectEditStatus')
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)
        actions = QVBoxLayout()
        self.undo_button = PushButton('撤销', self)
        self.undo_button.setObjectName('undoProjectEdit')
        self.undo_button.setAccessibleName('撤销最近编辑')
        self.redo_button = PushButton('重做', self)
        self.redo_button.setObjectName('redoProjectEdit')
        self.redo_button.setAccessibleName('重做已撤销的编辑')
        self.save_button = PrimaryPushButton('保存新版本', self)
        self.save_button.setObjectName('saveProjectEdit')
        self.cancel_button = PushButton('取消', self)
        self.cancel_button.setObjectName('cancelProjectEdit')
        for button in (self.undo_button, self.redo_button, self.save_button, self.cancel_button):
            actions.addWidget(button)
        layout.addLayout(actions)
        self.undo_button.clicked.connect(self._undo_latest)
        self.redo_button.clicked.connect(self._redo_latest)
        self.save_button.clicked.connect(self._save)
        self.cancel_button.clicked.connect(self.reject)
        self._source_stack.canUndoChanged.connect(self._update_history_buttons)
        self._source_stack.canRedoChanged.connect(self._update_history_buttons)
        self._set_source_paths(self._source_paths)
        self._install_file_shortcuts()
        self._update_history_buttons()
        self._update_dirty_state()

    @property
    def source_files(self) -> tuple[Path, ...]:
        return self._source_paths

    @property
    def template_path(self) -> Path | None:
        return self._template_path

    def _install_file_shortcuts(self):
        for widget in (self.file_list, self.add_file_button,
                       self.replace_file_button, self.remove_file_button):
            undo = QShortcut(QKeySequence.StandardKey.Undo, widget)
            undo.setContext(Qt.ShortcutContext.WidgetShortcut)
            undo.activated.connect(self._undo_files)
            redo = QShortcut(QKeySequence.StandardKey.Redo, widget)
            redo.setContext(Qt.ShortcutContext.WidgetShortcut)
            redo.activated.connect(self._redo_files)

    def _set_source_paths(self, paths):
        self._source_paths = tuple(Path(path) for path in paths)
        self._last_edit_target = 'files'
        self.file_list.clear()
        for path in self._source_paths:
            suffix = path.suffix.lstrip('.').upper()
            self.file_list.addItem(QListWidgetItem(f'{suffix}  {path.name}'))
        self._refresh_file_actions()
        self._update_history_buttons()
        self._update_dirty_state()

    def _record_source_paths(self, paths, label):
        candidate = tuple(Path(path) for path in paths)
        if len(candidate) > 32:
            self.status_label.setText('数据文件最多 32 个。')
            return False
        try:
            validated = tuple(validate_import_source_path(path) for path in candidate)
            if any(not path.is_file() or path.suffix.casefold() not in _DATA_EXTENSIONS
                   for path in validated):
                raise ValueError('数据文件必须是普通 CSV、XLSX 或 DOCX 文件。')
            if (len({str(path).casefold() for path in validated}) != len(validated)
                    or len({path.name.casefold() for path in validated}) != len(validated)):
                raise ValueError('数据文件不能重复，文件名也不能重复。')
        except (OSError, TypeError, ValueError) as exc:
            self.status_label.setText(f'文件范围无效：{str(exc)[:500]}')
            return False
        self._source_stack.push(_SourcePathsCommand(self, self._source_paths, validated, label))
        return True

    def _choose_add_files(self):
        paths, _selected_filter = QFileDialog.getOpenFileNames(
            self, '选择 CSV/XLSX/DOCX 数据文件', str(self.project_root),
            '数据文件 (*.csv *.xlsx *.docx)',
        )
        if paths:
            self._record_source_paths((*self._source_paths, *paths), '添加数据文件')

    def _choose_replacement(self):
        selected = self.file_list.selectedItems()
        if len(selected) != 1:
            self.status_label.setText('请先选择一个要替换的数据文件。')
            return
        row = self.file_list.row(selected[0])
        path, _selected_filter = QFileDialog.getOpenFileName(
            self, '选择替换数据文件', str(self.project_root),
            '数据文件 (*.csv *.xlsx *.docx)',
        )
        if path:
            candidate = list(self._source_paths)
            candidate[row] = Path(path)
            self._record_source_paths(candidate, '替换数据文件')

    def _remove_selected(self):
        rows = sorted({self.file_list.row(item) for item in self.file_list.selectedItems()}, reverse=True)
        if not rows:
            self.status_label.setText('请先选择要移除的数据文件。')
            return
        candidate = list(self._source_paths)
        for row in rows:
            candidate.pop(row)
        self._record_source_paths(candidate, '移除数据文件')

    def _refresh_file_actions(self, *_):
        selected_count = len(self.file_list.selectedItems())
        self.replace_file_button.setEnabled(selected_count == 1)
        self.remove_file_button.setEnabled(selected_count > 0)
        self.add_file_button.setEnabled(len(self._source_paths) < 32)

    def _problem_changed(self):
        self._last_edit_target = 'problem'
        self._update_history_buttons()
        self._update_dirty_state()

    def _undo_files(self):
        if self._source_stack.canUndo():
            self._source_stack.undo()

    def _redo_files(self):
        if self._source_stack.canRedo():
            self._source_stack.redo()

    def _undo_latest(self):
        if self._last_edit_target == 'files' and self._source_stack.canUndo():
            self._undo_files()
        elif self.problem_editor.document().isUndoAvailable():
            self.problem_editor.undo()
        else:
            self._undo_files()

    def _redo_latest(self):
        if self._last_edit_target == 'files' and self._source_stack.canRedo():
            self._redo_files()
        elif self.problem_editor.document().isRedoAvailable():
            self.problem_editor.redo()
        else:
            self._redo_files()

    def _update_history_buttons(self, *_):
        self.undo_button.setEnabled(
            self._source_stack.canUndo() or self.problem_editor.document().isUndoAvailable())
        self.redo_button.setEnabled(
            self._source_stack.canRedo() or self.problem_editor.document().isRedoAvailable())

    def _update_dirty_state(self, *_):
        changed = (self.problem_editor.toPlainText() != self._original_problem
                   or self._source_paths != self._original_source_paths)
        self.save_button.setEnabled(changed and self._worker is None)

    def _set_busy(self, busy):
        for widget in (self.problem_editor, self.file_list, self.add_file_button,
                       self.replace_file_button, self.remove_file_button,
                       self.undo_button, self.redo_button, self.cancel_button):
            widget.setEnabled(not busy)
        if busy:
            self.save_button.setText('正在保存…')
            self.save_button.setEnabled(False)
            self.status_label.setText('正在复制并校验新版本输入…')
        else:
            self.save_button.setText('保存新版本')
            self._refresh_file_actions()

    def _save(self):
        problem = self.problem_editor.toPlainText()
        if not problem.strip():
            self.status_label.setText('题目文本不能为空。')
            return
        if len(problem.encode('utf-8')) > MAX_PROBLEM_TEXT_BYTES:
            self.status_label.setText('题目文本超过 1 MiB 上限。')
            return
        if not self._source_paths:
            self.status_label.setText('至少保留一个数据文件。')
            return
        self._set_busy(True)
        self._worker = FunctionWorker(lambda _cancel, emit: self.service.update_inputs(
            self.project, problem, self._source_paths, template=self._template_path,
            expected_revision=self.revision, progress=emit,
        ))
        self._worker_signals = self._worker.signals
        self._worker_signals.event_received.connect(self._save_progress)
        self._worker_signals.succeeded.connect(self._saved)
        self._worker_signals.failed.connect(self._save_failed)
        self._worker_signals.finished.connect(self._save_finished)
        pool = self.thread_pool
        if pool is None:
            from PySide6.QtCore import QThreadPool
            pool = QThreadPool.globalInstance()
        pool.start(self._worker)

    def _save_progress(self, message):
        self.status_label.setText(str(message)[:500])

    def _saved(self, revision):
        self.status_label.setText(f'已保存为输入版本 v{revision:04d}。')
        self.revision_saved.emit(revision)
        self.accept()

    def _save_failed(self, message):
        self.status_label.setText(f'保存失败：{str(message)[:1000]}')

    def _save_finished(self):
        self._worker = None
        signals, self._worker_signals = self._worker_signals, None
        if signals is not None:
            signals.deleteLater()
        if self.result() != QDialog.DialogCode.Accepted:
            self._set_busy(False)
            self._update_history_buttons()
            self._update_dirty_state()

    def closeEvent(self, event):
        if self._worker is not None:
            event.ignore()
            self.status_label.setText('正在保存，请等待完成。')
            return
        super().closeEvent(event)

    def reject(self):
        if self._worker is not None:
            self.status_label.setText('正在保存，请等待完成。')
            return
        super().reject()
