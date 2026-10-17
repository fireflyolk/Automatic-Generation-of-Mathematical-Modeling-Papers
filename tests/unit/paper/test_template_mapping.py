from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from mathmodel_paper_studio.paper.template_inspector import inspect_template
from mathmodel_paper_studio.paper.template_mapping import TemplateMapping, TemplateMappingError
from tests.fixtures.docx.fixture_factory import DOCUMENT_XML, write_fixture

ROOT = Path(r'F:\Codex另一个工作的地方\tmp\mathmodel-paper-studio-20261015')


class TemplateMappingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=ROOT)
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.template = write_fixture(root / 'template.docx')
        self.inspection = inspect_template(self.template)
        self.project = root / 'project'
        (self.project / '70_review').mkdir(parents=True)

    def _selection(self):
        heading = next(item for item in self.inspection.anchors
                       if item.kind == 'heading' and item.label == '摘要')
        table = next(item for item in self.inspection.anchors if item.kind == 'table')
        return {'abstract': heading.anchor_id, 'results_table': table.anchor_id}

    def test_mapping_persists_template_hash_and_rejects_overwrite(self):
        mapping = TemplateMapping.from_selection(self.inspection, self._selection())
        path = mapping.save(self.project, version=1)
        saved = path.read_bytes()
        with patch.object(Path, 'read_bytes', side_effect=AssertionError('unbounded read')):
            loaded = TemplateMapping.load(self.project, version=1)
        loaded.validate_for(self.inspection)

        self.assertEqual(self.inspection.template_sha256, loaded.template_sha256)
        self.assertEqual(mapping.bindings, loaded.bindings)
        with self.assertRaises(FileExistsError):
            mapping.save(self.project, version=1)
        self.assertEqual(saved, path.read_bytes())

    def test_changed_template_hash_invalidates_mapping(self):
        mapping = TemplateMapping.from_selection(self.inspection, self._selection())
        changed = write_fixture(Path(self.temp.name) / 'changed.docx',
                                document_xml=DOCUMENT_XML.replace('摘要', '新的摘要'))
        with self.assertRaises(TemplateMappingError):
            mapping.validate_for(inspect_template(changed))

    def test_unknown_duplicate_and_malformed_mapping_entries_are_rejected(self):
        first, second = self._selection().values()
        with self.assertRaises(TemplateMappingError):
            TemplateMapping.from_selection(self.inspection, {'bad': 'missing-anchor'})
        with self.assertRaises(TemplateMappingError):
            TemplateMapping.from_selection(self.inspection, {'first': first, 'second': first})
        path = TemplateMapping.from_selection(self.inspection, self._selection()).save(self.project)
        path.write_bytes(b'{"schema_version":1,"schema_version":1}')
        with self.assertRaises(TemplateMappingError):
            TemplateMapping.load(self.project, version=1)

    def test_mapping_constructor_rejects_invalid_keys_digests_and_binding_kinds(self):
        from mathmodel_paper_studio.paper.template_mapping import AnchorBinding

        anchor_id = 'heading-0000-0123456789'
        for args in (('Upper', anchor_id, 'heading'), ('abstract', 'bad-anchor', 'heading'),
                     ('abstract', anchor_id, 'image')):
            with self.subTest(args=args), self.assertRaises(TemplateMappingError):
                AnchorBinding(*args)
        valid = AnchorBinding('abstract', anchor_id, 'heading')
        with self.assertRaises(TemplateMappingError):
            TemplateMapping('bad-digest', (valid,))
        with self.assertRaises(TemplateMappingError):
            TemplateMapping(self.inspection.template_sha256, ())
        with self.assertRaises(TemplateMappingError):
            TemplateMapping(self.inspection.template_sha256, (object(),))
        with self.assertRaises(TemplateMappingError):
            TemplateMapping(self.inspection.template_sha256, (valid, valid))

    def test_mapping_paths_reject_invalid_version_and_wrong_root_type(self):
        mapping = TemplateMapping.from_selection(self.inspection, self._selection())
        with self.assertRaises(TemplateMappingError):
            mapping.save(self.project, version=True)
        with self.assertRaises(TemplateMappingError):
            TemplateMapping.load(self.project, version=10000)
        with self.assertRaises(TemplateMappingError):
            mapping.save('not-a-path')


if __name__ == '__main__':
    unittest.main()