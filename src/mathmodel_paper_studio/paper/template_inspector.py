"""Read-only inspection of safe, editable WordprocessingML templates."""
from dataclasses import dataclass
from io import BytesIO
import hashlib
import posixpath
from pathlib import Path
import re
from zipfile import BadZipFile, ZipFile

from defusedxml import ElementTree as SafeET
from defusedxml.common import DefusedXmlException

from mathmodel_paper_studio.bootstrap.paths import validate_import_source_path

_W = '{http://schemas.openxmlformats.org/wordprocessingml/2006/main}'
_R = '{http://schemas.openxmlformats.org/officeDocument/2006/relationships}'
_PKG_REL = '{http://schemas.openxmlformats.org/package/2006/relationships}'
_CT = '{http://schemas.openxmlformats.org/package/2006/content-types}'
_A = '{http://schemas.openxmlformats.org/drawingml/2006/main}'
_MAX_PACKAGE_BYTES = 64 * 1024 * 1024
_MAX_MEMBER_BYTES = 16 * 1024 * 1024
_MAX_MEMBERS = 4096


class TemplateInspectionError(ValueError):
    """The package is invalid or cannot be inspected without ambiguity."""


class UnsupportedTemplateError(TemplateInspectionError):
    """The template contains a structure the writer cannot safely preserve."""


@dataclass(frozen=True)
class PageSetup:
    width_twips: int | None
    height_twips: int | None
    top_twips: int | None
    right_twips: int | None
    bottom_twips: int | None
    left_twips: int | None


@dataclass(frozen=True)
class TemplateAnchor:
    anchor_id: str
    kind: str
    label: str
    style_id: str | None
    index: int
    bookmark_names: tuple[str, ...] = ()


@dataclass(frozen=True)
class TemplateInspection:
    template_sha256: str
    size_bytes: int
    anchors: tuple[TemplateAnchor, ...]
    page_setup: PageSetup
    image_count: int
    header_parts: tuple[str, ...]
    footer_parts: tuple[str, ...]
    part_hashes: tuple[tuple[str, str], ...]

    def anchor(self, anchor_id: str) -> TemplateAnchor | None:
        return next((item for item in self.anchors if item.anchor_id == anchor_id), None)


def _parse_xml(content: bytes, name: str):
    try:
        return SafeET.fromstring(content, forbid_dtd=True, forbid_entities=True, forbid_external=True)
    except (DefusedXmlException, ValueError, TypeError) as exc:
        raise TemplateInspectionError(f'DOCX XML part is invalid: {name}') from exc


def _integer(element, attribute: str) -> int | None:
    if element is None:
        return None
    value = element.get(_W + attribute)
    if value is None:
        return None
    if re.fullmatch(r'-?[0-9]{1,8}', value) is None:
        raise TemplateInspectionError('DOCX page setup contains an invalid measurement.')
    return int(value)


def _part_path(source_part: str, target: str) -> str:
    if not target or '\\' in target or ':' in target:
        raise TemplateInspectionError('DOCX relationship target is invalid.')
    base = posixpath.dirname(source_part)
    normalized = posixpath.normpath(target.lstrip('/') if target.startswith('/')
                                    else posixpath.join(base, target))
    if normalized in ('', '.') or normalized == '..' or normalized.startswith('../'):
        raise TemplateInspectionError('DOCX relationship escapes the package root.')
    return normalized


def _relationship_source(rels_part: str) -> str:
    if rels_part == '_rels/.rels':
        return ''
    pieces = rels_part.split('/')
    if len(pieces) < 2 or pieces[-2] != '_rels' or not pieces[-1].endswith('.rels'):
        raise TemplateInspectionError('DOCX relationship part has an invalid package path.')
    source_name = pieces[-1][:-5]
    return posixpath.join(*pieces[:-2], source_name)


def _is_embedded_relationship(rel_type: str) -> bool:
    # Relationship type URIs for embedded objects end in one of these exact
    # names. Substring checks also match the standard core-properties URI,
    # whose namespace path contains the word "package".
    final_segment = rel_type.casefold().rstrip('/').rsplit('/', 1)[-1]
    return final_segment in {'oleobject', 'package', 'control'}


def _relationships(root, source_part: str, members: set[str]):
    if root.tag != _PKG_REL + 'Relationships':
        raise TemplateInspectionError('DOCX relationship part is malformed.')
    result = {}
    if root is None:
        return result
    for item in root.findall(_PKG_REL + 'Relationship'):
        rel_id = item.get('Id')
        rel_type = item.get('Type', '')
        target = item.get('Target', '')
        mode = item.get('TargetMode', 'Internal').casefold()
        if not rel_id or rel_id in result or not rel_type or not target:
            raise TemplateInspectionError('DOCX relationship IDs or fields are invalid.')
        if mode == 'external':
            if _is_embedded_relationship(rel_type):
                raise UnsupportedTemplateError('DOCX contains an external embedded object.')
            result[rel_id] = (rel_type, None)
            continue
        resolved = _part_path(source_part, target)
        if resolved not in members:
            raise TemplateInspectionError(f'DOCX relationship target is missing: {resolved}')
        if _is_embedded_relationship(rel_type):
            raise UnsupportedTemplateError('DOCX contains an embedded object.')
        result[rel_id] = (rel_type, resolved)
    return result


def _paragraph_text(element) -> str:
    return ''.join(node.text or '' for node in element.iter(_W + 't')).strip()


def _anchor_id(kind: str, index: int, label: str, template_sha256: str) -> str:
    seed = f'{template_sha256}:{kind}:{index}:{label}'.encode('utf-8')
    return f'{kind}-{index:04d}-{hashlib.sha256(seed).hexdigest()[:10]}'


def inspect_template(path: Path) -> TemplateInspection:
    """Inspect a user-selected local DOCX read-only without extracting or modifying it."""
    if not isinstance(path, Path):
        raise TypeError('Template path must be a pathlib.Path.')
    try:
        path = validate_import_source_path(path)
        if not path.is_file():
            raise TemplateInspectionError('DOCX template is not a regular file.')
        size_bytes = path.stat().st_size
        if not 0 < size_bytes <= _MAX_PACKAGE_BYTES:
            raise TemplateInspectionError('DOCX package is empty or exceeds the 64 MiB limit.')
        with path.open('rb') as stream:
            content = stream.read(_MAX_PACKAGE_BYTES + 1)
    except (OSError, ValueError) as exc:
        if isinstance(exc, TemplateInspectionError):
            raise
        raise TemplateInspectionError('DOCX template cannot be read safely.') from exc
    if not content or len(content) > _MAX_PACKAGE_BYTES:
        raise TemplateInspectionError('DOCX package is empty or exceeds the 64 MiB limit.')
    template_sha256 = hashlib.sha256(content).hexdigest()
    try:
        with ZipFile(BytesIO(content)) as archive:
            infos = archive.infolist()
            if not infos or len(infos) > _MAX_MEMBERS:
                raise TemplateInspectionError('DOCX package member count is empty or exceeds the limit.')
            names = set()
            parts = {}
            total = 0
            for info in infos:
                name = info.filename
                pieces = name.split('/')
                if (not name or name.startswith('/') or '\\' in name or ':' in name
                        or '..' in pieces or name.casefold() in names or info.flag_bits & 1):
                    raise TemplateInspectionError('DOCX package contains an unsafe, duplicate, or encrypted member.')
                names.add(name.casefold())
                total += info.file_size
                if info.file_size > _MAX_MEMBER_BYTES or total > _MAX_PACKAGE_BYTES:
                    raise TemplateInspectionError('DOCX package expands beyond the bounded read limit.')
                try:
                    data = archive.read(info)
                except (OSError, BadZipFile, RuntimeError) as exc:
                    raise TemplateInspectionError('DOCX package member cannot be read.') from exc
                if len(data) != info.file_size:
                    raise TemplateInspectionError('DOCX package member size changed during reading.')
                parts[name] = data
    except (BadZipFile, OSError, ValueError) as exc:
        if isinstance(exc, TemplateInspectionError):
            raise
        raise TemplateInspectionError('DOCX is not a valid ZIP package.') from exc

    required = {'[Content_Types].xml', '_rels/.rels', 'word/document.xml',
                'word/_rels/document.xml.rels'}
    if not required.issubset(parts):
        raise TemplateInspectionError('DOCX package is missing required Word parts.')
    roots = {name: _parse_xml(data, name) for name, data in parts.items()
             if name.casefold().endswith(('.xml', '.rels'))}
    names = set(parts)
    content_types = roots['[Content_Types].xml']
    if content_types.tag != _CT + 'Types':
        raise TemplateInspectionError('DOCX content type manifest is invalid.')

    relationship_maps = {
        name: _relationships(root, _relationship_source(name), names)
        for name, root in roots.items() if name.casefold().endswith('.rels')
    }
    root_rels = relationship_maps['_rels/.rels']
    if not any(kind.endswith('/officeDocument') and target == 'word/document.xml'
               for kind, target in root_rels.values()):
        raise TemplateInspectionError('DOCX root relationship does not identify its main document.')
    document_rels = relationship_maps['word/_rels/document.xml.rels']
    document = roots['word/document.xml']
    if document.tag != _W + 'document':
        raise TemplateInspectionError('DOCX main part is not a Word document.')

    for name, root in roots.items():
        if not name.startswith('word/'):
            continue
        for unsupported in (_W + 'txbxContent', _W + 'object', _W + 'altChunk'):
            if next(root.iter(unsupported), None) is not None:
                raise UnsupportedTemplateError('Template contains a text box, embedded object, or altChunk.')
    if any(name.casefold().startswith(('word/embeddings/', 'word/activex/')) for name in names):
        raise UnsupportedTemplateError('Template contains embedded or ActiveX content.')

    body = document.find(_W + 'body')
    if body is None:
        raise TemplateInspectionError('DOCX main document has no body.')
    styles = roots.get('word/styles.xml')
    style_names = {}
    if styles is not None:
        for style in styles.findall(_W + 'style'):
            style_id = style.get(_W + 'styleId')
            name = style.find(_W + 'name')
            if style_id:
                style_names[style_id] = '' if name is None else name.get(_W + 'val', '')

    template_anchors = []
    paragraph_index = 0
    table_index = 0
    for element in body.iter():
        if element.tag == _W + 'p':
            ppr = element.find(_W + 'pPr')
            style_node = None if ppr is None else ppr.find(_W + 'pStyle')
            style_id = None if style_node is None else style_node.get(_W + 'val')
            outline = None if ppr is None else ppr.find(_W + 'outlineLvl')
            style_name = style_names.get(style_id or '', '')
            is_heading = ((style_id or '').casefold().startswith('heading')
                          or style_name.casefold().startswith('heading') or outline is not None)
            label = _paragraph_text(element)
            if is_heading and label:
                bookmarks = tuple(node.get(_W + 'name', '') for node in element.iter(_W + 'bookmarkStart')
                                  if node.get(_W + 'name') and node.get(_W + 'name') != '_GoBack')
                template_anchors.append(TemplateAnchor(
                    _anchor_id('heading', paragraph_index, label, template_sha256),
                    'heading', label, style_id, paragraph_index, bookmarks,
                ))
            paragraph_index += 1
        elif element.tag == _W + 'tbl':
            caption = element.find(f'{_W}tblPr/{_W}tblCaption')
            label = '' if caption is None else caption.get(_W + 'val', '')
            if not label:
                label = ' | '.join(_paragraph_text(cell) for cell in element.findall(f'{_W}tr[1]/{_W}tc'))
            template_anchors.append(TemplateAnchor(
                _anchor_id('table', table_index, label, template_sha256),
                'table', label or f'Table {table_index + 1}', None, table_index, (),
            ))
            table_index += 1

    sect_pr = body.find(_W + 'sectPr')
    if sect_pr is None:
        sect_pr = next(body.iter(_W + 'sectPr'), None)
    page_size = None if sect_pr is None else sect_pr.find(_W + 'pgSz')
    page_margins = None if sect_pr is None else sect_pr.find(_W + 'pgMar')
    page_setup = PageSetup(
        _integer(page_size, 'w'), _integer(page_size, 'h'),
        _integer(page_margins, 'top'), _integer(page_margins, 'right'),
        _integer(page_margins, 'bottom'), _integer(page_margins, 'left'),
    )

    image_count = 0
    for blip in document.iter(_A + 'blip'):
        rel_id = blip.get(_R + 'embed')
        if not rel_id or rel_id not in document_rels:
            raise TemplateInspectionError('DOCX drawing references a missing image relationship.')
        rel_type, target = document_rels[rel_id]
        if not rel_type.endswith('/image') or target is None:
            raise TemplateInspectionError('DOCX drawing relationship is not an internal image part.')
        image_count += 1
    headers = tuple(sorted(target for kind, target in document_rels.values()
                           if kind.endswith('/header') and target is not None))
    footers = tuple(sorted(target for kind, target in document_rels.values()
                            if kind.endswith('/footer') and target is not None))
    return TemplateInspection(
        template_sha256, len(content), tuple(template_anchors), page_setup, image_count,
        headers, footers,
        tuple((name, hashlib.sha256(data).hexdigest()) for name, data in sorted(parts.items())),
    )
