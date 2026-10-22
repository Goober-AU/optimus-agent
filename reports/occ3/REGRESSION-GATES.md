# OCC-3 Regression Gates

**Worktree:** `optimus-occ3-20261021` · **Date:** 2026-10-22 · **Mode:** offline documentation from existing evidence; no live claims.

**Citation convention.** `occ3-campaign/<path>` refers to the campaign evidence root `C:/Users/openclawuser/Optimus-Assistant/profile/workspace/occ3-campaign/`; worktree files are repository-relative (branch `feat/occ3-generic-sglang-bridge`, head `ea9b9e509`). Full hashes in the evidence register. Gate definitions are quoted from the user specification's HARD ACCEPTANCE GATES section [E-SPEC]; statuses are taken from the final offline verification [E-FV], the cited evidence files, and the campaign state [E-STATE]. STATE.json and ROOT-HANDOFF.md are living campaign documents; their register hashes are the digests at this refresh.

## 1. Purpose

This report maps the specification's 15 hard acceptance gates to their current status, names the evidence that exists today, and names the evidence that would prove each gate. It encodes the two verified 2026-10-21 deployment failure modes (section 4), the four controller defect classes CD-1 to CD-4 found in the 2026-10-21 disposable rehearsals, and the live-window-1 build-guard defect as regression gates the campaign must keep passing; their history and repairs are recorded in ROLLBACK.md. Per the specification: do not claim full OCC-3 success unless all applicable gates pass; if a gate is blocked, report the actual blocker and preserve completed components [E-SPEC].

Statuses use: PASS (offline scope only; verified by frozen evidence) and PENDING-LIVE (offline scope closed, or code plus offline tests exist; the live execution has not been performed under the current approval; no live claim is made). No gate in this document is reported passed on live evidence: the only live executions to date are the four disposable rehearsal rounds (throwaway fixtures; production verified untouched in every round [E-RH1, E-RH2, E-RH3, E-RH4]) and live window 1, which aborted pre-mutation at the image-build guard with zero production impact [E-LIVE1].

## 2. Spec gate status matrix

| # | Spec gate (abbreviated) | Status | Existing evidence | Evidence that would prove it |
|---|---|---|---|---|
| 1 | Generic architecture passes without SGLang or DGX | PASS (offline scope) | Final offline verification 16/16 checks PASS (2026-10-21T23:32Z) [E-FV]: repo gate `bash scripts/check.sh` ok with 282 packaging tests run, 10 skipped; `cargo test --workspace --lib` two-stage (pi-agent-core 113, pi-ai 911, pi-coding-agent 4000 passed; the 18 parallel-run failures were classified (environment/parallelism classes per the baseline classification: containment-python, uv/PYTHONPATH, parallel races; not product regressions) and all 18 passed in the classified serial rerun [E-FV, E-STATE]); occ3 focused integration set 7/7 targets green; occ3 examples 15+8 passed; extension pytest 41+95 passed; worktree unchanged after the suites. Earlier owner chain: 401 aggregate test executions, 0 failed, 2 ignored, exit 0 in 304.9 s [E-NSO]; 16 bridge lifecycle/fallback tests with the mock connector [E-TBO] | None further for the offline scope; the residual gap (production daemon sender/receiver wiring absent [E-NIA]) is tracked under gates 7/8/13 |
| 2 | Same-model-only enforcement correct; cross-model never uses OCC-3 | PENDING-LIVE | Offline tests green in the final verification (focused set: `occ3_bridge_offline` 16, `occ3_native_inference` 17 passed) [E-FV]; negotiation ladder step 3 plus tests: same id with different checkpoint rejected; cross-model rejected; advertisement fingerprint mismatch rejected [E-NEG, E-TBO]; identity fingerprint algorithm pinned cross-language [E-NSO] | Live `model_identity()` verification of the deployed runtime against the attested tuple, and a live mismatched-pair rejection record |
| 3 | OCC-2/OCC-1/text remain functional | PENDING-LIVE | Offline suites green in the final verification: focused set `occ_comm_fidelity` 7, `occ2_comm_fidelity` 30, `occ2_selection_policy` 9 passed [E-FV]; `occ2_e2e_negotiation` 2 passed under the p4 kernel environment [E-FV] | Live regression of OCC-1/OCC-2 delivery on the deployed path after OCC-3 code ships |
| 4 | SGLang extension builds, installs, starts | PENDING-LIVE | Native package dry verification exit 0: 72 sg source files, 23 plugin files, 21 patched files, 33 resolved imports, 4,025,221 produced source bytes; explicitly applied=false, installed=false, runtime_imported=false, gpu_proof=false [E-STATE, E-DRY]. Live window 1 (TS=20261022T0030Z) attempted the real derived-image build once and aborted at the package-tree guard before any mutation (ROLLBACK.md section 2.4); repaired by commit `ea9b9e509`: separate 64 MiB binary traversal bound (metadata-only), `.py` keeps 2 MiB, 3 regression tests; extension suite 1413 passed / 324 skipped / 77 subtests [E-LIVE1, E-DD, E-NPKG] | A real derived-image build, install and server start on the DGX with `/occ3/v1/status` 200 (currently 404; no deployment under current approval) [E-FA1]; window TS2 pending the repair review ACCEPT [E-RH] |
| 5 | Extension works on the actual GLM-5.3 FP8 TP8 runtime | PENDING-LIVE | `production_swap` NOT_STARTED; live attempts under latest approval: 1 (window-1 pre-mutation abort at the build guard; the production swap itself is still unused) [E-STATE, E-LIVE1]; two historical attempts failed and were recovered (see section 4) [E-FA1, E-REC2]; disposable rehearsal round 4 complete: 8/8 scenarios, production untouched [E-RH4] | One controlled swap with candidate parity PASS, the seven supervisor gates ok, and external native proofs supplied twice [E-LIFE, E-SUP] |
| 6 | Cache safety: radix stays on; latent requests cannot contaminate ordinary cache | PENDING-LIVE | SGLang audit of the exact contamination mechanism and the salt/skip-insert mitigations [E-AUDIT]; scheduler-cache-ipc overlay and offline latent-cache tests in the extension suite [E-PATCH3]; `same_model_only` + salt-prefix `occ3-` in the capability shape [E-APIPY] | Live verification on the deployed server: normal traffic cache behaviour unchanged with the extension installed; salted-channel isolation demonstrated; contamination probe negative |
| 7 | Optimus communicates with the extension through the authenticated path | PENDING-LIVE | Connector transport/audit patterns and fail-closed control-plane contract implemented and tested offline [E-SGLANGRS, E-APIPY, E-OCC3CODEC]; window-1 phase-0 live checks resolved the native credentials via env references without printing values and got an authenticated `GET /v1/models` 200 [E-LIVE1] | A live authenticated connector health/capabilities call from Optimus to the deployed extension |
| 8 | True latent handoff: sender creates real latent state; receiver consumes it | PENDING-LIVE | Offline closure complete: terminal-v2 six pinned suites 350 passed with independent review ACCEPT [E-FV, E-STATE]; phase-suite producer re-issued on the post-SEAM1 final bytes with offline selftest passed (`complete_offline_selftest_passed`) and the SEAM1 accessor live seam closed under an accepted independent review [E-PROD, E-SEAM1]; retained-state offline proof 6/6 families [E-STATE]; full-MAX buffer/timeout/resource compatibility closed offline with one documented live-only residual [E-STATE]. First live execution remains gated by the live-phase runbook (window TS2) [E-RH] | A live capture -> transfer -> receive -> inference round trip with a consumed `x-optimus-occ3-receipt` on the receiver's actual native request [E-NIA] |
| 9 | Semantic fidelity: receiver demonstrates task-relevant information transfer | PENDING-LIVE | Offline benchmark harness exists (`benchmarks/bench_occ3.py`, `analyze.py`, task fixtures) [E-BENCH]; by design no claim without receiver proof [E-EXTREADME] | Semantic fidelity benchmark comparing OCC-3 against text baseline under matched model/task conditions |
| 10 | Empirical benchmarks: OCC-3 vs OCC-1/OCC-2 matched conditions | PENDING-LIVE | Offline benchmark harness exists but has produced no GPU results [E-BENCH]; benchmark kickoff was deferred with the window-1 live phase [E-LIVE1] | Benchmark outputs from the harness run on the deployed runtime, matched model and task conditions |
| 11 | Production performance: normal GLM inference reliable; GPU memory/throughput acceptable | PENDING-LIVE | Baseline preserved (original id/image/key, one actual rw model bind, health 200, no serving changes) [E-STATE]; window 1 verified health 200 before and after the abort with the original container untouched [E-LIVE1] | Pre/post-swap GPU memory and throughput measurements on the DGX with the extension installed |
| 12 | Existing GLM-5.3 models.json entry enables the validated connector | PENDING-LIVE (intentionally off) | `normal_occ3_enabled: false` [E-STATE]; no model entry carries an `occ3` block in this tree; capability schema requires `live-verified` validation to enable [E-PIAITYPES]; activation was deferred with the window-1 live phase [E-LIVE1] | The existing GLM-5.3 models.json entry with an enabled, live-verified `occ3` block and automatic activation where eligible; incompatible models falling back |
| 13 | Real multi-agent test: native parent/subagent workload uses OCC-3 | PENDING-LIVE | Offline examples verified in the final verification: `occ3_native_handoff` 15 passed, `occ3_native_phase_suite` 8 passed (synthetic native controller; receiver source facts absent by design) [E-FV, E-NSO] | A native Optimus parent/subagent workload on the deployed runtime completing an OCC-3 handoff with receipts |
| 14 | Failure recovery: connector/model/runtime failures safely fall back | PASS (offline + disposable DGX rehearsal); production failure injection PENDING-LIVE | v3 `recover()` design and offline/inert-Docker tests: 203 owned / 647 selected integration PASS, six independent inert-Docker probes [E-STATE, E-LW]; disposable rehearsal round 4 on the real DGX Docker 29.6.2 daemon: 8/8 scenarios PASS, every recovery verified (startup-exit RESTORED in 0.718 s, running-unready 0.109 s, controller/supervisor kills and controller hang all restored or NOOP as expected), zero residue, production untouched [E-RH4] | A live failure-injection run against the production swap with automatic restore verified |
| 15 | Final report: evidence, limitations, rollback and PR complete | PENDING-LIVE | Report set refreshed from offline evidence (this document set, 2026-10-22); PR description prepared and pinned in the campaign state [E-STATE]; live evidence and the PR itself remain outstanding | Completed report set with live evidence, the consolidated development PR, and limitations stated |

## 3. Campaign-internal gate statuses (from STATE.json)

| Campaign gate | Status (from the campaign state and cited evidence) |
|---|---|
| `baseline_preserved` | PASS: original ID/image/key, one actual rw model bind, health 200, no serving changes [E-STATE] |
| `offline_regressions` | PASS 2026-10-21: final offline verification 16/16 checks PASS (worktree freeze 143/143 hash-verified, all offline suites green, worktree unchanged after the suites) [E-FV] |
| `independent_review` | PASS: terminal-v2 independent review ACCEPT (350-suite green), O3 recheck ACCEPT, SEAM1 independent review ACCEPT; all pinned by the final verification's gate-evidence check [E-FV, E-STATE] |
| `disposable_recovery` | PASS 2026-10-21 round 4: 8/8 scenarios, every recovery verified, zero residue, production unchanged [E-RH4, E-STATE] |
| `actual_candidate_mount_parity` | PENDING-LIVE: the production artifact probe was not reached (window 1 aborted at the build guard before candidate creation; gates G1.5/G1.6 NOT_REACHED) [E-LIVE1] |
| `cpu_artifact_readability` | PENDING-LIVE: same window-1 abort point; artifact-probe verification requires a built candidate image [E-LIVE1] |
| `production_swap` | PENDING-LIVE: NOT_STARTED; window 1 aborted pre-mutation at the build guard; `live_attempts_under_latest_approval=1`; window TS2 pending [E-LIVE1, E-STATE] |
| `native_optimus_health` | PENDING-LIVE: post-swap native Optimus proof not yet run [E-STATE] |
| `live_gate8` | PENDING-LIVE: offline closure complete (terminal-v2, producer re-issue, SEAM1); first live execution gated on window TS2 [E-FV, E-PROD, E-SEAM1, E-RH] |
| `normal_occ3_enabled` | false (intentionally off; activation only if all gates pass) [E-STATE] |
| `terminal_v2_independent_review` | ACCEPT 2026-10-21 (three defects VERIFIED, 350-suite green, red/green two-gate probe) [E-STATE] |
| `fullmax_buffer_timeout_resource_compatibility` | CLOSED-OFFLINE 2026-10-21 with one documented live-only residual (buffer_source_sse has no client-side timer) [E-STATE] |
| `retained_state_offline_proof` | PASS 2026-10-21: 6/6 families, no production seams required [E-STATE] |
| `private_io_o3_repair` | ACCEPT 2026-10-21 (F2/A/B/C2/C3 VERIFIED line-level) [E-STATE] |
| `repo_baseline_poststop` | PASS no-source-regressions [E-STATE] |
| `commits_c10_c23` | PASS: the final-verification-frozen content landed as C10-C23, tip `4ae80950d` (2026-10-21T23:45Z); the window-1 repair is commit `ea9b9e509` on top [E-STATE, E-RH] |
| `b3_b5_gap_staging` + `gap1_executor_env` | PASS: 11 gaps closed, admission validated end-to-end, 13-file root-staged tree digest-verified on the DGX, credentials proven with an authenticated 200 [E-STATE] |
| `live_window_dispatch` | PASS (window 1 dispatched 2026-10-22T00:35Z); execution aborted pre-mutation at the build guard with zero production impact; `live_attempts_under_latest_approval=1` [E-STATE, E-LIVE1] |

## 4. Deployment failure-mode regression gates (mandatory encodings)

The two verified 2026-10-21 failure modes are permanent regression gates for this campaign, together with the four controller defect classes CD-1 to CD-4 found in the 2026-10-21 disposable rehearsals and the live-window-1 package-tree bound defect (all recorded with root causes and repairs in ROLLBACK.md sections 2.3 and 2.4). Each maps to a v3 mechanism that exists in code and offline tests, and to the live evidence still required.

| Gate | Failure mode (verified root cause) | v3 mechanism (code) | Offline/disposable evidence today | Live proof still required |
|---|---|---|---|---|
| FM-1: no empty launch-argument elements | docker 29.6.2 range templates append a trailing blank line even for non-empty ranges; line-based capture injected an empty-string argument; container ran `python3 "" -m sglang.launch_server ...` and exited 1 [E-FA1] | Structured-JSON inspect capture with exact arrays (never newline-split); candidate created STOPPED; `assert_candidate_parity` element-wise config diff vs the working original (every `Config` field equal except `Image`; `HostConfig`, top-level `Mounts` and effective launch identical) [E-DOCKCONF, E-LIFE] | v3 test suite (647 selected integration PASS, inert Docker) [E-LW]; disposable rehearsal round 4 exercised create + parity on the real daemon in all 8 scenarios (parity recorded in each attempt journal) [E-RH4] | PENDING-LIVE: a production candidate whose docker-recorded `Config.Cmd`/`Entrypoint` compares element-wise equal to the original's, with the parity artifact recorded |
| FM-2: mount fidelity from top-level Mounts | Controller normalised `NetworkSettings.Mounts`; docker's resolved mounts are top-level `Mounts`; both lists emptied, the `/data/neo-glm53` bind was omitted, `diff_configs()` falsely reported parity, and the server died on an invalid HF repo id [E-REC2] | `_actual_mounts()` parses raw top-level inspect `Mounts`, never `NetworkSettings.Mounts` (docstring states this); parity evidence records `actual_mounts_exact: true` and the exact mount count; artifact probe proves the candidate image can read the mounted model before mutation [E-DOCKCONF, E-LIFE] | v3 suite coverage [E-LW]; round 4 verified exact-mount parity on the real daemon in all 8 scenarios [E-RH4] | PENDING-LIVE: a production candidate with the exact original bind present and probe-verified readability |
| FM-3: exit-aware readiness | `wait_health()` only polled HTTP and never checked Docker `State.Running`/`ExitCode`; a dead candidate was treated as loading for twenty minutes [E-REC2] | `exit_aware()` re-checks `require_running()` around every verification step; `wait_ready()` raises `RunningUnready` vs `Deadline` distinctly; readiness observations journaled [E-LIFE, E-CTL] | v3 test suite [E-LW]; proven on disposable fixtures: the round-4 startup-exit scenario detected the candidate exit and restored in 0.718 s with recovery verified [E-RH4] | PENDING-LIVE: a production failure-injection run with immediate detection and automatic restore |
| FM-4: automatic restoration executes | v1 failure path printed rollback instructions and did not execute them; restart policies `no` on both containers left no self-recovery path (~41 min outage) [E-FA1] | `recover()` reconciles by immutable ID under an independent rollback budget (default 900 s); stops/renames the candidate, restores and starts the original, then re-verifies health, authenticated chat and ingress; supervisor watchdog runs rollback even if the controller is killed and never retries the deployment [E-LIFE, E-SUP, E-DEPREADME] | v3 test suite [E-LW]; disposable rehearsal complete: round 4 ran all 8 scenarios (startup crash, health timeout, controller/supervisor kills before and after mutation, controller hang) with every recovery verified and zero residue [E-RH4] | PENDING-LIVE: a live failure-injection restore against the production swap |
| FM-5: no armed auto-redeploy | After recovery, nothing may auto-redeploy: scripts disarmed, restart policies `no`, no scheduler entries [E-REC2, E-FA1] | Recovery-only tooling verifies no deployment processes remain; controller entrypoints replaced by refusing stubs on the host [E-REC2] | Verified during the 2026-10-21 recovery (audits found no matching scheduled tasks) [E-REC2]; every rehearsal round ended with exact-ID, label-verified cleanup, zero residue and no armed processes [E-RH1, E-RH2, E-RH3, E-RH4] | Re-verification immediately before any future swap window (TS2) |

## 5. Offline regression inventory (final, citable)

Final offline verification (authoritative pre-commit gate; 16/16 checks PASS; generated 2026-10-21T23:32:03Z) [E-FV]:

- Worktree frozen against the freeze manifest: 143/143 entries hash-verified, 0 new, 0 missing, 0 content drift, at pre-commit head `bd9d40c1`; the frozen content landed as commits C10-C23 (tip `4ae80950d`) [E-FV, E-STATE].
- Gate-evidence sha256 pins from the accepted reports verified (7 gate evidence files, including the terminal-v2 review and the CD3 offline repair result) [E-FV].
- Rehearsal round 4 pinned: result PASS, 8/8 scenarios, `production_touched=false`, residue check PASS, 24 evidence pairs checked [E-FV].
- Phase-suite producer result pinned: delivered, `complete_offline_selftest_passed` [E-FV, E-PROD].
- Accepted-review frozen worktree sources byte-identical (8 files) and gate reports' internal evidence references (logs, scripts) verified [E-FV].
- `bash scripts/check.sh` (repo gate): 282 packaging tests ran, ok, 10 skipped [E-FV].
- `cargo test --workspace --lib` two-stage: pi-agent-core 113 passed / 1 ignored; pi-ai 911 passed / 6 ignored; pi-coding-agent 4000 passed; the 18 parallel-run failures were classified (environment/parallelism classes per the baseline classification: containment-python, uv/PYTHONPATH, parallel races; not product regressions) and all 18 passed in the classified serial rerun [E-FV, E-STATE].
- occ3 focused integration set (7 targets): `occ2_comm_fidelity` 30, `occ2_selection_policy` 9, `occ3_bridge_offline` 16, `occ3_native_inference` 17, `occ3_retained_state_proof` 10, `occ3_source_handoff` 11, `occ_comm_fidelity` 7; all passed [E-FV].
- occ3 examples under test: `occ3_native_handoff` 15 passed; `occ3_native_phase_suite` 8 passed [E-FV].
- Extension python tests: `tests/test_api.py` 41 passed; `tests/test_capture.py` 95 passed [E-FV].
- Terminal six pinned suites: 350 passed, 0 failed, 0 skipped [E-FV].
- `prime-agent-runtime` `test_repl` under the p4 kernel environment: 110 ran, ok, 3 skipped [E-FV].
- `occ2_e2e_negotiation` under the p4 kernel environment: 2 passed [E-FV].
- Worktree unchanged after the offline suites: 0 new, 0 missing, 0 content drift [E-FV].

Post-repair extension suite (commit `ea9b9e509`, window-1 abort repair): 1413 passed / 324 skipped / 77 subtests; `git diff --check` ok; cargo workspace check ok; 282 unit tests ok / 10 skipped; layout ok [E-DD, E-RH].

Known caveats recorded by the campaign: the earlier owner-chain counts are aggregate executions, not unique test counts [E-NSO]; the repair commit's independent review was still pending at dispatch time and window TS2 is GO-gated on its ACCEPT [E-RH]; a passing offline gate is not live clearance [E-EXTREADME].

## 6. PENDING LIVE EVIDENCE (consolidated)

1. Everything in the status matrix marked PENDING-LIVE requires live execution on the DGX under the current approval before any gate may be reported as passed. Nothing live has passed: the only live attempts so far are the four disposable rehearsal rounds (production untouched every round) and live window 1, which aborted pre-mutation at the image-build guard with zero production impact [E-RH1, E-RH2, E-RH3, E-RH4, E-LIVE1].
2. Window TS2 is GO-gated on the independent repair review ACCEPT for commit `ea9b9e509` (review running at dispatch time) [E-RH].
3. Deployment under the current authorization requires offline regressions and disposable recovery tests to PASS without disturbing production GLM before exactly one controlled swap [E-AUTH]; both preconditions are now met [E-FV, E-RH4] and the single authorized production swap remains unused.
4. Live-only items deferred by the window-1 executor: optimus end-to-end response, native subagent gate 13, `models.json` activation, benchmark kickoff, arm-C baseline decision, fence retirement, retained-artifact deletion, final reports and PR [E-LIVE1].

## Evidence register

| Id | Path | sha256 |
|---|---|---|
| E-SPEC | occ3-campaign/v3-controlled-deploy/ORIGINAL-USER-SPECIFICATION.md | 9f731ebd2e15cc9ffc6dbe42a7c33c9e44e0cd8149dba76f39a8828a5df629e0 |
| E-STATE | occ3-campaign/v3-controlled-deploy/STATE.json | 859100d681be04a2229db65785a887f334f21f4b3031354860aa409804af837a (living file; digest at this refresh, field-level reads only) |
| E-FV | occ3-campaign/v3-controlled-deploy/FINAL-VERIFY-RESULT.json | 8d54c074ee3120fae92049dd5f07fd3dd60ed0bba7aa0634f5577ac9d21aad73 |
| E-RH1 | occ3-campaign/v3-controlled-deploy/REHEARSAL-RESULT.json | 40698dcad02f1fa4c35f3605a900f8bf84aae3680412fa2c3d4e5b2ffc362dca |
| E-RH2 | occ3-campaign/v3-controlled-deploy/REHEARSAL-RESULT-2.json | 00269632eee9635808d75cc2d96e576561a4f0c56d1c806fe8f25b33620e7050 |
| E-RH3 | occ3-campaign/v3-controlled-deploy/REHEARSAL-RESULT-3.json | 7f2784cb6473ed2dc8aaa5e6e96bf43983ac22646a82a86914c6f2499a3d59bc |
| E-RH4 | occ3-campaign/v3-controlled-deploy/REHEARSAL-RESULT-4.json | 1605fca0590d099b8d4b44301e5be3feb11ce90050ecb6ad9342c9127dbe910a |
| E-LIVE1 | occ3-campaign/v3-controlled-deploy/LIVE-PHASE-EXECUTION-REPORT.json | ce877c412649c488d8c72290cd47d7492863e3db4640f0c176bfd54d76219be8 |
| E-PROD | occ3-campaign/v3-controlled-deploy/PRODUCER-RESULT.json | 699901dd9bb557915d2a0549d02d2d2327796e5dd12c0b39347ee93fa431ae73 |
| E-SEAM1 | occ3-campaign/v3-controlled-deploy/SEAM1-INDEPENDENT-REVIEW.md | 9a6b2fe9bac1a8c644d7ea9c8c5d8a9a15bb298f46290d2438f1dd07af630d94 |
| E-RH | occ3-campaign/v3-controlled-deploy/ROOT-HANDOFF.md | 952041c5f59b072a2f4a68387a9e3f29144fe983a05aa15a60b5d6bfbbe6810b (living log; digest at this refresh) |
| E-DD | occ3-campaign/v3-controlled-deploy/DISPATCH-DIGESTS.json | 1f9bd5e8dcfc813c5f980df5e4c872b5414284a956077e24430a7252cfe648e3 |
| E-NSO | occ3-campaign/v3-controlled-deploy/native-source-offline-result.json | d845f80816706122f54a29a51e177e38e4b0f2a9ec711ad258e96e4bbfe9da79 |
| E-ROOTCHECK | occ3-campaign/v3-controlled-deploy/root-native-final-check.txt | 1a592bb82bc5f8b92b5f87c9bf9f4b90a04d20d1c723300b722469c814a8d447 |
| E-DRY | occ3-campaign/v3-controlled-deploy/root-native-package-dry-verify.json | 151494f9619fdf21a957ac74792a8fbbed0696b2d71b71cbfc69c118a4132d51 |
| E-FA1 | occ3-campaign/dgx-inspect/OCC3-DEPLOY-FAILURE-ANALYSIS.md | 084be196931164e17293ab3f60241cad8105c4c57a47b8d3894b278d945173d9 |
| E-REC2 | occ3-campaign/recovery-only/SECOND-DEPLOYMENT-RECOVERY.md | 5e0a9c06f4c9a5f8e91322fcb20258964a753e333d5d00351f0aef68390b80a7 |
| E-NIA | occ3-campaign/v3-controlled-deploy/NATIVE-INFERENCE-ARCHITECTURE.md | 0afb026dbf2d6329fd4c50b03265bd46a28b647c12881b063c42f2ea64087651 |
| E-AUDIT | occ3-campaign/research/sglang-audit.md | e48d08488fa65f9bc664f5aba30cedd6f3dd38ec3bcbfbebe14e4878efdb3049 |
| E-LW | occ3-campaign/v3-controlled-deploy/lifecycle-worker-l09-l10-v4-result.json | f0a6808c70e2b19e9b56ef6a357d1597b8b5b00d2475968327caea4459cd8f29 |
| E-AUTH | occ3-campaign/v3-controlled-deploy/AUTHORIZATION.txt | aa13cd2c99822f17cb142b6e1c04158fd0728a421244a64175ac53afc8d82c03 |
| E-TBO | crates/pi-coding-agent/tests/occ3_bridge_offline.rs | acaa9b8b5a3648c124953b71c07e26efaf8bab2141febbf78bce2868151428bf |
| E-NEG | crates/pi-coding-agent/src/core/occ/occ3/negotiation.rs | 1956cd91398fa334cf553b4de0cbbfb285b2aca3010e0915c7c856b9cc83a8e7 |
| E-PIAITYPES | crates/pi-ai/src/types.rs | 4c606e40ce7ffdb2de09a3a272f84b64d39e81a843f36485571ad216c247219e |
| E-SGLANGRS | crates/pi-coding-agent/src/core/occ3_connectors/sglang.rs | 64d8cb468687ec1c084d0aab02e89ff768eca18b1bb201dd0b3326755edd9250 |
| E-APIPY | extensions/sglang-occ3/occ3_sglang/api.py | e45b3b77c6fbff798f67815ef5b992066e1f0900d114632bd28f89afb146a9b1 |
| E-OCC3CODEC | crates/pi-coding-agent/src/core/occ/occ3/codec.rs | 344d9f1fad53b77cb3846a13b5c2fafd399f6941c50c1fb1fd6b0a7a7717ae11 |
| E-EXTREADME | extensions/sglang-occ3/README.md | 3fe79fadd0a9e5e55cac27e1feca1ee7431d71ec53579f141a920ac9bd454702 |
| E-BENCH | extensions/sglang-occ3/benchmarks/bench_occ3.py | ec665d665c601db38a083145984060a0b2a8c6f7b2728c0dacd43c7817918437 |
| E-PATCH3 | extensions/sglang-occ3/patches/scheduler-cache-ipc.v1.json | a80d0051754e2a69dbe88eb642871c871cf1707d15af9a2b4a5e6cc6595865c6 |
| E-DOCKCONF | extensions/sglang-occ3/deploy/v3/docker_config.py | faebe73d868bdca228b388125ea3b13d94d50ce0ef42102870950a9facd42304 |
| E-NPKG | extensions/sglang-occ3/deploy/v3/native_package.py | 53c5abdc9f6cc95868d2ff10927bae3a832007ddbb506ba46e988ea56a3c45d3 |
| E-LIFE | extensions/sglang-occ3/deploy/v3/lifecycle.py | 74d76ee99001537a976b20a204317863740ea505f9b9e4718e9558b6a8509442 |
| E-SUP | extensions/sglang-occ3/deploy/v3/supervisor.py | e4da466cc50fb6f842240c9cf3fa2d6e86461da30366bfd0a4a853b0e76a1b92 |
| E-CTL | extensions/sglang-occ3/deploy/v3/controller.py | 5c461beedde78ec98ee23293eaf0824e18d37139a3166a7f2a708f28927a3114 |
| E-DEPREADME | extensions/sglang-occ3/deploy/README.md | c363a736f85530156553f42fb7bfff6b506c085e8dd5d305f4f71f0479d102c1 |
