# Live Paper Preview Implementation Plan

> **For agentic workers:** Use the existing run progress event channel; preserve unrelated dirty worktree changes.

**Goal:** Show a Word-style live draft preview beside stage records while validated paper sections are assembled.

**Architecture:** The workflow emits bounded preview events only after validated batches merge. A modeless Qt dialog renders escaped plain text by section, opens automatically at paper drafting, and can be reopened from the stage-record card. It never writes or advertises a partial DOCX as final.

**Tech Stack:** Python, PySide6, existing `RunProgressEvent` and qfluentwidgets.

**Spec:** `docs/superpowers/specs/2026-10-24-live-paper-preview-design.md`

## Global Constraints

- Preserve existing dirty files and unrelated changes.
- Keep app-managed data on F:; use only bounded in-memory preview events.
- Preserve final DOCX/template writing and quality gates.
- Keep unvalidated whole-paper status visible until the full drafting stage succeeds.

## Review Focus

- A new run or retry must not show stale rejected draft text.
- Large chapters must not exceed the 64 KiB progress-event limit or stall generation.
- UI rendering must escape provider text and must not treat it as HTML.
- Paused, failed, cancelled, and completed runs must show distinct preview status.

### Task 1: Stream validated paper sections to a preview window

**Files:**
- Modify `src/mathmodel_paper_studio/workflow/progress.py` to add a dedicated preview event kind.
- Modify `src/mathmodel_paper_studio/workflow/live_engine.py` to emit bounded section snapshots after merge.
- Create `src/mathmodel_paper_studio/ui/paper_preview.py` for the non-modal read-only preview dialog.
- Modify `src/mathmodel_paper_studio/ui/run_panel.py` to place the open button by stage records, wire events, reset run state, and auto-open at paper drafting.
- Modify `src/mathmodel_paper_studio/ui/run_timeline.py` to classify the new event.

**Acceptance:** The preview displays each validated section once, updates repeated sections, marks the document incomplete until the paper and Word QA stages finish, and leaves final artifact writing unchanged. Use package build/startup and source diff inspection as this turn's allowed verification; do not send a live provider request.
