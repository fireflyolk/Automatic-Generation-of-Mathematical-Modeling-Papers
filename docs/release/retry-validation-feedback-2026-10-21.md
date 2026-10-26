# Retry validation feedback — 2026-10-21

## Issue

The paused Codex paper-writing run completed its provider request, but both the first draft and one correction attempt failed local validation. The app correctly discarded the invalid draft and paused. The raw provider response was intentionally not persisted, so the exact original validation field cannot be reconstructed from the checkpoint.

## Change

- Correction prompts now use static, categorized validation guidance and do not include the rejected raw response.
- Paper title validation failures now map to title-specific guidance instead of being misclassified as section-key failures.
- The paused run's completed checkpoints and artifacts are retained; no provider request was sent during this repair.

## Verification

- `tests/unit/workflow`: 38 passed, 8 subtests passed.
- Windows package dependency, F: path, runtime, package inventory, and executable archive audits passed.
- This does not prove acceptance by the live provider or completion of the existing paused run; resume from the updated desktop build to make that request.

## Package

`[本机路径已省略]`

SHA-256: `[本地发布哈希已省略]`
