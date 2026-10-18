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


WORKSPACE = Path(r'F:\Codex另一个工作的地方')


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
        runtime = r'F:\Codex另一个工作的地方\downloads\computation-runtime'
        with patch.dict(os.environ, {'MMPS_COMPUTATION_RUNTIME_ROOT': runtime}):
            resolver = getattr(windows_sandbox, 'resolve_computation_runtime_root', None)
            self.assertTrue(callable(resolver), 'source runtime resolver is missing')
            self.assertEqual(Path(runtime), resolver())

    def test_frozen_runtime_resolves_beside_pyinstaller_meipass(self):
        package_root = WORKSPACE / 'tmp' / 'frozen-package-fixture'
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop('MMPS_COMPUTATION_RUNTIME_ROOT', None)
            with patch.object(windows_sandbox.sys, '_MEIPASS', str(package_root), create=True):
                self.assertEqual(package_root / 'computation-runtime',
                                 windows_sandbox.resolve_computation_runtime_root())

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

    def test_cleanup_removes_read_only_snapshot_files_after_sandbox_stops(self):
        workspace_tmp = Path(r'F:\Codex另一个工作的地方\tmp')
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
        cli = Path(r'F:\Codex另一个工作的地方\wsb.exe')
        failed = __import__('subprocess').CompletedProcess([], 1, '', 'already stopped')
        with patch('mathmodel_paper_studio.execution.windows_sandbox.subprocess.run',
                   return_value=failed), patch(
                       'mathmodel_paper_studio.execution.windows_sandbox._sandbox_ids', return_value=set()
                   ):
            self.assertTrue(WindowsSandboxExecutor._stop(cli, 'session-id'))

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
            with patch.object(executor, '_stop', return_value=True) as stop:
                self.assertTrue(executor._recover_pending_sessions(temp_path))

            stop.assert_called_once_with(executor.cli_path, 'c214ca8d-54d3-44ea-b04a-951dd0b17aa0')
            self.assertFalse(work_root.exists())
            self.assertFalse(record.exists())

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
