# Fluent UI Redesign Implementation Plan

**Goal:** Deliver the approved complete Fluent redesign of MathModel Paper Studio while preserving its project, template, run, provider, disclosure, and packaging contracts.

**Architecture:** Replace the `QMainWindow`/`QTabWidget` shell with a PySide6-compatible Fluent main window and four persistent navigation destinations. Keep existing workflow page objects and signal-driven services; use a narrow compatibility adapter for existing `workspace.setCurrentWidget(...)` navigation calls. Restyle the pages and dialogs in place. Keep business and API behavior unchanged.

**Tech Stack:** Python 3.13, PySide6 6.8.2.1, PySide6-Fluent-Widgets (select a release only after validating its exact Qt/Python/Windows/PyInstaller compatibility), PyInstaller onedir, pytest/pytest-qt if already available in the locked environment.

**Spec:** `docs/superpowers/specs/2026-10-17-fluent-ui-redesign-design.md` (approved by the user).

**Global Constraints:**
- Keep all project, download, build, test temporary, and generated output paths under `F:\Codex另一个工作的地方\`; downloads under `downloads\mathmodel-paper-studio-fluent-20261017\`, temporary files/caches under `tmp\mathmodel-paper-studio-fluent-20261017\`.
- Preserve every pre-existing dirty or untracked file. Stage only the files owned by each completed task; use conventional commit messages if commits are made.
- Keep PySide6 as the only Qt binding. Preserve AppPaths F: validation, database and project layouts, source-file protection, provider choices, API/key handling, DeepSeek consent and revocation, scope disclosure, workflow stages, cancellation/resume, template mapping, DOCX generation, and quality checks.
- Do not change the desktop shortcut, migrate user data, overwrite the existing preview package, or broaden the work into API/security/business behavior.
- Keep the Fluent dependency's GPL-3.0 notice in the package license inventory. The approved scope is personal Windows use; commercial or closed-source distribution requires a separate licensing review.
- Follow test-first implementation for each behavior-changing task. Keep automated UI, source-visible desktop, package inventory/startup, and rendered/visual evidence as separate acceptance records.

**Review Focus:**
- Exactly four Chinese Fluent destinations; no Claude provider option is introduced.
- Project identity and active run state survive navigation; existing `project_list`, `workspace`, `project_page`, `template_view`, `run_panel`, and `settings_view` caller semantics remain available.
- The new dependency is the PySide6 binding, pinned with a complete hash-verified lock closure; Fluent runtime resources and notices are present in onedir.
- Empty, loading, success, error, paused, and cancellation states remain legible and actionable; keyboard navigation and narrow/high-DPI layouts remain usable.
- No change to data disclosure, consent, API calls, project files, templates, worker behavior, or shortcut target.

## Task 1: Verify the Fluent dependency and pin its runtime closure

**Files:** `pyproject.toml`, `requirements.lock`, `packaging/mathmodel_paper_studio.spec`, `scripts/verify_f_drive_outputs.py` (only if the existing license/resource verification needs extension), `tests/e2e/test_mvp_workflow.py` (only if a new license inventory contract is required).

1. In the F:-managed Python environment, inspect the PySide6-Fluent-Widgets release metadata and source API. Verify Python 3.13, Windows, PySide6 6.8.2.1, required transitive packages, PyInstaller resource collection, and GPL-3.0 license files. Put any wheel/source downloads only in `F:\Codex另一个工作的地方\downloads\mathmodel-paper-studio-fluent-20261017\`; route pip/build caches to the task temp directory.
2. If the latest release is incompatible, select a compatible PySide6-only release. Do not upgrade PySide6 or install PyQt bindings to make it work. Stop before UI migration if no supported compatible release can be established and report the concrete incompatibility.
3. Pin the selected distribution and its complete dependency closure in `pyproject.toml` and `requirements.lock` with hashes. Ensure the package metadata/lock includes its notices and required runtime assets. Extend PyInstaller collection only for discovered Fluent modules/data, not blanket unrelated packages.
4. Verify that the locked environment can resolve and import the selected package and that the existing lock verifier/license exporter recognizes every new distribution. Record the exact selected version and license finding in the release acceptance record in Task 7.

**Commit:** `build: pin PySide6 Fluent Widgets runtime` (stage only the dependency/lock/build files changed by this task).

## Task 2: Add the Fluent shell and navigation contracts

**Files:** new `tests/integration/ui/test_fluent_navigation.py`; `src/mathmodel_paper_studio/ui/main_window.py`; new `src/mathmodel_paper_studio/ui/fluent_theme.py` (or a small bootstrap module colocated with the UI if the verified package API requires it); `scripts/verify_f_drive_outputs.py` only if the launch bootstrap must apply the theme before constructing the window.

1. Write failing UI contract tests first. Cover four and only four Chinese destinations, switching each destination, page-instance retention, current project retention, active run-state retention, and preservation of the `workspace.setCurrentWidget(widget)` compatibility call used by scope review and run start. Assert there is no Claude choice in the selectable provider UI.
2. Implement the Fluent theme setup following the verified package API: system-following light/dark palette, resource initialization, readable Chinese font fallback, and no persistent user-setting changes. Keep startup argument and data-path behavior unchanged.
3. Convert `MainWindow` to the verified Fluent main-window base and add the four persistent destinations: `项目与数据`, `Word 模板`, `运行与审核`, `设置`. Add a thin navigation adapter exposing `setCurrentWidget` for existing call sites; preserve the existing named page attributes and signals. Move the project selector into the project/data page without changing `Qt.UserRole` project IDs, selection callbacks, or reload behavior.
4. Update the F:-only source launcher to apply the theme before creating the main window. Keep `python -m mathmodel_paper_studio`'s existing storage-checker behavior unchanged.

**Verification:** Run the new navigation contract test and existing `tests/unit/ui/test_main_window_deepseek_consent.py` after implementation; inspect the changed startup path for `QApplication` construction order and F: path validation.

**Commit:** `feat: add Fluent application shell and navigation` (stage only the shell, theme bootstrap, launcher, and navigation-test files owned by this task).

## Task 3: Redesign the project/data page and project wizard

**Files:** `src/mathmodel_paper_studio/ui/main_window.py`, `src/mathmodel_paper_studio/ui/project_wizard.py`, `tests/integration/ui/test_project_wizard.py`, `tests/integration/ui/test_fluent_navigation.py` only if page structure assertions belong there.

1. Add or update failing tests for project-list selection by stored project ID, empty state/new-project action, wizard validation errors, import progress/error/retry, and preservation of originals when data files are imported. Keep tests focused on visible UI contracts and existing services.
2. Reorganize the page into a title/active-project header, project selector and new-project action, problem statement/data summary, source files, and artifact review. Provide a useful empty state when no project is selected.
3. Restyle the wizard with Fluent inputs, file list, progress, error, retry, and confirmation controls. Keep worker execution, scope validation, thread-pool behavior, and object names/signals used by tests and callers.
4. Ensure keyboard focus order and scrolling work when the page is narrower than the current minimum desktop width.

**Verification:** Run `tests/integration/ui/test_project_wizard.py` and the relevant navigation contract checks. Confirm temporary fixtures remain below the approved F: task temp root.

**Commit:** `feat: redesign project and data screens` (stage only the project/data and wizard files plus their owned tests).

## Task 4: Redesign Word template mapping and related feedback

**Files:** `src/mathmodel_paper_studio/ui/template_mapping_view.py`, `tests/integration/ui/test_template_mapping.py` (new; no UI integration coverage currently exists), existing template-mapping unit tests discovered before implementation.

1. Write failing UI checks for template selection, mapping selection by `currentData()`, saving mapping, status feedback, and the unsupported-structure explanation. Preserve the actual project-root guard and do not mutate the chosen source template.
2. Group template status/selection, chapter mapping, unsupported content, and save feedback into clear Fluent sections. Add scrolling for long mappings and place validation/success messages beside the relevant action.
3. Preserve all existing mapping payload formats, worker-backed operations, signals, and template path validation. Do not change DOCX parsing or writing logic.

**Verification:** Run the new/updated template mapping checks and existing template-inspector/template-mapping tests. Compare a fixture template hash before and after mapping to verify the selected original remains unchanged.

**Commit:** `feat: redesign Word template mapping` (stage only template UI and its owned tests).

## Task 5: Redesign run, review, and settings screens

**Files:** `src/mathmodel_paper_studio/ui/run_panel.py`, `src/mathmodel_paper_studio/ui/run_review.py`, `src/mathmodel_paper_studio/ui/settings_view.py`, `src/mathmodel_paper_studio/ui/main_window.py` only where dialog construction styling is required; `tests/unit/ui/test_run_review_model.py`, `tests/unit/ui/test_deepseek_selection.py`, `tests/unit/ui/test_main_window_deepseek_consent.py`, `tests/integration/ui/test_run_controls.py`.

1. Add/adjust failing UI contract cases before styling for exact scope/provider disclosure, deliberate start availability, cancel/pause/resume behavior and text, provider selection/status, environment-only API key handling, and consent revocation on failure, resume completion, and close.
2. Present run state, current agent, progress/event list, cancellation and resume actions, and artifact access in a clear hierarchy. Style the pre-run and artifact-review dialogs with Fluent controls while retaining their explicit review and confirmation steps.
3. Restyle settings as grouped data-root, provider, and preflight sections. Keep Codex and DeepSeek options, status semantics, environment-only key reads, and existing signal payloads. Do not add Claude or display/save a secret.
4. Preserve the worker state machine, operation callbacks, consent lifecycle, and run IDs. Page navigation during a run must not destroy or reinitialize the active worker/panel.

**Verification:** Run the four listed UI test modules. Add a specific assertion only when an approved contract is currently untested; do not weaken an existing assertion to accommodate the redesign.

**Commit:** `feat: redesign run review and settings screens` (stage only these UI files and the tests owned by this task).

## Task 6: Unify remaining dialogs, accessibility, and responsive layout

**Files:** `src/mathmodel_paper_studio/ui/project_wizard.py`, `src/mathmodel_paper_studio/ui/main_window.py`, `src/mathmodel_paper_studio/ui/run_panel.py` (only if needed to keep its primary controls reachable at the approved viewport), `src/mathmodel_paper_studio/ui/run_review.py`, `src/mathmodel_paper_studio/ui/template_mapping_view.py`, plus focused tests under `tests/integration/ui/`.

1. Add failing checks for keyboard traversal, enabled/disabled/loading feedback, visible error and success messages, and no horizontal overflow at a 1024x720 viewport.
2. Apply the same Fluent typography, spacing, icons, focus states, button hierarchy, status colors, and dialog conventions across all user-facing UI. Do not replace functional icons with emoji.
3. Check Chinese text and contrast in both system light and dark themes. Ensure scrolling/wrapping keeps primary actions visible and the layout does not hide controls at narrow sizes or Windows 125%/150% scaling.

**Verification:** Run the targeted UI checks and inspect the source UI visibly at 1280x720 and 1920x1080, including 125% and 150% scaling where available. Record any unavailable scale/theme in the acceptance record rather than claiming it passed.

**Commit:** `style: unify Fluent accessibility and responsive behavior` (stage only the shared UI styling and tests owned by this task).

## Task 7: Package and perform acceptance

**Files:** `packaging/mathmodel_paper_studio.spec`, `scripts/build_windows.ps1` (add an optional validated `-PythonPath` while retaining the existing default), `scripts/verify_f_drive_outputs.py` only if package inventory checks need extension, `docs/release/acceptance.md` (append a Fluent redesign section; preserve prior content).

1. Run the scoped UI test set from Tasks 2-6, then the project’s existing complete test command if runtime permits. Keep coverage results distinct; do not claim 80% unless the report demonstrates it. Route pytest cache and temp files below `F:\Codex另一个工作的地方\tmp\mathmodel-paper-studio-fluent-20261017\`.
2. Build the onedir app with a fresh, empty build root and output root, e.g. `F:\Codex另一个工作的地方\tmp\mathmodel-paper-studio-fluent-20261017\build` and `F:\Codex另一个工作的地方\downloads\mathmodel-paper-studio-fluent-20261017\release`. Confirm the roots are on F: and do not already contain a prior build before invoking the release script. Add an optional `-PythonPath` argument to select the task's F:-managed isolated interpreter; retain the project's `.venv` default and validate the selected interpreter path beneath the approved F: root before invoking it or creating build outputs.
3. Verify package inventory, archive members, Fluent fonts/icons/theme resources, license notices, and startup. Do not replace or edit the old preview EXE or desktop shortcut. Confirm no PyQt runtime was collected.
4. Launch the new packaged EXE visibly and exercise project creation/import, template selection/mapping save, run scope and provider disclosure, deliberate run start/cancel/resume, artifact open, and settings. Verify both system themes and high-DPI layouts where available. Keep package evidence separate from source UI/test evidence.
5. Record exact commands, paths, test summaries, package hash/inventory, and any visual checks that could not be performed in `docs/release/acceptance.md`.

**Commit:** `build: package Fluent desktop and record acceptance` (stage only the packaging/resource/acceptance files owned by this task).

## Final review checklist

- Compare the final diff against the approved spec; remove accidental business, API, key, database, path, shortcut, and generated-output changes.
- Check the working tree before every commit and stage only the listed files so unrelated dirty work is preserved.
- Review dependency licenses and the full lock diff; confirm no PyQt binding or unsupported package entered the lock.
- Report automated UI checks, source-visible acceptance, package inventory/startup, and packaged visible acceptance as separate results, with exact F: output paths.
