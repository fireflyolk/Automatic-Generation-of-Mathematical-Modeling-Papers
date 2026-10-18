import unittest
from datetime import datetime, timezone

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from mathmodel_paper_studio.domain.models import Stage
from mathmodel_paper_studio.workflow.progress import ProgressKind, RunProgressEvent
from mathmodel_paper_studio.workflow.stages import ArtifactDigest
from mathmodel_paper_studio.ui.run_timeline import RunTimelineWidget


APP = None


def event(sequence, kind, title, *, run_id='a' * 32, detail='', stage=None,
          item_id=None, artifact=None):
    return RunProgressEvent(
        run_id=run_id,
        sequence=sequence,
        occurred_at_utc=datetime.now(timezone.utc),
        kind=kind,
        stage=stage,
        attempt=0 if stage is not None else None,
        item_id=item_id,
        title=title,
        detail=detail,
        artifact=artifact,
    )


class RunTimelineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        global APP
        APP = QApplication.instance() or QApplication([])

    def setUp(self):
        self.timeline = RunTimelineWidget()
        self.timeline.reset_for_run('a' * 32)
        self.addCleanup(self.timeline.close)

    def test_agent_message_detail_and_filter_are_visible(self):
        self.timeline.append_event(event(
            1, ProgressKind.STAGE_STARTED, '进入代码生成', stage=Stage.CODE_GENERATION,
        ))
        self.timeline.append_event(event(
            2, ProgressKind.AGENT_MESSAGE, 'Agent 输出', detail='已生成模型假设与输入校验。',
            stage=Stage.CODE_GENERATION, item_id='b' * 32,
        ))

        self.timeline.filter_combo.setCurrentIndex(
            self.timeline.filter_combo.findData('agent'))
        self.assertEqual(1, self.timeline.event_list.count())
        self.timeline.event_list.setCurrentRow(0)
        self.assertIn('已生成模型假设与输入校验。', self.timeline.detail_view.toPlainText())

    def test_long_agent_message_detail_is_not_trimmed_to_twelve_lines(self):
        lines = [f'Agent 消息第 {index} 行' for index in range(1, 21)]
        self.timeline.append_event(event(
            1, ProgressKind.AGENT_MESSAGE, 'Agent 输出', detail='\n'.join(lines),
        ))

        detail = self.timeline.detail_view.toPlainText()

        self.assertIn(lines[0], detail)
        self.assertIn(lines[-1], detail)

    def test_list_tooltip_does_not_duplicate_agent_message_body(self):
        message = '第一行可见消息\n第二行仍保留完整内容。'
        self.timeline.append_event(event(
            1, ProgressKind.AGENT_MESSAGE, 'Agent 输出', detail=message,
        ))

        self.assertIn(message, self.timeline.detail_view.toPlainText())
        self.assertNotIn(message, self.timeline.event_list.item(0).toolTip())

    def test_event_limit_and_new_run_clear_are_memory_only(self):
        for sequence in range(1, 2051):
            self.timeline.append_event(event(sequence, ProgressKind.STAGE_PROGRESS,
                                             f'进度 {sequence}', stage=Stage.PREFLIGHT))
        self.assertEqual(2048, self.timeline.event_count)
        self.assertIn('进度 2050', self.timeline.event_list.item(2047).text())

        self.timeline.reset_for_run('c' * 32)
        self.assertEqual(0, self.timeline.event_count)
        self.assertEqual(0, self.timeline.event_list.count())
        self.assertNotIn('进度 2050', self.timeline.detail_view.toPlainText())

    def test_artifact_review_status_updates_summary(self):
        artifact = ArtifactDigest(
            relative_path='90_runs/run1/analysis.csv',
            sha256='a' * 64,
            evidence_id='d' * 32,
            kind='table',
            stage=Stage.CALCULATION,
            attempt=0,
            delivery=1,
            size_bytes=42,
        )
        self.timeline.append_event(event(
            1, ProgressKind.ARTIFACT_CREATED, '计算产物已保存', stage=Stage.CALCULATION,
            item_id=artifact.evidence_id, artifact=artifact,
        ))

        self.timeline.set_artifact_review_status(artifact.evidence_id, 'passed', '哈希和路径检查通过')

        self.assertEqual(1, self.timeline.artifact_list.count())
        self.assertIn('哈希和路径检查通过', self.timeline.artifact_list.item(0).text())

    def test_timeline_rejects_wrong_run_event(self):
        self.timeline.append_event(event(1, ProgressKind.RUN_STARTED, '另一个运行', run_id='b' * 32))
        self.assertEqual(0, self.timeline.event_count)

    def test_timeline_preserves_scroll_when_not_at_bottom(self):
        for sequence in range(1, 80):
            self.timeline.append_event(event(sequence, ProgressKind.STAGE_PROGRESS,
                                             f'进度 {sequence}', stage=Stage.PREFLIGHT))
        self.timeline.resize(500, 400)
        self.timeline.show()
        QTest.qWait(20)
        bar = self.timeline.event_list.verticalScrollBar()
        bar.setValue(max(1, bar.maximum() // 2))
        before = bar.value()

        self.timeline.append_event(event(80, ProgressKind.STAGE_PROGRESS,
                                         '进度 80', stage=Stage.PREFLIGHT))
        APP.processEvents()

        self.assertEqual(before, bar.value())


if __name__ == '__main__':
    unittest.main()
