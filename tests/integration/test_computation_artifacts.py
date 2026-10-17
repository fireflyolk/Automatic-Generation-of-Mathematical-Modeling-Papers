from pathlib import Path
import tempfile
import unittest

from mathmodel_paper_studio.agents.preflight import Provider
from mathmodel_paper_studio.bootstrap.paths import AppPaths
from mathmodel_paper_studio.domain.models import (
    ArtifactEvidence, Project, Run, RunStatus, StageStatus,
)
from mathmodel_paper_studio.execution.python_runner import PythonRunner, RunArtifactLayout
from mathmodel_paper_studio.execution.result_validation import ArtifactExpectation, ResultValidator
from mathmodel_paper_studio.paper.evidence import EvidenceIndex
from mathmodel_paper_studio.projects.layout import create_layout

ROOT = Path(r'F:\Codex另一个工作的地方\tmp\mathmodel-paper-studio-20261015')


class ComputationArtifactsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=ROOT)
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.paths = AppPaths(self.base / 'app')
        self.paths.ensure_directories()
        self.project = Project('project1', 'fixture', 'projects/project1')
        self.project_root = self.paths.root / self.project.relative_path
        self.project_root.mkdir(parents=True)
        create_layout(self.project_root)
        self.run = Run('run1', self.project.project_id, Provider.CLAUDE,
                       RunStatus.CREATED, '90_runs/run1')
        self.layout = RunArtifactLayout.create(self.paths, self.project, self.run)

    def test_run_outputs_are_created_inside_run_only(self):
        for directory in (self.layout.source_dir, self.layout.results_dir, self.layout.figures_dir):
            self.assertTrue(directory.is_dir())
            self.assertTrue(directory.resolve().is_relative_to(self.layout.root.resolve()))
            self.assertEqual('f:', directory.drive.lower())
        self.assertEqual('90_runs/run1', self.run.relative_path)

    def test_forged_artifact_paths_never_read_or_write_outside_output_directories(self):
        outside = self.layout.project_root / 'outside.csv'
        outside.write_bytes(b'keep-me')
        candidate_paths = (
            '../outside.csv',
            'outside.csv',
            '40_results/../../outside.csv',
            str(outside),
        )
        for candidate in candidate_paths:
            with self.subTest(candidate=candidate), self.assertRaises(ValueError):
                ArtifactExpectation(candidate, 'table')
        self.assertEqual(b'keep-me', outside.read_bytes())
        self.assertFalse((self.layout.results_dir / 'outside.csv').exists())

    def test_numeric_claims_link_hashes_and_always_require_human_review(self):
        output = self.layout.results_dir / 'table.csv'
        output.write_bytes(b'year,value\n2024,12.5\n')
        report = ResultValidator(self.layout).validate((
            ArtifactExpectation('40_results/table.csv', 'table',
                                expected_columns=('year', 'value'), expected_rows=1),
        ))
        evidence, = report.evidence
        index = EvidenceIndex(report.evidence)
        claim = index.numeric_claim(
            'claim1', '2024 年指标为 12.5', '12.5', (evidence.evidence_id,)
        )
        self.assertEqual('evidence_linked', claim.status.value)
        citation, = claim.citations
        self.assertEqual(evidence.evidence_id, citation.evidence_id)
        self.assertEqual(evidence.sha256, citation.sha256)
        self.assertTrue(claim.human_review_required)
        self.assertFalse(claim.mathematical_correctness_verified)

        unverified = index.numeric_claim('claim2', '另一个数值', '3.0', ())
        self.assertEqual('unverified', unverified.status.value)
        self.assertTrue(unverified.human_review_required)
        self.assertFalse(unverified.mathematical_correctness_verified)

        manifest_path, manifest_evidence = index.write_claim_manifest(
            self.layout, (claim, unverified)
        )
        self.assertTrue(manifest_path.is_relative_to(self.layout.results_dir))
        self.assertTrue(manifest_evidence.verified)
        self.assertEqual(manifest_evidence.sha256,
                         __import__('hashlib').sha256(manifest_path.read_bytes()).hexdigest())

    def test_cross_run_evidence_cannot_be_written_into_another_run_manifest(self):
        other = ArtifactEvidence(
            'ev_other', 'other_run', '90_runs/other_run/40_results/table.csv',
            'a' * 64, 'table', True,
        )
        index = EvidenceIndex((other,))
        claim = index.numeric_claim('claim1', '跨运行数值', '1', ('ev_other',))
        with self.assertRaises(ValueError):
            index.write_claim_manifest(self.layout, (claim,))

    def test_invalid_claim_numbers_and_unknown_evidence_are_unverified(self):
        index = EvidenceIndex(())
        with self.assertRaises(ValueError):
            index.numeric_claim('claim1', 'not finite', 'NaN', ())
        claim = index.numeric_claim('claim2', 'orphan claim', '10', ('missing_evidence',))
        self.assertEqual('unverified', claim.status.value)
        self.assertEqual(('missing_evidence',), claim.unresolved_evidence_ids)

    def test_source_metadata_never_claims_process_or_exit_information(self):
        source = (
            "from pathlib import Path\n"
            "Path(__file__).with_name('executed.txt').write_text('ran')\n"
        )
        outcome = PythonRunner().run(self.layout, source, deterministic_seed=99)
        self.assertEqual(StageStatus.PAUSED, outcome.stage_status)
        self.assertFalse(outcome.execution_started)
        self.assertIsNone(outcome.exit_code)
        self.assertEqual((), outcome.command)
        self.assertEqual(99, outcome.deterministic_seed)
        self.assertIsNone(outcome.stdout_path)
        self.assertIsNone(outcome.stderr_path)
        self.assertFalse((self.layout.source_dir / 'executed.txt').exists())

    def test_generated_external_write_attempt_is_never_run(self):
        marker = self.base / 'outside-output-marker.txt'
        source = (
            "from pathlib import Path\n"
            f"Path({str(marker)!r}).write_text('ran')\n"
        )
        outcome = PythonRunner().run(self.layout, source, deterministic_seed=22)
        self.assertEqual(StageStatus.PAUSED, outcome.stage_status)
        self.assertFalse(outcome.execution_started)
        self.assertFalse(marker.exists())

    def test_existing_run_artifacts_are_never_overwritten(self):
        sentinel = self.layout.results_dir / 'keep.txt'
        sentinel.write_bytes(b'preserve')
        with self.assertRaises(FileExistsError):
            RunArtifactLayout.create(self.paths, self.project, self.run)
        self.assertEqual(b'preserve', sentinel.read_bytes())

