from pathlib import Path
import tempfile
import unittest

from mathmodel_paper_studio.agents.preflight import Provider
from mathmodel_paper_studio.bootstrap.paths import APPROVED_BASE, AppPaths
from mathmodel_paper_studio.domain.models import Project, Run, RunStatus, StageStatus
from mathmodel_paper_studio.execution.limits import ComputationLimits
from mathmodel_paper_studio.execution.python_runner import RunArtifactLayout
from mathmodel_paper_studio.execution.result_validation import ArtifactExpectation, ResultValidator

ROOT = Path(r'F:\MathModelPaperStudio\tmp\mathmodel-paper-studio-20261015')


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


class ArtifactExpectationTests(unittest.TestCase):
    def test_forged_absolute_traversal_and_wrong_category_paths_are_rejected(self):
        for path, kind in (
            (r'F:\outside.csv', 'table'),
            ('../outside.csv', 'table'),
            ('40_results/../50_figures/plot.png', 'figure'),
            ('30_src/generated.py', 'table'),
            ('50_figures/table.csv', 'table'),
        ):
            with self.subTest(path=path), self.assertRaises(ValueError):
                ArtifactExpectation(path, kind)

    def test_column_and_row_contracts_are_immutable_and_validated(self):
        expectation = ArtifactExpectation('40_results/table.csv', 'table',
                                          expected_columns=['x', 'y'], expected_rows=2)
        self.assertEqual(('x', 'y'), expectation.expected_columns)
        with self.assertRaises(ValueError):
            ArtifactExpectation('40_results/table.csv', 'table', expected_rows=-1)
        with self.assertRaises(ValueError):
            ArtifactExpectation('40_results/table.csv', 'table', expected_columns=['x', 'x'])


class ResultValidationTests(unittest.TestCase):
    def test_finite_csv_is_hashed_and_structurally_verified(self):
        _, _, _, layout = make_layout(self)
        output = layout.results_dir / 'table.csv'
        output.write_bytes(b'x,y\n1,2\n3,4\n')
        report = ResultValidator(layout).validate((
            ArtifactExpectation('40_results/table.csv', 'table',
                                expected_columns=('x', 'y'), expected_rows=2),
        ))
        self.assertEqual(StageStatus.COMPLETED, report.stage_status)
        self.assertTrue(report.human_review_required)
        self.assertFalse(report.mathematical_correctness_verified)
        evidence, = report.evidence
        self.assertEqual('table', evidence.kind)
        self.assertTrue(evidence.verified)
        self.assertEqual(64, len(evidence.sha256))
        self.assertEqual(evidence.sha256, report.hashes['40_results/table.csv'])
        self.assertTrue(report.summary_path.is_file())
        self.assertTrue(report.summary_path.is_relative_to(layout.results_dir))
        self.assertTrue(report.summary_evidence.verified)

    def test_nan_and_infinity_pause_instead_of_accepting_results(self):
        _, _, _, layout = make_layout(self)
        for index, body in enumerate((b'x\nNaN\n', b'x\nInf\n', b'x\n-Infinity\n'), start=1):
            output = layout.results_dir / f'table-{index}.csv'
            output.write_bytes(body)
            report = ResultValidator(layout).validate((
                ArtifactExpectation(f'40_results/table-{index}.csv', 'table'),
            ))
            with self.subTest(body=body):
                self.assertEqual(StageStatus.PAUSED, report.stage_status)
                self.assertFalse(report.evidence)
                self.assertTrue(any('有限' in issue for issue in report.issues))

    def test_row_count_columns_and_shape_mismatches_pause(self):
        _, _, _, layout = make_layout(self)
        (layout.results_dir / 'table.csv').write_bytes(b'x,y\n1,2\n')
        expectations = (
            ArtifactExpectation('40_results/table.csv', 'table',
                                expected_columns=('x', 'y'), expected_rows=2),
        )
        report = ResultValidator(layout).validate(expectations)
        self.assertEqual(StageStatus.PAUSED, report.stage_status)
        self.assertTrue(report.issues)

        report = ResultValidator(layout).validate((
            ArtifactExpectation('40_results/table.csv', 'table',
                                expected_columns=('wrong', 'y'), expected_rows=1),
        ))
        self.assertEqual(StageStatus.PAUSED, report.stage_status)

    def test_missing_required_artifact_pauses_and_writes_validation_summary(self):
        _, _, _, layout = make_layout(self)
        report = ResultValidator(layout).validate((
            ArtifactExpectation('40_results/missing.csv', 'table'),
        ))
        self.assertEqual(StageStatus.PAUSED, report.stage_status)
        self.assertIn('40_results/missing.csv', report.missing_paths)
        self.assertTrue(report.summary_path.is_file())
        self.assertTrue(any('缺少' in issue for issue in report.issues))

    def test_output_byte_and_shape_limits_are_enforced(self):
        _, _, _, layout = make_layout(self)
        (layout.results_dir / 'large.csv').write_bytes(b'x\n1234567890\n')
        report = ResultValidator(layout, ComputationLimits(artifact_bytes=4)).validate((
            ArtifactExpectation('40_results/large.csv', 'table'),
        ))
        self.assertEqual(StageStatus.PAUSED, report.stage_status)
        self.assertTrue(any('字节' in issue for issue in report.issues))

        (layout.results_dir / 'many.csv').write_bytes(b'x\n1\n2\n')
        report = ResultValidator(layout, ComputationLimits(max_rows=1)).validate((
            ArtifactExpectation('40_results/many.csv', 'table'),
        ))
        self.assertEqual(StageStatus.PAUSED, report.stage_status)
        self.assertTrue(any('行数' in issue for issue in report.issues))

    def test_valid_png_dimensions_and_hash_are_recorded(self):
        import struct
        import zlib

        def chunk(kind, data):
            payload = kind + data
            return struct.pack('>I', len(data)) + payload + struct.pack('>I', zlib.crc32(payload) & 0xffffffff)

        png = (b'\x89PNG\r\n\x1a\n'
               + chunk(b'IHDR', struct.pack('>IIBBBBB', 1, 1, 8, 6, 0, 0, 0))
               + chunk(b'IDAT', zlib.compress(b'\x00\x00\x00\x00\x00'))
               + chunk(b'IEND', b''))
        _, _, _, layout = make_layout(self)
        (layout.figures_dir / 'plot.png').write_bytes(png)
        report = ResultValidator(layout).validate((
            ArtifactExpectation('50_figures/plot.png', 'figure'),
        ))
        self.assertEqual(StageStatus.COMPLETED, report.stage_status)
        self.assertTrue(report.human_review_required)
        self.assertFalse(report.mathematical_correctness_verified)
        evidence, = report.evidence
        self.assertEqual('figure', evidence.kind)


    def test_empty_expectations_cannot_mark_result_validation_completed(self):
        _, _, _, layout = make_layout(self)
        with self.assertRaises(ValueError):
            ResultValidator(layout).validate(())
        self.assertFalse(tuple(layout.results_dir.glob('validation-v*.json')))
