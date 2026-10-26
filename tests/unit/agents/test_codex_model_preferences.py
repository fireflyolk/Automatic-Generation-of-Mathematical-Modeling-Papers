import json
from pathlib import Path
import tempfile

import pytest

from mathmodel_paper_studio.agents.codex_model_preferences import (
    CodexModelPreference,
    CodexModelPreferenceError,
    CodexModelPreferenceStore,
)
from mathmodel_paper_studio.bootstrap.paths import AppPaths, PathPolicyError


ROOT = Path(r'F:\MathModelPaperStudio\tmp\mathmodel-paper-studio-codex-model-preferences-tests')


def _temporary_paths():
    ROOT.mkdir(parents=True, exist_ok=True)
    temporary = tempfile.TemporaryDirectory(dir=ROOT)
    paths = AppPaths(Path(temporary.name) / 'app-data')
    return temporary, paths


def test_preference_store_round_trips_under_app_state_and_can_clear():
    temporary, paths = _temporary_paths()
    try:
        store = CodexModelPreferenceStore(paths)
        preference = CodexModelPreference(model='gpt-5.5', effort='high')

        store.save(preference)

        preference_path = paths.state / 'codex-model-preferences.json'
        assert preference_path.is_file()
        assert preference_path.drive.upper() == 'F:'
        assert preference_path.parent == paths.state
        assert store.load() == preference

        store.clear()

        assert not preference_path.exists()
        assert store.load() is None
    finally:
        temporary.cleanup()


@pytest.mark.parametrize('raw', [
    b'{not-json',
    json.dumps([]).encode('utf-8'),
    json.dumps({'model': '', 'effort': 'high'}).encode('utf-8'),
    json.dumps({'model': 'gpt-5.5', 'effort': ''}).encode('utf-8'),
    json.dumps({'model': 7, 'effort': 'high'}).encode('utf-8'),
    json.dumps({'model': 'gpt-5.5'}).encode('utf-8'),
    json.dumps({'schema': 1, 'model': 'gpt-5.5', 'effort': 'high'}).encode('utf-8')
    + (b' ' * 5000),
])
def test_corrupt_or_invalid_preference_raises_and_preserves_original_file(raw):
    temporary, paths = _temporary_paths()
    try:
        store = CodexModelPreferenceStore(paths)
        preference_path = paths.state / 'codex-model-preferences.json'
        paths.state.mkdir(parents=True)
        preference_path.write_bytes(raw)

        with pytest.raises(CodexModelPreferenceError):
            store.load()

        assert preference_path.read_bytes() == raw
    finally:
        temporary.cleanup()


def test_app_paths_reject_non_f_storage_before_preference_store_can_write():
    with pytest.raises(PathPolicyError):
        AppPaths(Path(r'C:\Codex另一个工作的地方\app-data'))
