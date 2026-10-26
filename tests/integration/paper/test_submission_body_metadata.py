from zipfile import ZipFile
import xml.etree.ElementTree as ET

from docx import Document

from mathmodel_paper_studio.paper.docx_qa import run_docx_qa
from mathmodel_paper_studio.paper.docx_writer import (
    EquationDraft, FigureDraft, PaperDraft, SubsectionDraft, TableDraft, TextSection,
    write_generic_template, write_paper,
)
from mathmodel_paper_studio.paper.template_inspector import inspect_template
from mathmodel_paper_studio.paper.template_mapping import TemplateMapping
from tests.fixtures.docx.fixture_factory import DOCUMENT_XML, PARTS, PNG_DATA, write_fixture


EVIDENCE = 'ev_123456789012345678901234'
W = '{http://schemas.openxmlformats.org/wordprocessingml/2006/main}'


def test_submission_body_has_no_workflow_markers_and_preserves_audit_state(tmp_path):
    from dataclasses import replace

    from mathmodel_paper_studio.paper.standards import get_default_paper_standard

    template = write_fixture(tmp_path / 'template.docx')
    inspection = inspect_template(template)
    headings = {a.label: a.anchor_id for a in inspection.anchors if a.kind == 'heading'}
    table = next(a.anchor_id for a in inspection.anchors if a.kind == 'table')
    mapping = TemplateMapping.from_selection(inspection, {
        'abstract': headings['摘要'], 'results_figure': headings['结果'], 'results_table': table,
    })
    output = tmp_path / 'output'
    output.mkdir()
    scientific_text = '该模型仅在轴对称和物性连续的假设下适用。'
    mixed_text = scientific_text + '该数值结论仍需人工复核后才能提交。' + '模型适用范围由边界条件和输入数据共同决定。'
    draft = PaperDraft(
        sections=(TextSection('abstract', (mixed_text,
                                           '待人工核验：数值结果尚未复核。',
                                           '需要人工补充可核验参考资料。'), (), ('需检查模型假设',), (
            SubsectionDraft('离散方法', ('采用守恒形式计算。',
                                         '人工补充：请填写真实计算结果。',
                                         'TODO: add the validation result here.'), (), ('需检查网格',)),
        )),),
        equations=(EquationDraft('abstract', r'\frac{a}{b}', '传热关系', (), ('需检查公式',)),),
        tables=(TableDraft('results_table', ('量', '值'), (('温度', '25'),),
                           (EVIDENCE,), ('需检查表格',)),),
        figures=(FigureDraft('results_figure', PNG_DATA, 'png', '温度曲线',
                             (EVIDENCE,), ('需检查图片',),
                             explanation='该图展示温度变化。待人工核验：请确认图与真实结果一致。',
                             visual_id='result-figure-001'),),
        pending_references=('缺少已核实的参考文献',),
    )
    generated = write_paper(template, output, inspection, mapping, draft)
    with ZipFile(generated.path) as archive:
        document = ET.fromstring(archive.read('word/document.xml'))
        visible_text = '\n'.join(n.text or '' for n in document.iter(W + 't'))
        descriptions = '\n'.join(node.get('descr', '') for node in document.iter()
                                 if node.tag.endswith('docPr') or node.tag.endswith('cNvPr'))

    assert '待人工核验' not in visible_text
    assert '待补文献' not in visible_text
    assert '数值结果尚未复核' not in visible_text
    assert '需要人工补充' not in visible_text
    assert '请填写真实计算结果' not in visible_text
    assert '该数值结论' not in visible_text
    assert 'TODO' not in visible_text
    assert EVIDENCE not in visible_text
    assert '需检查' not in visible_text
    assert '待人工核验' not in descriptions
    assert '请确认图与真实结果一致' not in descriptions
    assert scientific_text in visible_text
    assert '模型适用范围由边界条件和输入数据共同决定。' in visible_text
    focused_standard = replace(get_default_paper_standard(), required_sections=('abstract',),
                               target_body_characters=None, minimum_body_characters=None)
    report = run_docx_qa(generated.path, inspection, mapping,
                         required_section_keys=('abstract',),
                         required_figure_keys=('results_figure',),
                         expected_evidence_ids=(EVIDENCE,), standard=focused_standard,
                         standard_sections=('abstract',))
    assert report.structural_status == 'review_required'
    assert report.missing_figures == ()
    assert report.pending_citations == ('缺少已核实的参考文献',)
    assert set(report.pending_reviews) >= {
        '内容尚未关联证据', '需检查模型假设', '需检查网格', '需检查公式', '需检查表格', '需检查图片',
    }
    assert any('该数值结论仍需人工复核' in item for item in report.pending_reviews)
    assert report.linked_evidence_ids == (EVIDENCE,)
    assert not report.missing_evidence
    assert report.workflow_notes == ()
    assert report.table_character_count > 0
    assert report.content_character_count > 0


def test_docx_qa_requires_substantive_coverage_for_each_problem(tmp_path):
    from dataclasses import replace

    from mathmodel_paper_studio.paper.standards import get_default_paper_standard

    template = write_fixture(tmp_path / 'coverage-template.docx')
    inspection = inspect_template(template)
    result_heading = next(anchor.anchor_id for anchor in inspection.anchors
                          if anchor.kind == 'heading' and anchor.label == '结果')
    mapping = TemplateMapping.from_selection(inspection, {'results': result_heading})
    output = tmp_path / 'coverage-output'
    output.mkdir()
    problem_coverage = ({'problem_id': 'problem_1', 'heading': '问题一',
                         'description': '估计蒸发速率'},)
    problem_coverage_evidence = ({'problem_id': 'problem_1', 'section_key': 'results',
                                  'evidence_ids': (EVIDENCE,)},)
    complete_body = '基于输入数据计算蒸发速率，并说明单位、边界条件及与模型的关系。' * 12
    draft = PaperDraft(sections=(TextSection(
        'results', ('结果按子问题分别汇总。',), subsections=(
            SubsectionDraft('问题一：蒸发速率', (complete_body, complete_body), (EVIDENCE,)),
        ),
    ),))
    generated = write_paper(template, output, inspection, mapping, draft, problem_coverage=problem_coverage)
    standard = replace(get_default_paper_standard(), required_sections=('results',),
                       target_body_characters=None, minimum_body_characters=None)

    report = run_docx_qa(
        generated.path, inspection, mapping, required_section_keys=('results',),
        standard=standard, standard_sections=('results',), problem_coverage=problem_coverage,
        expected_evidence_ids=(EVIDENCE,), problem_coverage_evidence=problem_coverage_evidence,
    )

    assert report.problem_coverage_status == 'passed'
    assert report.missing_problem_coverage == ()

    incomplete = write_paper(
        template, output, inspection, mapping,
        PaperDraft(sections=(TextSection('results', ('结果按子问题分别汇总。',)),)),
    )
    failed_report = run_docx_qa(
        incomplete.path, inspection, mapping, required_section_keys=('results',),
        standard=standard, standard_sections=('results',), problem_coverage=problem_coverage,
        expected_evidence_ids=(EVIDENCE,), problem_coverage_evidence=(),
    )

    assert failed_report.problem_coverage_status == 'failed'
    assert set(failed_report.missing_problem_coverage) == {
        'problem_1:results', 'problem_1:results_evidence',
    }


def test_problem_result_evidence_cannot_be_borrowed_from_another_section(tmp_path):
    from dataclasses import replace

    from mathmodel_paper_studio.paper.standards import get_default_paper_standard

    other_evidence = 'ev_abcdefghijabcdefghij1234'
    problem_coverage = ({'problem_id': 'problem_1', 'heading': '问题一',
                         'description': '估计蒸发速率'},)
    template = write_fixture(tmp_path / 'scoped-evidence-template.docx')
    inspection = inspect_template(template)
    result_heading = next(anchor.anchor_id for anchor in inspection.anchors
                          if anchor.kind == 'heading' and anchor.label == '结果')
    abstract_heading = next(anchor.anchor_id for anchor in inspection.anchors
                            if anchor.kind == 'heading' and anchor.label == '摘要')
    mapping = TemplateMapping.from_selection(inspection, {
        'results': result_heading, 'abstract': abstract_heading,
    })
    draft = PaperDraft(sections=(
        TextSection('abstract', ('总体结果概述。',), (other_evidence,)),
        TextSection('results', ('结果汇总。',), subsections=(
            SubsectionDraft('问题一：结果分析', ('逐题说明模型结果、单位和适用范围。' * 12,), (EVIDENCE,)),
        )),
    ))
    generated = write_paper(template, tmp_path, inspection, mapping, draft,
                            problem_coverage=problem_coverage)
    standard = replace(get_default_paper_standard(), required_sections=('abstract', 'results'),
                       target_body_characters=None, minimum_body_characters=None)

    report = run_docx_qa(
        generated.path, inspection, mapping, required_section_keys=('abstract', 'results'),
        standard=standard, standard_sections=('abstract', 'results'),
        problem_coverage=problem_coverage,
        problem_coverage_evidence=({'problem_id': 'problem_1', 'section_key': 'results',
                                    'evidence_ids': (other_evidence,)},),
        expected_evidence_ids=(EVIDENCE, other_evidence),
    )

    assert report.structural_status == 'failed'
    assert report.missing_problem_coverage == ('problem_1:results_evidence',)


def test_docx_qa_requires_result_evidence_for_each_problem(tmp_path):
    from dataclasses import replace

    from mathmodel_paper_studio.paper.standards import get_default_paper_standard

    template = write_fixture(tmp_path / 'evidence-template.docx')
    inspection = inspect_template(template)
    result_heading = next(anchor.anchor_id for anchor in inspection.anchors
                          if anchor.kind == 'heading' and anchor.label == '结果')
    mapping = TemplateMapping.from_selection(inspection, {'results': result_heading})
    output = tmp_path / 'evidence-output'
    output.mkdir()
    complete_body = '依据所选数据计算结果，解释单位、边界条件和对模型结论的影响。' * 12
    generated = write_paper(
        template, output, inspection, mapping,
        PaperDraft(sections=(TextSection('results', ('结果汇总。',), subsections=(
            SubsectionDraft('问题一：结果分析', (complete_body, complete_body)),
        )),)),
    )
    standard = replace(get_default_paper_standard(), required_sections=('results',),
                       target_body_characters=None, minimum_body_characters=None)

    report = run_docx_qa(
        generated.path, inspection, mapping, required_section_keys=('results',),
        standard=standard, standard_sections=('results',),
        problem_coverage=({'problem_id': 'problem_1', 'heading': '问题一',
                           'description': '估计蒸发速率'},),
    )

    assert report.structural_status == 'failed'
    assert report.missing_problem_coverage == ('problem_1:results_evidence',)


def test_docx_qa_checks_combined_model_building_and_derivation_per_problem(tmp_path):
    from dataclasses import replace

    from mathmodel_paper_studio.paper.standards import get_default_paper_standard

    problem_coverage = ({'problem_id': 'problem_1', 'heading': '问题一',
                         'description': '建立并求解蒸发速率模型'},)
    model_anchor_xml = (
        '<w:p><w:pPr><w:pStyle w:val="Heading1"/></w:pPr><w:bookmarkStart w:id="9" '
        'w:name="model_anchor"/><w:r><w:t>模型</w:t></w:r><w:bookmarkEnd w:id="9"/></w:p>'
        '<w:p><w:r><w:t>{{section:model}}</w:t></w:r></w:p>'
    )
    model_xml = DOCUMENT_XML.replace(
        '<w:p><w:pPr><w:pStyle w:val="Heading1"/></w:pPr><w:r><w:t>结果</w:t></w:r></w:p>',
        model_anchor_xml + '<w:p><w:pPr><w:pStyle w:val="Heading1"/></w:pPr>'
        '<w:r><w:t>结果</w:t></w:r></w:p>',
    )
    styles_xml = PARTS['word/styles.xml'].decode('utf-8').replace(
        '</w:styles>', '<w:style w:type="paragraph" w:styleId="Heading2">'
        '<w:name w:val="heading 2"/><w:outlineLvl w:val="1"/></w:style></w:styles>',
    )
    source_template = write_fixture(
        tmp_path / 'model-template.docx', document_xml=model_xml,
        extra_parts={'word/styles.xml': styles_xml.encode('utf-8')},
    )
    source_inspection = inspect_template(source_template)
    model_heading_id = next(anchor.anchor_id for anchor in source_inspection.anchors
                            if anchor.kind == 'heading' and anchor.label == '模型')
    model_mapping = TemplateMapping.from_selection(source_inspection, {'model': model_heading_id})
    first_part = '模型变量与边界条件定义清楚，构造守恒关系并解释其适用范围。' * 8
    second_part = '推导过程与观测数据对应，参数来源和计算步骤均有说明。' * 10
    draft = PaperDraft(sections=(TextSection(
        'model', ('模型按问题逐项建立。',), subsections=(
            SubsectionDraft('问题一：变量和边界', (first_part,)),
            SubsectionDraft('问题一：关系推导', (second_part,)),
        ),
    ),))
    generated = write_paper(source_template, tmp_path, source_inspection, model_mapping, draft)
    standard = replace(get_default_paper_standard(), required_sections=('model',),
                       target_body_characters=None, minimum_body_characters=None)

    report = run_docx_qa(
        generated.path, source_inspection, model_mapping, required_section_keys=('model',),
        standard=standard, standard_sections=('model',), problem_coverage=problem_coverage,
    )

    assert report.problem_coverage_status == 'passed'
    assert report.missing_problem_coverage == ()

    short_draft = PaperDraft(sections=(TextSection(
        'model', ('模型按问题逐项建立。',),
        subsections=(SubsectionDraft('问题一：模型', ('变量关系及推导说明。' * 20,)),),
    ),))
    short_generated = write_paper(source_template, tmp_path, source_inspection, model_mapping, short_draft)
    short_report = run_docx_qa(
        short_generated.path, source_inspection, model_mapping, required_section_keys=('model',),
        standard=standard, standard_sections=('model',), problem_coverage=problem_coverage,
    )

    assert short_report.problem_coverage_status == 'failed'
    assert short_report.missing_problem_coverage == ('problem_1:model',)


def test_docx_qa_blocks_body_far_below_accepted_manuscript_scale(tmp_path):
    from dataclasses import replace

    from mathmodel_paper_studio.paper.standards import get_default_paper_standard

    template = write_fixture(tmp_path / 'short-template.docx')
    inspection = inspect_template(template)
    abstract_heading = next(anchor.anchor_id for anchor in inspection.anchors
                            if anchor.kind == 'heading' and anchor.label == '摘要')
    mapping = TemplateMapping.from_selection(inspection, {'abstract': abstract_heading})
    output = tmp_path / 'short-output'
    output.mkdir()
    generated = write_paper(
        template, output, inspection, mapping,
        PaperDraft(title='短稿', sections=(TextSection('abstract', ('仅有几句。',)),)),
    )
    standard = replace(get_default_paper_standard(), required_sections=('abstract',),
                       target_body_characters=100, minimum_body_characters=60)

    report = run_docx_qa(
        generated.path, inspection, mapping, required_section_keys=('abstract',),
        standard=standard, standard_sections=('abstract',),
    )

    assert report.structural_status == 'failed'
    assert report.content_character_count < standard.minimum_body_characters
    assert any('最低正文完整性门槛' in warning for warning in report.quality_warnings)


def test_template_sample_text_cannot_satisfy_authored_body_minimum(tmp_path):
    from dataclasses import replace

    from mathmodel_paper_studio.paper.standards import get_default_paper_standard

    template = write_fixture(tmp_path / 'sample-template.docx')
    document = Document(template)
    document.add_paragraph('这是模板自带的示例文字。' * 200)
    document.save(template)
    inspection = inspect_template(template)
    abstract_heading = next(anchor.anchor_id for anchor in inspection.anchors
                            if anchor.kind == 'heading' and anchor.label == '摘要')
    mapping = TemplateMapping.from_selection(inspection, {'abstract': abstract_heading})
    output = tmp_path / 'sample-output'
    output.mkdir()
    generated = write_paper(
        template, output, inspection, mapping,
        PaperDraft(title='短稿', sections=(TextSection('abstract', ('只有少量原创正文。',)),)),
    )
    standard = replace(get_default_paper_standard(), required_sections=('abstract',),
                       target_body_characters=500, minimum_body_characters=300)

    report = run_docx_qa(
        generated.path, inspection, mapping, required_section_keys=('abstract',),
        standard=standard, standard_sections=('abstract',),
    )

    assert report.structural_status == 'failed'
    assert report.content_character_count < standard.minimum_body_characters


def test_docx_qa_blocks_missing_core_standard_sections(tmp_path):
    from dataclasses import replace

    from mathmodel_paper_studio.paper.standards import get_default_paper_standard

    template = write_fixture(tmp_path / 'missing-section-template.docx')
    inspection = inspect_template(template)
    abstract_heading = next(anchor.anchor_id for anchor in inspection.anchors
                            if anchor.kind == 'heading' and anchor.label == '摘要')
    mapping = TemplateMapping.from_selection(inspection, {'abstract': abstract_heading})
    output = tmp_path / 'missing-section-output'
    output.mkdir()
    generated = write_paper(
        template, output, inspection, mapping,
        PaperDraft(sections=(TextSection('abstract', ('摘要说明研究目标和主要结果。',)),)),
    )
    standard = replace(get_default_paper_standard(), required_sections=('abstract', 'model'),
                       target_body_characters=None, minimum_body_characters=None)

    report = run_docx_qa(
        generated.path, inspection, mapping, required_section_keys=('abstract',),
        standard=standard, standard_sections=('abstract',),
    )

    assert report.structural_status == 'failed'
    assert report.missing_standard_sections == ('model',)


def test_docx_qa_rejects_workflow_notes_hidden_in_template_alt_text(tmp_path):
    template = write_fixture(
        tmp_path / 'template-with-review-note.docx',
        document_xml=DOCUMENT_XML.replace(
            'name="existing-image"',
            'name="existing-image" descr="待人工核验：请确认图示结果。"',
        ),
    )
    inspection = inspect_template(template)
    headings = {anchor.label: anchor.anchor_id for anchor in inspection.anchors
                if anchor.kind == 'heading'}
    table = next(anchor.anchor_id for anchor in inspection.anchors if anchor.kind == 'table')
    mapping = TemplateMapping.from_selection(inspection, {
        'abstract': headings['摘要'], 'results_table': table,
    })
    output = tmp_path / 'output'
    output.mkdir()
    generated = write_paper(
        template, output, inspection, mapping,
        PaperDraft(sections=(TextSection('abstract', ('本研究采用守恒模型。',)),)),
    )

    report = run_docx_qa(generated.path, inspection, mapping,
                         required_section_keys=('abstract',))

    assert report.structural_status == 'failed'
    assert any('待人工核验' in note for note in report.workflow_notes)


def test_docx_qa_scans_visible_header_and_footer_text_for_workflow_notes(tmp_path):
    footer = (
        '<w:ftr xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        '<w:p><w:r><w:t>待专家确认：核验数据来源。</w:t></w:r></w:p></w:ftr>'
    )
    template = write_fixture(
        tmp_path / 'footer-workflow-note.docx',
        extra_parts={'word/footer1.xml': footer.encode('utf-8')},
    )
    inspection = inspect_template(template)
    abstract_heading = next(anchor.anchor_id for anchor in inspection.anchors
                            if anchor.kind == 'heading' and anchor.label == '摘要')
    mapping = TemplateMapping.from_selection(inspection, {'abstract': abstract_heading})
    output = tmp_path / 'footer-note-output'
    output.mkdir()
    generated = write_paper(
        template, output, inspection, mapping,
        PaperDraft(sections=(TextSection('abstract', ('本文使用给定数据建立模型。',)),)),
    )

    report = run_docx_qa(
        generated.path, inspection, mapping, required_section_keys=('abstract',),
    )

    assert report.structural_status == 'failed'
    assert any('待专家确认' in note for note in report.workflow_notes)
