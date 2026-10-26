# DeepSeek SSE transport budget repair, 2026-10-20

## Confirmed failure

Run `[运行/项目 ID 已省略]` paused in `problem_decomposition` with
`invalid_response`. A read-only replay of its confirmed inputs reproduced the
failure with the real DeepSeek API using `deepseek-flash`, effort `max`, and
16,384 maximum output tokens:

- HTTP 200, normal `stop` finish, and `[DONE]` received.
- 8,471 events occupied 2,550,287 wire bytes, including repeated JSON envelopes.
- Final answer: 3,689 bytes; ignored reasoning: 29,872 bytes.
- The old parser rejected the wire total at its 2 MiB check (then line 909).
- No tool fields or UTF-8 BOM occurred in this real response.

The 2 MiB nonstream JSON budget was incorrectly reused for the complete SSE
transport. The earlier short-response tests did not cover this envelope overhead.

## Change

`agents/providers/deepseek_api.py` now uses a separate 16 MiB SSE wire budget.
Nonstream JSON remains bounded to 2 MiB, final answer to 1 MiB, and a stream line
to 64 KiB. JSON structure validation, normal completion requirements, tool-call
rejection, cancellation, and per-run authorization remain enforced.

## Verification

- New regression tests first produced 2 failures and 1 pass against the old
  implementation. All three pass with the new stream budget.
- Related provider/routing tests: 49 passed and 12 subtests passed.
- Unit suite: 479 passed, 1 skipped, 628 subtests passed; 48 existing Qt
  deprecation warnings. The skipped case requires Windows junction privileges.
- Independent read-only review found no blocking issue in the final change.
- The same real `problem_decomposition` stage completed through the provider and
  workflow validators: 2,461,239 wire bytes, 4,174 output bytes, 12 subproblems,
  44.377 seconds. The original checkpoint hash remained unchanged.
- Real service verification here covers that failed stage; the complete nine-stage
  workflow and rendered Word output were not rerun as part of this repair.

## Desktop package

Output:
`[本机路径已省略]`

SHA-256:
`[本地发布哈希已省略]`

- Build exited 0 with computation runtime, package-file, and EXE-member audits.
- The bundled parser bytecode references the new 16 MiB stream budget and retains
  the other budgets. Evidence: `package-parser-proof.json` in the output task root.
- Frozen startup check exited 0; its report is `startup-check.json` in that root,
  and all managed paths are on F:.
- The existing desktop shortcut now targets this EXE; its arguments, icon, and
  description were preserved. Backup: `diagnostics\desktop-shortcut-before.lnk`.
- New EXE process 35676 opened with title `数模论文工作台` and responded normally.

Protocol evidence is under the output task's `diagnostics` directory:
`wire-probe.json`, `stage-wire-before.json`, and `stage-live-after.json`.
These contain structural metadata and byte counts, with no API Key, prompt text,
raw model answer, or reasoning text. Diagnostic scripts and build intermediates
remain under `[本机路径已省略]`.
