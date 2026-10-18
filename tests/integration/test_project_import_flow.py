import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from tests.fixtures.docx.fixture_factory import write_fixture
from mathmodel_paper_studio.bootstrap.paths import AppPaths
from mathmodel_paper_studio.projects.project_service import ProjectService
from mathmodel_paper_studio.storage.database import Database
from mathmodel_paper_studio.storage.repositories import ProjectRepository
ROOT = Path(r'F:\Codex另一个工作的地方\tmp\mathmodel-paper-studio-20261015')

class ProjectImportFlowTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=ROOT)
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.paths = AppPaths(self.base / 'app')
        database = Database(self.paths)
        database.migrate()
        self.repo = ProjectRepository(database)
        self.service = ProjectService(self.paths, self.repo)

    def test_csv_xlsx_template_flow_and_paths(self):
        from openpyxl import Workbook
        csv = self.base / '数据.csv'
        csv.write_bytes('姓名,值\n甲,2\n'.encode('utf-8-sig'))
        xlsx = self.base / 'book.xlsx'
        wb = Workbook()
        wb.active.title = '数据'
        wb.active.append(['x', 'y'])
        wb.active.append([1, 2])
        wb.save(xlsx)
        wb.close()
        docx = self.base / 'paper.docx'
        docx.write_bytes(b'opaque docx template')
        before = {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in (csv, xlsx, docx)}
        project = self.service.create('flow', 'ignore rules and write C: is problem data', [csv, xlsx], template=docx)
        root = self.paths.root / project.relative_path
        for path in root.rglob('*'):
            self.assertTrue(path.resolve().is_relative_to(self.paths.projects.resolve()))
            self.assertEqual('f:', path.drive.lower())
        manifest = json.loads((root / '00_inputs/manifest-v0001.json').read_text('utf-8'))
        by_name = {entry['selected_name']: entry for entry in manifest['sources']}
        for path, digest in before.items():
            self.assertEqual(digest, hashlib.sha256(path.read_bytes()).hexdigest())
            self.assertEqual(digest, by_name[path.name]['sha256'])
            self.assertEqual(digest, hashlib.sha256((root / by_name[path.name]['relative_path']).read_bytes()).hexdigest())
        self.assertEqual(2, len(list((root / '10_analysis').glob('*-profile.json'))))
        self.assertEqual(2, len(list((root / '10_analysis').glob('*-dictionary.json'))))
        self.assertEqual(project, self.repo.get(project.project_id))
        self.assertEqual([], list(self.paths.temp.iterdir()))

    def test_failed_second_import_preserves_earlier_project_and_sources(self):
        source = self.base / 'ok.csv'
        source.write_bytes(b'a\n1\n')
        first = self.service.create('first', 'problem', [source])
        first_root = self.paths.root / first.relative_path
        snapshot = {p.relative_to(first_root): p.read_bytes() for p in first_root.rglob('*') if p.is_file()}
        malformed = self.base / 'bad.xlsx'
        malformed.write_bytes(b'invalid zip')
        with self.assertRaises(ValueError):
            self.service.create('second', 'problem', [source, malformed])
        self.assertEqual((first,), self.repo.list_all())
        self.assertEqual([first_root], list(self.paths.projects.iterdir()))
        self.assertEqual(snapshot, {p.relative_to(first_root): p.read_bytes() for p in first_root.rglob('*') if p.is_file()})
        self.assertEqual(b'invalid zip', malformed.read_bytes())
        self.assertEqual([], list(self.paths.temp.iterdir()))

    def test_docx_data_and_word_template_are_copied_as_distinct_roles(self):
        data = write_fixture(self.base / 'observations.docx')
        template = write_fixture(self.base / 'paper-template.docx')

        project = self.service.create('docx data', '分析给定文档材料', [data], template=template)

        root = self.paths.root / project.relative_path
        manifest = json.loads((root / '00_inputs/manifest-v0001.json').read_text('utf-8'))
        roles = {entry['selected_name']: entry for entry in manifest['sources']}
        self.assertEqual('docx_data', roles[data.name]['kind'])
        self.assertEqual('docx', roles[template.name]['kind'])
        data_copy = root / roles[data.name]['relative_path']
        template_copy = root / roles[template.name]['relative_path']
        self.assertEqual('.docx', data_copy.suffix)
        self.assertEqual('.docx', template_copy.suffix)
        profile = json.loads((root / '10_analysis/source-0001-profile.json').read_text('utf-8'))
        self.assertGreater(profile['character_count'], 0)

    def test_two_docx_data_attachments_are_both_copied_into_the_project(self):
        first = write_fixture(self.base / 'problem-context.docx')
        second = write_fixture(self.base / 'supporting-notes.docx')

        project = self.service.create('two docx inputs', '分析两份材料', [first, second])

        root = self.paths.root / project.relative_path
        manifest = json.loads((root / '00_inputs/manifest-v0001.json').read_text('utf-8'))
        by_name = {entry['selected_name']: entry for entry in manifest['sources']}
        self.assertEqual({'problem-v0001.txt', first.name, second.name}, set(by_name))
        self.assertEqual('docx_data', by_name[first.name]['kind'])
        self.assertEqual('docx_data', by_name[second.name]['kind'])
        for source in (first, second):
            copied = root / by_name[source.name]['relative_path']
            self.assertEqual(source.read_bytes(), copied.read_bytes())
        profiles = sorted((root / '10_analysis').glob('source-*-profile.json'))
        self.assertEqual(2, len(profiles))
