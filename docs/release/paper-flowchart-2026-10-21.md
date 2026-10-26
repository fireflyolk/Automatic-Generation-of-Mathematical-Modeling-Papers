# Paper drafting: optional flowchart failure, 2026-10-21

## Confirmed cause

Run `[运行/项目 ID 已省略]` completed calculation and result validation,
then paused twice in the analysis paper batch. The opening batch remained saved.
The four displayed analysis replies were valid JSON; strict local graph parsing
rejected two for more than 12 nodes and two for cyclic edges. The UI reported the
combined format/structure failure after a correction request.

## Change

- Keep strict graph parsing and rendering limits.
- Preserve validated prose when an individually unsupported optional graph is
  omitted. Add an application-owned Word review marker and manifest reason code.
- Preserve raw bounded responses and their existing cache hashes. Recompute graph
  omissions during cache recovery. Keep prose/evidence/envelope/count checks strict.
- Add drawing constraints only to batches requesting flowcharts, preserving the
  existing opening request hash.

## Evidence

- Focused tests: 7 passed, 4 subtests passed, including Word marker output,
  raw-cache recovery, supported graphs, invalid evidence, collection bounds and
  the previous large-table regression.
- New `paper_flowcharts` module coverage: 91.67% (36 statements, 3 missed).
- Offline replay of the actual latest analysis reply reused the saved opening,
  accepted the prose, recorded the cyclic-graph omission, and reached definitions.
  It used a copied cache and a fake request function; no provider request occurred.
- Original cache SHA-256 remained
  `[本地发布哈希已省略]`.
- Package dependency/runtime/protocol, file/secret and executable-member audits
  passed. The new helper is present in the executable archive.

## Desktop output

`[本机路径已省略]`

EXE SHA-256: `[本地发布哈希已省略]`.
The existing desktop shortcut targets this package; its icon and arguments were
preserved. The already open application was not restarted.

Diagnostic copies and replay script are under
`[本机路径已省略]`.

This acceptance does not assert a completed live provider continuation or rendered
final user paper. The Word check covered the omission marker in a synthetic draft.
