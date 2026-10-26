# Equation budget repair — 2026-10-22

Run `[运行/项目 ID 已省略]` paused during paper drafting with
`公式结构` after its results reply and one correction were both rejected.

## Exact failure

The actual six cached batches contain 0, 0, 4, 22, 19 and 19 equations: **64** in
total. The results reply adds 14 equations; its correction adds 3. Both fail the
old aggregate limit of 64 at `validate_part`, even though their JSON fields,
prose, and evidence can be accepted. The correction only described formula
structure and did not disclose the exhausted aggregate capacity.

## Repair

- Share a document limit of **128 equations** across aggregate validation,
  complete drafts, Word writing and DOCX QA. Keep **64** as the single-batch cap.
- Every request receives committed count, remaining capacity and the maximum
  allowed in this batch. A zero allowance requests `equations: []` explicitly.
- Reject over-budget replies with a dedicated equation-budget correction that
  points to the current allowance and retains complete chapter prose.
- Preserve exact legacy cache identities for pre-budget requests only when
  their source/evidence/schema hash matches and all current validation passes.
  Existing raw responses and their content digests are retained.
- Retain strict evidence, section, expression-size, response-size, document-size
  and resource checks. No existing formula is truncated or removed by this fix.

## Verification

The new tests initially produced 3 failures and 5 passes. The final scoped suite
of workflow, paper units and Word integrations passed **129 tests and 15 subtests**.
The new limits module reached 85.71% coverage; the measured limits/response
modules reached 95.45% combined coverage. This is not the entire repository suite.

Regression cases include 64 + 14 formulas reaching Word QA, exact old cache reuse,
an exhausted budget correcting one unwanted formula to an empty array, batch65
and total129 rejection, and a remaining budget of17 accepting17 and rejecting18.

Both actual failed results replies were replayed with the six real batches and
two SHA-256-verified CSV artifacts. Word writing and QA counted **78** and **67**
editable formula objects respectively. The same replay ran with workflow,
limits, Word writer and QA modules loaded from the new EXE.

Only closing prose was supplied as a clearly labeled diagnostic fixture. These
files are diagnostic drafts, not a completed live Agent-generated paper. No API
request, generated-code execution, or Word page rendering was performed.
The calculation artifacts currently record input auditing and model readiness,
not completed numerical simulation; no numerical results were invented.

Evidence directory:
`[本机路径已省略]`

Release directory:
`[本机路径已省略]`

EXE SHA-256:
`[本地发布哈希已省略]`

## Desktop deployment

The source release passed computation-runtime, package-file and EXE-archive
audits: 7,565 runtime files, 7,910 package files and 900 archive members.
The actual desktop launch directory was backed up and upgraded:
`[本机路径已省略]`

Previous package backup:
`[本机路径已省略]`

All 7,910 deployed files matched the audited source inventory by size and SHA-256.
The existing desktop shortcut already targets the upgraded directory; its icon
was preserved. The application was started interactively without resuming any
chargeable request. The reported run's original paper cache remains byte-identical.
