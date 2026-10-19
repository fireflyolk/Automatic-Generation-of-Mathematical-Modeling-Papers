import importlib.util
import json
from pathlib import Path
import tempfile
import unittest


ENTRY = Path(r'F:\Codex另一个工作的地方\worktrees\mathmodel-paper-studio-impl-20261015\packaging\runtime\sandbox_entry.py')
SPEC = importlib.util.spec_from_file_location('mmps_sandbox_entry', ENTRY)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class SandboxEntryFailureTests(unittest.TestCase):
    def test_extracts_only_allowlisted_name_error_metadata(self):
        stderr = (b'Traceback (most recent call last):\n'
                  b'  File "C:\\MMPS\\input\\generated.py", line 233, in <module>\n'
                  b'    print(SECRET_VALUE)\n'
                  b"NameError: name 'R_ARE' is not defined\n")

        result = MODULE._failure_summary(stderr)

        self.assertEqual({'error_type': 'NameError', 'line': 233, 'name': 'R_ARE'}, result)
        self.assertNotIn('SECRET_VALUE', repr(result))
        self.assertNotIn('Traceback', repr(result))

    def test_ignores_unrecognized_or_non_generated_tracebacks(self):
        for stderr in (
            b'Traceback\n  File "other.py", line 4\nNameError: name \'X\' is not defined',
            b'NameError: name \'X\' is not defined',
            b'\xff' * 100000,
        ):
            with self.subTest(stderr=stderr[:40]):
                self.assertIsNone(MODULE._failure_summary(stderr))

    def test_sandbox_result_emits_only_compact_failure_metadata(self):
        result = MODULE._guest_result(1, b'Traceback\n  File "generated.py", line 2\n'
                                      b"NameError: name 'R_ARE' is not defined\n",
                                      b'private output must not be returned')

        self.assertEqual({'ExitCode': 1,
                          'Failure': {'error_type': 'NameError', 'line': 2, 'name': 'R_ARE'}}, result)
        self.assertNotIn('private', repr(result))

    def test_guest_result_is_written_to_shared_output_mapping(self):
        with tempfile.TemporaryDirectory(dir=Path(r'F:\Codex另一个工作的地方\tmp')) as temp:
            output_dir = Path(temp)
            MODULE._write_guest_result(
                output_dir, 1,
                b'Traceback\n  File "generated.py", line 23\n'
                b"NameError: name 'R_ARE' is not defined\n",
            )

            result_path = output_dir / MODULE._GUEST_RESULT_FILENAME
            raw_result = result_path.read_text(encoding='utf-8')
            payload = json.loads(raw_result)

        self.assertEqual({'ExitCode': 1,
                          'Failure': {'error_type': 'NameError', 'line': 23, 'name': 'R_ARE'}},
                         payload)
        self.assertNotIn('Traceback', raw_result)
