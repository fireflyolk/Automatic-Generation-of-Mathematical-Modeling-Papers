# DeepSeek reasoning budget repair — 2026-10-21

## Confirmed cause

Run `105d87b9b49e4785be36705de6ac0194` paused at `model_proposal`
with `output_limit`. The saved schema 2 preference selected `deepseek-flash`,
effort `max`, and a stale catalog ceiling of 16,384 tokens. The previous
adapter requested 8,192 tokens and retried once at 16,384, so both requests
could exhaust the available generation budget.

An authenticated, read-only `GET /models` returned a ceiling of 393,216 for
both currently available models. No project text was sent and no generation
request was made during diagnosis or release acceptance.

## Change

- Use the provider's documented initial budgets: 8,192 for `none`, 65,536 for
  `low`/`high`, and 131,072 for `max`. Clamp each request to the model ceiling
  frozen by the run's consent and the 131,072 application ceiling.
- On an explicit length truncation, allow one doubled-budget retry only when
  the frozen ceiling has headroom. Cancellation and revocation still prevent
  further requests. Discard all incomplete answers.
- Allow up to 128 MiB of SSE transport metadata for long reasoning streams;
  retain the 1 MiB final-answer limit, 64 KiB line limit, and JSON limits.
  The cancellable generation deadline is bounded at 1,800 seconds.
- Show the actual first-request/retry limits in the run review. Disclose the
  worst case of 32 paper requests when format and length retries both occur.
- Keep a conservative 8,192 ceiling when a model catalog has not been saved.
- After the user closed the old app, back up its saved preference and refresh
  only its verified ceiling to 131,072. Keep model `deepseek-flash` and effort
  `max`. New runs require the normal explicit fee/scope confirmation.

References: [Chat Completions](https://api-docs.deepseek.com/api/create-chat-completion/)
and [Models API](https://api-docs.deepseek.com/api/list-models/).

## Acceptance

- Regression tests first failed for the fixed 8K budgets, rejected larger
  preferences, the 32 MiB stream cap, and the stale consent disclosure.
- Final unit suite: **496 passed, 1 skipped, 628 subtests passed**. Coverage
  for the two changed DeepSeek agent modules combined: **81.03%**.
- Focused offline integration and package E2E checks: **7 passed**. The real
  DeepSeek adapter, with a fake HTTP transport, completed problem decomposition
  and model proposal and reached code generation at the reviewed 131,072
  budget. The test stops before computation and sends no network requests.
- An additional existing DeepSeek workflow fixture still fails at paper
  drafting because its canned paper JSON is outdated. The broader integration
  suite is not claimed to pass.
- Independent code review found a missing-catalog fallback that would have
  authorized the app maximum. It was reproduced, fixed, and re-reviewed.
- Build, runtime inventory, package file scan, and executable archive audit
  passed. The frozen startup check exited **0**, reporting `status=ok`,
  `frozen=true`, with all managed paths on F:.
- The existing desktop shortcut was updated and re-read. Its icon, arguments,
  and description were retained. The new visible app was launched.

Package:

Release executable: [local path omitted].

Acceptance reports and preference backup:

Acceptance reports and preference backup: [local paths omitted].

Test/build logs:

Test/build logs: [local paths omitted].

Real paid generation and the user's complete nine-stage workflow were not run
as part of this acceptance. Open the original project in the new app, start a
new run, and confirm the displayed fee/scope and 131,072-token ceiling.
The old paused window's consent cannot be expanded by changing global settings.
