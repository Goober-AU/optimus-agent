# OCC-3 Bug Ledger

**Status: PARTIAL - first live-phase defect recorded (W4-A1); full consolidation still pending.** Individual defects and their fixes are recorded across campaign artifacts. A complete ledger must still enumerate every defect found during the campaign with root cause, fix evidence and regression coverage; several late-campaign items (terminal v2, private-IO O2) are recorded as unaccepted or interrupted and must be carried forward honestly. The live-phase defect found in window TS4 (W4-A1) is recorded below in the ledger format as the first consolidated entry; the remaining live-phase defects (both 2026-10-21 deployment failure modes, the CD-1 to CD-4 rehearsal defect classes, the two live build-guard defects and W3-A1) are recorded with root causes and repairs in ROLLBACK.md sections 2.1-2.7 and are listed for consolidation.

**Worktree:** `optimus-occ3-20261021` · **Date:** 2026-10-22 · **Mode:** offline documentation from existing evidence; live facts cited are exactly those recorded in the window reports.

**Citation convention.** `occ3-campaign/<path>` refers to the campaign evidence root `C:/Users/openclawuser/Optimus-Assistant/profile/workspace/occ3-campaign/`; worktree files are repository-relative (branch `feat/occ3-generic-sglang-bridge`, head `b2f508e23`; production code tip `0fb0375cc`). Full hashes inline below and in the ROLLBACK.md register. STATE.json and ROOT-HANDOFF.md are living campaign documents; their inline digests are the values at this refresh.

## Planned outline

1. Ledger format: bug id, component, symptom, root cause, fix (artifact + sha256), regression test, status (fixed/unaccepted/interrupted).
2. Deployment defects: empty Cmd argument (docker 29.x trailing blank line); mount normalisation from NetworkSettings.Mounts; HTTP-only health wait; no automatic restoration.
3. Validator defects: UTF-8/encoding defect (repro + fix logs); robustness repairs.
4. Terminal server: lifetime repairs v1/v2; red-run findings.
5. Kernel/provenance: null-presence repairs; retained negative stop-seam; created-at retention decision.
6. Protocol/scope: O2 private-IO adjudication; stale ACL pointer, cap edge, error timing candidates (review interrupted, unaccepted).
7. Carry-forward list for the live phase.

## Live-phase defects (recorded to date)

### W4-A1 - occ3 control router not mounted at serve time (window TS4 terminal anomaly)

- Bug id: W4-A1. Component: `extensions/sglang-occ3/occ3_sglang/native_install.py` (startup/install path). First observed: live window TS4 (TS=20261022T0356Z), P2.6 step-0 identity query. Severity: TERMINAL (the window rolled back by design at the native-proof deadline).
- Symptom: `POST /occ3/v1/native/runtime/identity` returns 404 with valid credentials (authentication passed; the request reached the app per the access log) and 401 keyless; six path variants including the `/v1`-prefixed form all 404; no occ3-bridge log lines anywhere in docker logs. The ordinary service gates (health, chat, tools, ingress) still PASS on the inert candidate, so a window burns the full 600 s native-proof wait before the designed rollback (ROLLBACK.md section 2.7) [E-LIVE4].
- Root cause chain (verified): the control router is mounted only inside `install_native_runtime`, invoked through `selected_serve`'s wrap of `server.serve`. A declined installation returns a clean `InstallationReceipt(False, code)`, stores it only on the app attribute inside the serving process, and serves an inert app that keeps the original api-key middleware but never mounts the occ3 control router - with zero log lines (the C26 lesson repeated at the startup layer). The build selection marker EXPERIMENTAL_STARTUP=true was present in the image; the synchronous prepare phase passes when probed with shape-faithful fakes (receipt `experimental_startup_prepared`); the plugin entry point is discoverable (`occ3_bridge` -> `occ3_sglang.plugin:load`) and `load_plugins()` is called by the launcher and the schedulers. The decline is therefore inside `install_native_runtime` at serve time. The exact receipt code is UNKNOWN: seven tiny-boot debug attempts against the candidate image failed on co-resident GPU memory limits, every one fail-safe [E-LIVE4, E-C29-DESIGN].
- Related honest correction: the control API has never been mounted in any live window. The earlier W3-A1 diagnosis ("no authorized read path") was incomplete - the sessions/enrollment surface it assumed reachable would also have 404'd - so the C28 identity route was necessary but not sufficient; the broken link is the runtime native-install gate itself (ROLLBACK.md sections 2.6-2.7) [E-LIVE4, E-RH].
- Fix: designed and in flight at this refresh (not yet committed on the branch). C29-DESIGN.md (sha256 afcd0aa6fa4bcda0de4e2557fe72b4dea90ee52b3ca7fb57a4f51064cece16a3) specifies: (1) decline-receipt observability - logging at the six startup/install receipt choke points in `native_install.py` (attempt/stage/code markers; declines at ERROR so the code survives absent logging config and reaches docker logs; no credentials, paths or exception strings logged); (2) the `occ3_identity` controller check as the FIRST service gate, so a route-absent candidate aborts at `service_initial` before the ingress reload, the native-proof checkpoint and the planned restart.
- Regression coverage: designed with the fix and to land with it - decline-logging tests (receipt code unchanged, one ERROR record with stage/code, no private strings), probe unit tests (404-with-valid-key -> `occ3_routes_absent`, shape-verified 200, malformed-body, 401/503/transport/missing-key fail-closed), a controller abort-ordering test (no ingress refresh, no checkpoint file, restart 0, recovery restored+verified) and a rehearsal fixture-contract test with a ninth scenario (`occ3-identity-absent`) proving the abort end-to-end in disposable Docker [E-C29-DESIGN].
- Status: OPEN - fix in flight; the exact receipt code is pending a boot with the new logging (window TS5 requires fresh approval).

### Other live-phase defects (recorded elsewhere; consolidation pending)

- FM-1 and FM-2 (the two 2026-10-21 deployment failure modes: empty launch-argument element; mount normalisation from `NetworkSettings.Mounts` plus the HTTP-only health wait), CD-1 to CD-4 (the four controller defect classes found in the disposable rehearsal rounds), the package-tree bound defect (live window 1), the overlayfs directory link-count collapse (window TS2) and W3-A1 (no authorized external read path for the runtime-incarnation tuple, fixed by C28 `0fb0375cc`) are recorded with verified root causes and repairs in ROLLBACK.md sections 2.1-2.7; their consolidation into this ledger's format is still pending.

## Evidence already available (offline + live-window record)

- Deployment failure analyses (both attempts, root causes and remediation requirements): occ3-campaign/dgx-inspect/OCC3-DEPLOY-FAILURE-ANALYSIS.md (sha256 084be196931164e17293ab3f60241cad8105c4c57a47b8d3894b278d945173d9); occ3-campaign/recovery-only/SECOND-DEPLOYMENT-RECOVERY.md (sha256 5e0a9c06f4c9a5f8e91322fcb20258964a753e333d5d00351f0aef68390b80a7).
- Validator encoding defect: occ3-campaign/v3-controlled-deploy/native-validator-encoding-defect-fixed.log (sha256 3d9544e142dc9bd0109961a5336d2db68e2b1bf6bb2c381c27e45602dbd49234); repro log (sha256 a2827d372915c226aa05acd4d21e9bf01a2c08d7daf5b99ce40c32c74ec4608e).
- O2 adjudication (unaccepted review findings): occ3-campaign/v3-controlled-deploy/O2-ADJUDICATION-REPORT.md (sha256 cfc00bde6352b92f182de504805f4fa392db26e0707483c423c8b2f1d908ae0a).
- Safe-stop record of interrupted/unaccepted items (terminal v2, private-IO O2): occ3-campaign/v3-controlled-deploy/USER-SAFE-STOP.json (sha256 6adcf0c498365c5cb4ec2700045b2aa292f04cf22d5648507aefc0888879fc4a).
- Campaign state review-hold lists: occ3-campaign/v3-controlled-deploy/STATE.json (sha256 e5cb73abbfb5974fd7f8001d209eceb0023644276ba8bb4b367e9134e0961a2c; living file, digest at this refresh).
- W4-A1 window record (terminal anomaly, root-cause chain and rollback evidence): occ3-campaign/v3-controlled-deploy/live-window/journal-20261022t0356z/LIVE-PHASE-EXECUTION-REPORT-20261022t0356z.json (sha256 6d7016f544c950bdfb0750ce43fc5b97423c6fa1342df216ec481eef0fe3d50e); evidence bundle occ3-campaign/v3-controlled-deploy/live-window/journal-20261022t0356z/occ3-live-evidence-20261022t0356z.tar.gz (sha256 817f340aea2d42e406b45731bbd0e8736b763af30b5bd55b2fc6739ab38424a5, 19,853 B, 53 files, remote and local copies matching).
- W4-A1 repair design (decline observability + occ3 identity gate; in flight): occ3-campaign/v3-controlled-deploy/C29-DESIGN.md (sha256 afcd0aa6fa4bcda0de4e2557fe72b4dea90ee52b3ca7fb57a4f51064cece16a3).
- W3-A1 design (the prior window's root cause; fixed by C28): occ3-campaign/v3-controlled-deploy/W3A1-DESIGN.md (sha256 9b1192e6b64d57eedc9da1011539c7d98b65230677d579e23d7c00b3c7231537).

## Evidence needed before this report can be completed (PENDING LIVE EVIDENCE)

1. Consolidation pass over the campaign artifacts to enumerate every defect with fix evidence and regression coverage (the ROLLBACK.md sections 2.1-2.7 history plus the offline review-hold lists).
2. The exact W4-A1 decline receipt code, from a boot with the C29 logging (window TS5 requires fresh approval), and the landed fix with its regression tests.
3. Further live-phase defects from any future controlled swap and benchmark runs.

## Evidence register

Citations are inline above; every cited evidence file appears there with its full path and sha256.


## Live-startup repair round (2026-10-22, branch `fix/occ3-sglang-live-startup`)

**Round context.** New worktree `optimus-occ3fix-20261022`, branch cut from `db8fec0b8` (merge of PR #189, which landed the C30/C31 follow-ups `0f40faf01`/`7fc540974` of the `feat/occ3-generic-sglang-bridge` chain). The W4-A1 root cause was verified first-hand (root session `01a16256`, 2026-10-22: read-only probes against the exact TS4 candidate image and the preserved candidate logs). The entries below follow the ledger format above; together they supersede the W4-A1 entry's `Status: OPEN` line (its fix description predates this round). The worktree advanced twice while this append was being written (two repairs landed mid-writing); every entry states its commit state and the observation time. Full chain and citations: `reports/occ3/live-startup-repair/ROOT-CAUSE.md`, `STARTUP-GATES.md`, `ROUTE-REGISTRATION.md`.

### W4-A1-RES - verified resolution: false profile rejection of the production default cache

- Bug id: W4-A1-RES (resolution record for W4-A1). Component: `extensions/sglang-occ3/occ3_sglang/latent_worker.py` (install gate, stage `radix_cache`). First observed: live window TS4 (TS=20261022T0356Z), P2.6 step-0 identity query. Severity: TERMINAL for the window; root cause now VERIFIED (offline code reads + read-only image probes).
- Root cause chain (verified): production sglang 0.5.21 initialises `tree_cache = UnifiedRadixCache` on all 8 TP ranks (candidate logs TS3/TS4); `UnifiedRadixCache` and `RadixCache` are SIBLING classes (both subclass `BasePrefixCache`; `__mro__` verified in the candidate image) and `registered_radix_cache_backends() == []`, so no flag-only switch to the classic cache exists. The install gate at latent_worker.py:72-74 (numbering at `64492e8b1`) `require(scheduler is not None and type(scheduler.tree_cache) is RadixCache, "profile")` is False on the production default -> `LatentError("profile")` on every rank -> the patched scheduler handler collapses it to `error=unsupported_profile` (patch `patches/scheduler-cache-ipc.v1.json`; image `scheduler.py:2793-2804`) -> the tokenizer-side generic handler maps it to `installation_unavailable` (native_install.py:745-747) -> clean pre-mutation rollback (decline at :698 precedes `mutation_started` :706; finally :748-779) -> `selected_serve` proceeds to `original_serve` (:1012) -> inert app with the original api-key middleware and no `/occ3/v1/native/*` routes -> the observed 404-with-valid-key / 401-keyless. The decline was FAST, not the 30 s control timeout: engine-ready 03:42:50.477Z -> Uvicorn running 03:42:50.606Z (129 ms).
- Classification: false rejection of a SUPPORTED default shape - SAFE-BUT-DIFFERENT-REPRESENTATION per the campaign spec, not a security failure. The verified UnifiedRadixCache surface (`root_node` property, `free_kv_row` with identical signature, `req_to_token_pool`) covers every `tree_cache` touchpoint the occ3 private paths use (the `mem_cache/common.py` patch: `occ3_private_match` / `occ3_release_private_kv`).
- Why the offline gates missed it: fake `class RadixCache: pass` mocks in the unit tests (tests/conftest.py:142-176 fake sglang package; tests/test_latent_scheduler_patch.py:124-149; the `64492e8b1`-era `_worker_init_env` in tests/test_latent_worker.py), rehearsals that cover docker lifecycle only (deploy/v3/rehearsal.py:27-87, pure-Python http.server fixtures), and seven tiny-boots that could not load the real model on co-resident GPU memory limits (W4-A1 entry above).
- Fix: W4-A1-R1 and W4-A1-R2 below (the two gates), plus the observability repairs OD-1/OD-2.
- Status: RESOLVED - root cause VERIFIED; both gate repairs COMMITTED (`d2ed1123b`, `99b2ebdc9`); live confirmation LIVE-PENDING (no live window has run on any repaired build).

### OD-1 - generic decline paths erased the failure category (observability defect)

- Bug id: OD-1. Component: `extensions/sglang-occ3/occ3_sglang/native_install.py` (generic decline paths). First observed: root session round 2 (2026-10-22, `occ3fix-audit/ROOT-NOTES.md` item 4/6), after C30 logging proved insufficient. Severity: HIGH (diagnostic; blocked root-cause capture on any live declined boot).
- Symptom: every non-InstallationError failure in `install_native_runtime` and `prepare_selected_native_server` collapsed into one fixed code (`installation_unavailable` / `experimental_startup_refused`); a control-path timeout, a latent worker refusal and an unrelated local exception were indistinguishable in the only live-observable channel (the C30 ERROR lines).
- Root cause (verified): the generic handlers caught `Exception` and logged `result.code` only - the failure category never reached any observable channel.
- Fix: COMMITTED `71e449783fe0711d43bc5a259258a1a9380f0e5c` (2026-10-22 07:41:03Z) - a bounded sanitised detail token (`latent_<code>`, `control_timeout`, `exception_<ClassName>`) on the two generic ERROR lines only (native_install.py:94-108, :782-787, :976-978); no exception text, payloads or paths logged; receipt taxonomy and evidence schema unchanged.
- Regression coverage: tests/test_native_install.py:581-624 (sanitised timeout/latent/generic details, taxonomy and evidence unchanged, private strings never logged).
- Status: FIXED - committed. Targeted files at this commit: 146 tests green (95 in test_native_install.py + 51 in test_native_startup_patch.py, root-session verified).

### OD-2 - worker-side bootstrap rejection invisible end to end (observability defect)

- Bug id: OD-2. Component: `extensions/sglang-occ3/occ3_sglang/latent_worker.py` plus the patched scheduler control handler. First observed: root session (2026-10-22), while tracing the TS4 decline. Severity: HIGH (diagnostic; the W4-A1 failing check produced zero observable output in TS3/TS4).
- Symptom: the scheduler patch handler collapsed every `bootstrap_worker` exception into the fixed reply error `unsupported_profile`; the tokenizer-side generic handler then logged only `installation_unavailable`; the failing runtime check never reached any observable channel.
- Root cause (verified): `handle_occ3_control`'s blanket `except Exception` (patch replacement 17/27/29; image `scheduler.py:2793-2804`) with no logging anywhere on the worker path.
- Fix: COMMITTED `64492e8b1c5df535e30e09cf3064a7584dff98a0` (2026-10-22 08:06:01Z) - fixed stage markers (`radix_cache, kv_layout, model_config, model_class, parallel_layout, speculative, embeddings, dist_init, streams, dtype`) in `WorkerRuntime.__init__` and `bootstrap_worker`, and one bounded ERROR line carrying stage plus sanitised code on decline (latent_worker.py:26-31, :121-122 at `64492e8b1`); no exception text, payloads or paths; receipt taxonomies unchanged.
- Regression coverage: tests/test_latent_worker.py at `64492e8b1` (failing stage logged, stream-stage distinction, exception class not text; 28 tests, root-session verified).
- Status: FIXED - committed.

### W4-A1-R1 - install gate rejected UnifiedRadixCache (first instance of the W4-A1 defect)

- Bug id: W4-A1-R1. Component: `extensions/sglang-occ3/occ3_sglang/latent_worker.py` (stage `radix_cache`). Root cause: W4-A1-RES.
- Fix: COMMITTED `d2ed1123be9ffe26a966a4ca2ec1fcd7282bf041` (2026-10-22 08:32:12Z) - accept exactly the two observed runtime classes: `type(scheduler.tree_cache) in (RadixCache, UnifiedRadixCache)` (latent_worker.py:72-78 at `d2ed1123b`); exact-type membership keeps subclasses and duck-typed variants declining with the existing stage/code ERROR line.
- Regression coverage: tests/test_latent_worker.py at `d2ed1123b` adds unified-passes-and-declines-only-later, init-completes-with-unified, init-completes-with-classic, subclass-still-declines (parametrised) and duck-typed-surface-still-declines; 34 collected / 34 passed in this append author's point-in-time run (~08:37Z, working tree equal to the commit).
- Status: FIXED - committed (landed during this append's writing; the round's task list had still recorded it as in flight).

### W4-A1-R2 - data-path admission gate rejected UnifiedRadixCache (second instance, same defect class)

- Bug id: W4-A1-R2. Component: `extensions/sglang-occ3/patches/scheduler-cache-ipc.v1.json` (managers/scheduler.py replacement 2; image `scheduler.py:2820`). Root cause: W4-A1-RES section "second data-path gate" - `_occ3_validate_received` opens with the same `type(self.tree_cache) is not RadixCache` check, so descriptor-carrying requests would abort `occ3_unsupported_profile` even with the install gate fixed (root-verified).
- Fix: COMMITTED `99b2ebdc99eecba17ec664d28d5749440f1f19b1` (2026-10-22 08:45:03Z) - the admission overlay accepts `type is RadixCache or type is UnifiedRadixCache`; the patch `after_sha256` and tests/test_latent_scheduler_patch.py updated.
- Regression coverage: tests/test_latent_scheduler_patch.py (admission-gate acceptance under the sibling shape; 32 passed / 15 subtests in this append author's point-in-time pre-commit run ~08:37Z; the two committed repair files together - test_latent_scheduler_patch.py + test_latent_worker.py - pass 66 tests / 15 subtests in the post-commit working tree at ~08:47Z).
- Status: FIXED - committed (observed landing during this append's writing).

### Round evidence register

- Root-verified facts: root session `01a16256`, 2026-10-22 (sibling `__mro__`, all-8-rank UnifiedRadixCache logs TS3/TS4, `registered_radix_cache_backends() == []`, 129 ms timeline, UnifiedRadixCache compatibility surface, image `scheduler.py:2820`).
- Chain and gate inventory with file:line: `reports/occ3/live-startup-repair/ROOT-CAUSE.md`, `STARTUP-GATES.md`, `ROUTE-REGISTRATION.md` (this worktree, this branch).
- Audit documents: occ3fix-shape-audit.md (sha256 `3f6f68600a546c44b81f7711f012d64887ab2ef70e007a64170fb04925702fe9`), occ3fix-decline-map.md (sha256 `ee946b8f32cf415c2319c35d13108c8625cfca4588038bd10a44f021ad5bf99e`), occ3fix-phasesplit-design.md (sha256 `af9c48b2c77ea07a6d9d65be9bf3626416a88f912b187279b51e61ef149ccd2c`), ROOT-NOTES.md (sha256 `2f784eaf761607b5ae51103a7f9910447b88d819016eb59280ca0b2c05f9c96a`), all under `C:/Users/openclawuser/Optimus-Assistant/profile/workspace/occ3fix-audit/`.
- Commits: `71e449783fe0711d43bc5a259258a1a9380f0e5c`, `64492e8b1c5df535e30e09cf3064a7584dff98a0`, `d2ed1123be9ffe26a966a4ca2ec1fcd7282bf041`, `99b2ebdc99eecba17ec664d28d5749440f1f19b1`.
- Verified suite state: extension suite at `64492e8b1` 1464 passed / 324 skipped / 77 subtests (root-session verified). The suite has not been re-run at `d2ed1123b`/`99b2ebdc9` by this append's author; the phase-split implementation (startup_state.py and controller-side edits) remains uncommitted in the working tree at 08:46Z and is not covered by any number in this entry.
