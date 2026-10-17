"""Structural checks for generated DOCX packages; page rendering is reported separately."""
from dataclasses import dataclass
from pathlib import Path
import re
from zipfile import BadZipFile, ZipFile

from defusedxml import ElementTree as SafeET
from defusedxml.common import DefusedXmlException

from mathmodel_paper_studio.bootstrap.paths import validate_managed_path
from mathmodel_paper_studio.paper.template_inspector import (
    TemplateInspection, TemplateInspectionError, inspect_template,
)
from mathmodel_paper_studio.paper.template_mapping import TemplateMapping, TemplateMappingError

_W = '{http://schemas.openxmlformats.org/wordprocessingml/2006/main}'
_WP = '{http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing}'
_A = '{http://schemas.openxmlformats.org/drawingml/2006/main}'
_R = '{http://schemas.openxmlformats.org/officeDocument/2006/relationships}'
_PLACEHOLDER = re.compile(r'\{\{[^{}]{1,128}\}\}')
_EVIDENCE_ID = re.compile(r'^[a-z][a-z0-9_-]{2,63}$')
_MAX_REPORT_KEYS = 256


@dataclass(frozen=True)
class DocxQaReport:
    package_valid: bool
    structural_status: str
    missing_sections: tuple[str, ...] = ()
    unresolved_placeholders: tuple[str, ...] = ()
    missing_figures: tuple[str, ...] = ()
    broken_relationships: tuple[str, ...] = ()
    pending_citations: tuple[str, ...] = ()
    pending_reviews: tuple[str, ...] = ()
    linked_evidence_ids: tuple[str, ...] = ()
    missing_evidence: tuple[str, ...] = ()
    package_errors: tuple[str, ...] = ()
    visual_rendering: str = 'not_performed'

    @property
    def structural_passed(self) -> bool:
        return self.structural_status == 'passed'


def _keys(values, name):
    if not isinstance(values, (tuple, list)) or len(values) > _MAX_REPORT_KEYS:
        raise ValueError(f'{name} must be a bounded list or tuple.')
    result = tuple(values)
    if any(not isinstance(item, str) or not item or len(item) > 128 for item in result):
        raise ValueError(f'{name} contains an invalid key.')
    if len(set(result)) != len(result):
        raise ValueError(f'{name} contains duplicate keys.')
    return result


def _unique(items):
    return tuple(dict.fromkeys(items))


def _paragraph_text(paragraph):
    return ''.join(node.text or '' for node in paragraph.iter(_W + 't')).strip()


def _section_has_content(document, source_anchor, source_anchors, output_anchors):
    same_label_before = sum(1 for item in source_anchors
                            if item.kind == 'heading' and item.label == source_anchor.label
                            and item.index < source_anchor.index)
    candidates = [item for item in output_anchors
                  if item.kind == 'heading' and item.label == source_anchor.label]
    if same_label_before >= len(candidates):
        return False
    output_anchor = candidates[same_label_before]
    paragraphs = list(document.iter(_W + 'p'))
    if output_anchor.index >= len(paragraphs):
        return False
    heading = paragraphs[output_anchor.index]
    parents = {child: parent for parent in document.iter() for child in list(parent)}
    parent = parents.get(heading)
    if parent is None:
        return False
    output_heading_nodes = {paragraphs[item.index] for item in output_anchors
                            if item.kind == 'heading' and item.index < len(paragraphs)}
    siblings = list(parent)
    ignored_markers = ('证据：', '待人工核验：', '待补文献：')
    for sibling in siblings[siblings.index(heading) + 1:]:
        if sibling in output_heading_nodes or sibling.tag in (_W + 'sectPr', _W + 'tbl'):
            break
        if sibling.tag != _W + 'p':
            continue
        text = _paragraph_text(sibling)
        if text and not text.startswith(ignored_markers) and not _PLACEHOLDER.fullmatch(text):
            return True
    return False


def _failed_package(error: str) -> DocxQaReport:
    relationship_errors = (error,) if 'relationship' in error.casefold() else ()
    return DocxQaReport(
        package_valid=False, structural_status='failed',
        broken_relationships=relationship_errors, package_errors=(error,),
    )


def run_docx_qa(path: Path, source_inspection: TemplateInspection, mapping: TemplateMapping,
                *, required_section_keys=(), required_figure_keys=(), expected_evidence_ids=()) -> DocxQaReport:
    """Check package structure and evidence markers without claiming rendered-page acceptance."""
    if not isinstance(source_inspection, TemplateInspection) or not isinstance(mapping, TemplateMapping):
        raise TypeError('Source template inspection and mapping are required.')
    mapping.validate_for(source_inspection)
    required_sections = _keys(required_section_keys, 'Required section keys')
    required_figures = _keys(required_figure_keys, 'Required figure keys')
    expected_evidence = _keys(expected_evidence_ids, 'Expected evidence IDs')
    if any(_EVIDENCE_ID.fullmatch(item) is None for item in expected_evidence):
        raise ValueError('Expected evidence IDs contain an invalid identifier.')
    try:
        managed_path = validate_managed_path(Path(path))
        output_inspection = inspect_template(managed_path)
    except (OSError, ValueError, TypeError, TemplateInspectionError) as exc:
        return _failed_package(str(exc) or 'Generated DOCX package could not be inspected.')
    try:
        with ZipFile(managed_path) as archive:
            document_bytes = archive.read('word/document.xml')
        document = SafeET.fromstring(document_bytes, forbid_dtd=True, forbid_entities=True,
                                     forbid_external=True)
    except (BadZipFile, OSError, KeyError, RuntimeError, DefusedXmlException, ValueError) as exc:
        return _failed_package(f'Generated DOCX main document is invalid: {type(exc).__name__}.')

    source_anchors = {item.anchor_id: item for item in source_inspection.anchors}
    bindings = {item.section_key: item for item in mapping.bindings}
    output_anchors = output_inspection.anchors
    output_headings = {item.label for item in output_anchors if item.kind == 'heading'}
    missing_sections = []
    for key in required_sections:
        binding = bindings.get(key)
        anchor = None if binding is None else source_anchors.get(binding.anchor_id)
        if (binding is None or binding.kind != 'heading' or anchor is None
                or anchor.label not in output_headings
                or not _section_has_content(document, anchor, source_inspection.anchors, output_anchors)):
            missing_sections.append(key)

    paragraph_texts = tuple(_paragraph_text(item) for item in document.iter(_W + 'p'))
    unresolved = _unique(match.group(0) for text in paragraph_texts for match in _PLACEHOLDER.finditer(text))
    figure_names = set()
    for paragraph in document.iter(_W + 'p'):
        if not any(item.get(_R + 'embed') for item in paragraph.iter(_A + 'blip')):
            continue
        figure_names.update(item.get('name', '') for item in paragraph.iter(_WP + 'docPr'))
    missing_figures = []
    for key in required_figures:
        binding = bindings.get(key)
        if binding is None or binding.kind != 'heading' or f'figure-{key}' not in figure_names:
            missing_figures.append(key)

    pending_citations = _unique(text[len('待补文献：'):].strip() for text in paragraph_texts
                                if text.startswith('待补文献：'))
    pending_reviews = _unique(text[len('待人工核验：'):].strip() for text in paragraph_texts
                              if text.startswith('待人工核验：'))
    text_content = '\n'.join(paragraph_texts)
    evidence_content = text_content + '\n' + '\n'.join(
        item.get('descr', '') for item in document.iter(_WP + 'docPr'))
    linked_evidence = tuple(item for item in expected_evidence if item in evidence_content)
    missing_evidence = tuple(item for item in expected_evidence if item not in linked_evidence)

    failed = bool(missing_sections or unresolved or missing_figures or missing_evidence)
    review_required = bool(pending_citations or pending_reviews)
    status = 'failed' if failed else 'review_required' if review_required else 'passed'
    return DocxQaReport(
        package_valid=True, structural_status=status,
        missing_sections=tuple(missing_sections), unresolved_placeholders=unresolved,
        missing_figures=tuple(missing_figures), pending_citations=pending_citations,
        pending_reviews=pending_reviews, linked_evidence_ids=linked_evidence,
        missing_evidence=missing_evidence,
    )
