"""Real native Job API layout/ordering and fail-closed fault contracts."""
import ctypes as c
from pathlib import Path
import sys
import time
import unittest
import uuid
from unittest.mock import patch

from mathmodel_paper_studio.bootstrap.paths import APPROVED_BASE, AppPaths
from mathmodel_paper_studio.agents.process_runner import ProcessLimits, native_cli_environment
from mathmodel_paper_studio.agents import windows_job as job


class NativeJobTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(APPROVED_BASE) / 'tmp/mathmodel-paper-studio-20261015/task3' / uuid.uuid4().hex
        self.paths = AppPaths(self.root)
        self.paths.ensure_directories()
        self.environment = native_cli_environment(self.paths, Path(sys.executable))
        self.fixture = Path(__file__).resolve().parents[2] / 'fixtures/cli/trusted_lifecycle.py'

    def test_windows_x64_structures_have_native_layout(self):
        self.assertEqual(c.sizeof(c.c_void_p), 8)
        for structure, expected in [(job.BasicLimits, 64), (job.ExtendedLimits, 144),
                                    (job.StartupInfo, 104), (job.StartupInfoEx, 112), (job.Accounting, 48)]:
            with self.subTest(structure=structure.__name__):
                self.assertEqual(c.sizeof(structure), expected)

    def test_atomic_attribute_and_job_limits_are_installed_before_create(self):
        real = job._api()
        observed = []
        class Spy:
            def __getattr__(self, name):
                function = getattr(real, name)
                def call(*args):
                    if name == 'SetInformationJobObject':
                        limits = c.cast(args[2], c.POINTER(job.ExtendedLimits)).contents
                        self_test.assertEqual(limits.basic.flags, 0x2308)
                        self_test.assertGreater(limits.process_memory, 0)
                        self_test.assertGreaterEqual(limits.job_memory, limits.process_memory)
                    if name == 'UpdateProcThreadAttribute':
                        observed.append(args[2])
                    if name == 'CreateProcessW':
                        self_test.assertEqual(observed, [0x2000D, 0x20002])
                        self_test.assertTrue(args[4])
                        self_test.assertTrue(args[5] & 0x80000)
                        self_test.assertTrue(args[5] & 0x400)
                        self_test.assertFalse(args[5] & 0x4)
                    return function(*args)
                return call
        self_test = self
        with patch.object(job, '_api', return_value=Spy()):
            process = job.NativeJobProcess(Path(sys.executable), ('-I', '-B', str(self.fixture), 'success', str(self.root)),
                                           self.root, self.environment, ProcessLimits())
            try:
                process.finish_stdin()
                deadline = time.monotonic() + 3
                while process.poll() is None and time.monotonic() < deadline:
                    time.sleep(.01)
                self.assertEqual(process.poll(), 0)
            finally:
                process.close()
                process.close()

    def test_failures_never_fall_back_and_close_every_acquired_handle(self):
        real = job._api()
        for failure in ('SetInformationJobObject', 'UpdateProcThreadAttribute', 'CreateProcessW'):
            created, closed, calls = [], [], []
            class Fault:
                def __getattr__(self, name):
                    function = getattr(real, name)
                    def call(*args):
                        calls.append(name)
                        if name == failure:
                            c.set_last_error(5)
                            return 0
                        result = function(*args)
                        if name == 'CreateJobObjectW' and result:
                            created.append(result)
                        if name == 'CreatePipe' and result:
                            created.extend([c.cast(args[i], c.POINTER(job.HANDLE)).contents.value for i in (0, 1)])
                        if name == 'CloseHandle' and result:
                            closed.append(args[0])
                        return result
                    return call
            with self.subTest(failure=failure), patch.object(job, '_api', return_value=Fault()):
                with self.assertRaises(job.NativeProcessError):
                    job.NativeJobProcess(Path(sys.executable), (), self.root, self.environment, ProcessLimits())
                self.assertCountEqual(created, closed)
                if failure != 'CreateProcessW':
                    self.assertNotIn('CreateProcessW', calls)

    def test_native_rejects_shell_launcher_null_argv_and_inherited_environment(self):
        for arguments in ['shell command', ('bad\0argument',), ('x' * 25000,)]:
            with self.subTest(arguments_type=type(arguments).__name__), self.assertRaises(ValueError):
                job.validate_argv(arguments)
        for environment in [None, {}, {'A': 'x', 'a': 'y'}, {'BAD=NAME': 'x'}]:
            with self.subTest(environment_type=type(environment).__name__), self.assertRaises(ValueError):
                job.environment_block(environment)
