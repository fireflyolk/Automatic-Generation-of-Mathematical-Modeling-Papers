"""Chinese PySide6 workbench shell; providers stay closed without fresh trusted evidence."""
import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from PySide6.QtCore import Qt, QUrl, QThreadPool, Signal
from PySide6.QtGui import QColor, QDesktopServices
from PySide6.QtWidgets import (
    QFrame, QHBoxLayout, QListWidget, QListWidgetItem,
    QPlainTextEdit, QScrollArea, QVBoxLayout, QWidget,
)
from qfluentwidgets import (
    BodyLabel, CaptionLabel, CardWidget, FluentIcon, FluentWindow,
    FluentSystemColor, PrimaryPushButton, PushButton,
    SubtitleLabel, Theme, TitleLabel, MessageBox, qconfig, setCustomStyleSheet,
)

from mathmodel_paper_studio.agents.preflight import Provider, ProviderStatus
from mathmodel_paper_studio.agents.providers.deepseek_api import DeepSeekApiClient
from mathmodel_paper_studio.agents.live_runtime import LiveProviderService
from mathmodel_paper_studio.bootstrap.paths import AppPaths, validate_managed_path
from mathmodel_paper_studio.domain.models import Run, RunStatus, SourceFile
from mathmodel_paper_studio.execution.python_runner import PythonRunner
from mathmodel_paper_studio.paper.docx_qa import DocxQaReport, run_docx_qa
from mathmodel_paper_studio.paper.template_inspector import inspect_template
from mathmodel_paper_studio.paper.template_mapping import TemplateMapping
from mathmodel_paper_studio.projects.project_service import ProjectService
from mathmodel_paper_studio.storage.database import Database
from mathmodel_paper_studio.storage.repositories import ProjectRepository, RunRepository
from mathmodel_paper_studio.ui.project_wizard import ProjectWizard
from mathmodel_paper_studio.ui.run_panel import RunPanel
from mathmodel_paper_studio.ui.run_review import RunReviewDialog, RunReviewModel
from mathmodel_paper_studio.ui.settings_view import SettingsView
from mathmodel_paper_studio.ui.template_mapping_view import TemplateMappingView
from mathmodel_paper_studio.ui.workers import FunctionWorker
from mathmodel_paper_studio.workflow.live_engine import LiveWorkflowEngine
from mathmodel_paper_studio.workflow.provider_clients import ProviderClientRegistry
from mathmodel_paper_studio.workflow.stages import WORKFLOW_STAGES

_MAX_MANIFEST_BYTES = 1024 * 1024
_MAX_PROFILE_BYTES = 1024 * 1024
_MAX_SOURCE_BYTES = 64 * 1024 * 1024
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


def _set_feedback(label, text, state):
    label.setText(text)
    label.setProperty('state', state)
    colors = _status_color_pair(state)
    if colors is None:
        setCustomStyleSheet(label, '', '')
        return
    light, dark = colors
    setCustomStyleSheet(label, f'QLabel {{ color: {light}; }}',
                        f'QLabel {{ color: {dark}; }}')


@dataclass(frozen=True)
class _ArtifactReviewResult:
    artifacts: tuple[tuple[str, str, Path | None, bool], ...]
    paper_path: Path | None
    qa_report: DocxQaReport | None
    review_items: tuple[str, ...]


class _WorkspaceNavigation:
    """Keep the QTabWidget navigation calls used by the workflow."""

    def __init__(self, window, pages):
        self._window = window
        self._pages = tuple(pages)

    def setCurrentWidget(self, widget):
        if widget not in self._pages:
            raise ValueError('页面不属于当前工作台。')
        self._window.switchTo(widget)

    def currentWidget(self):
        return self._window.stackedWidget.currentWidget()

    def count(self):
        return len(self._pages)

    def widget(self, index):
        return self._pages[index]


class MainWindow(FluentWindow):
    """Project list, imports, template mapping, run review, and artifact access."""
    project_selected = Signal(object)

    def __init__(self, paths: AppPaths, *, project_repository=None, run_repository=None,
                 project_service=None, workflow_engine=None, provider_statuses=None,
                 scope_verifier=None, thread_pool=None, codex_probe=None, parent=None):
        super().__init__(parent)
        if not isinstance(paths, AppPaths):
            raise TypeError('主窗口需要经过路径校验的 AppPaths。')
        self.paths = paths
        self.paths.ensure_directories()
        self.thread_pool = thread_pool or QThreadPool.globalInstance()
        self.database = Database(paths)
        self.database.migrate()
        self.projects = project_repository or ProjectRepository(self.database)
        self.runs = run_repository or RunRepository(self.database)
        self.project_service = project_service or ProjectService(paths, self.projects)
        codex_runtime = None
        if workflow_engine is None:
            codex_runtime = LiveProviderService(paths)
            provider_clients = ProviderClientRegistry({
                Provider.CODEX: codex_runtime,
                Provider.DEEPSEEK: DeepSeekApiClient(),
            })
            workflow_engine = LiveWorkflowEngine(
                paths, self.projects, self.runs, provider_client=provider_clients,
                computation_runner=PythonRunner(),
            )
        self.workflow = workflow_engine
        self.scope_verifier = scope_verifier
        self.codex_probe = codex_probe or codex_runtime
        self._codex_probe_generation = 0
        self._codex_probe_workers = {}
        self._window_closing = False
        self._project = None
        self._sources = ()
        self._project_load_generation = 0
        self._project_load_workers = {}
        self._artifact_review_generation = 0
        self._artifact_review_workers = {}
        self._run_review_generation = 0
        self._run_review_workers = {}
        self._run_review_pending = False
        self._active_run = None
        self._deepseek_consent_run_id = None
        self._review_dialog = None
        self.setWindowTitle('数模论文工作台')
        self.resize(1180, 780)
        self.project_list = QListWidget()
        self.project_list.setMinimumWidth(180)
        self.project_list.setMaximumWidth(260)
        self.project_list.setAccessibleName('项目列表')
        self.new_project_button = PrimaryPushButton('新建项目')
        self.new_project_button.setIcon(FluentIcon.FOLDER_ADD)
        self.new_project_button.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.new_project_button.setAccessibleName('新建项目')
        self.project_list.setContextMenuPolicy(Qt.ContextMenuPolicy.NoContextMenu)
        self.project_page = self._build_project_page()
        self.template_view = TemplateMappingView(thread_pool=self.thread_pool)
        self.run_panel = RunPanel(thread_pool=self.thread_pool)
        self.settings_view = SettingsView(paths, provider_statuses)
        destinations = (
            (self.project_page, 'projectPage', FluentIcon.FOLDER, '项目与数据'),
            (self.template_view, 'templatePage', FluentIcon.DOCUMENT, 'Word 模板'),
            (self.run_panel, 'runPanel', FluentIcon.PLAY, '运行与审核'),
            (self.settings_view, 'settingsPage', FluentIcon.SETTING, '设置'),
        )
        self.navigation_destinations = tuple(text for _, _, _, text in destinations)
        for page, object_name, icon, text in destinations:
            page.setObjectName(object_name)
            self.addSubInterface(page, icon, text)
        self.workspace = _WorkspaceNavigation(self, tuple(page for page, *_ in destinations))
        self.project_list.itemSelectionChanged.connect(self._select_project)
        self.new_project_button.clicked.connect(self._new_project)
        self.template_view.project_creation_requested.connect(self._new_project)
        self.run_button.clicked.connect(self._review_run)
        self.open_word_button.clicked.connect(self._open_selected_word)
        self.settings_view.provider_changed.connect(self._provider_changed)
        self.settings_view.codex_connection_test_requested.connect(self._start_codex_probe)
        self.run_panel.artifact_access_requested.connect(self._show_artifact_review)
        self.run_panel.operation_succeeded.connect(self._run_succeeded)
        self.run_panel.operation_failed.connect(self._run_failed)
        self.run_panel.resume_requested.connect(self._resume_run)
        self.run_panel.cancel_requested.connect(self._cancel_paused_run)
        self._provider_changed(self.settings_view.selected_status)
        self._load_projects()

    def _build_project_page(self):
        page = QWidget()
        page.setMinimumWidth(0)
        layout = QVBoxLayout(page)
        layout.setContentsMargins(20, 16, 20, 16)
        layout.setSpacing(12)

        header = QHBoxLayout()
        heading = QVBoxLayout()
        title = TitleLabel('项目与数据', page)
        title.setObjectName('projectPageTitle')
        heading.addWidget(title)
        self.data_root_label = CaptionLabel(f'数据目录：{self.paths.root}', page)
        self.data_root_label.setWordWrap(True)
        heading.addWidget(self.data_root_label)
        header.addLayout(heading, 1)
        self.selected_project_label = SubtitleLabel('尚未选择项目', page)
        self.selected_project_label.setObjectName('activeProjectHeader')
        self.selected_project_label.setAccessibleName('活动项目状态')
        self.selected_project_label.setWordWrap(True)
        _set_feedback(self.selected_project_label, '尚未选择项目', 'neutral')
        header.addWidget(self.selected_project_label, 1)
        layout.addLayout(header)

        columns = QHBoxLayout()
        columns.setSpacing(12)
        selector = QVBoxLayout()
        selector.addWidget(BodyLabel('项目列表', page))
        selector.addWidget(self.new_project_button)
        selector.addWidget(self.project_list, 1)
        columns.addLayout(selector, 0)

        self.project_details_scroll = QScrollArea(page)
        self.project_details_scroll.setObjectName('projectDetailsScrollArea')
        self.project_details_scroll.setWidgetResizable(True)
        self.project_details_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.project_details_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        details = QWidget(self.project_details_scroll)
        details.setMinimumWidth(0)
        details_layout = QVBoxLayout(details)
        details_layout.setContentsMargins(2, 2, 8, 10)
        details_layout.setSpacing(12)

        self.empty_state_card = CardWidget(details)
        empty_layout = QVBoxLayout(self.empty_state_card)
        empty_layout.addWidget(TitleLabel('开始一个数模项目', self.empty_state_card))
        self.empty_state_label = BodyLabel(
            '选择左侧项目查看题目、数据文件和论文产物，也可以先新建项目。',
            self.empty_state_card,
        )
        self.empty_state_label.setObjectName('emptyProjectState')
        self.empty_state_label.setWordWrap(True)
        empty_layout.addWidget(self.empty_state_label)
        details_layout.addWidget(self.empty_state_card)

        self.problem_card = CardWidget(details)
        problem_layout = QVBoxLayout(self.problem_card)
        problem_layout.addWidget(TitleLabel('题目与数据摘要', self.problem_card))
        problem_layout.addWidget(CaptionLabel('竞赛题目文本', self.problem_card))
        self.problem_view = QPlainTextEdit(self.problem_card)
        self.problem_view.setAccessibleName('当前项目题目文本')
        self.problem_view.setReadOnly(True)
        self.problem_view.setLineWrapMode(QPlainTextEdit.LineWrapMode.WidgetWidth)
        self.problem_view.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.problem_view.setMaximumHeight(140)
        problem_layout.addWidget(self.problem_view)
        problem_layout.addWidget(CaptionLabel('数据概况', self.problem_card))
        self.data_summary = QPlainTextEdit(self.problem_card)
        self.data_summary.setAccessibleName('当前项目数据摘要')
        self.data_summary.setReadOnly(True)
        self.data_summary.setLineWrapMode(QPlainTextEdit.LineWrapMode.WidgetWidth)
        self.data_summary.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.data_summary.setMaximumHeight(130)
        problem_layout.addWidget(self.data_summary)
        details_layout.addWidget(self.problem_card)

        self.source_card = CardWidget(details)
        sources_layout = QVBoxLayout(self.source_card)
        sources_layout.addWidget(TitleLabel('来源文件与运行范围', self.source_card))
        sources_note = CaptionLabel(
            '文件路径和哈希校验通过后，勾选的输入才会纳入本次运行范围。',
            self.source_card,
        )
        sources_note.setWordWrap(True)
        sources_layout.addWidget(sources_note)
        self.source_list = QListWidget(self.source_card)
        self.source_list.setAccessibleName('本次运行可选输入文件')
        self.source_list.setMinimumHeight(150)
        self.source_list.setWordWrap(True)
        self.source_list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.source_list.itemChanged.connect(self._selection_changed)
        sources_layout.addWidget(self.source_list)
        details_layout.addWidget(self.source_card)

        self.artifact_card = CardWidget(details)
        artifacts_layout = QVBoxLayout(self.artifact_card)
        artifacts_layout.addWidget(TitleLabel('产物审核', self.artifact_card))
        self.artifact_list = QListWidget(self.artifact_card)
        self.artifact_list.setAccessibleName('本次运行产物')
        self.artifact_list.setMaximumHeight(140)
        artifacts_layout.addWidget(self.artifact_list)
        self.qa_summary = BodyLabel('结构检查和 Word 页面检查结果会分开显示。', self.artifact_card)
        self.qa_summary.setAccessibleName('论文产物审核状态')
        self.qa_summary.setWordWrap(True)
        _set_feedback(self.qa_summary, self.qa_summary.text(), 'neutral')
        artifacts_layout.addWidget(self.qa_summary)
        details_layout.addWidget(self.artifact_card)

        actions = QVBoxLayout()
        self.run_button = PrimaryPushButton('审核本次运行范围…', details)
        self.run_button.setIcon(FluentIcon.PLAY)
        self.run_button.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.run_button.setAccessibleName('审核本次运行范围')
        self.run_button.setEnabled(False)
        self.open_word_button = PushButton('在 Word 中打开论文', details)
        self.open_word_button.setIcon(FluentIcon.DOCUMENT)
        self.open_word_button.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.open_word_button.setAccessibleName('在 Word 中打开论文')
        self.open_word_button.setEnabled(False)
        actions.addWidget(self.run_button)
        actions.addWidget(self.open_word_button)
        details_layout.addLayout(actions)
        self.project_details_scroll.setWidget(details)
        columns.addWidget(self.project_details_scroll, 1)
        layout.addLayout(columns, 1)

        self.setTabOrder(self.new_project_button, self.project_list)
        self.setTabOrder(self.project_list, self.source_list)
        self.setTabOrder(self.source_list, self.run_button)
        self.setTabOrder(self.run_button, self.open_word_button)
        self._set_project_page_state(False)
        return page

    def _set_project_page_state(self, has_project: bool):
        self.empty_state_card.setVisible(not has_project)
        self.problem_card.setVisible(has_project)
        self.source_card.setVisible(has_project)
        self.artifact_card.setVisible(has_project)
        self.run_button.setVisible(has_project)
        self.open_word_button.setVisible(has_project)

    def _load_projects(self):
        current_id = self._project.project_id if self._project is not None else None
        self.project_list.blockSignals(True)
        self.project_list.clear()
        for project in self.projects.list_all():
            item = QListWidgetItem(project.title)
            item.setData(Qt.ItemDataRole.UserRole, project.project_id)
            self.project_list.addItem(item)
            if project.project_id == current_id:
                item.setSelected(True)
        self.project_list.blockSignals(False)
        if current_id is None and self.project_list.count():
            self.project_list.setCurrentRow(0)
        elif self.project_list.count() == 0:
            self._project = None
            self._sources = ()
            _set_feedback(self.selected_project_label, '尚未选择项目', 'neutral')
            self.problem_view.clear()
            self.source_list.clear()
            self.data_summary.clear()
            self.run_button.setEnabled(False)
            self.open_word_button.setEnabled(False)
            self._set_project_page_state(False)

    def _new_project(self):
        wizard = ProjectWizard(self.project_service, self, thread_pool=self.thread_pool)
        wizard.project_created.connect(lambda _project: self._load_projects())
        wizard.exec()

    def _select_project(self):
        items = self.project_list.selectedItems()
        if not items:
            return
        project = self.projects.get(items[0].data(Qt.ItemDataRole.UserRole))
        if project is None:
            self._show_error('\u9879\u76ee\u8bb0\u5f55\u5df2\u4e0d\u5b58\u5728\uff0c\u8bf7\u5237\u65b0\u5217\u8868\u3002')
            return
        for worker, _signals in tuple(self._project_load_workers.values()):
            worker.cancel()
        for worker, _signals in tuple(self._run_review_workers.values()):
            worker.cancel()
        self._run_review_generation += 1
        self._run_review_pending = False
        self._project_load_generation += 1
        token = self._project_load_generation
        self._project = project
        self._sources = ()
        self._set_project_page_state(True)
        _set_feedback(self.selected_project_label, f'活动项目：{project.title} · 正在后台读取', 'loading')
        self.problem_view.clear()
        self.source_list.blockSignals(True)
        self.source_list.clear()
        self.source_list.blockSignals(False)
        self.data_summary.setPlainText('\u6b63\u5728 F: \u540e\u53f0\u6821\u9a8c\u8f93\u5165\u6e05\u5355\u548c\u6587\u4ef6\u54c8\u5e0c\u2026')
        self.run_button.setEnabled(False)
        self.open_word_button.setEnabled(False)
        project_root = self.paths.root / project.relative_path
        worker = FunctionWorker(lambda cancel, emit: self._read_project_snapshot(
            project.project_id, project_root, cancel, emit))
        signals = worker.signals
        self._project_load_workers[token] = (worker, signals)
        signals.succeeded.connect(lambda result, token=token: self._project_load_succeeded(token, result))
        signals.failed.connect(lambda message, token=token: self._project_load_failed(token, message))
        signals.finished.connect(lambda token=token: self._project_load_finished(token))
        self.thread_pool.start(worker)
        self.project_selected.emit(project)

    def _project_load_succeeded(self, token, snapshot):
        if token != self._project_load_generation:
            return
        project_root, sources, problem, summary = snapshot
        try:
            self.template_view.set_project_root(project_root)
            self._sources = sources
            self.source_list.blockSignals(True)
            self.source_list.clear()
            for source in sources:
                file_path = project_root / source.relative_path
                item = QListWidgetItem(f'{source.kind.upper()}  {source.relative_path}')
                item.setData(Qt.ItemDataRole.UserRole, source.source_id)
                item.setData(Qt.ItemDataRole.UserRole + 1, str(file_path))
                item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                item.setCheckState(Qt.CheckState.Checked)
                if source.kind == 'problem':
                    item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsUserCheckable)
                self.source_list.addItem(item)
            self.problem_view.setPlainText(problem)
            self.data_summary.setPlainText(summary)
            _set_feedback(self.selected_project_label, f'活动项目：{self._project.title} · 已就绪', 'success')
            templates = [item for item in sources if item.kind == 'docx']
            if templates:
                self.template_view.set_template_path(project_root / templates[0].relative_path)
            self.run_button.setEnabled(bool(sources) and not self._run_review_pending)
        except (OSError, ValueError, TypeError) as exc:
            self._project_load_failed(token, str(exc))
        finally:
            self.source_list.blockSignals(False)

    def _project_load_failed(self, token, message):
        if token != self._project_load_generation:
            return
        self._sources = ()
        self.run_button.setEnabled(False)
        self.data_summary.setPlainText(f'\u8f93\u5165\u6e05\u5355\u65e0\u6cd5\u9a8c\u8bc1\uff1a{str(message)[:1000]}')
        if self._project is not None:
            _set_feedback(self.selected_project_label, f'{self._project.title} \uff08\u8f93\u5165\u68c0\u67e5\u5931\u8d25\uff09', 'error')

    def _project_load_finished(self, token):
        entry = self._project_load_workers.pop(token, None)
        if entry is not None:
            _worker, signals = entry
            signals.deleteLater()

    def _read_project_snapshot(self, project_id, project_root, cancel_event, emit_event):
        project_root = validate_managed_path(project_root)
        if not project_root.is_dir():
            raise ValueError('\u9879\u76ee\u76ee\u5f55\u4e0d\u5b58\u5728\u3002')
        emit_event('\u5f00\u59cb\u8bfb\u53d6\u548c\u6821\u9a8c\u9879\u76ee\u8f93\u5165\u3002')
        sources = self._load_sources(project_id, project_root, cancel_event=cancel_event,
                                     emit_event=emit_event)
        self._raise_if_cancelled(cancel_event)
        problem_source = next((item for item in sources if item.kind == 'problem'), None)
        if problem_source is None:
            raise ValueError('\u9879\u76ee\u7f3a\u5c11\u9898\u76ee\u6587\u672c\u8f93\u5165\u3002')
        problem = self._read_small_text(project_root / problem_source.relative_path, 1024 * 1024)
        summary = self._load_data_summary(project_root, cancel_event=cancel_event)
        return project_root, sources, problem, summary

    def _load_sources(self, project_id: str, project_root: Path, *, cancel_event=None,
                      emit_event=None) -> tuple[SourceFile, ...]:
        manifest = validate_managed_path(project_root / '00_inputs' / 'manifest-v0001.json')
        if not manifest.is_relative_to(project_root) or not manifest.is_file() or manifest.stat().st_size > _MAX_MANIFEST_BYTES:
            raise ValueError('\u9879\u76ee\u8f93\u5165\u6e05\u5355\u7f3a\u5931\u6216\u8d85\u8fc7 1 MiB \u4e0a\u9650\u3002')
        value = json.loads(manifest.read_text(encoding='utf-8'))
        if (not isinstance(value, dict) or value.get('version') != 1
                or not isinstance(value.get('sources'), list) or len(value['sources']) > 33):
            raise ValueError('\u9879\u76ee\u8f93\u5165\u6e05\u5355\u7ed3\u6784\u65e0\u6548\u3002')
        records = []
        for index, item in enumerate(value['sources'], start=1):
            self._raise_if_cancelled(cancel_event)
            if not isinstance(item, dict) or not isinstance(item.get('record'), dict):
                raise ValueError('\u9879\u76ee\u8f93\u5165\u6e05\u5355\u8bb0\u5f55\u65e0\u6548\u3002')
            source = SourceFile(**item['record'])
            if source.project_id != project_id:
                raise ValueError('\u8f93\u5165\u8bb0\u5f55\u4e0e\u5f53\u524d\u9879\u76ee\u4e0d\u5339\u914d\u3002')
            file_path = validate_managed_path(project_root / source.relative_path)
            if (not file_path.is_relative_to(project_root) or not file_path.is_file()
                    or file_path.stat().st_size != source.size_bytes
                    or source.size_bytes > _MAX_SOURCE_BYTES):
                raise ValueError('\u9879\u76ee\u8f93\u5165\u526f\u672c\u5927\u5c0f\u6216\u6587\u4ef6\u72b6\u6001\u5df2\u53d8\u5316\u3002')
            digest = hashlib.sha256()
            with file_path.open('rb') as stream:
                chunk = stream.read(128 * 1024)
                while chunk:
                    self._raise_if_cancelled(cancel_event)
                    digest.update(chunk)
                    chunk = stream.read(128 * 1024)
            if digest.hexdigest() != source.sha256:
                raise ValueError('\u9879\u76ee\u8f93\u5165\u526f\u672c\u54c8\u5e0c\u6821\u9a8c\u5931\u8d25\uff0c\u5df2\u7981\u6b62\u8fd0\u884c\u3002')
            records.append(source)
            if emit_event is not None:
                emit_event(f'\u5df2\u6821\u9a8c\u8f93\u5165 {index}/{len(value["sources"])}\u3002')
        if not records or not any(item.kind == 'problem' for item in records):
            raise ValueError('\u9879\u76ee\u7f3a\u5c11\u9898\u76ee\u6587\u672c\u8f93\u5165\u3002')
        return tuple(records)

    @staticmethod
    def _raise_if_cancelled(cancel_event):
        if cancel_event is not None and cancel_event.is_set():
            raise InterruptedError('\u9879\u76ee\u5df2\u5207\u6362\uff0c\u5df2\u505c\u6b62\u5f53\u524d\u6587\u4ef6\u6821\u9a8c\u3002')

    @staticmethod
    def _read_small_text(path: Path, limit: int) -> str:
        if path.stat().st_size > limit:
            raise ValueError('题目文本超过 1 MiB 上限。')
        return path.read_text(encoding='utf-8')

    @staticmethod
    def _load_data_summary(project_root: Path, cancel_event=None) -> str:
        summaries = []
        analysis = validate_managed_path(project_root / '10_analysis')
        if not analysis.is_dir():
            return '当前项目没有数据摘要。'
        for path in sorted(analysis.glob('source-*-profile.json')):
            MainWindow._raise_if_cancelled(cancel_event)
            path = validate_managed_path(path)
            if not path.is_relative_to(project_root):
                raise ValueError('\u6570\u636e\u6458\u8981\u8d85\u51fa\u9879\u76ee\u76ee\u5f55\u3002')
            if not path.is_file() or path.stat().st_size > _MAX_PROFILE_BYTES:
                raise ValueError('数据摘要缺失或超过 1 MiB 上限。')
            value = json.loads(path.read_text(encoding='utf-8'))
            if not isinstance(value, dict):
                raise ValueError('数据摘要格式无效。')
            summaries.append(f"{path.name}：{value.get('row_count', '?')} 行，{len(value.get('columns', []))} 列")
        return '\n'.join(summaries) if summaries else '已导入数据；尚无表格摘要。'

    def _selection_changed(self, *_):
        self.run_button.setEnabled(self._project is not None and bool(self._sources)
                                   and not self._run_review_pending)

    def _selected_sources(self):
        selected_ids = []
        readable = []
        for row in range(self.source_list.count()):
            item = self.source_list.item(row)
            if item.data(Qt.ItemDataRole.UserRole) == next(
                    (source.source_id for source in self._sources if source.kind == 'problem'), None):
                chosen = True
            else:
                chosen = item.checkState() == Qt.CheckState.Checked
            if chosen:
                selected_ids.append(item.data(Qt.ItemDataRole.UserRole))
                readable.append(Path(item.data(Qt.ItemDataRole.UserRole + 1)))
        return tuple(readable), tuple(selected_ids)

    def _review_run(self):
        if self._project is None or self._run_review_pending:
            return
        readable, selected_ids = self._selected_sources()
        if not readable or not selected_ids:
            self._show_error('\u8bf7\u9009\u62e9\u81f3\u5c11\u4e00\u4e2a\u53ef\u8bfb\u8f93\u5165\u6587\u4ef6\u3002')
            return
        provider_status = self.settings_view.selected_status
        run_id = uuid4().hex
        run = Run(run_id, self._project.project_id, provider_status.provider,
                  RunStatus.CREATED, f'90_runs/{run_id}')
        project = self._project
        self._run_review_generation += 1
        token = self._run_review_generation
        self._run_review_pending = True
        self.run_button.setEnabled(False)
        _set_feedback(self.qa_summary, '\u6b63\u5728\u540e\u53f0\u68c0\u67e5\u672c\u6b21\u6587\u4ef6\u8303\u56f4\u2026', 'loading')
        worker = FunctionWorker(lambda cancel, emit: self._prepare_run_review(
            project, run, readable, selected_ids, provider_status, cancel, emit))
        signals = worker.signals
        self._run_review_workers[token] = (worker, signals)
        signals.succeeded.connect(lambda result, token=token: self._run_review_ready(token, result))
        signals.failed.connect(lambda message, token=token: self._run_review_failed(token, message))
        signals.finished.connect(lambda token=token: self._run_review_finished(token))
        self.thread_pool.start(worker)

    def _verify_deepseek_scope(self, project, run, readable, planned_output):
        try:
            project_root = validate_managed_path(self.paths.root / project.relative_path)
            output_root = validate_managed_path(Path(planned_output))
            expected_output = validate_managed_path(project_root / run.relative_path)
            if output_root != expected_output or not output_root.is_relative_to(project_root):
                raise ValueError('输出目录超出本项目范围。')
            if not readable:
                raise ValueError('没有明确选择可读输入。')
            for candidate in readable:
                path = validate_managed_path(Path(candidate))
                if path == project_root or not path.is_relative_to(project_root) or not path.is_file():
                    raise ValueError('输入文件超出本项目范围或不可读。')
        except (OSError, TypeError, ValueError):
            return False, '本机文件范围检查未通过，DeepSeek 请求已阻止。', (
                '输入或输出路径未通过 F: 项目目录校验。',
            )
        return True, (
            'DeepSeek 仅接收审核页列出的文本；没有文件附件、工具调用或本机文件访问权限。'
        ), ()

    def _prepare_run_review(self, project, run, readable, selected_ids, provider_status,
                            cancel_event, emit_event):
        self._raise_if_cancelled(cancel_event)
        project_root = validate_managed_path(self.paths.root / project.relative_path)
        planned_output = validate_managed_path(project_root / run.relative_path)
        scope_verified = False
        scope_message = '\u5c1a\u672a\u5b8c\u6210\u672c\u6b21\u53ef\u4fe1\u9694\u79bb\u4e0e\u6587\u4ef6\u8303\u56f4\u9884\u68c0\u3002'
        scope_errors = ()
        scope_verifier = self.scope_verifier
        if scope_verifier is None and run.provider is Provider.DEEPSEEK:
            scope_verifier = self._verify_deepseek_scope
        if scope_verifier is not None:
            try:
                verified = scope_verifier(project, run, readable, planned_output)
                if not isinstance(verified, (tuple, list)) or len(verified) != 3:
                    raise ValueError('\u8303\u56f4\u68c0\u67e5\u7ed3\u679c\u683c\u5f0f\u65e0\u6548\u3002')
                scope_verified, scope_message, scope_errors = verified
            except Exception as exc:
                scope_errors = (f'\u8303\u56f4\u9a8c\u8bc1\u5931\u8d25\uff1a{str(exc)[:500]}',)
        self._raise_if_cancelled(cancel_event)
        model = RunReviewModel(
            provider_status, project_root, readable, (planned_output,), WORKFLOW_STAGES,
            bool(scope_verified), str(scope_message), scope_errors=tuple(scope_errors),
        )
        emit_event('\u672c\u6b21\u6587\u4ef6\u8303\u56f4\u9884\u68c0\u5df2\u5b8c\u6210\u3002')
        return run, tuple(readable), tuple(selected_ids), model

    def _run_review_ready(self, token, result):
        if token != self._run_review_generation:
            return
        run, readable, selected_ids, model = result
        dialog = RunReviewDialog(model, self)
        self._review_dialog = dialog
        dialog.start_requested.connect(lambda _model: self._start_run(run, readable, selected_ids))
        dialog.edit_scope_requested.connect(lambda: self.workspace.setCurrentWidget(self.project_page))
        dialog.exec()
        self._review_dialog = None
        self._run_review_pending = False
        self.run_button.setEnabled(self._project is not None and bool(self._sources))

    def _run_review_failed(self, token, message):
        if token != self._run_review_generation:
            return
        self._run_review_pending = False
        self.run_button.setEnabled(self._project is not None and bool(self._sources))
        _set_feedback(self.qa_summary, f'\u8fd0\u884c\u524d\u8303\u56f4\u9884\u68c0\u5931\u8d25\uff1a{str(message)[:1000]}', 'error')

    def _run_review_finished(self, token):
        entry = self._run_review_workers.pop(token, None)
        if entry is not None:
            _worker, signals = entry
            signals.deleteLater()

    def _start_run(self, run: Run, sources, selected_ids):
        if (self.run_panel.active_worker is not None
                or (self._active_run is not None
                    and self._active_run.run_id != run.run_id
                    and self.run_panel.current_status is RunStatus.PAUSED)):
            self._show_error('当前有一项运行仍在后台或暂停；请先结束、继续或取消该运行，再开始新运行。')
            return
        self.run_panel.set_provider(run.provider)
        deepseek_client = None
        if run.provider is Provider.DEEPSEEK:
            deepseek_client = self._provider_client(Provider.DEEPSEEK)
            authorize = getattr(deepseek_client, 'authorize_chargeable_requests', None)
            if not callable(authorize):
                self._show_error('DeepSeek 文本服务尚未接入；本次运行没有发送任何请求。')
                return
            try:
                # This method is reachable only after the DeepSeek-specific
                # disclosure and consent button has been clicked.
                authorize(run.run_id)
                self._deepseek_consent_run_id = run.run_id
            except (ValueError, TypeError, RuntimeError):
                self._show_error('DeepSeek 本次费用授权未能建立；没有发送任何请求。')
                return
        consent_active = deepseek_client is not None

        def revoke_consent():
            nonlocal consent_active
            if consent_active:
                deepseek_client.revoke_chargeable_requests(run.run_id)
                consent_active = False
                if self._deepseek_consent_run_id == run.run_id:
                    self._deepseek_consent_run_id = None

        def finish_or_retain_consent(state):
            status = state if isinstance(state, RunStatus) else getattr(state, 'status', None)
            try:
                status = RunStatus(status)
            except (TypeError, ValueError):
                status = None
            if status is not RunStatus.PAUSED:
                revoke_consent()
            return state

        def operation(cancel, emit):
            try:
                emit('运行请求已进入后台线程。')
                state = self.workflow.start(self._project.project_id, run.run_id, self._sources,
                                            selected_ids, cancel_event=cancel)
                for record in state.stage_records:
                    emit(f'{record.stage.value}：{record.status.value}')
                for diagnostic in state.diagnostics:
                    emit(diagnostic)
                return finish_or_retain_consent(state)
            except Exception:
                revoke_consent()
                raise

        def resume_operation(cancel, emit):
            try:
                emit('正在恢复已验证的运行检查点。')
                state = self.workflow.resume(run.run_id, self._sources, selected_ids, cancel_event=cancel)
                return finish_or_retain_consent(state)
            except Exception:
                revoke_consent()
                raise

        def cancel_operation(cancel, emit):
            try:
                emit('正在取消已暂停运行。')
                state = self.workflow.cancel(run.run_id)
                return finish_or_retain_consent(RunStatus.CANCELLED if state is None else state)
            except Exception:
                revoke_consent()
                raise

        try:
            self.runs.add(run)
            self._active_run = run
            self.artifact_list.clear()
            self.open_word_button.setEnabled(False)
            self.open_word_button.setProperty('paperPath', '')
            _set_feedback(self.qa_summary, '运行已开始；产物会在后台校验。', 'loading')
            self.run_panel.set_run_id(run.run_id)
            self.workspace.setCurrentWidget(self.run_panel)
            self.run_panel.start_operation(operation, resume_operation=resume_operation,
                                           cancel_operation=cancel_operation)
        except Exception as exc:
            revoke_consent()
            if self._active_run is run:
                self._active_run = None
            self.run_panel.set_run_state(RunStatus.FAILED, '运行未能启动。')
            self._show_error(f'无法启动运行：{exc}')

    def _run_succeeded(self, result):
        if not hasattr(result, 'artifacts'):
            return
        if result.artifacts:
            self._start_artifact_review(result)
            if result.pause_reason is not None:
                _set_feedback(self.qa_summary, f'\u8fd0\u884c\u5df2\u6682\u505c\uff1a{result.pause_reason.value}\u3002\u6b63\u5728\u6821\u9a8c\u5df2\u751f\u6210\u4ea7\u7269\u3002', 'warning')
        else:
            items = []
            if result.pause_reason is not None:
                items.append(f'\u8fd0\u884c\u5df2\u6682\u505c\uff1a{result.pause_reason.value}')
            items.append('\u672c\u6b21\u8fd0\u884c\u6ca1\u6709\u751f\u6210\u4ea7\u7269\u3002')
            self.artifact_list.clear()
            self.open_word_button.setEnabled(False)
            _set_feedback(self.qa_summary, self._format_docx_qa_summary(None, tuple(items)), 'warning')

    def _start_artifact_review(self, state):
        if self._project is None:
            return
        for worker, _signals in tuple(self._artifact_review_workers.values()):
            worker.cancel()
        self._artifact_review_generation += 1
        token = self._artifact_review_generation
        project = self._project
        project_root = self.paths.root / project.relative_path
        template_source = next((project_root / source.relative_path for source in self._sources
                                if source.kind == 'docx'), None)
        worker = FunctionWorker(lambda cancel, emit: self._inspect_run_artifacts(
            state, project_root, template_source, cancel, emit))
        signals = worker.signals
        self._artifact_review_workers[token] = (worker, signals)
        signals.succeeded.connect(lambda result, token=token: self._artifact_review_succeeded(token, result))
        signals.failed.connect(lambda message, token=token: self._artifact_review_failed(token, message))
        signals.finished.connect(lambda token=token: self._artifact_review_finished(token))
        _set_feedback(self.qa_summary, '\u6b63\u5728\u540e\u53f0\u6821\u9a8c\u4ea7\u7269\u54c8\u5e0c\u5e76\u68c0\u67e5 Word \u7ed3\u6784\u2026', 'loading')
        self.open_word_button.setEnabled(False)
        self.thread_pool.start(worker)

    def _inspect_run_artifacts(self, state, project_root, template_source, cancel_event, emit_event):
        rows = []
        review_items = []
        paper_path = None
        for index, artifact in enumerate(state.artifacts, start=1):
            self._raise_if_cancelled(cancel_event)
            path = None
            verified = False
            try:
                path = validate_managed_path(project_root / artifact.relative_path)
                if not path.is_relative_to(project_root):
                    raise ValueError('\u4ea7\u7269\u8def\u5f84\u79bb\u5f00\u4e86\u9879\u76ee\u76ee\u5f55\u3002')
                if not path.is_file() or path.stat().st_size != artifact.size_bytes:
                    raise ValueError('\u4ea7\u7269\u6587\u4ef6\u5927\u5c0f\u4e0e\u8bb0\u5f55\u4e0d\u5339\u914d\u3002')
                if artifact.size_bytes > _MAX_SOURCE_BYTES:
                    raise ValueError('\u4ea7\u7269\u8d85\u8fc7 64 MiB \u4e0a\u9650\u3002')
                digest = hashlib.sha256()
                with path.open('rb') as stream:
                    chunk = stream.read(128 * 1024)
                    while chunk:
                        self._raise_if_cancelled(cancel_event)
                        digest.update(chunk)
                        chunk = stream.read(128 * 1024)
                if digest.hexdigest() != artifact.sha256:
                    raise ValueError('\u4ea7\u7269\u54c8\u5e0c\u6821\u9a8c\u5931\u8d25\u3002')
                verified = True
            except (OSError, TypeError, ValueError) as exc:
                review_items.append(f'\u4ea7\u7269\u6821\u9a8c\u5931\u8d25 {artifact.relative_path}\uff1a{exc}')
            rows.append((artifact.kind, artifact.relative_path, path, verified))
            if verified and artifact.kind == 'paper':
                if path.suffix.casefold() == '.docx':
                    paper_path = path
                else:
                    review_items.append('\u8bba\u6587\u4ea7\u7269\u4e0d\u662f DOCX \u6587\u4ef6\u3002')
            emit_event(f'\u5df2\u68c0\u67e5\u4ea7\u7269 {index}/{len(state.artifacts)}\u3002')

        report = None
        if paper_path is None:
            review_items.append('\u672a\u627e\u5230\u53ef\u6821\u9a8c\u54c8\u5e0c\u7684 DOCX \u8bba\u6587\u3002')
        elif template_source is None:
            review_items.append('\u6ca1\u6709\u53ef\u7528\u7684\u6e90 Word \u6a21\u677f\uff0c\u7ed3\u6784\u68c0\u67e5\u672a\u8fd0\u884c\u3002')
        else:
            try:
                inspection = inspect_template(template_source)
                version = self._latest_template_mapping_version(project_root)
                if version is None:
                    review_items.append('\u6a21\u677f\u6620\u5c04\u672a\u4fdd\u5b58\uff0c\u7ed3\u6784\u68c0\u67e5\u672a\u8fd0\u884c\u3002')
                else:
                    mapping = TemplateMapping.load(project_root, version=version)
                    required_sections = tuple(item.section_key for item in mapping.bindings
                                              if item.kind == 'heading')
                    expected_evidence = tuple(item.evidence_id for item in state.artifacts
                                              if item.kind not in ('paper', 'validation'))
                    report = run_docx_qa(
                        paper_path, inspection, mapping,
                        required_section_keys=required_sections,
                        expected_evidence_ids=expected_evidence,
                    )
            except (OSError, TypeError, ValueError) as exc:
                review_items.append(f'\u6a21\u677f\u4e0d\u53d7\u652f\u6301\u6216\u7ed3\u6784\u68c0\u67e5\u672a\u5b8c\u6210\uff1a{str(exc)[:500]}')
        return _ArtifactReviewResult(tuple(rows), paper_path, report, tuple(review_items))

    @staticmethod
    def _latest_template_mapping_version(project_root):
        directory = validate_managed_path(project_root / '70_review')
        if not directory.is_dir():
            return None
        versions = []
        for candidate in directory.glob('template-mapping-v*.json'):
            match = re.fullmatch(r'template-mapping-v([0-9]{4})\.json', candidate.name)
            if match is not None:
                version = int(match.group(1))
                if 1 <= version <= 9999:
                    versions.append(version)
        return max(versions, default=None)

    @staticmethod
    def _format_docx_qa_summary(report, review_items=()):
        lines = []
        if report is None:
            lines.append('\u8bba\u6587\u7ed3\u6784\u68c0\u67e5\u672a\u6267\u884c\u3002')
        elif not report.package_valid or report.structural_status == 'failed':
            lines.append('\u7ed3\u6784\u68c0\u67e5\u672a\u901a\u8fc7\u3002')
        elif report.structural_status == 'review_required':
            lines.append('\u7ed3\u6784\u68c0\u67e5\u9700\u4eba\u5de5\u590d\u6838\u3002')
        else:
            lines.append('\u7ed3\u6784\u68c0\u67e5\u901a\u8fc7\uff1b\u4ecd\u9700\u4eba\u5de5\u590d\u6838\u3002')
        if report is not None:
            groups = (
                ('\u7f3a\u5c11\u7ae0\u8282', report.missing_sections),
                ('\u672a\u66ff\u6362\u5360\u4f4d\u7b26', report.unresolved_placeholders),
                ('\u7f3a\u5c11\u56fe\u7247', report.missing_figures),
                ('\u5173\u7cfb\u9519\u8bef', report.broken_relationships),
                ('\u5f85\u8865\u5f15\u7528', report.pending_citations),
                ('\u5f85\u4eba\u5de5\u6838\u9a8c', report.pending_reviews),
                ('\u7f3a\u5c11\u8bc1\u636e\u5f15\u7528', report.missing_evidence),
                ('\u4ea7\u7269\u5305\u68c0\u67e5\u9519\u8bef', report.package_errors),
            )
            lines.extend(f'{label}\uff1a{"; ".join(values)}' for label, values in groups if values)
        lines.extend(f'\u590d\u6838\u9879\uff1a{item}' for item in review_items)
        lines.append('\u6570\u5b66\u7ed3\u8bba\u548c\u6570\u503c\u5c1a\u672a\u72ec\u7acb\u9a8c\u8bc1\uff0c\u8bf7\u4eba\u5de5\u590d\u6838\u3002')
        lines.append('Word \u9875\u9762\u6e32\u67d3\uff1a\u672a\u6267\u884c\u3002')
        return '\n'.join(lines)

    def _artifact_review_succeeded(self, token, result):
        if token != self._artifact_review_generation:
            return
        self.artifact_list.clear()
        for kind, relative_path, path, verified in result.artifacts:
            state = '\u54c8\u5e0c\u6821\u9a8c\u901a\u8fc7' if verified else '\u672a\u9a8c\u8bc1'
            item = QListWidgetItem(f'{kind.upper()}  {relative_path}  \uff08{state}\uff09')
            if path is not None:
                item.setData(Qt.ItemDataRole.UserRole, str(path))
            self.artifact_list.addItem(item)
        paper_path = result.paper_path
        self.open_word_button.setEnabled(paper_path is not None)
        self.open_word_button.setProperty('paperPath', '' if paper_path is None else str(paper_path))
        state = ('error' if result.qa_report is None or not result.qa_report.package_valid
                 or result.qa_report.structural_status == 'failed' else 'warning')
        _set_feedback(self.qa_summary,
                      self._format_docx_qa_summary(result.qa_report, result.review_items), state)

    def _artifact_review_failed(self, token, message):
        if token != self._artifact_review_generation:
            return
        self.open_word_button.setEnabled(False)
        _set_feedback(self.qa_summary, self._format_docx_qa_summary(
            None, (f'\u4ea7\u7269\u68c0\u67e5\u5931\u8d25\uff1a{str(message)[:1000]}',)), 'error')

    def _artifact_review_finished(self, token):
        entry = self._artifact_review_workers.pop(token, None)
        if entry is not None:
            _worker, signals = entry
            signals.deleteLater()

    def _run_failed(self, message):
        _set_feedback(self.qa_summary, f'\u8fd0\u884c\u5931\u8d25\uff1a{message}', 'error')

    def _open_selected_word(self):
        raw = self.open_word_button.property('paperPath')
        if not raw:
            self._show_error('当前运行没有可打开的 Word 论文。')
            return
        try:
            path = validate_managed_path(Path(raw))
            if not path.is_file() or path.suffix.casefold() != '.docx':
                raise ValueError('论文文件不可用。')
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))
        except (OSError, ValueError) as exc:
            self._show_error(f'无法打开 Word 论文：{exc}')

    def _start_codex_probe(self):
        if self.codex_probe is None:
            self.settings_view.set_codex_probe_failure()
            return
        self.settings_view.set_codex_probe_pending()
        self._codex_probe_generation += 1
        token = self._codex_probe_generation
        def verify(cancel, _emit):
            verify_connection = getattr(self.codex_probe, 'verify_connection', None)
            if callable(verify_connection):
                return verify_connection(Provider.CODEX, cancel)
            return self.codex_probe.verify(Provider.CODEX, cancel)
        worker = FunctionWorker(verify)
        signals = worker.signals
        self._codex_probe_workers[token] = (worker, signals)
        signals.succeeded.connect(
            lambda result, token=token: self._codex_probe_succeeded(token, result))
        signals.failed.connect(
            lambda _message, token=token: self._codex_probe_failed(token))
        signals.finished.connect(lambda token=token: self._codex_probe_finished(token))
        try:
            self.thread_pool.start(worker)
        except Exception:
            worker.cancel()
            self._codex_probe_workers.pop(token, None)
            self.settings_view.set_codex_probe_failure()

    def _codex_probe_succeeded(self, token, result):
        if token != self._codex_probe_generation or self._window_closing:
            return
        status = getattr(result, 'status', result)
        if not isinstance(status, ProviderStatus) or status.provider is not Provider.CODEX:
            self.settings_view.set_codex_probe_failure()
            return
        self.settings_view.set_codex_probe_status(status)

    def _codex_probe_failed(self, token):
        if token == self._codex_probe_generation and not self._window_closing:
            self.settings_view.set_codex_probe_failure()

    def _codex_probe_finished(self, token):
        entry = self._codex_probe_workers.pop(token, None)
        if entry is not None:
            _worker, signals = entry
            signals.deleteLater()

    def _provider_changed(self, status):
        self.run_button.setEnabled(self._project is not None and bool(self._sources)
                                   and not self._run_review_pending)
        self.run_button.setToolTip(status.message)
        if self._active_run is None and self.run_panel.active_worker is None:
            self.run_panel.set_provider(status.provider)

    def _show_artifact_review(self):
        self.workspace.setCurrentWidget(self.project_page)


    def _provider_client(self, provider):
        client_or_registry = getattr(self.workflow, 'provider_client', None)
        resolver = getattr(client_or_registry, 'for_provider', None)
        return resolver(provider) if callable(resolver) else client_or_registry

    def _resume_run(self, run_id):
        if self._active_run is None or self._active_run.run_id != run_id:
            self._show_error('当前窗口没有可继续的运行记录。')

    def _cancel_paused_run(self, run_id):
        if self._active_run is None or self._active_run.run_id != run_id:
            self._show_error('当前窗口没有可取消的运行记录。')

    def closeEvent(self, event):
        if self.run_panel.active_worker is not None:
            self.run_panel.cancel()
        run_id = self._deepseek_consent_run_id
        if run_id is not None:
            client = self._provider_client(Provider.DEEPSEEK)
            revoke = getattr(client, 'revoke_chargeable_requests', None)
            try:
                if not callable(revoke):
                    raise RuntimeError('DeepSeek 授权客户端不可用。')
                revoke(run_id)
                self._deepseek_consent_run_id = None
            except Exception as exc:
                self._show_error(f'无法撤销 DeepSeek 本次运行授权：{exc}')
                event.ignore()
                return
        self._window_closing = True
        for worker, _signals in tuple(self._codex_probe_workers.values()):
            worker.cancel()
        super().closeEvent(event)

    def _show_error(self, message):
        MessageBox('操作未完成', str(message)[:2000], self).exec()
