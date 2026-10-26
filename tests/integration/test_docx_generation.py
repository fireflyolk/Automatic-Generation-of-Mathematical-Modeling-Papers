import tempfile
import unittest
from pathlib import Path

from mathmodel_paper_studio.paper.docx_qa import run_docx_qa
from mathmodel_paper_studio.paper.docx_writer import PaperDraft, FigureDraft, TableDraft, TextSection, write_paper
from mathmodel_paper_studio.paper.standards import PaperStandard
from mathmodel_paper_studio.paper.template_inspector import inspect_template
from mathmodel_paper_studio.paper.template_mapping import TemplateMapping
from tests.fixtures.docx.fixture_factory import DOCUMENT_XML, PNG_DATA, write_fixture

ROOT = Path(r'F:\MathModelPaperStudio\tmp\mathmodel-paper-studio-20261015')
EVIDENCE = 'ev_123456789012345678901234'
# Isolates citation/rendering status from the default B078 body-length minimum.
LENIENT_STANDARD = PaperStandard(
    standard_id='test-qa-standard', version='1', display_name='测试 QA 规范',
    target_pages=1, required_sections=('abstract',), optional_sections=(),
    content_rules={}, style_defaults={}, stage_rules={})


class DocxGenerationQaTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=ROOT)
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.template = write_fixture(self.root / 'template.docx')
        self.inspection = inspect_template(self.template)
        headings = {item.label: item.anchor_id for item in self.inspection.anchors if item.kind == 'heading'}
        table = next(item.anchor_id for item in self.inspection.anchors if item.kind == 'table')
        self.mapping = TemplateMapping.from_selection(self.inspection, {
            'abstract': headings['摘要'], 'results_figure': headings['结果'], 'results_table': table,
        })
        self.output = self.root / '60_paper'
        self.output.mkdir()

    def test_structural_qa_distinguishes_pending_citations_from_rendering(self):
        draft = PaperDraft(
            sections=(TextSection('abstract', ('模型结果由样例数据计算。',), (EVIDENCE,)),),
            tables=(TableDraft('results_table', ('指标', '数值'), (('误差', '0.02'),), (EVIDENCE,)),),
            figures=(FigureDraft('results_figure', PNG_DATA, 'png', '误差曲线', (EVIDENCE,)),),
            pending_references=('请补充原始论文来源',),
        )
        generated = write_paper(self.template, self.output, self.inspection, self.mapping, draft)
        report = run_docx_qa(generated.path, self.inspection, self.mapping,
                              standard=LENIENT_STANDARD,
                              required_section_keys=('abstract',),
                              required_figure_keys=('results_figure',),
                              standard_sections=('abstract',),
                              expected_evidence_ids=(EVIDENCE,))

        self.assertTrue(report.package_valid)
        self.assertEqual('review_required', report.structural_status)
        self.assertEqual(('请补充原始论文来源',), report.pending_citations)
        self.assertEqual((), report.missing_sections)
        self.assertEqual((), report.missing_figures)
        self.assertEqual((), report.missing_evidence)
        self.assertEqual('not_performed', report.visual_rendering)

    def test_qa_reports_unresolved_placeholder_missing_figure_and_evidence(self):
        generated = write_paper(self.template, self.output, self.inspection, self.mapping, PaperDraft())
        report = run_docx_qa(generated.path, self.inspection, self.mapping,
                              required_section_keys=('abstract', 'missing-section'),
                              required_figure_keys=('results_figure',),
                              expected_evidence_ids=(EVIDENCE,))

        self.assertEqual('failed', report.structural_status)
        self.assertEqual(('abstract', 'missing-section'), report.missing_sections)
        self.assertIn('{{section:abstract}}', report.unresolved_placeholders)
        self.assertEqual(('results_figure',), report.missing_figures)
        self.assertIn(EVIDENCE, report.missing_evidence)

    def test_qa_requires_content_after_a_required_heading(self):
        no_placeholder = DOCUMENT_XML.replace(
            '<w:p><w:r><w:t>{{section:abstract}}</w:t></w:r></w:p>', '')
        template = write_fixture(self.root / 'empty-section.docx', document_xml=no_placeholder)
        inspection = inspect_template(template)
        abstract = next(item.anchor_id for item in inspection.anchors
                        if item.kind == 'heading' and item.label == '摘要')
        mapping = TemplateMapping.from_selection(inspection, {'abstract': abstract})
        output = write_paper(template, self.output, inspection, mapping, PaperDraft())

        report = run_docx_qa(output.path, inspection, mapping, required_section_keys=('abstract',))

        self.assertEqual(('abstract',), report.missing_sections)
        self.assertEqual('failed', report.structural_status)

    def test_qa_reports_broken_relationships_without_crashing(self):
        broken = write_fixture(self.root / 'broken.docx', extra_parts={
            'word/_rels/document.xml.rels': b'<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rIdBad" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/image" Target="missing.png"/></Relationships>'
        })
        report = run_docx_qa(broken, self.inspection, self.mapping)

        self.assertFalse(report.package_valid)
        self.assertTrue(report.broken_relationships)
        self.assertEqual('failed', report.structural_status)


if __name__ == '__main__':
    unittest.main()
