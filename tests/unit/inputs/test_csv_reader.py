from pathlib import Path
import tempfile
import unittest
from mathmodel_paper_studio.inputs.csv_reader import read_csv
from mathmodel_paper_studio.inputs.profiling import ImportLimits, profile_table
ROOT = Path(r'F:\MathModelPaperStudio\tmp\mathmodel-paper-studio-20261015')
class CsvTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=ROOT)
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / 'data.csv'
    def test_encodings_and_profile(self):
        for encoding in ('utf-8-sig','gbk','gb18030'):
            text = '姓名,值,空\n张三,2,\n李四,4,\n'
            if encoding == 'gb18030':
                text = text.replace('李四','\U00020000')
            self.path.write_bytes(text.encode(encoding))
            table = read_csv(self.path)
            profile = profile_table(table)
            self.assertEqual(('姓名','值','空'),table.headers)
            self.assertEqual({'min':2.0,'max':4.0},profile['columns'][1]['numeric_range'])
            self.assertEqual(2,profile['columns'][2]['missing'])
            self.assertEqual('number',profile['columns'][1]['type'])
            self.assertEqual(profile,profile_table(table))
    def test_limits_and_malformed(self):
        cases = [(b'a,b\n1\n',{}),(b'\xff\x81',{}),(b'a\n"unterminated',{}),
                 (b'a\n1\n2\n',{'max_rows':1}),(b'a,b\n1,2\n',{'max_columns':1}),
                 (b'ab\n1\n',{'max_cell_chars':1}),(b'a\n1\n',{'max_file_bytes':2})]
        for content,limits in cases:
            with self.subTest(content=content,limits=limits):
                self.path.write_bytes(content)
                with self.assertRaises(ValueError):
                    read_csv(self.path,limits=ImportLimits(**limits))
    def test_reads_verified_byte_snapshot_with_size_limit(self):
        table = read_csv(b'value\n5\n')
        self.assertEqual((('5',),), table.rows)
        with self.assertRaises(ValueError):
            read_csv(b'value\n5\n', limits=ImportLimits(max_file_bytes=1))
    def test_expressions_and_nonfinite_stay_data(self):
        self.path.write_text('value\n=2+2\nNaN\nInfinity\n',encoding='utf-8')
        table = read_csv(self.path)
        self.assertEqual('=2+2',table.rows[0][0])
        self.assertIsNone(profile_table(table)['columns'][0]['numeric_range'])

    def test_empty_and_duplicate_headers_rejected(self):
        for data in (b'', b'\n', b'a,a\n1,2\n', b'a,\n1,2\n'):
            self.path.write_bytes(data)
            with self.subTest(data=data), self.assertRaises(ValueError):
                read_csv(self.path)

    def test_boolean_text_missing_and_finite_profile(self):
        self.path.write_text('n,t,flag\n-2,word,true\n3,,false\n,word,\n', encoding='utf-8')
        profile = profile_table(read_csv(self.path))
        self.assertEqual({'min': -2.0, 'max': 3.0}, profile['columns'][0]['numeric_range'])
        self.assertEqual(1, profile['columns'][0]['missing'])
        self.assertEqual('text', profile['columns'][1]['type'])
        self.assertEqual('boolean', profile['columns'][2]['type'])

    def test_invalid_limits_are_rejected(self):
        for value in (0, -1, True, 1.5):
            with self.subTest(value=value), self.assertRaises(ValueError):
                ImportLimits(max_rows=value)
