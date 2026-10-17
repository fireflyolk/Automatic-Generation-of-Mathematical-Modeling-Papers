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


if __name__ == '__main__':
    unittest.main()
