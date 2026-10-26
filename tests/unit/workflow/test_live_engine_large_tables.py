import unittest
from types import SimpleNamespace

from mathmodel_paper_studio.paper.standards import get_default_paper_standard
from mathmodel_paper_studio.workflow.live_engine import LiveWorkflowEngine


class LiveEngineLargeTableTests(unittest.TestCase):
    def test_table_at_word_limit_remains_complete(self):
        row_count = 10_000
        csv_content = (
            'index,value\n'
            + ''.join(f'{index},{index * 2}\n' for index in range(row_count))
        ).encode('utf-8')
        engine = LiveWorkflowEngine.__new__(LiveWorkflowEngine)
        engine._result_bytes = lambda _context, _evidence: csv_content

        headers, rows, total_rows, truncated = engine._table_values(
            SimpleNamespace(), SimpleNamespace(relative_path='results.csv'))

        self.assertEqual(('index', 'value'), headers)
        self.assertEqual(row_count, len(rows))
        self.assertEqual(row_count, total_rows)
        self.assertFalse(truncated)

    def test_oversized_result_table_becomes_a_labeled_preview_in_the_paper(self):
        row_count = 10_001
        csv_content = (
            'index,value\n'
            + ''.join(f'{index},{index * 2}\n' for index in range(row_count))
        ).encode('utf-8')
        evidence = SimpleNamespace(evidence_id='ev_' + '1' * 24, kind='table')
        engine = LiveWorkflowEngine.__new__(LiveWorkflowEngine)
        engine._result_bytes = lambda _context, _evidence: csv_content
        value = {
            'title': '结果论文',
            'keywords': [],
            'sections': [{
                'section_key': 'abstract',
                'paragraphs': ['本文摘要待人工核验。'],
                'evidence_ids': [],
            }],
            'equations': [],
            'tables': [{
                'section_key': 'results_table_1',
                'evidence_id': evidence.evidence_id,
                'caption': '计算结果',
            }],
            'figures': [],
            'references': [],
        }

        draft = engine._paper_draft(
            SimpleNamespace(), value, (evidence,), standard=get_default_paper_standard())

        table = draft.tables[0]
        self.assertEqual(('index', 'value'), table.headers)
        self.assertEqual(100, len(table.rows))
        self.assertEqual(('0', '0'), table.rows[0])
        self.assertIn('10,001 行', table.caption)
        self.assertIn('前 100 行', table.caption)
        self.assertTrue(any('完整 CSV' in review for review in table.pending_reviews))


if __name__ == '__main__':
    unittest.main()
