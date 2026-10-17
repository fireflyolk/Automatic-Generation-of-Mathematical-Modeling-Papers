"""Package archive audit matches config and test members without rejecting Codex code."""
import unittest

from scripts.verify_f_drive_outputs import is_forbidden_archive_member


class PackageArchiveAuditTests(unittest.TestCase):
    def test_codex_provider_module_is_not_a_native_codex_config_directory(self):
        self.assertFalse(is_forbidden_archive_member(
            'PYZ/mathmodel_paper_studio.agents.providers.codex_cli'))

    def test_codex_and_claude_config_directories_are_forbidden(self):
        for name in ('DATA/.codex/config.toml', 'DATA/.claude/settings.json'):
            with self.subTest(name=name):
                self.assertTrue(is_forbidden_archive_member(name))

    def test_auth_and_credentials_files_are_forbidden(self):
        for name in ('auth.json', 'DATA/credentials.json'):
            with self.subTest(name=name):
                self.assertTrue(is_forbidden_archive_member(name))

    def test_test_modules_are_forbidden(self):
        for name in ('PYZ/tests/test_module', 'mathmodel_paper_studio.tests.test_module'):
            with self.subTest(name=name):
                self.assertTrue(is_forbidden_archive_member(name))


if __name__ == '__main__':
    unittest.main()
