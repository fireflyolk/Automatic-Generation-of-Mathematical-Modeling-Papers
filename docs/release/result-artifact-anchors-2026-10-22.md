# Result artifact anchors — 2026-10-22

## Reported failure

Run `[运行/项目 ID 已省略]` in project
`[运行/项目 ID 已省略]` paused in paper drafting with the
chapter-key validation category. Its six completed prose batches remain cached;
results and closing are absent. The failed raw response is not persisted.
The supplied observer screenshot shows two tables and a figure using the prose
parent key `results` as their artifact anchor.

The built-in Word layout requires independent `results_table_N` and
`results_figure_N` anchors. The request called them confirmed anchors while the
batch's allowed-key lists were null. The repair classifier treated an artifact
anchor failure as a prose-section error and directed the Agent back to prose keys.

## Repair

- Generate deterministic kind-specific evidence-to-anchor maps for built-in Word.
- Resolve only the exact parent heading `results`, and only for a verified
  evidence ID of the matching artifact kind. Preserve raw response/cache data.
- Keep explicit artifact keys, invalid/unknown references, wrong kinds, extra
  fields and duplicate anchors subject to the original strict validators.
- Keep custom template mappings unchanged.
- Supply explicit artifact anchors, evidence associations, and priority over
  common recommendations only in the results batch. Its namespace is distinct
  from prose headings. Correct artifact-key errors using artifact-key guidance.
- Preserve earlier batch payloads and request hashes.

## Verification

- Before the fix: new regression file had 4 failures and 12 passes, reproducing
  the pause, incomplete request contract and incorrect repair classification.
- After the fix: workflow/unit paper/integration paper/DOCX checks passed:
  **119 passed, 15 subtests passed**. The changed response-normalization module
  reached **97.30%** coverage. This is a scoped suite, not the entire repository.
- Offline replay verified SHA-256 of all three real result artifacts, merged all
  six real cached batches, and inserted two tables and one result figure into a
  diagnostic Word file. Remaining results prose is explicitly diagnostic.
- The previous shipped engine and current engine produced identical request
  hashes for all six prior batches; only the results-batch hash changed.
- The same normalization was extracted from the newly built EXE and replayed.
  Original run cache SHA-256 remained unchanged.
- No API requests, generated-code execution, or Word page rendering were performed
  in this repair. This does not establish completed live paper generation or
  scientific correctness.

Evidence directory:
`[本机路径已省略]`

Release directory:
`[本机路径已省略]`

EXE SHA-256:
`[本地发布哈希已省略]`

## Desktop deployment

The reported process had started at 19:51 on 2026-10-22 from the old
evidence-citation release directory, despite the desktop link having previously
been updated. It was closed normally while the reported run was paused.

The actual launch directory was upgraded so existing launch entries also use
the repair:
`[本机路径已省略]`

Previous package backup:
`[本机路径已省略]`

All **7,910 deployed files** were checked against the new release inventory by
size and SHA-256. The desktop shortcut targets this upgraded directory and
retains its icon and arguments. The application was started interactively;
no workflow was automatically resumed.
