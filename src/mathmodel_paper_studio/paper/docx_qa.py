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
from mathmodel_paper_studio.paper.standards import PaperStandard, get_default_paper_standard
from mathmodel_paper_studio.paper.limits import MAX_PAPER_EQUATIONS
from mathmodel_paper_studio.paper.quality import FINAL_COVERAGE_SECTION_MINIMUMS
from mathmodel_paper_studio.paper.review_metadata import (
    PaperReviewMetadata, REVIEW_PART, decode_review_metadata,
)
from mathmodel_paper_studio.paper.workflow_notes import workflow_note_fragments

_W = '{http://schemas.openxmlformats.org/wordprocessingml/2006/main}'
_M = '{http://schemas.openxmlformats.org/officeDocument/2006/math}'
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
    standard_id: str | None = None
    standard_version: str | None = None
    standard_sha256: str | None = None
    standard_sections_included: tuple[str, ...] = ()
    missing_standard_sections: tuple[str, ...] = ()
    omitted_optional_sections: tuple[str, ...] = ()
    quality_warnings: tuple[str, ...] = ()
    target_pages: int | None = None
    minimum_pages: int | None = None
    maximum_pages: int | None = None
    actual_pages: int | None = None
    target_page_status: str = 'not_rendered'
    minimum_body_characters: int | None = None
    equation_count: int = 0
    flowchart_count: int = 0
    content_character_count: int = 0
    table_character_count: int = 0
    missing_problem_coverage: tuple[str, ...] = ()
    problem_coverage_status: str = 'not_available'
    workflow_notes: tuple[str, ...] = ()
    caption_numbering_status: str = 'not_checked'

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


def _missing_figure_keys(required_figure_keys, figure_names):
    names = frozenset(name for name in figure_names if isinstance(name, str))
    missing = []
    for key in required_figure_keys:
        expected_name = f'figure-{key}'
        if not any(name == expected_name or name.startswith(expected_name + '-') for name in names):
            missing.append(key)

    # Older writer versions used numbered result image names without the section key.
    legacy_result_count = sum(
        re.fullmatch(r'figure-result-figure-[0-9]{3}', name) is not None
        for name in names
    )
    return tuple(missing[min(legacy_result_count, len(missing)):])


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


def _subsection_problem_content(document, section_anchor, source_anchors, output_anchors, heading):
    same_label_before = sum(1 for item in source_anchors
                            if item.kind == 'heading' and item.label == section_anchor.label
                            and item.index < section_anchor.index)
    candidates = [item for item in output_anchors
                  if item.kind == 'heading' and item.label == section_anchor.label]
    if same_label_before >= len(candidates):
        return ''
    selected = candidates[same_label_before]
    paragraphs = list(document.iter(_W + 'p'))
    if selected.index >= len(paragraphs):
        return ''
    selected_node = paragraphs[selected.index]
    parents = {child: parent for parent in document.iter() for child in list(parent)}
    parent = parents.get(selected_node)
    if parent is None:
        return ''
    source_labels = {item.label for item in source_anchors if item.kind == 'heading'}
    output_heading_nodes = {
        paragraphs[item.index] for item in output_anchors
        if item.kind == 'heading' and item.label in source_labels and item.index < len(paragraphs)
    }
    collecting = False
    current_subsection = []
    collected = []
    for sibling in list(parent)[list(parent).index(selected_node) + 1:]:
        if sibling in output_heading_nodes or sibling.tag == _W + 'sectPr':
            break
        for paragraph in sibling.iter(_W + 'p'):
            text = _paragraph_text(paragraph)
            style = paragraph.find(f'{_W}pPr/{_W}pStyle')
            style_id = '' if style is None else style.get(_W + 'val', '')
            if style_id.casefold() in ('heading2', 'heading 2'):
                if collecting:
                    collected.extend(current_subsection)
                collecting = heading in text
                current_subsection = []
                continue
            if collecting and text:
                current_subsection.append(text)
    if collecting:
        collected.extend(current_subsection)
    return '\n'.join(collected)


def _validated_problem_coverage(values):
    if not isinstance(values, (tuple, list)) or len(values) > 12:
        raise ValueError('Problem coverage must be a bounded list or tuple.')
    result = tuple(values)
    ids, headings = set(), set()
    for item in result:
        if (not isinstance(item, dict) or set(item) != {'problem_id', 'heading', 'description'}
                or not isinstance(item['problem_id'], str)
                or re.fullmatch(r'problem_[1-9][0-9]?', item['problem_id']) is None
                or not isinstance(item['heading'], str) or not 2 <= len(item['heading']) <= 40
                or not isinstance(item['description'], str) or not item['description'].strip()
                or len(item['description']) > 8000):
            raise ValueError('Problem coverage item is invalid.')
        if item['problem_id'] in ids or item['heading'] in headings:
            raise ValueError('Problem coverage identifiers must be unique.')
        ids.add(item['problem_id'])
        headings.add(item['heading'])
    return result


def _validated_problem_coverage_evidence(values, scopes):
    if not isinstance(values, (tuple, list)) or len(values) > 12:
        raise ValueError('Problem coverage evidence must be a bounded list or tuple.')
    scope_ids = {item['problem_id'] for item in scopes}
    result = {}
    for item in values:
        if (not isinstance(item, dict) or set(item) != {'problem_id', 'section_key', 'evidence_ids'}
                or item['problem_id'] not in scope_ids or item['section_key'] != 'results'
                or not isinstance(item['evidence_ids'], (tuple, list))
                or len(item['evidence_ids']) > 64):
            raise ValueError('Problem coverage evidence item is invalid.')
        evidence_ids = tuple(item['evidence_ids'])
        if (len(set(evidence_ids)) != len(evidence_ids)
                or any(not isinstance(value, str) or _EVIDENCE_ID.fullmatch(value) is None
                       for value in evidence_ids)):
            raise ValueError('Problem coverage evidence identifiers are invalid.')
        if item['problem_id'] in result:
            raise ValueError('Problem coverage evidence identifiers must be unique.')
        result[item['problem_id']] = evidence_ids
    return result


def _failed_package(error: str) -> DocxQaReport:
    relationship_errors = (error,) if 'relationship' in error.casefold() else ()
    return DocxQaReport(
        package_valid=False, structural_status='failed',
        broken_relationships=relationship_errors, package_errors=(error,),
    )


def run_docx_qa(path: Path, source_inspection: TemplateInspection, mapping: TemplateMapping,
                *, required_section_keys=(), required_figure_keys=(), expected_evidence_ids=(),
                standard: PaperStandard | None = None, standard_sections=(),
                keyword_count: int | None = None, expected_equation_count: int | None = None,
                expected_flowchart_count: int | None = None,
                content_character_count: int | None = None, table_character_count: int | None = None,
                problem_coverage=(), problem_coverage_evidence=(), pending_references=()) -> DocxQaReport:
    """Check package structure and evidence markers without claiming rendered-page acceptance."""
    if not isinstance(source_inspection, TemplateInspection) or not isinstance(mapping, TemplateMapping):
        raise TypeError('Source template inspection and mapping are required.')
    mapping.validate_for(source_inspection)
    required_sections = _keys(required_section_keys, 'Required section keys')
    required_figures = _keys(required_figure_keys, 'Required figure keys')
    expected_evidence = _keys(expected_evidence_ids, 'Expected evidence IDs')
    selected_standard = get_default_paper_standard() if standard is None else standard
    if not isinstance(selected_standard, PaperStandard):
        raise TypeError('Paper standard profile is invalid.')
    included_standard_sections = _keys(standard_sections, 'Included standard section keys')
    if keyword_count is not None and (type(keyword_count) is not int or not 0 <= keyword_count <= 32):
        raise ValueError('Keyword count is invalid.')
    if expected_equation_count is not None and (
            type(expected_equation_count) is not int or not 0 <= expected_equation_count <= MAX_PAPER_EQUATIONS):
        raise ValueError('Expected equation count is invalid.')
    if expected_flowchart_count is not None and (
            type(expected_flowchart_count) is not int or not 0 <= expected_flowchart_count <= 64):
        raise ValueError('Expected flowchart count is invalid.')
    if content_character_count is not None and (
            type(content_character_count) is not int or not 0 <= content_character_count <= 1_000_000):
        raise ValueError('Paper content character count is invalid.')
    pending_reference_notes = _keys(pending_references, 'Pending reference notes')
    problem_scopes = _validated_problem_coverage(problem_coverage)
    coverage_evidence = _validated_problem_coverage_evidence(problem_coverage_evidence, problem_scopes)
    if table_character_count is not None and (
            type(table_character_count) is not int or not 0 <= table_character_count <= 1_000_000):
        raise ValueError('Table character count is invalid.')
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
            audit = (decode_review_metadata(archive.read(REVIEW_PART))
                     if REVIEW_PART in archive.namelist() else PaperReviewMetadata())
            auxiliary_document_parts = tuple(
                (name, archive.read(name)) for name in archive.namelist()
                if re.fullmatch(r'word/(?:header[0-9]+|footer[0-9]+|footnotes|endnotes)\.xml', name)
            )
        document = SafeET.fromstring(document_bytes, forbid_dtd=True, forbid_entities=True,
                                     forbid_external=True)
        auxiliary_documents = tuple(
            SafeET.fromstring(content, forbid_dtd=True, forbid_entities=True, forbid_external=True)
            for _name, content in auxiliary_document_parts
        )
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

    paragraph_nodes = tuple(document.iter(_W + 'p'))
    paragraph_texts = tuple(_paragraph_text(item) for item in paragraph_nodes)
    table_paragraphs = {item for table in document.iter(_W + 'tbl') for item in table.iter(_W + 'p')}
    table_texts = tuple(_paragraph_text(item) for item in table_paragraphs)
    body_texts = tuple(_paragraph_text(item) for item in paragraph_nodes if item not in table_paragraphs)
    visible_documents = (document, *auxiliary_documents)
    visible_paragraph_texts = tuple(
        _paragraph_text(paragraph)
        for visible_document in visible_documents
        for paragraph in visible_document.iter(_W + 'p')
    )
    alt_texts = tuple(value for visible_document in visible_documents for node in visible_document.iter()
                      if node.tag in (_WP + 'docPr', '{http://schemas.openxmlformats.org/drawingml/2006/picture}cNvPr')
                      for value in (node.get('descr', ''), node.get('title', '')) if value)
    workflow_notes = _unique(fragment for text in (*visible_paragraph_texts, *alt_texts)
                             for fragment in workflow_note_fragments(text))
    unresolved = _unique(match.group(0) for text in paragraph_texts for match in _PLACEHOLDER.finditer(text))
    figure_names = set()
    for paragraph in document.iter(_W + 'p'):
        if not any(item.get(_R + 'embed') for item in paragraph.iter(_A + 'blip')):
            continue
        figure_names.update(item.get('name', '') for item in paragraph.iter(_WP + 'docPr'))
    missing_figures = list(_missing_figure_keys(required_figures, figure_names))
    missing_figures.extend(
        key for key in required_figures
        if key not in missing_figures
        and (bindings.get(key) is None or bindings[key].kind != 'heading')
    )

    pending_citations = _unique((*audit.pending_citations, *(text[len('待补文献：'):].strip()
                                for text in paragraph_texts if text.startswith('待补文献：'))))
    pending_reviews = _unique((*audit.pending_reviews, *(text[len('待人工核验：'):].strip()
                              for text in paragraph_texts if text.startswith('待人工核验：'))))
    text_content = '\n'.join(paragraph_texts)
    evidence_content = text_content + '\n' + '\n'.join(audit.evidence_ids) + '\n' + '\n'.join(
        item.get('descr', '') for item in document.iter(_WP + 'docPr'))
    linked_evidence = tuple(item for item in expected_evidence if item in evidence_content)
    missing_evidence = tuple(item for item in expected_evidence if item not in linked_evidence)

    # Count the generated draft recorded by the writer; template boilerplate is not authored content.
    authored_body_count_missing = audit.body_character_count is None
    authored_body_count_mismatch = (
        content_character_count is not None
        and audit.body_character_count != content_character_count
    )
    content_character_count = audit.body_character_count or 0
    table_character_count = sum(len(text) for text in table_texts if text)

    missing_problem_coverage = []
    document_problem_evidence = dict(audit.problem_result_evidence)
    coverage_sections = tuple(key for key in FINAL_COVERAGE_SECTION_MINIMUMS
                              if key in selected_standard.required_sections)
    if problem_scopes:
        for problem in problem_scopes:
            for section_key in coverage_sections:
                binding = bindings.get(section_key)
                anchor = None if binding is None else source_anchors.get(binding.anchor_id)
                if (anchor is None or binding.kind != 'heading'
                        or len(_subsection_problem_content(document, anchor, source_inspection.anchors,
                                                           output_anchors, problem['heading']))
                        < FINAL_COVERAGE_SECTION_MINIMUMS[section_key]):
                    missing_problem_coverage.append(f"{problem['problem_id']}:{section_key}")
                if (section_key == 'results'
                        and (not coverage_evidence.get(problem['problem_id'])
                             or tuple(document_problem_evidence.get(problem['problem_id'], ()))
                             != tuple(coverage_evidence.get(problem['problem_id'], ()))
                             or any(evidence_id not in expected_evidence
                                    or evidence_id not in linked_evidence
                                    for evidence_id in coverage_evidence[problem['problem_id']]))):
                    missing_problem_coverage.append(f"{problem['problem_id']}:results_evidence")

    missing_standard_sections = tuple(
        key for key in selected_standard.required_sections if key not in included_standard_sections)
    missing_core_standard_sections = tuple(
        key for key in missing_standard_sections if key != 'references')
    omitted_optional_sections = tuple(
        key for key in selected_standard.optional_sections if key not in included_standard_sections)
    equation_count = sum(1 for _ in document.iter(_M + 'oMath'))
    flowchart_count = sum(
        1 for item in document.iter(_WP + 'docPr')
        if (item.get('descr') or '').startswith('Visual type: flowchart;')
    )
    below_minimum_body = (
        selected_standard.minimum_body_characters is not None
        and (authored_body_count_missing
             or content_character_count < selected_standard.minimum_body_characters)
    )
    failed = bool(
        missing_sections or missing_core_standard_sections or unresolved or missing_figures
        or missing_evidence or missing_problem_coverage or workflow_notes or below_minimum_body
        or authored_body_count_mismatch
    )
    review_required = bool(pending_citations or pending_reviews)
    status = 'failed' if failed else 'review_required' if review_required else 'passed'
    warnings = []
    if missing_standard_sections:
        warnings.append('未覆盖规范要求章节：' + '、'.join(missing_standard_sections))
    if missing_core_standard_sections:
        warnings.append('关键论文章节缺失，未通过完整性验收：' + '、'.join(missing_core_standard_sections))
    if missing_problem_coverage:
        warnings.append('逐题论证闭环未通过：' + '、'.join(missing_problem_coverage))
    if workflow_notes:
        warnings.append('正文仍包含内部工作流提示；请先清理提示后再使用论文。')
    if omitted_optional_sections:
        warnings.append('可选章节未生成；请按题目判断是否需要：' + '、'.join(omitted_optional_sections))
    if keyword_count == 0:
        warnings.append('论文关键词缺失；可在 Word 中补充。')
    if selected_standard.minimum_pages is not None and selected_standard.maximum_pages is not None:
        warnings.append(
            f'篇幅目标 {selected_standard.minimum_pages}–{selected_standard.maximum_pages} 页，'
            f'中心目标 {selected_standard.target_pages} 页；页面尚未渲染，实际页数未知。'
        )
    else:
        warnings.append(f'篇幅参考目标约 {selected_standard.target_pages} 页；页面尚未渲染，实际页数未知。')
    if selected_standard.target_body_characters is not None:
        if content_character_count < selected_standard.target_body_characters:
            warnings.append(
                f'当前正文约 {content_character_count:,} 字，低于约 '
                f'{selected_standard.target_body_characters:,} 字的内容规划目标；请检查是否缺少推导、分析或证据。'
            )
        else:
            warnings.append(f'正文内容量约 {content_character_count:,} 字；最终页数仍需 Word 渲染确认。')
    if below_minimum_body:
        if authored_body_count_missing:
            warnings.append('Word 文件缺少原创正文计数审计信息；模板示例文字不能代替本次生成正文。')
        else:
            warnings.append(
                f'正文约 {content_character_count:,} 字，低于最低正文完整性门槛 '
                f'{selected_standard.minimum_body_characters:,} 字，未通过完整性验收。'
            )
    if authored_body_count_mismatch:
        warnings.append('论文清单的正文字符数与 Word 文件审计记录不一致。')
    if pending_reference_notes:
        warnings.extend(pending_reference_notes)
    if not pending_citations and 'references' not in included_standard_sections:
        warnings.append('没有可验证参考文献清单；请按实际来源补充或确认无需引用。')
    if expected_equation_count is not None and equation_count != expected_equation_count:
        warnings.append(f'公式对象数量与清单不一致：清单 {expected_equation_count}，文档 {equation_count}。')
    if selected_standard.recommended_equation_count is not None and (
            equation_count < selected_standard.recommended_equation_count):
        warnings.append(
            f'当前可编辑公式对象 {equation_count} 个，低于约 '
            f'{selected_standard.recommended_equation_count} 个的建议值；请按模型需要补充有效推导。'
        )
    if expected_flowchart_count is not None and flowchart_count != expected_flowchart_count:
        warnings.append(f'流程图数量与清单不一致：清单 {expected_flowchart_count}，文档 {flowchart_count}。')
    if selected_standard.recommended_flowchart_count is not None and (
            flowchart_count < selected_standard.recommended_flowchart_count):
        warnings.append(
            f'当前结构化流程图 {flowchart_count} 张，低于 '
            f'{selected_standard.recommended_flowchart_count} 张的建议值；只补充与题目相关的流程图。'
        )
    equation_labels = []
    table_labels = []
    figure_labels = []
    for text in paragraph_texts:
        for pattern, target in (
                (r'^公式[（(]([0-9]+)[）)]', equation_labels),
                (r'^表\s*([0-9]+)[：:.、]', table_labels),
                (r'^图\s*([0-9]+)[：:.、]', figure_labels)):
            match = re.match(pattern, text)
            if match:
                target.append(int(match.group(1)))
    caption_numbering_ok = all(values == list(range(1, len(values) + 1))
                               for values in (equation_labels, table_labels, figure_labels))
    caption_numbering_status = 'passed' if caption_numbering_ok else 'review_required'
    if not caption_numbering_ok:
        warnings.append('公式、表格或图片编号未连续；请在 Word 中复核。')
    return DocxQaReport(
        package_valid=True, structural_status=status,
        missing_sections=tuple(missing_sections), unresolved_placeholders=unresolved,
        missing_figures=tuple(missing_figures), pending_citations=pending_citations,
        pending_reviews=pending_reviews, linked_evidence_ids=linked_evidence,
        missing_evidence=missing_evidence,
        standard_id=selected_standard.standard_id,
        standard_version=selected_standard.version,
        standard_sha256=selected_standard.sha256,
        standard_sections_included=included_standard_sections,
        missing_standard_sections=missing_standard_sections,
        omitted_optional_sections=omitted_optional_sections,
        quality_warnings=tuple(dict.fromkeys(warnings)),
        target_pages=selected_standard.target_pages,
        minimum_pages=selected_standard.minimum_pages,
        maximum_pages=selected_standard.maximum_pages,
        actual_pages=None,
        target_page_status='not_rendered',
        minimum_body_characters=selected_standard.minimum_body_characters,
        equation_count=equation_count,
        flowchart_count=flowchart_count,
        content_character_count=content_character_count,
        table_character_count=table_character_count,
        missing_problem_coverage=tuple(missing_problem_coverage),
        problem_coverage_status=('failed' if missing_problem_coverage
                                 else 'passed' if problem_scopes else 'not_available'),
        workflow_notes=workflow_notes,
        caption_numbering_status=caption_numbering_status,
    )
