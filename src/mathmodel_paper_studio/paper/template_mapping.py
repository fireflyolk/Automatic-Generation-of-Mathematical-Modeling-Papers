"""Template-hash-bound, versioned user mappings."""
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
from uuid import uuid4
from collections.abc import Mapping

from mathmodel_paper_studio.bootstrap.paths import validate_managed_path
from mathmodel_paper_studio.paper.template_inspector import TemplateInspection

_SHA256 = re.compile(r'^[0-9a-f]{64}$')
_SECTION_KEY = re.compile(r'^[a-z][a-z0-9_-]{0,63}$')
_MAX_MAPPING_BYTES = 64 * 1024


class TemplateMappingError(ValueError):
    """A mapping is malformed, stale, or points outside confirmed anchors."""


def _strict_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise TemplateMappingError('Mapping JSON contains duplicate keys.')
        result[key] = value
    return result


@dataclass(frozen=True)
class AnchorBinding:
    section_key: str
    anchor_id: str
    kind: str

    def __post_init__(self):
        if not isinstance(self.section_key, str) or not _SECTION_KEY.fullmatch(self.section_key):
            raise TemplateMappingError('Section keys must be short lowercase identifiers.')
        if not isinstance(self.anchor_id, str) or not re.fullmatch(r'(heading|table)-[0-9]{4}-[0-9a-f]{10}', self.anchor_id):
            raise TemplateMappingError('Template anchor ID is invalid.')
        if self.kind not in ('heading', 'table'):
            raise TemplateMappingError('Only inspected heading and table anchors can be mapped.')


@dataclass(frozen=True)
class TemplateMapping:
    template_sha256: str
    bindings: tuple[AnchorBinding, ...]

    def __post_init__(self):
        if not isinstance(self.template_sha256, str) or not _SHA256.fullmatch(self.template_sha256):
            raise TemplateMappingError('Template mapping requires a SHA-256 digest.')
        if not isinstance(self.bindings, (tuple, list)) or not self.bindings:
            raise TemplateMappingError('At least one confirmed template anchor is required.')
        bindings = tuple(self.bindings)
        if any(not isinstance(item, AnchorBinding) for item in bindings):
            raise TemplateMappingError('Template mapping contains an invalid binding.')
        if (len({item.section_key for item in bindings}) != len(bindings)
                or len({item.anchor_id for item in bindings}) != len(bindings)):
            raise TemplateMappingError('Section keys and template anchors must be unique.')
        object.__setattr__(self, 'bindings', tuple(sorted(bindings, key=lambda item: item.section_key)))

    @classmethod
    def from_selection(cls, inspection: TemplateInspection, selections: Mapping[str, str]) -> 'TemplateMapping':
        if not isinstance(inspection, TemplateInspection) or not isinstance(selections, Mapping):
            raise TemplateMappingError('A template inspection and user selection mapping are required.')
        anchors = {item.anchor_id: item for item in inspection.anchors}
        bindings = []
        for section_key, anchor_id in selections.items():
            if not isinstance(anchor_id, str) or anchor_id not in anchors:
                raise TemplateMappingError('Selection references an unknown template anchor.')
            anchor = anchors[anchor_id]
            bindings.append(AnchorBinding(section_key, anchor_id, anchor.kind))
        return cls(inspection.template_sha256, tuple(bindings))

    def validate_for(self, inspection: TemplateInspection) -> None:
        if not isinstance(inspection, TemplateInspection):
            raise TemplateMappingError('Template inspection is invalid.')
        if inspection.template_sha256 != self.template_sha256:
            raise TemplateMappingError('Template file changed; confirm its anchors again.')
        anchors = {item.anchor_id: item for item in inspection.anchors}
        for binding in self.bindings:
            anchor = anchors.get(binding.anchor_id)
            if anchor is None or anchor.kind != binding.kind:
                raise TemplateMappingError('A mapped template anchor is missing or changed.')

    def to_dict(self) -> dict:
        return {
            'schema_version': 1,
            'template_sha256': self.template_sha256,
            'bindings': [
                {'section_key': item.section_key, 'anchor_id': item.anchor_id, 'kind': item.kind}
                for item in self.bindings
            ],
        }

    @classmethod
    def from_dict(cls, value: dict) -> 'TemplateMapping':
        if not isinstance(value, dict) or set(value) != {'schema_version', 'template_sha256', 'bindings'}:
            raise TemplateMappingError('Template mapping fields are invalid.')
        if type(value['schema_version']) is not int or value['schema_version'] != 1:
            raise TemplateMappingError('Template mapping schema version is unsupported.')
        if not isinstance(value['bindings'], list) or len(value['bindings']) > 256:
            raise TemplateMappingError('Template mapping binding list is invalid or too large.')
        bindings = []
        expected = {'section_key', 'anchor_id', 'kind'}
        for item in value['bindings']:
            if not isinstance(item, dict) or set(item) != expected:
                raise TemplateMappingError('Template mapping binding fields are invalid.')
            bindings.append(AnchorBinding(item['section_key'], item['anchor_id'], item['kind']))
        return cls(value['template_sha256'], tuple(bindings))

    def save(self, project_root: Path, *, version: int = 1) -> Path:
        destination = _mapping_path(project_root, version)
        encoded = json.dumps(self.to_dict(), ensure_ascii=False, sort_keys=True,
                             separators=(',', ':'), allow_nan=False).encode('utf-8')
        if len(encoded) > _MAX_MAPPING_BYTES:
            raise TemplateMappingError('Template mapping exceeds the 64 KiB limit.')
        if destination.exists():
            raise FileExistsError(destination)
        temporary = destination.parent / f'.{destination.name}.{uuid4().hex}.tmp'
        try:
            validate_managed_path(temporary)
            with temporary.open('xb') as stream:
                stream.write(encoded)
                stream.flush()
                os.fsync(stream.fileno())
            validate_managed_path(temporary)
            validate_managed_path(destination)
            if destination.exists():
                raise FileExistsError(destination)
            temporary.rename(destination)
            validate_managed_path(destination)
        except BaseException:
            if temporary.exists():
                validate_managed_path(temporary)
                temporary.unlink()
            raise
        return destination

    @classmethod
    def load(cls, project_root: Path, *, version: int = 1) -> 'TemplateMapping':
        path = _mapping_path(project_root, version)
        try:
            validate_managed_path(path)
            if path.is_symlink() or not path.is_file() or path.stat().st_size > _MAX_MAPPING_BYTES:
                raise TemplateMappingError('Template mapping is missing, unsafe, or too large.')
            with path.open('rb') as stream:
                content = stream.read(_MAX_MAPPING_BYTES + 1)
            if len(content) > _MAX_MAPPING_BYTES:
                raise TemplateMappingError('Template mapping exceeds the 64 KiB limit.')
            value = json.loads(content.decode('utf-8'), object_pairs_hook=_strict_object,
                               parse_constant=lambda _: (_ for _ in ()).throw(
                                   TemplateMappingError('Template mapping contains a non-finite value.')))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise TemplateMappingError('Template mapping cannot be read safely.') from exc
        return cls.from_dict(value)


def _mapping_path(project_root: Path, version: int) -> Path:
    if type(version) is not int or not 1 <= version <= 9999:
        raise TemplateMappingError('Template mapping version must be in 1..9999.')
    if not isinstance(project_root, Path):
        raise TemplateMappingError('Project mapping root must be a pathlib.Path.')
    try:
        root = validate_managed_path(project_root)
        mapping_dir = validate_managed_path(root / '70_review')
        if not root.is_dir() or not mapping_dir.is_dir():
            raise TemplateMappingError('Project review directory does not exist.')
        path = validate_managed_path(mapping_dir / f'template-mapping-v{version:04d}.json')
        if not path.resolve(strict=False).is_relative_to(root.resolve(strict=True)):
            raise TemplateMappingError('Template mapping path escaped the project root.')
        return path
    except (OSError, ValueError) as exc:
        if isinstance(exc, TemplateMappingError):
            raise
        raise TemplateMappingError('Project mapping path is invalid.') from exc