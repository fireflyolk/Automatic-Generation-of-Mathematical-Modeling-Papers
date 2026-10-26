import hashlib
import os
from pathlib import Path
import subprocess
import threading
import sys
import tempfile
import textwrap
import unittest

from mathmodel_paper_studio.agents.preflight import Provider
from mathmodel_paper_studio.bootstrap.paths import AppPaths
from mathmodel_paper_studio.domain.models import Project, Run, RunStatus, SourceFile, Stage, StageStatus
from mathmodel_paper_studio.projects.layout import create_layout
from mathmodel_paper_studio.storage.database import Database
from mathmodel_paper_studio.storage.repositories import ProjectRepository, RecordConflictError, RunRepository
from mathmodel_paper_studio.execution.python_runner import RunArtifactLayout
from mathmodel_paper_studio.workflow.checkpoint import CheckpointConflictError, CheckpointRecoveryError, CheckpointStore
from mathmodel_paper_studio.workflow.engine import WorkflowDeliveryConflictError, WorkflowEngine
from mathmodel_paper_studio.workflow.progress import ProgressKind, RunProgressReporter
from mathmodel_paper_studio.workflow.stages import PauseReason, StageOutput, StageResult, WORKFLOW_STAGES

ROOT = Path(r'F:\MathModelPaperStudio\tmp\mathmodel-paper-studio-run-sidebar\workflow-resume')


class FakeWorkflowEngine(WorkflowEngine):
    def __init__(self, *args, events=None, responses=None, crash_after=None, output_conflict=False, **kwargs):
        super().__init__(*args, **kwargs)
        self.events = events if events is not None else []
        self.responses = responses or {}
        self.crash_after = crash_after
        self.output_conflict = output_conflict

    def _execute_stage(self, context, artifact_directory, cancel_event=None):
        self.events.append((context.stage, context.attempt))
        if self.output_conflict and context.stage is Stage.PREFLIGHT:
            (artifact_directory / 'result.json').write_bytes(b'keep-existing')
        sequence = self.responses.get(context.stage, [])
        status, reason = sequence.pop(0) if sequence else (StageStatus.COMPLETED, None)
        output = StageOutput('result.json', f'{context.stage.value}:{context.attempt}'.encode('utf-8'))
        diagnostic = 'failure detail' if status is StageStatus.FAILED else ''
        return StageResult(context.stage, context.attempt, status,
                           outputs=(output,), diagnostic=diagnostic, pause_reason=reason)

    def _after_checkpoint(self, state):
        if self.crash_after == len(state.stage_records):
            raise SystemExit('simulated process termination at committed stage boundary')


def make_fixture(test):
    ROOT.mkdir(parents=True, exist_ok=True)
    test.temp = tempfile.TemporaryDirectory(dir=ROOT)
    test.addCleanup(test.temp.cleanup)
    paths = AppPaths(Path(test.temp.name) / 'app')
    paths.ensure_directories()
    database = Database(paths)
    database.migrate()
    projects = ProjectRepository(database)
    runs = RunRepository(database)
    project = Project('demo1', 'fixture', 'projects/demo1')
    projects.add(project)
    project_root = paths.root / project.relative_path
    project_root.mkdir()
    create_layout(project_root)
    problem_path = project_root / '00_inputs/problem.txt'
    problem_path.write_text('Fit observations.', encoding='utf-8')
    csv_path = project_root / '00_inputs/data.csv'
    csv_path.write_text('x,y\n1,2\n', encoding='utf-8')
    sources = tuple(
        SourceFile(source_id, project.project_id, relative, hashlib.sha256(path.read_bytes()).hexdigest(),
                   path.stat().st_size, kind)
        for source_id, relative, kind, path in (
            ('problem1', '00_inputs/problem.txt', 'problem', problem_path),
            ('csv1', '00_inputs/data.csv', 'csv', csv_path),
        )
    )
    run = Run('run1', project.project_id, Provider.CLAUDE, RunStatus.CREATED, '90_runs/run1')
    runs.add(run)
    return paths, projects, runs, project, run, sources, ('problem1', 'csv1')


class WorkflowResumeTests(unittest.TestCase):
    def setUp(self):
        (self.paths, self.projects, self.runs, self.project, self.run,
         self.sources, self.selected) = make_fixture(self)

    def test_restart_after_committed_stage_and_duplicate_start_do_not_replay(self):
        events = []
        first = FakeWorkflowEngine(self.paths, self.projects, self.runs, events=events, crash_after=1)
        with self.assertRaises(SystemExit):
            first.start(self.project.project_id, self.run.run_id, self.sources, self.selected)
        self.assertEqual(RunStatus.RUNNING, self.runs.get(self.run.run_id).status)
        before = self.runs.get(self.run.run_id).checkpoint_sha256
        duplicate = first.reconcile_duplicate_delivery(self.run.run_id, StageResult(
            Stage.PREFLIGHT, 0, StageStatus.COMPLETED,
            outputs=(StageOutput('result.json', b'preflight:0'),),
        ))
        self.assertEqual(2, duplicate.generation)
        self.assertEqual(before, self.runs.get(self.run.run_id).checkpoint_sha256)
        with self.assertRaises(WorkflowDeliveryConflictError):
            first.reconcile_duplicate_delivery(self.run.run_id, StageResult(
                Stage.PREFLIGHT, 0, StageStatus.COMPLETED,
                outputs=(StageOutput('result.json', b'different'),),
            ))
        self.assertEqual(before, self.runs.get(self.run.run_id).checkpoint_sha256)

        resumed = FakeWorkflowEngine(self.paths, self.projects, self.runs, events=events)
        state = resumed.resume(self.run.run_id, self.sources, self.selected)
        self.assertEqual(RunStatus.COMPLETED, state.status)
        self.assertEqual(list(WORKFLOW_STAGES), [stage for stage, _ in events])
        self.assertEqual(RunStatus.COMPLETED, self.runs.get(self.run.run_id).status)
        generation = state.generation
        duplicate = resumed.start(self.project.project_id, self.run.run_id, self.sources, self.selected)
        self.assertEqual(generation, duplicate.generation)
        self.assertEqual(list(WORKFLOW_STAGES), [stage for stage, _ in events])

    def test_resume_continues_progress_sequence_without_serializing_event_history(self):
        progress_events = []
        reporter = RunProgressReporter(self.run.run_id, progress_events.append)
        reporter.emit(ProgressKind.AGENT_MESSAGE, '会话内测试事件',
                      detail='SIDEBAR_MEMORY_ONLY_MARKER')
        blocked = FakeWorkflowEngine(
            self.paths, self.projects, self.runs,
            responses={Stage.PREFLIGHT: [(StageStatus.PAUSED, PauseReason.AUTH_REQUIRED)]},
        )

        paused = blocked.start(
            self.project.project_id, self.run.run_id, self.sources, self.selected,
            progress=reporter,
        )
        self.assertEqual(RunStatus.PAUSED, paused.status)
        first_count = len(progress_events)
        resumed = FakeWorkflowEngine(self.paths, self.projects, self.runs).resume(
            self.run.run_id, self.sources, self.selected, progress=reporter,
        )

        self.assertEqual(RunStatus.COMPLETED, resumed.status)
        self.assertGreater(len(progress_events), first_count)
        self.assertEqual(list(range(1, len(progress_events) + 1)),
                         [event.sequence for event in progress_events])
        self.assertGreaterEqual(sum(event.kind is ProgressKind.RUN_STARTED
                                    for event in progress_events), 2)

        run_record = self.runs.get(self.run.run_id)
        layout = RunArtifactLayout(self.paths, self.projects.get(self.project.project_id), run_record)
        checkpoint_path = next(
            path for path in layout.root.glob('checkpoint-v*.json')
            if hashlib.sha256(path.read_bytes()).hexdigest() == run_record.checkpoint_sha256
        )
        serialized = checkpoint_path.read_text(encoding='utf-8')
        self.assertNotIn('SIDEBAR_MEMORY_ONLY_MARKER', serialized)
        self.assertNotIn('progress_events', serialized)

    def test_hard_process_exit_after_checkpoint_recovers_in_new_process(self):
        worker = Path(self.temp.name) / 'kill_after_checkpoint.py'
        worker.write_text(textwrap.dedent(r'''
            import hashlib
            import os
            import sys
            from pathlib import Path
            from mathmodel_paper_studio.bootstrap.paths import AppPaths
            from mathmodel_paper_studio.domain.models import SourceFile, StageStatus
            from mathmodel_paper_studio.storage.database import Database
            from mathmodel_paper_studio.storage.repositories import ProjectRepository, RecordConflictError, RunRepository
            from mathmodel_paper_studio.workflow.engine import WorkflowDeliveryConflictError, WorkflowEngine
            from mathmodel_paper_studio.workflow.stages import StageOutput, StageResult

            class KillEngine(WorkflowEngine):
                def _execute_stage(self, context, artifact_directory, cancel_event=None):
                    return StageResult(context.stage, context.attempt, StageStatus.COMPLETED,
                                       outputs=(StageOutput('result.json', b'preflight:0'),))
                def _after_checkpoint(self, state):
                    if len(state.stage_records) == 1:
                        os._exit(73)

            paths = AppPaths(Path(sys.argv[1]))
            db = Database(paths)
            projects = ProjectRepository(db)
            runs = RunRepository(db)
            project_id, run_id = sys.argv[2:4]
            project = projects.get(project_id)
            project_root = paths.root / project.relative_path
            sources = []
            for source_id, relative, kind in (('problem1', '00_inputs/problem.txt', 'problem'),
                                               ('csv1', '00_inputs/data.csv', 'csv')):
                source_path = project_root / relative
                content = source_path.read_bytes()
                sources.append(SourceFile(source_id, project_id, relative,
                                          hashlib.sha256(content).hexdigest(), len(content), kind))
            KillEngine(paths, projects, runs).start(project_id, run_id, sources, ('problem1', 'csv1'))
        '''), encoding='utf-8')
        worktree = Path(__file__).resolve().parents[2]
        child_temp = Path(self.temp.name) / 'child-temp'
        child_temp.mkdir()
        env = {
            'PATH': os.environ.get('PATH', ''),
            'SystemRoot': os.environ.get('SystemRoot', ''),
            'WINDIR': os.environ.get('WINDIR', ''),
            'TEMP': str(child_temp), 'TMP': str(child_temp), 'TMPDIR': str(child_temp),
            'PYTHONPYCACHEPREFIX': str(child_temp / 'pycache'),
            'PIP_CACHE_DIR': str(child_temp / 'pip-cache'), 'UV_CACHE_DIR': str(child_temp / 'uv-cache'),
            'MPLCONFIGDIR': str(child_temp / 'matplotlib'), 'QT_SHADER_CACHE_PATH': str(child_temp / 'qt'),
            'PYTHONPATH': str(worktree / 'src'), 'PYTHONIOENCODING': 'utf-8',
        }
        result = subprocess.run([sys.executable, str(worker), str(self.paths.root),
                                 self.project.project_id, self.run.run_id], cwd=worktree,
                               env=env, capture_output=True, text=True, timeout=45, check=False)
        self.assertEqual(73, result.returncode, result.stderr)
        self.assertEqual(RunStatus.RUNNING, self.runs.get(self.run.run_id).status)
        events = []
        resumed = FakeWorkflowEngine(self.paths, self.projects, self.runs, events=events)
        state = resumed.resume(self.run.run_id, self.sources, self.selected)
        self.assertEqual(RunStatus.COMPLETED, state.status)
        self.assertEqual(list(WORKFLOW_STAGES[1:]), [stage for stage, _ in events])

    def test_corrupt_or_missing_checkpoint_pauses_without_replaying_stage(self):
        for missing in (False, True):
            with self.subTest(missing=missing):
                self.tearDown()
                self.setUp()
                events = []
                blocked = FakeWorkflowEngine(self.paths, self.projects, self.runs, events=events,
                    responses={Stage.PREFLIGHT: [(StageStatus.PAUSED, PauseReason.AUTH_REQUIRED)]})
                blocked.start(self.project.project_id, self.run.run_id, self.sources, self.selected)
                checkpoint_files = list((self.paths.root / self.project.relative_path / self.run.relative_path).glob('checkpoint-v*.json'))
                current_hash = self.runs.get(self.run.run_id).checkpoint_sha256
                current_files = [path for path in checkpoint_files
                                  if hashlib.sha256(path.read_bytes()).hexdigest() == current_hash]
                self.assertEqual(1, len(current_files))
                if missing:
                    current_files[0].unlink()
                else:
                    current_files[0].write_bytes(b'corrupted')
                recover = FakeWorkflowEngine(self.paths, self.projects, self.runs, events=events)
                with self.assertRaises(CheckpointRecoveryError):
                    recover.resume(self.run.run_id, self.sources, self.selected)
                self.assertEqual(RunStatus.PAUSED, self.runs.get(self.run.run_id).status)
                self.assertEqual([(Stage.PREFLIGHT, 0)], events)

    def test_matching_hash_with_malformed_checkpoint_pauses(self):
        events = []
        engine = FakeWorkflowEngine(self.paths, self.projects, self.runs, events=events, responses={
            Stage.PREFLIGHT: [(StageStatus.PAUSED, PauseReason.AUTH_REQUIRED)]
        })
        engine.start(self.project.project_id, self.run.run_id, self.sources, self.selected)
        run = self.runs.get(self.run.run_id)
        layout = RunArtifactLayout(self.paths, self.project, run)
        checkpoints = list(layout.root.glob('checkpoint-v*.json'))
        current = next(path for path in checkpoints
                       if hashlib.sha256(path.read_bytes()).hexdigest() == run.checkpoint_sha256)
        store = CheckpointStore(layout, self.runs)
        malformed = (
            b'{"schema_version":9,"state":{}}',
            b'{"schema_version":1,"schema_version":1,"state":{}}',
            b'{"schema_version":1,"state":NaN}',
            b'{"schema_version":1,"state":{}}',
        )
        for payload in malformed:
            old_hash = run.checkpoint_sha256
            current.write_bytes(payload)
            new_hash = hashlib.sha256(payload).hexdigest()
            self.runs.commit_checkpoint(self.run.run_id, new_hash,
                expected_status=run.status, expected_checkpoint_sha256=old_hash,
                target_status=run.status)
            run = self.runs.get(self.run.run_id)
            with self.assertRaises(CheckpointRecoveryError):
                store.recover(run)
        self.assertEqual(RunStatus.PAUSED, run.status)
        self.assertEqual([(Stage.PREFLIGHT, 0)], events)

    def test_corrupt_stage_artifact_pauses_without_replaying_next_stage(self):
        events = []
        first = FakeWorkflowEngine(self.paths, self.projects, self.runs, events=events, crash_after=1)
        with self.assertRaises(SystemExit):
            first.start(self.project.project_id, self.run.run_id, self.sources, self.selected)
        run_root = self.paths.root / self.project.relative_path / self.run.relative_path
        artifacts = list(run_root.glob('stages/preflight/attempt-00-delivery-*/result.json'))
        self.assertEqual(1, len(artifacts))
        artifacts[0].write_bytes(b'corrupted artifact')
        restarted = FakeWorkflowEngine(self.paths, self.projects, self.runs, events=events)
        with self.assertRaises(CheckpointRecoveryError):
            restarted.resume(self.run.run_id, self.sources, self.selected)
        self.assertEqual(RunStatus.PAUSED, self.runs.get(self.run.run_id).status)
        self.assertEqual([(Stage.PREFLIGHT, 0)], events)

    def test_checkpoint_version_conflict_never_overwrites_persisted_state(self):
        engine = FakeWorkflowEngine(self.paths, self.projects, self.runs, responses={
            Stage.PREFLIGHT: [(StageStatus.PAUSED, PauseReason.AUTH_REQUIRED)]
        })
        engine.start(self.project.project_id, self.run.run_id, self.sources, self.selected)
        run = self.runs.get(self.run.run_id)
        layout = RunArtifactLayout(self.paths, self.project, run)
        store = CheckpointStore(layout, self.runs)
        state = store.recover(run)
        checkpoint_path = next(path for path in layout.root.glob('checkpoint-v*.json')
                               if hashlib.sha256(path.read_bytes()).hexdigest() == run.checkpoint_sha256)
        original_payload = checkpoint_path.read_bytes()

        with self.assertRaises(CheckpointConflictError):
            store.persist(state, run)

        self.assertEqual(original_payload, checkpoint_path.read_bytes())
        self.assertEqual(run.checkpoint_sha256, self.runs.get(self.run.run_id).checkpoint_sha256)
    def test_checkpoint_compare_and_set_rejects_stale_hash_and_illegal_transition(self):
        engine = FakeWorkflowEngine(self.paths, self.projects, self.runs, responses={
            Stage.PREFLIGHT: [(StageStatus.PAUSED, PauseReason.AUTH_REQUIRED)]
        })
        engine.start(self.project.project_id, self.run.run_id, self.sources, self.selected)
        run = self.runs.get(self.run.run_id)
        with self.assertRaises(RecordConflictError):
            self.runs.commit_checkpoint(self.run.run_id, 'a' * 64,
                expected_status=run.status, expected_checkpoint_sha256='0' * 64,
                target_status=run.status)
        with self.assertRaises(ValueError):
            self.runs.commit_checkpoint(self.run.run_id, 'a' * 64,
                expected_status=run.status, expected_checkpoint_sha256=run.checkpoint_sha256,
                target_status=RunStatus.COMPLETED)
        current = self.runs.get(self.run.run_id)
        self.assertEqual(run.checkpoint_sha256, current.checkpoint_sha256)
        self.assertEqual(RunStatus.PAUSED, current.status)

    def test_created_run_and_pre_set_cancel_signal_are_persisted(self):
        engine = WorkflowEngine(self.paths, self.projects, self.runs)
        with self.assertRaises(TypeError):
            engine.start(self.project.project_id, self.run.run_id, self.sources, self.selected,
                         cancel_event=object())
        self.assertEqual(RunStatus.CREATED, self.runs.get(self.run.run_id).status)
        self.assertIsNone(engine.cancel(self.run.run_id))
        self.assertEqual(RunStatus.CANCELLED, self.runs.get(self.run.run_id).status)

        self.tearDown()
        self.setUp()
        cancel_event = threading.Event()
        cancel_event.set()
        events = []
        started = FakeWorkflowEngine(self.paths, self.projects, self.runs, events=events)
        state = started.start(self.project.project_id, self.run.run_id, self.sources,
                              self.selected, cancel_event=cancel_event)
        self.assertEqual(RunStatus.CANCELLED, state.status)
        self.assertEqual([], events)

    def test_cancel_after_pause_is_persisted(self):
        engine = FakeWorkflowEngine(self.paths, self.projects, self.runs, responses={
            Stage.PREFLIGHT: [(StageStatus.PAUSED, PauseReason.AUTH_REQUIRED)]
        })
        engine.start(self.project.project_id, self.run.run_id, self.sources, self.selected)
        state = engine.cancel(self.run.run_id)
        self.assertEqual(RunStatus.CANCELLED, state.status)
        self.assertEqual(RunStatus.CANCELLED, self.runs.get(self.run.run_id).status)


if __name__ == '__main__':
    unittest.main()
