"""Opt-in acceptance test for the real Windows Sandbox backend.

This test writes its fixture and evidence only below the configured F: workspace.
Generated source runs exclusively in Windows Sandbox via ``wsb exec``.
"""
import csv
import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest
from uuid import uuid4

from mathmodel_paper_studio.agents.preflight import Provider
from mathmodel_paper_studio.bootstrap.paths import AppPaths
from mathmodel_paper_studio.domain.models import Project, Run, RunStatus, StageStatus
from mathmodel_paper_studio.execution.python_runner import PythonRunner, RunArtifactLayout
from mathmodel_paper_studio.execution.result_validation import ArtifactExpectation
from mathmodel_paper_studio.execution.windows_sandbox import WindowsSandboxExecutor


F_WORKSPACE = Path(r"F:\Codex另一个工作的地方")
RUNTIME_DEFAULT = Path(
    r"F:\Codex另一个工作的地方\downloads\mathmodel-paper-studio-sandbox-20261018"
    r"\computation-runtime-final"
)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(128 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


class WindowsSandboxLiveAcceptanceTests(unittest.TestCase):
    """Exercises process isolation, offline execution, runtime imports, and artifacts."""

    def test_generated_calculation_runs_only_in_sandbox_and_returns_valid_artifacts(self):
        runtime_root = Path(os.environ.get("MMPS_COMPUTATION_RUNTIME_ROOT", RUNTIME_DEFAULT))
        executor = WindowsSandboxExecutor(runtime_root)
        available, reason = executor.available()
        self.assertTrue(available, reason)

        task_root = F_WORKSPACE / "tmp" / f"windows-sandbox-live-{uuid4().hex[:12]}"
        task_root.mkdir(parents=True)
        self.assertTrue(task_root.resolve().is_relative_to(F_WORKSPACE.resolve()))
        paths = AppPaths(task_root / "app")
        paths.ensure_directories()

        suffix = uuid4().hex[:8]
        project = Project(f"sandbox{suffix}", "沙盒合成验收", f"projects/sandbox{suffix}")
        project_root = paths.root / project.relative_path
        project_root.mkdir(parents=True)
        for name in (
            "00_inputs", "10_analysis", "20_model", "30_src", "40_results",
            "50_figures", "60_paper", "70_review", "90_runs",
        ):
            (project_root / name).mkdir()
        run = Run(f"run{suffix}", project.project_id, Provider.CODEX,
                  RunStatus.CREATED, f"90_runs/run{suffix}")
        layout = RunArtifactLayout.create(paths, project, run)

        source_csv = project_root / "00_inputs" / "synthetic.csv"
        source_csv.write_text("x,y\n0,1\n1,3\n2,5\n3,7\n", encoding="utf-8")
        input_hash = _sha256(source_csv)
        host_sentinel = task_root / "host-only-sentinel.txt"
        host_sentinel.write_text("must remain unavailable in guest", encoding="utf-8")
        sentinel_hash = _sha256(host_sentinel)

        generated_source = r'''import csv
import pathlib
import socket
import sys
import traceback

input_dir = pathlib.Path(sys.argv[1])
output_dir = pathlib.Path(sys.argv[2])
row = {
    "execution_status": "ok", "diagnostic": "", "slope": "", "intercept": "",
    "readonly_blocked": "", "host_path_blocked": "", "network_blocked": "",
    "numpy_version": "", "pandas_version": "", "scipy_version": "",
    "matplotlib_version": "", "docx_version": "", "openpyxl_version": "",
    "pypdf_version": "",
}
try:
    import docx
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import numpy as np
    import openpyxl
    import pandas as pd
    import pypdf
    import scipy
    from scipy.stats import linregress

    frame = pd.read_csv(input_dir / "synthetic.csv")
    fit = linregress(frame["x"].to_numpy(), frame["y"].to_numpy())

    readonly_blocked = False
    try:
        (input_dir / "write-probe.txt").write_text("unexpected", encoding="utf-8")
    except OSError:
        readonly_blocked = True

    host_path_blocked = False
    try:
        pathlib.Path(__HOST_SENTINEL__).read_text(encoding="utf-8")
    except OSError:
        host_path_blocked = True

    network_blocked = False
    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    probe.settimeout(1.0)
    try:
        probe.connect(("1.1.1.1", 443))
    except OSError:
        network_blocked = True
    finally:
        probe.close()

    row.update({
        "slope": fit.slope,
        "intercept": fit.intercept,
        "readonly_blocked": int(readonly_blocked),
        "host_path_blocked": int(host_path_blocked),
        "network_blocked": int(network_blocked),
        "numpy_version": np.__version__,
        "pandas_version": pd.__version__,
        "scipy_version": scipy.__version__,
        "matplotlib_version": matplotlib.__version__,
        "docx_version": docx.__version__,
        "openpyxl_version": openpyxl.__version__,
        "pypdf_version": pypdf.__version__,
    })
    figure, axis = plt.subplots(figsize=(4, 3))
    axis.scatter(frame["x"], frame["y"])
    axis.plot(frame["x"], fit.intercept + fit.slope * frame["x"])
    figure.savefig(output_dir / "calc-plot.png", dpi=100)
    plt.close(figure)
except Exception:
    row["execution_status"] = "error"
    row["diagnostic"] = traceback.format_exc(limit=12)

with (output_dir / "calc-result.csv").open("w", encoding="utf-8", newline="") as stream:
    writer = csv.DictWriter(stream, fieldnames=list(row))
    writer.writeheader()
    writer.writerow(row)
'''.replace("__HOST_SENTINEL__", repr(str(host_sentinel)))

        expectations = (
            ArtifactExpectation(
                "40_results/calc-result.csv", "table",
                expected_columns=(
                    "execution_status", "diagnostic", "slope", "intercept",
                    "readonly_blocked", "host_path_blocked", "network_blocked",
                    "numpy_version", "pandas_version", "scipy_version", "matplotlib_version",
                    "docx_version", "openpyxl_version", "pypdf_version",
                ),
                expected_rows=1,
            ),
            ArtifactExpectation("50_figures/calc-plot.png", "figure"),
        )

        outcome = PythonRunner(executor=executor).run(
            layout, generated_source, input_files=(source_csv,), expected_outputs=expectations,
            deterministic_seed=8675309,
        )

        self.assertEqual(StageStatus.COMPLETED, outcome.stage_status,
                         f"{outcome.blocked_reason}; code={outcome.exit_code}")
        self.assertTrue(outcome.execution_started)
        self.assertEqual(0, outcome.exit_code)
        self.assertEqual("sandbox:python.exe", outcome.interpreter)
        self.assertEqual(input_hash, _sha256(source_csv))
        self.assertEqual(sentinel_hash, _sha256(host_sentinel))
        self.assertFalse((source_csv.parent / "write-probe.txt").exists())

        result_csv = layout.path_for("40_results/calc-result.csv")
        figure_png = layout.path_for("50_figures/calc-plot.png")
        self.assertTrue(result_csv.is_file())
        self.assertTrue(figure_png.is_file())
        self.assertGreater(figure_png.stat().st_size, 100)
        with result_csv.open("r", encoding="utf-8-sig", newline="") as stream:
            result = next(csv.DictReader(stream))
        self.assertEqual("ok", result["execution_status"], result["diagnostic"])
        self.assertAlmostEqual(2.0, float(result["slope"]), places=10)
        self.assertAlmostEqual(1.0, float(result["intercept"]), places=10)
        self.assertEqual("1", result["readonly_blocked"])
        self.assertEqual("1", result["host_path_blocked"])
        self.assertEqual("1", result["network_blocked"])
        for package_version in (
            "numpy_version", "pandas_version", "scipy_version", "matplotlib_version",
            "docx_version", "openpyxl_version", "pypdf_version",
        ):
            self.assertTrue(result[package_version], package_version)

        summary = json.loads(outcome.summary_path.read_text("utf-8"))
        self.assertTrue(summary["execution_started"])
        self.assertEqual("sandbox:python.exe", summary["interpreter"])
        self.assertTrue(outcome.artifacts)


if __name__ == "__main__":
    unittest.main()
