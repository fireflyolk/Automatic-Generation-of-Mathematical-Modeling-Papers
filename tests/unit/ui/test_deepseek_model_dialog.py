from pathlib import Path
import tempfile
from unittest.mock import patch

from PySide6.QtWidgets import QApplication

from mathmodel_paper_studio.agents.deepseek_model_catalog import DeepSeekModelInfo
from mathmodel_paper_studio.agents.deepseek_model_preferences import (
    DeepSeekModelPreference,
    DeepSeekModelPreferenceStore,
)
from mathmodel_paper_studio.bootstrap.paths import AppPaths
from mathmodel_paper_studio.ui.deepseek_model_dialog import DeepSeekModelDialog
from mathmodel_paper_studio.ui.settings_view import SettingsView


ROOT = Path(r'F:\MathModelPaperStudio\tmp\mathmodel-paper-studio-deepseek-model-dialog-tests')
APP = None


def _application():
    global APP
    if APP is None:
        APP = QApplication.instance() or QApplication([])
    return APP


def _models():
    return (
        DeepSeekModelInfo('deepseek-flash', 'DeepSeek Flash', ('low', 'high', 'max'), 'high',
                          max_output_tokens=12_288),
        DeepSeekModelInfo('deepseek-v4-pro', 'DeepSeek Pro', ('low', 'high', 'max'), 'high',
                          max_output_tokens=16_384),
    )


def _paths():
    ROOT.mkdir(parents=True, exist_ok=True)
    temporary = tempfile.TemporaryDirectory(dir=ROOT)
    return temporary, AppPaths(Path(temporary.name) / 'app-data')


def test_dialog_selects_model_and_effort_and_persists_choice():
    _application()
    temporary, paths = _paths()
    try:
        store = DeepSeekModelPreferenceStore(paths)
        dialog = DeepSeekModelDialog(store)
        dialog.set_models(_models())

        assert dialog.model_combo.currentData() == 'deepseek-flash'
        assert [dialog.effort_combo.itemData(index)
                for index in range(dialog.effort_combo.count())] == ['none', 'low', 'high', 'max']
        assert dialog.effort_combo.currentData() == 'high'
        dialog.model_combo.setCurrentIndex(1)
        dialog.effort_combo.setCurrentIndex(3)
        dialog.save_button.click()

        assert store.load() == DeepSeekModelPreference(
            'deepseek-v4-pro', 'max', max_output_tokens=16_384)
        assert dialog.result() == dialog.DialogCode.Accepted
    finally:
        temporary.cleanup()


def test_dialog_can_disable_thinking_and_does_not_persist_until_save():
    _application()
    temporary, paths = _paths()
    try:
        store = DeepSeekModelPreferenceStore(paths)
        original = DeepSeekModelPreference('deepseek-flash', 'high')
        store.save(original)
        dialog = DeepSeekModelDialog(store, original)
        dialog.set_models(_models())
        dialog.effort_combo.setCurrentIndex(0)
        dialog.reject()

        assert store.load() == original
    finally:
        temporary.cleanup()


def test_settings_exposes_deepseek_model_picker_without_changing_provider():
    _application()
    temporary, paths = _paths()
    try:
        store = DeepSeekModelPreferenceStore(paths)
        with patch.dict('os.environ', {'DEEPSEEK_API_KEY': ''}):
            view = SettingsView(paths, deepseek_model_preferences=store)
        try:
            emitted = []
            view.deepseek_model_settings_requested.connect(lambda: emitted.append(True))
            view.deepseek_model_button.click()

            assert emitted == [True]
            assert view.deepseek_model_summary.text().startswith('当前使用 DeepSeek 默认')
            assert view.selected_provider.value == 'codex'
        finally:
            view.close()
    finally:
        temporary.cleanup()
