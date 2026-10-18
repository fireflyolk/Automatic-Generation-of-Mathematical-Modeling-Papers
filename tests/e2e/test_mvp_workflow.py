"""Release contracts using real import/checkpoint/DOCX services and fixed test data."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
from uuid import uuid4
from zipfile import ZipFile

from openpyxl import Workbook
from mathmodel_paper_studio.agents.preflight import Provider
from mathmodel_paper_studio.bootstrap.paths import AppPaths
from mathmodel_paper_studio.domain.models import Run, RunStatus, SourceFile, Stage, StageStatus
from mathmodel_paper_studio.execution.python_runner import RunArtifactLayout
from mathmodel_paper_studio.execution.result_validation import ArtifactExpectation, ResultValidator
from mathmodel_paper_studio.paper.docx_qa import run_docx_qa
from mathmodel_paper_studio.paper.docx_writer import PaperDraft, TextSection, write_paper
from mathmodel_paper_studio.paper.template_inspector import inspect_template
from mathmodel_paper_studio.paper.template_mapping import TemplateMapping
from mathmodel_paper_studio.projects.project_service import ProjectService
from mathmodel_paper_studio.storage.database import Database
from mathmodel_paper_studio.storage.repositories import ProjectRepository, RunRepository
from mathmodel_paper_studio.workflow.engine import WorkflowEngine
from mathmodel_paper_studio.workflow.stages import StageOutput, StageResult, WORKFLOW_STAGES
from tests.fixtures.docx.fixture_factory import write_fixture

ROOT = Path(__file__).resolve().parents[2]
TEMP = Path(os.environ["TEMP"])
AUDITOR = ROOT / "scripts/verify_f_drive_outputs.py"


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


class FixedFixtureEngine(WorkflowEngine):
    """Test-only provider seam. Never runs generated code or a remote provider."""

    def __init__(self, *args, project, run, template, mapping, inspection, **kwargs):
        super().__init__(*args, **kwargs)
        self.fixture_project, self.fixture_run = project, run
        self.template, self.mapping, self.inspection = template, mapping, inspection
        self.seen = []
        self.paper_evidence = ()

    @property
    def layout(self):
        return RunArtifactLayout(self.paths, self.fixture_project, self.fixture_run)

    def _execute_stage(self, context, artifact_directory, cancel_event=None):
        self.seen.append(context.stage)
        if context.stage is Stage.CALCULATION:
            # Hand-checked fixture: values 2 and 4 have mean 3. No code execution.
            content = b"metric,value\nmean,3\n"
            (self.layout.results_dir / "metrics.csv").write_bytes(content)
            outputs = (StageOutput("metrics.csv", content, "table"),)
        elif context.stage is Stage.RESULT_VALIDATION:
            report = ResultValidator(self.layout).validate((
                ArtifactExpectation("40_results/metrics.csv", "table",
                                    expected_columns=("metric", "value"), expected_rows=1),
            ))
            if report.issues:
                raise AssertionError(report.issues)
            outputs = (StageOutput("validation.json", report.summary_path.read_bytes(), "validation"),)
        elif context.stage is Stage.PAPER_DRAFTING:
            self.paper_evidence = tuple(item.evidence_id for item in context.upstream_artifacts
                                        if item.kind == "table")
            staging = self.paths.temp / "paper-staging"
            staging.mkdir(exist_ok=True)
            draft = PaperDraft(sections=(TextSection(
                "abstract", ("The fixed fixture mean is 3.",), self.paper_evidence,
                ("Fixture data; mathematical review required.",),
            ),))
            generated = write_paper(self.template, staging, self.inspection, self.mapping, draft)
            outputs = (StageOutput("paper.docx", generated.path.read_bytes(), "paper"),)
        elif context.stage is Stage.DOCX_QA:
            paper = next(item for item in context.upstream_artifacts if item.kind == "paper")
            report = run_docx_qa(self.layout.project_root / paper.relative_path,
                                self.inspection, self.mapping,
                                required_section_keys=("abstract",),
                                expected_evidence_ids=self.paper_evidence)
            if not report.package_valid or report.missing_evidence or report.missing_sections:
                raise AssertionError(report)
            outputs = (StageOutput("qa.json", json.dumps({
                "structural_status": report.structural_status,
                "visual_rendering": report.visual_rendering,
                "linked_evidence_ids": report.linked_evidence_ids,
            }).encode("utf-8"), "validation"),)
        else:
            outputs = (StageOutput("result.json", json.dumps({
                "stage": context.stage.value, "test_fixture": True,
            }).encode("utf-8")),)
        return StageResult(context.stage, context.attempt, StageStatus.COMPLETED, outputs=outputs)


class MvpWorkflowE2ETests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="task10-e2e-", dir=TEMP)
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.paths = AppPaths(self.root / "app")
        self.paths.ensure_directories()
        database = Database(self.paths)
        database.migrate()
        self.projects, self.runs = ProjectRepository(database), RunRepository(database)
        inputs = self.root / "originals"
        inputs.mkdir()
        self.csv = inputs / "observations.csv"
        self.csv.write_bytes(b"x,y\n1,2\n2,4\n")
        self.xlsx = inputs / "observations.xlsx"
        book = Workbook()
        book.active.append(("x", "y"))
        book.active.append((1, 2))
        book.active.append((2, 4))
        book.save(self.xlsx)
        book.close()
        self.template = write_fixture(inputs / "template.docx")
        self.originals = {path: digest(path) for path in inputs.iterdir()}
        self.project = ProjectService(self.paths, self.projects).create(
            "Release fixture", "Find the mean of y.", (self.csv, self.xlsx), template=self.template)
        self.project_root = self.paths.root / self.project.relative_path
        manifest = json.loads((self.project_root / "00_inputs/manifest-v0001.json").read_text("utf-8"))
        self.sources = tuple(SourceFile(**item["record"]) for item in manifest["sources"])
        imported = next(item for item in self.sources if item.kind == "docx")
        self.imported_template = self.project_root / imported.relative_path
        self.inspection = inspect_template(self.imported_template)
        heading = next(item.anchor_id for item in self.inspection.anchors
                       if item.kind == "heading" and item.label == "\u6458\u8981")
        self.mapping = TemplateMapping.from_selection(self.inspection, {"abstract": heading})
        self.mapping.save(self.project_root)

    def make_run(self):
        run_id = uuid4().hex
        run = Run(run_id, self.project.project_id, Provider.CLAUDE,
                  RunStatus.CREATED, f"90_runs/{run_id}")
        self.runs.add(run)
        return run

    def test_all_nine_stages_preserve_originals_and_link_real_docx_to_fixture_results(self):
        run = self.make_run()
        engine = FixedFixtureEngine(self.paths, self.projects, self.runs,
                                    project=self.project, run=run,
                                    template=self.imported_template, mapping=self.mapping,
                                    inspection=self.inspection)
        selected = tuple(item.source_id for item in self.sources)
        state = engine.start(self.project.project_id, run.run_id, self.sources, selected)
        self.assertEqual(RunStatus.COMPLETED, state.status)
        self.assertEqual(list(WORKFLOW_STAGES), engine.seen)
        self.assertEqual(9, len(state.stage_records))
        self.assertEqual({"problem", "csv", "xlsx", "docx"}, {s.kind for s in self.sources})
        for artifact in state.artifacts:
            self.assertEqual(artifact.sha256, digest(self.project_root / artifact.relative_path))
        paper = next(item for item in state.artifacts if item.kind == "paper")
        with ZipFile(self.project_root / paper.relative_path) as archive:
            xml = archive.read("word/document.xml").decode("utf-8")
        self.assertIn("The fixed fixture mean is 3.", xml)
        self.assertTrue(engine.paper_evidence)
        for evidence_id in engine.paper_evidence:
            self.assertIn(evidence_id, xml)
        qa = next(item for item in state.artifacts if item.stage is Stage.DOCX_QA)
        self.assertEqual("not_performed", json.loads(
            (self.project_root / qa.relative_path).read_text("utf-8"))["visual_rendering"])
        self.assertEqual(self.originals, {path: digest(path) for path in self.originals})
        self.assertEqual(digest(self.template), digest(self.imported_template))
        restarted = engine.start(self.project.project_id, run.run_id, self.sources, selected)
        self.assertEqual(state, restarted)
        self.assertEqual(9, len(engine.seen))

    def test_production_engine_still_pauses_and_cancelled_fixture_writes_no_stage_outputs(self):
        run = self.make_run()
        engine = WorkflowEngine(self.paths, self.projects, self.runs)
        selected = tuple(item.source_id for item in self.sources)
        state = engine.start(self.project.project_id, run.run_id, self.sources, selected)
        self.assertEqual(RunStatus.PAUSED, state.status)
        self.assertEqual((), state.artifacts)
        cancelled_run = self.make_run()
        cancel = threading.Event()
        cancel.set()
        state = engine.start(self.project.project_id, cancelled_run.run_id, self.sources, selected,
                             cancel_event=cancel)
        self.assertEqual(RunStatus.CANCELLED, state.status)
        self.assertEqual((), state.artifacts)


class ReleaseAuditContracts(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="task10-audit-", dir=TEMP)
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def auditor(self):
        self.assertTrue(AUDITOR.is_file(), "Release path/credential auditor is missing")
        spec = importlib.util.spec_from_file_location("task10_release_auditor", AUDITOR)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def test_build_preflight_requires_the_locked_computation_runtime(self):
        script = ROOT / "scripts/build_windows.ps1"
        runtime = Path(
            r"F:\Codex另一个工作的地方\downloads\mathmodel-paper-studio-sandbox-20261018"
            r"\computation-runtime-final"
        )
        good = subprocess.run(
            ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script),
             "-CheckOnly", "-BuildRoot", str(self.root / "good-build"),
             "-OutputRoot", str(self.root / "good-output"), "-TaskTemp", str(self.root / "good-tmp"),
             "-ComputationRuntimeRoot", str(runtime)],
            cwd=ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30,
        )
        self.assertEqual(0, good.returncode, good.stderr)

        missing_runtime = self.root / "missing-runtime"
        bad = subprocess.run(
            ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script),
             "-CheckOnly", "-BuildRoot", str(self.root / "bad-build"),
             "-OutputRoot", str(self.root / "bad-output"), "-TaskTemp", str(self.root / "bad-tmp"),
             "-ComputationRuntimeRoot", str(missing_runtime)],
            cwd=ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30,
        )
        self.assertNotEqual(0, bad.returncode)
        self.assertFalse((self.root / "bad-build").exists())
        self.assertFalse((self.root / "bad-output").exists())

    def test_source_launch_exports_computation_runtime_before_desktop_bootstrap(self):
        script = (ROOT / "scripts/build_windows.ps1").read_text(encoding="utf-8-sig")
        export_runtime = script.index("$env:MMPS_COMPUTATION_RUNTIME_ROOT = $ComputationRuntimeRoot")
        launch_source = script.index("if ($LaunchSource)")
        self.assertLess(export_runtime, launch_source)

    def test_inventory_hashes_files_and_rejects_paths_outside_approved_f_root(self):
        audit = self.auditor()
        tree = self.root / "package"
        tree.mkdir()
        (tree / "safe.txt").write_bytes(b"fixture payload")
        inventory = audit.inventory_tree(tree)
        self.assertEqual(digest(tree / "safe.txt"), inventory[0]["sha256"])
        with self.assertRaises(ValueError):
            audit.inventory_tree(Path("C:/forbidden-release-root"))

    def test_inventory_rejects_credentials_without_echoing_the_value(self):
        audit = self.auditor()
        (self.root / "output.log").write_text("api_key=" + "sk-" + "x" * 35, encoding="utf-8")
        with self.assertRaises(ValueError) as caught:
            audit.inventory_tree(self.root)
        self.assertNotIn("x" * 35, str(caught.exception))
        self.assertIn("output.log", str(caught.exception))

    def test_inventory_rejects_native_login_store_names_even_when_empty(self):
        audit = self.auditor()
        (self.root / "auth.json").write_bytes(b"{}")
        with self.assertRaises(ValueError):
            audit.inventory_tree(self.root)

    def test_build_preflight_rejects_reparse_output_before_creating_directories(self):
        from tests_task3_helpers import junction
        script = ROOT / "scripts/build_windows.ps1"
        good = subprocess.run(["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script), "-CheckOnly",
                               "-BuildRoot", str(self.root / "build"),
                               "-OutputRoot", str(self.root / "safe-output")],
                              cwd=ROOT, capture_output=True, text=True,
                              encoding="utf-8", errors="replace", timeout=30)
        self.assertEqual(0, good.returncode, good.stderr)
        target = self.root / "real-target"
        target.mkdir()
        with junction(self.root / "linked-output", target) as link:
            bad = subprocess.run(["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script), "-CheckOnly",
                                  "-BuildRoot", str(self.root / "build"),
                                  "-OutputRoot", str(link / "release")],
                                 cwd=ROOT, capture_output=True, text=True,
                                 encoding="utf-8", errors="replace", timeout=30)
        self.assertNotEqual(0, bad.returncode, "Unsafe junction output passed build preflight")
        self.assertEqual((), tuple(target.iterdir()))

    def test_key_format_marker_is_allowed_but_private_key_body_is_rejected(self):
        audit = self.auditor()
        member = self.root / "parser.dat"
        marker = b"-----BEGIN RSA PRIVATE KEY-----"
        member.write_bytes(marker + b"\x00format marker only")
        self.assertEqual(1, len(audit.inventory_tree(self.root)))
        member.write_bytes(marker + b"\n" + b"A" * 64 + b"\n-----END RSA PRIVATE KEY-----")
        with self.assertRaises(ValueError):
            audit.inventory_tree(self.root)

    def test_license_inventory_excludes_python_cache_records_from_installed_wheels(self):
        audit = self.auditor()
        try:
            notices = audit.export_licenses(ROOT / "requirements.lock", self.root / "notices")
        except ValueError as exc:
            self.fail("Trusted wheel license inventory failed: " + str(exc))
        self.assertTrue(notices)
        self.assertTrue(any(item["license_files"] for item in notices))
        self.assertEqual((), tuple(item["name"] for item in notices if not item["license_files"]),
                         "Every locked distribution needs its actual license notice")
        files = tuple((self.root / "notices").rglob("*"))
        self.assertFalse(any(p.suffix in (".py", ".pyc", ".pyo", ".pyd") for p in files))
        self.assertTrue(all(p.drive.lower() == "f:" for p in files))

    def test_startup_check_initializes_only_f_paths_and_c_root_fails(self):
        self.assertTrue(AUDITOR.is_file(), "Desktop startup checker is missing")
        report = self.root / "startup.json"
        command = [sys.executable, "-B", str(AUDITOR), "--startup-check",
                   "--root", str(self.root / "app"), "--report", str(report)]
        good = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, timeout=30)
        self.assertEqual(0, good.returncode, good.stderr)
        payload = json.loads(report.read_text("utf-8"))
        self.assertEqual("ok", payload["status"])
        self.assertTrue(all(Path(p).drive.lower() == "f:" for p in payload["managed_paths"]))
        bad = subprocess.run([*command[:4], "--root", "C:/invalid-root"],
                             cwd=ROOT, capture_output=True, text=True, timeout=30)
        self.assertNotEqual(0, bad.returncode)


if __name__ == "__main__":
    unittest.main()
