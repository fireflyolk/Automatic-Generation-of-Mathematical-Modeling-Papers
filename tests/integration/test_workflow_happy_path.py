import unittest

from mathmodel_paper_studio.domain.models import RunStatus, Stage, StageStatus
from mathmodel_paper_studio.workflow.stages import PauseReason, StageOutput, StageResult, WORKFLOW_STAGES
from tests.integration.test_workflow_resume import FakeWorkflowEngine, make_fixture
from mathmodel_paper_studio.workflow.engine import WorkflowEngine


class WorkflowHappyPathTests(unittest.TestCase):
    def setUp(self):
        (self.paths, self.projects, self.runs, self.project, self.run,
         self.sources, self.selected) = make_fixture(self)

    def test_complete_flow_runs_in_order_and_persists_evidence_hashes(self):
        events = []
        engine = FakeWorkflowEngine(self.paths, self.projects, self.runs, events=events)
        state = engine.start(self.project.project_id, self.run.run_id, self.sources, self.selected)
        self.assertEqual(RunStatus.COMPLETED, state.status)
        self.assertEqual(list(WORKFLOW_STAGES), [stage for stage, _ in events])
        self.assertEqual(len(WORKFLOW_STAGES), len(state.stage_records))
        self.assertEqual(len(WORKFLOW_STAGES), len(state.artifacts))
        self.assertTrue(all(len(item.sha256) == 64 and item.evidence_id for item in state.artifacts))
        self.assertTrue(all((self.paths.root / self.project.relative_path / item.relative_path).is_file() for item in state.artifacts))

    def test_calculation_and_validation_allow_two_repairs_then_pause_with_diagnostics(self):
        responses = {
            Stage.CALCULATION: [
                (StageStatus.FAILED, None), (StageStatus.FAILED, None), (StageStatus.COMPLETED, None),
            ],
            Stage.RESULT_VALIDATION: [
                (StageStatus.FAILED, None), (StageStatus.FAILED, None), (StageStatus.FAILED, None),
            ],
        }
        events = []
        engine = FakeWorkflowEngine(self.paths, self.projects, self.runs, events=events, responses=responses)
        state = engine.start(self.project.project_id, self.run.run_id, self.sources, self.selected)
        calculation_attempts = [attempt for stage, attempt in events if stage is Stage.CALCULATION]
        validation_attempts = [attempt for stage, attempt in events if stage is Stage.RESULT_VALIDATION]
        self.assertEqual([0, 1, 2], calculation_attempts)
        self.assertEqual([0, 1, 2], validation_attempts)
        self.assertEqual(RunStatus.PAUSED, state.status)
        self.assertEqual(PauseReason.RETRY_LIMIT, state.pause_reason)
        self.assertTrue(any('failure detail' in item for item in state.diagnostics))
        result_paths = [item.relative_path for item in state.artifacts if item.stage is Stage.RESULT_VALIDATION]
        self.assertEqual(3, len(set(result_paths)))

    def test_output_conflict_pauses_without_overwriting_existing_bytes(self):
        engine = FakeWorkflowEngine(self.paths, self.projects, self.runs, output_conflict=True)
        state = engine.start(self.project.project_id, self.run.run_id, self.sources, self.selected)
        self.assertEqual(RunStatus.PAUSED, state.status)
        self.assertEqual(PauseReason.OUTPUT_CONFLICT, state.pause_reason)
        run_root = self.paths.root / self.project.relative_path / self.run.relative_path
        existing = list(run_root.glob('stages/preflight/attempt-00-delivery-*/result.json'))
        self.assertEqual(1, len(existing))
        self.assertEqual(b'keep-existing', existing[0].read_bytes())
        self.assertFalse(state.artifacts)

    def test_default_production_engine_pauses_at_unverified_preflight(self):
        engine = WorkflowEngine(self.paths, self.projects, self.runs)
        state = engine.start(self.project.project_id, self.run.run_id, self.sources, self.selected)
        self.assertEqual(RunStatus.PAUSED, state.status)
        self.assertEqual(PauseReason.PREFLIGHT_FAILED, state.pause_reason)
        self.assertEqual(Stage.PREFLIGHT, state.next_stage)
        self.assertEqual((), state.artifacts)

    def test_missing_and_ambiguous_inputs_pause_before_unsafe_work(self):
        engine = FakeWorkflowEngine(self.paths, self.projects, self.runs, events=[])
        state = engine.start(self.project.project_id, self.run.run_id, self.sources, ('problem1',))
        self.assertEqual(RunStatus.PAUSED, state.status)
        self.assertEqual(PauseReason.MISSING_DATA, state.pause_reason)
        self.assertEqual(Stage.DATA_PROFILE, state.next_stage)

    def test_duplicate_input_selection_pauses_at_preflight(self):
        engine = FakeWorkflowEngine(self.paths, self.projects, self.runs, events=[])
        state = engine.start(self.project.project_id, self.run.run_id, self.sources,
                             ('problem1', 'problem1'))
        self.assertEqual(RunStatus.PAUSED, state.status)
        self.assertEqual(PauseReason.AMBIGUOUS_DATA, state.pause_reason)
        self.assertEqual(Stage.PREFLIGHT, state.next_stage)

    def test_changed_input_copy_pauses_before_profile_stage_execution(self):
        csv_path = self.paths.root / self.project.relative_path / '00_inputs/data.csv'
        csv_path.write_text('x,y\n99,100\n', encoding='utf-8')
        events = []
        engine = FakeWorkflowEngine(self.paths, self.projects, self.runs, events=events)
        state = engine.start(self.project.project_id, self.run.run_id, self.sources, self.selected)
        self.assertEqual(RunStatus.PAUSED, state.status)
        self.assertEqual(PauseReason.INPUT_CHANGED, state.pause_reason)
        self.assertEqual(Stage.DATA_PROFILE, state.next_stage)
        self.assertEqual([(Stage.PREFLIGHT, 0)], events)

    def test_authentication_and_unsupported_template_are_explicit_pause_branches(self):
        for stage, reason in ((Stage.PREFLIGHT, PauseReason.AUTH_REQUIRED),
                              (Stage.CODE_GENERATION, PauseReason.QUOTA_EXHAUSTED),
                              (Stage.DOCX_QA, PauseReason.UNSUPPORTED_TEMPLATE)):
            with self.subTest(stage=stage):
                self.tearDown()
                self.setUp()
                events = []
                engine = FakeWorkflowEngine(self.paths, self.projects, self.runs, events=events,
                    responses={stage: [(StageStatus.PAUSED, reason)]})
                state = engine.start(self.project.project_id, self.run.run_id, self.sources, self.selected)
                self.assertEqual(RunStatus.PAUSED, state.status)
                self.assertEqual(reason, state.pause_reason)
                self.assertEqual(stage, state.next_stage)


if __name__ == '__main__':
    unittest.main()