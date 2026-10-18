import os
from pathlib import Path
import subprocess
import unittest
import uuid

from mathmodel_paper_studio.bootstrap.paths import (
    AppPaths, PathPolicyError, APPROVED_BASE, validate_import_source_path, validate_managed_path,
)


class PathsTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(APPROVED_BASE) / 'tmp/mathmodel-paper-studio-20261015' / ('paths-' + uuid.uuid4().hex)

    def test_default_and_explicit_derivation(self):
        default = AppPaths()
        self.assertEqual(default.root, Path(APPROVED_BASE) / 'mathmodel-paper-studio-data')
        self.assertEqual(default.database, default.state / 'studio.sqlite3')
        paths = AppPaths(self.root)
        self.assertEqual(paths.projects, self.root / 'projects')
        self.assertEqual(paths.logs, self.root / 'logs')
        self.assertEqual(paths.cache, self.root / 'cache')
        self.assertEqual(paths.temp, self.root / 'tmp')
        self.assertEqual(paths.build, self.root / 'build')
        self.assertEqual(paths.tests, self.root / 'tests')

    def test_rejects_nonlocal_relative_traversal_and_external_paths(self):
        invalid = ['C:/app', r'\\server\share\app', r'\\?\F:\app', r'\\.\F:\app',
                   'F:app', 'relative', 'F:/other/app', str(Path(APPROVED_BASE) / '../outside'),
                   str(Path(APPROVED_BASE) / 'foo/../bar'), str(Path(APPROVED_BASE) / 'app:stream'),
                   str(Path(APPROVED_BASE) / 'dir./app'), APPROVED_BASE]
        for path in invalid:
            with self.subTest(path=path), self.assertRaises(PathPolicyError):
                AppPaths(path)

    def test_import_sources_allow_any_local_drive_without_widening_managed_storage(self):
        for raw in (r'C:\__mathmodel_path_probe__\data.docx',
                    r'F:\documents\contest\data.docx'):
            with self.subTest(path=raw):
                self.assertEqual(Path(raw), validate_import_source_path(raw))
        for path in (r'\\server\share\data.docx', 'C:documents/data.docx',
                     'relative/data.docx', r'C:\documents\..\secret.docx'):
            with self.subTest(path=path), self.assertRaises(PathPolicyError):
                validate_import_source_path(path)
        with self.assertRaises(PathPolicyError):
            validate_managed_path(Path(r'C:\__mathmodel_path_probe__\data.docx'))
        with self.assertRaises(PathPolicyError):
            validate_managed_path(Path(r'F:\documents\contest\data.docx'))

    def test_environment_configured_before_optional_imports_without_auth_relocation(self):
        paths = AppPaths(self.root)
        original = {name: os.environ.get(name) for name in ('HOME', 'USERPROFILE', 'APPDATA')}
        environment = paths.configure_environment()
        for name in ('TEMP', 'TMP', 'TMPDIR', 'PYTHONPYCACHEPREFIX', 'PIP_CACHE_DIR',
                     'UV_CACHE_DIR', 'MPLCONFIGDIR', 'QT_SHADER_CACHE_PATH', 'XDG_CACHE_HOME'):
            self.assertEqual(os.environ[name], str(environment[name]))
            self.assertTrue(Path(environment[name]).is_relative_to(self.root))
            self.assertTrue(Path(environment[name]).is_dir())
        self.assertEqual(original, {name: os.environ.get(name) for name in original})

    def test_ensure_rechecks_junction_escape_before_writing(self):
        paths = AppPaths(self.root)
        self.root.mkdir(parents=True)
        outside = self.root.parent / ('outside-' + uuid.uuid4().hex)
        outside.mkdir()
        junction = self.root / 'cache'
        completed = subprocess.run(['cmd.exe', '/d', '/c', 'mklink', '/J', str(junction), str(outside)],
                                   capture_output=True, check=False)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        try:
            with self.assertRaises(PathPolicyError):
                paths.ensure_directories()
            self.assertEqual(list(outside.iterdir()), [])
        finally:
            junction.rmdir()
            outside.rmdir()

    def test_construction_rejects_root_junction_and_regular_file(self):
        self.root.parent.mkdir(parents=True, exist_ok=True)
        outside = self.root.parent / ('target-' + uuid.uuid4().hex)
        outside.mkdir()
        result = subprocess.run(['cmd.exe', '/d', '/c', 'mklink', '/J', str(self.root), str(outside)],
                                capture_output=True, check=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        # A root alias inside the approved base is still not an app-owned directory.
        try:
            with self.assertRaises(PathPolicyError):
                AppPaths(self.root)
        finally:
            self.root.rmdir()
            outside.rmdir()
        self.root.write_text('existing user file', encoding='utf-8')
        try:
            with self.assertRaises(PathPolicyError):
                AppPaths(self.root)
            self.assertEqual(self.root.read_text(encoding='utf-8'), 'existing user file')
        finally:
            self.root.unlink()


    def test_bootstrap_diagnostic_and_corrupt_database_are_truthful(self):
        from contextlib import redirect_stdout, redirect_stderr
        from io import StringIO
        from mathmodel_paper_studio.__main__ import main
        import sys
        pyside_before = {name for name in sys.modules
                         if name == 'PySide6' or name.startswith('PySide6.')}
        paths = AppPaths(self.root)
        output = StringIO()
        with redirect_stdout(output):
            self.assertEqual(main(['--check', '--root', str(self.root)]), 0)
        self.assertIn(str(paths.database), output.getvalue())
        self.assertIn('尚未实现', output.getvalue())
        pyside_after = {name for name in sys.modules
                        if name == 'PySide6' or name.startswith('PySide6.')}
        self.assertEqual(pyside_before, pyside_after)
        paths.database.write_bytes(b'not a SQLite database')
        errors = StringIO()
        with redirect_stderr(errors):
            self.assertEqual(main(['--check', '--root', str(self.root)]), 1)
        self.assertIn('启动检查失败', errors.getvalue())
        errors = StringIO()
        with redirect_stderr(errors):
            self.assertEqual(main(['--root', 'C:/invalid']), 1)

if __name__ == '__main__':
    unittest.main()
