# OCC-3 Regression Gates

**Worktree:** `optimus-occ3-20261021` · **Date:** 2026-10-21 · **Mode:** offline documentation from existing evidence; no live claims.

**Citation convention.** `occ3-campaign/<path>` refers to the campaign evidence root `C:/Users/openclawuser/Optimus-Assistant/profile/workspace/occ3-campaign/`; worktree files are repository-relative. Full hashes in the evidence register. Gate definitions are quoted from the user specification's HARD ACCEPTANCE GATES section [E-SPEC]; statuses are taken from the campaign state [E-STATE] and the cited evidence files.

## 1. Purpose

This report maps the specification's 15 hard acceptance gates to their current status, names the evidence that exists today, and names the evidence that would prove each gate. It also encodes the two verified 2026-10-21 deployment failure modes as explicit regression gates the campaign must keep passing. Per the specification: do not claim full OCC-3 success unless all applicable gates pass; if a gate is blocked, report the actual blocker and preserve completed components [E-SPEC].

## 2. Spec gate status matrix

Statuses use: PASS (offline scope only), OFFLINE-ONLY (code and offline tests exist, live part not run), NOT STARTED (live), BLOCKED (specific blocker recorded), PENDING (no evidence yet).

| # | Spec gate (abbreviated) | Status | Existing evidence | Evidence that would prove it |
|---|---|---|---|---|
| 1 | Generic architecture passes without SGLang or DGX | PASS (offline scope) | 16 bridge lifecycle/fallback tests with the mock connector [E-TBO]; 401 aggregate owner test executions, 0 failed, 2 ignored; final chain exit 0 in 304.9 s [E-NSO]; root `bash scripts/check.sh` exit 0, cargo workspace all targets, 282 packaging unit tests, 10 skipped [E-STATE, E-ROOTCHECK] | None further for the offline scope; the residual gap (production daemon sender/receiver wiring absent [E-NIA]) is tracked under gates 7/8/13 |
| 2 | Same-model-only enforcement correct; cross-model never uses OCC-3 | OFFLINE-ONLY | Negotiation ladder step 3 plus tests: same id with different checkpoint rejected; cross-model rejected; advertisement fingerprint mismatch rejected [E-NEG, E-TBO]; identity fingerprint algorithm pinned cross-language [E-NSO] | Live `model_identity()` verification of the deployed runtime against the attested tuple, and a live mismatched-pair rejection record |
| 3 | OCC-2/OCC-1/text remain functional | OFFLINE-ONLY | OCC fidelity/selection/negotiation suites passed in the final chain (`occ_comm_fidelity`, `occ2_comm_fidelity`, `occ2_selection_policy`, `occ2_e2e_negotiation`: 30+2+9+16+7 passed) [E-NSO] | Live regression of OCC-1/OCC-2 delivery on the deployed path after OCC-3 code ships |
| 4 | SGLang extension builds, installs, starts | OFFLINE-ONLY (build/dry-run) | Native package dry verification exit 0: 72 sg source files, 23 plugin files, 21 patched files, 33 resolved imports, 4,025,221 produced source bytes, inputs stable; explicitly applied=false, installed=false, runtime_imported=false, gpu_proof=false [E-STATE, E-DRY] | A real derived-image build, install and server start on the DGX with `/occ3/v1/status` 200 (currently 404; no deployment under current approval) [E-FA1] |
| 5 | Extension works on the actual GLM-5.3 FP8 TP8 runtime | NOT STARTED (live) | `production_swap` NOT_STARTED; live attempts under latest approval: 0 [E-STATE]; two historical attempts failed and were recovered (see section 4) [E-FA1, E-REC2] | One controlled swap with candidate parity PASS, the seven supervisor gates ok, and external native proofs supplied twice [E-LIFE, E-SUP] |
| 6 | Cache safety: radix stays on; latent requests cannot contaminate ordinary cache | OFFLINE-ONLY (design + tests) | SGLang audit of the exact contamination mechanism and the salt/skip-insert mitigations [E-AUDIT]; scheduler-cache-ipc overlay and offline latent-cache tests in the extension suite [E-PATCH3]; `same_model_only` + salt-prefix `occ3-` in the capability shape [E-APIPY] | Live verification on the deployed server: normal traffic cache behaviour unchanged with the extension installed; salted-channel isolation demonstrated; contamination probe negative |
| 7 | Optimus communicates with the extension through the authenticated path | OFFLINE-ONLY (contract) | Connector transport/audit patterns and fail-closed control-plane contract implemented and tested offline [E-SGLANGRS, E-APIPY, E-OCC3CODEC] | A live authenticated connector health/capabilities call from Optimus to the deployed extension |
| 8 | True latent handoff: sender creates real latent state; receiver consumes it | BLOCKED | Campaign state: "BLOCKED: true latent method and actual native chat adapter missing. Text/radix baseline cannot satisfy this gate." [E-STATE]; native inference blocker recorded [E-STATE]; remaining blockers list [E-NSO] | A live capture -> transfer -> receive -> inference round trip with a consumed `x-optimus-occ3-receipt` on the receiver's actual native request [E-NIA] |
| 9 | Semantic fidelity: receiver demonstrates task-relevant information transfer | PENDING | None (by design: no claim without receiver proof) [E-EXTREADME] | Semantic fidelity benchmark comparing OCC-3 against text baseline under matched model/task conditions |
| 10 | Empirical benchmarks: OCC-3 vs OCC-1/OCC-2 matched conditions | PENDING | Offline benchmark harness exists (`benchmarks/bench_occ3.py`, `analyze.py`, task fixtures) but has produced no GPU results [E-BENCH] | Benchmark outputs from the harness run on the deployed runtime, matched model and task conditions |
| 11 | Production performance: normal GLM inference reliable; GPU memory/throughput acceptable | PENDING | Baseline preserved (original id/image/key, one actual rw model bind, health 200, no serving changes) [E-STATE] | Pre/post-swap GPU memory and throughput measurements on the DGX with the extension installed |
| 12 | Existing GLM-5.3 models.json entry enables the validated connector | PENDING (intentionally off) | `normal_occ3_enabled: false` [E-STATE]; no model entry carries an `occ3` block in this tree; capability schema requires `live-verified` validation to enable [E-PIAITYPES] | The existing GLM-5.3 models.json entry with an enabled, live-verified `occ3` block and automatic activation where eligible; incompatible models falling back |
| 13 | Real multi-agent test: native parent/subagent workload uses OCC-3 | PENDING (offline synthetic only) | Offline example runs a synthetic native controller: 3 model requests, 1 admitted message, receiver source facts absent by design [E-NSO]; no live run | A native Optimus parent/subagent workload on the deployed runtime completing an OCC-3 handoff with receipts |
| 14 | Failure recovery: connector/model/runtime failures safely fall back | OFFLINE-ONLY (code + inert tests) | v3 `recover()` design and offline/inert-Docker tests: 203 owned / 647 selected integration PASS, six independent inert-Docker probes, real-Docker clearance false [E-STATE, E-LW]; disposable rehearsal design [E-DEPREADME] | Disposable recovery rehearsal on the DGX (three failure scenarios) plus a live failure-injection run with automatic restore verified |
| 15 | Final report: evidence, limitations, rollback and PR complete | PENDING (drafts only) | This reports package (offline evidence drafts and stubs); PR not prepared | Completed report set with live evidence, the consolidated development PR, and limitations stated |

## 3. Campaign-internal gate statuses (from STATE.json)

The v3-controlled-deploy campaign tracked its own gates alongside the spec gates [E-STATE]:

| Campaign gate | Status (verbatim or summarised from state) |
|---|---|
| `baseline_preserved` | PASS: original ID/image/key, one actual rw model bind, health 200, no serving changes |
| `offline_regressions` | Root reruns config 116/116 + validation 147/147 before new review fixes; lifecycle final 232 worker PASS; native 116 + provider 141 worker PASS; more fixes/core/limit tests pending; no live proof |
| `independent_review` | Reviewer auditing settled lifecycle/config; validation 7 unsafe cases documented; owner fixes now require re-review |
| `disposable_recovery` | PENDING |
| `actual_candidate_mount_parity` | PENDING |
| `cpu_artifact_readability` | PENDING |
| `production_swap` | NOT_STARTED |
| `native_optimus_health` | PENDING |
| `live_gate8` | BLOCKED (see spec gate 8) |
| `normal_occ3_enabled` | false |

## 4. Deployment failure-mode regression gates (mandatory encodings)

The two verified 2026-10-21 failure modes are permanent regression gates for this campaign. Each maps to a v3 mechanism that exists in code and offline tests, and to the live evidence still required.

| Gate | Failure mode (verified root cause) | v3 mechanism (code) | Offline evidence today | Live proof still required |
|---|---|---|---|---|
| FM-1: no empty launch-argument elements | docker 29.6.2 range templates append a trailing blank line even for non-empty ranges; line-based capture injected an empty-string argument; container ran `python3 "" -m sglang.launch_server ...` and exited 1 [E-FA1] | Structured-JSON inspect capture with exact arrays (never newline-split); candidate created STOPPED; `assert_candidate_parity` element-wise config diff vs the working original (every `Config` field equal except `Image`; `HostConfig`, top-level `Mounts` and effective launch identical) [E-DOCKCONF, E-LIFE] | Parity assertions and create/verify flow covered by the v3 test suite (647 selected integration PASS, inert Docker) [E-LW] | A live candidate whose docker-recorded `Config.Cmd`/`Entrypoint` compares element-wise equal to the original's, with the parity artifact recorded |
| FM-2: mount fidelity from top-level Mounts | Controller normalised `NetworkSettings.Mounts`; docker's resolved mounts are top-level `Mounts`; both lists emptied, the `/data/neo-glm53` bind was omitted, `diff_configs()` falsely reported parity, and the server died on an invalid HF repo id [E-REC2] | `_actual_mounts()` parses raw top-level inspect `Mounts`, never `NetworkSettings.Mounts` (docstring states this); parity evidence records `actual_mounts_exact: true` and the exact mount count; artifact probe proves the candidate image can read the mounted model before mutation [E-DOCKCONF, E-LIFE] | Same v3 suite coverage [E-LW] | A live candidate with the exact original bind present and probe-verified readability |
| FM-3: exit-aware readiness | `wait_health()` only polled HTTP and never checked Docker `State.Running`/`ExitCode`; a dead candidate was treated as loading for twenty minutes [E-REC2] | `exit_aware()` re-checks `require_running()` around every verification step; `wait_ready()` raises `RunningUnready` vs `Deadline` distinctly; readiness observations journaled [E-LIFE, E-CTL] | v3 test suite [E-LW] | Disposable rehearsal: a crashing candidate is detected within one poll interval and triggers rollback |
| FM-4: automatic restoration executes | v1 failure path printed rollback instructions and did not execute them; restart policies `no` on both containers left no self-recovery path (~41 min outage) [E-FA1] | `recover()` reconciles by immutable ID under an independent rollback budget (default 900 s); stops/renames the candidate, restores and starts the original, then re-verifies health, authenticated chat and ingress; supervisor watchdog runs rollback even if the controller is killed and never retries the deployment [E-LIFE, E-SUP, E-DEPREADME] | v3 test suite; `disposable-test` rehearsal design [E-LW, E-DEPREADME] | The three-scenario disposable rehearsal on the DGX and a live failure-injection restore |
| FM-5: no armed auto-redeploy | After recovery, nothing may auto-redeploy: scripts disarmed, restart policies `no`, no scheduler entries [E-REC2, E-FA1] | Recovery-only tooling verifies no deployment processes remain; controller entrypoints replaced by refusing stubs on the host [E-REC2] | Verified during the 2026-10-21 recovery (audits found no matching scheduled tasks) [E-REC2] | Re-verification immediately before any future swap window |

## 5. Offline regression inventory (current, citable)

Rust (commands and results recorded in the frozen increment result) [E-NSO]:

- `cargo test --locked --offline -p pi-agent-core -- --test-threads=1`: 113+3+4 passed, 1 ignored.
- `cargo test --locked --offline -p pi-coding-agent --test occ3_source_handoff --test occ3_native_inference`: 16+9 passed.
- `cargo run --locked --offline -p pi-coding-agent --example occ3_native_handoff -- --offline`: exit 0 (offline synthetic native controller).
- `cargo test --locked --offline -p pi-ai --test source_capture --test inference_attachment`: 11+10 passed; `--lib inference_attachment`: 9 passed; `--lib providers::openai_completions`: 122 passed, 1 ignored.
- OCC regression chain (`occ3_bridge_offline`, `occ_comm_fidelity`, `occ2_comm_fidelity`, `occ2_selection_policy`, `occ2_e2e_negotiation`): 30+2+9+16+7 passed.
- Message policy (`core::agent_messages::tests::occ3`) and further suites in the same result; aggregate 401 passed / 0 failed / 2 ignored (aggregate executions, not 401 unique cases); final chain exit 0, 304.9 s.

Python extension (v3 deploy code) [E-LW, E-STATE]:

- Lifecycle worker snapshot: 186 owned tests passed; integration 618 collected passed across the ten v3 modules; latest lifecycle review: 203 owned / 647 selected integration PASS with six independent inert-Docker probes; real-Docker clearance explicitly false.
- Root repository gate `bash scripts/check.sh`: exit 0, 221.1 s, cargo workspace all targets passed, 282 packaging unit tests passed, 10 skipped, zero new warning candidates [E-ROOTCHECK, E-STATE].

Known caveats recorded by the campaign: aggregate executions are not unique test counts; the terminal-receipt v2 suite's last run is owner-reported only (no final freeze or independent review) [E-STATE]; a passing offline gate is not live clearance [E-EXTREADME].

## 6. PENDING LIVE EVIDENCE (consolidated)

1. Everything in the status matrix marked OFFLINE-ONLY, NOT STARTED, BLOCKED or PENDING above requires live evidence on the DGX under the current approval before any gate may be reported as passed.
2. The campaign's own remaining-blockers list: root final `scripts/check.sh` across the integrated repository; ordinary kernel execution-scope source provenance unsupported; full-MAX buffer/timeout/resource compatibility unproved; actual installed worker GPU all-rank state capture/injection/readiness and live native/semantic proof unexecuted; scientific Gate 8 and disposable recovery / live rollback gates remain root-owned [E-NSO].
3. Deployment under the current authorization requires offline regressions and disposable recovery tests to PASS without disturbing production GLM before exactly one controlled swap [E-AUTH].

## Evidence register

| Id | Path | sha256 |
|---|---|---|
| E-SPEC | occ3-campaign/v3-controlled-deploy/ORIGINAL-USER-SPECIFICATION.md | 9f731ebd2e15cc9ffc6dbe42a7c33c9e44e0cd8149dba76f39a8828a5df629e0 |
| E-STATE | occ3-campaign/v3-controlled-deploy/STATE.json | 19a497e2c5682ccd2f03cd6dc364b02ca8e93f8b29dd2cc29939853cd0e5223d |
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
| E-BENCH | extensions/sglang-occ3/benchmarks/bench_occ3.py | 42f892fb9da44da99334183b6b79c4f1f50817519e471c7e3818c12458c170b2 |
| E-PATCH3 | extensions/sglang-occ3/patches/scheduler-cache-ipc.v1.json | a80d0051754e2a69dbe88eb642871c871cf1707d15af9a2b4a5e6cc6595865c6 |
| E-DOCKCONF | extensions/sglang-occ3/deploy/v3/docker_config.py | 9c7c96c0831b4bc8358f3e1479877350c573b768c5a847146fbcc95cedc6bfb4 |
| E-LIFE | extensions/sglang-occ3/deploy/v3/lifecycle.py | 74d76ee99001537a976b20a204317863740ea505f9b9e4718e9558b6a8509442 |
| E-SUP | extensions/sglang-occ3/deploy/v3/supervisor.py | 09df965edb3a4742b4c1610792b78498f2acb8a13d855773cf80a6c955bdfbf1 |
| E-CTL | extensions/sglang-occ3/deploy/v3/controller.py | 5c461beedde78ec98ee23293eaf0824e18d37139a3166a7f2a708f28927a3114 |
| E-DEPREADME | extensions/sglang-occ3/deploy/README.md | c363a736f85530156553f42fb7bfff6b506c085e8dd5d305f4f71f0479d102c1 |
