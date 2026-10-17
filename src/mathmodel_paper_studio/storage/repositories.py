"""Parameterized project index and optimistic run-summary persistence."""
import sqlite3
from mathmodel_paper_studio.domain.models import Project, Run, RunStatus, identifier, canonical_directory_path, sha256_digest
from mathmodel_paper_studio.storage.database import Database


class RecordConflictError(ValueError):
    pass


def _project(row) -> Project | None:
    return None if row is None else Project(row['project_id'], row['title'], row['relative_path'])


def _run(row) -> Run | None:
    return None if row is None else Run(row['run_id'], row['project_id'], row['provider'],
                                        row['status'], row['relative_path'], row['checkpoint_sha256'])


class ProjectRepository:
    def __init__(self, database: Database):
        self.database = database

    def add(self, project: Project) -> None:
        if not isinstance(project, Project):
            raise TypeError('需要 Project 记录。')
        try:
            with self.database.connect() as connection:
                connection.execute('INSERT INTO projects (project_id, title, relative_path, path_key) VALUES (?, ?, ?, ?)',
                                   (project.project_id, project.title, project.relative_path, canonical_directory_path(project.relative_path, 'projects', project.project_id)))
        except sqlite3.IntegrityError as exc:
            raise RecordConflictError('项目标识符或目录已经存在，请使用新项目。') from exc

    def get(self, project_id: str) -> Project | None:
        identifier(project_id)
        with self.database.connect() as connection:
            return _project(connection.execute('SELECT * FROM projects WHERE project_id = ?', (project_id,)).fetchone())

    def list_all(self) -> tuple[Project, ...]:
        with self.database.connect() as connection:
            return tuple(_project(row) for row in connection.execute('SELECT * FROM projects ORDER BY project_id'))


class RunRepository:
    def __init__(self, database: Database):
        self.database = database

    def add(self, run: Run) -> None:
        if not isinstance(run, Run):
            raise TypeError('需要 Run 记录。')
        try:
            with self.database.connect() as connection:
                connection.execute('''INSERT INTO runs
                    (run_id, project_id, provider, status, relative_path, checkpoint_sha256, path_key)
                    VALUES (?, ?, ?, ?, ?, ?, ?)''',
                    (run.run_id, run.project_id, run.provider.value, run.status.value,
                     run.relative_path, run.checkpoint_sha256, canonical_directory_path(run.relative_path, '90_runs', run.run_id)))
        except sqlite3.IntegrityError as exc:
            raise RecordConflictError('运行标识符或目录冲突，或所属项目不存在。') from exc

    def get(self, run_id: str) -> Run | None:
        identifier(run_id)
        with self.database.connect() as connection:
            return _run(connection.execute('SELECT * FROM runs WHERE run_id = ?', (run_id,)).fetchone())

    def list_for_project(self, project_id: str) -> tuple[Run, ...]:
        identifier(project_id)
        with self.database.connect() as connection:
            return tuple(_run(row) for row in connection.execute(
                'SELECT * FROM runs WHERE project_id = ? ORDER BY run_id', (project_id,)))

    def update(self, run: Run, *, expected_status: RunStatus) -> None:
        expected = RunStatus(expected_status)
        with self.database.connect() as connection:
            connection.execute('BEGIN IMMEDIATE')
            old = _run(connection.execute('SELECT * FROM runs WHERE run_id = ?', (run.run_id,)).fetchone())
            if old is None or old.status != expected:
                raise RecordConflictError('运行状态已变化，请重新读取后再操作。')
            if (old.project_id, old.provider, old.relative_path) != (run.project_id, run.provider, run.relative_path):
                raise RecordConflictError('运行归属、Agent 或目录不能修改。')
            transitioned = old.transition(run.status, checkpoint_sha256=run.checkpoint_sha256)
            changed = connection.execute('''UPDATE runs SET status = ?, checkpoint_sha256 = ?
                WHERE run_id = ? AND status = ?''',
                (transitioned.status.value, transitioned.checkpoint_sha256, run.run_id, expected.value)).rowcount
            if changed != 1:
                raise RecordConflictError('运行状态冲突，请重新读取后再操作。')

    def commit_checkpoint(self, run_id: str, checkpoint_sha256: str, *,
                         expected_status: RunStatus, expected_checkpoint_sha256: str | None,
                         target_status: RunStatus) -> None:
        """Atomically compare the run pointer and commit status plus checkpoint hash."""
        identifier(run_id)
        sha256_digest(checkpoint_sha256)
        expected = RunStatus(expected_status)
        target = RunStatus(target_status)
        if expected_checkpoint_sha256 is not None:
            sha256_digest(expected_checkpoint_sha256)
        with self.database.connect() as connection:
            connection.execute('BEGIN IMMEDIATE')
            old = _run(connection.execute('SELECT * FROM runs WHERE run_id = ?', (run_id,)).fetchone())
            if (old is None or old.status is not expected
                    or old.checkpoint_sha256 != expected_checkpoint_sha256):
                raise RecordConflictError('运行状态或检查点指针已变化，请重新读取后再操作。')
            if target is not expected:
                old.transition(target, checkpoint_sha256=checkpoint_sha256)
            changed = connection.execute('''UPDATE runs SET status = ?, checkpoint_sha256 = ?
                WHERE run_id = ? AND status = ? AND checkpoint_sha256 IS ?''',
                (target.value, checkpoint_sha256, run_id, expected.value,
                 expected_checkpoint_sha256)).rowcount
            if changed != 1:
                raise RecordConflictError('检查点提交冲突，请重新读取后再操作。')