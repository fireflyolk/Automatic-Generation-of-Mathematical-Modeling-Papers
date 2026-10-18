"""Model and reasoning-effort picker for the pinned local Codex app-server."""
from PySide6.QtCore import Signal
from PySide6.QtWidgets import QDialog, QFormLayout, QHBoxLayout, QVBoxLayout
from qfluentwidgets import BodyLabel, ComboBox, PrimaryPushButton, PushButton, TitleLabel

from mathmodel_paper_studio.agents.codex_app_server import CodexModelInfo
from mathmodel_paper_studio.agents.codex_model_preferences import (
    CodexModelPreference,
    CodexModelPreferenceError,
    CodexModelPreferenceStore,
)


_EFFORT_LABELS = {
    'none': '无（none）',
    'minimal': '最小（minimal）',
    'low': '低（low）',
    'medium': '中（medium）',
    'high': '高（high）',
    'xhigh': '超高（xhigh）',
}


class CodexModelDialog(QDialog):
    """Loads a bounded local model catalog and saves only a validated selection."""

    load_requested = Signal()
    preference_saved = Signal(object)

    def __init__(self, preference_store: CodexModelPreferenceStore,
                 current_preference: CodexModelPreference | None = None, parent=None,
                 preference_error=False):
        super().__init__(parent)
        if not isinstance(preference_store, CodexModelPreferenceStore):
            raise TypeError('Codex 模型窗口需要 CodexModelPreferenceStore。')
        if current_preference is not None and not isinstance(current_preference, CodexModelPreference):
            raise TypeError('当前 Codex 模型设置类型无效。')
        self.preference_store = preference_store
        self.current_preference = current_preference
        self.preference_error = bool(preference_error)
        self._models: tuple[CodexModelInfo, ...] = ()
        self.setWindowTitle('Codex 模型与推理强度')
        self.setMinimumWidth(440)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(14)
        layout.addWidget(TitleLabel('Codex 模型与推理强度', self))
        self.disclosure_label = BodyLabel(
            '从本机 Codex 读取当前可用模型目录；此查询不发送题目或文件。', self,
        )
        self.disclosure_label.setWordWrap(True)
        layout.addWidget(self.disclosure_label)

        form = QFormLayout()
        self.model_combo = ComboBox(self)
        self.model_combo.setObjectName('codexModelCombo')
        self.effort_combo = ComboBox(self)
        self.effort_combo.setObjectName('codexReasoningEffortCombo')
        form.addRow('模型', self.model_combo)
        form.addRow('推理强度', self.effort_combo)
        layout.addLayout(form)

        self.status_label = BodyLabel('正在读取本机 Codex 模型目录…', self)
        self.status_label.setObjectName('codexModelCatalogStatus')
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)

        actions = QHBoxLayout()
        actions.addStretch(1)
        self.refresh_button = PushButton('重新加载', self)
        self.refresh_button.setObjectName('refreshCodexModelCatalogButton')
        self.save_button = PrimaryPushButton('保存', self)
        self.save_button.setObjectName('saveCodexModelSelectionButton')
        self.cancel_button = PushButton('取消', self)
        self.cancel_button.setObjectName('cancelCodexModelSelectionButton')
        actions.addWidget(self.refresh_button)
        actions.addWidget(self.save_button)
        actions.addWidget(self.cancel_button)
        layout.addLayout(actions)

        self.model_combo.currentIndexChanged.connect(self._model_changed)
        self.refresh_button.clicked.connect(self._request_reload)
        self.save_button.clicked.connect(self._save_preference)
        self.cancel_button.clicked.connect(self.reject)
        self.set_loading()

    def set_loading(self):
        self.status_label.setText('正在读取本机 Codex 模型目录…')
        self.model_combo.setEnabled(False)
        self.effort_combo.setEnabled(False)
        self.save_button.setEnabled(False)
        self.refresh_button.setEnabled(False)

    def set_load_error(self, message):
        self.status_label.setText(f'模型目录读取失败：{str(message)[:500]}')
        self.model_combo.setEnabled(False)
        self.effort_combo.setEnabled(False)
        self.save_button.setEnabled(False)
        self.refresh_button.setEnabled(True)

    def set_models(self, models):
        models = tuple(models)
        if (any(not isinstance(model, CodexModelInfo) for model in models)
                or len(models) > 1000):
            self.set_load_error('Codex 返回的模型目录无效。')
            return
        self._models = models
        self.model_combo.blockSignals(True)
        self.model_combo.clear()
        self.model_combo.addItem('沿用 Codex CLI 默认模型', userData=None)
        used_labels = set()
        for model in models:
            label = model.display_name
            if label in used_labels:
                label = f'{label}（{model.id}）'
            used_labels.add(model.display_name)
            if model.is_default:
                label += '（CLI 默认）'
            self.model_combo.addItem(label, userData=model.id)

        selected_index = 0
        if self.current_preference is not None:
            for index in range(1, self.model_combo.count()):
                if self.model_combo.itemData(index) == self.current_preference.model:
                    selected_index = index
                    break
        self.model_combo.setCurrentIndex(selected_index)
        self.model_combo.blockSignals(False)
        if self.preference_error:
            self.status_label.setText(
                '已保存的本机模型设置文件损坏。重新选择并保存会修复它；也可以恢复 Codex CLI 默认。'
            )
        elif self.current_preference is not None and selected_index == 0:
            self.status_label.setText(
                '已保存的模型当前不在可用目录中。请重新选择模型，或选择沿用 Codex CLI 默认。'
            )
        else:
            self.status_label.setText(
                '目录读取完成。选择“沿用 Codex CLI 默认模型”会清除本应用保存的覆盖项。'
                if models else '当前没有返回可选模型；可以沿用 Codex CLI 默认设置。'
            )
        self.model_combo.setEnabled(True)
        self.refresh_button.setEnabled(True)
        self.save_button.setEnabled(True)
        self._model_changed()

    def _request_reload(self):
        self.set_loading()
        self.load_requested.emit()

    def _model_changed(self, *_):
        model_id = self.model_combo.currentData()
        selected_model = next((model for model in self._models if model.id == model_id), None)
        self.effort_combo.blockSignals(True)
        self.effort_combo.clear()
        if selected_model is None:
            self.effort_combo.addItem('由 Codex CLI 默认决定', userData=None)
            self.effort_combo.setEnabled(False)
        else:
            for effort in selected_model.supported_efforts:
                self.effort_combo.addItem(_EFFORT_LABELS.get(effort, effort), userData=effort)
            desired_effort = selected_model.default_effort
            if (self.current_preference is not None
                    and self.current_preference.model == selected_model.id
                    and self.current_preference.effort in selected_model.supported_efforts):
                desired_effort = self.current_preference.effort
            for index in range(self.effort_combo.count()):
                if self.effort_combo.itemData(index) == desired_effort:
                    self.effort_combo.setCurrentIndex(index)
                    break
            self.effort_combo.setEnabled(True)
        self.effort_combo.blockSignals(False)

    def _save_preference(self):
        model_id = self.model_combo.currentData()
        try:
            if model_id is None:
                self.preference_store.clear()
                preference = None
            else:
                preference = CodexModelPreference(
                    model=model_id, effort=self.effort_combo.currentData(),
                )
                self.preference_store.save(preference)
        except (CodexModelPreferenceError, OSError, ValueError, TypeError) as exc:
            self.status_label.setText(f'设置未保存：{str(exc)[:500]}')
            return
        self.current_preference = preference
        self.preference_saved.emit(preference)
        self.accept()
