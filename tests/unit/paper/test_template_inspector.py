import hashlib
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from zipfile import ZipFile

from mathmodel_paper_studio.paper.template_inspector import (
    TemplateInspectionError, UnsupportedTemplateError, inspect_template,
)
from tests.fixtures.docx.fixture_factory import DOCUMENT_XML, write_fixture, unsupported_object, unsupported_text_box

ROOT = Path(r'F:\Codex另一个工作的地方\tmp\mathmodel-paper-studio-20261015')


class TemplateInspectorTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=ROOT)
        self.addCleanup(self.temp.cleanup)
        self.path = write_fixture(Path(self.temp.name) / 'template.docx')

    def test_read_only_inspection_exposes_headings_tables_layout_images_and_bookmarks(self):
        source_hash = hashlib.sha256(self.path.read_bytes()).hexdigest()
        with ZipFile(self.path) as archive:
            members_before = {item.filename: hashlib.sha256(archive.read(item)).hexdigest()
                              for item in archive.infolist()}

        inspection = inspect_template(self.path)

        self.assertEqual(source_hash, inspection.template_sha256)
        self.assertEqual(source_hash, hashlib.sha256(self.path.read_bytes()).hexdigest())
        self.assertEqual(members_before, dict(inspection.part_hashes))
        headings = [item for item in inspection.anchors if item.kind == 'heading']
        tables = [item for item in inspection.anchors if item.kind == 'table']
        self.assertEqual(['摘要', '结果'], [item.label for item in headings])
        self.assertEqual('Heading1', headings[0].style_id)
        self.assertIn('abstract_anchor', headings[0].bookmark_names)
        self.assertEqual(1, len(tables))
        self.assertEqual(11906, inspection.page_setup.width_twips)
        self.assertEqual(11906, inspection.page_setup.width_twips)
        self.assertEqual(16838, inspection.page_setup.height_twips)
        self.assertEqual(1, inspection.image_count)
        self.assertEqual(('word/header1.xml',), inspection.header_parts)
        self.assertEqual(('word/footer1.xml',), inspection.footer_parts)

    def test_template_read_uses_bounded_streaming_not_read_bytes(self):
        with patch.object(Path, 'read_bytes', side_effect=AssertionError('unbounded read')):
            inspection = inspect_template(self.path)
        self.assertEqual(hashlib.sha256(self.path.read_bytes()).hexdigest(), inspection.template_sha256)

    def test_rejects_malformed_packages_and_unsupported_text_boxes_or_objects(self):
        malformed = Path(self.temp.name) / 'bad.docx'
        malformed.write_bytes(b'not a zip package')
        with self.assertRaises(TemplateInspectionError):
            inspect_template(malformed)
        for name, xml in (('textbox.docx', unsupported_text_box()),
                          ('object.docx', unsupported_object())):
            path = write_fixture(Path(self.temp.name) / name, document_xml=xml)
            with self.subTest(name=name), self.assertRaises(UnsupportedTemplateError):
                inspect_template(path)

    def test_accepts_standard_core_properties_relationship(self):
        path = write_fixture(Path(self.temp.name) / 'core-properties.docx', extra_parts={
            '_rels/.rels': b'<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rIdRoot" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/><Relationship Id="rIdCore" Type="http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties" Target="docProps/core.xml"/></Relationships>',
            'docProps/core.xml': b'<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties"/>',
        })
        inspection = inspect_template(path)
        self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), inspection.template_sha256)
    def test_rejects_broken_internal_relationships(self):
        path = write_fixture(Path(self.temp.name) / 'broken.docx', extra_parts={
            'word/_rels/document.xml.rels': b'<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rIdBad" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/image" Target="missing.png"/></Relationships>'
        })
        with self.assertRaises(TemplateInspectionError):
            inspect_template(path)

        other = write_fixture(Path(self.temp.name) / 'broken-header-rel.docx', extra_parts={
            'word/_rels/header1.xml.rels': b'<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rIdBad" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/image" Target="missing.png"/></Relationships>'
        })
        with self.assertRaises(TemplateInspectionError):
            inspect_template(other)


if __name__ == '__main__':
    unittest.main()