# Paper subsection response repair — 2026-10-22

Run `[运行/项目 ID 已省略]` paused in model derivation with a
generic JSON-field-structure diagnostic. Its four preceding paper batches are
cached. Both failed replies are valid JSON; each subsection contains an extra
`subsections: []` field. All four/five child arrays are empty. Strict subsection
field checks reject this redundant field at both batch and full-draft boundaries.

## Repair

Canonicalize only an empty list-valued `subsections` field on a subsection.
Create a new dictionary and preserve the raw provider response and cached digest.
Do not alter nonempty children, null, wrong types or unrelated fields; existing
validators continue to reject them. Existing evidence validation remains active.

The trusted request now describes the two supported prose levels. A dedicated
subsection-field correction identifies the permitted keys. Schema, request-data
and batch/hash-building functions remain unchanged, so earlier caches remain
compatible and are revalidated during recovery.

## Evidence

- Regression before the fix: 2 failed, 6 passed.
- Scoped workflow/paper/Word integration checks after the fix:
  **137 passed, 15 subtests passed**. Changed response module coverage: **97.62%**.
- Both actual replies replay successfully with four real cached batches and
  four SHA-256-verified result artifacts. Their 4/5 subsection titles, paragraphs
  and evidence are preserved in Word; QA counts 18/17 editable formula objects.
- The same replay passed using application modules loaded from the new EXE.
- Actual responses remain unchanged and canonicalization is idempotent. Original
  run cache remains byte-identical. Request and hash-builder code fingerprints
  match the preceding shipped EXE.
- Independent read-only review found no HIGH/CRITICAL issue.

The offline checks above verify partial drafts through model derivation and DOCX
structure. They did not send API requests, execute generated code or render Word
pages. See the live continuation below for subsequent real-service evidence.

Evidence directory:
`[本机路径已省略]`

Release directory:
`[本机路径已省略]`

EXE SHA-256:
`[本地发布哈希已省略]`

## Live continuation and protocol repair — 2026-10-23

The first repaired EXE was deployed to the existing shortcut target after all
7,910 staged files were checked by size and SHA-256. The prior installation was
retained as `MathModelPaperStudio.backup-paper-shape-20261022` beside that target.
The run directory and SQLite database were backed up before a live continuation.
Only the original problem TXT and the two originally selected XLSX records were
registered, with their hashes and the committed data-profile artifact verified.

That continuation reused all four cached batches. It then made two real Codex
request attempts for model derivation. Both completed but contained malformed
JSON: an extra closing brace in the first, and an unclosed paragraphs array in
the correction. Neither was truncated. The strict parser correctly paused the
run. An empty-array-only normalizer cannot repair those responses.

The pinned app-server protocol supports `turn/start.outputSchema`, as documented
in [Codex App Server](https://learn.chatgpt.com/docs/app-server). Previously only
an example in the prompt described the response shape. The repair now:

- Builds a bounded, closed JSON Schema from application-owned paper examples.
  Sections, leaf subsections, equations, figures and tables keep their required
  field types and positions. Empty reference and zero-budget arrays stay empty.
- Passes the schema as a separate option only for Codex paper requests, through
  both ordinary and streaming service methods. It does not parse imported text
  or prompt content to determine policy or schema.
- Checks and snapshots the JSON value before launching the transport, then
  sends `outputSchema` on the turn. Model selection and the fresh empty-workspace
  and no-tools checks remain in force.
- Keeps all local syntax, evidence, section, equation-budget and complete-draft
  validation. It retains the existing single correction attempt. Prompt data,
  batch-request hashes and previously completed caches remain compatible.

The combined focused suite passed **254 tests and 15 subtests**; schema module
coverage was **100%**, and response/schema combined coverage was **98.86%**.
An additional accepted-size-boundary schema regression passed separately;
the final focused protocol/schema/pause-display suite passed **142 tests**.
Independent read-only review found no HIGH/CRITICAL issue. These automated checks
do not prove scientific correctness or rendered page layout.

A second, separately logged continuation used the repaired production source,
the same authorized files, and the existing `gpt-6-luna / xhigh` preference.
It sent four real Codex requests for the four uncached paper batches. All eight
paper batches and the final Word QA stage completed. Run `[运行/项目 ID 已省略]`
is `completed`, with 18 output artifacts and `next_stage: null`. The generated
DOCX is `[本机路径已省略]`.
The final QA marks the OOXML package valid, all required sections present, 22
editable linear formula objects, and 4 flowcharts. It also requires human review
of mathematics, evidence links, diagram logic and references.

The F: only Word-render preflight passed and confirmed the DOCX SHA-256. Actual
Word automation created an owned process but `Documents.Open` returned null
(`WORD_OPEN_RETURNED_NULL`), so rendering stopped before page count/export. The
QA target remains 45 pages and actual page count is unknown; the output is not
rendered-page accepted. The output and Word failure logs remain under
`[本机路径已省略]`.

The separate UI change keeps old pause errors in run history but only displays
diagnostics for currently paused or failed runs. Completed, running, created and
cancelled statuses no longer append an old diagnostic.

The initial EXE-backed attempt that failed model derivation remains separately
recorded as `live-resume-report.json`; the successful source continuation is
`structured-live/live-resume-report.json`. Both logs and the pre-resume SQLite
and run backups are under the evidence directory. An intermediate package
without the final status-message change is retained in the download directory;
the deployed package is recorded below.

## Desktop deployment — 2026-10-23

The status-corrected package was staged and verified against all **7,907**
manifest files. The six application modules changed or involved in structured
paper output and run status match their source code fingerprints inside the EXE.
Archive and package inventory audits completed. The old installation was kept
at `MathModelPaperStudio.backup-structured-output-20261023` beside the target.

The deployed EXE is at
`[本机路径已省略]`
with SHA-256
`[本地发布哈希已省略]`.
F: startup validation passed. The existing desktop shortcut directory and icon
were left intact; the application was started and responded with title
`数模论文工作台`.
