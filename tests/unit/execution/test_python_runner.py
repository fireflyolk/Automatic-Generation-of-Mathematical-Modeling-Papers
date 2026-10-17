import json
from pathlib import Path
import tempfile
import threading
import unittest

from mathmodel_paper_studio.agents.preflight import Provider
from mathmodel_paper_studio.bootstrap.paths import APPROVED_BASE, AppPaths
from mathmodel_paper_studio.domain.models import Project, Run, RunStatus, StageStatus
from mathmodel_paper_studio.execution.limits import ComputationLimits
from mathmodel_paper_studio.execution.python_runner import PythonRunner, RunArtifactLayout

ROOT = Path(r'F:\Codex另一个工作的地方\tmp\mathmodel-paper-studio-20261015')


def make_layout(test):
    test.tmp = tempfile.TemporaryDirectory(dir=ROOT)
    test.addCleanup(test.tmp.cleanup)
    paths = AppPaths(Path(test.tmp.name) / 'app')
    paths.ensure_directories()
    project = Project('demo1', 'fixture', 'projects/demo1')
    project_root = paths.root / project.relative_path
    project_root.mkdir(parents=True)
    for name in ('00_inputs', '10_analysis', '20_model', '30_src', '40_results',
                 '50_figures', '60_paper', '70_review', '90_runs'):
        (project_root / name).mkdir()
    run = Run('run1', project.project_id, Provider.CLAUDE, RunStatus.CREATED, '90_runs/run1')
    return paths, project, run, RunArtifactLayout.create(paths, project, run)


class ComputationLimitsTests(unittest.TestCase):
    def test_defaults_are_finite_and_bounded(self):
        limits = ComputationLimits()
        self.assertGreater(limits.wall_seconds, 0)
        self.assertGreater(limits.memory_bytes, 0)
        self.assertGreater(limits.stdout_bytes, 0)
        self.assertGreater(limits.artifact_bytes, 0)

    def test_invalid_or_unbounded_limits_are_rejected(self):
        cases = (
            {'wall_seconds': 0},
            {'wall_seconds': float('inf')},
            {'wall_seconds': 10**1000},
            {'memory_bytes': True},
            {'memory_bytes': 0},
            {'stdout_bytes': -1},
            {'stderr_bytes': 0},
            {'artifact_bytes': 0},
            {'validation_summary_bytes': 0},
            {'max_artifacts': 0},
            {'max_rows': True},
            {'max_columns': 0},
            {'max_source_bytes': 0},
            {'memory_bytes': 2**64},
        )
        for values in cases:
            with self.subTest(values=values), self.assertRaises(ValueError):
                ComputationLimits(**values)


class PythonRunnerTests(unittest.TestCase):
    def test_generated_source_is_saved_but_execution_pauses_fail_closed(self):
        _, _, _, layout = make_layout(self)
        source = "print('must never execute')\n"
        outcome = PythonRunner().run(layout, source, deterministic_seed=314159)

        self.assertEqual(StageStatus.PAUSED, outcome.stage_status)
        self.assertFalse(outcome.execution_started)
        self.assertIsNone(outcome.exit_code)
        self.assertEqual((), outcome.command)
        self.assertIsNone(outcome.interpreter)
        self.assertIsNone(outcome.stdout_path)
        self.assertIsNone(outcome.stderr_path)
        self.assertEqual(314159, outcome.deterministic_seed)
        self.assertTrue(outcome.script_path.is_file())
        self.assertEqual(source.encode('utf-8'), outcome.script_path.read_bytes())
        self.assertTrue(outcome.script_path.is_relative_to(layout.source_dir))
        self.assertTrue(outcome.summary_path.is_relative_to(layout.results_dir))
        summary = json.loads(outcome.summary_path.read_text('utf-8'))
        self.assertFalse(summary['execution_started'])
        self.assertIsNone(summary['exit_code'])
        self.assertEqual([], summary['command'])
        self.assertIn('未证明', outcome.blocked_reason)

    def test_cancel_before_start_never_saves_or_executes_generated_source(self):
        _, _, _, layout = make_layout(self)
        cancelled = threading.Event()
        cancelled.set()
        outcome = PythonRunner().run(layout, "raise RuntimeError('no')\n",
                                     deterministic_seed=7, cancel_event=cancelled)
        self.assertEqual(StageStatus.CANCELLED, outcome.stage_status)
        self.assertFalse(outcome.execution_started)
        self.assertIsNone(outcome.script_path)
        self.assertIsNone(outcome.exit_code)
        self.assertEqual([], list(layout.source_dir.iterdir()))

    def test_same_run_cannot_overwrite_preserved_script(self):
        _, _, _, layout = make_layout(self)
        runner = PythonRunner()
        source = "print('first')\n"
        runner.run(layout, source, deterministic_seed=1)
        with self.assertRaises(FileExistsError):
            runner.run(layout, "print('replacement')\n", deterministic_seed=1)
        self.assertEqual(source.encode('utf-8'), (layout.source_dir / 'generated.py').read_bytes())

    def test_seed_and_source_bounds_are_validated_before_writing(self):
        _, _, _, layout = make_layout(self)
        limits = ComputationLimits(max_source_bytes=8)
        runner = PythonRunner(limits)
        for seed in (-1, True, 2**64):
            with self.subTest(seed=seed), self.assertRaises(ValueError):
                runner.run(layout, 'x=1\n', deterministic_seed=seed)
        with self.assertRaises(ValueError):
            runner.run(layout, 'x=123456789\n', deterministic_seed=1)
        self.assertEqual([], list(layout.source_dir.iterdir()))


    def test_blocked_summary_records_seed_hash_and_no_runtime_observation(self):
        _, _, _, layout = make_layout(self)
        outcome = PythonRunner().run(layout, "x = 1\n", deterministic_seed=123)
        summary = json.loads(outcome.summary_path.read_text('utf-8'))
        self.assertEqual(123, summary['deterministic_seed'])
        self.assertEqual(outcome.script_evidence.sha256, summary['script']['sha256'])
        self.assertIsNone(summary['interpreter'])
        self.assertIsNone(summary['dependency_fingerprint'])
        self.assertEqual([], summary['command'])
        self.assertIsNone(summary['exit_code'])


    def test_cancelled_run_record_prevents_partial_retry_write(self):
        _, _, _, layout = make_layout(self)
        cancelled = threading.Event()
        cancelled.set()
        first = PythonRunner().run(layout, 'x = 1\n', deterministic_seed=1,
                                   cancel_event=cancelled)
        self.assertEqual(StageStatus.CANCELLED, first.stage_status)
        with self.assertRaises(FileExistsError):
            PythonRunner().run(layout, 'x = 2\n', deterministic_seed=2)
        self.assertFalse((layout.source_dir / 'generated.py').exists())
