# Run observation window — 2026-10-21

## Change

The Run Events card now opens a reusable modeless observation window. It shows
the current stage and public assistant body/code as progress events arrive,
supports copy, clear, and follow-scroll controls, and labels all displayed
content as unvalidated. It shows a waiting state before body output, excludes
provider reasoning content, marks automatic retries as discarding the previous
draft, and marks failed or rejected drafts as discarded. The view retains only
the most recent 256 KiB and reports truncation.

DeepSeek SSE body deltas are batched and forwarded while the response is still
open. Streaming remains display-only: it does not authorize requests, change
the run scope, or treat the draft as an accepted artifact.

## Verification

- The observer unit suite passed (10 tests). Feature-focused UI, retry, progress,
  and mocked DeepSeek SSE tests passed (38 tests); the end-to-end UI flow and
  final-format-rejection regression passed (5 tests).
- The complete unit suite reported **522 passed, 1 skipped, 628 subtests
  passed**, with 51 warnings. Its aggregate coverage was **65.81%**, so the
  repository's 80% global coverage threshold did not pass. The observer module
  measured 99%, the DeepSeek provider 83%, and the run timeline 92%; the large
  pre-existing main-window and live-workflow modules account for substantial
  uncovered code.
- Independent final review confirmed that the terminal JSON/schema failure
  emits a draft-discard event before pausing; no remaining actionable issue was
  reported.
- Offline Qt screenshots were captured under
  `[本机路径已省略]`. The render environment
  lacked CJK fonts, so those images do not establish Windows visual appearance.
- Frozen package build and runtime, package-file, and archive-member audits
  passed. The runtime inventory lists 7,565 files; the package manifest lists
  7,910 files. Frozen startup check exited 0 with `status=ok`, `frozen=true`,
  and managed paths under F:.
- No live DeepSeek API request or billable generation was made. Streaming was
  exercised through a fake HTTP SSE response.

## Desktop package

`[本机路径已省略]`

SHA-256: `[本地发布哈希已省略]`

The existing desktop shortcut was updated to this executable. Its icon,
arguments, and description were retained. Before/after shortcut metadata and a
backup are in the package `diagnostics` directory. After confirming the older
process had exited, the new package opened with window title `数模论文工作台`
(PID 29900). Its launch record is `diagnostics\visible-launch.json`.

Build and test logs are under
`[本机路径已省略]`.

## Redaction and event-bound correction — 2026-10-21

The first desktop package exposed a preview-redaction mistake: the DeepSeek
observer treated the request prompt and the user's problem text as credentials,
so ordinary body content appeared mostly as `[已隐藏]`. The preview now masks
the actual request key and credential-shaped strings while leaving prompt and
problem echoes readable. Provider reasoning remains excluded.

The shared CLI redactor now bounds partial known-literal candidates by the
validated literal size (up to the 1 MiB prompt limit, plus its short marker
carry), while generic credential-marker candidates remain capped at 256
characters. Releasing long safe text is split on UTF-8 character boundaries at
the configured event byte limit. Long bodies become `TEXT` events; a tool or
status event is emitted only once.

Verification for this correction:

- Focused unit coverage: **105 passed, 246 subtests passed** across the CLI
  redactor, DeepSeek streaming, workflow progress, and observer UI suites.
- Observer integration flow: **2 passed** on an isolated rerun. The first
  combined run hit a timing race in the test's second-read synchronization;
  the isolated rerun passed.
- Independent final code review found no remaining issue in credential
  redaction, UTF-8 event chunking, status-event handling, or event accounting.
- Frozen runtime, package-file, and executable archive audits returned
  `status=ok`. Frozen startup check returned `status=ok`, `frozen=true`, with
  managed paths under F:.
- No live DeepSeek request or billable generation was made; provider streaming
  checks used fake SSE responses.

Replacement desktop package:

`[本机路径已省略]`

SHA-256: `[本地发布哈希已省略]`

The desktop shortcut at `[本机路径已省略]` now
targets this replacement. Its icon, arguments, and description were preserved.
The prior shortcut is backed up under the replacement package's `diagnostics`
directory. The replacement opened with title `数模论文工作台` (PID 46068).
Startup and shortcut records are in `release\diagnostics`; build logs are in
`[本机路径已省略]`.

## Codex observer content correction — 2026-10-21

Codex preview redaction treated the full prompt and selected problem as exact
secrets, including each short line. Common JSON fragments and words from the
generated response therefore appeared as repeated `[已隐藏]`. The observer now
preserves prompt/problem echoes while retaining credential-marker and Windows
path redaction. Blocked host-action payloads remain undisplayed.

Verification:

- Codex text-service and observer unit suites: **23 passed**.
- The observer integration file still fails its separate DeepSeek streaming
  timing assertion (`second_read_blocked` remains unset after the first read);
  that flow does not exercise Codex redaction.
- Frozen startup check: `status=ok`, `frozen=true`; its database and managed
  paths were isolated under `[本机路径已省略]`.
- No live Codex generation request was made. The package opened visibly with
  title `数模论文工作台` (PID 56328).

Replacement package:

`[本机路径已省略]`

SHA-256: `[本地发布哈希已省略]`

The desktop shortcut now targets this package. Its original icon, arguments,
description, and window style were retained; the prior link and before/after
metadata are in `release\diagnostics`. Build logs are under
`[本机路径已省略]`.
