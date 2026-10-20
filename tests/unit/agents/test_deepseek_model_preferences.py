import json
from pathlib import Path
import tempfile

import pytest

from mathmodel_paper_studio.agents.deepseek_model_preferences import (
    DEFAULT_DEEPSEEK_MAX_OUTPUT_TOKENS,
    DeepSeekModelPreference,
    DeepSeekModelPreferenceError,
    DeepSeekModelPreferenceStore,
)
from mathmodel_paper_studio.bootstrap.paths import AppPaths


ROOT = Path(r'F:\Codex另一个工作的地方\tmp\mathmodel-paper-studio-deepseek-preferences-tests')


def _store():
    ROOT.mkdir(parents=True, exist_ok=True)
    temporary = tempfile.TemporaryDirectory(dir=ROOT)
    return temporary, DeepSeekModelPreferenceStore(AppPaths(Path(temporary.name) / 'app-data'))


def test_preference_is_immutable_and_accepts_supported_deepseek_efforts():
    preference = DeepSeekModelPreference('deepseek-flash', 'high')

    assert preference.model == 'deepseek-flash'
    assert preference.effort == 'high'
    with pytest.raises(AttributeError):
        preference.model = 'deepseek-v4-pro'
    with pytest.raises(DeepSeekModelPreferenceError):
        DeepSeekModelPreference('../outside', 'high')
    with pytest.raises(DeepSeekModelPreferenceError):
        DeepSeekModelPreference('deepseek-flash', 'xhigh')


def test_store_round_trips_model_effort_and_output_limit_under_managed_f_state():
    temporary, store = _store()
    try:
        preference = DeepSeekModelPreference('deepseek-v4-pro', 'max')
        store.save(preference)

        assert store.load() == preference
        assert store.path.is_relative_to(store.paths.state)
        assert json.loads(store.path.read_text(encoding='utf-8')) == {
            'schema': 2, 'model': 'deepseek-v4-pro', 'effort': 'max', 'max_output_tokens': 8192,
        }
    finally:
        temporary.cleanup()


def test_legacy_preferences_without_catalog_ceiling_use_conservative_output_limit():
    temporary, store = _store()
    try:
        store.paths.ensure_directories()
        store.path.write_text(json.dumps({
            'schema': 1, 'model': 'deepseek-flash', 'effort': 'max',
        }), encoding='utf-8')

        preference = store.load()

        assert preference.max_output_tokens == DEFAULT_DEEPSEEK_MAX_OUTPUT_TOKENS
    finally:
        temporary.cleanup()


def test_store_rejects_malformed_or_extra_fields_without_using_them():
    temporary, store = _store()
    try:
        store.paths.ensure_directories()
        store.path.write_text(json.dumps({
            'schema': 1, 'model': 'deepseek-flash', 'effort': 'high', 'api_key': 'must-not-load',
        }), encoding='utf-8')

        with pytest.raises(DeepSeekModelPreferenceError):
            store.load()
    finally:
        temporary.cleanup()
