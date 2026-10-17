import ctypes
from dataclasses import FrozenInstanceError
from pathlib import Path
import unittest
import uuid

from mathmodel_paper_studio.bootstrap.paths import APPROVED_BASE, PathPolicyError
from mathmodel_paper_studio.security.path_guard import selected_path, native_executable


class PathGuardTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(APPROVED_BASE) / 'tmp/mathmodel-paper-studio-20261015/task3' / uuid.uuid4().hex
        (self.root / 'inputs').mkdir(parents=True)
        (self.root / 'out').mkdir()
        (self.root / 'inputs/data.csv').write_text('x\n1', encoding='utf-8')

    def test_normalized_selected_files_and_outputs(self):
        value = str(self.root / 'inputs/data.csv').replace('\\', '/')
        self.assertEqual(selected_path(value, self.root, (self.root / 'inputs/data.csv',)), self.root / 'inputs/data.csv')
        self.assertEqual(selected_path(self.root / 'out/new.txt', self.root, (self.root / 'out',), write=True), self.root / 'out/new.txt')

    def test_roots_unc_devices_streams_traversal_and_unselected_paths(self):
        invalid = ['F:\\', 'F:relative', r'\\server\share\a', r'\\?\F:\a', r'\\.\F:\a',
                   'C:/outside', str(self.root / 'inputs/../out/x'), str(self.root / 'out/x:stream'),
                   str(self.root / 'out./x'), str(self.root / 'out/CON'), str(self.root / 'inputs/other.csv')]
        for value in invalid:
            with self.subTest(path=value), self.assertRaises(PathPolicyError):
                selected_path(value, self.root, (self.root / 'inputs/data.csv',))
        with self.assertRaises(PathPolicyError):
            selected_path(self.root / 'out2/x', self.root, (self.root / 'out',), write=True)

    def test_read_permission_never_implies_write_permission(self):
        with self.assertRaises(PathPolicyError):
            selected_path(self.root / 'inputs/data.csv', self.root, (self.root / 'inputs/data.csv',), write=True)
        with self.assertRaises(PathPolicyError):
            native_executable(self.root / 'fake.cmd')
        with self.assertRaises(PathPolicyError):
            native_executable(self.root / 'fake.bat')

    def test_real_junction_is_rejected_even_when_target_is_inside_project(self):
        # Use the native reparse API through a fixture helper; no provider/shell launch.
        from tests_task3_helpers import junction
        link = self.root / 'alias'
        with junction(link, self.root / 'out'):
            with self.assertRaises(PathPolicyError):
                selected_path(link / 'x', self.root, (link,), write=True)

    def test_existing_local_cli_can_be_read_without_relocating_auth_or_app_storage(self):
        from io import BytesIO
        from types import SimpleNamespace
        from unittest.mock import patch
        external = Path(r'C:\Program Files\ExistingCLI\agent.exe')
        metadata = SimpleNamespace(st_mode=0, st_file_attributes=0)
        # Metadata and image reads are mocked: no C: filesystem access occurs.
        with patch.object(Path, 'lstat', return_value=metadata), patch.object(Path, 'is_file', return_value=True), patch.object(Path, 'open', return_value=BytesIO(b'MZfixture')):
            self.assertEqual(native_executable(external), external)
