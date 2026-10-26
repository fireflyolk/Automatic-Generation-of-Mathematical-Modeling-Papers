import copy

import pytest

from mathmodel_paper_studio.workflow.live_engine import _validate_paper_batch
from mathmodel_paper_studio.workflow.paper_response import normalize_optional_evidence


@pytest.mark.parametrize('field, anchor', [
    ('tables', 'results_table_1'), ('figures', 'results_figure_1'),
])
def test_result_artifact_without_evidence_is_still_rejected(field, anchor):
    value = {
        'sections': [{'section_key': 'model', 'paragraphs': ['解释模型。']}],
        'equations': [], 'flowcharts': [],
        field: [{'section_key': anchor, 'caption': '未经绑定的图表'}],
    }
    with pytest.raises(ValueError, match='图表字段'):
        _validate_paper_batch(normalize_optional_evidence(value), set(value),
                              ('model',), {'model'}, {'model'}, 0)


def test_valid_evidence_is_preserved_and_response_is_not_mutated():
    value = {
        'sections': [{
            'section_key': 'model', 'paragraphs': ['解释结果。'],
            'evidence_ids': ['ev_valid'],
            'subsections': [{'title': '适用条件', 'paragraphs': ['解释适用范围。']}],
        }],
        'equations': [{
            'section_key': 'model', 'expression': 'y = x',
            'subsection_index': '1', 'description': '关系说明', 'evidence_ids': ['ev_valid'],
        }],
        'tables': [{'section_key': 'results_table_1', 'evidence_id': 'ev_valid', 'caption': '结果'}],
    }
    before = copy.deepcopy(value)
    result = normalize_optional_evidence(value)
    assert result['sections'][0]['evidence_ids'] == ['ev_valid']
    assert result['sections'][0]['subsections'][0]['evidence_ids'] == []
    assert result['equations'][0]['evidence_ids'] == ['ev_valid']
    assert result['equations'][0]['subsection_index'] == '1'
    assert result['tables'] == [{'section_key': 'results_table_1', 'evidence_id': 'ev_valid', 'caption': '结果'}]
    assert value == before


def test_equation_subsection_index_must_reference_a_subsection_in_its_section():
    value = {
        'sections': [{
            'section_key': 'model', 'paragraphs': ['解释模型。'],
            'subsections': [{'title': '适用条件', 'paragraphs': ['说明边界。']}],
        }],
        'equations': [{
            'section_key': 'model', 'subsection_index': '1', 'expression': 'y=x',
            'description': '关系说明', 'evidence_ids': [],
        }],
        'flowcharts': [],
    }

    _validate_paper_batch(
        normalize_optional_evidence(value), set(value), ('model',), {'model'}, {'model'}, 0,
    )

    assert value['equations'][0]['subsection_index'] == '1'


@pytest.mark.parametrize('subsection_index', ['0', '2', 'one', 1, None])
def test_equation_rejects_invalid_subsection_indices(subsection_index):
    value = {
        'sections': [{
            'section_key': 'model', 'paragraphs': ['解释模型。'],
            'subsections': [{'title': '适用条件', 'paragraphs': ['说明边界。']}],
        }],
        'equations': [{
            'section_key': 'model', 'subsection_index': subsection_index, 'expression': 'y=x',
            'description': '关系说明', 'evidence_ids': [],
        }],
        'flowcharts': [],
    }

    with pytest.raises(ValueError):
        _validate_paper_batch(
            normalize_optional_evidence(value), set(value), ('model',), {'model'}, {'model'}, 0,
        )


@pytest.mark.parametrize('value', [
    None,
    {'sections': 'not-an-array', 'equations': [], 'flowcharts': []},
    {'sections': [None], 'equations': [], 'flowcharts': []},
    {'sections': [{'section_key': 'model', 'paragraphs': ['解释。'], 'subsections': None}],
     'equations': [], 'flowcharts': []},
    {'sections': [{'section_key': 'model', 'paragraphs': ['解释。'], 'subsections': [None]}],
     'equations': [], 'flowcharts': []},
    {'sections': [{'section_key': 'model', 'paragraphs': ['解释。']}],
     'equations': [None], 'flowcharts': []},
])
def test_invalid_paper_structures_remain_invalid(value):
    with pytest.raises(ValueError):
        _validate_paper_batch(normalize_optional_evidence(value),
                              {'sections', 'equations', 'flowcharts'},
                              ('model',), {'model'}, {'model'}, 0)
