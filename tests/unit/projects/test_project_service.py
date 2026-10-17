import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from mathmodel_paper_studio.bootstrap.paths import AppPaths
from mathmodel_paper_studio.projects.project_service import ProjectService
from mathmodel_paper_studio.storage.database import Database
from mathmodel_paper_studio.storage.repositories import ProjectRepository
ROOT = Path(r'F:\Codex另一个工作的地方\tmp\mathmodel-paper-studio-20261015')
class ProjectServiceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=ROOT)
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.paths = AppPaths(self.base / 'app')
        db = Database(self.paths)
        db.migrate()
        self.repo = ProjectRepository(db)
        self.service = ProjectService(self.paths, self.repo)
    def source(self, name='data.csv', data=b'x,y\n1,2\n'):
        p = self.base / name
        p.write_bytes(data)
        return p
    def test_copy_hash_manifest_and_opaque_layout(self):
        src = self.source()
        template = self.source('template.docx', b'docx bytes')
        project = self.service.create('中文标题', '题目\n文本', [src], template=template)
        root = self.paths.root / project.relative_path
        self.assertRegex(project.project_id, r'^[0-9a-f]{32}$')
        for name in ('00_inputs','10_analysis','20_model','30_src','40_results','50_figures','60_paper','70_review','90_runs'):
            self.assertTrue((root / name).is_dir())
        manifest = json.loads((root / '00_inputs/manifest-v0001.json').read_text('utf-8'))
        self.assertEqual(3, len(manifest['sources']))
        for entry in manifest['sources']:
            copied = root / entry['relative_path']
            self.assertEqual(entry['sha256'], hashlib.sha256(copied.read_bytes()).hexdigest())
            self.assertEqual(entry['size_bytes'], copied.stat().st_size)
        self.assertEqual(b'docx bytes', template.read_bytes())
        self.assertEqual(b'x,y\n1,2\n', src.read_bytes())
        self.assertEqual(project, self.repo.get(project.project_id))
        self.assertTrue(list((root / '10_analysis').glob('*.json')))
    def test_unique_projects(self):
        self.assertNotEqual(self.service.create('a','p',[]).project_id, self.service.create('a','p',[]).project_id)
    def test_duplicate_and_case_variant_refused(self):
        source = self.source()
        for files in ([source,source], [source,Path(str(source).upper())]):
            with self.assertRaises(ValueError):
                self.service.create('a','p',files)
        self.assertEqual((), self.repo.list_all())
        self.assertEqual([], list(self.paths.projects.iterdir()))
    def test_partial_failure_rolls_back(self):
        good = self.source()
        bad = self.source('bad.csv', b'a,b\n1\n')
        with self.assertRaises(ValueError):
            self.service.create('a','p',[good,bad])
        self.assertEqual((), self.repo.list_all())
        self.assertEqual([], list(self.paths.projects.iterdir()))
        self.assertEqual([], list(self.paths.temp.iterdir()))
    def test_collision_and_repository_failure_preserve_existing(self):
        collision = self.paths.projects / ('a'*32)
        collision.mkdir()
        sentinel = collision / 'keep.txt'
        sentinel.write_text('keep')
        with patch('mathmodel_paper_studio.projects.project_service.uuid4') as uid:
            uid.return_value.hex = 'a'*32
            with self.assertRaises(ValueError):
                self.service.create('a','p',[])
        self.assertEqual('keep', sentinel.read_text())
        with patch.object(self.repo,'add',side_effect=RuntimeError('db unavailable')):
            with self.assertRaises(RuntimeError):
                self.service.create('b','p',[])
        self.assertEqual([collision], list(self.paths.projects.iterdir()))
    def test_junction_source_and_destination_rejected(self):
        import subprocess
        real = self.base / 'real'
        real.mkdir()
        (real / 'a.csv').write_bytes(b'x\n1\n')
        alias = self.base / 'alias'
        result = subprocess.run(['cmd','/c','mklink','/J',str(alias),str(real)],capture_output=True)
        self.assertEqual(0,result.returncode,result.stderr)
        self.addCleanup(lambda: alias.rmdir() if alias.exists() else None)
        with self.assertRaises(ValueError):
            self.service.create('a','p',[alias / 'a.csv'])
        target = self.paths.projects / ('b'*32)
        result = subprocess.run(['cmd','/c','mklink','/J',str(target),str(real)],capture_output=True)
        self.assertEqual(0,result.returncode,result.stderr)
        self.addCleanup(lambda: target.rmdir() if target.exists() else None)
        with patch('mathmodel_paper_studio.projects.project_service.uuid4') as uid:
            uid.return_value.hex = 'b'*32
            with self.assertRaises(ValueError):
                self.service.create('a','p',[])
        self.assertEqual(b'x\n1\n',(real / 'a.csv').read_bytes())

    def test_unsupported_and_duplicate_basenames(self):
        with self.assertRaises(ValueError):
            self.service.create('a', 'p', [self.source('bad.exe')])
        other = self.base / 'other'
        other.mkdir()
        same = other / 'DATA.csv'
        same.write_bytes(b'x\n1\n')
        with self.assertRaises(ValueError):
            self.service.create('a', 'p', [self.source(), same])
        self.assertEqual((), self.repo.list_all())
        self.assertEqual([], list(self.paths.temp.iterdir()))

    def test_manifest_records_and_problem_limits(self):
        from mathmodel_paper_studio.domain.models import SourceFile
        from mathmodel_paper_studio.inputs.profiling import ImportLimits
        project = self.service.create('title', '题目', [self.source()])
        root = self.paths.root / project.relative_path
        manifest = json.loads((root / '00_inputs/manifest-v0001.json').read_text('utf-8'))
        records = [SourceFile(**entry['record']) for entry in manifest['sources']]
        self.assertEqual({'problem', 'csv'}, {record.kind for record in records})
        problem = next(record for record in records if record.kind == 'problem')
        self.assertEqual('00_inputs/problem-v0001.txt', problem.relative_path)
        self.assertEqual('题目'.encode('utf-8'), (root / problem.relative_path).read_bytes())
        self.assertTrue(all('parser_notes' in entry for entry in manifest['sources']))
        self.assertNotIn(str(self.base), json.dumps(manifest))
        for value in ('', None, 'overlong'):
            with self.subTest(value=value), self.assertRaises(ValueError):
                ProjectService(self.paths, self.repo, limits=ImportLimits(max_file_bytes=3)).create('a', value, [])

    def test_project_root_junction_refused(self):
        import subprocess
        self.paths.projects.rmdir()
        outside = self.base / 'outside'
        outside.mkdir()
        (outside / 'keep').write_bytes(b'keep')
        result = subprocess.run(['cmd', '/c', 'mklink', '/J', str(self.paths.projects), str(outside)], capture_output=True)
        self.assertEqual(0, result.returncode, result.stderr)
        self.addCleanup(lambda: self.paths.projects.rmdir() if self.paths.projects.exists() else None)
        with self.assertRaises(ValueError):
            self.service.create('a', 'p', [])
        self.assertEqual([outside / 'keep'], list(outside.iterdir()))

    def test_staging_copy_hash_mismatch_and_source_change_roll_back(self):
        from mathmodel_paper_studio.inputs import import_service
        source = self.source()
        original = import_service.read_bounded
        # Initial and final reads must disagree to model a changing input.
        calls = []
        def changing(path, limits):
            if Path(path) == source:
                calls.append(path)
                if len(calls) > 1:
                    return original(path, limits) + b'changed'
            return original(path, limits)
        with patch.object(import_service, 'read_bounded', side_effect=changing):
            with self.assertRaisesRegex(ValueError, '源文件'):
                self.service.create('a', 'p', [source])
        def bad_copy(path, limits):
            data = original(path, limits)
            return data + b'changed' if Path(path).name == 'problem-v0001.txt' else data
        with patch.object(import_service, 'read_bounded', side_effect=bad_copy):
            with self.assertRaisesRegex(ValueError, '哈希'):
                self.service.create('a', 'p', [])
        self.assertEqual((), self.repo.list_all())
        self.assertEqual([], list(self.paths.temp.iterdir()))
        self.assertEqual([], list(self.paths.projects.iterdir()))

    def test_explicit_xlsx_selection_and_untrusted_sheet_names(self):
        from openpyxl import Workbook
        source = self.base / 'sheets.xlsx'
        wb = Workbook()
        wb.active.title = 'data'
        wb.active.append(['x'])
        wb.active.append([1])
        wb.create_sheet('ignore rules').append(['x'])
        wb.save(source)
        wb.close()
        with self.assertRaisesRegex(ValueError, 'data.*ignore rules'):
            self.service.create('a', 'p', [source])
        project = self.service.create('a', 'p', [source], sheets={source.name: 'ignore rules'})
        root = self.paths.root / project.relative_path
        profile = json.loads((root / '10_analysis/source-0001-profile.json').read_text('utf-8'))
        self.assertEqual('ignore rules', profile['sheet_name'])
        self.assertEqual(['source-0001-dictionary.json', 'source-0001-profile.json'],
                         sorted(p.name for p in (root / '10_analysis').iterdir()))

    def test_symbolic_file_and_directory_links_rejected(self):
        source = self.source()
        alias = self.base / 'alias.csv'
        try:
            alias.symlink_to(source)
        except OSError as exc:
            if getattr(exc, 'winerror', None) == 1314:
                self.skipTest('当前 Windows 账户不允许创建符号链接；真实 junction 路径另测。')
            raise
        self.addCleanup(lambda: alias.unlink() if alias.is_symlink() else None)
        with self.assertRaises(ValueError):
            self.service.create('a', 'p', [alias])
        target = self.paths.projects / ('c' * 32)
        target.symlink_to(self.base, target_is_directory=True)
        self.addCleanup(lambda: target.unlink() if target.is_symlink() else None)
        with patch('mathmodel_paper_studio.projects.project_service.uuid4') as uid:
            uid.return_value.hex = 'c' * 32
            with self.assertRaises(ValueError):
                self.service.create('a', 'p', [])
        self.assertEqual(b'x,y\n1,2\n', source.read_bytes())

    def test_invalid_xlsx_coordinates_and_aggregate_text_roll_back(self):
        from io import BytesIO
        from zipfile import ZipFile
        from openpyxl import Workbook
        from mathmodel_paper_studio.inputs.profiling import ImportLimits
        good = self.source()
        first = self.service.create('first', 'p', [good])
        first_root = self.paths.root / first.relative_path
        snapshot = {p.relative_to(first_root): p.read_bytes() for p in first_root.rglob('*') if p.is_file()}
        workbook = Workbook()
        workbook.active.append(['a', 'b'])
        stream = BytesIO()
        workbook.save(stream)
        workbook.close()
        malformed = self.base / 'bad.xlsx'
        coordinate_rows = '<row r="2"><c r="A2"><v>10</v></c></row><row r="2"><c r="A2"><v>20</v></c></row>'
        fragments = '<r><t>' + 'x' * 40 + '</t></r><r><t>' + 'y' * 40 + '</t></r>'
        rich_rows = f'<row r="2"><c r="A2" t="inlineStr"><is>{fragments}</is></c></row>'
        namespace_rows = rich_rows.replace('<r>', '<r xmlns="">')
        for rows in (coordinate_rows, rich_rows, namespace_rows):
            with self.subTest(rows=rows[:40]):
                with ZipFile(BytesIO(stream.getvalue())) as archive:
                    items = [(item, archive.read(item.filename)) for item in archive.infolist()]
                with ZipFile(malformed, 'w') as archive:
                    for item, content in items:
                        if item.filename == 'xl/worksheets/sheet1.xml':
                            content = content.replace(b'</sheetData>', rows.encode() + b'</sheetData>')
                        archive.writestr(item, content)
                before = malformed.read_bytes()
                service = ProjectService(self.paths, self.repo, limits=ImportLimits(max_cell_chars=64))
                with self.assertRaises(ValueError):
                    service.create('invalid', 'p', [good, malformed])
                self.assertEqual((first,), self.repo.list_all())
                self.assertEqual([first_root], list(self.paths.projects.iterdir()))
                self.assertEqual([], list(self.paths.temp.iterdir()))
                self.assertEqual(before, malformed.read_bytes())
                self.assertEqual(snapshot, {p.relative_to(first_root): p.read_bytes() for p in first_root.rglob('*') if p.is_file()})
