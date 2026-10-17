# MathModel Paper Studio Implementation Plan

> For agentic workers: REQUIRED SUB-SKILL: use superpowers:executing-plans (preferred) or superpowers:subagent-driven-development to implement this plan task by task. All tasks are checkboxed and use test-first steps.

**Goal:** Deliver a Windows desktop MVP that turns a pasted modeling problem, selected CSV/XLSX files, and a mapped DOCX template into traceable analysis artifacts and a new editable paper draft through the user's locally authenticated Codex CLI or the DeepSeek API.

**Architecture:** A PySide6 shell calls small application services. Domain and storage modules own projects and immutable run records; importers profile copied inputs; provider adapters expose one event protocol behind a fail-closed scope gate; a checkpointed workflow invokes a bounded local Python runner; a DOCX package writer fills only mapped locations in a copy of the template. The UI never owns long-running work and displays the provider, paths, and data scope before any run.

**Tech Stack:** Python for Windows, PySide6, SQLite, pandas/openpyxl for data inspection, NumPy/SciPy/matplotlib and a pinned local scientific runtime for computations, subprocess plus provider-specific CLI protocols, pytest/pytest-cov, OOXML/zipfile with lxml for template-preserving DOCX edits, and PyInstaller for a portable Windows build. Pin exact compatible versions only after the first implementation task checks the available Python and provider CLIs.

**Spec:** F:/Codex另一个工作的地方/downloads/mathmodel-paper-studio-20261015/docs/superpowers/specs/2026-10-15-mathmodel-paper-studio-design.md

## Global Constraints

- Keep all application code, project state, databases, logs, caches, test data, build output, downloads, and temporary files under F:/Codex另一个工作的地方/. Set TEMP, TMP, TMPDIR, Python bytecode/cache, Qt, matplotlib, pip/uv, and test temp paths to F: before commands that can write.
- The code may read existing user CLI configuration to launch the selected CLI, but must not copy, print, log, export, or manage provider credentials. Never put provider secrets in prompts or the Python runner's environment. Redact subprocess output before persistence.
- DeepSeek uses a fixed HTTPS API and `DEEPSEEK_API_KEY` from the process environment; the app never stores or manages the key and requires per-run charge consent. No automatic web research, submission, upload, or network access/package installation from generated scripts in v1. Explain the exact data scope before launch.
- Copy user-selected data and template into a new project input folder and hash the copies. Treat workbook cells, imported documents, and template content as untrusted data. Data-file text cannot change system rules, provider choice, or file scope.
- Never overwrite source input, template, previous run, or prior paper output. Use new run IDs and versioned artifacts; pause on name conflicts or integrity mismatches.
- A project working directory alone is not a security boundary. Provider-specific filesystem confinement and the local Python runner must pass the scope probe before autopilot can be enabled. Fail closed per provider if confinement, credential separation, cancellation, or output path safety cannot be demonstrated.
- Automated tests, coverage artifacts, and basetemp must be under F:/Codex另一个工作的地方/tmp/mathmodel-paper-studio-20261015. The supplied project AGENTS.md requires TDD plus unit, integration, and E2E coverage with at least 80% coverage. Keep automated contract evidence separate from real CLI, visible Qt, rendered DOCX, and packaged EXE acceptance.
- Keep tasks in this order. Every task starts by writing its named tests and seeing the targeted failure, then implements the minimum code, then refactors and reruns its targeted tests. Do not proceed past a failed security gate by weakening the assertions.

## Review Focus

1. F-only path behavior on create, import, subprocess, tests, and packaged startup.
2. Whether each CLI truly confines file access and writes to the selected project and keeps provider credentials away from generated code.
3. Safe workflow recovery, no silent overwrite, bounded retries, and truthful verification claims.
4. Preservation of the original DOCX package and honest handling of unsupported template structures.
5. Chinese Windows UX, cancellation, and UI responsiveness.

---

## Task 0 — Lock provider and sandbox feasibility

**Files:** src/mathmodel_paper_studio/agents/preflight.py; docs/adr/0001-local-cli-scope-and-auth.md; tests/unit/agents/test_provider_preflight.py; tests/fixtures/cli/fake_codex.py; tests/fixtures/cli/fake_claude.py.

- [ ] Re-read current official CLI and security documentation, then probe only non-sensitive local facts: executable resolution, version, documented headless protocol, auth-ready summary, filesystem sandbox flags, and child-process cancellation. Do not read or display credential contents. Pin the supported CLI version range and Python version after confirming compatibility.
- [ ] Before implementation, add preflight contract tests using fake CLI executables. Assert missing CLI, unsupported version, login-required status, malformed capability report, and unconfined filesystem all produce distinct unavailable reasons; assert a healthy provider does not imply the other is healthy.
- [ ] Implement a provider capability record with separate fields for streaming, cancellation, filesystem confinement, network behavior, and credential inheritance. Build a tiny disposable F: fixture and probe attempts to read/write outside its root. Do not use the user's project inputs for this probe.
- [ ] Record a short ADR with exact supported versions, selected protocol, permission mode, probe results, and known limitations. If a provider cannot pass the gate, keep its adapter unavailable and continue with the other provider only; if neither passes, stop this plan and report the blocker.

**Verification:** targeted unit tests for the preflight contract; manual version/help/auth summary; disposable outside-root read/write and process-tree cancellation checks on Windows. Redact command output before recording it. Do not attempt any provider call using user data in this task.

**Official documentation to re-check at implementation time:** [Codex as a platform](https://developers.openai.com/blog/codex-as-a-platform); [Codex iterative repair and workspace sandbox example](https://developers.openai.com/cookbook/examples/codex/build_iterative_repair_loops_with_codex); [Claude Code CLI reference](https://docs.anthropic.com/en/docs/claude-code/cli-usage); [Claude Code Windows setup](https://docs.anthropic.com/en/docs/claude-code/getting-started).

## Task 1 — Project scaffold, paths, and domain records

**Files:** pyproject.toml; requirements.lock; README.md; src/mathmodel_paper_studio/__main__.py; src/mathmodel_paper_studio/bootstrap/paths.py; src/mathmodel_paper_studio/domain/models.py; src/mathmodel_paper_studio/storage/database.py; src/mathmodel_paper_studio/storage/repositories.py; tests/unit/bootstrap/test_paths.py; tests/unit/domain/test_models.py; tests/unit/storage/test_database.py.

- [ ] Write tests first: default root resolves to the configured F: application directory; C:, UNC, drive-relative, traversal, junction/symlink escapes, and paths outside the approved root are rejected; enums serialize stably; SQLite migrations are repeatable and use F: paths.
- [ ] Add a small AppPaths value object that discovers and validates application root, state, logs, cache, temp, projects, and test/build roots. Set write-related environment variables before importing Qt, pandas, or matplotlib. Never use the current working directory as an implicit storage root.
- [ ] Define immutable records for project, source file, run, stage status, scope manifest, provider status, and artifact evidence. Validate state transitions and path/root invariants at construction boundaries.
- [ ] Add SQLite schema versioning and repositories for project index and run summaries. Store large artifacts on disk; database rows contain IDs, hashes, and relative F: paths rather than credentials or full prompts.
- [ ] Add the initial locked dependency declaration after Task 0 confirms versions. Keep package-manager cache and build outputs on F:.

**Verification:** targeted unit tests and migration tests. Inspect actual created paths and SQLite database location; assert no test fixture writes outside the approved F: roots.

## Task 2 — New project, immutable imports, CSV/XLSX profiling

**Files:** src/mathmodel_paper_studio/projects/layout.py; src/mathmodel_paper_studio/projects/project_service.py; src/mathmodel_paper_studio/inputs/import_service.py; src/mathmodel_paper_studio/inputs/csv_reader.py; src/mathmodel_paper_studio/inputs/xlsx_reader.py; src/mathmodel_paper_studio/inputs/profiling.py; tests/unit/projects/test_project_service.py; tests/unit/inputs/test_csv_reader.py; tests/unit/inputs/test_xlsx_reader.py; tests/integration/test_project_import_flow.py.

- [ ] Write tests first: create isolated project folders; copy rather than move source files; preserve source/template hashes; reject duplicate, oversized, unsupported, malformed, and path-escaping inputs; detect UTF-8 BOM and common Chinese CSV encodings; flag formulas and merged cells without evaluating them; profile missing values, types, and basic ranges.
- [ ] Implement create_project(title, problem_text, input_paths, template_path) with a unique project ID, staged copy plus atomic finalize, rollback on partial failure, and immutable 00_inputs source copies. Save the pasted problem as a versioned UTF-8 input file.
- [ ] Implement CSV and XLSX readers with explicit size/row limits, sheet selection, formula detection, and clear Chinese import diagnostics. Never execute spreadsheet formulas or macros.
- [ ] Produce a deterministic profile JSON and data dictionary in 10_analysis; include the exact input hash and parser/transformation notes. Any cleaning step writes a new derived artifact rather than modifying source copies.
- [ ] Keep imports in an F: temp staging folder until hashes and metadata are verified; refuse a project-root symlink or reparse-point escape.

**Verification:** unit plus project-import integration tests; compare source and copied SHA-256 hashes before and after profiling. Confirm error messages identify the affected filename and next action.

## Task 3 — Scope manifest, safe process runner, and fail-closed gate

**Files:** src/mathmodel_paper_studio/security/path_guard.py; src/mathmodel_paper_studio/security/scope_manifest.py; src/mathmodel_paper_studio/security/sandbox_gate.py; src/mathmodel_paper_studio/agents/protocol.py; src/mathmodel_paper_studio/agents/process_runner.py; tests/unit/security/test_path_guard.py; tests/unit/security/test_scope_manifest.py; tests/unit/agents/test_process_runner.py; tests/integration/test_scope_gate.py.

- [ ] Write tests first for Windows path normalization, drive roots, UNC and device paths, alternate streams, traversal, symlink/junction escape, selected read/write folders, secret-redaction, timeout, cancellation, nonzero exit, malformed event, and killing descendants.
- [ ] Define AgentAdapter with probe() -> ProviderStatus, capabilities() -> AdapterCapabilities, stream(request) -> Iterable[AgentEvent], and cancel(run_id) -> None. Define typed request/event schemas and validate every provider event before forwarding it to UI or storage.
- [ ] Build a ScopeManifest that lists provider, executable/version, project root, exact readable inputs, writable output directories, run ID, stages, and the external-processing notice. Restrict provider-added directories and tool/MCP expansion.
- [ ] Construct argv as a sequence with shell execution disabled. Use a minimal inherited environment; preserve only what the CLI needs to locate its existing login and executable, set temp/cache paths to F:, and exclude secret-bearing variables from generated-code children. Never persist raw environment, auth status details, or unredacted prompts.
- [ ] Require a successful capability probe and a confirmed project-root sandbox before launch. Deny network or package-install commands from generated analysis code. If the installed CLI does not enforce the declared file boundary, return a Chinese blocked status with the specific failed capability.
- [ ] Use Windows Job Objects or a verified equivalent to cancel the entire provider and Python child process tree; enforce per-run timeout and bounded output buffering.

**Verification:** unit and integration tests use fake processes only. Probe fixture files outside the project and assert denied reads/writes. Audit logs for tokens, absolute external paths, and unredacted process environment.

## Task 4 — Codex CLI adapter

**Files:** src/mathmodel_paper_studio/agents/providers/codex_cli.py; tests/unit/agents/providers/test_codex_cli.py; tests/integration/agents/test_codex_cli_fake.py; docs/adr/0001-local-cli-scope-and-auth.md.

- [ ] Write adapter contract tests first with a fake Codex process: health/version output, authentication summary, event parsing, partial stream chunks, cancellation, timeout, nonzero exit, and sandbox capability mismatch.
- [ ] Implement only the Task 0 selected supported protocol. Compare product-oriented app-server streaming with the installed CLI's supported local execution path; use the option that keeps the user's existing CLI authentication, structured streaming, interruption, and project-only sandbox without adding a token-sharing or API-key flow.
- [ ] Map provider events to AgentEvent; distinguish completed, failed, and interrupted turns. Preserve only a provider session/thread identifier needed for resume; never store credentials.
- [ ] Pass an explicit project working directory and safe workspace permission configuration. Do not use permission-bypass flags unless Task 0 has demonstrated a separate OS-level confined workspace that makes bypass safe; otherwise Codex autopilot stays unavailable.
- [ ] Expose short, non-sensitive health text to Settings and pre-run review.

**Verification:** fake CLI contract/integration tests. Then a separate, disposable project smoke test with the installed Codex CLI, no real contest data, and an explicit scope manifest; prove outside-root read/write denial and successful cancellation before marking the adapter ready.

## Task 5 — Claude Code CLI adapter

**Files:** src/mathmodel_paper_studio/agents/providers/claude_code_cli.py; tests/unit/agents/providers/test_claude_code_cli.py; tests/integration/agents/test_claude_code_cli_fake.py; docs/adr/0001-local-cli-scope-and-auth.md.

- [ ] Write tests first for print/headless structured streaming, JSON event parsing, permissions, max turn limit, cancellation, timeout, and an unsupported Windows shell/sandbox configuration.
- [ ] Follow the current documented Windows runtime requirement and selected supported headless protocol; parse stream-json incrementally if supported by the installed version. Do not pass --add-dir or --dangerously-skip-permissions as a substitute for sandboxing.
- [ ] Restrict tools to the minimum needed by each workflow stage, disable unapproved MCP additions, and require the same project-root filesystem boundary as Codex. Do not claim a directory restriction from the working-directory flag alone.
- [ ] Normalize tool and result events, expose login readiness as a non-sensitive summary, and map permission, authentication, rate-limit, interruption, and protocol errors to distinct Chinese messages.

**Verification:** fake CLI contract/integration tests. Then a disposable F: project smoke test under the configured Windows shell; confirm denied outside-root read/write, no credential in child model tools or logs, stream cancellation, and nonzero/error handling. If any security assertion fails, leave this provider disabled.

## Task 6 — Bounded Python computation and result evidence

**Files:** src/mathmodel_paper_studio/execution/limits.py; src/mathmodel_paper_studio/execution/python_runner.py; src/mathmodel_paper_studio/execution/result_validation.py; src/mathmodel_paper_studio/paper/evidence.py; tests/unit/execution/test_python_runner.py; tests/unit/execution/test_result_validation.py; tests/integration/test_computation_artifacts.py.

- [ ] Write tests first for time/memory/output limits, cancel and descendant cleanup, nonzero exit, missing expected artifacts, NaN/Inf, row-count mismatch, deterministic seed metadata, and attempted writes outside project outputs.
- [ ] Run generated scripts with the pinned F: scientific environment, isolated working directory, no shell=True, no application credentials, no package install, no unrestricted internet, bounded runtime and output, and captured interpreter/dependency/command/exit metadata.
- [ ] Save scripts, stdout/stderr (redacted), tables, plots, and validation summaries under the current run's new 30_src, 40_results, and 50_figures directories. Never accept paths outside those directories.
- [ ] Attach evidence IDs and hashes to key numeric claims, tables, and figures. A successful process exit is not a mathematical-correctness assertion; mark unverified claims for human review.
- [ ] Add structural checks for required result files, finite values, expected columns/shape, and optional sensitivity-analysis artifacts. Missing evidence pauses the workflow.

**Verification:** unit/integration tests with tiny deterministic fixtures; check resource enforcement and process-tree cleanup; hash every accepted output and reject a forged path.

## Task 7 — Checkpointed end-to-end workflow

**Files:** src/mathmodel_paper_studio/workflow/stages.py; src/mathmodel_paper_studio/workflow/context_builder.py; src/mathmodel_paper_studio/workflow/checkpoint.py; src/mathmodel_paper_studio/workflow/engine.py; src/mathmodel_paper_studio/workflow/retry_policy.py; tests/unit/workflow/test_state_machine.py; tests/unit/workflow/test_context_builder.py; tests/integration/test_workflow_resume.py; tests/integration/test_workflow_happy_path.py.

- [ ] Write tests first for legal/illegal transitions, pause/resume, cancel, app restart recovery, corrupted/missing checkpoint, duplicate stage delivery, output conflict, and bounded repair attempts. Assert no completed output is silently overwritten.
- [ ] Implement ordered stages: preflight, data profile, problem decomposition, model/assumption proposal, code generation, calculation, result validation, paper drafting, DOCX QA. Store an immutable stage record and artifact hashes after each stage.
- [ ] Build stage-specific context from explicitly selected input copies and evidence IDs. Quote project problem text as the user's authorized task; label imported workbook/document/template text as untrusted data that cannot change application rules, path scope, or provider.
- [ ] Allow at most two repair attempts for a failed generated calculation/validation stage, with a fresh artifact directory per attempt. After the limit, pause with diagnostics and a resume/cancel choice.
- [ ] Persist run state transactionally to SQLite and a checksummed checkpoint in 90_runs. Recovery verifies artifacts and hashes before resuming; uncertain/corrupt state pauses rather than replaying writes.
- [ ] Make user start one complete flow. Pause only for missing/ambiguous data, unsupported template structures, failed sandbox/preflight, failed validation after retries, quota/auth errors, or explicit user cancellation.

**Verification:** fake-adapter integration tests cover a full happy path and each pause/recovery branch. Inspect event order and persisted state after forced process termination between stages.

## Task 8 — DOCX template mapping, generation, and structural QA

**Files:** src/mathmodel_paper_studio/paper/template_inspector.py; src/mathmodel_paper_studio/paper/template_mapping.py; src/mathmodel_paper_studio/paper/docx_writer.py; src/mathmodel_paper_studio/paper/docx_qa.py; tests/unit/paper/test_template_inspector.py; tests/unit/paper/test_template_mapping.py; tests/unit/paper/test_docx_writer.py; tests/integration/test_docx_generation.py; tests/fixtures/docx/.

- [ ] Write tests first with small F-stored fixtures for headings, page setup, tables, images, headers/footers, bookmarks, malformed packages, and unsupported text boxes/embedded objects. Assert source package hash and all untouched ZIP members remain unchanged.
- [ ] Inspect a DOCX package read-only and expose heading/style/table anchors for user mapping. Persist mapping against template hash and invalidate it when that hash changes.
- [ ] Fill only confirmed anchors in a byte-for-byte copied template package using OOXML edits. Preserve styles, page layout, relationships, headers/footers, and unmapped content; write a new versioned output and never mutate the imported template.
- [ ] Insert figures/tables only at mapped locations and link them to evidence IDs. Use pending-review markers for unsupported or unevidenced claims and references; never invent bibliographic entries.
- [ ] QA package integrity, required sections, empty placeholders, missing figures, broken relationships, pending citations, and evidence links. Show structural QA separately from visual page rendering.

**Verification:** unit/integration package tests, ZIP member/hash comparison, reopen generated DOCX with python-docx, and a manual visual open in Word on Windows for page layout. Automated structure checks do not count as rendered-page acceptance.

## Task 9 — PySide6 user flow and responsive operations

**Files:** src/mathmodel_paper_studio/ui/main_window.py; src/mathmodel_paper_studio/ui/project_wizard.py; src/mathmodel_paper_studio/ui/run_review.py; src/mathmodel_paper_studio/ui/run_panel.py; src/mathmodel_paper_studio/ui/template_mapping_view.py; src/mathmodel_paper_studio/ui/settings_view.py; src/mathmodel_paper_studio/ui/workers.py; tests/unit/ui/test_run_review_model.py; tests/integration/ui/test_project_wizard.py; tests/integration/ui/test_run_controls.py.

- [ ] Write tests first for the pre-run summary fields, start disabled if provider/scope fails, selected file scope, cancel, resume, Chinese error states, and UI worker signals.
- [ ] Implement a Chinese main window: project list, new-project wizard, paste problem, CSV/XLSX and DOCX selection, data summary, template mapping, run stage/events, cancel/resume, artifact list, structural QA summary, and open-in-Word action.
- [ ] Before start, show provider name/status, project root, exact selected readable files and writable directories, planned stages, and plain-language notice that selected information may be sent to that provider. Require a deliberate Start click; permit scope edits or Cancel.
- [ ] Run all CLI, parsing, computation, and DOCX tasks in worker threads/processes. Stream bounded log events through Qt signals, keep progress controls responsive, and make cancellation available throughout.
- [ ] Display unsupported templates, incomplete citations, unverified math, and failed checks as review items. Do not present provider success as a correctness badge.
- [ ] Default all app-managed state and runtime temp/cache paths to the F: root. If the root is missing/unwritable, show a blocking setup message instead of falling back to C:.

**Verification:** offscreen Qt unit/integration tests plus a visible Windows session. Exercise create/import, pre-run review/cancel, fake-provider completion, pause/resume, and open generated DOCX. Report visible UI evidence separately from offscreen tests.

## Task 10 — Portable build and release acceptance

**Files:** pyproject.toml; requirements.lock; packaging/mathmodel_paper_studio.spec; scripts/build_windows.ps1; scripts/verify_f_drive_outputs.py; tests/e2e/test_mvp_workflow.py; docs/release/acceptance.md; .gitignore.

- [ ] Write a fake-provider E2E test before packaging. It creates a new fixture project, imports CSV/XLSX and a DOCX template, runs every workflow stage, produces evidence-linked results and a draft, and confirms originals remain unchanged.
- [ ] Package an onedir Windows application with explicit F: build, cache, extraction-temp, and output directories. Include a startup assertion that no app-managed path resolves to C: and a clear error if F: is unavailable.
- [ ] Verify dependency lock, licenses, startup/shutdown, no credential strings in logs/package, generated project hashes, and no project artifacts outside F:. Do not bundle provider credentials or alter either CLI's native login store.
- [ ] Run automated unit/integration/E2E suite and require at least 80% coverage for src/mathmodel_paper_studio core modules. Keep pytest basetemp, coverage files, Qt cache, and build output under the task temp or project directory on F:.
- [ ] Complete separate manual gates: visible PySide6 session; Codex UI canary; DeepSeek API canary; Word-rendered output review; packaged EXE create/import/run/cancel/restart. Record pass/fail, CLI/app versions, and exact evidence paths in docs/release/acceptance.md.
- [ ] Review git diff, secret scan, path audit, package member list, and all acceptance results. Commit locally using conventional messages; do not push or publish without a new explicit request.

**Verification commands (run only during implementation; do not execute while this is a plan review):**

    $env:TEMP = 'F:/Codex另一个工作的地方/tmp/mathmodel-paper-studio-20261015'
    $env:TMP = $env:TEMP
    $env:TMPDIR = $env:TEMP
    $env:PYTHONPYCACHEPREFIX = 'F:/Codex另一个工作的地方/tmp/mathmodel-paper-studio-20261015/pycache'
    $env:UV_CACHE_DIR = 'F:/Codex另一个工作的地方/tmp/mathmodel-paper-studio-20261015/uv-cache'
    $env:PIP_CACHE_DIR = 'F:/Codex另一个工作的地方/tmp/mathmodel-paper-studio-20261015/pip-cache'
    $env:MPLCONFIGDIR = 'F:/Codex另一个工作的地方/tmp/mathmodel-paper-studio-20261015/matplotlib'
    & 'F:/Codex另一个工作的地方/downloads/mathmodel-paper-studio-20261015/.venv/Scripts/python.exe' -m pytest tests/unit tests/integration tests/e2e --basetemp 'F:/Codex另一个工作的地方/tmp/mathmodel-paper-studio-20261015/pytest' --cov=mathmodel_paper_studio --cov-report=term-missing

## Plan Self-Review

- [x] Each stage has named owned files, test-first steps, verification, and stop conditions.
- [x] Project inputs and templates are copied, hashed, immutable, and kept separate from generated artifacts.
- [x] Provider auth stays in native CLI state; provider capability failure blocks only that adapter; an unsafe provider cannot enter autopilot.
- [x] User-facing preflight lists exact provider and project scope and states that data may be externally processed.
- [x] CSV/XLSX, bounded computation, checkpoints, traceable paper evidence, template preservation, responsive UI, and packaged Windows acceptance map to the approved spec.
- [x] Unit/integration/E2E, coverage, visible UI, real CLI, rendered DOCX, and packaged EXE evidence remain separate claims.
- [x] No tests, provider calls, downloads, app runtime, or source implementation were run or created as part of drafting this plan.

## 范围变更（2026-10-17）

用户选择停用 Claude Code 并改为 DeepSeek API。原 Task 5（Claude Code CLI adapter）不再是活动实现目标，保留作历史计划；DeepSeek API 客户端和按运行授权的保护已实现并做本地假传输测试。Claude 的真实登录或请求不再继续。

截至 2026-10-16，DeepSeek 尚未装配进默认桌面工作流；这条状态属于历史记录。v5 的 Codex UI canary 结论后来被撤回：它使用独立注入服务，并把文本回复误当作完整运行证据。后续修正后，默认桌面按钮通过固定 Codex 文本 canary，但 `available=false`、`evidence_verified=false`；没有独立范围/隔离探测时，运行保持关闭。DeepSeek 固定 API canary 曾通过。受控 Python 执行仍被 STATUS_ACCESS_DENIED 阻止，用户最终 Word 模板仍未验收。当前证据和剩余门禁见 `docs/release/acceptance.md`。
