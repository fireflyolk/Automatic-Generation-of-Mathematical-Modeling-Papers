# ADR 0001: Local CLI scope and authentication feasibility

Date: 2026-10-15
Status: Claude eligible only for the verified restricted Read/Write profile; Codex unavailable.

## Decision

Keep readiness scoped to the verified tool profile. Claude Code 2.1.291 is eligible only with restricted built-in Read/Write tools; Codex remains unavailable. No app-owned API key or direct API is introduced. Existing CLI authentication remains with the CLI. A version/auth-ready result alone cannot enable automatic execution.

Candidate protocol versions observed locally are Codex CLI **0.159.3** and Claude Code **2.1.291**. The preflight contract accepts exactly these version strings for further assessment; this is not a supported live-runtime range or a readiness claim. Claude 2.1.291 is above the documented restricted-mode minimum of 2.1.248. Python/dependency pinning belongs to Task 1 and has not been performed.

The application-owned capability report is distinct from either native CLI protocol. It records streaming, cancellation, filesystem confinement for the selected tool protocol, network behavior and credential inheritance independently. Reports reject missing/extra fields, duplicate keys, provider mismatches, unknown values and coercible non-booleans. The trusted harness must separately verify evidence; model text, imported inputs and self-reported CLI capabilities cannot set that flag. Unverified observations remain unavailable. Dated observations in `observed_provider_statuses()` document this decision; they are not a live health check and must not replace a fresh adapter probe of the actual executable and project scope.

## Evidence observed by the coordinating agent

Both executable resolution and non-sensitive auth-ready summaries succeeded. Credential contents and account identifiers were not read or printed by the Task 0 implementation worker.

### Codex

Selected candidate: structured `codex exec` with `--sandbox workspace-write --ephemeral --ignore-user-config --ignore-rules --skip-git-repo-check`, using disposable F: fixture paths.

The actual invocation reported **command execution was blocked by the read-only policy**. Its stream contained no command-execution event. Therefore the probe did not demonstrate inside-root execution, outside-root read/write denial, cancellation or credential separation. Do not infer a scope result from the flag name. Codex remains unavailable.

### Claude Code

Native Windows OS shell sandboxing is unsupported according to the official documentation. `--restricted` provides a separate boundary for built-in file tools and removes command/code tools by default. It does not sandbox unrestricted commands.

The coordinating agent ran the installed CLI against disposable markers with:

```text
--restricted --tools Read,Write --disallowedTools mcp__* --no-session-persistence --permission-prompts none
```

Evidence root:

```text
F:\Codex另一个工作的地方\tmp\mathmodel-paper-studio-20261015\preflight\claude-restricted-root
```

The structured stream contained one Read call and two Write calls. The outside-root Read and Write returned errors matching a boundary restriction; the outside Write created no file. Inside-root Write succeeded. No shell or MCP tool appeared. This proves only the exercised restricted Read/Write behavior. Shell/code execution remains unavailable. The coordinating agent separately started a provider response, confirmed the CLI was running, and used `taskkill /T /F`: it returned zero and the CLI exited. Temporary cancellation logs were removed. Evidence directories were the restricted fixture root and the task `preflight\cancel` directory. This verifies the tested cancellation path, without claiming that a descendant process existed in this restricted profile.

The coordinating agent subsequently tested an F: NTFS junction named `escape-junction` pointing outside the disposable project root. Restricted Read and Write through that junction were denied; the outside marker was not read and the outside write file was absent. This adds evidence for the exercised junction path under the same restricted Read/Write profile. It does not establish a general native Windows OS sandbox or prove every possible reparse-point configuration.
The capability boundary is the agent-exposed tools. With shell/code/WebFetch/MCP absent, `network_behavior=provider_transport_only` means provider transport remains available while generated tools have no network tool. It does not claim absence of provider telemetry. `credential_inheritance=cli_native_only` means the CLI retains native authentication and there is no generated shell/code/MCP child to inspect the environment; Read/Write cannot leave the checked project root. No actual credential-read attempt was performed or claimed. These conclusions rely on the exact restricted tool set and the exercised file boundary, not on a general native Windows shell sandbox.

A future adapter may select only this exact verified restricted file-tool profile; a broader profile requires new evidence. Do not broaden tool permissions merely to pass a check.

## Test evidence and limits

The owned fake CLIs produce synthetic application reports; they do not simulate provider security enforcement. Passing tests prove assessment behavior, malformed-report rejection and independent provider status only. They prove no live sandbox, authentication, cancellation, credential separation, UI, computation, DOCX or package behavior.

The pre-existing F: environment lacks pytest. No dependency was installed. Tests use Python standard-library unittest, preserving the test-first contract without downloading packages. The first test discovery failed because the source module did not exist; the same contract tests passed after implementation. Logs are under the task F: temporary directory (`task0-red.txt`, `task0-green.txt`).

No source, cache, test output or generated artifact was intentionally written to C:. Test commands set TEMP/TMP/TMPDIR to the task F: directory and disable Python bytecode. The initial F: virtual environment resolves its base standard library on C:; the final repeat uses the standalone F: Python 3.13.14 runtime so its interpreter inputs also stay on F:.

## Consequence

Task 0 can continue with Claude only under the exact restricted Read/Write profile above. Codex stays unavailable. This does not authorize generated code execution: the bounded Python runner still needs its own later isolation checks. Any change to CLI version, permitted tools, extra directories, settings or executable must trigger a fresh probe. No desktop UI, paper generation or package has been implemented or accepted in Task 0.

## Official references

- [Codex sandboxed execution example](https://developers.openai.com/cookbook/examples/codex/build_iterative_repair_loops_with_codex)
- [Claude Code CLI reference](https://code.claude.com/docs/en/cli-reference)
- [Claude Code sandboxing](https://code.claude.com/docs/en/sandboxing)
- [Claude Code restricted mode](https://code.claude.com/docs/en/restricted-mode)

Documentation and local help were checked by the coordinating agent; exact versions and probe outcomes above are dated observations.



## Task 4 implementation — version-bound parsing, production disabled (2026-10-15)

Task4 implements the JSONL data contract described in the F: Task4/5 protocol notes, bound to official source tag `rust-v0.159.3`. It does not change Task0's Codex-unavailable decision. `CodexCliAdapter.probe()` assesses only explicitly supplied executable/version display hints with authentication unverified, never runs discovery/version/login commands and never returns available. Missing and unsupported hints remain distinct Chinese health states. `capabilities()` returns the default false/unverified capabilities. `stream()` emits Chinese BLOCKED for a valid Codex request, FAILED for request/provider/version mismatch, and has no subprocess launch path. A synthetic report, fake grant or historical status cannot enable this class. A later reviewed implementation and fresh exact executable/version/configuration/argv/project/input/output proof are both required before adding a production launch path.

The standalone `CodexEventParser` is a callable for Task3 `ProcessRunner`; it accepts one thread and one turn. It validates `thread.started`, `turn.started`, `turn.completed`, `turn.failed`, top-level `error` and item events before mapping them. Agent-message snapshots emit only their new suffix; `item.completed` never completes a run. Nonfatal item errors emit generic text and do not become a turn failure. Fatal errors remain generic: the pinned Codex protocol supplies arbitrary message text, so it cannot safely identify auth/billing/rate limits from those messages. Reasoning and command/file/MCP/web/unknown item payloads are never forwarded; unexpected tool activity is BLOCKED. This conservative parser cannot certify any Codex tool profile.

Limits are 65,536 UTF-8 bytes per JSON line, 8 MiB per parser run, at most 4,096 records and 1,024 item identities, depth 16, at most 4,096 value nodes per record, bounded keys/text, unique JSON keys and finite numbers. Required fields/counts use strict types, with actual integers rather than bool token counts. Bounded optional provider metadata is tolerated; it is separate from the strict application capability-report schema. Malformed input permanently fails this parser and discards retained snapshots/resume ID. After a terminal record, further records are invalid. Task3 owns byte-chunk framing, strict EOF UTF-8/JSON handling, CRLF, final JSON without a newline, output/queue bounds, whole-Job cleanup and withholding completion until exactly one accepted terminal event plus exit 0. Cancel/timeout intent and nonzero exit override a buffered success.

No native thread ID is retained by default. Explicit parser-only opt-in can retain one in-memory opaque 1–64-character thread identifier; it grants no scope or resume permission, is cleared on failure, and the disabled adapter never opts in or stores it. No credential/account/session dump, raw stderr, argv, tool input/result, or prompt is logged by the adapter/parser. Native PATH/HOME/login stores and credentials are neither read nor modified by this implementation. The test runner reuses Task3's existing native-process environment policy only for a known F: fake script.

Evidence: 15 unit tests and 5 integration tests execute a bounded deterministic F: Python JSONL fixture through Task3's real native Job runner. They cover split UTF-8/CRLF, complete final JSON without newline, cancellation, timeout, nonzero exit, missing/conflicting/truncated results, redacted stderr and forbidden-tool mismatch. This is parser/lifecycle evidence only. There was no real Codex/provider call, disposable Codex scope probe, outside-root denial proof, auth verification, UI test, generated-code sandbox acceptance, DOCX render, packaged EXE test or Python3.11 run. All tests use the existing F: Python3.13.14 environment; the final focused log is `F:\Codex另一个工作的地方\tmp\mathmodel-paper-studio-20261015\task4-final-green-coverage.log` (20/20, 0 skips, exit0; owned module stdlib executable-line coverage183/190=96.3%). No Task0–3 full-suite rerun or branch-coverage claim is made.


## Task 5 implementation — Claude Code JSONL parser, production disabled (2026-10-15)

The Task 5 adapter is bound to the observed candidate version 2.1.291. The public argv builder selects headless print mode with stream-json, verbose partial events, and a max-turn count bounded to 1–8, while retaining exactly the Task 0 restricted Read/Write profile: --restricted, --tools Read,Write, --disallowedTools mcp__*, --no-session-persistence, and --permission-prompts none. It adds no extra directories, shell/code/WebFetch/MCP tools, or permission bypass. The argv tuple is a contract builder, not authorization or proof that this combination was accepted by the candidate executable.

ClaudeCodeCliAdapter remains unavailable. Its probe reports only supplied non-sensitive executable/version hints, never checks auth stores or launches a discovery command; authentication stays unverified and capabilities stay false. stream() never launches a provider. A synthetic test grant cannot enable the production adapter. A fresh independent exact-scope grant is absent. This task did not inspect actual CLI health, installed version, login state, account, auth store, or native settings, and did not call Claude or any model.

ClaudeEventParser accepts bounded JSONL records, not raw stderr. It rejects duplicate keys, non-finite values, unsupported versions, malformed types/sequences, conflicting or repeated terminal results, forbidden tools and oversized/nested records. Limits are 65,536 UTF-8 bytes per line, 8 MiB and 4,096 records per run, 16 nested levels, 4,096 JSON value nodes per record, and 1,024 tool/message identities. Text, Read/Write tool state and the final result are normalized without forwarding prompt, tool input/result, raw provider message, stderr, or auth payload. message_stop does not complete a run; only one valid result plus Task 3's clean exit-zero outcome can complete it. Cancellation, timeout, malformed/truncated stream, and nonzero exit remain failures or cancellation even if success text was buffered. Only explicit typed protocol data can produce distinct Chinese auth, billing, rate-limit, permission, interruption, or protocol messages. A bounded opaque session identifier is retained only under explicit parser-only opt-in and never grants resume authority.

The documented profile is still not a native Windows shell sandbox. Official sandboxing documentation says native Windows shell commands are unsupported; restricted mode constrains the built-in file-tool profile and is not evidence of general OS confinement. Therefore the production gate remains closed. Task 0's historical restricted Read/Write boundary observation does not constitute a fresh Task 5 grant, and the documented CLI flags do not prove local protocol compatibility or exact scope.

Verification used only fixed local F: fake-process scenarios through Task 3 ProcessRunner and its native Windows Job lifecycle. It did not invoke Claude, test a real login/auth state, inspect credentials, prove live outside-root denial for this new argv, or establish shell/network/credential isolation. Task 5 focused fake tests passed; details and named RED evidence are in tasks/task-5-report.md.

Official documentation URLs were checked on 2026-10-15 and are recorded in the protocol notes: https://code.claude.com/docs/en/cli-reference, https://code.claude.com/docs/en/headless, and https://code.claude.com/docs/en/sandboxing. The SDK parser and type source discussed in those notes is current main, not a version-pinned 2.1.291 transcript. This documentation does not prove the installed candidate's wire protocol or readiness.

## Provider revision (2026-10-17)

The earlier Claude eligibility decision is historical and is superseded by the user's choice to retire Claude Code and use DeepSeek API. The current API client uses a fixed HTTPS endpoint, text-only payloads, an environment-only key, and explicit per-run charge consent. The no-live-request statement in the original revision recorded its state at that time; later real API, Codex UI, v5 package, and remaining gate evidence are recorded in `docs/release/acceptance.md`.
