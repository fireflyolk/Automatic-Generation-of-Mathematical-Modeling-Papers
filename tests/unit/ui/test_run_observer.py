from datetime import datetime, timezone
from unittest.mock import patch

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QWidget

from mathmodel_paper_studio.domain.models import Stage
from mathmodel_paper_studio.ui.run_observer import (
    MAX_OBSERVER_CHARACTERS,
    RunObserverDialog,
)
from mathmodel_paper_studio.workflow.progress import ProgressKind, RunProgressEvent


APP = None


def _application():
    global APP
    if APP is None:
        APP = QApplication.instance() or QApplication([])
    return APP


def _dialog():
    _application()
    dialog = RunObserverDialog()
    dialog.reset_for_run('run-observer-1')
    return dialog


def _event(sequence, kind, detail='', *, run_id='run-observer-1', stage=None):
    return RunProgressEvent(
        run_id, sequence, datetime.now(timezone.utc), kind,
        Stage.MODEL_PROPOSAL if stage is None else stage,
        0, None, '事件', detail,
    )


def test_stream_fragments_preserve_whitespace_and_code_as_plain_text():
    dialog = _dialog()
    try:
        dialog.append_event(_event(1, ProgressKind.STAGE_STARTED))
        dialog.append_event(_event(2, ProgressKind.AGENT_REQUEST_STARTED))
        fragments = ['<b>模型</b>\n```python\n', 'x = ', '1\n  print(x)\n```']
        for sequence, fragment in enumerate(fragments, 3):
            assert dialog.append_event(_event(sequence, ProgressKind.AGENT_MESSAGE, fragment))

        assert ''.join(fragments) in dialog.text_view.toPlainText()
        assert dialog.text_view.isReadOnly()
        assert '尚未校验' in dialog.draft_notice.text()
        assert '模型方案' in dialog.status_label.text()
    finally:
        dialog.close()


def test_waiting_and_completion_do_not_display_reasoning_detail():
    dialog = _dialog()
    try:
        dialog.append_event(_event(1, ProgressKind.AGENT_WAITING, 'private reasoning sentinel'))
        assert '等待正文' in dialog.status_label.text()
        assert 'private reasoning sentinel' not in dialog.text_view.toPlainText()
        dialog.append_event(_event(2, ProgressKind.AGENT_MESSAGE, '草稿正文'))
        dialog.append_event(_event(3, ProgressKind.AGENT_COMPLETED, 'reasoning must stay hidden'))
        assert '等待校验' in dialog.status_label.text()
        assert 'reasoning must stay hidden' not in dialog.text_view.toPlainText()
    finally:
        dialog.close()


def test_retry_and_cancellation_mark_draft_discarded_and_separate_requests():
    dialog = _dialog()
    try:
        dialog.append_event(_event(1, ProgressKind.AGENT_REQUEST_STARTED))
        dialog.append_event(_event(2, ProgressKind.AGENT_MESSAGE, '上一份正文'))
        dialog.append_event(_event(3, ProgressKind('agent_retrying')))
        dialog.append_event(_event(4, ProgressKind.AGENT_REQUEST_STARTED))
        dialog.append_event(_event(5, ProgressKind.AGENT_MESSAGE, '重试正文'))
        dialog.append_event(_event(6, ProgressKind.RUN_CANCELLED))

        text = dialog.text_view.toPlainText()
        assert text.index('上一份正文') < text.index('草稿已丢弃') < text.index('重试正文')
        assert text.count('请求开始') == 2
        assert '已取消' in dialog.status_label.text()
        assert '尚未校验' in dialog.draft_notice.text()
    finally:
        dialog.close()


def test_wrong_run_duplicate_and_out_of_order_events_are_ignored():
    dialog = _dialog()
    try:
        assert dialog.append_event(_event(5, ProgressKind.AGENT_MESSAGE, '最新正文'))
        before = dialog.text_view.toPlainText()
        assert not dialog.append_event(_event(6, ProgressKind.AGENT_MESSAGE, '别的运行', run_id='other-run'))
        assert not dialog.append_event(_event(5, ProgressKind.AGENT_MESSAGE, '重复'))
        assert not dialog.append_event(_event(4, ProgressKind.AGENT_MESSAGE, '过期'))
        assert not dialog.append_event(object())
        assert dialog.text_view.toPlainText() == before
    finally:
        dialog.close()


def test_same_run_resume_preserves_text_but_new_run_clears_and_accepts_fresh_sequence():
    dialog = _dialog()
    try:
        dialog.append_event(_event(8, ProgressKind.AGENT_MESSAGE, '保留正文'))
        dialog.reset_for_run('run-observer-1')
        assert '保留正文' in dialog.text_view.toPlainText()
        dialog.reset_for_run('run-observer-2')
        assert dialog.run_id == 'run-observer-2'
        assert dialog.text_view.toPlainText() == ''
        assert dialog.append_event(_event(1, ProgressKind.AGENT_MESSAGE, '新正文', run_id='run-observer-2'))
    finally:
        dialog.close()


def test_retained_text_is_bounded_with_visible_truncation_notice_and_recent_tail():
    dialog = _dialog()
    try:
        for sequence in range(1, 13):
            dialog.append_event(_event(sequence, ProgressKind.AGENT_MESSAGE, '数' * 19000))
        dialog.append_event(_event(13, ProgressKind.AGENT_MESSAGE, '尾部已到达'))
        # Each event is below the progress protocol's own 64 KiB limit.
        for sequence in range(14, 19):
            dialog.append_event(_event(sequence, ProgressKind.AGENT_MESSAGE, '新' * 19000))
        dialog.append_event(_event(19, ProgressKind.AGENT_MESSAGE, '最终尾部'))
        retained = dialog.text_view.toPlainText()
        assert len(retained) <= MAX_OBSERVER_CHARACTERS
        assert '较早内容已移出观察窗口' in retained
        assert retained.endswith('最终尾部')
    finally:
        dialog.close()


def test_follow_off_preserves_scroll_position_when_more_text_arrives():
    dialog = _dialog()
    try:
        dialog.resize(600, 340)
        dialog.show()
        dialog.append_event(_event(1, ProgressKind.AGENT_MESSAGE, '\n'.join(str(i) for i in range(400))))
        QApplication.processEvents()
        dialog.follow_checkbox.setChecked(False)
        scrollbar = dialog.text_view.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum() // 3)
        previous = scrollbar.value()

        dialog.append_event(_event(2, ProgressKind.AGENT_MESSAGE, '\n新内容' * 80))
        QApplication.processEvents()

        assert scrollbar.value() == previous
        dialog.follow_checkbox.setChecked(True)
        QApplication.processEvents()
        assert scrollbar.value() == scrollbar.maximum()
    finally:
        dialog.close()


def test_clear_changes_only_view_and_copy_requires_explicit_button_click():
    dialog = _dialog()
    try:
        with patch('mathmodel_paper_studio.ui.run_observer.QApplication.clipboard') as clipboard:
            dialog.append_event(_event(1, ProgressKind.AGENT_MESSAGE, '待复制正文'))
            dialog.show()
            QApplication.processEvents()
            dialog.text_view.setFocus()
            QTest.keyClick(dialog, Qt.Key.Key_Return)
            clipboard.assert_not_called()
            dialog.copy_button.click()
            clipboard.return_value.setText.assert_called_once_with(dialog.text_view.toPlainText())

        dialog.clear_button.click()
        assert dialog.text_view.toPlainText() == ''
        assert dialog.run_id == 'run-observer-1'
        assert not dialog.append_event(_event(1, ProgressKind.AGENT_MESSAGE, '不可重放'))
        assert dialog.append_event(_event(2, ProgressKind.AGENT_MESSAGE, '新到正文'))
        assert dialog.text_view.toPlainText().endswith('新到正文')
    finally:
        dialog.close()


def test_window_is_modeless_and_reusable_after_close():
    _application()
    parent = QWidget()
    dialog = RunObserverDialog(parent)
    dialog.reset_for_run('run-observer-1')
    dialog.append_event(_event(1, ProgressKind.AGENT_MESSAGE, '关闭后保留'))
    try:
        dialog.show()
        QApplication.processEvents()
        assert not dialog.isModal()
        assert not dialog.testAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        dialog.close()
        assert not dialog.isVisible()
        dialog.show()
        QApplication.processEvents()
        assert dialog.isVisible()
        assert '关闭后保留' in dialog.text_view.toPlainText()
    finally:
        dialog.close()
        parent.close()


def test_stage_and_terminal_statuses_are_visible_without_output_validation_claim():
    dialog = _dialog()
    try:
        for sequence, kind in enumerate((
            ProgressKind.STAGE_STARTED, ProgressKind.STAGE_COMPLETED,
            ProgressKind.STAGE_PAUSED, ProgressKind.STAGE_FAILED,
            ProgressKind.RUN_COMPLETED,
        ), 1):
            dialog.append_event(_event(sequence, kind))
        text = dialog.text_view.toPlainText()
        assert '模型方案' in text
        assert '阶段完成' in text
        assert '已暂停' in text
        assert '失败' in text
        assert '运行完成' in dialog.status_label.text()
        assert '尚未校验' in dialog.draft_notice.text()
    finally:
        dialog.close()
