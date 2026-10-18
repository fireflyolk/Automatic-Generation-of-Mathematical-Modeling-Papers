"""Model and reasoning-effort picker for the DeepSeek API."""
from PySide6.QtCore import Signal
from PySide6.QtWidgets import QDialog, QFormLayout, QHBoxLayout, QVBoxLayout
from qfluentwidgets import BodyLabel, ComboBox, PrimaryPushButton, PushButton, TitleLabel

from mathmodel_paper_studio.agents.deepseek_model_catalog import DeepSeekModelInfo
from mathmodel_paper_studio.agents.deepseek_model_preferences import (
    DEFAULT_DEEPSEEK_EFFORT,
    DEFAULT_DEEPSEEK_MODEL,
    DeepSeekModelPreference,
    DeepSeekModelPreferenceError,
    DeepSeekModelPreferenceStore,
)


_EFFORT_LABELS = {
    'none': '关闭推理（none）',
    'low': '低（low）',
    'high': '高（high）',
    'max': '最高（max）',
}


class DeepSeekModelDialog(QDialog):
    """Loads an API-key-protected model catalog and persists only selection IDs."""

    load_requested = Signal()
    preference_saved = Signal(object)

    def __init__(self, preference_store: DeepSeekModelPreferenceStore,
                 current_preference: DeepSeekModelPreference | None = None, parent=None,
                 preference_error=False):
        super().__init__(parent)
        if not isinstance(preference_store, DeepSeekModelPreferenceStore):
            raise TypeError('DeepSeek 模型窗口需要 DeepSeekModelPreferenceStore。')
        if (current_preference is not None
                and not isinstance(current_preference, DeepSeekModelPreference)):
            raise TypeError('当前 DeepSeek 模型设置类型无效。')
        self.preference_store = preference_store
        self.current_preference = current_preference
        self.preference_error = bool(preference_error)
        self._models: tuple[DeepSeekModelInfo, ...] = ()
        self.setWindowTitle('DeepSeek 模型与推理强度')
        self.setMinimumWidth(460)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(14)
        layout.addWidget(TitleLabel('DeepSeek 模型与推理强度', self))
        self.disclosure_label = BodyLabel(
            '将使用 DEEPSEEK_API_KEY 读取账户可用模型目录；目录请求只发送 API Key，不发送题目、项目文件或提示词。',
            self,
        )
        self.disclosure_label.setWordWrap(True)
        layout.addWidget(self.disclosure_label)

        form = QFormLayout()
        self.model_combo = ComboBox(self)
        self.model_combo.setObjectName('deepseekModelCombo')
        self.effort_combo = ComboBox(self)
        self.effort_combo.setObjectName('deepseekReasoningEffortCombo')
        form.addRow('模型', self.model_combo)
        form.addRow('推理强度', self.effort_combo)
        layout.addLayout(form)

        self.status_label = BodyLabel('正在读取 DeepSeek 模型目录…', self)
        self.status_label.setObjectName('deepseekModelCatalogStatus')
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)

        actions = QHBoxLayout()
        actions.addStretch(1)
        self.refresh_button = PushButton('重新加载', self)
        self.refresh_button.setObjectName('refreshDeepSeekModelCatalogButton')
        self.save_button = PrimaryPushButton('保存', self)
        self.save_button.setObjectName('saveDeepSeekModelSelectionButton')
        self.cancel_button = PushButton('取消', self)
        self.cancel_button.setObjectName('cancelDeepSeekModelSelectionButton')
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
        self.status_label.setText('正在读取 DeepSeek 模型目录…')
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
        if (not models or any(not isinstance(model, DeepSeekModelInfo) for model in models)
                or len(models) > 256):
            self.set_load_error('DeepSeek 返回的模型目录为空或无效。')
            return
        self._models = models
        self.model_combo.blockSignals(True)
        self.model_combo.clear()
        used_labels = set()
        for model in models:
            label = model.display_name
            if label in used_labels:
                label = f'{label}（{model.id}）'
            used_labels.add(model.display_name)
            self.model_combo.addItem(label, userData=model.id)

        desired_model = (
            self.current_preference.model if self.current_preference is not None
            else DEFAULT_DEEPSEEK_MODEL
        )
        selected_index = next(
            (index for index in range(self.model_combo.count())
             if self.model_combo.itemData(index) == desired_model),
            next((index for index, model in enumerate(models)
                  if model.id == DEFAULT_DEEPSEEK_MODEL), 0),
        )
        self.model_combo.setCurrentIndex(selected_index)
        self.model_combo.blockSignals(False)
        if self.preference_error:
            self.status_label.setText(
                '已保存的本机模型设置文件无效。重新选择并保存会修复它。'
            )
        elif (self.current_preference is not None
              and self.current_preference.model != self.model_combo.currentData()):
            self.status_label.setText(
                '已保存的模型当前不在可用目录中，请重新选择一个可用模型。'
            )
        else:
            self.status_label.setText(
                '目录读取完成。选择会保存到 F 盘应用状态，并用于之后的 DeepSeek 运行。'
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
            self.effort_combo.setEnabled(False)
            self.effort_combo.blockSignals(False)
            return
        self.effort_combo.addItem(_EFFORT_LABELS['none'], userData='none')
        for effort in selected_model.supported_efforts:
            self.effort_combo.addItem(_EFFORT_LABELS.get(effort, effort), userData=effort)
        desired_effort = selected_model.default_effort or DEFAULT_DEEPSEEK_EFFORT
        if (self.current_preference is not None
                and self.current_preference.model == selected_model.id
                and self.current_preference.effort in ('none',) + selected_model.supported_efforts):
            desired_effort = self.current_preference.effort
        for index in range(self.effort_combo.count()):
            if self.effort_combo.itemData(index) == desired_effort:
                self.effort_combo.setCurrentIndex(index)
                break
        self.effort_combo.setEnabled(True)
        self.effort_combo.blockSignals(False)

    def _save_preference(self):
        try:
            preference = DeepSeekModelPreference(
                model=self.model_combo.currentData(),
                effort=self.effort_combo.currentData(),
            )
            self.preference_store.save(preference)
        except (DeepSeekModelPreferenceError, OSError, ValueError, TypeError) as exc:
            self.status_label.setText(f'设置未保存：{str(exc)[:500]}')
            return
        self.current_preference = preference
        self.preference_saved.emit(preference)
        self.accept()
