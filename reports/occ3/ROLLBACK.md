# OCC-3 Rollback

**Worktree:** `optimus-occ3-20261021` · **Date:** 2026-10-21 · **Mode:** offline documentation from existing evidence; no live claims.

**Citation convention.** `occ3-campaign/<path>` refers to the campaign evidence root `C:/Users/openclawuser/Optimus-Assistant/profile/workspace/occ3-campaign/`; worktree files are repository-relative (sha256 of the current working-tree state, which includes uncommitted edits). Full hashes in the evidence register.

## 1. Purpose and scope

This report documents (a) the preserved-container rollback procedure used by the v1 deployment scripts, (b) the honest failure history of the two 2026-10-21 deployment attempts with their verified root causes, and (c) the v3 controller's automatic rollback design, which encodes both failure modes as regression-checked invariants. No automated rollback has been executed against the real DGX Docker daemon; every live recovery to date was manual and is documented as such.

## 2. Deployment failure history (verified root causes)

### 2.1 First attempt (2026-10-21 ~03:34-04:19 UTC, ~41 min downtime)

Timeline: deploy armed 03:34Z; derived image `neo-glm53:occ3-20261021-033716` built 03:37:16Z; plugin preflight passed inside the image 03:37:19Z; mutation phase renamed `neo-glm53` to `neo-glm53-pre-occ3` and stopped it; new container started 03:37:43Z and exited code 1 within ~1 s; deploy reported FAILURE with rollback instructions; manual recovery completed 04:19:05Z [E-FA1].

Root cause chain (verified against docker-recorded state) [E-FA1]:

1. DGX docker 29.6.2 emits a trailing blank line for `{{range X}}{{println .}}{{end}}` Go templates even when the range is non-empty (verified live: entrypoint range printed `python3` plus one blank line; cmd range produced 47 lines for 46 elements).
2. The v1 deploy script captured arrays with `mapfile -t`; the trailing blank became an extra empty-string element in `ENTRYPOINT_ARGS` and `CMD_ARGS`.
3. The multi-element entrypoint replication logic prepended the empty string, so `Config.Cmd` of the failed container recorded `["", "-m", "sglang.launch_server", ...]`.
4. The container executed `python3 "" -m sglang.launch_server ...`; Python treated the empty script path as the current directory, found no `__main__.py` in the workdir, printed `can't find '__main__' module in '/sgl-workspace/sglang'` and exited 1.
5. The plugin was NOT the cause: preflight had imported the entry point cleanly in the same image, and the error is a Python argument-resolution failure before any sglang or plugin code ran.

Why nothing caught it: the dry run printed the planned command via shell string expansion in which empty-string elements render invisibly (the line looked byte-identical to a correct one); the preflight verified the plugin import, not launch-argument fidelity; no step compared the would-be container `Config` (Cmd/Entrypoint as docker records them) against the original [E-FA1].

Why service did not self-restore: the deploy script's failure path called `die()` with rollback instructions and deliberately did not execute the rollback; the new container's restart policy was `no` (captured from the original); the preserved original container had been explicitly set `docker update --restart=no` (to avoid port fights on daemon restart), so nothing would bring it back [E-FA1].

### 2.2 Second attempt (2026-10-21 ~05:04-05:31 UTC)

Candidate started 05:04:06 UTC and exited code 1 at 05:04:35.88 UTC. The log ends with HFValidationError/OSError: the local model path `/data/neo-glm53/model` was treated as an invalid Hugging Face repository id [E-REC2].

Root cause (docker evidence): the original has one bind mount `/data/neo-glm53 -> /data/neo-glm53`, recorded in `HostConfig.Mounts` (one entry), not `HostConfig.Binds` (null). The controller's `normalize()` read `NetworkSettings.Mounts`; docker's resolved mounts are top-level `Mounts`. Both normalised mount lists were therefore empty, `build_run_args()` omitted the mount, and `diff_configs()` falsely reported parity. The earlier structural PASS claim was invalid. The plugin import check and a `launch_server --help` canary never verified that the candidate could access its model path [E-REC2].

Why rollback was slow: `wait_health()` only polled HTTP and slept until `HEALTH_TIMEOUT_S=1200`; it never checked Docker `State.Running`/`ExitCode`, so a dead candidate was treated like a still-loading server for twenty minutes. The watchdog only waited for controller exit or its 3000-second outer timeout, with no independent container-exit check. The disposable tests used short health timeouts and did not prove immediate detection of a terminated serving container [E-REC2].

Recovery (verified): all deployment control processes stopped by identity; controller/watchdog/deploy entrypoints replaced by refusing stubs with originals retained privately; original container `c4a26b345ec6`, image `sha256:b1259f3e327...` restored as `neo-glm53` and started 05:23:47 UTC; Config, HostConfig, Mounts, Image and Id matched the preserved original exactly; authenticated chat 200 at 05:30:47 UTC; catalog 200 (glm-5.3); health 200; ingress path verified; native Optimus proof receipt `agentmsg_1add7b64-e7cb-4c52-b758-840514f5e1a4`; failed candidate preserved as `neo-glm53-occ3-failed-second-20261021T050435Z` [E-REC2].

## 3. Preserved-container rollback procedure (v1 scripts)

The v1 design principle: the original container and image are never deleted; only OCC-3-added state is removed [E-DEPREADME, E-ROLLBACKSH].

deploy.sh mutation phase: rename `neo-glm53` to `neo-glm53-pre-occ3` (preserve, not delete); stop it; set its restart policy to `no` (avoid port conflict on daemon restart; rollback restores the original policy); wait for GPU memory release; start a new container on the derived image with the captured configuration [E-DEPREADME].

rollback.sh steps (six, from the script header) [E-ROLLBACKSH]:

1. Stop and remove the occ3-bridge container currently named `neo-glm53` (only the OCC-3-added container is ever removed).
2. Rename `neo-glm53-pre-occ3` back to `neo-glm53`.
3. Restore the original restart policy from `occ3-deploy-state.env`.
4. Start the restored container.
5. Poll `GET http://127.0.0.1:30001/health` until 200 (default timeout 900 s, poll 5 s, docker stop timeout 300 s).
6. Confirm the plugin routes are gone (informational) and remove the state file.

Credential handling: the API key is read from `docker inspect` into memory only, piped to curl through a stdin config, never printed, logged or written to a file; the derived image is not auto-deleted (`docker rmi` after confirmation) [E-ROLLBACKSH, E-DEPREADME].

Known v1 limitation (materialised in the first outage): the failure path prints rollback instructions instead of executing them, so with restart policies `no` the box has no path to self-recovery [E-FA1]. Current state: the DGX-side runnable controller, watchdog and legacy deploy.sh were replaced by refusing stubs during the recovery-only phase (exact originals retained in the private evidence directory); the local watcher is likewise disarmed and backed up; no Windows scheduled task, DGX cron/systemd timer/unit or at-queue references the deployment [E-REC2].

## 4. v3 controller automatic rollback design

The v3 deployment tooling (`extensions/sglang-occ3/deploy/v3/`) redesigns rollback around immutable IDs, structured JSON inspection, exit-aware monitoring and automatic restoration. Key files: `lifecycle.py` (state machine + `recover`), `docker_config.py` (structured create bodies + parity), `controller.py` (CLI; `swap` only launches an independent supervisor), `supervisor.py` (independent guardian).

### 4.1 One-attempt state machine [E-LIFE]

- Terminal phases: `COMPLETE`, `RESTORED`, `NOOP`, `RESTORE_FAILED`, `PREFLIGHT_FAILED`. A new, independent recovery budget backs every restoration; only idempotent restoration operations are retried; candidate deployment is never retried; transport loss after a successful Docker operation is resolved by inspecting its effect, not by assuming failure.
- Immutable journal fields (original/candidate ids and image ids, names, plan, created_at, schema) bind every later action to the exact captured originals; state files are written atomically with 0600 permissions and symlink refusal; an advisory file lock serialises attempts.
- Default budgets (seconds): startup 1200, deployment 3600, rollback 900, proof 300, controller 45, supervisor lease 5, poll 0.5, request 120, probe 120, stop 30, validation 900.

### 4.2 Preparation and parity (encodes failure mode 1 and 2) [E-LIFE, E-DOCKCONF]

- `docker inspect` is read as structured JSON; Entrypoint/Cmd/env are carried as exact arrays, never newline-split [E-DEPREADME].
- `_actual_mounts()` parses raw top-level inspect `Mounts`, never `NetworkSettings.Mounts` — the function docstring states this verbatim [E-DOCKCONF].
- `prepare()` creates the candidate STOPPED under a fresh nonce-bound temporary name, then `assert_candidate_parity(original, candidate, candidate_image_id)` diffs the recorded configuration element-wise against the working original: every `Config` field must be identical except `Image`, `HostConfig` must be identical, top-level `Mounts` must be identical, the effective launch (`Path`/`Args`) must be identical, and network options must match modulo generated aliases/DNS names; the parity evidence records `actual_mounts_exact: true` with the exact mount count [E-LIFE, E-DOCKCONF]. An artifact probe (CPU-only container with the same mounts/user/workdir) proves the candidate image can read the mounted model before any mutation [E-LIFE].
- Production mutation additionally requires a fresh, exact, one-attempt authorization (matching attempt/original/candidate ids, expiry within one hour) plus verified receipts for the offline, rehearsal and source-attestation gates and an explicit unchanged-config ingress-reload approval [E-LIFE].

### 4.3 Exit-aware monitoring (encodes the slow-rollback failure) [E-LIFE]

- `exit_aware()` runs every verification operation on a worker thread while continuously re-checking `require_running()` against Docker `State.Running` with the expected image; a container that dies is detected within one poll interval rather than after an HTTP timeout. `wait_ready()` raises `RunningUnready` when the container is running but not ready, and surfaces Docker exit instead of sleeping out the budget.
- The controller's health wait records readiness observations (HTTP status, ok, timestamps) into the journal [E-CTL].

### 4.4 Automatic restoration (`recover`) [E-LIFE]

On any failure after mutation begins (or an explicit recovery invocation), `recover()` reconciles by immutable ID under the rollback budget: verify the original still exists with its exact image and passes parity; stop the candidate (bounded stop timeout); rename the candidate to the failed name if it holds the original name; rename the original back to its original name; start the original. Restoration has priority over helper cleanup. Then: refresh ingress (recovery mode); run service proofs for health, authenticated chat and the ingress path with exit-aware monitoring; re-verify parity of the restored original and that the candidate is not running; settle helper operations (admission rotation, operation fence); write `recovery-result.json` plus timestamped `recovery-evidence-*.json` (evidence-write failures cannot block recovery). The watchdog supervises with a hard timeout and runs the rollback even if the controller itself is killed; it never retries the deployment [E-DEPREADME].

### 4.5 Independent supervisor and completion gates [E-SUP, E-CTL]

- The supervisor is a separate process owning the original-ID fence (private 0700 directory, guardian identity recorded) and never treats exit code zero alone as deployment success.
- Completion requires phase `GATES_COMPLETE`, authorization consumed, exactly one restart, and seven required gates all ok: `service_initial`, `native_initial`, `live_before`, `restart`, `live_after`, `service_final`, `native_final`. Native Optimus proof must be supplied externally twice, matched against the checkpoint (attempt/candidate/process epoch/phase/challenge, kind `native-optimus-glm-response`, verified true, fallback_used false, fresh evidence receipt with digest verification).

### 4.6 Offline verification of the v3 design [E-LW, E-STATE]

- Lifecycle worker snapshot: owned tests 186 passed (40.4 s); integration run 618 collected tests passed across the ten v3 test modules; latest lifecycle review records 203 owned / 647 selected integration PASS with six independent inert-Docker probes, and explicitly marks real-Docker clearance false (inert Docker only).
- The deploy README documents a `disposable-test` rehearsal covering three failure scenarios (startup crash, health timeout, validation failure) against throwaway containers, proving rollback restores and verifies service [E-DEPREADME].

## 5. Current rollback posture

- The live DGX runs the original `neo-glm53` with no OCC-3 plugin (routes 404); both failed candidates and their startup logs are preserved; all deployment control processes are stopped and their entrypoints disarmed [E-FA1, E-REC2, E-STATE].
- Campaign state under the current approval: zero live attempts, `production_swap` NOT_STARTED, `disposable_recovery` PENDING, `baseline_preserved` PASS (original id/image/key, one actual rw model bind, health 200, no serving changes) [E-STATE].

## 6. PENDING LIVE EVIDENCE

1. The v3 automatic rollback has never been executed against the real DGX Docker daemon; every code path is offline/inert-Docker verified only (`real_Docker_clearance: false`) [E-STATE, E-LW].
2. The disposable recovery rehearsal on the DGX (startup-crash, health-timeout, validation-failure scenarios with throwaway containers) is a PENDING campaign gate [E-STATE].
3. A live end-to-end production swap plus automatic rollback on failure remains unexecuted under the current approval.
4. Rollback timing under real conditions (weights reload ~minutes) has only the v1 900 s health-wait precedent; no v3 live measurement exists.

## Evidence register

| Id | Path | sha256 |
|---|---|---|
| E-FA1 | occ3-campaign/dgx-inspect/OCC3-DEPLOY-FAILURE-ANALYSIS.md | 084be196931164e17293ab3f60241cad8105c4c57a47b8d3894b278d945173d9 |
| E-REC2 | occ3-campaign/recovery-only/SECOND-DEPLOYMENT-RECOVERY.md | 5e0a9c06f4c9a5f8e91322fcb20258964a753e333d5d00351f0aef68390b80a7 |
| E-STATE | occ3-campaign/v3-controlled-deploy/STATE.json | 19a497e2c5682ccd2f03cd6dc364b02ca8e93f8b29dd2cc29939853cd0e5223d |
| E-LW | occ3-campaign/v3-controlled-deploy/lifecycle-worker-l09-l10-v4-result.json | f0a6808c70e2b19e9b56ef6a357d1597b8b5b00d2475968327caea4459cd8f29 |
| E-DEPREADME | extensions/sglang-occ3/deploy/README.md | c363a736f85530156553f42fb7bfff6b506c085e8dd5d305f4f71f0479d102c1 |
| E-ROLLBACKSH | extensions/sglang-occ3/deploy/rollback.sh | f2f86063869aabdaa4be0e610a62b1a087d3d25561c2e43fa31d1d2be82570f4 |
| E-LIFE | extensions/sglang-occ3/deploy/v3/lifecycle.py | 74d76ee99001537a976b20a204317863740ea505f9b9e4718e9558b6a8509442 |
| E-DOCKCONF | extensions/sglang-occ3/deploy/v3/docker_config.py | 9c7c96c0831b4bc8358f3e1479877350c573b768c5a847146fbcc95cedc6bfb4 |
| E-CTL | extensions/sglang-occ3/deploy/v3/controller.py | 5c461beedde78ec98ee23293eaf0824e18d37139a3166a7f2a708f28927a3114 |
| E-SUP | extensions/sglang-occ3/deploy/v3/supervisor.py | 09df965edb3a4742b4c1610792b78498f2acb8a13d855773cf80a6c955bdfbf1 |
