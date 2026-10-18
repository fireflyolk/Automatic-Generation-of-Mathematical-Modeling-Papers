from __future__ import annotations

import hashlib
from pathlib import Path

from mathmodel_paper_studio.agents.preflight import Provider
from mathmodel_paper_studio.bootstrap.paths import AppPaths
from mathmodel_paper_studio.domain.models import Project, Run, RunStatus, SourceFile, Stage, StageStatus
from mathmodel_paper_studio.projects.layout import create_layout
from mathmodel_paper_studio.storage.database import Database
from mathmodel_paper_studio.storage.repositories import ProjectRepository, RunRepository
from mathmodel_paper_studio.execution.python_runner import RunArtifactLayout
from mathmodel_paper_studio.workflow.checkpoint import CheckpointStore
from mathmodel_paper_studio.workflow.engine import WorkflowEngine
from mathmodel_paper_studio.workflow.progress import ProgressKind, RunProgressReporter


def make_engine(tmp_path):
    paths = AppPaths(tmp_path / 'app')
    paths.ensure_directories()
    database = Database(paths)
    database.migrate()
    projects, runs = ProjectRepository(database), RunRepository(database)
    project = Project('project1', 'fixture', 'projects/project1')
    projects.add(project)
    project_root = paths.root / project.relative_path
    project_root.mkdir(parents=True)
    create_layout(project_root)
    files = (
        ('problem1', '00_inputs/problem.txt', 'problem', 'Fit observations.'),
        ('csv1', '00_inputs/data.csv', 'csv', 'x,y\n1,2\n'),
    )
    sources = []
    for source_id, relative, kind, text in files:
        path = project_root / relative
        path.write_text(text, encoding='utf-8')
        sources.append(SourceFile(source_id, project.project_id, relative,
                                  hashlib.sha256(path.read_bytes()).hexdigest(),
                                  path.stat().st_size, kind))
    run = Run('run1', project.project_id, Provider.CLAUDE, RunStatus.CREATED, '90_runs/run1')
    runs.add(run)
    engine = WorkflowEngine(paths, projects, runs)
    return engine, paths, projects, runs, project, run, tuple(sources), ('problem1', 'csv1')


def persisted_state(paths, runs, project, run):
    current = runs.get(run.run_id)
    layout = RunArtifactLayout(paths, project, current)
    return CheckpointStore(layout, runs).recover(current)


def test_stage_pause_event_follows_checkpoint_commit(tmp_path):
    engine, paths, _, runs, project, run, sources, selected = make_engine(tmp_path)
    events = []
    persisted = []

    def sink(event):
        events.append(event)
        if event.kind is ProgressKind.STAGE_PAUSED:
            state = persisted_state(paths, runs, project, run)
            persisted.append(runs.get(run.run_id).status is RunStatus.PAUSED
                             and state.stage_records[-1].status is StageStatus.PAUSED)

    reporter = RunProgressReporter(run.run_id, sink)
    state = engine.start(project.project_id, run.run_id, sources, selected, progress=reporter)

    assert state.status is RunStatus.PAUSED
    assert [event.kind for event in events] == [
        ProgressKind.RUN_STARTED, ProgressKind.STAGE_STARTED,
        ProgressKind.STAGE_PAUSED, ProgressKind.RUN_PAUSED,
    ]
    assert persisted == [True]
    assert events[1].stage is Stage.PREFLIGHT


def test_cancel_reports_only_after_cancel_checkpoint_commit(tmp_path):
    engine, paths, _, runs, project, run, sources, selected = make_engine(tmp_path)
    events = []
    persisted = []

    def sink(event):
        events.append(event)
        if event.kind is ProgressKind.RUN_CANCELLED:
            state = persisted_state(paths, runs, project, run)
            persisted.append(runs.get(run.run_id).status is RunStatus.CANCELLED
                             and state.status is RunStatus.CANCELLED)

    reporter = RunProgressReporter(run.run_id, sink)
    engine.start(project.project_id, run.run_id, sources, selected, progress=reporter)
    cancelled = engine.cancel(run.run_id, progress=reporter)

    assert cancelled.status is RunStatus.CANCELLED
    assert persisted == [True]
    assert events[-1].kind is ProgressKind.RUN_CANCELLED


def test_observer_failure_does_not_change_checkpoint_or_stage_result(tmp_path):
    engine, paths, _, runs, project, run, sources, selected = make_engine(tmp_path)

    def broken_sink(_event):
        raise RuntimeError('display observer failed')

    state = engine.start(
        project.project_id, run.run_id, sources, selected,
        progress=RunProgressReporter(run.run_id, broken_sink),
    )
    recovered = persisted_state(paths, runs, project, run)

    assert state.status is RunStatus.PAUSED
    assert recovered.status is state.status
    assert recovered.stage_records == state.stage_records
