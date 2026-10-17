"""Versioned OOXML writer that changes only explicitly mapped template anchors."""
from copy import deepcopy
from dataclasses import dataclass
from io import BytesIO
import hashlib
import os
from pathlib import Path
import re
from uuid import uuid4
from zipfile import BadZipFile, ZIP_DEFLATED, ZipFile
import xml.etree.ElementTree as ET

from defusedxml import ElementTree as SafeET
from defusedxml.common import DefusedXmlException

from mathmodel_paper_studio.bootstrap.paths import validate_managed_path
from mathmodel_paper_studio.paper.template_inspector import TemplateInspection, inspect_template
from mathmodel_paper_studio.paper.template_mapping import TemplateMapping, TemplateMappingError

_W = '{http://schemas.openxmlformats.org/wordprocessingml/2006/main}'
_R = '{http://schemas.openxmlformats.org/officeDocument/2006/relationships}'
_PKG_REL = '{http://schemas.openxmlformats.org/package/2006/relationships}'
_CT = '{http://schemas.openxmlformats.org/package/2006/content-types}'
_WP = '{http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing}'
_A = '{http://schemas.openxmlformats.org/drawingml/2006/main}'
_PIC = '{http://schemas.openxmlformats.org/drawingml/2006/picture}'
_XML_SPACE = '{http://www.w3.org/XML/1998/namespace}space'
_IMAGE_REL = 'http://schemas.openxmlformats.org/officeDocument/2006/relationships/image'
_MAX_PACKAGE_BYTES = 64 * 1024 * 1024
_MAX_IMAGE_BYTES = 16 * 1024 * 1024
_MAX_TABLE_ROWS = 10000
_MAX_TEXT_CHARS = 100000
_EVIDENCE_ID = re.compile(r'^[a-z][a-z0-9_-]{2,63}$')

for _prefix, _uri in (
    ('w', _W[1:-1]), ('r', _R[1:-1]),
    ('wp', _WP[1:-1]), ('a', _A[1:-1]), ('pic', _PIC[1:-1]),
    ('pr', 'http://schemas.openxmlformats.org/package/2006/relationships'),
    ('ct', 'http://schemas.openxmlformats.org/package/2006/content-types'),
):
    ET.register_namespace(_prefix, _uri)


class DocxWriteError(ValueError):
    """The draft cannot be safely applied to the selected template mapping."""


def _text(value: str, *, allow_empty: bool = False) -> str:
    if (not isinstance(value, str) or len(value) > _MAX_TEXT_CHARS or '\x00' in value
            or (not allow_empty and not value.strip())):
        raise DocxWriteError('Document text is empty, invalid, or exceeds its size limit.')
    return value


def _evidence_ids(values) -> tuple[str, ...]:
    if not isinstance(values, (tuple, list)) or len(values) > 64:
        raise DocxWriteError('Evidence ID collection is invalid or too large.')
    result = tuple(values)
    if any(not isinstance(item, str) or not _EVIDENCE_ID.fullmatch(item) for item in result):
        raise DocxWriteError('Evidence ID is invalid.')
    if len(set(result)) != len(result):
        raise DocxWriteError('Evidence IDs cannot be duplicated.')
    return result


@dataclass(frozen=True)
class TextSection:
    section_key: str
    paragraphs: tuple[str, ...]
    evidence_ids: tuple[str, ...] = ()
    pending_reviews: tuple[str, ...] = ()

    def __post_init__(self):
        if not isinstance(self.paragraphs, (tuple, list)) or not self.paragraphs:
            raise DocxWriteError('Text section requires at least one paragraph.')
        paragraphs = tuple(_text(item) for item in self.paragraphs)
        evidence = _evidence_ids(self.evidence_ids)
        pending = tuple(_text(item) for item in self.pending_reviews)
        if len(pending) > 32:
            raise DocxWriteError('Too many pending review markers.')
        object.__setattr__(self, 'paragraphs', paragraphs)
        object.__setattr__(self, 'evidence_ids', evidence)
        object.__setattr__(self, 'pending_reviews', pending)


@dataclass(frozen=True)
class TableDraft:
    section_key: str
    headers: tuple[str, ...]
    rows: tuple[tuple[str, ...], ...]
    evidence_ids: tuple[str, ...] = ()
    pending_reviews: tuple[str, ...] = ()

    def __post_init__(self):
        headers = tuple(_text(item) for item in self.headers)
        rows = tuple(tuple(_text(cell, allow_empty=True) for cell in row) for row in self.rows)
        if not headers or len(headers) > 128 or len(rows) > _MAX_TABLE_ROWS:
            raise DocxWriteError('Table dimensions are empty or exceed limits.')
        if any(len(row) != len(headers) for row in rows):
            raise DocxWriteError('Table rows must have the same column count as the header.')
        evidence = _evidence_ids(self.evidence_ids)
        pending = tuple(_text(item) for item in self.pending_reviews)
        object.__setattr__(self, 'headers', headers)
        object.__setattr__(self, 'rows', rows)
        object.__setattr__(self, 'evidence_ids', evidence)
        object.__setattr__(self, 'pending_reviews', pending)


@dataclass(frozen=True)
class FigureDraft:
    section_key: str
    image_bytes: bytes
    image_format: str
    caption: str
    evidence_ids: tuple[str, ...] = ()
    pending_reviews: tuple[str, ...] = ()

    def __post_init__(self):
        _text(self.caption)
        if not isinstance(self.image_bytes, bytes) or not 8 <= len(self.image_bytes) <= _MAX_IMAGE_BYTES:
            raise DocxWriteError('Figure image must be a bounded PNG or JPEG byte sequence.')
        if self.image_format not in ('png', 'jpeg'):
            raise DocxWriteError('Only PNG and JPEG figures are supported.')
        if self.image_format == 'png' and not self.image_bytes.startswith(b'\x89PNG\r\n\x1a\n'):
            raise DocxWriteError('PNG figure signature is invalid.')
        if self.image_format == 'jpeg' and not (
                self.image_bytes.startswith(b'\xff\xd8\xff') and self.image_bytes.endswith(b'\xff\xd9')):
            raise DocxWriteError('JPEG figure signature is invalid.')
        object.__setattr__(self, 'evidence_ids', _evidence_ids(self.evidence_ids))
        object.__setattr__(self, 'pending_reviews', tuple(_text(item) for item in self.pending_reviews))


@dataclass(frozen=True)
class PaperDraft:
    sections: tuple[TextSection, ...] = ()
    tables: tuple[TableDraft, ...] = ()
    figures: tuple[FigureDraft, ...] = ()
    pending_references: tuple[str, ...] = ()

    def __post_init__(self):
        for name in ('sections', 'tables', 'figures', 'pending_references'):
            value = getattr(self, name)
            if not isinstance(value, (tuple, list)):
                raise DocxWriteError(f'{name} must be a list or tuple.')
            object.__setattr__(self, name, tuple(value))
        if any(not isinstance(item, TextSection) for item in self.sections):
            raise DocxWriteError('Paper sections contain an invalid value.')
        if any(not isinstance(item, TableDraft) for item in self.tables):
            raise DocxWriteError('Paper tables contain an invalid value.')
        if any(not isinstance(item, FigureDraft) for item in self.figures):
            raise DocxWriteError('Paper figures contain an invalid value.')
        keys = [item.section_key for item in (*self.sections, *self.tables, *self.figures)]
        if len(set(keys)) != len(keys):
            raise DocxWriteError('Each draft block requires a distinct mapping key.')
        if len(self.pending_references) > 256:
            raise DocxWriteError('Too many pending references.')
        object.__setattr__(self, 'pending_references', tuple(_text(item) for item in self.pending_references))


@dataclass(frozen=True)
class DocxWriteResult:
    path: Path
    sha256: str
    source_sha256: str
    version: int
    modified_parts: tuple[str, ...]


def _safe_parse(content: bytes, part: str):
    try:
        SafeET.fromstring(content, forbid_dtd=True, forbid_entities=True, forbid_external=True)
        parser = ET.XMLParser(target=ET.TreeBuilder(insert_comments=True, insert_pis=True))
        return ET.fromstring(content, parser=parser)
    except (DefusedXmlException, ET.ParseError, ValueError) as exc:
        raise DocxWriteError(f'Template XML is invalid: {part}') from exc


def _serialize(root, original: bytes) -> bytes:
    namespaces = []
    seen = set()
    for _, pair in ET.iterparse(BytesIO(original), events=('start-ns',)):
        prefix, uri = pair
        if pair not in seen:
            seen.add(pair)
            namespaces.append((prefix, uri))
            try:
                ET.register_namespace(prefix, uri)
            except ValueError:
                pass
    data = ET.tostring(root, encoding='utf-8', xml_declaration=True, short_empty_elements=True)
    declaration_end = data.find(b'?>')
    root_start = data.find(b'<', declaration_end + 2)
    root_end = data.find(b'>', root_start)
    for prefix, uri in namespaces:
        token = b'xmlns=' if not prefix else b'xmlns:' + prefix.encode('utf-8') + b'='
        if token in data[root_start:root_end]:
            continue
        escaped = uri.replace('&', '&amp;').replace('"', '&quot;')
        attribute = (b' xmlns="' if not prefix else b' xmlns:' + prefix.encode('utf-8') + b'="')
        addition = attribute + escaped.encode('utf-8') + b'"'
        data = data[:root_end] + addition + data[root_end:]
        root_end += len(addition)
    return data


def _all_parents(root):
    return {child: parent for parent in root.iter() for child in list(parent)}


def _set_paragraph_text(paragraph, text: str) -> None:
    ppr = paragraph.find(_W + 'pPr')
    first_run = paragraph.find(_W + 'r')
    rpr = None if first_run is None else first_run.find(_W + 'rPr')
    retained = [child for child in list(paragraph)
                if child.tag in (_W + 'pPr', _W + 'bookmarkStart', _W + 'bookmarkEnd',
                                 _W + 'commentRangeStart', _W + 'commentRangeEnd', _W + 'proofErr')]
    paragraph[:] = []
    if ppr is not None:
        paragraph.append(ppr)
    run = ET.Element(_W + 'r')
    if rpr is not None:
        run.append(deepcopy(rpr))
    node = ET.SubElement(run, _W + 't')
    if text[:1].isspace() or text[-1:].isspace():
        node.set(_XML_SPACE, 'preserve')
    node.text = text
    children = [item for item in retained if item.tag != _W + 'pPr']
    closing_marks = {_W + 'bookmarkEnd', _W + 'commentRangeEnd'}
    insertion = next((i for i, item in enumerate(children) if item.tag in closing_marks), len(children))
    children.insert(insertion, run)
    paragraph[:] = ([ppr] if ppr is not None else []) + children


def _paragraph(text: str):
    result = ET.Element(_W + 'p')
    _set_paragraph_text(result, text)
    return result


def _after(parent, target, elements) -> None:
    index = list(parent).index(target) + 1
    for offset, element in enumerate(elements):
        parent.insert(index + offset, element)


def _section_placeholder(parent, heading, token, heading_nodes):
    siblings = list(parent)
    for sibling in siblings[siblings.index(heading) + 1:]:
        if sibling in heading_nodes or sibling.tag == _W + 'sectPr':
            break
        if sibling.tag == _W + 'tbl':
            break
        if sibling.tag != _W + 'p':
            continue
        if token in ''.join(node.text or '' for node in sibling.iter(_W + 't')):
            return sibling
    return None


def _markers(evidence_ids, pending_reviews=()):
    result = []
    if evidence_ids:
        result.append('证据：' + ', '.join(evidence_ids))
    else:
        result.append('待人工核验：内容尚未关联证据')
    result.extend('待人工核验：' + item for item in pending_reviews)
    return result


def _replace_table(table, draft: TableDraft):
    rows = table.findall(_W + 'tr')
    if not rows:
        raise DocxWriteError('Mapped table has no header row to preserve.')
    grid = table.find(_W + 'tblGrid')
    column_count = len(grid.findall(_W + 'gridCol')) if grid is not None else len(rows[0].findall(_W + 'tc'))
    if column_count != len(draft.headers) or len(rows[0].findall(_W + 'tc')) != column_count:
        raise DocxWriteError('Mapped table columns do not match the confirmed draft schema.')
    _set_row(rows[0], draft.headers)
    data_template = deepcopy(rows[1] if len(rows) > 1 else rows[0])
    for row in rows[1:]:
        table.remove(row)
    header_flag = data_template.find(f'{_W}trPr/{_W}tblHeader')
    if header_flag is not None:
        data_template.find(_W + 'trPr').remove(header_flag)
    for values in draft.rows:
        row = deepcopy(data_template)
        _set_row(row, values)
        table.append(row)


def _set_row(row, values):
    cells = row.findall(_W + 'tc')
    if len(cells) != len(values):
        raise DocxWriteError('Mapped table row has an unexpected cell count.')
    for cell, value in zip(cells, values):
        paragraph = cell.find(_W + 'p')
        if paragraph is None:
            paragraph = ET.SubElement(cell, _W + 'p')
        _set_paragraph_text(paragraph, value)
        for extra in cell.findall(_W + 'p')[1:]:
            cell.remove(extra)


def _drawing_paragraph(relationship_id: str, figure: FigureDraft, drawing_id: int):
    paragraph = ET.Element(_W + 'p')
    run = ET.SubElement(paragraph, _W + 'r')
    drawing = ET.SubElement(run, _W + 'drawing')
    inline = ET.SubElement(drawing, _WP + 'inline', {'distT': '0', 'distB': '0', 'distL': '0', 'distR': '0'})
    ET.SubElement(inline, _WP + 'extent', {'cx': '4572000', 'cy': '3429000'})
    evidence = ', '.join(figure.evidence_ids) or 'not linked'
    ET.SubElement(inline, _WP + 'docPr', {'id': str(drawing_id), 'name': f'figure-{figure.section_key}',
                                          'descr': f'Evidence: {evidence}'})
    frame = ET.SubElement(inline, _WP + 'cNvGraphicFramePr')
    ET.SubElement(frame, _A + 'graphicFrameLocks', {'noChangeAspect': '1'})
    graphic = ET.SubElement(ET.SubElement(inline, _A + 'graphic'), _A + 'graphicData',
                            {'uri': 'http://schemas.openxmlformats.org/drawingml/2006/picture'})
    picture = ET.SubElement(graphic, _PIC + 'pic')
    nv = ET.SubElement(picture, _PIC + 'nvPicPr')
    ET.SubElement(nv, _PIC + 'cNvPr', {'id': str(drawing_id), 'name': f'figure-{figure.section_key}',
                                       'descr': f'Evidence: {evidence}'})
    ET.SubElement(nv, _PIC + 'cNvPicPr')
    fill = ET.SubElement(picture, _PIC + 'blipFill')
    ET.SubElement(fill, _A + 'blip', {_R + 'embed': relationship_id})
    ET.SubElement(ET.SubElement(fill, _A + 'stretch'), _A + 'fillRect')
    shape = ET.SubElement(picture, _PIC + 'spPr')
    transform = ET.SubElement(shape, _A + 'xfrm')
    ET.SubElement(transform, _A + 'off', {'x': '0', 'y': '0'})
    ET.SubElement(transform, _A + 'ext', {'cx': '4572000', 'cy': '3429000'})
    geometry = ET.SubElement(shape, _A + 'prstGeom', {'prst': 'rect'})
    ET.SubElement(geometry, _A + 'avLst')
    return paragraph


def _add_content_type(root, extension: str, mime_type: str) -> None:
    for item in root.findall(_CT + 'Default'):
        if item.get('Extension', '').casefold() == extension.casefold():
            if item.get('ContentType') != mime_type:
                raise DocxWriteError('Template declares a conflicting image content type.')
            return
    ET.SubElement(root, _CT + 'Default', {'Extension': extension, 'ContentType': mime_type})


def _replace_placeholder(paragraph, token: str, replacement: str) -> bool:
    current = ''.join(node.text or '' for node in paragraph.iter(_W + 't'))
    if token not in current:
        return False
    _set_paragraph_text(paragraph, current.replace(token, replacement))
    return True


def _binding_for(mapping, key, kind):
    binding = next((item for item in mapping.bindings if item.section_key == key), None)
    if binding is None or binding.kind != kind:
        raise DocxWriteError(f'Draft block {key!r} has no confirmed {kind} mapping.')
    return binding


def write_paper(template_path: Path, output_directory: Path, inspection: TemplateInspection,
                mapping: TemplateMapping, draft: PaperDraft) -> DocxWriteResult:
    """Write a new versioned DOCX. The source template is never opened for writing."""
    if not isinstance(inspection, TemplateInspection) or not isinstance(mapping, TemplateMapping):
        raise DocxWriteError('Template inspection and mapping are required.')
    if not isinstance(draft, PaperDraft):
        raise DocxWriteError('Paper draft type is invalid.')
    mapping.validate_for(inspection)
    try:
        template_path = validate_managed_path(Path(template_path))
        output_directory = validate_managed_path(Path(output_directory))
        if not template_path.is_file() or not output_directory.is_dir():
            raise DocxWriteError('Template or paper output directory is unavailable.')
        with template_path.open('rb') as stream:
            source_bytes = stream.read(_MAX_PACKAGE_BYTES + 1)
        if not source_bytes or len(source_bytes) > _MAX_PACKAGE_BYTES:
            raise DocxWriteError('Template package is empty or exceeds the 64 MiB limit.')
    except (OSError, ValueError, TypeError) as exc:
        if isinstance(exc, DocxWriteError):
            raise
        raise DocxWriteError('Template or output path cannot be safely read.') from exc
    source_hash = hashlib.sha256(source_bytes).hexdigest()
    if source_hash != inspection.template_sha256:
        raise TemplateMappingError('Template changed after inspection; confirm mapping again.')
    try:
        with ZipFile(BytesIO(source_bytes)) as archive:
            original_infos = archive.infolist()
            parts = {info.filename: archive.read(info) for info in original_infos}
            archive_comment = archive.comment
    except (BadZipFile, OSError, RuntimeError, ValueError) as exc:
        raise DocxWriteError('Template package could not be copied safely.') from exc

    document_original = parts['word/document.xml']
    rels_original = parts['word/_rels/document.xml.rels']
    types_original = parts['[Content_Types].xml']
    document = _safe_parse(document_original, 'word/document.xml')
    relationships = _safe_parse(rels_original, 'word/_rels/document.xml.rels')
    content_types = _safe_parse(types_original, '[Content_Types].xml')
    body = document.find(_W + 'body')
    if body is None:
        raise DocxWriteError('Template document body is missing.')
    parent_map = _all_parents(document)
    paragraphs = list(body.iter(_W + 'p'))
    tables = list(body.iter(_W + 'tbl'))
    by_id = {item.anchor_id: item for item in inspection.anchors}
    anchor_nodes = {}
    for anchor in inspection.anchors:
        nodes = paragraphs if anchor.kind == 'heading' else tables
        if anchor.index >= len(nodes):
            raise DocxWriteError('Mapped anchor index is outside the document.')
        anchor_nodes[anchor.anchor_id] = nodes[anchor.index]
    heading_nodes = {anchor_nodes[item.anchor_id] for item in inspection.anchors if item.kind == 'heading'}
    for binding in mapping.bindings:
        if binding.anchor_id not in by_id or binding.anchor_id not in anchor_nodes:
            raise TemplateMappingError('Mapped anchor no longer exists in the inspected template.')

    for section in draft.sections:
        binding = _binding_for(mapping, section.section_key, 'heading')
        heading = anchor_nodes[binding.anchor_id]
        parent = parent_map.get(heading)
        if parent is None:
            raise DocxWriteError('Mapped heading has no writable parent.')
        token = '{{section:' + section.section_key + '}}'
        inserted = []
        placeholder = _section_placeholder(parent, heading, token, heading_nodes)
        if placeholder is not None:
            _replace_placeholder(placeholder, token, section.paragraphs[0])
        if placeholder is not None:
            inserted.append(placeholder)
            inserted.extend(_paragraph(value) for value in section.paragraphs[1:])
            _after(parent, placeholder, inserted[1:])
        else:
            inserted = [_paragraph(value) for value in section.paragraphs]
            _after(parent, heading, inserted)
        marker_paragraphs = [_paragraph(text) for text in _markers(section.evidence_ids, section.pending_reviews)]
        _after(parent, inserted[-1], marker_paragraphs)
        parent_map = _all_parents(document)

    for table_draft in draft.tables:
        binding = _binding_for(mapping, table_draft.section_key, 'table')
        table = anchor_nodes[binding.anchor_id]
        _replace_table(table, table_draft)
        parent = parent_map.get(table)
        if parent is None:
            raise DocxWriteError('Mapped table has no writable parent.')
        markers = [_paragraph(text) for text in _markers(table_draft.evidence_ids, table_draft.pending_reviews)]
        _after(parent, table, markers)
        parent_map = _all_parents(document)

    existing_ids = {item.get('Id') for item in relationships.findall(_PKG_REL + 'Relationship')}
    existing_draw_ids = [int(node.get('id')) for node in document.iter(_WP + 'docPr')
                         if (node.get('id') or '').isdigit()]
    drawing_id = max(existing_draw_ids, default=0) + 1
    figure_number = 0
    for figure in draft.figures:
        binding = _binding_for(mapping, figure.section_key, 'heading')
        heading = anchor_nodes[binding.anchor_id]
        parent = _all_parents(document).get(heading)
        if parent is None:
            raise DocxWriteError('Mapped figure heading has no writable parent.')
        figure_number += 1
        extension = 'png' if figure.image_format == 'png' else 'jpg'
        mime_type = 'image/png' if extension == 'png' else 'image/jpeg'
        media_name = f'word/media/mmps-figure-{figure_number:04d}.{extension}'
        if media_name in parts:
            raise DocxWriteError('Figure media path already exists in the template.')
        relationship_id = f'rIdMMPS{figure_number:04d}'
        while relationship_id in existing_ids:
            figure_number += 1
            relationship_id = f'rIdMMPS{figure_number:04d}'
        existing_ids.add(relationship_id)
        ET.SubElement(relationships, _PKG_REL + 'Relationship', {
            'Id': relationship_id, 'Type': _IMAGE_REL, 'Target': 'media/' + Path(media_name).name,
        })
        _add_content_type(content_types, extension, mime_type)
        parts[media_name] = figure.image_bytes
        figure_paragraph = _drawing_paragraph(relationship_id, figure, drawing_id)
        drawing_id += 1
        extras = [figure_paragraph, _paragraph(figure.caption)]
        extras.extend(_paragraph(text) for text in _markers(figure.evidence_ids, figure.pending_reviews))
        _after(parent, heading, extras)

    for pending in draft.pending_references:
        body.insert(max(0, len(list(body)) - (1 if body.find(_W + 'sectPr') is not None else 0)),
                    _paragraph('待补文献：' + pending))

    parts['word/document.xml'] = _serialize(document, document_original)
    parts['word/_rels/document.xml.rels'] = _serialize(relationships, rels_original)
    parts['[Content_Types].xml'] = _serialize(content_types, types_original)
    modified = ('[Content_Types].xml', 'word/_rels/document.xml.rels', 'word/document.xml')
    output_bytes = BytesIO()
    try:
        with ZipFile(BytesIO(source_bytes)) as source, ZipFile(output_bytes, 'w', ZIP_DEFLATED) as output:
            output.comment = archive_comment
            for info in original_infos:
                output.writestr(info, parts[info.filename])
            for name in sorted(set(parts) - set(source.namelist())):
                output.writestr(name, parts[name], compress_type=ZIP_DEFLATED)
    except (BadZipFile, OSError, RuntimeError, ValueError) as exc:
        raise DocxWriteError('Generated DOCX package could not be assembled.') from exc
    package_bytes = output_bytes.getvalue()
    if len(package_bytes) > _MAX_PACKAGE_BYTES:
        raise DocxWriteError('Generated DOCX exceeds the 64 MiB package limit.')

    for version in range(1, 10000):
        destination = validate_managed_path(output_directory / f'paper-v{version:04d}.docx')
        if destination.exists():
            continue
        temporary = validate_managed_path(output_directory / f'.paper-v{version:04d}.{uuid4().hex}.tmp')
        try:
            with temporary.open('xb') as stream:
                stream.write(package_bytes)
                stream.flush()
                os.fsync(stream.fileno())
            validate_managed_path(temporary)
            validate_managed_path(destination)
            if destination.exists():
                temporary.unlink()
                continue
            temporary.rename(destination)
            validate_managed_path(destination)
            return DocxWriteResult(
                destination, hashlib.sha256(package_bytes).hexdigest(), source_hash, version,
                tuple(sorted(set(modified) | {name for name in parts if name not in {info.filename for info in original_infos}})),
            )
        except FileExistsError:
            if temporary.exists():
                temporary.unlink()
            continue
        except BaseException:
            if temporary.exists():
                validate_managed_path(temporary)
                temporary.unlink()
            raise
    raise DocxWriteError('No free versioned paper output name remains.')