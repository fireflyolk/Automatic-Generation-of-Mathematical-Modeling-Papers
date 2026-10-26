from pathlib import Path
import tempfile
import unittest

from mathmodel_paper_studio.inputs.docx_reader import read_docx
from mathmodel_paper_studio.inputs.profiling import ImportLimits
from tests.fixtures.docx.fixture_factory import write_fixture

ROOT = Path(r'F:\MathModelPaperStudio\tmp\mathmodel-paper-studio-20261015')


class DocxReaderTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=ROOT)
        self.addCleanup(self.temp.cleanup)

    def test_extracts_body_paragraphs_and_table_text_as_untrusted_input(self):
        path = write_fixture(Path(self.temp.name) / 'data.docx')

        result = read_docx(path.read_bytes())

        self.assertIn('摘要', result.text)
        self.assertIn('指标\t数值', result.text)
        self.assertGreater(result.paragraph_count, 0)
        self.assertEqual(1, result.table_count)
        self.assertGreater(result.character_count, 0)

    def test_rejects_missing_document_xml_and_dtd_entities(self):
        missing = Path(self.temp.name) / 'missing.docx'
        write_fixture(missing, extra_parts={'word/document.xml': b'<not-a-word-document/>'})
        with self.assertRaises(ValueError):
            read_docx(missing.read_bytes())

        unsafe = Path(self.temp.name) / 'unsafe.docx'
        write_fixture(unsafe, document_xml='<!DOCTYPE x [<!ENTITY e SYSTEM "file:///C:/secret">]><x>&e;</x>')
        with self.assertRaises(ValueError):
            read_docx(unsafe.read_bytes())

    def test_enforces_extracted_text_limit(self):
        path = write_fixture(Path(self.temp.name) / 'limited.docx')
        with self.assertRaisesRegex(ValueError, '文本超过'):
            read_docx(path.read_bytes(), limits=ImportLimits(max_docx_chars=10))


if __name__ == '__main__':
    unittest.main()
