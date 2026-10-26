from pathlib import Path
import tempfile
import unittest

from scripts.verify_f_drive_outputs import inventory_tree, read_lock


ROOT = Path(r'F:\MathModelPaperStudio\tmp\mathmodel-paper-studio-sandbox-20261018')


class RequirementLockParserTests(unittest.TestCase):
    def setUp(self):
        ROOT.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=ROOT)
        self.addCleanup(self.temp.cleanup)

    def write_lock(self, content):
        path = Path(self.temp.name) / 'requirements.lock'
        path.write_text(content, encoding='utf-8')
        return path

    def test_reads_existing_single_line_pip_hash_format(self):
        lock = self.write_lock('numpy==2.0.0 --hash=sha256:' + 'a' * 64 + '\n')
        self.assertEqual((('numpy', '2.0.0'),), read_lock(lock))

    def test_reads_hashed_multiline_uv_compile_format(self):
        lock = self.write_lock(
            'numpy==2.5.4 \\\n'
            '    --hash=sha256:' + 'a' * 64 + ' \\\n'
            '    --hash=sha256:' + 'b' * 64 + '\n'
            '    # via requirements.in\n'
        )
        self.assertEqual((('numpy', '2.5.4'),), read_lock(lock))

    def test_rejects_unpinned_or_invalid_hash_entries(self):
        for content in ('numpy>=2.0\n', 'numpy==2.0.0 \\\n    --hash=sha256:bad\n'):
            with self.subTest(content=content), self.assertRaises(ValueError):
                read_lock(self.write_lock(content))

    def test_inventory_does_not_treat_multiline_password_documentation_as_secret(self):
        root = Path(self.temp.name) / 'runtime'
        vendor = root / 'Lib' / 'site-packages'
        vendor.mkdir(parents=True)
        (vendor / 'sample.py').write_text(
            'Password:\n            self.set_workbook_password', encoding='utf-8')
        (root / 'runtime-manifest.json').write_text('{"status":"ok"}', encoding='utf-8')

        records = inventory_tree(root)

        self.assertEqual(2, len(records))

    def test_inventory_does_not_treat_password_function_lookup_as_secret(self):
        root = Path(self.temp.name) / 'urllib-runtime'
        root.mkdir()
        (root / 'request.py').write_text(
            'user, password = HTTPPasswordMgr.find_user_password(self, realm,',
            encoding='utf-8')
        self.assertEqual(1, len(inventory_tree(root)))

    def test_inventory_still_rejects_actual_secret_assignments(self):
        root = Path(self.temp.name) / 'unsafe-runtime'
        vendor = root / 'Lib' / 'site-packages'
        vendor.mkdir(parents=True)
        (vendor / 'sample.py').write_text(
            'Password:\n            self.set_workbook_password', encoding='utf-8')
        (root / 'config.py').write_text('api_key = "' + 'A' * 32 + '"', encoding='utf-8')

        with self.assertRaisesRegex(ValueError, 'Possible credential material'):
            inventory_tree(root)

    def test_inventory_does_not_treat_spdx_exception_name_as_api_token(self):
        root = Path(self.temp.name) / 'spdx-runtime'
        package = root / 'Lib' / 'site-packages' / 'packaging' / 'licenses'
        package.mkdir(parents=True)
        (package / '_spdx.py').write_text(
            'sk-linking-protocols-exception', encoding='utf-8')
        self.assertEqual(1, len(inventory_tree(root)))

    def test_inventory_rejects_long_sk_prefixed_token(self):
        root = Path(self.temp.name) / 'secret-runtime'
        root.mkdir()
        (root / 'config.py').write_text('sk-' + 'A7b9' * 12, encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'Possible credential material'):
            inventory_tree(root)

    def test_inventory_ignores_aws_pattern_inside_embedded_base64_data(self):
        root = Path(self.temp.name) / 'font-runtime'
        root.mkdir()
        (root / 'fontdata.py').write_text(
            'FONT_DATA = "AQAAAAAAAwAaAKIAAQAAAAAABAAHAM0AAQAAAAAABQA8"',
            encoding='utf-8')
        self.assertEqual(1, len(inventory_tree(root)))

    def test_inventory_rejects_bounded_aws_access_key(self):
        root = Path(self.temp.name) / 'aws-secret-runtime'
        root.mkdir()
        (root / 'config.py').write_text(
            'aws_key = "AKIA' + 'A' * 16 + '"', encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'Possible credential material'):
            inventory_tree(root)


if __name__ == '__main__':
    unittest.main()
