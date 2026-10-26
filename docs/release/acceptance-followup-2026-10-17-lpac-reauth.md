# LPAC Acceptance Follow-up — 2026-10-17

## Scope

One freshly authorized, disposable AppContainer/LPAC diagnostic was run. It used the fixed Python smoke fixture and was intended to proceed to the existing fixed boundary canaries only if Python started successfully. No model-generated or user-provided code was executed. No application source was changed for this diagnostic.

## Result

**Not accepted: LPAC-controlled execution remains blocked.** The profile was created successfully, but the runner stopped before `CreateProcessW` while `NativeJobProcess` validated the copied Python executable through `security.path_guard.native_executable`. The raised `PathPolicyError` was “只能启动本机现有的原生 .exe，不能启动脚本或命令壳。” The evidence records an empty `tests` object, so neither the Python smoke nor any later canary ran.

Before the profile run, a host-only smoke confirmed the copied `python313._pth` exposed `Lib` and `DLLs` and Python started outside LPAC. After the failed run and profile cleanup, a host-only path probe checked the same copied executable: `.exe` suffix, file existence, `MZ` header, and `native_executable()` acceptance all passed. This leaves the condition seen during the run unresolved; it is not evidence that LPAC execution passed.

## Temporary profile cleanup

- Profile: `MMPS.Acceptance.[运行/项目 ID 已省略]`
- `CreateAppContainerProfile` HRESULT: `0`
- `DeleteAppContainerProfile` HRESULT: `0`
- Harness cleanup verification: `true`
- Independent post-run check: the API-reported `Packages\<profile>\AC` folder, its package root, and the three profile registry keys were absent.
- No Python process remained under the run directory.

This was the one authorized temporary profile instance. No temporary AppContainer profile or associated registry entries remain.

## Evidence

- Run log: `[本机路径已省略]`
- Structured evidence: `[本机路径已省略]`
- Host-only path probe: `[本机路径已省略]`
- Copied runtime and diagnostic inputs: `[本机路径已省略]`

## Acceptance boundary

Keep controlled code execution fail-closed. This run does not establish that the production runner is wired to an accepted LPAC boundary. A further profile-backed attempt requires a new explicit authorization after the executable validation discrepancy is resolved. DeepSeek/API checks and synthetic Word rendering remain separate gates; this diagnostic provides no new evidence for them.

## Instrumented retry

A second, separately authorized temporary profile was used to capture executable path state immediately before and after profile creation and at the actual `native_executable()` call. Both snapshots showed the copied `runtime\python.exe` existed, was a regular `.exe`, had the `MZ` header, and passed the path guard.

The actual call exposed a harness argument mismatch: `supervise()` expects the runtime **directory** and appends `python.exe` itself, but the smoke invocation passed `runtime\python.exe`. The rejected path was therefore `...\runtime\python.exe\python.exe`, which correctly did not exist. The product path guard behaved as designed; no LPAC process or canary ran in this attempt.

The temporary harness on F: has now been corrected to pass `runtime`, and its syntax check passes. The instrumented run created and deleted its profile successfully (`CreateAppContainerProfile` and `DeleteAppContainerProfile` HRESULT `0`); an independent check confirmed the package root and all three related registry keys are absent. The corrected harness still needs one profile-backed run before LPAC execution can be accepted.

- Instrumented log: `[本机路径已省略]`
- Instrumented evidence: `[本机路径已省略]`
- Actual rejected argument: `[本机路径已省略]`

## Corrected LPAC run

The corrected harness reached the intended path. The actual `native_executable()` call accepted the real `runtime\python.exe`; the child process was created and independently attested as AppContainer + LPAC with the expected package SID, zero capabilities, low integrity RID `4096`, and Job membership.

The fixed smoke then exited with `3221225506` (`0xC0000022`, `STATUS_ACCESS_DENIED`) after about 0.25 seconds, with empty stdout and stderr. No later canaries ran. The same copied runtime and same fixed smoke passed outside LPAC (`LPAC_SMOKE_OK`, exit `0`). This isolates the failure to LPAC access/startup behavior rather than the copied Python runtime alone.

ACL inspection found the temporary package SID had read/execute access on the runtime, standard-library files, and smoke fixture. The F: volume root and shared path ancestors (`[本机路径已省略]`, `tmp`, this task directory, and `harness`) had no ACE for that package SID. Microsoft documents that AppContainer access depends on both the normal user/group DACL and the AppContainer SID DACL; the missing ancestor access is a plausible cause, but this run did not identify the exact denied file operation. No ACL changes were made to those existing directories.

The temporary profile was deleted successfully (`DeleteAppContainerProfile` HRESULT `0`). Independent checks found the API-reported profile folder, package root, and all three associated registry keys absent. The smoke PID was no longer running. Controlled-execution acceptance remains blocked; the ACL hypothesis is not treated as a pass.

- Corrected LPAC log: `[本机路径已省略]`
- Corrected LPAC evidence: `[本机路径已省略]`
- Host control: `[本机路径已省略]`

A further diagnostic would require a new temporary profile and narrowly scoped, temporary traverse-only ACL entries for its package SID on the F: path ancestors, with before/after ACL snapshots and restoration. That filesystem permission change needs separate authorization.

## Temporary ACL permission preflight

The user authorized a scoped temporary ACL/Profile diagnostic. Before changing any ACL or creating another profile, the current process token was checked. `[本机路径已省略]` is owned by `NT AUTHORITY\SYSTEM`; the current `BUILTIN\Administrators` group is present only as “Group used for deny only,” and the `Authenticated Users` Modify grant does not include `ChangePermissions` (`WRITE_DAC`). The current process therefore cannot safely add the required ACE to the F: root.

No ACLs were changed and no additional AppContainer profile was created for this follow-up. Proceeding would require an elevated administrator process or a different approved workspace root whose ancestor DACLs can be changed and restored by the current owner.

## Authorized elevated ACL retry — stopped before Profile creation

The user authorized one temporary ACL/Profile diagnostic. A hidden elevated helper wrote a before-snapshot for the five approved F: directories, then failed to produce its ready marker within the 90-second parent timeout. The parent recorded harness_started=false; the LPAC harness and smoke/canaries did not run. The helper process was using sustained CPU after the snapshot, so it was stopped rather than left modifying ACLs without progress.

Independent checks after stopping found all five Access SDDL strings exactly equal to their saved originals and no ACE for the temporary package SID. The package directory under the user's Packages path and all three profile registry keys were absent. No profile was created and no ACL restoration was needed.

The F: root DACL contains existing inheritable ACEs. Windows documents that setting an object's DACL automatically propagates inheritable ACEs to existing child objects: https://learn.microsoft.com/en-us/windows/win32/api/aclapi/nf-aclapi-setnamedsecurityinfow. Because changing the root DACL this way can affect descendants beyond the five approved directory entries, this retry was stopped; the 0xC0000022 hypothesis remains untested. LPAC-controlled execution is still not accepted.

- ACL snapshot: [本机路径已省略]
- Orchestration result: [本机路径已省略]
- Helper and driver: [本机路径已省略], [本机路径已省略]
## Authorized inheritance restoration and full comparison — 2026-10-18

The user authorized one restoration of the workspace root DACL auto-inherited (`AI`) flag, including Windows propagation to existing descendants, and a full before/after comparison. `SetNamedSecurityInfoW` returned `0`. The workspace root now has `D:AI`, matching the saved original control flags. Windows canonicalized ACE ordering while propagating; the root ACE multiset is unchanged. No further ACL write or AppContainer profile creation was performed in this follow-up.

The five authorized anchor paths were checked again after restoration. All five have the original control flags and ACE multisets, and the temporary AppContainer package SID appears in zero of them. Exact SDDL strings differ only where Windows reordered ACEs.

The full comparison covered all 534 manifest partitions: 523 standard snapshots and 11 native long-path snapshots. Standard entries were 1,519,820 before and 1,520,043 after; native entries were 487,103 before and 487,247 after. The 618 direct-root paths are unchanged as a set. No entries disappeared during the two snapshots; 223 standard and 144 native path entries were added. The 223 entries were under two temporary test/build trees; sampled paths are now absent. The 144 `paths-<id>` entries recorded in the mathmodel temp tree were created during the snapshot interval; sampled creation times fall between 16:31:12Z and 16:31:36Z.

ACL changes classify as follows:

- Of 588,719 exact SDDL changes in standard partitions, 579,011 are ACE ordering only. The remaining 9,708 consist of 8,912 DACL control-flag updates and 796 inherited ACE instances. The 8,912 control changes are exactly 8,821 `D:` → `D:AI` and 91 `D:P` → `D:PAI` transitions.
- The 796 ACE instances are 398 unique files, each linked under both `downloads\huawei-ict-innovation-study-space-2026-10-10\pdf-build-dependencies` and `tmp\huawei-ict-pdf-uv-cache`. Each gained the inherited ACE `(A;ID;0x1301bf;;;S-1-5-21-213357048-4285094246-4097403751-1216974287)`. This ACE already existed with inheritance flags on the original `tmp` DACL; `fsutil hardlink list` confirmed all 398 path pairs refer to the same hard-linked files. No ACE was removed and no other ACE-set change was found.
- In the 11 native long-path partitions, all 35,499 changed common-path SDDLs are ACE ordering only. The only path-set difference there is the 144 newly created mathmodel temp entries described above.

The full comparison therefore found no unexplained ACE content change. The 398 inherited ACE additions are a propagation effect from a pre-existing inheritable `tmp` ACE, and they also appear at the paired `downloads` paths because NTFS hard links share one file record. The package SID used by the earlier LPAC diagnostic remains absent from the authorized anchors. LPAC-controlled execution is still not accepted; this follow-up did not run a new profile, smoke, or canary.

- Restore result: `[本机路径已省略]`
- Five-path restore check: `[本机路径已省略]`
- Final anchor check: `[本机路径已省略]`
- Full partition comparison: `[本机路径已省略]`
- Change classification: `[本机路径已省略]`
- Hard-link verification: `[本机路径已省略]`