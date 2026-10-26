import pytest

from mathmodel_paper_studio.paper.quality import (
    batch_problem_coverage, problem_coverage_evidence, problem_scopes, validate_problem_coverage,
)
from mathmodel_paper_studio.paper.standards import get_default_paper_standard


def test_default_standard_uses_the_accepted_manuscript_scale_without_count_padding():
    standard = get_default_paper_standard()

    assert standard.version == '1.2.0'
    assert standard.target_pages == 14
    assert standard.minimum_pages == 10
    assert standard.maximum_pages == 20
    assert standard.target_body_characters == 7_500
    assert standard.minimum_body_characters == 7_000
    assert standard.recommended_equation_count is None
    assert standard.recommended_flowchart_count is None
    guidance = standard.guidance_for('paper_drafting')
    assert '逐项覆盖' in guidance
    assert '建议至少 12 个' not in guidance
    assert '人工复核' not in guidance


def test_problem_scopes_keep_stable_question_ids_and_chinese_labels():
    scopes = problem_scopes({'subproblems': ['估计蒸发速率', '规划干燥曲线']})

    assert scopes == (
        {'problem_id': 'problem_1', 'heading': '问题一', 'description': '估计蒸发速率'},
        {'problem_id': 'problem_2', 'heading': '问题二', 'description': '规划干燥曲线'},
    )


def test_problem_result_evidence_links_are_carried_to_the_quality_manifest():
    from types import SimpleNamespace

    scopes = problem_scopes({'subproblems': ['估计蒸发速率', '规划干燥曲线']})
    sections = (SimpleNamespace(section_key='results', subsections=(
        SimpleNamespace(title='问题一：速率结果', evidence_ids=('ev_result_table',)),
        SimpleNamespace(title='问题二：规划结果', evidence_ids=('ev_result_figure',)),
    )),)

    links = problem_coverage_evidence(sections, scopes)

    assert links == (
        {'problem_id': 'problem_1', 'section_key': 'results',
         'evidence_ids': ('ev_result_table',)},
        {'problem_id': 'problem_2', 'section_key': 'results',
         'evidence_ids': ('ev_result_figure',)},
    )


def test_batch_problem_coverage_requires_substantive_per_question_subsections():
    scopes = problem_scopes({'subproblems': ['估计蒸发速率', '规划干燥曲线']})
    requirement = batch_problem_coverage('results', scopes)
    sections = [{
        'section_key': 'results',
        'subsections': [
            {'title': '问题一：蒸发速率结果', 'paragraphs': ['蒸发速率约为 0.42 kg/h。' * 10,
                                                            '该结果由质量变化数据计算。' * 10],
             'evidence_ids': ['ev_result_table']},
            {'title': '问题二：曲线规划结果', 'paragraphs': ['干燥曲线满足边界条件。' * 10,
                                                            '规划结果保持温度约束。' * 10],
             'evidence_ids': ['ev_result_figure']},
        ],
    }]

    validate_problem_coverage(sections, requirement)

    sections[0]['subsections'][1]['paragraphs'] = ['结果。']
    with pytest.raises(ValueError, match='子问题覆盖'):
        validate_problem_coverage(sections, requirement)


def test_results_coverage_requires_per_problem_validated_evidence_links():
    scopes = problem_scopes({'subproblems': ['估计蒸发速率']})
    requirement = batch_problem_coverage('results', scopes)
    sections = [{
        'section_key': 'results',
        'subsections': [{
            'title': '问题一：蒸发速率结果',
            'paragraphs': ['蒸发速率根据模型求解并完成单位检查。' * 10,
                           '该结论与输入条件和边界限制一致。' * 10],
            'evidence_ids': [],
        }],
    }]

    with pytest.raises(ValueError, match='证据'):
        validate_problem_coverage(sections, requirement)


def test_problem_coverage_does_not_require_problem_sections_for_noncoverage_batches():
    scopes = problem_scopes({'subproblems': ['估计蒸发速率']})

    assert batch_problem_coverage('opening', scopes) is None
