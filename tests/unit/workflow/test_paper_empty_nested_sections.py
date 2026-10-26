import copy
import json
from types import SimpleNamespace
from zipfile import ZipFile

import pytest

from mathmodel_paper_studio.paper.docx_writer import write_generic_template, write_paper
from mathmodel_paper_studio.workflow.live_engine import LiveWorkflowEngine, _response_format_reason
from tests.unit.workflow.test_paper_optional_evidence import draft_with_evidence_field


def nested_workflow(tmp_path, children, *, extra_field=False):
    engine, context, run, _ = draft_with_evidence_field(tmp_path, 'subsection')
    build_response = engine._request
    calls, raw_responses = [], []

    def generate(context, prompt, cancel_event=None):
        calls.append(prompt)
        schema = json.loads(prompt.split('JSON schema example: ', 1)[1].splitlines()[0])
        extra = json.loads(prompt.split('<UNTRUSTED_STAGE_DATA>\n', 1)[1]
                           .split('\n</UNTRUSTED_STAGE_DATA>', 1)[0])
        value = build_response(context, set(schema), extra, cancel_event, None,
                               schema_override=schema, response_validator=lambda value: None)
        for section in value['sections']:
            for subsection in section['subsections']:
                subsection['subsections'] = copy.deepcopy(children)
                if extra_field:
                    subsection['unsupported'] = 'must remain invalid'
        raw_responses.append(copy.deepcopy(value))
        return json.dumps(value, ensure_ascii=False)

    engine.provider_client = SimpleNamespace(generate=generate)
    engine._request = LiveWorkflowEngine._request.__get__(engine)
    return engine, context, run, calls, raw_responses


def test_empty_nested_arrays_preserve_prose_in_word_and_raw_cached_replies(tmp_path):
    engine, context, run, calls, raw = nested_workflow(tmp_path, [])
    combined = run()
    assert len(calls) == 8
    draft = engine._paper_draft(context, combined, ())
    template = write_generic_template(tmp_path, draft)
    word = write_paper(template[0], tmp_path, template[1], template[2], draft)
    with ZipFile(word.path) as archive:
        xml = archive.read('word/document.xml').decode('utf-8')
    assert '模型的适用条件' in xml and '本小节说明边界条件与模型推导' in xml
    saved = json.loads((tmp_path / 'paper-progress.json').read_text('utf-8'))
    assert saved['batches']['opening']['response'] == raw[0]
    assert raw[0]['sections'][0]['subsections'][0]['subsections'] == []
    assert run() == combined
    assert len(calls) == 8


@pytest.mark.parametrize('children', [
    None, False, '', {},
    [{'title': '真实的下级内容', 'paragraphs': ['不能静默丢弃。'], 'evidence_ids': []}],
])
def test_invalid_or_nonempty_nested_content_is_still_rejected(tmp_path, children):
    _, _, run, _, _ = nested_workflow(tmp_path, children)
    with pytest.raises(ValueError):
        run()


def test_empty_children_do_not_hide_unrelated_unknown_fields(tmp_path):
    _, _, run, _, _ = nested_workflow(tmp_path, [], extra_field=True)
    with pytest.raises(ValueError):
        run()


def test_subsection_field_error_has_specific_repair_guidance_category():
    assert _response_format_reason(ValueError('论文分章小节字段无效。')) == 'subsection_shape'
