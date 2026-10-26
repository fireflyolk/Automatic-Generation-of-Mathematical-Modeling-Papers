# DeepSeek incomplete-response handling — 2026-10-20

## Change

- Start generation at 8,192 output tokens. If DeepSeek explicitly returns
  `finish_reason=length`, retry the same stage once at the smaller of twice the
  initial budget, the selected model's saved catalog ceiling, and the 32,768
  application ceiling. Cancellation or revoked run authorization prevents the
  retry; partial output remains discarded.
- Show a dedicated safe message for `content_filter`. It does not retry, since
  a content-filter result is not an output-length failure.
- Legacy schema 1 preferences contain no catalog ceiling, so they now load with
  the conservative 8,192 ceiling until the user reloads and saves the model
  catalog.

## Evidence and limits

The paused run `[运行/项目 ID 已省略]` stopped at `model_proposal`
with the safe diagnostic `incomplete_response`. Its saved checkpoint does not
contain the provider's exact `finish_reason`, so the original response cannot
be classified as `length`, `content_filter`, or another non-`stop` reason.
This release improves the confirmed `length` case and makes `content_filter`
visible; it does not claim to have confirmed the original provider-side cause.
No DeepSeek API request was made during this update.

## Verification

- Unit suite: 484 passed, 1 skipped, 628 subtests passed.
- Integration/E2E suite excluding the opt-in live Windows Sandbox test:
  154 passed, 32 failed. Failures include existing workflow fixtures that lack
  the current paper-standard context, an old sandbox runtime/source mismatch,
  and template-state expectation drift. The DeepSeek retry and diagnostic unit
  tests pass.
- Windows package build, runtime inventory, package file scan, and EXE archive
  audit passed. Frozen `--startup-check` exited 0 with `status=ok`, `frozen=true`,
  and all managed paths under F:.
- The existing desktop shortcut was updated and verified while retaining its
  icon, arguments, and description.

Package:

`[本机路径已省略]`
