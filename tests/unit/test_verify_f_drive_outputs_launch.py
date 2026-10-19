import os
import sys
from tempfile import TemporaryDirectory
import unittest
from pathlib import Path
from unittest.mock import patch

from mathmodel_paper_studio.bootstrap.paths import AppPaths
from scripts import verify_f_drive_outputs


class _FakeApplication:
    def __init__(self, _arguments):
        pass

    def setOrganizationName(self, _value):
        pass

    def setApplicationName(self, _value):
        pass

    def exec(self):
        return 0


class _FakeWindow:
    kwargs = None

    def __init__(self, _paths, **kwargs):
        type(self).kwargs = kwargs

    def show(self):
        pass


class DesktopLaunchWiringTests(unittest.TestCase):
    ROOT = Path(r'F:\Codex另一个工作的地方\tmp\mathmodel-paper-studio-gates-20261017\launch-wiring')

    def test_frozen_app_starts_from_approved_c_install_with_all_data_paths_on_f(self):
        installation = Path(os.environ['LOCALAPPDATA']) / 'Programs' / 'MathModelPaperStudio'
        with patch.object(sys, 'frozen', True, create=True), \
                patch.object(sys, 'executable', str(installation / 'MathModelPaperStudio.exe')), \
                patch.object(sys, '_MEIPASS', str(installation / '_internal'), create=True):
            paths, payload = verify_f_drive_outputs.prepare_startup(self.ROOT / 'c-install-data')
        self.assertEqual('f:', paths.root.drive.casefold())
        self.assertTrue(payload['frozen'])
        self.assertTrue(all(Path(value).drive.casefold() == 'f:'
                            for value in payload['managed_paths']))

    def test_frozen_app_rejects_c_locations_outside_the_approved_installation(self):
        installation = Path(r'C:\unapproved-mathmodel-installation')
        with patch.object(sys, 'frozen', True, create=True), \
                patch.object(sys, 'executable', str(installation / 'MathModelPaperStudio.exe')), \
                patch.object(sys, '_MEIPASS', str(installation / '_internal'), create=True):
            with self.assertRaises(ValueError):
                verify_f_drive_outputs.prepare_startup(self.ROOT / 'rejected-c-install-data')

    def test_desktop_uses_main_windows_shared_live_provider_service(self):
        self.ROOT.mkdir(parents=True, exist_ok=True)
        paths = AppPaths(self.ROOT / 'data')
        paths.ensure_directories()
        with patch('PySide6.QtWidgets.QApplication', _FakeApplication), \
                patch('mathmodel_paper_studio.ui.main_window.MainWindow', _FakeWindow), \
                patch('mathmodel_paper_studio.ui.fluent_theme.apply_fluent_theme'):
            result = verify_f_drive_outputs.launch_desktop(paths)
        self.assertEqual(result, 0)
        self.assertEqual(_FakeWindow.kwargs, {})

    def test_runtime_entry_verifier_accepts_matching_protocol_source(self):
        verifier = getattr(verify_f_drive_outputs, 'verify_runtime_entry', None)
        self.assertTrue(callable(verifier), 'runtime entry compatibility verifier is missing')
        with TemporaryDirectory(dir=Path(r'F:\Codex另一个工作的地方\tmp')) as temp:
            root = Path(temp)
            source = root / 'source-sandbox-entry.py'
            bundled = root / 'runtime' / 'sandbox_entry.py'
            bundled.parent.mkdir()
            source.write_text('write_result_record()\n', encoding='utf-8')
            bundled.write_text('write_result_record()\n', encoding='utf-8')

            digest = verifier(bundled, source)

        self.assertEqual(64, len(digest))

    def test_runtime_entry_verifier_rejects_stale_launcher(self):
        verifier = getattr(verify_f_drive_outputs, 'verify_runtime_entry', None)
        self.assertTrue(callable(verifier), 'runtime entry compatibility verifier is missing')
        with TemporaryDirectory(dir=Path(r'F:\Codex另一个工作的地方\tmp')) as temp:
            root = Path(temp)
            source = root / 'source-sandbox-entry.py'
            bundled = root / 'runtime' / 'sandbox_entry.py'
            bundled.parent.mkdir()
            source.write_text('write_result_record()\n', encoding='utf-8')
            bundled.write_text('return child_status\n', encoding='utf-8')

            with self.assertRaisesRegex(ValueError, 'does not match'):
                verifier(bundled, source)


if __name__ == '__main__':
    unittest.main()
