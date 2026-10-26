import copy
import json
from types import SimpleNamespace
from zipfile import ZipFile

import pytest

from mathmodel_paper_studio.paper.docx_qa import run_docx_qa
from mathmodel_paper_studio.paper.docx_writer import write_generic_template, write_paper
from mathmodel_paper_studio.workflow.live_engine import (
    LiveWorkflowEngine, _paper_batch_request_sha256,
)
from tests.unit.workflow.test_paper_optional_evidence import draft_with_evidence_field


def equation_workflow(tmp_path, counts, *, overflow_results_once=False,
                      minimum_equation_count=12, result_retry_equation_count=None,
                      duplicate_results_once=False):
    engine, context, run, _ = draft_with_evidence_field(
        tmp_path, 'subsection',
        common_extra={'paper_contract': {} if minimum_equation_count is None else {
            'minimum_equation_count': minimum_equation_count,
        }},
    )
    build_response = engine._request
    calls, request_records = [], {}
    overflowing = False
    batch_call_counts = {}

    def generate(context, prompt, cancel_event=None):
        nonlocal overflowing
        schema = json.loads(prompt.split('JSON schema example: ', 1)[1].splitlines()[0])
        extra = json.loads(prompt.split('<UNTRUSTED_STAGE_DATA>\n', 1)[1]
                           .split('\n</UNTRUSTED_STAGE_DATA>', 1)[0])
        batch = extra['paper_batch']
        batch_id = batch['batch_id']
        batch_call_index = batch_call_counts.get(batch_id, 0)
        batch_call_counts[batch_id] = batch_call_index + 1
        calls.append((batch_id, prompt))
        request_records[batch_id] = (set(schema), copy.deepcopy(extra), copy.deepcopy(schema))
        value = build_response(context, set(schema), extra, cancel_event, None,
                               schema_override=schema, response_validator=lambda value: None)
        count = counts[batch['ordinal'] - 1]
        if (batch_id == 'results' and batch_call_index > 0
                and result_retry_equation_count is not None):
            count = result_retry_equation_count
        if overflow_results_once and batch_id == 'results' and not overflowing:
            count = 1
            overflowing = True
        expression = (lambda index: f'{batch_id}_x_{index} = {index}')
        if duplicate_results_once and batch_id == 'results' and batch_call_index == 0:
            expression = lambda _index: 'x = 1'
        value['equations'] = [{
            'section_key': batch['requested_section_keys'][0],
            'subsection_index': '1',
            'expression': expression(index), 'description': f'变量 {index} 的定义。',
            'evidence_ids': [],
        } for index in range(count)]
        return json.dumps(value, ensure_ascii=False)

    engine.provider_client = SimpleNamespace(generate=generate)
    engine._request = LiveWorkflowEngine._request.__get__(engine)
    return engine, context, run, calls, request_records


def test_batches_do_not_include_formula_count_targets(tmp_path):
    _, _, run, _, request_records = equation_workflow(tmp_path, [0, 0, 3, 4, 3, 1, 1, 0])

    result = run()

    assert len(result['equations']) == 12
    assert all('minimum_equation_count' not in record[1]['paper_contract']
               for record in request_records.values())
    assert all('meaningful_equation_target' not in record[1]['paper_batch']
               for record in request_records.values())
    assert all('minimum_document_equations' not in record[1]['paper_batch']
               for record in request_records.values())


def test_batch_character_guidance_matches_default_body_target(tmp_path):
    from mathmodel_paper_studio.paper.standards import get_default_paper_standard

    _, _, run, _, request_records = equation_workflow(tmp_path, [0, 0, 3, 3, 3, 2, 1, 0])

    run()

    guided_characters = sum(
        record[1]['paper_batch']['target_substantive_characters']
        for record in request_records.values()
    )
    assert guided_characters == get_default_paper_standard().target_body_characters


def test_result_batch_is_not_retried_to_reach_a_formula_count(tmp_path):
    _, _, run, calls, _ = equation_workflow(
        tmp_path, [0, 0, 3, 3, 3, 2, 1, 0], result_retry_equation_count=4)

    result = run()

    assert len(result['equations']) == 12
    result_calls = [prompt for batch_id, prompt in calls if batch_id == 'results']
    assert len(result_calls) == 1


def test_zero_formulas_are_accepted_even_with_legacy_formula_quota(tmp_path):
    _, _, run, calls, _ = equation_workflow(
        tmp_path, [0, 0, 0, 0, 0, 0, 0, 0], minimum_equation_count=12,
        result_retry_equation_count=4)

    result = run()

    assert result['equations'] == []
    result_calls = [prompt for batch_id, prompt in calls if batch_id == 'results']
    assert len(result_calls) == 1


def test_duplicate_equations_do_not_trigger_formula_count_retry(tmp_path):
    _, _, run, calls, _ = equation_workflow(
        tmp_path, [0, 0, 3, 2, 2, 1, 4, 0],
        result_retry_equation_count=4, duplicate_results_once=True)

    result = run()

    assert len(result['equations']) == 12
    result_calls = [prompt for batch_id, prompt in calls if batch_id == 'results']
    assert len(result_calls) == 1


def test_internal_batching_without_contract_does_not_require_a_formula_count(tmp_path):
    _, _, run, calls, _ = equation_workflow(
        tmp_path, [0, 0, 0, 0, 0, 0, 0, 0], minimum_equation_count=None)

    result = run()

    assert result['equations'] == []
    result_calls = [prompt for batch_id, prompt in calls if batch_id == 'results']
    assert len(result_calls) == 1


def test_zero_equations_are_allowed_with_legacy_production_contract(tmp_path):
    _, _, run, calls, _ = equation_workflow(
        tmp_path, [0, 0, 0, 0, 0, 0, 0, 0], minimum_equation_count=12)

    result = run()

    assert result['equations'] == []
    result_calls = [prompt for batch_id, prompt in calls if batch_id == 'results']
    assert len(result_calls) == 1


def test_sixty_four_cached_equations_and_fourteen_results_equations_reach_word_qa(tmp_path):
    engine, context, run, calls, _ = equation_workflow(tmp_path, [0, 0, 4, 22, 19, 19, 14, 0])
    combined = run()
    assert len(combined['equations']) == 78
    assert len(calls) == 8
    draft = engine._paper_draft(context, combined, ())
    template = write_generic_template(tmp_path, draft)
    result = write_paper(template[0], tmp_path, template[1], template[2], draft)
    report = run_docx_qa(result.path, template[1], template[2], expected_equation_count=78)
    assert report.package_valid and report.equation_count == 78
    with ZipFile(result.path) as archive:
        document = archive.read('word/document.xml').decode('utf-8')
    assert document.count('<m:oMath>') == 78
    assert run() == combined
    assert len(calls) == 8


def test_legacy_six_batch_cache_is_reused_when_equation_budget_is_added(tmp_path):
    engine, context, run, calls, request_records = equation_workflow(
        tmp_path, [0, 0, 4, 22, 19, 19, 14, 0])
    expected = run()
    cache_path = tmp_path / 'paper-progress.json'
    cache = json.loads(cache_path.read_text('utf-8'))
    for batch_id in ('results', 'closing'):
        del cache['batches'][batch_id]
    for batch_id, record in cache['batches'].items():
        fields, payload, schema = copy.deepcopy(request_records[batch_id])
        extra = {
            'paper_contract': {},
            'paper_batch': {
                key: value for key, value in payload['paper_batch'].items()
                if key not in {'equation_budget', 'meaningful_equation_target',
                               'minimum_document_equations'}
            },
        }
        record['request_sha256'] = _paper_batch_request_sha256(context, fields, extra, schema)
    old_records = copy.deepcopy(cache['batches'])
    cache_path.write_text(json.dumps(cache, ensure_ascii=False), encoding='utf-8')
    calls.clear()
    assert run() == expected
    assert [batch_id for batch_id, _ in calls] == ['results', 'closing']
    saved = json.loads(cache_path.read_text('utf-8'))
    assert all(saved['batches'][key] == value for key, value in old_records.items())


def test_equation_budget_exhaustion_requests_empty_array_and_repairs_one_overflow(tmp_path):
    _, _, run, calls, records = equation_workflow(
        tmp_path, [0, 0, 4, 44, 40, 40, 0, 0], overflow_results_once=True)
    result = run()
    assert len(result['equations']) == 128
    assert len(calls) == 9
    result_calls = [prompt for batch_id, prompt in calls if batch_id == 'results']
    assert len(result_calls) == 2
    assert '公式数量额度' in result_calls[1]
    assert records['results'][1]['paper_batch']['equation_budget'] == {
        'document_maximum': 128, 'already_committed': 128,
        'remaining_for_document': 0, 'maximum_for_batch': 0,
    }
    assert records['results'][2]['equations'] == []


@pytest.mark.parametrize('counts', [
    [0, 0, 0, 65, 0, 0, 0, 0],
    [0, 0, 4, 44, 40, 40, 1, 0],
])
def test_single_batch_and_document_equation_limits_still_reject_overflow(tmp_path, counts):
    _, _, run, _, _ = equation_workflow(tmp_path, counts)
    with pytest.raises(ValueError):
        run()


@pytest.mark.parametrize('count', [True, -1, 129])
def test_word_qa_rejects_invalid_expected_equation_counts(tmp_path, count):
    engine, context, run, _, _ = equation_workflow(tmp_path, [0, 0, 3, 3, 3, 2, 1, 0])
    draft = engine._paper_draft(context, run(), ())
    template = write_generic_template(tmp_path, draft)
    result = write_paper(template[0], tmp_path, template[1], template[2], draft)
    with pytest.raises(ValueError):
        run_docx_qa(result.path, template[1], template[2], expected_equation_count=count)


@pytest.mark.parametrize('result_count', [17, 18])
def test_partial_remaining_budget_accepts_exact_capacity_and_rejects_one_more(tmp_path, result_count):
    _, _, run, _, records = equation_workflow(tmp_path, [0, 0, 4, 44, 40, 23, result_count, 0])
    if result_count == 18:
        with pytest.raises(ValueError):
            run()
    else:
        assert len(run()['equations']) == 128
    budget = records['results'][1]['paper_batch']['equation_budget']
    assert budget['already_committed'] == 111
    assert budget['remaining_for_document'] == budget['maximum_for_batch'] == 17
