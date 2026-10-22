# OCC-3 Live-Startup Repair - Route Registration and the 404 Mechanism

**Status: MECHANISM STATIC-VERIFIED (code reads + live-window observations); phase-split implementation IN FLIGHT (uncommitted) and LIVE-PENDING.** This report explains why a declined install produces exactly the observed 404-with-valid-key / 401-keyless signature, records the phase-split design that makes a DECLINED server observable over authenticated HTTP, and lists the live milestones any future window must hit before the control plane can be called working.

**Worktree:** `optimus-occ3fix-20261022` · **Branch:** `fix/occ3-sglang-live-startup` · **Head at writing:** `99b2ebdc9` · **Date:** 2026-10-22 (written ~08:48Z; the worktree advanced during this report's writing - the state below is timestamped) · **Mode:** offline documentation from verified evidence and direct file reads.

**Citation convention.** Same as ROOT-CAUSE.md in this directory: repository-relative paths under `extensions/sglang-occ3/`, line numbers at the named commit (default `99b2ebdc9`; the cited `native_install.py`, `latent_control_api.py`, `plugin.py`, `api.py` and `service_checks.py` are unchanged from `d2ed1123b`); `image:` = file inside the TS4 candidate image `neo-glm53:occ3-v3-live-20261022t0356z` (id `a31b23c0401b`).

## 1. The mechanism: route mounting is the LAST mutation

`install_native_runtime` (native_install.py:652-788) performs its entire verification chain before it touches the app:

1. preflight + auth + components + hooks (native_install.py:682-696),
2. the all-rank worker readiness RPC (:698),
3. the precommit ownership fence (:704, `_revalidate_precommit` :546-591),
4. and only then `mutation_started = True` (:706) followed by the single route mount `app.include_router(components.router)` (:709), the broker middleware (:712-719), the hook setattrs (:720-726) and the OpenAPI reset (:727).

The control router carries exactly five POST routes under `/occ3/v1/native/` (`_CONTROL_PATHS`, native_install.py:448-452; registration at latent_control_api.py:190-195): `sessions/enroll`, `publish`, `publication/status`, `retire`, `runtime/identity`. There is no other mount site: the dormant plugin routers (`plugin.py:36-37`) mount only the legacy `/occ3/v1/status|identity|capabilities|channels` GET surface and the 410 legacy trio (api.py:71-131) plus the `/occ3/v1/latent/*` KV-handle surface (kv_handles.py) - none covers `/occ3/v1/native/*`.

Therefore ANY decline before :709 leaves the served app with:

- the original api-key middleware still active (it was verified, not replaced, by `_existing_auth`, native_install.py:111-134) - so keyless requests get 401 and valid-key requests pass auth;
- zero `/occ3/v1/native/*` routes - so the authenticated request falls through to 404.

That is the W4-A1 signature, confirmed live in TS4: `POST /occ3/v1/native/runtime/identity` returned 404 with valid credentials and 401 keyless, across six path variants, with no occ3-bridge log lines in docker logs (BUG-LEDGER.md, W4-A1 entry). The receipt lived only on a process-local app attribute (`_STARTUP_ATTR`, native_install.py:802, set at :999) and in the C30 ERROR lines (:782-787, :1001-1002) - invisible to every authorized external prober. The controller's committed probe encodes this discrimination: 404 -> `occ3_routes_absent` (deploy/v3/service_checks.py:139-141 at `d2ed1123b`).

## 2. Why the app stays healthy (the design intent)

A declined install is a clean refusal, not a fault: the finally block (native_install.py:748-779) verifies the no-mutation rollback and closes the transaction's control client, the crash-set check (:1003-1009) passes only when nothing leaked, and `selected_serve` proceeds to `owner.original_serve` (:1012). The server then serves ordinary traffic indefinitely - TS4's candidate passed health, authenticated chat, the occ3_echo tool round trip and ingress for about twenty-two minutes - while every occ3 native route 404s. The cost of this safety is observability: without the C30 logging or a mounted status surface, a declined boot is externally indistinguishable from a never-installed one, and a live window burns its full native-proof wait (600 s in TS4) before the designed rollback.

## 3. The phase-split design: DECLINED becomes observable

The design report is `C:/Users/openclawuser/Optimus-Assistant/profile/workspace/occ3fix-audit/occ3fix-phasesplit-design.md` (sha256 `af9c48b2c77ea07a6d9d65be9bf3626416a88f912b187279b51e61ef149ccd2c`; verified by this author). Its essentials:

- **State machine (server-side, main process):** `DISABLED -> CONTROL_REGISTERED -> INSTALLING -> DECLINED | NATIVE_READY`, with `LIVE_VERIFIED`/`AVAILABLE` deliberately NOT server states (the server never self-certifies live proof; the receipt hard-codes `live_proof: False, gate8_pass: False`, native_install.py:147-152). Only the lock-holding install attempt mutates the terminal states; `installation_busy` and opt-in refusals never transition.
- **Minimal control-plane router mounted BEFORE native GPU validation:** three routes - `GET /occ3/v1/status`, `GET /occ3/v1/capabilities`, `POST /occ3/v1/native/runtime/identity` - registered by a new `register_control_plane(app, tm)` step inside `prepare_selected_native_server`, refused unless `_existing_auth` passes (never mount unauthenticated control routes; no security check weakened). Single ownership: the two GET handlers move out of the dormant Router A and the identity route out of the install-time Router C, so the paths have one owner and no shadowed duplicates.
- **Truthful refusal shapes:** pre-ready identity answers HTTP 503 `{"error": {"code": "not_ready"}}` through the existing error envelope (latent_control_api.py:172-177) - never a fabricated incarnation tuple (the per-boot values exist only in the worker's health reply). `status` exposes the fixed decline `code`, the `stage`, an attempt marker correlating with the C30 log lines, and seven monotonic progress booleans (`experimental_selected`, `preflight_reached`, `backend_constructed`, `worker_readiness_reached`, `hooks_prepared`, `route_mount_attempted`, `rollback_completed`). DECLINED never advertises availability: `supported=false`, `methods=[]`, `enabled=false` in every state.
- **Controller reconciliation:** the 200-implies-native-ready rule is preserved (identity 200 requires NATIVE_READY server-side AND the full validated tuple controller-side); a 503 identity triggers a `GET /occ3/v1/status` probe - `state=DECLINED` -> fail fast with evidence `occ3_declined` carrying the exact decline code, `state=INSTALLING|CONTROL_REGISTERED` -> transient `occ3_identity_unavailable`, status 404 -> `occ3_routes_absent` (control-plane registration itself failed). The supervisor/restore path is untouched: DECLINED still means serve-inert and restore-at-deadline.

## 4. Implementation status at writing (honest, point-in-time)

- Server-side phase split (the `startup_state` module, `register_control_plane`, the moved routes): IN FLIGHT - an untracked `occ3_sglang/startup_state.py` plus uncommitted edits to `occ3_sglang/api.py`, `latent_control_api.py`, `plugin.py` and `native_install.py` (the `register_control_plane` site, ~170 added lines) and `deploy/v3/native_package.py` were observed in the working tree at 08:50Z (not present at `99b2ebdc9`; nothing committed at writing).
- Controller-side evidence branch: IN FLIGHT - uncommitted working-tree edits observed 08:46Z to `deploy/v3/service_checks.py` (the `_occ3_identity_without_native_tuple` status probe and `occ3_declined` evidence), `deploy/v3/controller.py` (fail-fast detail + journal `occ3_decline` record) and `deploy/v3/rehearsal.py` (a tenth `occ3-declined` fixture scenario answering 503 identity + DECLINED status), with matching v3 test edits (test_v3_lifecycle.py, test_v3_rehearsal.py, test_v3_service_checks.py). None of this is committed; nothing here is claimed done.
- Until the server side lands and is committed, a declined boot still 404s on the identity route; the controller probe then reports `occ3_routes_absent` (status 404), not `occ3_declined`. The stage/code ERROR lines from `71e449783` + `64492e8b1` remain the primary live diagnostic in the interim.

## 5. Required live milestones (any future window; TS5 gated on fresh user authorisation)

1. Repaired image built from the landed repairs (install-gate acceptance `d2ed1123b`; admission-overlay acceptance `99b2ebdc9`; observability commits `71e449783` + `64492e8b1`; the phase-split once it lands), with the offline suite green at that head.
2. Boot reaches serve: `occ3-bridge:` ERROR lines visible in docker logs if any decline occurs (grep targets in the decline-map section 6); no line means the mount line `occ3-bridge: native runtime mounted (attempt=... stage=install code=installed_experimental_not_live_validated)` must appear (native_install.py:737-738).
3. `service_initial` gate: `check_occ3_identity` returns the full validated tuple (expected_ranks 8, ready_ranks 8, capability flags true) - the first live proof that the control router mounted and the worker bootstrap passed on the unified cache. With the phase split landed, DECLINED is instead observable as identity 503 + status `state=DECLINED` with the exact code.
4. The ordinary service gates (health, authenticated chat, tools, ingress) green on the candidate.
5. The native proofs (GATE 8 external descriptors) supplied twice from the live identity tuple - still never started in any window.
6. Rollback path exercised only by design (deadline or abort), with the original restored and verified.

No live window has run on any repaired build; every item above is LIVE-PENDING.
