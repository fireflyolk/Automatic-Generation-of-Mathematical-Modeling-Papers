# LPAC Acceptance Follow-up — 2026-10-17

## Scope

One freshly authorized, disposable AppContainer/LPAC diagnostic was run. It used the fixed Python smoke fixture and was intended to proceed to the existing fixed boundary canaries only if Python started successfully. No model-generated or user-provided code was executed. No application source was changed for this diagnostic.

## Result

**Not accepted: LPAC-controlled execution remains blocked.** The profile was created successfully, but the runner stopped before `CreateProcessW` while `NativeJobProcess` validated the copied Python executable through `security.path_guard.native_executable`. The raised `PathPolicyError` was “只能启动本机现有的原生 .exe，不能启动脚本或命令壳。” The evidence records an empty `tests` object, so neither the Python smoke nor any later canary ran.

Before the profile run, a host-only smoke confirmed the copied `python313._pth` exposed `Lib` and `DLLs` and Python started outside LPAC. After the failed run and profile cleanup, a host-only path probe checked the same copied executable: `.exe` suffix, file existence, `MZ` header, and `native_executable()` acceptance all passed. This leaves the condition seen during the run unresolved; it is not evidence that LPAC execution passed.

## Temporary profile cleanup

- Profile: `MMPS.Acceptance.17517a53e1f54e58985301c0126c3066`
- `CreateAppContainerProfile` HRESULT: `0`
- `DeleteAppContainerProfile` HRESULT: `0`
- Harness cleanup verification: `true`
- Independent post-run check: the API-reported `Packages\<profile>\AC` folder, its package root, and the three profile registry keys were absent.
- No Python process remained under the run directory.

This was the one authorized temporary profile instance. No temporary AppContainer profile or associated registry entries remain.

## Evidence

- Run log: `F:\Codex另一个工作的地方\tmp\lpac-reauth-20261017\lpac-run.log`
- Structured evidence: `F:\Codex另一个工作的地方\tmp\lpac-reauth-20261017\harness\profile-run-577822a5c8504eb38276749dba006f2e\evidence.json`
- Host-only path probe: `F:\Codex另一个工作的地方\tmp\lpac-reauth-20261017\check_native_path.py`
- Copied runtime and diagnostic inputs: `F:\Codex另一个工作的地方\tmp\lpac-reauth-20261017\harness\profile-run-577822a5c8504eb38276749dba006f2e\`

## Acceptance boundary

Keep controlled code execution fail-closed. This run does not establish that the production runner is wired to an accepted LPAC boundary. A further profile-backed attempt requires a new explicit authorization after the executable validation discrepancy is resolved. DeepSeek/API checks and synthetic Word rendering remain separate gates; this diagnostic provides no new evidence for them.

## Instrumented retry

A second, separately authorized temporary profile was used to capture executable path state immediately before and after profile creation and at the actual `native_executable()` call. Both snapshots showed the copied `runtime\python.exe` existed, was a regular `.exe`, had the `MZ` header, and passed the path guard.

The actual call exposed a harness argument mismatch: `supervise()` expects the runtime **directory** and appends `python.exe` itself, but the smoke invocation passed `runtime\python.exe`. The rejected path was therefore `...\runtime\python.exe\python.exe`, which correctly did not exist. The product path guard behaved as designed; no LPAC process or canary ran in this attempt.

The temporary harness on F: has now been corrected to pass `runtime`, and its syntax check passes. The instrumented run created and deleted its profile successfully (`CreateAppContainerProfile` and `DeleteAppContainerProfile` HRESULT `0`); an independent check confirmed the package root and all three related registry keys are absent. The corrected harness still needs one profile-backed run before LPAC execution can be accepted.

- Instrumented log: `F:\Codex另一个工作的地方\tmp\lpac-reauth-20261017\lpac-run-instrumented.log`
- Instrumented evidence: `F:\Codex另一个工作的地方\tmp\lpac-reauth-20261017\harness\profile-run-7ffcef4320954813948dce1f9ba21745\evidence.json`
- Actual rejected argument: `F:\Codex另一个工作的地方\tmp\lpac-reauth-20261017\harness\profile-run-7ffcef4320954813948dce1f9ba21745\runtime\python.exe\python.exe`

## Corrected LPAC run

The corrected harness reached the intended path. The actual `native_executable()` call accepted the real `runtime\python.exe`; the child process was created and independently attested as AppContainer + LPAC with the expected package SID, zero capabilities, low integrity RID `4096`, and Job membership.

The fixed smoke then exited with `3221225506` (`0xC0000022`, `STATUS_ACCESS_DENIED`) after about 0.25 seconds, with empty stdout and stderr. No later canaries ran. The same copied runtime and same fixed smoke passed outside LPAC (`LPAC_SMOKE_OK`, exit `0`). This isolates the failure to LPAC access/startup behavior rather than the copied Python runtime alone.

ACL inspection found the temporary package SID had read/execute access on the runtime, standard-library files, and smoke fixture. The F: volume root and shared path ancestors (`F:\Codex另一个工作的地方`, `tmp`, this task directory, and `harness`) had no ACE for that package SID. Microsoft documents that AppContainer access depends on both the normal user/group DACL and the AppContainer SID DACL; the missing ancestor access is a plausible cause, but this run did not identify the exact denied file operation. No ACL changes were made to those existing directories.

The temporary profile was deleted successfully (`DeleteAppContainerProfile` HRESULT `0`). Independent checks found the API-reported profile folder, package root, and all three associated registry keys absent. The smoke PID was no longer running. Controlled-execution acceptance remains blocked; the ACL hypothesis is not treated as a pass.

- Corrected LPAC log: `F:\Codex另一个工作的地方\tmp\lpac-reauth-20261017\lpac-run-final-20261017.log`
- Corrected LPAC evidence: `F:\Codex另一个工作的地方\tmp\lpac-reauth-20261017\harness\profile-run-52ca17dfba274efca703879fd364a8c1\evidence.json`
- Host control: `F:\Codex另一个工作的地方\tmp\lpac-reauth-20261017\host-control-smoke.log`

A further diagnostic would require a new temporary profile and narrowly scoped, temporary traverse-only ACL entries for its package SID on the F: path ancestors, with before/after ACL snapshots and restoration. That filesystem permission change needs separate authorization.

## Temporary ACL permission preflight

The user authorized a scoped temporary ACL/Profile diagnostic. Before changing any ACL or creating another profile, the current process token was checked. `F:\` is owned by `NT AUTHORITY\SYSTEM`; the current `BUILTIN\Administrators` group is present only as “Group used for deny only,” and the `Authenticated Users` Modify grant does not include `ChangePermissions` (`WRITE_DAC`). The current process therefore cannot safely add the required ACE to the F: root.

No ACLs were changed and no additional AppContainer profile was created for this follow-up. Proceeding would require an elevated administrator process or a different approved workspace root whose ancestor DACLs can be changed and restored by the current owner.

## Authorized elevated ACL retry — stopped before Profile creation

The user authorized one temporary ACL/Profile diagnostic. A hidden elevated helper wrote a before-snapshot for the five approved F: directories, then failed to produce its ready marker within the 90-second parent timeout. The parent recorded harness_started=false; the LPAC harness and smoke/canaries did not run. The helper process was using sustained CPU after the snapshot, so it was stopped rather than left modifying ACLs without progress.

Independent checks after stopping found all five Access SDDL strings exactly equal to their saved originals and no ACE for the temporary package SID. The package directory under the user's Packages path and all three profile registry keys were absent. No profile was created and no ACL restoration was needed.

The F: root DACL contains existing inheritable ACEs. Windows documents that setting an object's DACL automatically propagates inheritable ACEs to existing child objects: https://learn.microsoft.com/en-us/windows/win32/api/aclapi/nf-aclapi-setnamedsecurityinfow. Because changing the root DACL this way can affect descendants beyond the five approved directory entries, this retry was stopped; the 0xC0000022 hypothesis remains untested. LPAC-controlled execution is still not accepted.

- ACL snapshot: F:\Codex另一个工作的地方\tmp\lpac-reauth-20261017\acl-retry-cb9c00eb62ba4dd6945c07cdc3d3f6bf\acl-before.json
- Orchestration result: F:\Codex另一个工作的地方\tmp\lpac-reauth-20261017\acl-retry-cb9c00eb62ba4dd6945c07cdc3d3f6bf\orchestration.json
- Helper and driver: F:\Codex另一个工作的地方\tmp\lpac-reauth-20261017\acl_profile_helper.ps1, F:\Codex另一个工作的地方\tmp\lpac-reauth-20261017\run_acl_profile_retry.ps1