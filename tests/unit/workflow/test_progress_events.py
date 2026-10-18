from dataclasses import FrozenInstanceError
from datetime import datetime, timezone

import pytest

from mathmodel_paper_studio.domain.models import Stage
from mathmodel_paper_studio.workflow.progress import (
    ProgressKind,
    RunProgressEvent,
    RunProgressReporter,
)


def make_event(**overrides):
    values = {
        'run_id': 'run-1',
        'sequence': 1,
        'occurred_at_utc': datetime(2026, 10, 18, tzinfo=timezone.utc),
        'kind': ProgressKind.RUN_STARTED,
        'stage': None,
        'attempt': None,
        'item_id': None,
        'title': 'Run started',
        'detail': '',
        'artifact': None,
    }
    values.update(overrides)
    return RunProgressEvent(**values)


def test_event_is_immutable_and_rejects_invalid_run_sequence_and_timestamp():
    event = make_event()
    with pytest.raises(FrozenInstanceError):
        event.title = 'Changed'

    with pytest.raises(ValueError):
        make_event(run_id='bad id')
    with pytest.raises(ValueError):
        make_event(sequence=True)
    with pytest.raises(ValueError):
        make_event(occurred_at_utc=datetime(2026, 10, 18))


def test_event_validates_stage_attempt_and_provider_item_id():
    event = make_event(stage=Stage.CALCULATION, attempt=2, item_id='item-7')
    assert event.stage is Stage.CALCULATION
    assert event.attempt == 2
    assert event.item_id == 'item-7'

    with pytest.raises(ValueError):
        make_event(item_id='../secret')
    with pytest.raises(ValueError):
        make_event(attempt=3)


def test_reporter_assigns_monotonic_sequence_numbers():
    received = []
    reporter = RunProgressReporter('run-1', received.append)

    reporter.emit(ProgressKind.RUN_STARTED, 'Started')
    reporter.emit(ProgressKind.STAGE_STARTED, 'Stage started', stage=Stage.PREFLIGHT)

    assert [event.sequence for event in received] == [1, 2]
    assert [event.kind for event in received] == [ProgressKind.RUN_STARTED, ProgressKind.STAGE_STARTED]


def test_reporter_redacts_secret_patterns():
    received = []
    reporter = RunProgressReporter('run-1', received.append)
    raw = 'Authorization: Bearer opaque-secret-value x-api-key: deepseek-secret-value DEEPSEEK_API_KEY=other-secret-value api_key=sk-live-abcdefghijklmnopqrstuvwxyz'

    reporter.emit(ProgressKind.AGENT_MESSAGE, 'Agent response', detail=raw)

    displayed = received[0].detail
    assert 'opaque-secret-value' not in displayed
    assert 'sk-live-abcdefghijklmnopqrstuvwxyz' not in displayed
    assert 'deepseek-secret-value' not in displayed
    assert 'other-secret-value' not in displayed
    assert '[REDACTED]' in displayed


def test_reporter_truncates_utf8_text_at_limit_with_notice():
    received = []
    reporter = RunProgressReporter('run-1', received.append)
    reporter.emit(ProgressKind.AGENT_MESSAGE, 'Agent response', detail='数' * 50_000)

    event = received[0]
    assert len((event.title + event.detail).encode('utf-8')) <= 64 * 1024
    assert '[内容因 64 KiB 上限截断]' in event.detail
    event.detail.encode('utf-8').decode('utf-8')


def test_reporter_isolates_sink_failure_and_reports_generic_omission_after_recovery():
    received = []
    calls = 0

    def flaky_sink(event):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError('token=private-secret-value')
        received.append(event)

    reporter = RunProgressReporter('run-1', flaky_sink)
    assert reporter.emit(ProgressKind.AGENT_MESSAGE, 'private title', detail='token=private-secret-value') is None
    current = reporter.emit(ProgressKind.RUN_STARTED, 'Recovered')

    assert [event.kind for event in received] == [ProgressKind.EVENTS_OMITTED, ProgressKind.RUN_STARTED]
    assert received[0].title == '部分运行事件未能显示'
    assert 'private' not in received[0].detail
    assert 'private-secret-value' not in received[0].detail
    assert received[0].sequence < current.sequence
    assert current.sequence == received[1].sequence

