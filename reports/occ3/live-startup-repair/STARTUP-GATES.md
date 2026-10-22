# OCC-3 Live-Startup Repair - Startup and Install Gate Inventory (status at TS4)

**Status: STATIC-VERIFIED for every gate except one; the failing gate is root-verified; live re-verification on repaired code is LIVE-PENDING.** This report inventories every gate between container start and a mounted occ3 control router, states what each gate proves, and records its live status in window TS4 (the fourth live window, TS=20261022T0356Z). It references the campaign's decline-path taxonomy rather than duplicating it.

**Worktree:** `optimus-occ3fix-20261022` · **Branch:** `fix/occ3-sglang-live-startup` · **Head at writing:** `99b2ebdc9` · **Date:** 2026-10-22 (written ~08:48Z; the worktree advanced during this report's writing - the state below is timestamped) · **Mode:** offline documentation from verified evidence and direct file reads.

**Citation convention.** Same as ROOT-CAUSE.md in this directory: repository-relative paths under `extensions/sglang-occ3/`, line numbers at the named commit (default `99b2ebdc9`; `latent_worker.py` cited at `64492e8b1` where the pre-repair gate matters; `native_install.py`, `latent_backend.py` and `service_checks.py` are unchanged from `d2ed1123b`; the scheduler admission-overlay patch replacement is cited at `64492e8b1` where its pre-repair condition is quoted); `image:` = file inside the TS4 candidate image `neo-glm53:occ3-v3-live-20261022t0356z` (id `a31b23c0401b`); root-verified = the root session's first-hand verification (2026-10-22, session `01a16256`).

## 1. How to read the status column

- **PASS (static-verified)**: the gate's condition was verified to hold against the real candidate image or the candidate's own logs, without a live request exercising it (decline-map probes P1-P15, shape-audit matrix, root notes).
- **PASS (live)**: the gate ran and passed during a live window's serving phase.
- **FAIL (root-verified)**: the gate's condition is known false in the production runtime; it fired in TS4.
- **NOT REACHED**: the gate sits after a failing gate in the same attempt.

## 2. Phase P0 - prepare (synchronous, before serve; main process)

Entry: `prepare_selected_native_server` (native_install.py:941-1024), inserted by the packaged source patch immediately before `server.run()` (patches/native-startup.v1.json; image `http_server.py:2767-2770`).

| Gate (file:line at `d2ed1123b`) | Proves | Receipt code on refusal | TS4 status |
|---|---|---|---|
| `_read_startup_literal` native_install.py:806-859 | The image-baked selection file exists, is a regular non-symlink single-link file of bounded size, unmodified between stat and read, and carries `EXPERIMENTAL_STARTUP=True` + the two pinned digests | `experimental_startup_not_selected` / `startup_selection_unsafe` / `startup_selection_invalid` | PASS (static-verified; build receipt `experimental_startup_selected=true`, probe P1 imports the module) |
| `_verify_startup_artifacts` :861-871 | The imported module's literals equal the parsed ones; the pinned profile and the source attestation load | `startup_selection_changed` / `experimental_startup_refused` | PASS (static-verified; probe P1 ran both loaders in the exact image) |
| `_verify_native_server` :873-931 | The uvicorn shape is exactly one default `uvicorn.Server(uvicorn.Config(app, ...))` with `loop="uvloop"`, workers 1, no reload, lazy middleware stack, single-tokenizer app attributes, and untampered `Server.run/serve` provenance | `unsupported_native_startup` / `startup_server_provenance` / `startup_owner_mismatch` | PASS (static-verified; the wrapper demonstrably ran - the decline was serve-time) |
| `_existing_auth` :111-134 | The original api-key middleware is exactly one `_ApiKeyASGIMiddleware` registration bound to this app with the server's own key | `original_api_key_required` / `original_auth_registration_required` / `original_auth_mismatch` | PASS (static-verified; probe P10; the live 401-keyless behavior confirms the middleware active) |

Prepare passing is what makes the decline serve-time: `selected_serve` wraps `server.serve` only after these gates (native_install.py:1014-1017).

## 3. Phase P1-P7 - serve-time install chain (inside `server.run()`)

Entry: `selected_serve` (native_install.py:982-1012) -> `install_native_runtime` (:652-788). Order below is execution order; each row's refusal code is the receipt returned (decline ERROR line at :782-787, echo at :1001-1002).

| # | Gate (file:line at `d2ed1123b`) | Proves | Refusal code | TS4 status |
|---|---|---|---|---|
| 1 | Opt-in check :660-661 | Only the trusted bootstrap may install | `explicit_experimental_opt_in_required` | PASS (static-verified; `selected_serve` passes `True` at :988) |
| 2 | Install lock :662-663 | No concurrent install transaction | `installation_busy` | PASS (fresh process, single call) |
| 3 | Existing-owner check :673-681 | A stale/partial install attribute cannot be silently reused | `installation_owner_mismatch` | PASS (fresh app) |
| 4 | `_runtime_preflight` :309-330 | sglang `0.5.21`, the 10-clause frontend profile (tokenizer_worker_num 1, cuda, tp8/dp1/pp1, no lora/disagg/trace/log_requests/root_path), `app.middleware_stack is None` | `unsupported_runtime` / `unsupported_frontend_profile` / `pre_serving_install_required` | PASS (static-verified; the candidate's own server_args dump attests every clause - decline-map E4) |
| 5 | Control-client occupancy :683-684 | No half-open control client on the tokenizer manager | `control_client_occupied` | PASS (fresh tm) |
| 6 | `_check_route_conflicts` :462-473 | No existing route full-matches the five POST `/occ3/v1/native/*` paths; route count bounded | `route_profile_unsupported` / `native_route_conflict` | PASS (static-verified; all six live probes 404 - no conflicting route exists) |
| 7 | Buffer profile :687-689 | Default `BufferProfile` within the 4 MiB bound | `unsupported_buffer_profile` | PASS (static-verified) |
| 8 | `_construct_components` :380-445 | Installed source bytes re-verified against the attestation (95 files) in the running interpreter; descriptor overlay present; provenance of `decide_request_auth`/`_wrap_fn`/middleware `__call__`; `tm` is exactly `TokenizerManager` serving `OpenAIServingChat`; control router built with exactly `_CONTROL_PATHS` (:448-452), all POST, endpoints provenance-verified | `source_*` / `hook_provenance_invalid` / `hook_source_unattested` / `descriptor_overlay_unavailable` / `unsupported_serving_class` / `native_router_unverified` / `factory_cleanup_unconfirmed` | PASS (static-verified; probe P1 re-ran the full attestation verification in the exact image; probes P2/P3/P7) |
| 9 | `_prepare_hooks` :475-505 | The three hook targets are the attested originals and unoccupied | `unsupported_hook_registry` / `unsupported_hook_targets` / `hook_target_occupied` / `hook_provenance_invalid` | PASS (static-verified; probes P2/P7) |
| 10 | **Worker readiness RPC** :698 (latent_backend.py:117-127 -> ControlClient.call :26-55) | All 8 TP ranks bootstrap the worker runtime and answer a consensus health tuple (identity, layout, capabilities) | `installation_unavailable` (generic; sub-cause categories: control timeout / worker refusal / identity mismatch) | **FAIL (root-verified)** - see section 4 |
| 11 | Readiness flags :701-703 | The refreshed readiness snapshot is current | `backend_not_ready` | NOT REACHED |
| 12 | `_revalidate_precommit` :704 (:546-591) | No app/router/middleware/args/auth drift between snapshot and commit | `installation_ownership_changed` | NOT REACHED |
| 13 | Route mount :706-711 | `app.include_router(components.router)` - the ONLY mount of the control router | (mutation begins) | NOT REACHED - this is why the app served inert |
| 14 | Broker middleware :712-719, hooks :720-726, OpenAPI reset :727 | Verified-broker gate and adapter hooks installed and verified | `hook_install_unverified` | NOT REACHED |

The crash-set (native_install.py:1003-1009) deserves its own row: any receipt in `{installation_cleanup_unconfirmed, factory_cleanup_unconfirmed, installation_busy, installation_owner_mismatch, hook_target_occupied, control_client_occupied, installation_ownership_changed}` - or a declined receipt with leaked state - makes `selected_serve` RAISE instead of serving. TS4's `installation_unavailable` with a clean pre-mutation rollback is precisely the non-crash path: the server stayed healthy and served ordinary traffic for ~22 minutes.

## 4. The gate that failed - scheduler-side worker bootstrap (stage inventory)

Reached from gate 10: the Occ3ControlReq is broadcast to all 8 TP ranks (image `request_receiver.py:232-241`); each rank runs `handle_occ3_control` -> `bootstrap_worker` (latent_worker.py:819-849 at `64492e8b1`) -> `install_worker` (:359-366) -> `WorkerRuntime.__init__` (:59). The stage markers (latent_worker.py:70-115 at `64492e8b1`, added by `64492e8b1`; before that commit the stages existed only as the local `stage` variable with no logging):

| Stage (latent_worker.py at `64492e8b1`) | Checks | TS4 status |
|---|---|---|
| `radix_cache` :72-74 | `scheduler is not None and type(scheduler.tree_cache) is RadixCache` | **FAIL (root-verified)** - production default is `UnifiedRadixCache`, a sibling class; `LatentError("profile")` on every rank |
| `kv_layout` :75-76 | DSA KV pool geometry/dtype on the live pool | NOT REACHED (static config values match per probes E4/E7) |
| `model_config` :79-82 | `glm_moe_dsa` / `GlmMoeDsaForCausalLM`, hidden 6144, 78 layers, fp8 | NOT REACHED (static-verified matching) |
| `model_class` :83-84 | Target model class validation | NOT REACHED |
| `parallel_layout` :87-90 | `(tp, dp, pp, attn_cp, attn_dcp, attn_tp) == (8, 1, 1, 1, 1, 8)`, group identity | NOT REACHED (static-verified matching) |
| `speculative` :91-95 | EAGLE/NEXTN with topk 1; no lora/dp-attention/disaggregation | NOT REACHED (static-verified matching) |
| `embeddings` :96-99 | Input embedding on CUDA with an allowed dtype | NOT REACHED |
| `dist_init` :100-104 | Torch distributed initialised; world size 8 | NOT REACHED |
| `streams` :107-111 | Scheduler/model-runner streams present on the runtime device | NOT REACHED |
| `dtype` :112-115 | Embedding dtype in the allowed set | NOT REACHED |
| post-init consensus :124-131 | All-rank gather; identical layouts; digest consensus | NOT REACHED |

Every NOT-REACHED stage is a live-state check that no static probe could fully certify (decline-map section 5a); at TS4 only the first stage ran, and its condition was root-verified false. The stages after `radix_cache` remain LIVE-PENDING on any repaired boot - the install-gate fix (d2ed1123b) admits `UnifiedRadixCache` at `radix_cache`, so the next live boot exercises `kv_layout` through `dtype` for the first time.

## 5. The per-request data-path gate

Independent of the install chain: `_occ3_validate_received` (patch `patches/scheduler-cache-ipc.v1.json`, scheduler.py replacement 2 at `64492e8b1` - the state baked into the TS4 image; image `scheduler.py:2820`) opens with the same `type(self.tree_cache) is not RadixCache` check and aborts descriptor-carrying requests with `occ3_unsupported_profile` via `_occ3_reject_received` (image `scheduler.py:2806-2812`). At TS4 no descriptor request ever arrived (the install had declined), so this gate never fired live; its condition is root-verified false in the production runtime. Repair COMMITTED as `99b2ebdc9` (08:45:03Z: the admission overlay accepts `type is RadixCache or type is UnifiedRadixCache`; observed landing during this report's writing); its live exercise remains LIVE-PENDING.

## 6. Controller-side service gates (the `service_initial` gate, committed at `d2ed1123b`)

Single-shot proofs, no retries (deploy/v3/service_checks.py:1-5 at `d2ed1123b`): `check_health` :123-128 (liveness only), `check_occ3_identity` :130-154 (C30's first gate: POST the identity route; 404 -> `occ3_routes_absent`, non-200 -> `occ3_identity_unavailable`, 200 must be the full tuple with expected_ranks==8 / ready_ranks==8 / all three capability flags true -> `occ3_identity_malformed` otherwise), `check_chat` :157-180, `check_tools` :183-222, `check_auth` :225-239, `check_ingress` :242-262 (through neo-k3-ingress only).

TS4 live status: health, chat, tools, ingress all PASS live on the inert candidate (the ordinary service worked); `occ3_identity` FAIL live with `occ3_routes_absent` - by design the first gate to abort the window (BUG-LEDGER.md, W4-A1). The phase-split design's `occ3_declined` evidence branch (identity 503 -> probe `GET /occ3/v1/status` -> fail fast with the exact decline code) is IN FLIGHT in the working tree at writing (deploy/v3/service_checks.py, controller.py, rehearsal.py, native_package.py and the server-side occ3_sglang edits including a new untracked startup_state.py; uncommitted, observed 08:50Z); see ROUTE-REGISTRATION.md in this directory.

## 7. Decline-path taxonomy reference

The complete enumeration - all 42 serve-time decline outcomes (function, stage, receipt code, exact trigger, C30 logging, TS4-reachability) plus the 9 prepare-stage codes, every `InstallationError` raise with line, the non-InstallationError exceptions that funnel into `installation_unavailable`, the TS4 elimination table, and the exact log-grep patterns - is the decline-map report: `C:/Users/openclawuser/Optimus-Assistant/profile/workspace/occ3fix-audit/occ3fix-decline-map.md` (sha256 `ee946b8f32cf415c2319c35d13108c8625cfca4588038bd10a44f021ad5bf99e`). Its elimination logic held: every code except `installation_unavailable` was excluded by direct in-image evidence, and its top-ranked sub-cause (a fast worker-side refusal collapsed by the scheduler catch-all) is what the root verification confirmed as the URC rejection. The six C30 logging choke points it documents are, at `d2ed1123b`: success INFO native_install.py:737-738; install decline ERROR :782-787 (detail token since `71e449783`); prepare not-selected INFO :954-955; prepare InstallationError ERROR :970-971; prepare generic ERROR :976-978 (detail token since `71e449783`); selected_serve decline echo :1001-1002. The worker-side decline line (since `64492e8b1`) is latent_worker.py:121-122 (at `64492e8b1`).

## 8. LIVE-PENDING (what only a live window can prove)

1. A boot on the repaired image: the `radix_cache` stage passing with `UnifiedRadixCache`, and the never-exercised stages `kv_layout` through `dtype` plus the post-init all-rank consensus running for the first time on the real GPU stack (repair commits `d2ed1123b` + `99b2ebdc9`).
2. The mount actually happening (route registration, broker middleware, hooks) and the identity route answering 200 with the full validated tuple.
3. The data-path gate accepting a real descriptor-carrying request on the unified cache (repair committed as `99b2ebdc9`; the live exercise is pending).
4. GATE 8 (live_before/live_after) - never started in any window.

Window TS5 requires fresh user authorisation; no live success is claimed in this report.
