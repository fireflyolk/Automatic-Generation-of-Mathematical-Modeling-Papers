"""Validated model metadata returned by the DeepSeek Models API."""
from dataclasses import dataclass
import json

from mathmodel_paper_studio.agents.deepseek_model_preferences import (
    SUPPORTED_DEEPSEEK_EFFORTS,
    validate_deepseek_model_id,
)


_MAX_CATALOG_BYTES = 512 * 1024
_MAX_MODELS = 256


class DeepSeekModelCatalogError(ValueError):
    """The provider returned a malformed or unsafe model catalog."""


@dataclass(frozen=True)
class DeepSeekModelInfo:
    id: str
    display_name: str
    supported_efforts: tuple[str, ...]
    default_effort: str

    def __post_init__(self):
        try:
            validate_deepseek_model_id(self.id)
        except ValueError as exc:
            raise DeepSeekModelCatalogError('DeepSeek 模型目录含有无效 ID。') from exc
        if (not isinstance(self.display_name, str) or not self.display_name
                or len(self.display_name) > 256 or any(ord(char) < 32 for char in self.display_name)):
            raise DeepSeekModelCatalogError('DeepSeek 模型目录含有无效名称。')
        efforts = self.supported_efforts
        if (not isinstance(efforts, tuple) or len(efforts) > len(SUPPORTED_DEEPSEEK_EFFORTS)
                or len(set(efforts)) != len(efforts)
                or any(effort not in SUPPORTED_DEEPSEEK_EFFORTS[1:] for effort in efforts)):
            raise DeepSeekModelCatalogError('DeepSeek 模型目录含有无效推理强度。')
        if self.default_effort not in (('none',) + efforts):
            raise DeepSeekModelCatalogError('DeepSeek 模型目录的默认推理强度无效。')


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise DeepSeekModelCatalogError('DeepSeek 模型目录含有重复字段。')
        result[key] = value
    return result


def _validate_tree(value, depth=0, budget=None):
    if budget is None:
        budget = [20_000]
    budget[0] -= 1
    if depth > 16 or budget[0] < 0:
        raise DeepSeekModelCatalogError('DeepSeek 模型目录超过安全限制。')
    if type(value) is dict:
        for key, child in value.items():
            if (not isinstance(key, str) or len(key) > 128
                    or any(ord(char) < 32 for char in key)):
                raise DeepSeekModelCatalogError('DeepSeek 模型目录含有无效字段。')
            _validate_tree(child, depth + 1, budget)
    elif type(value) is list:
        if len(value) > 4096:
            raise DeepSeekModelCatalogError('DeepSeek 模型目录超过安全限制。')
        for child in value:
            _validate_tree(child, depth + 1, budget)
    elif isinstance(value, str):
        if '\0' in value or len(value.encode('utf-8')) > _MAX_CATALOG_BYTES:
            raise DeepSeekModelCatalogError('DeepSeek 模型目录含有无效文本。')
    elif value is not None and type(value) not in (bool, int, float):
        raise DeepSeekModelCatalogError('DeepSeek 模型目录含有无效值。')
    elif type(value) is float and (value != value or abs(value) == float('inf')):
        raise DeepSeekModelCatalogError('DeepSeek 模型目录含有无效数值。')


def parse_model_catalog(raw):
    if not isinstance(raw, bytes) or len(raw) > _MAX_CATALOG_BYTES:
        raise DeepSeekModelCatalogError('DeepSeek 模型目录无效或超过大小限制。')
    try:
        payload = json.loads(raw.decode('utf-8', errors='strict'), object_pairs_hook=_unique_object)
        _validate_tree(payload)
        if type(payload) is not dict or payload.get('object') != 'list':
            raise DeepSeekModelCatalogError('DeepSeek 模型目录格式无效。')
        rows = payload.get('data')
        if type(rows) is not list or len(rows) > _MAX_MODELS:
            raise DeepSeekModelCatalogError('DeepSeek 模型目录格式无效。')
        models = []
        seen_ids = set()
        for row in rows:
            if type(row) is not dict:
                raise DeepSeekModelCatalogError('DeepSeek 模型目录格式无效。')
            model_id = row.get('id')
            try:
                validate_deepseek_model_id(model_id)
            except ValueError as exc:
                raise DeepSeekModelCatalogError('DeepSeek 模型目录含有无效 ID。') from exc
            if model_id in seen_ids:
                raise DeepSeekModelCatalogError('DeepSeek 模型目录含有重复模型。')
            seen_ids.add(model_id)
            if row.get('object') != 'model' or row.get('owned_by') != 'deepseek':
                continue
            inputs = row.get('input_modalities')
            outputs = row.get('output_modalities')
            if (type(inputs) is not list or type(outputs) is not list
                    or any(not isinstance(item, str) for item in (*inputs, *outputs))):
                raise DeepSeekModelCatalogError('DeepSeek 模型目录含有无效模态信息。')
            if 'text' not in inputs or 'text' not in outputs:
                continue
            name = row.get('name', model_id)
            effort_info = row.get('effort')
            if effort_info is None:
                supported = ()
                default = 'none'
            else:
                if type(effort_info) is not dict:
                    raise DeepSeekModelCatalogError('DeepSeek 模型目录含有无效推理强度。')
                levels = effort_info.get('supported_levels')
                if type(levels) is not list or any(not isinstance(item, str) for item in levels):
                    raise DeepSeekModelCatalogError('DeepSeek 模型目录含有无效推理强度。')
                supported = tuple(level for level in levels if level in SUPPORTED_DEEPSEEK_EFFORTS[1:])
                if len(set(supported)) != len(supported):
                    raise DeepSeekModelCatalogError('DeepSeek 模型目录含有重复推理强度。')
                if not supported:
                    default = 'none'
                else:
                    requested_default = effort_info.get('default_level')
                    default = requested_default if requested_default in supported else (
                        'high' if 'high' in supported else supported[0]
                    )
            models.append(DeepSeekModelInfo(model_id, name, supported, default))
        return tuple(models)
    except DeepSeekModelCatalogError:
        raise
    except (UnicodeError, json.JSONDecodeError, TypeError, ValueError, RecursionError, OverflowError) as exc:
        raise DeepSeekModelCatalogError('DeepSeek 模型目录格式无效。') from exc
