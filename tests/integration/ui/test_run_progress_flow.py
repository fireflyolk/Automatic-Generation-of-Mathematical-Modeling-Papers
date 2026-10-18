import unittest
import hashlib

from PySide6.QtCore import QThreadPool, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from mathmodel_paper_studio.domain.models import RunStatus, Stage
from mathmodel_paper_studio.agents.protocol import AgentEvent, EventKind
from mathmodel_paper_studio.execution.python_runner import RunArtifactLayout
from mathmodel_paper_studio.ui.run_panel import RunPanel
from mathmodel_paper_studio.workflow.progress import ProgressKind
from mathmodel_paper_studio.workflow.stages import WORKFLOW_STAGES
from tests.integration import test_live_workflow as _live_fixtures


APP = None


class _PauseOnceProgressProvider(_live_fixtures.ProgressFakeProvider):
    def __init__(self):
        super().__init__()
        self._paused_once = False

    def generate_with_progress(self, context, prompt, cancel_event=None, *, event_callback):
        if not self._paused_once:
            self._paused_once = True
            event_callback(AgentEvent(context.run_id, EventKind.STARTED))
            event_callback(AgentEvent(context.run_id, EventKind.FAILED))
            raise RuntimeError('fixture transient provider failure')
        return super().generate_with_progress(
            context, prompt, cancel_event=cancel_event, event_callback=event_callback,
        )


class RunProgressFlowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        global APP
        APP = QApplication.instance() or QApplication([])

    def setUp(self):
        self.workflow_fixture = _live_fixtures.LiveWorkflowTests(
            'test_composes_nine_real_host_stages_and_keeps_originals')
        self.workflow_fixture.setUp()
        self.addCleanup(self.workflow_fixture.doCleanups)
        self.workflow_fixture.provider = _live_fixtures.ProgressFakeProvider()
        self.panel = RunPanel(thread_pool=QThreadPool())
        self.panel.resize(1200, 760)
        self.panel.show()
        self.addCleanup(self.panel.close)
        QTest.qWait(10)

    def test_fake_live_run_renders_stage_agent_validation_and_artifact_events_in_order(self):
        reporter = self.panel.begin_progress_run(self.workflow_fixture.run.run_id)

        state = self.workflow_fixture.start(progress=reporter)
        APP.processEvents()

        self.assertIs(state.status, RunStatus.COMPLETED)
        events = self.panel.run_timeline.events
        self.assertGreater(len(events), 0)
        self.assertEqual(list(range(1, len(events) + 1)), [event.sequence for event in events])
        self.assertEqual(ProgressKind.RUN_STARTED, events[0].kind)
        self.assertEqual(ProgressKind.RUN_COMPLETED, events[-1].kind)

        stage_started = [event.stage for event in events
                         if event.kind is ProgressKind.STAGE_STARTED]
        self.assertEqual(list(WORKFLOW_STAGES), stage_started)
        self.assertTrue(any(event.kind is ProgressKind.AGENT_MESSAGE for event in events))
        self.assertTrue(any(event.kind is ProgressKind.VALIDATION_PASSED
                            and event.stage is Stage.RESULT_VALIDATION for event in events))
        artifact_events = [event for event in events
                           if event.kind is ProgressKind.ARTIFACT_CREATED]
        self.assertTrue(artifact_events)
        for artifact in artifact_events:
            stage_completion = next(
                index for index, event in enumerate(events)
                if event.kind is ProgressKind.STAGE_COMPLETED
                and event.stage is artifact.stage
                and event.attempt == artifact.attempt
            )
            self.assertLess(stage_completion, events.index(artifact))

        self.panel.run_timeline.filter_combo.setCurrentIndex(
            self.panel.run_timeline.filter_combo.findData('agent'))
        self.assertGreater(self.panel.run_timeline.event_list.count(), 0)
        visible_message = next(event for event in events
                               if event.kind is ProgressKind.AGENT_MESSAGE)
        message_row = next(
            row for row in range(self.panel.run_timeline.event_list.count())
            if self.panel.run_timeline.event_list.item(row).data(Qt.ItemDataRole.UserRole)
            == visible_message.sequence
        )
        self.panel.run_timeline.event_list.setCurrentRow(message_row)
        self.assertIn('Visible fixture response',
                      self.panel.run_timeline.detail_view.toPlainText())

    def test_resume_appends_to_same_timeline_and_new_run_clears_it(self):
        self.workflow_fixture.provider = _PauseOnceProgressProvider()
        self.workflow_fixture.runner = _live_fixtures.FakeComputation()
        engine = self.workflow_fixture.engine()
        reporter = self.panel.begin_progress_run(self.workflow_fixture.run.run_id)

        paused = self.workflow_fixture.start(engine, progress=reporter)
        self.assertIs(paused.status, RunStatus.PAUSED)
        first_count = self.panel.run_timeline.event_count
        self.assertGreater(first_count, 0)

        resumed_reporter = self.panel.begin_progress_run(self.workflow_fixture.run.run_id)
        resumed = engine.resume(
            self.workflow_fixture.run.run_id, self.workflow_fixture.sources,
            self.workflow_fixture.selected, progress=resumed_reporter,
        )
        APP.processEvents()

        self.assertIs(resumed.status, RunStatus.COMPLETED)
        self.assertIs(reporter, resumed_reporter)
        self.assertGreater(self.panel.run_timeline.event_count, first_count)
        self.assertEqual(
            list(range(1, self.panel.run_timeline.event_count + 1)),
            [event.sequence for event in self.panel.run_timeline.events],
        )

        next_reporter = self.panel.begin_progress_run('b' * 32)
        self.assertIsNot(reporter, next_reporter)
        self.assertEqual(0, self.panel.run_timeline.event_count)
        self.assertEqual('b' * 32, self.panel.run_timeline.run_id)

    def test_progress_events_are_not_serialized_to_checkpoint(self):
        reporter = self.panel.begin_progress_run(self.workflow_fixture.run.run_id)

        state = self.workflow_fixture.start(progress=reporter)
        run_record = self.workflow_fixture.runs.get(self.workflow_fixture.run.run_id)
        layout = RunArtifactLayout(
            self.workflow_fixture.paths, self.workflow_fixture.project, run_record,
        )
        checkpoint_path = next(
            path for path in layout.root.glob('checkpoint-v*.json')
            if hashlib.sha256(path.read_bytes()).hexdigest() == run_record.checkpoint_sha256
        )
        serialized = checkpoint_path.read_text(encoding='utf-8')

        self.assertIs(state.status, RunStatus.COMPLETED)
        self.assertNotIn('Visible fixture response', serialized)
        self.assertNotIn('progress_events', serialized)


if __name__ == '__main__':
    unittest.main()
