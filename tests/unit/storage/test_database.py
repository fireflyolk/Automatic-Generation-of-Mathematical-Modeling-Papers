from pathlib import Path
import sqlite3
import unittest
import uuid

from mathmodel_paper_studio.bootstrap.paths import AppPaths, APPROVED_BASE, PathPolicyError
from mathmodel_paper_studio.domain.models import Project, Run, RunStatus, Provider
from mathmodel_paper_studio.storage.database import Database, SchemaVersionError
from mathmodel_paper_studio.storage.repositories import ProjectRepository, RunRepository, RecordConflictError


class DatabaseTests(unittest.TestCase):
    def setUp(self):
        self.paths = AppPaths(Path(APPROVED_BASE) / 'tmp/mathmodel-paper-studio-20261015' / ('db-' + uuid.uuid4().hex))
        self.db = Database(self.paths)
        self.db.migrate()
        self.projects = ProjectRepository(self.db)
        self.runs = RunRepository(self.db)

    def test_migration_is_repeatable_and_enables_foreign_keys(self):
        self.db.migrate()
        with self.db.connect() as connection:
            self.assertEqual(connection.execute('PRAGMA user_version').fetchone()[0], 5)
            self.assertEqual(connection.execute('PRAGMA foreign_keys').fetchone()[0], 1)
            tables = {r[0] for r in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            self.assertTrue({'projects', 'runs'}.issubset(tables))
        self.assertTrue(self.paths.database.is_file())
        self.assertTrue(self.paths.database.is_relative_to(Path(APPROVED_BASE)))
        with self.assertRaises(TypeError):
            Database(Path('C:/unsafe.db'))

    def test_rejects_newer_database_version(self):
        with self.db.connect() as connection:
            connection.execute('PRAGMA user_version = 99')
        with self.assertRaises(SchemaVersionError):
            self.db.migrate()
        with self.db.connect() as connection:
            self.assertEqual(connection.execute('PRAGMA user_version').fetchone()[0], 99)

    def test_roundtrip_parameterization_and_duplicate_conflict(self):
        project = Project('p_1', "a'); DROP TABLE projects; --", 'projects/p_1')
        self.projects.add(project)
        self.assertEqual(self.projects.get('p_1'), project)
        self.assertEqual(self.projects.list_all(), (project,))
        self.assertIsNone(self.projects.get('absent'))
        with self.assertRaises(RecordConflictError):
            self.projects.add(project)
        run = Run('r_1', project.project_id, Provider.CLAUDE, RunStatus.CREATED, '90_runs/r_1')
        self.runs.add(run)
        self.assertEqual(self.runs.get(run.run_id), run)
        self.assertEqual(self.runs.list_for_project('p_1'), (run,))
        with self.assertRaises(RecordConflictError):
            self.runs.add(run)
        with self.assertRaises(RecordConflictError):
            self.runs.add(Run('r_2', 'missing', Provider.CODEX, RunStatus.CREATED, '90_runs/r_2'))

    def test_deepseek_provider_roundtrips_in_runs_table(self):
        project = Project('deepseek_project', 'DeepSeek', 'projects/deepseek_project')
        self.projects.add(project)
        run = Run('deepseek_run', project.project_id, Provider.DEEPSEEK,
                  RunStatus.CREATED, '90_runs/deepseek_run')
        self.runs.add(run)
        self.assertEqual(self.runs.get(run.run_id), run)

    def test_v4_migration_adds_deepseek_and_preserves_existing_rows(self):
        legacy = self._v2_database(version=4)
        legacy.migrate()
        self.assertEqual(RunRepository(legacy).get('old_run').provider, Provider.CLAUDE)
        project = ProjectRepository(legacy).get('legacy')
        deepseek_run = Run('deepseek_run', project.project_id, Provider.DEEPSEEK,
                           RunStatus.CREATED, '90_runs/deepseek_run')
        RunRepository(legacy).add(deepseek_run)
        self.assertEqual(RunRepository(legacy).get(deepseek_run.run_id), deepseek_run)
        with legacy.connect() as connection:
            self.assertEqual(connection.execute('PRAGMA user_version').fetchone()[0], 5)

    def test_transactional_run_update_rejects_stale_or_illegal_transitions(self):
        self.projects.add(Project('p', 'project', 'projects/p'))
        original = Run('r', 'p', Provider.CLAUDE, RunStatus.CREATED, '90_runs/r')
        self.runs.add(original)
        running = original.transition(RunStatus.RUNNING)
        self.runs.update(running, expected_status=RunStatus.CREATED)
        self.assertEqual(self.runs.get('r'), running)
        with self.assertRaises(RecordConflictError):
            self.runs.update(running, expected_status=RunStatus.CREATED)
        with self.assertRaises(ValueError):
            self.runs.update(Run('r', 'p', Provider.CLAUDE, RunStatus.CREATED, '90_runs/r'),
                             expected_status=RunStatus.RUNNING)
        with self.assertRaises(RecordConflictError):
            self.runs.update(Run('r', 'other', Provider.CLAUDE, RunStatus.PAUSED, '90_runs/r'),
                             expected_status=RunStatus.RUNNING)
        with self.db.connect() as connection:
            columns = {r[1] for r in connection.execute('PRAGMA table_info(runs)')}
            self.assertFalse({'prompt', 'credentials', 'auth', 'problem_text'} & columns)


    def test_canonical_directory_keys_roundtrip_without_transforming_display(self):
        project = Project('p', 'Original title', 'projects/p')
        self.projects.add(project)
        run = Run('r', 'p', Provider.CLAUDE, RunStatus.CREATED, '90_runs/r')
        self.runs.add(run)
        self.assertEqual(self.projects.get('p'), project)
        self.assertEqual(self.runs.get('r'), run)
        with self.db.connect() as connection:
            self.assertEqual(connection.execute('SELECT path_key FROM projects').fetchone()[0], project.relative_path)
            self.assertEqual(connection.execute('SELECT path_key FROM runs').fetchone()[0], run.relative_path)
        with self.assertRaises(RecordConflictError):
            self.projects.add(project)
        with self.assertRaises(RecordConflictError):
            self.runs.add(run)

    def test_direct_sql_rejects_uppercase_nonascii_and_wrong_directory_prefix(self):
        self.projects.add(Project('p', 'Project', 'projects/p'))
        project_paths = ('Projects/alpha', 'projects/Alpha', 'projects/ı', 'projects/ärea', '90_runs/alpha')
        run_paths = ('90_RUNS/alpha', '90_runs/Alpha', '90_runs/ı', '90_runs/ärea', 'projects/alpha')
        with self.db.connect() as connection:
            for index, path in enumerate(project_paths):
                with self.subTest(path=path), self.assertRaises((sqlite3.IntegrityError, sqlite3.OperationalError)):
                    connection.execute('INSERT INTO projects (project_id, title, relative_path, path_key) VALUES (?, ?, ?, ?)',
                                       (f'badp{index}', 'Bad', path, path.casefold()))
            for index, path in enumerate(run_paths):
                with self.subTest(path=path), self.assertRaises((sqlite3.IntegrityError, sqlite3.OperationalError)):
                    connection.execute('''INSERT INTO runs
                        (run_id, project_id, provider, status, relative_path, checkpoint_sha256, path_key)
                        VALUES (?, ?, ?, ?, ?, ?, ?)''',
                        (f'badr{index}', 'p', 'claude', 'created', path, None, path.casefold()))
            self.assertEqual(connection.execute('SELECT COUNT(*) FROM projects').fetchone()[0], 1)
            self.assertEqual(connection.execute('SELECT COUNT(*) FROM runs').fetchone()[0], 0)

    def test_run_update_preserves_existing_checkpoint_when_incoming_is_none(self):
        self.projects.add(Project('p', 'Project', 'projects/p'))
        original = Run('r', 'p', Provider.CLAUDE, RunStatus.CREATED, '90_runs/r', 'a' * 64)
        self.runs.add(original)
        incoming = Run('r', 'p', Provider.CLAUDE, RunStatus.RUNNING, '90_runs/r', None)
        self.runs.update(incoming, expected_status=RunStatus.CREATED)
        self.assertEqual(self.runs.get('r'), original.transition(RunStatus.RUNNING))
        updated = Run('r', 'p', Provider.CLAUDE, RunStatus.PAUSED, '90_runs/r', 'b' * 64)
        self.runs.update(updated, expected_status=RunStatus.RUNNING)
        self.assertEqual(self.runs.get('r'), updated)

    def _legacy_database(self, *, conflict=False):
        legacy = Database(AppPaths(self.paths.root / 'legacy'))
        with legacy.connect() as connection:
            connection.execute('CREATE TABLE projects (project_id TEXT PRIMARY KEY, title TEXT NOT NULL, relative_path TEXT NOT NULL UNIQUE)')
            connection.execute('''CREATE TABLE runs (run_id TEXT PRIMARY KEY,
                project_id TEXT NOT NULL REFERENCES projects(project_id), provider TEXT NOT NULL,
                status TEXT NOT NULL, relative_path TEXT NOT NULL, checkpoint_sha256 TEXT,
                UNIQUE(project_id, relative_path))''')
            connection.execute('INSERT INTO projects VALUES (?, ?, ?)', ('legacy', 'Legacy title', 'projects/legacy'))
            connection.execute('INSERT INTO runs VALUES (?, ?, ?, ?, ?, ?)',
                               ('old_run', 'legacy', 'claude', 'created', '90_runs/old_run', 'a' * 64))
            if conflict:
                connection.execute('INSERT INTO projects VALUES (?, ?, ?)', ('conflict', 'Conflict', 'Projects/LEGACY'))
            connection.execute('PRAGMA user_version = 1')
        return legacy

    def test_v1_migration_preserves_records_and_adds_identity_keys(self):
        legacy = self._legacy_database()
        legacy.migrate()
        legacy.migrate()
        self.assertEqual(ProjectRepository(legacy).get('legacy'),
                         Project('legacy', 'Legacy title', 'projects/legacy'))
        self.assertEqual(RunRepository(legacy).get('old_run').checkpoint_sha256, 'a' * 64)
        with self.assertRaises(RecordConflictError):
            ProjectRepository(legacy).add(Project('legacy', 'Conflict', 'projects/legacy'))
        with legacy.connect() as connection:
            self.assertEqual(connection.execute('PRAGMA user_version').fetchone()[0], 5)
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute('INSERT INTO projects (project_id, title, relative_path, path_key) VALUES (?, ?, ?, ?)',
                                   ('forged', 'Forged', 'projects/forged', 'different-key'))

    def test_v1_migration_conflicts_fail_without_losing_legacy_rows(self):
        legacy = self._legacy_database(conflict=True)
        with self.assertRaises(SchemaVersionError):
            legacy.migrate()
        with legacy.connect() as connection:
            self.assertEqual(connection.execute('PRAGMA user_version').fetchone()[0], 1)
            self.assertEqual(connection.execute('SELECT COUNT(*) FROM projects').fetchone()[0], 2)
            self.assertEqual(connection.execute('SELECT COUNT(*) FROM runs').fetchone()[0], 1)

    def _v2_database(self, *, noncanonical=False, version=2, mismatched=False):
        legacy = Database(AppPaths(self.paths.root / f'legacy-v{version}'))
        project_path = 'Projects/LEGACY' if noncanonical else 'projects/legacy'
        if mismatched:
            project_path = 'projects/other'
        with legacy.connect() as connection:
            connection.create_function('windows_path_key', 1, lambda value: value.casefold(), deterministic=True)
            from mathmodel_paper_studio.domain.models import canonical_directory_path
            connection.create_function('legacy_directory_path', 2, canonical_directory_path, deterministic=True)
            connection.create_function('canonical_directory_path', 3, canonical_directory_path, deterministic=True)
            if version == 2:
                project_key = 'windows_path_key(relative_path)'
                run_key = 'windows_path_key(relative_path)'
            elif version == 4:
                project_key = "canonical_directory_path(relative_path, 'projects', project_id)"
                run_key = "canonical_directory_path(relative_path, '90_runs', run_id)"
            else:
                project_key = "legacy_directory_path(relative_path, 'projects')"
                run_key = "legacy_directory_path(relative_path, '90_runs')"
            connection.execute(f'''CREATE TABLE projects (
                project_id TEXT PRIMARY KEY NOT NULL, title TEXT NOT NULL, relative_path TEXT NOT NULL,
                path_key TEXT NOT NULL UNIQUE CHECK(path_key = {project_key}))''')
            connection.execute(f'''CREATE TABLE runs (
                run_id TEXT PRIMARY KEY NOT NULL, project_id TEXT NOT NULL REFERENCES projects(project_id),
                provider TEXT NOT NULL CHECK(provider IN ('codex', 'claude')),
                status TEXT NOT NULL CHECK(status IN ('created','running','paused','completed','failed','cancelled')),
                relative_path TEXT NOT NULL, checkpoint_sha256 TEXT,
                path_key TEXT NOT NULL CHECK(path_key = {run_key}), UNIQUE(project_id,path_key))''')
            connection.execute('CREATE INDEX runs_project_id ON runs(project_id)')
            connection.execute('INSERT INTO projects VALUES (?, ?, ?, ?)',
                               ('legacy', 'Legacy v2 title', project_path, project_path.casefold()))
            connection.execute('INSERT INTO runs VALUES (?, ?, ?, ?, ?, ?, ?)',
                               ('old_run', 'legacy', 'claude', 'created', '90_runs/old_run', 'b' * 64, '90_runs/old_run'))
            connection.execute(f'PRAGMA user_version = {version}')
        return legacy

    def test_v2_migration_preserves_canonical_records_and_checkpoint(self):
        legacy = self._v2_database()
        legacy.migrate()
        legacy.migrate()
        self.assertEqual(ProjectRepository(legacy).get('legacy'),
                         Project('legacy', 'Legacy v2 title', 'projects/legacy'))
        self.assertEqual(RunRepository(legacy).get('old_run'),
                         Run('old_run', 'legacy', Provider.CLAUDE, RunStatus.CREATED, '90_runs/old_run', 'b' * 64))
        with legacy.connect() as connection:
            self.assertEqual(connection.execute('PRAGMA user_version').fetchone()[0], 5)
            self.assertEqual(list(connection.execute('PRAGMA foreign_key_check')), [])
            self.assertEqual(connection.execute('SELECT path_key FROM projects').fetchone()[0], 'projects/legacy')
            self.assertEqual(connection.execute('SELECT path_key FROM runs').fetchone()[0], '90_runs/old_run')

    def test_v2_migration_noncanonical_path_rolls_back_without_data_loss(self):
        legacy = self._v2_database(noncanonical=True)
        with self.assertRaises(SchemaVersionError):
            legacy.migrate()
        with legacy.connect() as connection:
            self.assertEqual(connection.execute('PRAGMA user_version').fetchone()[0], 2)
            self.assertEqual(connection.execute('SELECT relative_path FROM projects').fetchone()[0], 'Projects/LEGACY')
            self.assertEqual(connection.execute('SELECT checkpoint_sha256 FROM runs').fetchone()[0], 'b' * 64)
            self.assertEqual(list(connection.execute('PRAGMA foreign_key_check')), [])

    def test_direct_sql_rejects_mismatched_path_ids_and_uppercase_ids(self):
        self.projects.add(Project('p', 'Project', 'projects/p'))
        with self.db.connect() as connection:
            for record_id, path in (('other', 'projects/different'), ('UPPER', 'projects/upper')):
                with self.subTest(record_id=record_id), self.assertRaises((sqlite3.IntegrityError, sqlite3.OperationalError)):
                    connection.execute('INSERT INTO projects VALUES (?, ?, ?, ?)',
                                       (record_id, 'Invalid', path, path))
            for record_id, path in (('other', '90_runs/different'), ('UPPER', '90_runs/upper')):
                with self.subTest(record_id=record_id), self.assertRaises((sqlite3.IntegrityError, sqlite3.OperationalError)):
                    connection.execute('INSERT INTO runs VALUES (?, ?, ?, ?, ?, ?, ?)',
                                       (record_id, 'p', 'claude', 'created', path, None, path))

    def test_v3_migration_preserves_matching_ids_and_checkpoint(self):
        legacy = self._v2_database(version=3)
        legacy.migrate()
        self.assertEqual(ProjectRepository(legacy).get('legacy').relative_path, 'projects/legacy')
        self.assertEqual(RunRepository(legacy).get('old_run').checkpoint_sha256, 'b' * 64)
        with legacy.connect() as connection:
            self.assertEqual(connection.execute('PRAGMA user_version').fetchone()[0], 5)
            self.assertEqual(list(connection.execute('PRAGMA foreign_key_check')), [])

    def test_v3_migration_mismatched_id_rolls_back_without_data_loss(self):
        legacy = self._v2_database(version=3, mismatched=True)
        with self.assertRaises(SchemaVersionError):
            legacy.migrate()
        with legacy.connect() as connection:
            self.assertEqual(connection.execute('PRAGMA user_version').fetchone()[0], 3)
            self.assertEqual(connection.execute('SELECT relative_path FROM projects').fetchone()[0], 'projects/other')
            self.assertEqual(connection.execute('SELECT checkpoint_sha256 FROM runs').fetchone()[0], 'b' * 64)

if __name__ == '__main__':
    unittest.main()
