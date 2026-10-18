"""Safe, in-memory progress events for a single workflow run."""
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
import re
from typing import Callable

from mathmodel_paper_studio.domain.models import Stage, identifier
from mathmodel_paper_studio.workflow.stages import ArtifactDigest


MAX_EVENT_TEXT_BYTES = 64 * 1024
_TRUNCATION_NOTICE = '…[内容因 64 KiB 上限截断]'
_OMITTED_TITLE = '部分运行事件未能显示'
_OMITTED_DETAIL = '界面未能接收部分运行事件；工作流状态与产物未受影响。'

_SECRET_ASSIGNMENT = re.compile(
    r'(?i)(?P<key>\b[\w.-]*(?:api[_ -]?key|access[_ -]?token|refresh[_ -]?token|'
    r'auth(?:orization)?|password|secret|token)\b)(?P<sep>\s*[:=]\s*)'
    r'(?P<value>"[^"]*"|\'[^\']*\'|[^\s,;<>]+)'
)
_BEARER = re.compile(r'(?i)\bBearer\s+[A-Za-z0-9._~+/-]+=*')
_TOKEN = re.compile(
    r'(?i)\b(?:sk-[A-Za-z0-9_-]{16,}|gh[pousr]_[A-Za-z0-9_]{20,}|'
    r'github_pat_[A-Za-z0-9_]{20,}|AKIA[0-9A-Z]{16}|'
    r'eyJ[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{5,})\b'
)


class ProgressKind(str, Enum):
    RUN_STARTED = 'run_started'
    RUN_PAUSED = 'run_paused'
    RUN_CANCELLED = 'run_cancelled'
    RUN_FAILED = 'run_failed'
    RUN_COMPLETED = 'run_completed'
    STAGE_STARTED = 'stage_started'
    STAGE_PROGRESS = 'stage_progress'
    STAGE_COMPLETED = 'stage_completed'
    STAGE_PAUSED = 'stage_paused'
    STAGE_FAILED = 'stage_failed'
    STAGE_CANCELLED = 'stage_cancelled'
    AGENT_REQUEST_STARTED = 'agent_request_started'
    AGENT_WAITING = 'agent_waiting'
    AGENT_MESSAGE = 'agent_message'
    AGENT_COMPLETED = 'agent_completed'
    AGENT_CANCELLED = 'agent_cancelled'
    AGENT_FAILED = 'agent_failed'
    AGENT_BLOCKED = 'agent_blocked'
    VALIDATION_STARTED = 'validation_started'
    VALIDATION_PASSED = 'validation_passed'
    VALIDATION_FAILED = 'validation_failed'
    ARTIFACT_CREATED = 'artifact_created'
    ARTIFACT_REVIEWED = 'artifact_reviewed'
    EVENTS_OMITTED = 'events_omitted'


@dataclass(frozen=True, slots=True)
class RunProgressEvent:
    run_id: str
    sequence: int
    occurred_at_utc: datetime
    kind: ProgressKind
    stage: Stage | None
    attempt: int | None
    item_id: str | None
    title: str
    detail: str = ''
    artifact: ArtifactDigest | None = None

    def __post_init__(self) -> None:
        identifier(self.run_id)
        if type(self.sequence) is not int or self.sequence < 1:
            raise ValueError('事件序号必须为正整数。')
        stamp = self.occurred_at_utc
        if (not isinstance(stamp, datetime) or stamp.tzinfo is None
                or stamp.utcoffset() != timezone.utc.utcoffset(stamp)):
            raise ValueError('事件时间必须是带 UTC 时区的时间。')
        object.__setattr__(self, 'kind', ProgressKind(self.kind))
        if self.stage is not None:
            object.__setattr__(self, 'stage', Stage(self.stage))
        if self.attempt is not None and (type(self.attempt) is not int or not 0 <= self.attempt <= 2):
            raise ValueError('尝试次数必须在 0–2 之间。')
        if self.item_id is not None:
            identifier(self.item_id)
        if not isinstance(self.title, str) or not self.title.strip() or '\0' in self.title:
            raise ValueError('事件标题不能为空或包含无效字符。')
        if not isinstance(self.detail, str) or '\0' in self.detail:
            raise ValueError('事件详情包含无效字符。')
        if len((self.title + self.detail).encode('utf-8')) > MAX_EVENT_TEXT_BYTES:
            raise ValueError('事件文本超过 64 KiB 上限。')
        if self.artifact is not None and not isinstance(self.artifact, ArtifactDigest):
            raise ValueError('事件产物摘要类型无效。')


class RunProgressReporter:
    """Assign sequence numbers and safely deliver display-only events."""

    def __init__(self, run_id: str, sink: Callable[[RunProgressEvent], object]):
        self.run_id = identifier(run_id)
        if not callable(sink):
            raise ValueError('事件接收器必须可调用。')
        self._sink = sink
        self._sequence = 0
        self._omission_pending = False

    def emit(
        self,
        kind: ProgressKind,
        title: str,
        *,
        stage: Stage | None = None,
        attempt: int | None = None,
        item_id: str | None = None,
        detail: str = '',
        artifact: ArtifactDigest | None = None,
    ) -> RunProgressEvent | None:
        if self._omission_pending:
            notice = self._new_event(
                ProgressKind.EVENTS_OMITTED, _OMITTED_TITLE, detail=_OMITTED_DETAIL,
            )
            if not self._deliver(notice):
                return None
            self._omission_pending = False

        safe_title, safe_detail = _sanitize_text(title, detail)
        event = self._new_event(
            kind, safe_title, stage=stage, attempt=attempt, item_id=item_id,
            detail=safe_detail, artifact=artifact,
        )
        if not self._deliver(event):
            self._omission_pending = True
            return None
        return event

    def _new_event(
        self,
        kind: ProgressKind,
        title: str,
        *,
        stage: Stage | None = None,
        attempt: int | None = None,
        item_id: str | None = None,
        detail: str = '',
        artifact: ArtifactDigest | None = None,
    ) -> RunProgressEvent:
        event = RunProgressEvent(
            run_id=self.run_id,
            sequence=self._sequence + 1,
            occurred_at_utc=datetime.now(timezone.utc),
            kind=kind,
            stage=stage,
            attempt=attempt,
            item_id=item_id,
            title=title,
            detail=detail,
            artifact=artifact,
        )
        self._sequence = event.sequence
        return event

    def _deliver(self, event: RunProgressEvent) -> bool:
        try:
            self._sink(event)
        except Exception:
            return False
        return True


def _sanitize_text(title: str, detail: str) -> tuple[str, str]:
    safe_title = _redact(title)
    safe_detail = _redact(detail)
    total = len((safe_title + safe_detail).encode('utf-8'))
    if total <= MAX_EVENT_TEXT_BYTES:
        return safe_title, safe_detail

    notice_bytes = len(_TRUNCATION_NOTICE.encode('utf-8'))
    title_bytes = len(safe_title.encode('utf-8'))
    if title_bytes >= MAX_EVENT_TEXT_BYTES - notice_bytes:
        return _truncate_utf8(safe_title, MAX_EVENT_TEXT_BYTES, _TRUNCATION_NOTICE), ''
    return safe_title, _truncate_utf8(
        safe_detail, MAX_EVENT_TEXT_BYTES - title_bytes, _TRUNCATION_NOTICE,
    )


def _truncate_utf8(value: str, max_bytes: int, notice: str) -> str:
    encoded = value.encode('utf-8')
    if len(encoded) <= max_bytes:
        return value
    marker = notice.encode('utf-8')
    prefix = encoded[:max(0, max_bytes - len(marker))].decode('utf-8', errors='ignore')
    return prefix + notice


def _redact(value: str) -> str:
    value = _BEARER.sub('Bearer [REDACTED]', value)
    value = _SECRET_ASSIGNMENT.sub(
        lambda match: f"{match.group('key')}{match.group('sep')}[REDACTED]", value,
    )
    return _TOKEN.sub('[REDACTED]', value)

