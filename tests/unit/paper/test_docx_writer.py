import hashlib
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from zipfile import ZipFile

from docx import Document

from mathmodel_paper_studio.paper.template_inspector import inspect_template
from mathmodel_paper_studio.paper.template_mapping import TemplateMapping
from mathmodel_paper_studio.paper.docx_writer import (
    EquationDraft, FigureDraft, PaperDraft, SubsectionDraft, TableDraft, TextSection,
    _math_paragraph, write_paper,
)
from tests.fixtures.docx.fixture_factory import DOCUMENT_XML, PNG_DATA, write_fixture

ROOT = Path(r'F:\MathModelPaperStudio\tmp\mathmodel-paper-studio-20261015')
EVIDENCE = 'ev_123456789012345678901234'


class DocxWriterTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=ROOT)
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.template = write_fixture(root / 'template.docx')
        self.inspection = inspect_template(self.template)
        headings = {item.label: item.anchor_id for item in self.inspection.anchors if item.kind == 'heading'}
        table = next(item.anchor_id for item in self.inspection.anchors if item.kind == 'table')
        self.mapping = TemplateMapping.from_selection(self.inspection, {
            'abstract': headings['摘要'], 'results_figure': headings['结果'], 'results_table': table,
        })
        self.output_dir = root / '60_paper'
        self.output_dir.mkdir()
        self.source_bytes = self.template.read_bytes()
        with ZipFile(self.template) as archive:
            self.original_parts = {item.filename: archive.read(item) for item in archive.infolist()}
        self.draft = PaperDraft(
            sections=(TextSection('abstract', ('基于样例数据建立了确定性模型。',), (EVIDENCE,)),),
            tables=(TableDraft('results_table', ('指标', '数值'), (('误差', '0.02'),), (EVIDENCE,)),),
            figures=(FigureDraft('results_figure', PNG_DATA, 'png', '验证误差曲线', (EVIDENCE,)),),
        )

    def test_writes_versioned_copy_preserving_source_and_untouched_package_parts(self):
        result = write_paper(self.template, self.output_dir, self.inspection, self.mapping, self.draft)

        self.assertEqual('paper-v0001.docx', result.path.name)
        self.assertEqual(self.inspection.template_sha256, result.source_sha256)
        self.assertEqual(self.source_bytes, self.template.read_bytes())
        with ZipFile(result.path) as archive:
            self.assertIsNone(archive.testzip())
            output_parts = {item.filename: archive.read(item) for item in archive.infolist()}
        changed = {'word/document.xml', 'word/_rels/document.xml.rels', '[Content_Types].xml'}
        self.assertTrue(set(self.original_parts).issubset(output_parts))
        for name, content in self.original_parts.items():
            if name not in changed:
                self.assertEqual(content, output_parts[name], name)
        self.assertIn('word/media/mmps-figure-0001.png', output_parts)
        document = output_parts['word/document.xml'].decode('utf-8')
        self.assertIn('基于样例数据建立了确定性模型。', document)
        self.assertIn(EVIDENCE, document)
        self.assertIn('验证误差曲线', document)
        self.assertNotIn('{{section:abstract}}', document)
        self.assertIn('customXml/item1.xml', output_parts)
        self.assertEqual(hashlib.sha256(result.path.read_bytes()).hexdigest(), result.sha256)

    def test_new_versions_never_overwrite_and_stale_or_unmapped_content_is_rejected(self):
        first = write_paper(self.template, self.output_dir, self.inspection, self.mapping, self.draft)
        first_bytes = first.path.read_bytes()
        second = write_paper(self.template, self.output_dir, self.inspection, self.mapping, self.draft)
        self.assertEqual('paper-v0002.docx', second.path.name)
        self.assertEqual(first_bytes, first.path.read_bytes())

        changed = write_fixture(Path(self.temp.name) / 'changed.docx',
                                document_xml=DOCUMENT_XML.replace('摘要', '新的摘要'))
        with self.assertRaises(ValueError):
            write_paper(changed, self.output_dir, self.inspection, self.mapping, self.draft)
        bad_draft = PaperDraft(sections=(TextSection('unmapped', ('text',), (EVIDENCE,)),))
        with self.assertRaises(ValueError):
            write_paper(self.template, self.output_dir, self.inspection, self.mapping, bad_draft)
        self.assertEqual(first_bytes, first.path.read_bytes())

    def test_unevidenced_claim_review_stays_out_of_body_and_bad_image_is_rejected(self):
        draft = PaperDraft(sections=(TextSection('abstract', ('结论需要复核。',)),))
        result = write_paper(self.template, self.output_dir, self.inspection, self.mapping, draft)
        with ZipFile(result.path) as archive:
            document = archive.read('word/document.xml').decode('utf-8')
            audit = archive.read('customXml/mmps-paper-review.xml').decode('utf-8')
        self.assertNotIn('待人工核验', document)
        self.assertIn('内容尚未关联证据', audit)
        with self.assertRaises(ValueError):
            FigureDraft('results_figure', b'bad-image', 'png', 'x', (EVIDENCE,))

    def test_template_copy_uses_bounded_streaming_not_read_bytes(self):
        with patch.object(Path, 'read_bytes', side_effect=AssertionError('unbounded read')):
            result = write_paper(self.template, self.output_dir, self.inspection, self.mapping,
                                 PaperDraft(sections=(TextSection('abstract', ('内容。',), (EVIDENCE,)),)))
        self.assertTrue(result.path.is_file())

    def test_original_heading_references_survive_earlier_insertions(self):
        result = write_paper(self.template, self.output_dir, self.inspection, self.mapping, self.draft)
        with ZipFile(result.path) as archive:
            document = archive.read('word/document.xml').decode('utf-8')
        self.assertLess(document.index('>结果<'), document.index('figure-results_figure'))

    def test_equation_is_inserted_under_its_declared_subsection(self):
        draft = PaperDraft(
            sections=(TextSection(
                'abstract', ('章节引言。',), subsections=(
                    SubsectionDraft('守恒方程', ('控制体推导。',)),
                    SubsectionDraft('边界条件', ('边界说明。',)),
                ),
            ),),
            equations=(EquationDraft(
                'abstract', 'x=1', '守恒关系', subsection_index=1,
            ),),
        )

        result = write_paper(self.template, self.output_dir, self.inspection, self.mapping, draft)
        document = Document(result.path)
        paragraphs = document.paragraphs
        equation_index = next(
            index for index, paragraph in enumerate(paragraphs)
            if paragraph._p.xpath('.//m:oMath')
        )
        first_subsection_index = next(
            index for index, paragraph in enumerate(paragraphs)
            if paragraph.text.endswith('守恒方程')
        )
        second_subsection_index = next(
            index for index, paragraph in enumerate(paragraphs)
            if paragraph.text.endswith('边界条件')
        )

        self.assertGreater(equation_index, first_subsection_index)
        self.assertLess(equation_index, second_subsection_index)

    def test_equation_rejects_a_subsection_index_outside_its_parent(self):
        section = TextSection(
            'abstract', ('章节引言。',),
            subsections=(SubsectionDraft('守恒方程', ('控制体推导。',)),),
        )
        equation = EquationDraft('abstract', 'x=1', '守恒关系', subsection_index=2)

        with self.assertRaises(ValueError):
            PaperDraft(sections=(section,), equations=(equation,))

    def test_common_latex_fraction_and_math_commands_render_as_readable_editable_math(self):
        paragraph, supported = _math_paragraph(
            r'\frac{A_s(t)}{V(t)}=\frac{2}{R(t)}, \rho_d, \tag{11}'
        )

        math_text = ''.join(paragraph.itertext())
        self.assertTrue(supported)
        self.assertNotIn(r'\frac', math_text)
        self.assertNotIn(r'\tag', math_text)
        self.assertIn('(', math_text)
        self.assertIn('/', math_text)
        self.assertIn('ρ', math_text)
        self.assertTrue(any(node.tag.endswith('}oMath') for node in paragraph.iter()))

    def test_common_latex_integral_and_limits_do_not_leak_commands_into_equation_text(self):
        paragraph, supported = _math_paragraph(
            r'\int_0^{R(t)} \rho_d(r,t) \, dr, \mathcal{R}_n, '
            r'\mathbb{Z}, t_{k+1}\le t'
        )

        math_text = ''.join(paragraph.itertext())
        self.assertTrue(supported)
        for command in (r'\int', r'\rho', r'\mathcal', r'\mathbb', r'\le'):
            self.assertNotIn(command, math_text)
        self.assertIn('∫', math_text)
        self.assertIn('ℛ', math_text)
        self.assertIn('ℤ', math_text)
        self.assertIn('≤', math_text)

    def test_section_placeholder_search_stops_at_the_next_heading(self):
        moved = DOCUMENT_XML.replace(
            '<w:p><w:r><w:t>{{section:abstract}}</w:t></w:r></w:p>', '<w:p/>')
        moved = moved.replace(
            '<w:p><w:pPr><w:pStyle w:val="Heading1"/></w:pPr><w:r><w:t>结果</w:t></w:r></w:p>',
            '<w:p><w:pPr><w:pStyle w:val="Heading1"/></w:pPr><w:r><w:t>结果</w:t></w:r></w:p>\n'
            '<w:p><w:r><w:t>{{section:abstract}}</w:t></w:r></w:p>')
        template = write_fixture(Path(self.temp.name) / 'moved-placeholder.docx', document_xml=moved)
        inspection = inspect_template(template)
        heading = next(item.anchor_id for item in inspection.anchors
                       if item.kind == 'heading' and item.label == '摘要')
        mapping = TemplateMapping.from_selection(inspection, {'abstract': heading})
        result = write_paper(template, self.output_dir, inspection, mapping,
                             PaperDraft(sections=(TextSection('abstract', ('摘要内容。',), (EVIDENCE,)),)))
        with ZipFile(result.path) as archive:
            document = archive.read('word/document.xml').decode('utf-8')
        self.assertIn('{{section:abstract}}', document)

    def test_placeholder_replacement_keeps_bookmark_range_around_inserted_text(self):
        marked = DOCUMENT_XML.replace(
            '<w:p><w:r><w:t>{{section:abstract}}</w:t></w:r></w:p>',
            '<w:p><w:bookmarkStart w:id="7" w:name="abstract_body"/>'
            '<w:r><w:t>{{section:abstract}}</w:t></w:r><w:bookmarkEnd w:id="7"/></w:p>')
        template = write_fixture(Path(self.temp.name) / 'marked-placeholder.docx', document_xml=marked)
        inspection = inspect_template(template)
        heading = next(item.anchor_id for item in inspection.anchors
                       if item.kind == 'heading' and item.label == '摘要')
        mapping = TemplateMapping.from_selection(inspection, {'abstract': heading})
        result = write_paper(template, self.output_dir, inspection, mapping,
                             PaperDraft(sections=(TextSection('abstract', ('摘要内容。',), (EVIDENCE,)),)))
        with ZipFile(result.path) as archive:
            document = archive.read('word/document.xml').decode('utf-8')
        self.assertRegex(document, r'<w:bookmarkStart[^>]*/><w:r>.*?摘要内容。.*?</w:r><w:bookmarkEnd')


if __name__ == '__main__':
    unittest.main()
