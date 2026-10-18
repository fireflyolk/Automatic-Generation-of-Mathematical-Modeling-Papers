import unittest

from mathmodel_paper_studio.inputs.profiling import ImportLimits
from tests.fixtures.pdf.fixture_factory import pdf_bytes, type1_font_pdf_bytes


class PdfReaderTests(unittest.TestCase):
    def read_pdf(self, *args, **kwargs):
        from mathmodel_paper_studio.inputs.pdf_reader import read_pdf
        return read_pdf(*args, **kwargs)

    def test_extracts_chinese_text_in_page_order(self):
        result = self.read_pdf(pdf_bytes(compress=True))
        self.assertEqual('数学建模题目：优化运输成本\n\n附件数据见表一', result.text)
        self.assertEqual(2, result.page_count)
        self.assertEqual(len(result.text), result.character_count)

    def test_rejects_encrypted_malformed_and_image_only_documents(self):
        for content, reason in (
            (pdf_bytes(password='test-password'), '加密'),
            (b'%PDF-1.7\nnot a valid PDF', '损坏|无效'),
            (b'not a pdf', '无效'),
            (pdf_bytes(pages=('',)), '文字|文本'),
        ):
            with self.subTest(reason=reason), self.assertRaisesRegex(ValueError, reason):
                self.read_pdf(content)

    def test_rejects_files_pages_text_and_decompressed_streams_over_limit(self):
        cases = (
            (pdf_bytes(), ImportLimits(max_file_bytes=10)),
            (pdf_bytes(), ImportLimits(max_pdf_pages=1)),
            (pdf_bytes(), ImportLimits(max_pdf_chars=5)),
            (pdf_bytes(compress=True, padding=8192), ImportLimits(max_pdf_stream_bytes=1024)),
            (pdf_bytes(padding=100), ImportLimits(max_pdf_total_stream_bytes=200)),
        )
        for content, limits in cases:
            with self.subTest(limits=limits), self.assertRaisesRegex(ValueError, '上限|大小'):
                self.read_pdf(content, limits=limits)

    def test_nested_form_text_is_extracted_and_counted_toward_aggregate_stream_limit(self):
        content = pdf_bytes(pages=('嵌套题目文字',) * 2, padding=80, as_form=True)
        self.assertEqual('嵌套题目文字\n\n嵌套题目文字', self.read_pdf(content).text)
        with self.assertRaisesRegex(ValueError, '大小|上限'):
            self.read_pdf(content, limits=ImportLimits(max_pdf_total_stream_bytes=200))

    def test_rejects_partial_text_when_form_invocation_budget_is_exceeded(self):
        content = pdf_bytes(pages=('嵌套题目',), as_form=True, form_uses=101)
        with self.assertRaisesRegex(ValueError, '完整|内容|解析'):
            self.read_pdf(content)

    def test_normalizes_parser_assertions_for_malformed_inline_images(self):
        content = pdf_bytes(pages=('',), raw_content=b'1 BI /W 1 /H 1 /BPC 8 /CS /RGB ID abc EI')
        with self.assertRaisesRegex(ValueError, '损坏|无效|内容'):
            self.read_pdf(content)

    def test_type1_font_mapping_stream_counts_toward_aggregate_limit(self):
        content = type1_font_pdf_bytes()
        self.assertEqual('A', self.read_pdf(content).text)
        with self.assertRaisesRegex(ValueError, '大小|上限'):
            self.read_pdf(content, limits=ImportLimits(max_pdf_total_stream_bytes=100))
