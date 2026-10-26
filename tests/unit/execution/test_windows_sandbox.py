from xml.etree import ElementTree
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from mathmodel_paper_studio.execution import windows_sandbox
from mathmodel_paper_studio.execution.windows_sandbox import (
    WindowsSandboxExecutor,
    build_sandbox_configuration,
    guest_command,
)
from mathmodel_paper_studio.agents.preflight import Provider
from mathmodel_paper_studio.bootstrap.paths import AppPaths
from mathmodel_paper_studio.domain.models import Project, Run, RunStatus, StageStatus
from mathmodel_paper_studio.execution.python_runner import RunArtifactLayout
from mathmodel_paper_studio.execution.result_validation import ArtifactExpectation


WORKSPACE = Path(r'F:\MathModelPaperStudio')


def make_layout(test):
    test.temp = tempfile.TemporaryDirectory(dir=WORKSPACE / 'tmp')
    test.addCleanup(test.temp.cleanup)
    paths = AppPaths(Path(test.temp.name) / 'app')
    paths.ensure_directories()
    project = Project('sandbox1', 'sandbox test', 'projects/sandbox1')
    project_root = paths.root / project.relative_path
    project_root.mkdir(parents=True)
    for name in ('00_inputs', '10_analysis', '20_model', '30_src', '40_results',
                 '50_figures', '60_paper', '70_review', '90_runs'):
        (project_root / name).mkdir()
    run = Run('run1', project.project_id, Provider.CODEX, RunStatus.CREATED, '90_runs/run1')
    return paths, project_root, RunArtifactLayout.create(paths, project, run)


class WindowsSandboxConfigurationTests(unittest.TestCase):
    def test_source_runtime_uses_explicit_f_drive_configuration(self):
        runtime = r'F:\MathModelPaperStudio\downloads\computation-runtime'
        with patch.dict(os.environ, {'MMPS_COMPUTATION_RUNTIME_ROOT': runtime}):
            resolver = getattr(windows_sandbox, 'resolve_computation_runtime_root', None)
            self.assertTrue(callable(resolver), 'source runtime resolver is missing')
            self.assertEqual(Path(runtime), resolver())

    def test_explicit_source_runtime_cannot_redirect_to_c(self):
        runtime = (Path(os.environ['LOCALAPPDATA']) / 'Programs'
                   / 'MathModelPaperStudio' / '_internal' / 'computation-runtime')
        with patch.dict(os.environ, {'MMPS_COMPUTATION_RUNTIME_ROOT': str(runtime)}):
            with self.assertRaises(ValueError):
                windows_sandbox.resolve_computation_runtime_root()

    def test_frozen_runtime_resolves_beside_pyinstaller_meipass(self):
        package_root = WORKSPACE / 'tmp' / 'frozen-package-fixture'
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop('MMPS_COMPUTATION_RUNTIME_ROOT', None)
            with patch.object(windows_sandbox.sys, '_MEIPASS', str(package_root), create=True):
                self.assertEqual(package_root / 'computation-runtime',
                                 windows_sandbox.resolve_computation_runtime_root())

    def test_frozen_c_install_resolves_bundled_runtime(self):
        package_root = (Path(os.environ['LOCALAPPDATA']) / 'Programs'
                        / 'MathModelPaperStudio' / '_internal')
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop('MMPS_COMPUTATION_RUNTIME_ROOT', None)
            with patch.object(windows_sandbox.sys, '_MEIPASS', str(package_root), create=True):
                runtime = windows_sandbox.resolve_computation_runtime_root()
                executor = WindowsSandboxExecutor(runtime)
        self.assertEqual(package_root / 'computation-runtime', executor.runtime_root)

    def test_c_bundled_runtime_is_only_mapped_read_only_with_f_inputs_and_outputs(self):
        runtime = (Path(os.environ['LOCALAPPDATA']) / 'Programs'
                   / 'MathModelPaperStudio' / '_internal' / 'computation-runtime')
        with tempfile.TemporaryDirectory(dir=WORKSPACE / 'tmp') as temp:
            input_root, output_root = Path(temp) / 'input', Path(temp) / 'output'
            input_root.mkdir()
            output_root.mkdir()
            folders = (
                windows_sandbox.MappedFolder(input_root, windows_sandbox.GUEST_INPUT, True),
                windows_sandbox.MappedFolder(runtime, windows_sandbox.GUEST_RUNTIME, True),
                windows_sandbox.MappedFolder(output_root, windows_sandbox.GUEST_OUTPUT, False),
            )
            original_is_dir = Path.is_dir
            with patch.object(Path, 'is_dir',
                              lambda candidate: candidate == runtime or original_is_dir(candidate)):
                root = ElementTree.fromstring(build_sandbox_configuration(mapped_folders=folders))
            runtime_mapping = root.find('MappedFolders')[1]
            self.assertEqual(str(runtime), runtime_mapping.findtext('HostFolder'))
            self.assertEqual('true', runtime_mapping.findtext('ReadOnly'))
            for index in (0, 2):
                replaced = list(folders)
                old = folders[index]
                replaced[index] = windows_sandbox.MappedFolder(runtime, old.guest_path, old.read_only)
                with self.subTest(guest=old.guest_path), self.assertRaises(ValueError):
                    build_sandbox_configuration(mapped_folders=tuple(replaced))

    def test_configuration_disables_host_integrations_and_network(self):
        root = ElementTree.fromstring(build_sandbox_configuration())
        values = {item.tag: item.text for item in root}

        self.assertEqual('Configuration', root.tag)
        self.assertEqual('Disable', values['Networking'])
        self.assertEqual('Disable', values['ClipboardRedirection'])
        self.assertEqual('Disable', values['PrinterRedirection'])
        self.assertEqual('Disable', values['AudioInput'])
        self.assertEqual('Disable', values['VideoInput'])
        self.assertEqual('Disable', values['vGPU'])
        self.assertEqual('2048', values['MemoryInMB'])

    def test_memory_cannot_be_lower_than_windows_sandbox_minimum(self):
        with self.assertRaises(ValueError):
            build_sandbox_configuration(memory_mib=1024)

    def test_guest_command_uses_fixed_runtime_wrapper_and_separate_directories(self):
        command = guest_command(
            r'C:\MMPS\runtime\python.exe',
            r'C:\MMPS\runtime\sandbox_entry.py',
            r'C:\MMPS\input\generated.py',
            r'C:\MMPS\input',
            r'C:\MMPS\output',
        )

        self.assertEqual(
            r'C:\MMPS\runtime\python.exe C:\MMPS\runtime\sandbox_entry.py C:\MMPS\input\generated.py C:\MMPS\input C:\MMPS\output',
            command,
        )
        self.assertNotIn('powershell', command.casefold())

    def test_guest_command_rejects_unexpected_mapping_paths(self):
        with self.assertRaises(ValueError):
            guest_command(
                r'C:\MMPS\runtime\python.exe',
                r'C:\MMPS\runtime\sandbox_entry.py',
                r'C:\Users\WDAGUtilityAccount\Desktop\generated.py',
                r'C:\Users\WDAGUtilityAccount\Desktop',
                r'C:\MMPS\output',
            )

    def test_guest_result_is_loaded_from_a_bounded_shared_file(self):
        with tempfile.TemporaryDirectory(dir=WORKSPACE / 'tmp') as temp:
            output_root = Path(temp)
            result_path = output_root / windows_sandbox.GUEST_RESULT_FILENAME
            result_path.write_text(json.dumps({
                'ExitCode': 1,
                'Failure': {'error_type': 'NameError', 'line': 233, 'name': 'R_ARE'},
            }), encoding='utf-8')

            exit_code, failure = WindowsSandboxExecutor._read_guest_result(output_root)

        self.assertEqual(1, exit_code)
        self.assertEqual('NameError，generated.py 第 233 行，未定义名称 R_ARE。',
                         failure.display_text())

    def test_guest_result_rejects_unsafe_failure_metadata(self):
        with tempfile.TemporaryDirectory(dir=WORKSPACE / 'tmp') as temp:
            output_root = Path(temp)
            result_path = output_root / windows_sandbox.GUEST_RESULT_FILENAME
            result_path.write_text(json.dumps({
                'ExitCode': 1,
                'Failure': {'error_type': 'NameError', 'line': 233, 'name': 'secret value'},
            }), encoding='utf-8')

            with self.assertRaises(windows_sandbox.SandboxExecutionError):
                WindowsSandboxExecutor._read_guest_result(output_root)

    def test_guest_result_rejects_missing_malformed_or_extra_fields(self):
        with tempfile.TemporaryDirectory(dir=WORKSPACE / 'tmp') as temp:
            output_root = Path(temp)
            result_path = output_root / windows_sandbox.GUEST_RESULT_FILENAME
            for content in ('', '{bad json',
                            json.dumps({'ExitCode': 0, 'Failure': None, 'private': 'value'}),
                            json.dumps({'ExitCode': True, 'Failure': None})):
                with self.subTest(content=content):
                    result_path.write_text(content, encoding='utf-8')
                    with self.assertRaises(windows_sandbox.SandboxExecutionError):
                        WindowsSandboxExecutor._read_guest_result(output_root)
            result_path.unlink()
            with self.assertRaises(windows_sandbox.SandboxExecutionError):
                WindowsSandboxExecutor._read_guest_result(output_root)

    def test_output_inspection_allows_only_the_reserved_result_file(self):
        with tempfile.TemporaryDirectory(dir=WORKSPACE / 'tmp') as temp:
            output_root = Path(temp)
            result_path = output_root / windows_sandbox.GUEST_RESULT_FILENAME
            result_path.write_text('{}', encoding='utf-8')
            expected = (ArtifactExpectation('40_results/result.csv', 'table'),)

            total, unexpected = WindowsSandboxExecutor._inspect_output(output_root, expected)

            self.assertEqual(2, total)
            self.assertFalse(unexpected)
            (output_root / 'unexpected.txt').write_text('x', encoding='utf-8')
            _, unexpected = WindowsSandboxExecutor._inspect_output(output_root, expected)
            self.assertTrue(unexpected)

    def test_cleanup_removes_read_only_snapshot_files_after_sandbox_stops(self):
        workspace_tmp = Path(r'F:\MathModelPaperStudio\tmp')
        with tempfile.TemporaryDirectory(dir=workspace_tmp) as temp_root:
            work_root = Path(temp_root) / 'sandbox'
            input_root = work_root / 'input'
            input_root.mkdir(parents=True)
            snapshot = input_root / 'generated.py'
            snapshot.write_text('trusted test fixture', encoding='utf-8')
            snapshot.chmod(0o444)

            WindowsSandboxExecutor._remove_work_root(work_root)

            self.assertFalse(work_root.exists())

    def test_cleanup_removes_guest_created_read_only_output_files(self):
        workspace_tmp = WORKSPACE / 'tmp'
        with tempfile.TemporaryDirectory(dir=workspace_tmp) as temp_root:
            work_root = Path(temp_root) / 'sandbox'
            output_root = work_root / 'output'
            output_root.mkdir(parents=True)
            artifact = output_root / 'result.csv'
            artifact.write_text('x\n1\n', encoding='utf-8')
            artifact.chmod(0o444)

            WindowsSandboxExecutor._remove_work_root(work_root)

            self.assertFalse(work_root.exists())

    def test_stop_reconciles_absent_session_even_when_stop_command_fails(self):
        cli = Path(r'F:\MathModelPaperStudio\wsb.exe')
        failed = __import__('subprocess').CompletedProcess([], 1, '', 'already stopped')
        with patch('mathmodel_paper_studio.execution.windows_sandbox.subprocess.run',
                   return_value=failed), patch(
                       'mathmodel_paper_studio.execution.windows_sandbox._sandbox_ids', return_value=set()
                   ):
            self.assertTrue(WindowsSandboxExecutor._stop(cli, 'session-id'))

    def test_owner_process_probe_recognizes_current_process(self):
        self.assertTrue(WindowsSandboxExecutor._owner_process_alive(os.getpid()))

    def test_start_timeout_retains_recovery_record_when_session_shutdown_is_unknown(self):
        paths, project_root, layout = make_layout(self)
        runtime_root = paths.root / 'runtime'
        runtime_root.mkdir()
        (runtime_root / 'runtime-manifest.json').write_text('{}', encoding='utf-8')
        executor = WindowsSandboxExecutor(runtime_root, cli_path=paths.root / 'wsb.exe')
        source = layout.path_for('30_src/generated.py')
        source.write_text('raise SystemExit(0)\n', encoding='utf-8')
        input_file = project_root / '00_inputs' / 'sample.csv'
        input_file.write_text('x\n1\n', encoding='utf-8')
        expected = (ArtifactExpectation('40_results/result.csv', 'table'),)

        with patch.object(executor, 'available', return_value=(True, 'ready')), \
                patch('mathmodel_paper_studio.execution.windows_sandbox.subprocess.run',
                      side_effect=__import__('subprocess').TimeoutExpired('wsb start', 30)), \
                patch.object(executor, '_stop', return_value=False) as stop:
            result = executor.execute(
                layout, source, (input_file,), expected, memory_mib=2048,
                wall_seconds=30, artifact_bytes=1024,
            )

        self.assertGreaterEqual(stop.call_count, 1)
        self.assertIn('Sandbox', result.blocked_reason)
        recovery_dir = paths.temp / 'sandbox-recovery'
        records = tuple(recovery_dir.glob('*.json'))
        self.assertEqual(1, len(records))
        record = json.loads(records[0].read_text('utf-8'))
        retained_work_root = Path(record['work_root'])
        self.assertTrue(retained_work_root.is_dir())

    def test_recovery_stops_then_removes_quarantined_work_directory(self):
        with tempfile.TemporaryDirectory(dir=WORKSPACE / 'tmp') as temp_root:
            temp_path = Path(temp_root)
            work_root = temp_path / 'mmps-sandbox-retained'
            (work_root / 'output').mkdir(parents=True)
            record = WindowsSandboxExecutor._write_recovery_record(
                temp_path, 'c214ca8d-54d3-44ea-b04a-951dd0b17aa0', work_root,
            )
            executor = WindowsSandboxExecutor(
                temp_path, cli_path=temp_path / 'wsb.exe',
            )
            with patch.object(executor, '_owner_process_alive', return_value=False), \
                    patch.object(executor, '_stop', return_value=True) as stop:
                self.assertTrue(executor._recover_pending_sessions(temp_path))

            stop.assert_called_once_with(executor.cli_path, 'c214ca8d-54d3-44ea-b04a-951dd0b17aa0')
            self.assertFalse(work_root.exists())
            self.assertFalse(record.exists())

    def test_recovery_does_not_stop_a_session_owned_by_a_live_process(self):
        with tempfile.TemporaryDirectory(dir=WORKSPACE / 'tmp') as temp_root:
            temp_path = Path(temp_root)
            work_root = temp_path / 'mmps-sandbox-active'
            (work_root / 'output').mkdir(parents=True)
            record = WindowsSandboxExecutor._write_recovery_record(
                temp_path, '90c92a08-9418-46e8-b844-5d7fc9f1edca', work_root,
            )
            executor = WindowsSandboxExecutor(temp_path, cli_path=temp_path / 'wsb.exe')
            with patch.object(executor, '_owner_process_alive', return_value=True), \
                    patch('mathmodel_paper_studio.execution.windows_sandbox._session_is_active',
                          return_value=True), \
                    patch.object(executor, '_stop', return_value=True) as stop:
                self.assertFalse(executor._recover_pending_sessions(temp_path))

            stop.assert_not_called()
            self.assertTrue(work_root.is_dir())
            self.assertTrue(record.is_file())

    def test_recovery_can_retry_finished_session_in_same_process(self):
        with tempfile.TemporaryDirectory(dir=WORKSPACE / 'tmp') as temp_root:
            temp_path = Path(temp_root)
            work_root = temp_path / 'mmps-sandbox-finished'
            (work_root / 'output').mkdir(parents=True)
            session_id = '994d422e-9844-4e07-9d75-37f38f61f1ab'
            record = WindowsSandboxExecutor._write_recovery_record(
                temp_path, session_id, work_root,
            )
            executor = WindowsSandboxExecutor(temp_path, cli_path=temp_path / 'wsb.exe')

            with patch.object(executor, '_owner_process_alive', return_value=True), \
                    patch('mathmodel_paper_studio.execution.windows_sandbox._session_is_active',
                          return_value=False), \
                    patch.object(executor, '_stop', return_value=True) as stop:
                self.assertTrue(executor._recover_pending_sessions(temp_path))

            stop.assert_called_once_with(executor.cli_path, session_id)
            self.assertFalse(work_root.exists())
            self.assertFalse(record.exists())

    def test_legacy_recovery_record_cleans_only_when_session_is_absent(self):
        with tempfile.TemporaryDirectory(dir=WORKSPACE / 'tmp') as temp_root:
            temp_path = Path(temp_root)
            work_root = temp_path / 'mmps-sandbox-legacy'
            (work_root / 'output').mkdir(parents=True)
            recovery_root = temp_path / 'sandbox-recovery'
            recovery_root.mkdir()
            session_id = 'a67595ec-f797-4625-b934-2ea4a34be4ac'
            record = recovery_root / f'{session_id}.json'
            record.write_text(json.dumps({
                'schema_version': 1,
                'session_id': session_id,
                'work_root': str(work_root),
            }), encoding='utf-8')
            executor = WindowsSandboxExecutor(temp_path, cli_path=temp_path / 'wsb.exe')

            with patch('mathmodel_paper_studio.execution.windows_sandbox._sandbox_ids', return_value=set()), \
                    patch.object(executor, '_stop') as stop:
                self.assertTrue(executor._recover_pending_sessions(temp_path))

            stop.assert_not_called()
            self.assertFalse(work_root.exists())
            self.assertFalse(record.exists())

    def test_legacy_recovery_record_never_stops_unowned_active_session(self):
        with tempfile.TemporaryDirectory(dir=WORKSPACE / 'tmp') as temp_root:
            temp_path = Path(temp_root)
            work_root = temp_path / 'mmps-sandbox-legacy-active'
            (work_root / 'output').mkdir(parents=True)
            recovery_root = temp_path / 'sandbox-recovery'
            recovery_root.mkdir()
            session_id = '5ed44020-5d7d-4d86-b088-14a41c8b7801'
            record = recovery_root / f'{session_id}.json'
            record.write_text(json.dumps({
                'schema_version': 1,
                'session_id': session_id,
                'work_root': str(work_root),
            }), encoding='utf-8')
            executor = WindowsSandboxExecutor(temp_path, cli_path=temp_path / 'wsb.exe')

            with patch('mathmodel_paper_studio.execution.windows_sandbox._sandbox_ids',
                       return_value={session_id}), patch.object(executor, '_stop') as stop:
                self.assertFalse(executor._recover_pending_sessions(temp_path))

            stop.assert_not_called()
            self.assertTrue(work_root.is_dir())
            self.assertTrue(record.is_file())

    def test_legacy_recovery_list_timeout_fails_closed_without_raising(self):
        with tempfile.TemporaryDirectory(dir=WORKSPACE / 'tmp') as temp_root:
            temp_path = Path(temp_root)
            recovery_root = temp_path / 'sandbox-recovery'
            recovery_root.mkdir()
            session_id = 'b8a68706-e0fd-4876-a052-e9ad1fe2f8c2'
            (recovery_root / f'{session_id}.json').write_text(json.dumps({
                'schema_version': 1,
                'session_id': session_id,
                'work_root': str(temp_path / 'mmps-sandbox-legacy-timeout'),
            }), encoding='utf-8')
            executor = WindowsSandboxExecutor(temp_path, cli_path=temp_path / 'wsb.exe')

            with patch('mathmodel_paper_studio.execution.windows_sandbox._sandbox_ids',
                       side_effect=__import__('subprocess').TimeoutExpired('wsb list', 10)):
                self.assertFalse(executor._recover_pending_sessions(temp_path))

    def test_malformed_recovery_record_fails_closed_without_raising(self):
        with tempfile.TemporaryDirectory(dir=WORKSPACE / 'tmp') as temp_root:
            temp_path = Path(temp_root)
            recovery_root = temp_path / 'sandbox-recovery'
            recovery_root.mkdir()
            (recovery_root / 'bad.json').write_text('[]', encoding='utf-8')
            executor = WindowsSandboxExecutor(temp_path, cli_path=temp_path / 'wsb.exe')
            with patch.object(executor, '_stop') as stop:
                try:
                    result = executor._recover_pending_sessions(temp_path)
                except AttributeError:
                    result = 'raised AttributeError'

            self.assertFalse(result)
            stop.assert_not_called()

    def test_input_snapshot_must_match_the_reviewed_digest(self):
        paths, project_root, layout = make_layout(self)
        runtime_root = paths.root / 'runtime'
        runtime_root.mkdir()
        (runtime_root / 'runtime-manifest.json').write_text('{}', encoding='utf-8')
        executor = WindowsSandboxExecutor(runtime_root, cli_path=paths.root / 'wsb.exe')
        source = layout.path_for('30_src/generated.py')
        source.write_text('raise SystemExit(0)\n', encoding='utf-8')
        input_file = project_root / '00_inputs' / 'sample.csv'
        input_file.write_text('x\n1\n', encoding='utf-8')

        with patch.object(executor, 'available', return_value=(True, 'ready')), \
                patch('mathmodel_paper_studio.execution.windows_sandbox.subprocess.run') as start:
            result = executor.execute(
                layout, source, (input_file,), (ArtifactExpectation('40_results/result.csv', 'table'),),
                memory_mib=2048, wall_seconds=30, artifact_bytes=1024,
                expected_input_hashes=('0' * 64,),
            )

        self.assertEqual(StageStatus.PAUSED, result.stage_status)
        start.assert_not_called()
