from dataclasses import FrozenInstanceError
import unittest

from mathmodel_paper_studio.domain.models import (
    ArtifactEvidence, Project, Run, RunStatus, SourceFile, ScopeManifest,
    Stage, StageRecord, StageStatus, Provider, ProviderStatus, relative_path,
)

HASH = 'a' * 64


class ModelsTests(unittest.TestCase):
    def test_project_frozen_and_title_is_data(self):
        project = Project('p_123', "Robert'); DROP TABLE projects;--", 'projects/p_123')
        with self.assertRaises(FrozenInstanceError):
            project.title = 'changed'
        for values in [('bad/id', 'title', 'projects/p'), ('p', '', 'projects/p'),
                       ('p', 'title', '../p'), ('p', 'title', 'F:/p')]:
            with self.subTest(values=values), self.assertRaises(ValueError):
                Project(*values)

    def test_source_hash_and_relative_paths(self):
        source = SourceFile('s_1', 'p_1', '00_inputs/数据.csv', HASH, 3, 'csv')
        self.assertEqual(source.sha256, HASH)
        for path in ('../secret', '/root', 'F:foo', 'a/../b', r'a\b', 'a:stream', 'a//b', 'NUL', 'a./b'):
            with self.subTest(path=path), self.assertRaises(ValueError):
                relative_path(path)
        for digest in ('a' * 63, 'g' * 64, 'A' * 64):
            with self.subTest(digest=digest), self.assertRaises(ValueError):
                SourceFile('s', 'p', '00_inputs/a.csv', digest, 1, 'csv')
        for size in (-1, True):
            with self.subTest(size=size), self.assertRaises(ValueError):
                SourceFile('s', 'p', 'a.csv', HASH, size, 'csv')

    def test_run_transitions_are_explicit_and_immutable(self):
        original = Run('r_1', 'p_1', Provider.CLAUDE, RunStatus.CREATED, '90_runs/r_1')
        running = original.transition(RunStatus.RUNNING)
        paused = running.transition(RunStatus.PAUSED)
        completed = paused.transition(RunStatus.RUNNING).transition(RunStatus.COMPLETED)
        self.assertEqual(original.status, RunStatus.CREATED)
        self.assertEqual(running.status.value, 'running')
        self.assertEqual(completed.status, RunStatus.COMPLETED)
        for target in (RunStatus.RUNNING, RunStatus.CANCELLED, RunStatus.CREATED):
            with self.subTest(target=target), self.assertRaises(ValueError):
                completed.transition(target)
        with self.assertRaises(ValueError):
            original.transition(RunStatus.COMPLETED)
        with self.assertRaises(ValueError):
            Run('r', 'p', 'invented', RunStatus.CREATED, 'runs/r')
        with self.assertRaises(ValueError):
            Run('r', 'p', Provider.CODEX, 'invented', 'runs/r')

    def test_stage_status_and_artifact_validation(self):
        stage = StageRecord('r_1', Stage.PREFLIGHT, StageStatus.PENDING)
        finished = stage.transition(StageStatus.RUNNING).transition(StageStatus.COMPLETED)
        self.assertEqual(stage.status.value, 'pending')
        self.assertEqual(finished.stage.value, 'preflight')
        with self.assertRaises(ValueError):
            stage.transition(StageStatus.COMPLETED)
        evidence = ArtifactEvidence('e_1', 'r_1', '40_results/table.csv', HASH, 'table', False)
        self.assertFalse(evidence.verified)
        with self.assertRaises(ValueError):
            ArtifactEvidence('e', 'r', 'a', HASH, 'invented', False)
        with self.assertRaises(ValueError):
            StageRecord('r', 'invented', StageStatus.PENDING)

    def test_scope_immutable_input_collections_and_no_absolute_paths(self):
        inputs = ['00_inputs/a.csv']
        manifest = ScopeManifest('scope_1', 'r_1', Provider.CLAUDE, 'projects/p_1',
                                 inputs, ['40_results'], [Stage.PREFLIGHT], True)
        inputs.append('00_inputs/new.csv')
        self.assertEqual(manifest.readable_inputs, ('00_inputs/a.csv',))
        self.assertEqual(manifest.stages, (Stage.PREFLIGHT,))
        with self.assertRaises(ValueError):
            ScopeManifest('scope', 'run', Provider.CLAUDE, 'projects/p',
                          ['../secret'], ['40_results'], [Stage.PREFLIGHT], True)
        # The provider contract must be the actual Task 0 type.
        from mathmodel_paper_studio.agents.preflight import ProviderStatus as ExistingStatus
        self.assertIs(ProviderStatus, ExistingStatus)


    def test_string_is_not_a_scope_or_evidence_collection(self):
        with self.assertRaises(ValueError):
            StageRecord('r', Stage.PREFLIGHT, StageStatus.PENDING, evidence_ids='ab')
        with self.assertRaises(ValueError):
            ScopeManifest('scope', 'run', Provider.CLAUDE, 'projects/p',
                          'ab', ['40_results'], [Stage.PREFLIGHT], True)

    def test_project_and_run_directory_paths_are_lowercase_ascii(self):
        project_paths = ('Projects/p', 'projects/P', 'projects/ı', 'projects/ärea',
                         'projects/中文', 'projects/p/nested', '90_runs/p')
        run_paths = ('90_RUNS/r', '90_runs/R', '90_runs/ı', '90_runs/ärea',
                     '90_runs/中文', '90_runs/r/nested', 'projects/r')
        for path in project_paths:
            with self.subTest(path=path), self.assertRaises(ValueError):
                Project('p', 'Project', path)
        for path in run_paths:
            with self.subTest(path=path), self.assertRaises(ValueError):
                Run('r', 'p', Provider.CLAUDE, RunStatus.CREATED, path)
        source = SourceFile('s', 'p', '00_inputs/ÄREA中文.csv', HASH, 1, 'csv')
        self.assertEqual(source.relative_path, '00_inputs/ÄREA中文.csv')
        self.assertEqual(relative_path('00_inputs/ÄREA中文.csv'), '00_inputs/ÄREA中文.csv')

    def test_directory_suffix_matches_lowercase_record_id(self):
        for project_id, path in (('p', 'projects/other'), ('P', 'projects/p')):
            with self.subTest(project_id=project_id), self.assertRaises(ValueError):
                Project(project_id, 'Title', path)
        for run_id, path in (('r', '90_runs/other'), ('R', '90_runs/r')):
            with self.subTest(run_id=run_id), self.assertRaises(ValueError):
                Run(run_id, 'p', Provider.CLAUDE, RunStatus.CREATED, path)
        for path in ('Projects/p', 'projects/ı', '90_runs/p', 'projects/p/nested'):
            with self.subTest(path=path), self.assertRaises(ValueError):
                ScopeManifest('scope', 'r', Provider.CLAUDE, path,
                              ['00_inputs/中文.csv'], ['40_results'], [Stage.PREFLIGHT], True)

if __name__ == '__main__':
    unittest.main()
