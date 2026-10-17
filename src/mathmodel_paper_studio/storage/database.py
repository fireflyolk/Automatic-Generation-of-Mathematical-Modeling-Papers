"""Small versioned SQLite store restricted to AppPaths.database."""
from contextlib import contextmanager
import sqlite3

from mathmodel_paper_studio.bootstrap.paths import AppPaths, validate_managed_path
from mathmodel_paper_studio.domain.models import canonical_directory_path

SCHEMA_VERSION = 5


class SchemaVersionError(RuntimeError):
    pass


def _create_v4_tables(connection) -> None:
    connection.execute('''CREATE TABLE projects_v4 (
        project_id TEXT PRIMARY KEY NOT NULL,
        title TEXT NOT NULL,
        relative_path TEXT NOT NULL,
        path_key TEXT NOT NULL UNIQUE CHECK(path_key = canonical_directory_path(relative_path, 'projects', project_id))
    )''')
    connection.execute('''CREATE TABLE runs_v4 (
        run_id TEXT PRIMARY KEY NOT NULL,
        project_id TEXT NOT NULL REFERENCES projects_v4(project_id),
        provider TEXT NOT NULL CHECK(provider IN ('codex', 'claude')),
        status TEXT NOT NULL CHECK(status IN
          ('created', 'running', 'paused', 'completed', 'failed', 'cancelled')),
        relative_path TEXT NOT NULL,
        checkpoint_sha256 TEXT,
        path_key TEXT NOT NULL CHECK(path_key = canonical_directory_path(relative_path, '90_runs', run_id)),
        UNIQUE(project_id, path_key)
    )''')


def _create_v5_tables(connection) -> None:
    connection.execute('''CREATE TABLE projects_v5 (
        project_id TEXT PRIMARY KEY NOT NULL,
        title TEXT NOT NULL,
        relative_path TEXT NOT NULL,
        path_key TEXT NOT NULL UNIQUE CHECK(path_key = canonical_directory_path(relative_path, 'projects', project_id))
    )''')
    connection.execute('''CREATE TABLE runs_v5 (
        run_id TEXT PRIMARY KEY NOT NULL,
        project_id TEXT NOT NULL REFERENCES projects_v5(project_id),
        provider TEXT NOT NULL CHECK(provider IN ('codex', 'claude', 'deepseek')),
        status TEXT NOT NULL CHECK(status IN
          ('created', 'running', 'paused', 'completed', 'failed', 'cancelled')),
        relative_path TEXT NOT NULL,
        checkpoint_sha256 TEXT,
        path_key TEXT NOT NULL CHECK(path_key = canonical_directory_path(relative_path, '90_runs', run_id)),
        UNIQUE(project_id, path_key)
    )''')


def _upgrade_to_v4(connection, previous_version: int) -> None:
    _create_v4_tables(connection)
    if previous_version in (1, 2, 3):
        connection.execute('''INSERT INTO projects_v4
            (project_id, title, relative_path, path_key)
            SELECT project_id, title, relative_path, canonical_directory_path(relative_path, 'projects', project_id) FROM projects''')
        connection.execute('''INSERT INTO runs_v4
            (run_id, project_id, provider, status, relative_path, checkpoint_sha256, path_key)
            SELECT run_id, project_id, provider, status, relative_path,
                   checkpoint_sha256, canonical_directory_path(relative_path, '90_runs', run_id) FROM runs''')
        connection.execute('DROP TABLE runs')
        connection.execute('DROP TABLE projects')
    connection.execute('ALTER TABLE projects_v4 RENAME TO projects')
    connection.execute('ALTER TABLE runs_v4 RENAME TO runs')
    connection.execute('CREATE INDEX runs_project_id ON runs(project_id)')
    connection.execute('PRAGMA user_version = 4')


def _upgrade_to_v5(connection) -> None:
    """Allow the new DeepSeek provider while preserving all existing records."""
    _create_v5_tables(connection)
    connection.execute('''INSERT INTO projects_v5
        (project_id, title, relative_path, path_key)
        SELECT project_id, title, relative_path,
               canonical_directory_path(relative_path, 'projects', project_id) FROM projects''')
    connection.execute('''INSERT INTO runs_v5
        (run_id, project_id, provider, status, relative_path, checkpoint_sha256, path_key)
        SELECT run_id, project_id, provider, status, relative_path, checkpoint_sha256,
               canonical_directory_path(relative_path, '90_runs', run_id) FROM runs''')
    connection.execute('DROP TABLE runs')
    connection.execute('DROP TABLE projects')
    connection.execute('ALTER TABLE projects_v5 RENAME TO projects')
    connection.execute('ALTER TABLE runs_v5 RENAME TO runs')
    connection.execute('CREATE INDEX runs_project_id ON runs(project_id)')
    connection.execute('PRAGMA user_version = 5')


class Database:
    def __init__(self, paths: AppPaths):
        if not isinstance(paths, AppPaths):
            raise TypeError('数据库路径必须来自经过验证的 AppPaths。')
        self.paths = paths

    @contextmanager
    def connect(self):
        self.paths.ensure_directories()
        validate_managed_path(self.paths.database)
        connection = sqlite3.connect(str(self.paths.database), timeout=5)
        try:
            connection.row_factory = sqlite3.Row
            connection.create_function('canonical_directory_path', 3, canonical_directory_path, deterministic=True)
            connection.execute('PRAGMA foreign_keys = ON')
            with connection:
                yield connection
        finally:
            connection.close()

    def migrate(self) -> None:
        try:
            with self.connect() as connection:
                connection.execute('BEGIN IMMEDIATE')
                version = connection.execute('PRAGMA user_version').fetchone()[0]
                if version > SCHEMA_VERSION:
                    raise SchemaVersionError('数据库版本较新，请使用兼容的应用版本。')
                if version < 4:
                    _upgrade_to_v4(connection, version)
                    version = 4
                if version < 5:
                    _upgrade_to_v5(connection)
        except (sqlite3.IntegrityError, sqlite3.OperationalError, ValueError) as exc:
            raise SchemaVersionError('数据库迁移失败，存在非规范目录、目录冲突或无效旧记录；原事务已回滚。') from exc
