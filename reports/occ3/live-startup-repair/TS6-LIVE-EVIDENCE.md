# TS6 Live-Window Evidence - the frontend gate repaired, the frontier at kv_layout

**Status: rolled back by design at the worker-side kv_layout gate; production restored and verified.
Two live windows now bracket the repair: TS5 declined at the frontend gate, TS6 passed it.**

Attempt `occ3-live-20261022t114708z` (2026-10-22 12:02Z-12:35Z, Ollama GLM 5.3 executor,
supervised v3 controller). Candidate image `sha256:5aa0148351dd...` from commit `9bfaa96e0`;
build digest gates all matched (native_sources `80760b2f...`, patched scheduler `81db4aee...`,
native_install `32e3a6aa...`). Evidence bundle `occ3-live-evidence-20261022t114708z.tar.gz`
(sha256 `708b57c99bae089010b407cf1c332984b6d3a2f4d4264f77a0043555fc8f9a97`, remote and
local mirror verified equal); full report `LIVE-PHASE-EXECUTION-REPORT-TS6.json`
(sha256 `205d7cbac166efd931b52c6ac9891ced15d76c4276055ec2dd5426876901cc37`).

## Proven live in this window

1. **The TS5 root cause is fixed in production shape.** `preflight_reached=true`, then the
   pre-swap disposable TP1 boot (Phase L) declined with `detail=frontend_members=tp_size`
   exactly - the device member absent. Auto-detected CUDA launches pass the frontend gate.
2. **The TS4 root cause is fixed live: all 8 TP ranks passed `radix_cache`.** The decline stage
   moved past it for the first time across all five windows.
3. **All-rank worker dispatch works**: every rank answered the profile init (`TP0..TP7`
   decline lines at the same instant).
4. The fail-safe path worked a fifth time: controller fail-fast at `occ3_identity`,
   verify-then-stop recovery, original restored (`RESTORED verified=true`), end-to-end chat
   200 with exact nonce match through the public origin.

## The new frontier: kv_layout under EAGLE speculative decoding

All 8 ranks declined `stage=kv_layout code=latent_profile` at 12:27:29. `validate_kv_layout`
pins the target pool geometry as `layer_num == 78, start_layer == 0, end_layer == 77,
len(kv_buffer) == 78`. Production launches with `--speculative-algorithm EAGLE
--speculative-adaptive`; the boot log shows TWO pools per rank (145.95 GB target + 2.17 GB
draft). sglang 0.5.21's `_compute_model_num_layers` (model_runner_components/layer_setup.py)
excludes the MTP predictor layer from the target when a draft worker exists: the target owns
layers 0..76 and the draft owns layer 77. The pinned 78-layer geometry therefore cannot hold
on any EAGLE/NEXTN launch - the sub-check fails before the profile's design question is even
reached: the captured method is `statebridge.glm53.final-prenorm-predictor...` at
layer_index 77, which is exactly the layer EAGLE's draft owns. TS7 must either pin the
speculative geometry deliberately (77 target layers, capture on the last target-owned layer
or a draft-inclusive read path) or disable speculative decoding for OCC-3 serving; both need
a design decision, which is why no blind patch was attempted.

## Window timeline

12:02 Phase 0 PASS (all staged digests on-DGX; fence retired; 53/53 files byte-identical) ->
12:05 Phase R PASS (10/10 rehearsal scenarios, `REHEARSAL-RESULT-6.json` `2c588f86...`) ->
12:10 Phase 1 PASS (build receipt ok, digest gates matched) -> 12:13 Phase L PASS (the
decisive `frontend_members=tp_size` line, control plane 503/200/401) -> 12:15 Phase 2
(parity PASS, artifact-proof PASS, approval consumed once; candidate served 12:16-12:28) ->
12:27 all-rank kv_layout decline -> 12:28 controller exit, recovery RESTORED (verified,
cleanup complete, helpers settled) -> 12:35 Phase 4 evidence bundle pulled and hash-verified
both sides.

Production: original `c4a26b345ec6a...` back as `neo-glm53`, restart policy `no`, RestartCount
0, health 200; ingress/traefik untouched; failed candidates and images preserved as evidence.
