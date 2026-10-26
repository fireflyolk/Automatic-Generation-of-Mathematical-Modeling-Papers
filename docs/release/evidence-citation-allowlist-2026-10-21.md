# Evidence citation allowlist fix — 2026-10-21

## Reported failure

The paper drafting stage paused after the Agent cited source IDs (`src_…`) and
upstream stage artifact IDs (`ev_…`) that were visible in its context but were
not approved as paper evidence. The targeted retry still failed because the
prompt did not give the Agent a concrete citation allowlist. Validation also
rejects duplicate evidence IDs and a table or figure that cites an evidence
item of the wrong kind.

## Change

- Include the exact IDs from validated result evidence in
  `paper_contract.allowed_paper_evidence_ids`.
- Tell the Agent that only those IDs may appear in paper evidence lists;
  problem IDs, source IDs, and upstream-stage artifact IDs are context labels,
  not citations.
- Explicitly prohibit duplicate IDs and require tables to cite `table` evidence
  and figures to cite `figure` evidence. When no matching validated result
  exists, the Agent must leave the evidence list empty rather than invent an ID.
- Apply the same rules to the initial paper request and its evidence-correction
  retry.

## Verification

- Workflow unit suite: 43 passed, 10 subtests passed.
- `git diff --check`: passed.
- Windows package build and release audits: passed. Runtime inventory contains
  7,565 files; package inventory contains 7,910 files; executable archive audit
  contains 898 members.
- No real Agent/API request was sent during this repair. The already-paused run
  remains unchanged; resume it from the updated desktop shortcut to use this
  fix.

## Release artifact

- Executable: `[本机路径已省略]`
- SHA-256: `[本地发布哈希已省略]`
