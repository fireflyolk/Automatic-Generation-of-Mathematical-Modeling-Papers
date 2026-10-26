"""Actual provider retries need a distinct display boundary for partial drafts."""
from types import SimpleNamespace

import pytest

from mathmodel_paper_studio.agents.preflight import Provider
from mathmodel_paper_studio.agents.protocol import AgentEvent, EventKind
from mathmodel_paper_studio.domain.models import Stage
from mathmodel_paper_studio.workflow.live_engine import LiveWorkflowEngine
from mathmodel_paper_studio.workflow.progress import ProgressKind, RunProgressReporter


def context():
    return SimpleNamespace(
        provider=Provider.DEEPSEEK, run_id='preview_run', stage=Stage.PROBLEM_DECOMPOSITION,
        attempt=0, upstream_artifacts=(), authorized_problem='fixture',
        problem_evidence_id='problem1', inputs=(), system_instructions='', trust_notice='',
        paper_standard_id=None, paper_standard_version=None, paper_standard_sha256=None,
    )


class PreviewClient:
    def prepare_scope(self, context, prompt):
        return SimpleNamespace(provider='DeepSeek', stage=context.stage.value,
                               billable=True, tools_enabled=False)

    def generate_with_progress(self, context, prompt, cancel_event=None, *, event_callback):
        for kind, text in ((EventKind.STARTED, ''), (EventKind.TEXT, 'old partial'),
                           (EventKind.RETRYING, '上次草稿已丢弃，正在重新生成。'),
                           (EventKind.STARTED, ''), (EventKind.TEXT, 'new complete'),
                           (EventKind.COMPLETED, '')):
            event_callback(AgentEvent(context.run_id, kind, text=text))
        return '{"subproblems":["complete"]}'


def test_provider_length_retry_has_a_separate_safe_progress_boundary():
    engine = LiveWorkflowEngine.__new__(LiveWorkflowEngine)
    engine.provider_client = PreviewClient()
    engine.deterministic_seed = 0
    events = []
    reporter = RunProgressReporter('preview_run', events.append)

    value = engine._request(context(), {'subproblems'}, {}, progress=reporter)

    assert value == {'subproblems': ['complete']}
    kinds = [event.kind for event in events]
    assert kinds.count(ProgressKind.AGENT_RETRYING) == 1
    retry_index = kinds.index(ProgressKind.AGENT_RETRYING)
    assert any(event.detail == 'old partial' for event in events[:retry_index])
    assert any(event.detail == 'new complete' for event in events[retry_index + 1:])


def test_json_repair_discloses_discarded_draft_before_second_request():
    class InvalidJsonOnce(PreviewClient):
        calls = 0

        def generate_with_progress(self, context, prompt, cancel_event=None, *, event_callback):
            self.calls += 1
            event_callback(AgentEvent(context.run_id, EventKind.STARTED))
            event_callback(AgentEvent(context.run_id, EventKind.TEXT, text='visible draft'))
            event_callback(AgentEvent(context.run_id, EventKind.COMPLETED))
            return 'invalid json' if self.calls == 1 else '{"subproblems":["complete"]}'

    engine = LiveWorkflowEngine.__new__(LiveWorkflowEngine)
    engine.provider_client = InvalidJsonOnce()
    engine.deterministic_seed = 0
    events = []

    engine._request(context(), {'subproblems'}, {},
                    progress=RunProgressReporter('preview_run', events.append))

    retries = [event for event in events if event.kind.value == 'agent_retrying']
    assert len(retries) == 1
    assert '丢弃' in retries[0].detail


def test_last_invalid_json_draft_is_marked_discarded_before_stage_pauses():
    class InvalidJsonAlways(PreviewClient):
        def generate_with_progress(self, context, prompt, cancel_event=None, *, event_callback):
            event_callback(AgentEvent(context.run_id, EventKind.STARTED))
            event_callback(AgentEvent(context.run_id, EventKind.TEXT, text='invalid visible draft'))
            event_callback(AgentEvent(context.run_id, EventKind.COMPLETED))
            return 'invalid json'

    engine = LiveWorkflowEngine.__new__(LiveWorkflowEngine)
    engine.provider_client = InvalidJsonAlways()
    engine.deterministic_seed = 0
    events = []

    with pytest.raises(ValueError):
        engine._request(context(), {'subproblems'}, {},
                        progress=RunProgressReporter('preview_run', events.append))

    assert events[-1].kind is ProgressKind.AGENT_FAILED
    assert '丢弃' in events[-1].detail


def test_format_retry_includes_safe_validation_feedback_without_echoing_invalid_draft():
    class MissingRequiredOnce(PreviewClient):
        calls = 0
        prompts = []

        def generate_with_progress(self, context, prompt, cancel_event=None, *, event_callback):
            self.calls += 1
            self.prompts.append(prompt)
            event_callback(AgentEvent(context.run_id, EventKind.STARTED))
            event_callback(AgentEvent(context.run_id, EventKind.COMPLETED))
            return '{"subproblems":[]}' if self.calls == 1 else '{"subproblems":["complete"]}'

    def require_subproblems(value):
        if not value['subproblems']:
            raise ValueError('论文分章响应缺少指定的必需章节。')

    client = MissingRequiredOnce()
    engine = LiveWorkflowEngine.__new__(LiveWorkflowEngine)
    engine.provider_client = client
    engine.deterministic_seed = 0

    result = engine._request(
        context(), {'subproblems'}, {},
        progress=RunProgressReporter('preview_run', lambda event: None),
        response_validator=require_subproblems,
    )

    assert result == {'subproblems': ['complete']}
    assert len(client.prompts) == 2
    assert '必需章节' in client.prompts[1]
    assert '{"subproblems":[]}' not in client.prompts[1]


def test_paper_retry_explains_that_mapping_keys_must_not_conflict():
    class InvalidMappingOnce(PreviewClient):
        calls = 0
        prompts = []

        def generate_with_progress(self, context, prompt, cancel_event=None, *, event_callback):
            self.calls += 1
            self.prompts.append(prompt)
            event_callback(AgentEvent(context.run_id, EventKind.STARTED))
            event_callback(AgentEvent(context.run_id, EventKind.COMPLETED))
            return '{"subproblems":["complete"]}'

    validation_calls = 0

    def reject_duplicate_mapping(_value):
        nonlocal validation_calls
        validation_calls += 1
        if validation_calls == 1:
            raise ValueError('论文正文、表格或结果图的映射键重复。')

    client = InvalidMappingOnce()
    engine = LiveWorkflowEngine.__new__(LiveWorkflowEngine)
    engine.provider_client = client
    engine.deterministic_seed = 0

    result = engine._request(
        context(), {'subproblems'}, {},
        progress=RunProgressReporter('preview_run', lambda event: None),
        response_validator=reject_duplicate_mapping,
    )

    assert result == {'subproblems': ['complete']}
    assert '映射键不能与正文或彼此重复' in client.prompts[1]


def test_paper_retry_explains_how_to_repair_an_invalid_title():
    class InvalidTitleOnce(PreviewClient):
        calls = 0
        prompts = []

        def generate_with_progress(self, context, prompt, cancel_event=None, *, event_callback):
            self.calls += 1
            self.prompts.append(prompt)
            event_callback(AgentEvent(context.run_id, EventKind.STARTED))
            event_callback(AgentEvent(context.run_id, EventKind.COMPLETED))
            return '{"subproblems":["complete"]}'

    validation_calls = 0

    def reject_title_once(_value):
        nonlocal validation_calls
        validation_calls += 1
        if validation_calls == 1:
            raise ValueError('论文标题无效。')

    client = InvalidTitleOnce()
    engine = LiveWorkflowEngine.__new__(LiveWorkflowEngine)
    engine.provider_client = client
    engine.deterministic_seed = 0

    result = engine._request(
        context(), {'subproblems'}, {},
        progress=RunProgressReporter('preview_run', lambda event: None),
        response_validator=reject_title_once,
    )

    assert result == {'subproblems': ['complete']}
    assert 'title 必须是非空字符串' in client.prompts[1]


@pytest.mark.parametrize('invalid_ids, validation_error', [
    (['src_forbidden'], '正文引用未经验证的结果。'),
    (['ev_result_table', 'ev_result_table'], '论文分章正文包含重复证据标识。'),
    (['ev_wrong_kind'], '图表引用未经验证的结果。'),
])
def test_evidence_retry_identifies_allowed_ids_and_evidence_rules(invalid_ids, validation_error):
    class InvalidEvidenceOnce(PreviewClient):
        calls = 0
        prompts = []

        def generate_with_progress(self, context, prompt, cancel_event=None, *, event_callback):
            self.calls += 1
            self.prompts.append(prompt)
            event_callback(AgentEvent(context.run_id, EventKind.STARTED))
            event_callback(AgentEvent(context.run_id, EventKind.COMPLETED))
            import json
            payload = {'evidence_ids': invalid_ids if self.calls == 1 else []}
            return json.dumps(payload)

    def reject_unapproved_ids(value):
        if value['evidence_ids']:
            raise ValueError(validation_error)

    client = InvalidEvidenceOnce()
    engine = LiveWorkflowEngine.__new__(LiveWorkflowEngine)
    engine.provider_client = client
    engine.deterministic_seed = 0

    result = engine._request(
        context(), {'evidence_ids'}, {},
        progress=RunProgressReporter('preview_run', lambda event: None),
        response_validator=reject_unapproved_ids,
        schema_override={'evidence_ids': []},
    )

    assert result == {'evidence_ids': []}
    assert 'paper_contract.allowed_paper_evidence_ids' in client.prompts[1]
    assert '每个 evidence_ids 列表不得包含重复 ID' in client.prompts[1]
    assert '表格必须引用 kind=table 的 ID' in client.prompts[1]
    assert 'problem_evidence_id' in client.prompts[1]
    assert 'selected_inputs' in client.prompts[1]
    assert 'upstream 阶段工件 ID' in client.prompts[1]
    assert all(identifier not in client.prompts[1] for identifier in invalid_ids)
