import os
from pathlib import Path
import tempfile
from unittest.mock import patch

from PySide6.QtWidgets import QApplication

from mathmodel_paper_studio.agents.codex_app_server import CodexModelInfo
from mathmodel_paper_studio.agents.codex_model_preferences import (
    CodexModelPreference,
    CodexModelPreferenceStore,
)
from mathmodel_paper_studio.bootstrap.paths import AppPaths
from mathmodel_paper_studio.ui.codex_model_dialog import CodexModelDialog
from mathmodel_paper_studio.ui.settings_view import SettingsView


ROOT = Path(r'F:\MathModelPaperStudio\tmp\mathmodel-paper-studio-codex-model-dialog-tests')
APP = None


def _models():
    return (
        CodexModelInfo('gpt-5.5', 'GPT 5.5', ('low', 'medium', 'high'), 'medium', True),
        CodexModelInfo('gpt-5.4', 'GPT 5.4', ('low', 'high'), 'low', False),
    )


def _app_paths():
    ROOT.mkdir(parents=True, exist_ok=True)
    temporary = tempfile.TemporaryDirectory(dir=ROOT)
    paths = AppPaths(Path(temporary.name) / 'app-data')
    return temporary, paths


def _application():
    global APP
    if APP is None:
        APP = QApplication.instance() or QApplication([])
    return APP


def test_dialog_chooses_model_specific_effort_and_persists_selection():
    _application()
    temporary, paths = _app_paths()
    try:
        store = CodexModelPreferenceStore(paths)
        dialog = CodexModelDialog(store)
        dialog.set_models(_models())

        assert dialog.model_combo.currentData() is None
        assert not dialog.effort_combo.isEnabled()
        dialog.model_combo.setCurrentIndex(1)
        assert dialog.effort_combo.currentData() == 'medium'
        dialog.effort_combo.setCurrentIndex(2)
        dialog.save_button.click()

        assert store.load() == CodexModelPreference('gpt-5.5', 'high')
        assert dialog.result() == dialog.DialogCode.Accepted
    finally:
        temporary.cleanup()


def test_switching_models_refreshes_effort_choices_and_uses_new_model_default():
    _application()
    temporary, paths = _app_paths()
    try:
        dialog = CodexModelDialog(CodexModelPreferenceStore(paths))
        dialog.set_models(_models())
        dialog.model_combo.setCurrentIndex(2)

        assert [dialog.effort_combo.itemData(index)
                for index in range(dialog.effort_combo.count())] == ['low', 'high']
        assert dialog.effort_combo.currentData() == 'low'
    finally:
        temporary.cleanup()


def test_cancel_does_not_change_previously_saved_selection():
    _application()
    temporary, paths = _app_paths()
    try:
        store = CodexModelPreferenceStore(paths)
        original = CodexModelPreference('gpt-5.4', 'high')
        store.save(original)
        dialog = CodexModelDialog(store, original)
        dialog.set_models(_models())
        dialog.model_combo.setCurrentIndex(1)
        dialog.reject()

        assert store.load() == original
    finally:
        temporary.cleanup()


def test_default_choice_clears_custom_override():
    _application()
    temporary, paths = _app_paths()
    try:
        store = CodexModelPreferenceStore(paths)
        store.save(CodexModelPreference('gpt-5.5', 'high'))
        dialog = CodexModelDialog(store, store.load())
        dialog.set_models(_models())
        dialog.model_combo.setCurrentIndex(0)
        dialog.save_button.click()

        assert store.load() is None
    finally:
        temporary.cleanup()


def test_settings_exposes_codex_model_dialog_action_without_changing_provider():
    _application()
    temporary, paths = _app_paths()
    try:
        with patch.dict(os.environ, {'DEEPSEEK_API_KEY': ''}):
            view = SettingsView(paths)
        try:
            emitted = []
            view.codex_model_settings_requested.connect(lambda: emitted.append(True))
            view.codex_model_button.click()

            assert emitted == [True]
            assert view.codex_model_summary.text().startswith('当前沿用 Codex CLI 默认')
        finally:
            view.close()
    finally:
        temporary.cleanup()
