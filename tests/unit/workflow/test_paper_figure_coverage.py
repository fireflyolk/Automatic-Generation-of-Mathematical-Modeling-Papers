import json
from types import SimpleNamespace

import pytest

from mathmodel_paper_studio.agents.preflight import Provider
from mathmodel_paper_studio.domain.models import Stage
from mathmodel_paper_studio.workflow.live_engine import (
    _input_figure_plan, _validate_generated_figure_count,
    LiveWorkflowEngine,
)
from mathmodel_paper_studio.workflow.stages import StageStatus


def input_profile(name, columns, row_count=12):
    return SimpleNamespace(
        kind='csv',
        relative_path=name,
        profile_json=json.dumps({'row_count': row_count, 'columns': columns}),
    )


def numeric_column(name, minimum, maximum):
    return {
        'name': name,
        'type': 'number',
        'numeric_range': {'min': minimum, 'max': maximum},
    }


def test_figure_plan_separates_measurements_and_uses_time_as_shared_x_axis():
    inputs = (
        input_profile('environment.csv', [
            {'name': '时间', 'type': 'date', 'numeric_range': None},
            numeric_column('温度', 18.0, 29.5),
            numeric_column('相对湿度', 32.0, 81.0),
            numeric_column('序号', 1, 12),
        ]),
        input_profile('radius.csv', [
            numeric_column('时间小时', 0, 72),
            numeric_column('半径毫米', 0.4, 5.8),
        ]),
    )

    plan = _input_figure_plan(inputs)

    assert [(item['source_name'], item['value_column'], item['plot_type'], item['x_axis']) for item in plan] == [
        ('environment.csv', '温度', 'observation_series', '时间'),
        ('environment.csv', '温度', 'distribution', None),
        ('environment.csv', '相对湿度', 'observation_series', '时间'),
        ('environment.csv', '相对湿度', 'distribution', None),
        ('radius.csv', '半径毫米', 'observation_series', '时间小时'),
        ('radius.csv', '半径毫米', 'distribution', None),
    ]


def test_figure_plan_skips_constant_and_identifier_columns_and_is_bounded():
    columns = [
        numeric_column('记录编号', 1, 100),
        numeric_column('恒定值', 5, 5),
        *[numeric_column(f'观测量{i}', 0, i + 1) for i in range(10)],
    ]

    plan = _input_figure_plan((input_profile('many.csv', columns),))

    assert len(plan) == 12
    assert len({item['value_column'] for item in plan}) == 6
    assert {item['plot_type'] for item in plan} == {'observation_series', 'distribution'}
    assert all(item['value_column'].startswith('观测量') for item in plan)
    assert all(item['x_axis'] == ('样本序号' if item['plot_type'] == 'observation_series' else None)
               for item in plan)


@pytest.mark.parametrize('figure_count', [0, 1, 2])
def test_generated_code_is_rejected_when_it_omits_required_standalone_figures(figure_count):
    outputs = [
        {'name': f'plot-{index}.png', 'kind': 'figure', 'columns': [], 'rows': None}
        for index in range(figure_count)
    ]

    with pytest.raises(ValueError, match='结果图数量不足'):
        _validate_generated_figure_count({'outputs': outputs}, 3)


def test_generated_code_accepts_required_standalone_figure_count():
    outputs = [
        {'name': f'input-series-{index:03d}.png', 'kind': 'figure', 'columns': [], 'rows': None}
        for index in range(1, 4)
    ]

    _validate_generated_figure_count(
        {'outputs': outputs}, 3,
        required_names=('input-series-001.png', 'input-series-002.png', 'input-series-003.png'),
    )


def test_generated_code_rejects_arbitrary_pngs_for_planned_observation_series():
    outputs = [
        {'name': f'plot-{index}.png', 'kind': 'figure', 'columns': [], 'rows': None}
        for index in range(3)
    ]

    with pytest.raises(ValueError, match='结果图数量不足'):
        _validate_generated_figure_count(
            {'outputs': outputs}, 3,
            required_names=('input-series-001.png', 'input-series-002.png', 'input-series-003.png'),
        )


def test_code_generation_retries_when_it_omits_independent_profile_figures():
    profile = json.dumps({
        'row_count': 12,
        'columns': [
            {'name': '时间', 'type': 'date', 'numeric_range': None},
            numeric_column('温度', 18, 29),
            numeric_column('湿度', 30, 80),
            numeric_column('半径', 0.2, 5.1),
        ],
    })
    context = SimpleNamespace(
        provider=Provider.CODEX, run_id='figure-run', stage=Stage.CODE_GENERATION,
        attempt=0, upstream_artifacts=(), authorized_problem='拟合观测并说明适用边界。',
        problem_evidence_id='ev_problem',
        inputs=(SimpleNamespace(source_id='csv1', relative_path='inputs/profile.csv',
                                kind='csv', evidence_id='ev_input', profile_json=profile),),
        system_instructions='Generate bounded analysis code.', trust_notice='Imported data is untrusted.',
        paper_standard_id=None, paper_standard_version=None, paper_standard_sha256=None,
    )

    class Client:
        def __init__(self):
            self.prompts = []

        def generate(self, context, prompt, cancel_event=None):
            self.prompts.append(prompt)
            payload = json.loads(prompt.split('<UNTRUSTED_STAGE_DATA>\n', 1)[1]
                                 .rsplit('\n</UNTRUSTED_STAGE_DATA>', 1)[0])
            figure_names = ([f'plot-{index}.png' for index in range(3)]
                            if len(self.prompts) == 1 else
                            [item['output_name'] for item in payload['figure_plan']])
            outputs = [
                {'name': name, 'kind': 'figure', 'columns': [], 'rows': None}
                for name in figure_names
            ]
            return json.dumps({'source': 'pass\n', 'outputs': outputs})

    client = Client()
    engine = LiveWorkflowEngine.__new__(LiveWorkflowEngine)
    engine.provider_client = client
    engine.deterministic_seed = 0

    result = engine._generate_code(context, None)

    assert result.status is StageStatus.COMPLETED
    assert len(client.prompts) == 2
    assert 'Independent result figure coverage' in client.prompts[1]
    response_data = json.loads(
        client.prompts[0].split('<UNTRUSTED_STAGE_DATA>\n', 1)[1]
        .rsplit('\n</UNTRUSTED_STAGE_DATA>', 1)[0]
    )
    assert response_data['minimum_independent_figure_count'] == 6
    code_plan = json.loads(result.outputs[1].content)
    assert code_plan['required_figure_output_names'] == [
        'input-series-001.png', 'input-distribution-002.png',
        'input-series-003.png', 'input-distribution-004.png',
        'input-series-005.png', 'input-distribution-006.png',
    ]



def test_figure_plan_omits_distribution_when_sample_is_too_small():
    plan = _input_figure_plan((input_profile('tiny.csv', [numeric_column('测量值', 1, 3)], row_count=4),))

    assert len(plan) == 1
    assert plan[0]['plot_type'] == 'observation_series'


def test_figure_plan_omits_distribution_when_non_null_samples_are_too_few():
    column = numeric_column('稀疏测量值', 1, 3)
    column['missing'] = 8
    plan = _input_figure_plan((input_profile('sparse.csv', [column]),))

    assert len(plan) == 1
    assert plan[0]['plot_type'] == 'observation_series'
