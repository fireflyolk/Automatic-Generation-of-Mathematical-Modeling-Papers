import unittest

from mathmodel_paper_studio.agents.preflight import Provider
from mathmodel_paper_studio.domain.models import Run, RunStatus, Stage, StageStatus
from mathmodel_paper_studio.workflow.retry_policy import RetryPolicy
from mathmodel_paper_studio.workflow.stages import PauseReason, StageOutput, StageResult, WorkflowState, WORKFLOW_STAGES


def make_run():
    return Run('run1', 'demo1', Provider.CLAUDE, RunStatus.CREATED, '90_runs/run1')


class WorkflowStateTests(unittest.TestCase):
    def test_windows_reserved_output_names_are_rejected(self):
        for name in ('CON', 'NUL.csv', 'LPT1.log', 'COM9.json'):
            with self.subTest(name=name), self.assertRaises(ValueError):
                StageOutput(name, b'fixture')

    def test_order_accepts_next_stage_and_rejects_skips_or_duplicate_completion(self):
        state = WorkflowState.initial(make_run()).start()
        with self.assertRaises(ValueError):
            state.record(StageResult(Stage.DATA_PROFILE, 0, StageStatus.COMPLETED))
        state = state.record(StageResult(Stage.PREFLIGHT, 0, StageStatus.COMPLETED))
        self.assertEqual(Stage.DATA_PROFILE, state.next_stage)
        with self.assertRaises(ValueError):
            state.record(StageResult(Stage.PREFLIGHT, 0, StageStatus.COMPLETED))

    def test_pause_resume_and_cancel_follow_legal_run_transitions(self):
        state = WorkflowState.initial(make_run()).start()
        paused = state.record(StageResult(
            Stage.PREFLIGHT, 0, StageStatus.PAUSED,
            diagnostic='fresh preflight required', pause_reason=PauseReason.PREFLIGHT_FAILED,
        ))
        self.assertEqual(RunStatus.PAUSED, paused.status)
        resumed = paused.resume()
        self.assertEqual(RunStatus.RUNNING, resumed.status)
        self.assertEqual(Stage.PREFLIGHT, resumed.next_stage)
        cancelled = resumed.cancel()
        self.assertEqual(RunStatus.CANCELLED, cancelled.status)
        with self.assertRaises(ValueError):
            cancelled.resume()

    def test_calculation_repairs_are_bounded_to_two_and_keep_attempt_history(self):
        state = WorkflowState.initial(make_run()).start()
        for stage in WORKFLOW_STAGES:
            if stage is Stage.CALCULATION:
                break
            state = state.record(StageResult(stage, 0, StageStatus.COMPLETED))
        policy = RetryPolicy()
        for attempt in range(2):
            state = state.record(StageResult(
                Stage.CALCULATION, attempt, StageStatus.FAILED, diagnostic='bad output',
            ), retry_policy=policy)
            self.assertEqual(attempt + 1, state.next_attempt)
            self.assertEqual(RunStatus.RUNNING, state.status)
        state = state.record(StageResult(
            Stage.CALCULATION, 2, StageStatus.FAILED, diagnostic='still invalid',
        ), retry_policy=policy)
        self.assertEqual(RunStatus.PAUSED, state.status)
        self.assertEqual(PauseReason.RETRY_LIMIT, state.pause_reason)
        self.assertEqual([0, 1, 2], [r.attempt for r in state.stage_records if r.stage is Stage.CALCULATION])
        self.assertIsNone(policy.next_attempt(Stage.DATA_PROFILE, 0))
        with self.assertRaises(ValueError):
            RetryPolicy(max_repair_attempts=3)
        with self.assertRaises(ValueError):
            RetryPolicy(retryable_stages=(Stage.DOCX_QA,))
        with self.assertRaises(ValueError):
            policy.next_attempt(Stage.CALCULATION, 3)


if __name__ == '__main__':
    unittest.main()