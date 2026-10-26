# B078 数模论文标准整合实施计划

> **For agentic workers:** 使用 `superpowers:executing-plans` 在当前会话按任务实施；每个任务结束先检查自己的差异，再继续下一个任务。

**Goal:** 在 MathModel Paper Studio 中统一应用 B078 风格的论文内容标准和默认 Word 样式，同时允许用户选用自己的 DOCX 模板。

**Architecture:** 新增不可变、带版本的 `PaperStandard` 档案，作为 Agent 阶段提示、结构化论文草稿、内置 DOCX 默认样式及质量报告的共同来源。默认输出采用从 B078 扫描 PDF 重建的可编辑 Word 样式；用户 DOCX 模板仍控制输出版式。运行清单固化规范版本和模板来源，质量建议不因页数或可选内容不足而暂停整轮运行。

**Tech Stack:** Python 3.12, PySide6, python-docx, OOXML, 当前 PyInstaller/PowerShell 构建脚本。

**Spec:** [2026-10-19-b078-paper-standard-design.md](../specs/2026-10-19-b078-paper-standard-design.md)

## Global Constraints

- 项目、临时文件、缓存和生成输出均位于 `[本机路径已省略]`。
- `[本机路径已省略]` 只读；不复制原文、图表、数据、数值或水印。
- B078 风格是无用户模板时的默认 DOCX 样式；用户导入的 DOCX 仍可选，源模板只读。
- 约 30 页是内容规划参考，不是最低页数要求；禁止重复内容、空白页和虚构实验。
- Codex 与 DeepSeek 使用同一规范档案和相同阶段要求；供应商适配器不承载论文标准。
- 结构检查不等于数学正确性或 Word 页面验收；未渲染时标记为未进行视觉验收。
- 保留工作树已有未提交修改；不提交、不推送、不修改 C 盘桌面版本。
- 当前执行约束不新增或运行自动化测试；完成后报告代码变更、F 盘构建与视觉验收的实际证据，不把未执行项写成通过。

## File Map

- Create `src/mathmodel_paper_studio/paper/standards.py`: immutable standard profile and canonical version/hash payload.
- Modify `src/mathmodel_paper_studio/workflow/context_builder.py`: attach concise stage-specific standard guidance to both providers' shared contexts.
- Modify `src/mathmodel_paper_studio/workflow/live_engine.py`: extend the paper response contract and persist the standard/template identity in `paper-manifest.json`.
- Modify `src/mathmodel_paper_studio/paper/docx_writer.py`: render the built-in B078 style and structured paper front matter/equations; continue to fill custom DOCX templates without rewriting them.
- Modify `src/mathmodel_paper_studio/paper/docx_qa.py`: report standard coverage and quality warnings separately from hard package/mapping/evidence errors.
- Modify `src/mathmodel_paper_studio/ui/template_mapping_view.py`, `src/mathmodel_paper_studio/ui/main_window.py`, and if needed `src/mathmodel_paper_studio/ui/run_review.py`: expose B078 default versus imported DOCX and show standard/version plus warnings.
- Build outputs go to a new task-specific folder under `[本机路径已省略]`; build/cache/temp roots remain under `[本机路径已省略]`.

## Review Focus

1. **Scanned B078 source:** the default must use editable Word content and derived styles, never 50 raster page backgrounds.
2. **No custom template:** the default route must create the B078 first page and section hierarchy without requiring a DOCX upload.
3. **Custom DOCX selected:** preserve the source hash and mapped styles while applying the same content rubric.
4. **Evidence or citations absent:** identify missing evidence and pending references; do not invent values, experiments, or bibliographic entries.
5. **Short or topic-inapplicable paper:** report a page-target/content warning without pausing the workflow or adding filler.
6. **Unsupported equation syntax:** retain readable expression text and identify the conversion limitation instead of dropping the formula.

---

### Task 1: Add the canonical B078 standard profile

**Files:**
- Create `src/mathmodel_paper_studio/paper/standards.py`
- Modify `src/mathmodel_paper_studio/paper/__init__.py` only if package exports are used by existing callers.

**Interfaces:**
- Produce `PaperStandard` as a frozen dataclass with `standard_id`, `version`, `display_name`, `target_pages`, `required_sections`, `optional_sections`, `content_rules`, and `style_defaults`.
- Produce `get_default_paper_standard() -> PaperStandard` returning `b078-competition-v1`, target 30, and immutable section/rule data.
- Produce `canonical_payload() -> bytes` and a SHA-256 property over deterministic UTF-8 JSON, excluding runtime paths and timestamps.

- [x] Define the profile values once from the approved spec; separate required structural sections from optional, topic-dependent quality items.
- [x] Add stage guidance lookup that returns only the concise rules relevant to the requested stage and never copies source-paper prose.
- [x] Review profile immutability, canonical serialization order, and exact ID/version/hash stability by inspecting the implementation diff.

### Task 2: Feed the same standard to providers and paper generation

**Files:**
- Modify `src/mathmodel_paper_studio/workflow/context_builder.py`
- Modify `src/mathmodel_paper_studio/workflow/live_engine.py`

**Consumes:** `PaperStandard` from Task 1.

**Produces:** One provider-neutral context contract containing standard ID/version and stage-specific instructions; an expanded `Stage.PAPER_DRAFTING` response contract with title, abstract, keywords, sections, equations, tables, figures, and references where the source run supports them.

- [x] Add B078 guidance to problem analysis, model proposal, result interpretation, paper drafting, and DOCX review contexts; leave file scope and untrusted-data instructions unchanged.
- [x] Extend the paper JSON schema and `_paper_draft` validation to reject unknown fields, invalid section keys, unverified evidence IDs, and oversized collections.
- [x] Ensure all provider calls pass through the existing shared prompt builder; do not add a second Codex-only or DeepSeek-only standard.
- [x] Include standard ID/version/hash and template mode/source hash in `paper-manifest.json`.
- [x] Inspect the generated manifest construction and provider routing to confirm standard data is app-owned, bounded, and does not authorize new files or evidence.

### Task 3: Render the B078 built-in Word style and structured content

**Files:**
- Modify `src/mathmodel_paper_studio/paper/docx_writer.py`
- Modify `src/mathmodel_paper_studio/workflow/live_engine.py` only where it constructs `PaperDraft` values.

**Consumes:** Structured paper draft from Task 2.

**Produces:** Editable DOCX output with a B078-style first page, headings, page numbering, figure/table captions, and supported editable equations; a custom-template path that retains existing styles and source-template immutability.

- [x] Extend the immutable draft data model for title, abstract, keywords, references, and equation blocks, with bounded text and supported syntax validation.
- [x] Update `_generic_document_bytes` to produce the B078 built-in style: title/abstract/keywords on page one; Chinese main-heading hierarchy and decimal subheadings; notation table; readable body and table styles; sequential captions; footer page numbers; no source watermark.
- [x] Add a safe equation writer for supported structured expressions using editable Word math objects; retain readable text plus a warning for unsupported expressions.
- [x] Route no-template runs to the built-in B078 style; route user templates through the existing mapping and OOXML-preserving writer without changing source bytes.
- [x] Inspect the produced package-writing paths and error handling to confirm no PDF page images, hidden page padding, or stale placeholders are introduced.

### Task 4: Add standard-aware quality reporting without new page-count pauses

**Files:**
- Modify `src/mathmodel_paper_studio/paper/docx_qa.py`
- Modify `src/mathmodel_paper_studio/workflow/live_engine.py`
- Modify `src/mathmodel_paper_studio/ui/run_review.py` and/or `src/mathmodel_paper_studio/ui/main_window.py` for report presentation.

**Consumes:** Standard profile, generated DOCX, paper manifest, and current evidence IDs.

**Produces:** `docx-qa.json` with standard identity, section coverage, caption/numbering checks, evidence and reference status, quality warnings, target-page status, and visual-rendering status.

- [x] Keep corrupt packages, invalid mappings, unresolved placeholders, and invalid evidence associations as hard failures.
- [x] Report the 30-page planning target, missing optional sections, unsupported equations, incomplete citations, and weak explanation as review warnings; these findings alone must not pause the workflow.
- [x] Keep actual page count as unknown unless Word rendering provides it; set visual rendering to `not_performed` otherwise.
- [x] Update the run review UI to distinguish blocking document-integrity errors from review suggestions and show the B078 standard version.
- [x] Inspect `_qa` state transitions to ensure only defined hard failures can pause the workflow.

### Task 5: Expose B078 default and custom-template choice

**Files:**
- Modify `src/mathmodel_paper_studio/ui/template_mapping_view.py`
- Modify `src/mathmodel_paper_studio/ui/main_window.py`
- Modify `src/mathmodel_paper_studio/ui/project_wizard.py` only if the current import flow requires it.

**Consumes:** Built-in default routing from Task 3 and standard metadata from Task 1.

**Produces:** A visible choice between “B078 论文格式（默认）” and an imported custom DOCX template, with the selected mode carried into run preflight and the paper manifest.

- [x] Make B078 the default selection when a project contains no DOCX template.
- [x] Preserve the current “选择模板” flow for projects that have imported DOCX templates; choosing one changes only the style source, not the content standard.
- [x] Show the active standard name/version and selected template source before a run begins.
- [x] Inspect the new-project and existing-project navigation to confirm users can reach both choices and the current custom-template path remains usable.

### Task 6: Build the F-drive release and record acceptance boundaries

**Files:**
- No source changes unless build findings identify a required packaging inclusion.
- Create acceptance notes only under the existing `docs/release/` structure if an output artifact is produced.

**Produces:** New portable app directory and executable under `[本机路径已省略]`, with build/temp/cache paths under the corresponding F: `tmp` task directory.

- [x] Choose a fresh output directory and validate every build/runtime/temp path is on F: before invoking `scripts/build_windows.ps1`.
- [x] Build using the existing locked dependencies; run the packaged startup check and package/archive path audit, without invoking pytest.
- [x] Word page rendering was not performed; visual rendering is recorded as unverified.
- [x] Report the executable path and SHA-256, and state separately whether a real Agent request and Word page render occurred.

## Plan Self-Review

- Every spec area maps to a task: profile and stage guidance (1-2), B078 layout and equations (3), quality/failure semantics and run versioning (2 and 4), template selection (5), and F-drive build/evidence (6).
- Tasks share explicit interfaces in dependency order; Task 2 consumes the profile, Task 3 consumes the structured draft, Task 4 consumes the manifest and DOCX, and Task 5 selects the writer path.
- The six review-focus cases are assigned to the relevant writer, QA, routing, or UI inspection steps.
- No migration is required for prior run manifests; the new profile is recorded on new runs only.
- The existing worktree is already dirty. Do not stage or revert unrelated changes and do not commit as part of this task.
