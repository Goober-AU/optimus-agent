# OCC-3 Live-Startup Repair - Disposable Boot Ladder Evidence (R0-R2)

**Status: R0 PASS, R1 PASS, R2 SKIPPED-MEMORY (disposable validation, production never touched).**
Executed 2026-10-22 09:37Z-10:10Z on the DGX by an Ollama GLM 5.3 executor against the preserved TS4 candidate image `neo-glm53:occ3-v3-live-20261022t0356z` (id `a31b23c0401b`) with the repaired package overlaid via read-only bind mounts (30/30 files sha256-verified against `OVERLAY-MANIFEST.json` `73028aca3278ebdb...` before any boot). Evidence bundle: `/data/occ3-smoke2/boot-ladder-evidence-20261022T093736Z.tar.gz` (sha256 `f720ea7ab71c7e99a20f4956897134add7ed4bd452f3887eadffbd9ed88e1bdf`, local mirror `occ3-campaign` workspace). Boots were named disposable containers on port 127.0.0.1:30010 with a throwaway 64-hex API key generated on the DGX (never the production key). Production `neo-glm53` (`c4a26b345ec6`) verified identical before and after every rung: Up since 2026-10-22T03:54:08Z, RestartCount 0, health 200.

## R0 - CPU-only attestation: PASS

`_read_startup_literal` -> SELECTION True (EXPERIMENTAL_STARTUP with the regenerated SOURCE_SHA256 `6041d374...`); `_verify_startup_artifacts` OK; `_verify_installed_sources` ATTESTED 96 (72 sglang_srt + 24 occ3_sglang, the full per-file digest check over both roots - the check that in TS4 ran only after the tp gate); `create_control_router()` routes exactly `(/occ3/v1/capabilities, GET), (/occ3/v1/native/runtime/identity, POST), (/occ3/v1/status, GET)`.

Attempt 1 exposed a staging uid mismatch (`runtime_unsafe` from `load_pinned_profile`): the bind-mount sources needed `chown root:root`. A staging fix, not a repair defect.

## R1 - TP1 control-plane boot (GPU 7): PASS

The decisive TS4 gap - the control plane - is closed live. With a tiny TP1 model the install declines by design at the tp gate, and the decline is now OBSERVABLE:

- `occ3-bridge: native installation decline (attempt=c04d1418 stage=install code=unsupported_frontend_profile)` plus the startup echo (docker logs 09:54:49; R1 attempt 3).
- `GET /occ3/v1/status` (valid key) -> 200, `state=DECLINED`, `code=unsupported_frontend_profile`, `stage=install`, `rollback_completed=true`, schema `optimus.occ3.status.v1`.
- `POST /occ3/v1/native/runtime/identity` (valid key) -> **503 `{"error":{"code":"not_ready"}}` with cache-control no-store - NOT the TS4 404**. This is the route-mounting proof.
- `GET /occ3/v1/capabilities` -> 200, `supported=false`, `methods=[]`, `reason=native_install_declined` (nothing advertised on decline).
- All three routes without the key -> 401 (original auth enforced).

Two harness-level fixes were needed before the clean attempt (mem-fraction below the configurator floor; head_dim 8 below flashinfer's 32 minimum) - both documented in the summary, neither touching the repair. The status `preflight_reached=false` is code-correct for this decline (the marker is set after `_runtime_preflight` returns; the tp gate raises inside it). A post-probe tiny-model warmup 500 occurred after all markers were captured (tiny-model artifact; full log kept).

## R2 - TP8 tiny boot: SKIPPED-MEMORY

Free VRAM measured 2100-2268 MiB per GPU (2.05-2.21 GiB) against the 3.1 GiB precondition - the marginal case. The single authorized attempt died in `PyNcclCommunicator` init (`NCCL error: unhandled cuda error`) on all 8 ranks before any occ3-bridge code ran (zero occ3-bridge log lines). No retry per the one-attempt rule. The worker-side stage marker (decline at a stage past `radix_cache`) therefore remains **LIVE-PENDING** until TS5; the URC repair itself is statically verified (production logs show `impl=UnifiedRadixCache` on all 8 ranks; the gate now accepts exactly that class; the scheduler admission patch digest verified end-to-end through the packaging pipeline).

## What R0+R1 prove, precisely

1. The regenerated attestation chain is self-consistent end to end (96 files, both roots, digest module).
2. The control-plane routes mount on the real image BEFORE native validation, behind the original API-key middleware.
3. A declined install is an authenticated, truthful, schema-valid DECLINED state - exactly the observability TS4 lacked (W4-A1).
4. The decline taxonomy (fixed codes, no secrets) survives to docker logs and HTTP.

What they do NOT prove: the mounted native runtime (needs TP8 + the real GLM-5.3), the worker-side stage progression past `radix_cache` (R2 skipped), and the GPU-resident latent handoff (GATE 8) - all TS5 scope.
