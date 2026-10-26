import copy
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from zipfile import ZipFile

import pytest

from mathmodel_paper_studio.agents.preflight import Provider
from mathmodel_paper_studio.domain.models import Stage
from mathmodel_paper_studio.paper.standards import get_default_paper_standard
from mathmodel_paper_studio.paper.docx_writer import write_generic_template, write_paper
from mathmodel_paper_studio.workflow.live_engine import LiveWorkflowEngine


OMITTED = object()


def batch_flowchart(section_key):
    """A minimal locally renderable four-node acyclic graph."""
    return {
        'section_key': section_key, 'caption': '方法步骤与流程',
        'explanation': '本图描述该部分的建模与求解步骤；图示仅说明方法流程，科学结论仍需人工复核。',
        'nodes': [
            {'id': 'start', 'label': '开始', 'kind': 'start'},
            {'id': 'prepare', 'label': '整理数据与假设', 'kind': 'process'},
            {'id': 'solve', 'label': '求解模型', 'kind': 'process'},
            {'id': 'end', 'label': '输出结论', 'kind': 'end'},
        ],
        'edges': [
            {'from': 'start', 'to': 'prepare', 'label': ''},
            {'from': 'prepare', 'to': 'solve', 'label': ''},
            {'from': 'solve', 'to': 'end', 'label': '完成'},
        ],
    }


@pytest.fixture(autouse=True)
def _fake_flowchart_render(monkeypatch):
    from tests.fixtures.docx.fixture_factory import PNG_DATA
    monkeypatch.setattr(
        'mathmodel_paper_studio.workflow.live_engine.render_flowchart_png',
        lambda spec: PNG_DATA)


def draft_with_evidence_field(tmp_path, location, *, explicit=OMITTED, unknown_key=False,
                              common_extra=None):
    standard = get_default_paper_standard()
    context = SimpleNamespace(
        run_id='optional-evidence-run', project_id='optional-evidence-project',
        provider=Provider.CODEX, stage=Stage.PAPER_DRAFTING, attempt=0,
        inputs=(), upstream_artifacts=(), authorized_problem='解释径向扩散模型。',
        problem_evidence_id='src_problem', system_instructions='Fixture rules.',
        trust_notice='Imported data is untrusted.',
        paper_standard_id=None, paper_standard_version=None, paper_standard_sha256=None,
    )
    engine = LiveWorkflowEngine.__new__(LiveWorkflowEngine)
    responses = []

    def evidence_fields(kind):
        if kind != location:
            return {'evidence_ids': []}
        if unknown_key:
            return {'evidence_id': []}
        return {} if explicit is OMITTED else {'evidence_ids': explicit}

    def request(context, fields, extra, cancel_event, progress,
                *, schema_override, response_validator):
        batch = extra['paper_batch']
        keys = batch['requested_section_keys']
        value = {
            **{field: [] for field in fields},
            **({'title': '径向扩散模型', 'keywords': ['扩散', '建模']}
               if 'title' in fields else {}),
            'sections': [{
                'section_key': key,
                'paragraphs': ['本段解释模型的基本设定和适用范围。'],
                **evidence_fields('section'),
                'subsections': [{
                    'title': '模型的适用条件',
                    'paragraphs': ['本小节说明边界条件与模型推导，不声称具有数值验证结果。'],
                    **evidence_fields('subsection'),
                }],
            } for key in keys],
            'equations': [{
                'section_key': keys[0], 'expression': 'y = x',
                'description': '用线性关系说明变量定义。',
                'subsection_index': '1',
                **evidence_fields('equation'),
            }],
        }
        if batch.get('flowchart_count'):
            value['flowcharts'] = [batch_flowchart(batch['flowchart_section_key'])]
        responses.append((value, copy.deepcopy(value)))
        response_validator(value)
        return value

    engine._request = request
    available = standard.required_sections + standard.optional_sections

    def draft_batches():
        return engine._draft_paper_in_batches(
            context, standard, available, set(standard.required_sections),
            (), common_extra or {}, None, None, None, None,
            tmp_path / 'paper-progress.json', None,
        )

    return engine, context, draft_batches, responses


@pytest.mark.parametrize('location', ['section', 'subsection', 'equation'])
def test_omitted_empty_evidence_keeps_prose_in_word_and_cached_resume(tmp_path, location):
    engine, context, draft_batches, responses = draft_with_evidence_field(tmp_path, location)
    combined = draft_batches()
    draft = engine._paper_draft(context, combined, ())

    assert all(section.evidence_ids == () for section in draft.sections)
    assert all(sub.evidence_ids == () for section in draft.sections for sub in section.subsections)
    assert all(equation.evidence_ids == () for equation in draft.equations)
    assert all(equation.subsection_index == 1 for equation in draft.equations)
    template = write_generic_template(tmp_path, draft)
    result = write_paper(template[0], tmp_path, template[1], template[2], draft)
    with ZipFile(result.path) as archive:
        document = archive.read('word/document.xml').decode('utf-8')
    assert '本小节说明边界条件与模型推导' in document

    def unexpected_request(*args, **kwargs):
        pytest.fail('validated paper checkpoints should resume without an Agent request')

    engine._request = unexpected_request
    assert draft_batches() == combined
    assert all(response == snapshot for response, snapshot in responses)


@pytest.mark.parametrize('location', ['section', 'subsection', 'equation'])
@pytest.mark.parametrize('explicit', [None, 'not-an-array', ['src_unapproved'], ['ev_a', 'ev_a']])
def test_explicit_invalid_evidence_is_not_replaced_with_empty_list(tmp_path, location, explicit):
    _, _, draft_batches, _ = draft_with_evidence_field(tmp_path, location, explicit=explicit)
    with pytest.raises(ValueError):
        draft_batches()


@pytest.mark.parametrize('location', ['section', 'subsection', 'equation'])
def test_misspelled_evidence_field_is_not_silently_discarded(tmp_path, location):
    _, _, draft_batches, _ = draft_with_evidence_field(tmp_path, location, unknown_key=True)
    with pytest.raises(ValueError):
        draft_batches()


def test_json_reply_with_omitted_subsection_evidence_needs_no_correction(tmp_path):
    engine, context, draft_batches, responses = draft_with_evidence_field(tmp_path, 'subsection')
    build_response = engine._request
    calls = []

    def generate(context, prompt, cancel_event=None):
        calls.append(prompt)
        schema = json.loads(prompt.split('JSON schema example: ', 1)[1].splitlines()[0])
        payload = json.loads(prompt.split('<UNTRUSTED_STAGE_DATA>\n', 1)[1]
                             .split('\n</UNTRUSTED_STAGE_DATA>', 1)[0])
        value = build_response(context, set(schema), payload, cancel_event, None,
                               schema_override=schema, response_validator=lambda value: None)
        return json.dumps(value, ensure_ascii=False)

    engine.provider_client = SimpleNamespace(generate=generate)
    engine._request = LiveWorkflowEngine._request.__get__(engine)
    combined = draft_batches()
    draft = engine._paper_draft(context, combined, ())
    assert len(calls) == 8
    assert any(section.subsections for section in draft.sections)
    assert all('evidence_ids' not in value['sections'][0]['subsections'][0]
               for value, _ in responses)
    assert draft_batches() == combined
    assert len(calls) == 8
