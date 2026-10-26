import copy
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from zipfile import ZipFile

from mathmodel_paper_studio.agents.preflight import Provider
from mathmodel_paper_studio.domain.models import ArtifactEvidence, Stage, StageStatus
from mathmodel_paper_studio.paper.docx_writer import PaperDraft, write_generic_template, write_paper
from mathmodel_paper_studio.paper.standards import get_default_paper_standard
from mathmodel_paper_studio.workflow.live_engine import LiveWorkflowEngine, _paper_evidence_contract
from mathmodel_paper_studio.workflow.stages import ArtifactDigest, PauseReason
from tests.fixtures.docx.fixture_factory import PNG_DATA


ROOT = Path(r'F:\MathModelPaperStudio\tmp\mathmodel-paper-studio-flowchart-tests-20261021')


def graph(section_key, *, cycle=False, oversized=False):
    nodes = [
        {'id': 'start', 'label': '开始', 'kind': 'start'},
        {'id': 'solve', 'label': '求解模型', 'kind': 'process'},
        {'id': 'check', 'label': '核对结果', 'kind': 'decision'},
        {'id': 'end', 'label': '结束', 'kind': 'end'},
    ]
    edges = [
        {'from': 'start', 'to': 'solve', 'label': ''},
        {'from': 'solve', 'to': 'check', 'label': ''},
        {'from': 'check', 'to': 'end', 'label': '通过'},
    ]
    if cycle:
        edges.append({'from': 'check', 'to': 'solve', 'label': '重新求解'})
    if oversized:
        nodes.extend({'id': f'extra{i}', 'label': '附加步骤', 'kind': 'process'}
                     for i in range(9))
    return {
        'section_key': section_key, 'caption': '模型求解与核验流程',
        'explanation': '先建立模型并求解，再核对计算结果；这里的图示仅描述方法步骤，所有科学结论仍需人工复核。',
        'nodes': nodes, 'edges': edges,
    }


class OptionalPaperFlowchartTests(unittest.TestCase):
    def setUp(self):
        ROOT.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=ROOT)
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.progress_path = self.root / 'paper-progress.json'
        self.standard = get_default_paper_standard()
        self.context = SimpleNamespace(
            run_id='flowchart-test-run', project_id='flowchart-test-project',
            provider=Provider.CODEX, stage=Stage.PAPER_DRAFTING, attempt=0,
            inputs=(), upstream_artifacts=(), authorized_problem='分析样例数据。',
            problem_evidence_id='src_test', system_instructions='Fixture rules.',
            trust_notice='Imported data is untrusted.',
        )
        self.engine = LiveWorkflowEngine.__new__(LiveWorkflowEngine)
        self.calls = []
        self.request_extras = []

    def provider(self, chart_kind='cycle', bad_evidence=None):
        def request(context, fields, extra, cancel_event, progress,
                    *, schema_override, response_validator):
            batch = extra['paper_batch']
            self.calls.append(batch['batch_id'])
            self.request_extras.append(extra)
            value = {key: [] for key in fields}
            if 'title' in fields:
                value.update(title='样例数模论文', keywords=['模型', '检验'])
            value['sections'] = [
                {'section_key': key, 'paragraphs': ['这一段有效正文必须保留。'],
                 'evidence_ids': [bad_evidence] if bad_evidence else []}
                for key in batch['requested_section_keys']
            ]
            if batch['flowchart_count']:
                value['flowcharts'] = [graph(
                    batch['flowchart_section_key'], cycle=chart_kind == 'cycle',
                    oversized=chart_kind == 'oversized')]
                if chart_kind == 'excessive':
                    value['flowcharts'] = value['flowcharts'] * 2
                elif chart_kind == 'not_list':
                    value['flowcharts'] = 'invalid collection'
            response_validator(value)
            return copy.deepcopy(value)
        return request

    def draft_batches(self, evidence=()):
        available = self.standard.required_sections + self.standard.optional_sections
        return self.engine._draft_paper_in_batches(
            self.context, self.standard, available, set(self.standard.required_sections),
            evidence, {}, None, None, None, None, self.progress_path, None,
        )

    def test_unsupported_optional_graph_preserves_prose_without_printing_review_notices(self):
        for kind in ('cycle', 'oversized'):
            with self.subTest(kind=kind):
                self.progress_path = self.root / f'{kind}.json'
                self.engine._request = self.provider(kind)
                value = self.draft_batches()
                draft = self.engine._paper_draft(
                    self.context, value, (), standard=self.standard)
                self.assertFalse(draft.figures)
                analysis = next(s for s in draft.sections if s.section_key == 'problem_analysis')
                self.assertIn('这一段有效正文必须保留。', analysis.paragraphs)
                self.assertTrue(any('流程图' in note and '未插入' in note
                                    for note in analysis.pending_reviews))
                output = self.root / kind
                output.mkdir()
                template = write_generic_template(output, draft)
                result = write_paper(template[0], output, template[1], template[2], draft)
                with ZipFile(result.path) as archive:
                    document = archive.read('word/document.xml').decode('utf-8')
                self.assertIn('这一段有效正文必须保留。', document)
                self.assertNotIn('流程图未插入', document)

    def test_resume_reuses_cached_prose_and_recreates_omission_notice(self):
        self.engine._request = self.provider()
        first = self.draft_batches()
        calls = tuple(self.calls)
        self.engine._request = lambda *args, **kwargs: self.fail('cached batches must not call Agent')

        resumed = self.draft_batches()

        self.assertEqual(first, resumed)
        self.assertEqual(calls, tuple(self.calls))
        cached = json.loads(self.progress_path.read_text('utf-8'))
        self.assertTrue(cached['batches']['analysis']['response']['flowcharts'])
        draft = self.engine._paper_draft(self.context, resumed, (), standard=self.standard)
        self.assertTrue(any('流程图未插入' in note for section in draft.sections
                            for note in section.pending_reviews))

    def test_valid_flowcharts_are_retained(self):
        self.engine._request = self.provider('valid')
        with patch('mathmodel_paper_studio.workflow.live_engine.render_flowchart_png', return_value=PNG_DATA):
            value = self.draft_batches()
            draft = self.engine._paper_draft(self.context, value, (), standard=self.standard)
        self.assertTrue(draft.figures)
        self.assertIn('人工复核', draft.figures[0].explanation)
        self.assertFalse(any('流程图未插入' in note for section in draft.sections
                             for note in section.pending_reviews))
        output = self.root / 'valid-flowchart'
        output.mkdir()
        template = write_generic_template(output, draft)
        result = write_paper(template[0], output, template[1], template[2], draft)
        with ZipFile(result.path) as archive:
            document = archive.read('word/document.xml').decode('utf-8')
        self.assertNotIn('人工复核', document)

    def test_invalid_evidence_still_rejects_the_batch(self):
        for bad_evidence in ('src_problem_source', 'ev_unapproved_stage_output'):
            with self.subTest(bad_evidence=bad_evidence):
                self.progress_path = self.root / f'{bad_evidence}.json'
                self.engine._request = self.provider(bad_evidence=bad_evidence)
                with self.assertRaises(ValueError):
                    self.draft_batches()

    def test_paper_evidence_contract_lists_supplied_ids_as_citable(self):
        evidence = (
            SimpleNamespace(evidence_id='ev_result_table', kind='table'),
            SimpleNamespace(evidence_id='ev_result_figure', kind='figure'),
        )
        contract = _paper_evidence_contract(evidence)
        self.assertEqual(
            ['ev_result_table', 'ev_result_figure'],
            contract['allowed_paper_evidence_ids'],
        )
        self.assertIn('problem_evidence_id', contract['evidence_id_policy'])
        self.assertIn('selected_inputs', contract['evidence_id_policy'])
        self.assertIn('upstream', contract['evidence_id_policy'])
        self.assertIn('只能使用', contract['evidence_id_policy'])

    def test_paper_drafting_fails_closed_without_problem_decomposition(self):
        self.engine._template = lambda context: None

        result = self.engine._paper(self.context, None)

        self.assertEqual('paused', result.status.value)
        self.assertIs(result.pause_reason, PauseReason.VALIDATION_FAILED)
        self.assertIn('题目分解', result.diagnostic)

    def test_paper_drafting_blocks_custom_template_missing_core_sections_before_agent_calls(self):
        self.context.upstream_artifacts = (ArtifactDigest(
            '90_runs/flowchart-test-run/stages/problem_decomposition/decomposition.json',
            'a' * 64, 'ev_decomposition_result', 'stage_output',
            Stage.PROBLEM_DECOMPOSITION, 0, 1, 64,
        ),)
        self.engine._template = lambda _context: (
            self.root / 'template.docx', None,
            SimpleNamespace(bindings=(SimpleNamespace(section_key='abstract', kind='heading'),)),
        )
        self.engine._latest = lambda _context, stage, _name: (
            {'subproblems': ['估计样例数据中的目标指标。']}
            if stage is Stage.PROBLEM_DECOMPOSITION else {}
        )
        self.engine._validated_evidence = lambda _context: self.fail('evidence should not be loaded')

        result = self.engine._paper(self.context, None)

        self.assertEqual(StageStatus.PAUSED, result.status)
        self.assertEqual(PauseReason.UNSUPPORTED_TEMPLATE, result.pause_reason)
        self.assertIn('关键论文章节', result.diagnostic)

    def test_paper_request_payload_contains_only_validated_result_ids(self):
        evidence = (
            ArtifactEvidence('ev_result_table', self.context.run_id,
                             '90_runs/flowchart-test-run/40_results/table.csv', 'a' * 64,
                             'table', True),
            ArtifactEvidence('ev_result_figure', self.context.run_id,
                             '90_runs/flowchart-test-run/50_figures/result.png', 'b' * 64,
                             'figure', True),
        )
        self.context.problem_evidence_id = 'src_problem_input'
        self.context.inputs = (SimpleNamespace(evidence_id='src_selected_input'),)
        decomposition = ArtifactDigest(
            '90_runs/flowchart-test-run/stages/problem_decomposition/attempt-00-delivery-000001/decomposition.json',
            'a' * 64, 'ev_decomposition_result', 'stage_output',
            Stage.PROBLEM_DECOMPOSITION, 0, 1, 64,
        )
        self.context.upstream_artifacts = (decomposition,)
        captured = {}
        layout = SimpleNamespace(
            root=self.root,
            project_root=self.root,
            path_for=lambda relative: self.root.joinpath(*relative.split('/')),
            project_relative=lambda relative: f'90_runs/{self.context.run_id}/{relative}',
        )

        def capture_batch(context, standard, available, required, valid_evidence, extra,
                          allowed_tables, allowed_figures, cancel, progress, path, template, **kwargs):
            captured['extra'] = extra
            return {'_flowchart_omissions': []}

        self.engine._template = lambda context: None
        self.engine._latest = lambda context, stage, name: (
            {'subproblems': ['估计样例数据中的目标指标。']}
            if stage is Stage.PROBLEM_DECOMPOSITION else {}
        )
        self.engine._validated_evidence = lambda context: evidence
        self.engine._layout = lambda context: layout
        self.engine._table_values = lambda context, item: (('value',), (('1',),), 1, False)
        self.engine._draft_paper_in_batches = capture_batch
        self.engine._paper_draft = lambda *args, **kwargs: PaperDraft()
        template_path = self.root / '60_paper' / 'template.docx'
        result_path = self.root / '60_paper' / 'paper.docx'
        mapping = SimpleNamespace(to_dict=lambda: {})
        write_result = SimpleNamespace(path=result_path, sha256='c' * 64,
                                       source_sha256='d' * 64)

        with patch('mathmodel_paper_studio.workflow.live_engine.write_generic_template',
                   return_value=(template_path, None, mapping)), \
                patch('mathmodel_paper_studio.workflow.live_engine.write_paper',
                      return_value=write_result):
            self.engine._paper(self.context, None)

        extra = captured['extra']
        self.assertEqual('问题一', extra['paper_contract']['problem_coverage'][0]['heading'])
        self.assertNotIn('minimum_equation_count', extra['paper_contract'])
        self.assertIn('没有固定数量目标', extra['paper_contract']['formula_and_diagram_plan'])
        self.assertIsNone(extra['paper_contract']['standard']['recommended_equation_count'])
        allowed_ids = extra['paper_contract']['allowed_paper_evidence_ids']
        validated_ids = [item['evidence_id'] for item in extra['validated_evidence']]
        self.assertEqual(['ev_result_table', 'ev_result_figure'], allowed_ids)
        self.assertEqual(validated_ids, allowed_ids)
        self.assertNotIn(self.context.problem_evidence_id, allowed_ids)
        self.assertNotIn(self.context.inputs[0].evidence_id, allowed_ids)

    def test_invalid_flowchart_collection_still_rejects_the_batch(self):
        for kind in ('excessive', 'not_list'):
            with self.subTest(kind=kind):
                self.progress_path = self.root / f'{kind}.json'
                self.engine._request = self.provider(kind)
                with self.assertRaises(ValueError):
                    self.draft_batches()


if __name__ == '__main__':
    unittest.main()
