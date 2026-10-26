"""Codex receives a closed shape derived only from trusted paper examples."""
import copy
import json

import pytest

from mathmodel_paper_studio.domain.models import Stage
from mathmodel_paper_studio.workflow.live_engine import _SCHEMAS, _paper_batch_schema
from mathmodel_paper_studio.workflow.paper_output_schema import paper_output_schema


def assert_closed_objects(schema):
    if schema['type'] == 'object':
        assert schema['additionalProperties'] is False
        assert schema['required'] == list(schema['properties'])
        for child in schema['properties'].values():
            assert_closed_objects(child)
    elif schema['type'] == 'array':
        assert_closed_objects(schema['items'])
    else:
        assert schema == {'type': 'string'}


@pytest.mark.parametrize('fields, section_keys, flowcharts', [
    (('title', 'keywords', 'sections', 'equations', 'flowcharts'),
     ('abstract', 'problem_restatement'), 0),
    (('sections', 'equations', 'flowcharts'), ('problem_analysis',), 1),
    (('sections', 'equations', 'flowcharts'), ('assumptions', 'notation'), 0),
    (('sections', 'equations', 'flowcharts'), ('model',), 0),
    (('sections', 'equations', 'flowcharts'), ('model',), 1),
    (('sections', 'equations', 'flowcharts'), ('solution', 'computational_complexity'), 1),
    (('sections', 'equations', 'flowcharts', 'tables', 'figures'),
     ('results', 'verification', 'sensitivity_analysis', 'robustness_check'), 1),
    (('sections', 'equations', 'flowcharts', 'references'), ('conclusion', 'appendix'), 0),
])
def test_real_batch_examples_preserve_exact_top_level_and_leaf_shapes(
        fields, section_keys, flowcharts):
    example = _paper_batch_schema(fields, section_keys, flowchart_count=flowcharts,
                                 table_keys=('results_table_1',),
                                 figure_keys=('results_figure_1',))
    original = copy.deepcopy(example)
    schema = paper_output_schema(example)
    assert_closed_objects(schema)
    assert schema['required'] == list(example)
    assert set(schema['properties']) == set(fields)
    section = schema['properties']['sections']['items']['properties']
    assert set(section) == {'section_key', 'paragraphs', 'evidence_ids', 'subsections'}
    leaf = section['subsections']['items']
    assert set(leaf['properties']) == {'title', 'paragraphs', 'evidence_ids'}
    assert 'subsections' not in leaf['properties']
    assert set(schema['properties']['equations']['items']['properties']) == {
        'section_key', 'subsection_index', 'expression', 'description', 'evidence_ids'}
    assert not {'equations', 'flowcharts', 'tables', 'figures'}.intersection(section)
    if flowcharts:
        flowchart = schema['properties']['flowcharts']['items']['properties']
        assert set(flowchart) == {'section_key', 'caption', 'explanation', 'nodes', 'edges'}
        assert set(flowchart['nodes']['items']['properties']) == {'id', 'label', 'kind'}
        assert set(flowchart['edges']['items']['properties']) == {'from', 'to', 'label'}
    else:
        assert schema['properties']['flowcharts'] == {
            'type': 'array', 'items': {'type': 'string'}, 'maxItems': 0}
    for artifact in ('tables', 'figures'):
        if artifact in fields:
            assert set(schema['properties'][artifact]['items']['properties']) == {
                'section_key', 'evidence_id', 'caption'}
    if 'references' in fields:
        assert schema['properties']['references'] == {
            'type': 'array', 'items': {'type': 'string'}, 'maxItems': 0}
    assert example == original
    assert json.loads(json.dumps(schema)) == schema


def test_full_paper_example_has_closed_references_and_artifact_fields():
    example = _SCHEMAS[Stage.PAPER_DRAFTING]
    original = copy.deepcopy(example)
    schema = paper_output_schema(example)
    assert_closed_objects(schema)
    assert set(schema['properties']) == {
        'title', 'keywords', 'sections', 'equations', 'tables', 'figures', 'references'}
    assert schema['properties']['references']['maxItems'] == 0
    assert set(schema['properties']['tables']['items']['properties']) == {
        'section_key', 'evidence_id', 'caption'}
    assert set(schema['properties']['figures']['items']['properties']) == {
        'section_key', 'evidence_id', 'caption'}
    assert example == original


def test_string_examples_are_only_types_not_literals_or_evidence_authority():
    assert paper_output_schema({'evidence_ids': ['ev_exact_sample'],
                                'section_key': 'model'}) == {
        'type': 'object',
        'properties': {
            'evidence_ids': {'type': 'array', 'items': {'type': 'string'}},
            'section_key': {'type': 'string'},
        },
        'required': ['evidence_ids', 'section_key'],
        'additionalProperties': False,
    }
    assert paper_output_schema('a string') == {'type': 'string'}


def test_all_returned_objects_are_fresh_including_shared_example_nodes():
    shared = {'caption': 'text'}
    example = {'first': [shared], 'second': [shared]}
    first = paper_output_schema(example)
    second = paper_output_schema(example)
    first['properties']['first']['items']['properties']['caption']['type'] = 'invalid'
    assert first['properties']['second']['items']['properties']['caption']['type'] == 'string'
    assert second['properties']['first']['items']['properties']['caption']['type'] == 'string'
    assert example == {'first': [{'caption': 'text'}], 'second': [{'caption': 'text'}]}


@pytest.mark.parametrize('value', [None, False, 1, 1.2, ('text',), b'text', {'text'}])
def test_rejects_non_shape_types_at_every_level(value):
    with pytest.raises(ValueError, match='type'):
        paper_output_schema(value)
    with pytest.raises(ValueError, match='type'):
        paper_output_schema({'nested': [value]})


@pytest.mark.parametrize('value', [['one', 'two'], [{'first': 'a'}, {'second': 'b'}]])
def test_rejects_multiple_array_examples(value):
    with pytest.raises(ValueError, match='one'):
        paper_output_schema({'items': value})


@pytest.mark.parametrize('value', [{1: 'a'}, {'outer': {None: 'b'}}])
def test_rejects_non_string_object_keys(value):
    with pytest.raises(ValueError, match='key'):
        paper_output_schema(value)


def test_rejects_examples_beyond_depth_limit():
    value = 'leaf'
    for _ in range(17):
        value = {'nested': value}
    with pytest.raises(ValueError, match='depth'):
        paper_output_schema(value)


def test_rejects_examples_beyond_node_limit():
    with pytest.raises(ValueError, match='nodes'):
        paper_output_schema({str(index): 'leaf' for index in range(4096)})


@pytest.mark.parametrize('value', ['x' * 65537, {'x' * 65537: 'leaf'},
                                   {'a': 'x' * 32768, 'b': 'x' * 32768}],
                         ids=('large-string', 'large-key', 'cumulative-text'))
def test_rejects_oversized_example_text_and_keys(value):
    with pytest.raises(ValueError, match='characters'):
        paper_output_schema(value)


def test_rejects_cycles_without_recursion_failure():
    value = {}
    value['self'] = value
    with pytest.raises(ValueError, match='cycle|depth'):
        paper_output_schema(value)


def test_examples_at_size_and_depth_limits_remain_supported():
    value = 'leaf'
    for _ in range(16):
        value = {'nested': value}
    assert paper_output_schema(value)['type'] == 'object'
    assert len(paper_output_schema({str(index): 'leaf' for index in range(4095)})[
        'properties']) == 4095
    assert paper_output_schema({'x': 'y' * 65535})['properties']['x'] == {'type': 'string'}


def test_empty_object_and_array_have_explicit_closed_shapes():
    assert paper_output_schema({}) == {
        'type': 'object', 'properties': {}, 'required': [], 'additionalProperties': False}
    assert paper_output_schema([]) == {
        'type': 'array', 'items': {'type': 'string'}, 'maxItems': 0}
