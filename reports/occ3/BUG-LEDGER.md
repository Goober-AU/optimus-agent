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
