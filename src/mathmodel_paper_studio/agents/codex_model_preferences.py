"""F:-only persistence for the selected Codex model and reasoning effort."""
from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
from uuid import uuid4

from mathmodel_paper_studio.bootstrap.paths import AppPaths, validate_managed_path


_MAX_PREFERENCE_BYTES = 4096
_MODEL_ID = re.compile(r'^[A-Za-z0-9][A-Za-z0-9._:/-]{0,199}$')
_EFFORT_ID = re.compile(r'^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$')


class CodexModelPreferenceError(ValueError):
    """Saved Codex model settings are invalid or cannot be stored safely."""


def validate_codex_model_id(value):
    if not isinstance(value, str) or not _MODEL_ID.fullmatch(value):
        raise CodexModelPreferenceError('Codex 模型 ID 无效。')
    return value


def validate_codex_effort(value):
    if not isinstance(value, str) or not _EFFORT_ID.fullmatch(value):
        raise CodexModelPreferenceError('Codex 推理强度 ID 无效。')
    return value


@dataclass(frozen=True)
class CodexModelPreference:
    model: str
    effort: str

    def __post_init__(self):
        validate_codex_model_id(self.model)
        validate_codex_effort(self.effort)


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise CodexModelPreferenceError('Codex 模型设置含有重复字段。')
        result[key] = value
    return result


class CodexModelPreferenceStore:
    """Atomically stores only model IDs and effort IDs under app-owned F: state."""

    def __init__(self, paths: AppPaths):
        if not isinstance(paths, AppPaths):
            raise TypeError('Codex 模型设置需要经过路径校验的 AppPaths。')
        self.paths = paths
        self.path = validate_managed_path(paths.state / 'codex-model-preferences.json')

    def load(self) -> CodexModelPreference | None:
        path = validate_managed_path(self.path)
        try:
            if not path.exists():
                return None
            if not path.is_file():
                raise CodexModelPreferenceError('Codex 模型设置路径不是普通文件。')
            with path.open('rb') as stream:
                raw = stream.read(_MAX_PREFERENCE_BYTES + 1)
        except CodexModelPreferenceError:
            raise
        except OSError as exc:
            raise CodexModelPreferenceError('无法读取 F: 上的 Codex 模型设置。') from exc
        if len(raw) > _MAX_PREFERENCE_BYTES:
            raise CodexModelPreferenceError('Codex 模型设置文件超过大小限制。')
        try:
            payload = json.loads(raw.decode('utf-8'), object_pairs_hook=_unique_object)
        except (UnicodeError, json.JSONDecodeError, RecursionError) as exc:
            raise CodexModelPreferenceError('Codex 模型设置文件格式无效。') from exc
        if (type(payload) is not dict or set(payload) != {'schema', 'model', 'effort'}
                or payload.get('schema') != 1):
            raise CodexModelPreferenceError('Codex 模型设置文件版本或字段无效。')
        try:
            return CodexModelPreference(model=payload['model'], effort=payload['effort'])
        except (KeyError, TypeError, CodexModelPreferenceError) as exc:
            raise CodexModelPreferenceError('Codex 模型或推理强度设置无效。') from exc

    def save(self, preference: CodexModelPreference) -> None:
        if not isinstance(preference, CodexModelPreference):
            raise TypeError('Codex 模型设置类型无效。')
        self.paths.ensure_directories()
        path = validate_managed_path(self.path)
        payload = json.dumps(
            {'schema': 1, 'model': preference.model, 'effort': preference.effort},
            ensure_ascii=False, separators=(',', ':'), allow_nan=False,
        ).encode('utf-8')
        if len(payload) > _MAX_PREFERENCE_BYTES:
            raise CodexModelPreferenceError('Codex 模型设置超过大小限制。')
        temporary = validate_managed_path(
            path.with_name(f'.{path.name}.{uuid4().hex}.tmp')
        )
        try:
            with temporary.open('xb') as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            validate_managed_path(temporary)
            os.replace(temporary, path)
            validate_managed_path(path)
        except (OSError, ValueError) as exc:
            raise CodexModelPreferenceError('无法安全保存 F: 上的 Codex 模型设置。') from exc
        finally:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass

    def clear(self) -> None:
        path = validate_managed_path(self.path)
        try:
            path.unlink(missing_ok=True)
        except OSError as exc:
            raise CodexModelPreferenceError('无法清除 F: 上的 Codex 模型设置。') from exc
