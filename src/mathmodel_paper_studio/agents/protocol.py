"""Strict application-owned request/event protocol. Provider JSON is never trusted."""
from dataclasses import dataclass, field
from enum import Enum
from typing import Iterable, Protocol

from mathmodel_paper_studio.agents.preflight import AdapterCapabilities, ProviderStatus
from mathmodel_paper_studio.domain.models import Stage, identifier
from mathmodel_paper_studio.security.scope_manifest import RuntimeScope


class EventKind(str, Enum):
    STARTED = 'started'
    TEXT = 'text'
    TOOL_STARTED = 'tool_started'
    TOOL_FINISHED = 'tool_finished'
    COMPLETED = 'completed'
    FAILED = 'failed'
    CANCELLED = 'cancelled'
    BLOCKED = 'blocked'


TERMINAL_KINDS = frozenset((EventKind.COMPLETED, EventKind.FAILED, EventKind.CANCELLED, EventKind.BLOCKED))


@dataclass(frozen=True)
class AgentRequest:
    scope: RuntimeScope
    stage: Stage
    prompt: str = field(repr=False)

    def __post_init__(self):
        if not isinstance(self.scope, RuntimeScope):
            raise ValueError('请求缺少有效运行范围。')
        object.__setattr__(self, 'stage', Stage(self.stage))
        if self.stage not in self.scope.metadata.stages:
            raise ValueError('请求阶段未包含在本次范围中。')
        if not isinstance(self.prompt, str) or not self.prompt or len(self.prompt.encode('utf-8')) > 1024 * 1024 or '\0' in self.prompt:
            raise ValueError('请求内容为空、过长或包含不允许的字符。')

    @property
    def run_id(self) -> str:
        return self.scope.metadata.run_id


@dataclass(frozen=True)
class AgentEvent:
    run_id: str
    kind: EventKind
    text: str = ''
    item_id: str | None = None

    def __post_init__(self):
        identifier(self.run_id)
        object.__setattr__(self, 'kind', EventKind(self.kind))
        if not isinstance(self.text, str) or len(self.text.encode('utf-8')) > 65536 or '\0' in self.text:
            raise ValueError('事件文本类型、长度或字符无效。')
        if self.item_id is not None:
            identifier(self.item_id)


class AgentAdapter(Protocol):
    def probe(self) -> ProviderStatus: ...
    def capabilities(self) -> AdapterCapabilities: ...
    def stream(self, request: AgentRequest) -> Iterable[AgentEvent]: ...
    def cancel(self, run_id: str) -> None: ...
