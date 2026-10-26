from types import SimpleNamespace

import pytest

from mathmodel_paper_studio.domain.models import RunStatus
from mathmodel_paper_studio.workflow.stages import PauseReason
from mathmodel_paper_studio.ui.run_panel import _status_detail


@pytest.mark.parametrize('status', [
    RunStatus.CREATED, RunStatus.RUNNING, RunStatus.COMPLETED, RunStatus.CANCELLED,
])
def test_old_diagnostic_is_not_shown_as_current_status(status):
    state = SimpleNamespace(status=status, diagnostics=('old request failed',), pause_reason=None)

    assert _status_detail(state) == ''
    assert state.diagnostics == ('old request failed',)


def test_paused_run_shows_latest_diagnostic_and_keeps_history():
    state = SimpleNamespace(status=RunStatus.PAUSED,
                            diagnostics=('older error', 'current pause'), pause_reason=None)

    assert _status_detail(state) == 'current pause'
    assert state.diagnostics == ('older error', 'current pause')


def test_pause_reason_is_a_fallback_when_pause_has_no_diagnostic():
    state = SimpleNamespace(status=RunStatus.PAUSED, diagnostics=(), pause_reason=PauseReason.RETRY_LIMIT)

    assert _status_detail(state) == f'暂停原因：{PauseReason.RETRY_LIMIT.value}'


def test_failed_run_shows_latest_diagnostic():
    state = SimpleNamespace(status=RunStatus.FAILED,
                            diagnostics=('older pause', 'current failed stage'), pause_reason=None)

    assert _status_detail(state) == 'current failed stage'
