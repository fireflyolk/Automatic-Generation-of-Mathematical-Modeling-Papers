"""F:-only persistence for the selected DeepSeek model, effort, and output ceiling."""
from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
from uuid import uuid4

from mathmodel_paper_studio.bootstrap.paths import AppPaths, validate_managed_path


DEFAULT_DEEPSEEK_MODEL = 'deepseek-flash'
DEFAULT_DEEPSEEK_EFFORT = 'high'
DEFAULT_DEEPSEEK_MAX_OUTPUT_TOKENS = 8192
APPLICATION_MAX_OUTPUT_TOKENS = 131_072
SUPPORTED_DEEPSEEK_EFFORTS = ('none', 'low', 'high', 'max')
_MAX_PREFERENCE_BYTES = 4096
_MODEL_ID = re.compile(r'^[A-Za-z0-9][A-Za-z0-9._:/-]{0,199}$')


class DeepSeekModelPreferenceError(ValueError):
    """Saved DeepSeek model settings are invalid or cannot be stored safely."""


def validate_deepseek_model_id(value):
    if not isinstance(value, str) or not _MODEL_ID.fullmatch(value):
        raise DeepSeekModelPreferenceError('DeepSeek 模型 ID 无效。')
    return value


@dataclass(frozen=True)
class DeepSeekModelPreference:
    model: str
    effort: str
    # Model-catalog ceiling after the application-level clamp.
    max_output_tokens: int = DEFAULT_DEEPSEEK_MAX_OUTPUT_TOKENS

    def __post_init__(self):
        validate_deepseek_model_id(self.model)
        if self.effort not in SUPPORTED_DEEPSEEK_EFFORTS:
            raise DeepSeekModelPreferenceError('DeepSeek 推理强度无效。')
        if (type(self.max_output_tokens) is not int
                or not 1 <= self.max_output_tokens <= APPLICATION_MAX_OUTPUT_TOKENS):
            raise DeepSeekModelPreferenceError('DeepSeek 输出 token 上限无效。')


def deepseek_output_token_limits(preference: DeepSeekModelPreference) -> tuple[int, int]:
    """Bound provider-recommended reasoning budgets by the reviewed model ceiling.

    Chat Completions defaults: non-thinking 8K, thinking 64K, max effort 128K.
    A length retry can double the initial budget once, within the same consent.
    """
    if not isinstance(preference, DeepSeekModelPreference):
        raise TypeError('DeepSeek 生成预算需要已验证的模型设置。')
    recommended = (DEFAULT_DEEPSEEK_MAX_OUTPUT_TOKENS if preference.effort == 'none'
                   else 131_072 if preference.effort == 'max' else 65_536)
    ceiling = min(preference.max_output_tokens, APPLICATION_MAX_OUTPUT_TOKENS)
    initial = min(recommended, ceiling)
    return initial, min(initial * 2, ceiling)


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise DeepSeekModelPreferenceError('DeepSeek 模型设置含有重复字段。')
        result[key] = value
    return result


class DeepSeekModelPreferenceStore:
    """Atomically stores model selection and the catalog output ceiling under F: state."""

    def __init__(self, paths: AppPaths):
        if not isinstance(paths, AppPaths):
            raise TypeError('DeepSeek 模型设置需要经过路径校验的 AppPaths。')
        self.paths = paths
        self.path = validate_managed_path(paths.state / 'deepseek-model-preferences.json')

    def load(self) -> DeepSeekModelPreference | None:
        path = validate_managed_path(self.path)
        try:
            if not path.exists():
                return None
            if not path.is_file():
                raise DeepSeekModelPreferenceError('DeepSeek 模型设置路径不是普通文件。')
            with path.open('rb') as stream:
                raw = stream.read(_MAX_PREFERENCE_BYTES + 1)
        except DeepSeekModelPreferenceError:
            raise
        except OSError as exc:
            raise DeepSeekModelPreferenceError('无法读取 F: 上的 DeepSeek 模型设置。') from exc
        if len(raw) > _MAX_PREFERENCE_BYTES:
            raise DeepSeekModelPreferenceError('DeepSeek 模型设置文件超过大小限制。')
        try:
            payload = json.loads(raw.decode('utf-8'), object_pairs_hook=_unique_object)
        except (UnicodeError, json.JSONDecodeError, RecursionError) as exc:
            raise DeepSeekModelPreferenceError('DeepSeek 模型设置文件格式无效。') from exc
        if type(payload) is not dict:
            raise DeepSeekModelPreferenceError('DeepSeek 模型设置文件版本或字段无效。')
        schema = payload.get('schema')
        expected_fields = (
            {'schema', 'model', 'effort'} if schema == 1 else
            {'schema', 'model', 'effort', 'max_output_tokens'} if schema == 2 else None
        )
        if expected_fields is None or set(payload) != expected_fields:
            raise DeepSeekModelPreferenceError('DeepSeek 模型设置文件版本或字段无效。')
        try:
            return DeepSeekModelPreference(
                model=payload['model'], effort=payload['effort'],
                max_output_tokens=payload.get(
                    'max_output_tokens', DEFAULT_DEEPSEEK_MAX_OUTPUT_TOKENS),
            )
        except (KeyError, TypeError, DeepSeekModelPreferenceError) as exc:
            raise DeepSeekModelPreferenceError('DeepSeek 模型或推理强度设置无效。') from exc

    def save(self, preference: DeepSeekModelPreference) -> None:
        if not isinstance(preference, DeepSeekModelPreference):
            raise TypeError('DeepSeek 模型设置类型无效。')
        self.paths.ensure_directories()
        path = validate_managed_path(self.path)
        payload = json.dumps(
            {'schema': 2, 'model': preference.model, 'effort': preference.effort,
             'max_output_tokens': preference.max_output_tokens},
            ensure_ascii=False, separators=(',', ':'), allow_nan=False,
        ).encode('utf-8')
        if len(payload) > _MAX_PREFERENCE_BYTES:
            raise DeepSeekModelPreferenceError('DeepSeek 模型设置超过大小限制。')
        temporary = validate_managed_path(path.with_name(f'.{path.name}.{uuid4().hex}.tmp'))
        try:
            with temporary.open('xb') as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            validate_managed_path(temporary)
            os.replace(temporary, path)
            validate_managed_path(path)
        except (OSError, ValueError) as exc:
            raise DeepSeekModelPreferenceError('无法安全保存 F: 上的 DeepSeek 模型设置。') from exc
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
            raise DeepSeekModelPreferenceError('无法清除 F: 上的 DeepSeek 模型设置。') from exc
