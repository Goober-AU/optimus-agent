# SGLang Source Audit (v0.5.21)

**Worktree:** `optimus-occ3-20261021` · **Date:** 2026-10-21 · **Mode:** offline documentation from existing evidence; no live claims.

**Citation convention.** `occ3-campaign/<path>` refers to the campaign evidence root `C:/Users/openclawuser/Optimus-Assistant/profile/workspace/occ3-campaign/`; worktree files are repository-relative. Full hashes in the evidence register. The audited source tree is `occ3-campaign/deps/sglang-v0.5.21`; all file:line references below are quoted from the audit document, which was produced by targeted reading (no whole-repo greps).

## 1. Audit scope and target

The audit mapped the exact SGLang v0.5.21 source against the deployed runtime: GLM-5.3 FP8 (`GlmMoeDsaForCausalLM`, a DeepseekV2-style architecture), hidden 6144, TP8, EAGLE adaptive speculative decoding, `fp8_e4m3` KV cache, radix cache on by default, FastAPI http_server in the main process, port 30001 [E-AUDIT, E-ENV].

Executive finding of the audit: an OCC-3 v1 is feasible on this runtime with zero source patches and no server restart, via (a) `positional_embed_overrides` + `cache_salt` on ordinary `/generate` requests for the latent data path, and (b) the built-in plugin framework for the control-plane HTTP routes [E-AUDIT].

## 2. Findings that shaped the design

### 2.1 The two embedding-injection APIs

- `input_embeds` (full input replacement): hard-requires `--disable-radix-cache` (the tokenizer manager raises `input_embeds is provided while disable_radix_cache is False`). The deployed server has radix on, so this path would need a restart and would disable all prefix caching globally. Rejected for OCC-3 v1 [E-AUDIT].
- `positional_embed_overrides` (scatter over chosen positions): no extra server flag; works with radix cache ON. Base embeddings are computed from the real token ids and latent rows are scattered over absolute positions; injection is prefill-only; decode clears input embeds. Not directly JSON-settable from remote HTTP (the value contains a torch tensor), so only an in-process engine API or a plugin-side route can set it [E-AUDIT].

### 2.2 Cache behavior for override requests

- Lookup (read side) is disabled for override requests: the same token ids with different override vectors must not share cached KV, so an override request always full-prefills [E-AUDIT].
- Insert (write side) is NOT disabled: KV computed with injected latents is stored under the request's plain token ids, so a later normal request with the same token ids could silently reuse latent-corrupted KV. This is the real contamination risk [E-AUDIT].
- In-tree mitigations: `cache_salt` (namespaces the radix tree from the root; salted entries never share nodes with default-namespace traffic), `extra_key` (second namespacing lever), per-request `skip_radix_cache_insert`, and env force-miss [E-AUDIT].

### 2.3 Plugin framework (no http_server patch needed)

- Discovery via setuptools entry points group `sglang.srt.plugins`, optional `SGLANG_PLUGINS` env whitelist; hooks via `HookRegistry` (BEFORE/AFTER/AROUND/REPLACE on any fully-qualified function, with patch propagation) [E-AUDIT].
- Load points: main process at launch and every scheduler process (so hooks run on all TP ranks and must stay rank-consistent) [E-AUDIT].
- Route registration without patching: the FastAPI `app` is a module global created at import time; a plugin executed in the main process before uvicorn serves can `app.include_router(...)`; existing in-tree precedent for `include_router` exists. Constraints: `tokenizer_worker_num` must be 1 (deployed default) and the Rust server mode bypasses FastAPI (not this runtime) [E-AUDIT].

### 2.4 Radix cache structure

- `RadixKey` = token id sequence + optional `extra_key` + optional `cache_salt`; matching is exact token-id prefix compare; with EAGLE, keys become bigram views (draft KV packed in the same allocator row set) [E-AUDIT].
- `cache_salt` namespaces the whole tree from the root: the child key becomes `((extra_key, cache_salt), plain)`, so entries under different salts never share nodes [E-AUDIT].
- Eviction LRU by default; locked nodes protected; session refs can pin KV against eviction [E-AUDIT].
- The radix tree is the GPU-resident mailbox: latents live in KV rows shared zero-copy between sender and receiver requests [E-AUDIT].

### 2.5 EAGLE/MTP capture point

- The target's hidden states of accepted tokens are exactly `logits_output.hidden_states[accept_index]` from the verify forward; the recommended capture point is an AROUND/AFTER hook on `run_eagle_verify` reading `batch_result.logits_output.hidden_states` + `accept_index`, on GPU, every verify round [E-AUDIT].
- Capture hazard: outputs of CUDA-graph replay live in graph-owned static buffers and are overwritten by the next replay — any capture hook must clone (or async D2H) on the forward stream [E-AUDIT].
- Adaptive speculative decoding changes draft width at runtime; capture hooks must not assume a fixed tree width [E-AUDIT].

### 2.6 KV transfer primitives reusable in-server

Retrieve (`match_prefix` -> KV slot indices, zero copy), insert paths, eviction, lock/pin refs, request-to-token pool remapping, CPU offload/restore, host-pool backup/restore for retraction, the `state_capturer` pinned-host async D2H pattern, and the multimodal embedding-cache precedent. Disaggregation transfer machinery (NIXL/Mooncake) is cross-server PD machinery, not needed intra-server [E-AUDIT].

## 3. The two arms considered

| Dimension | Arm A: StateBridge-prefix | Arm B: KV-radix-channel |
|---|---|---|
| Space mapping | crosses hidden->embedding (Procrustes; raw cos ~0.24) | none — stays in native fp8 KV space |
| Per-receiver cost | full prefill every time (lookup disabled) | prefix hit, suffix only |
| Spec decode | receiver OK after prefill; capture must clone graph buffers | draft KV transfers natively |
| Verification | none built in | `cached_tokens` (deterministic) |
| Contamination | must salt or skip-insert | salt namespace is the mechanism |
| Portability | payload bytes cross-server | same-server only; LRU-evictable window |
| Payload size | ~1.5 MB fp32 for [64, 6144] | ~0 over the wire (slot indices) |
| Known fragility | AVP gates: 61%->19% at 500+ tokens; alignment is task-dependent | eviction/hit-rate risk |

Research recommendation: Arm B (KV-radix-channel) as the v1 primary for this runtime — zero space-mapping error by construction, cheapest at scale, EAGLE-native, verifiable via `cached_tokens`, zero-patch and no restart — with Arm A's machinery retained as the portable/cross-model fallback arm and latent-export substrate [E-LATENT].

## 4. What the campaign built on top of the audit

- The SGLang-side extension `extensions/sglang-occ3/` (plugin package `occ3-bridge`, entry point `occ3_bridge`), with the control-plane routes documented in BRIDGE-API.md [E-EXTREADME].
- The campaign later moved from "no source patch" to explicitly reviewed, raw-hash-pinned source overlays for the native path: `patches/capture-eager-mtp.v1.json`, `patches/native-startup.v1.json`, `patches/scheduler-cache-ipc.v1.json`, applied through the `deploy.v3` packaging path against the attested original SGLang source snapshot [E-EXTREADME, E-PATCH1, E-PATCH2, E-PATCH3].
- Source attestation of the running SGLang sources was performed (read-only scope, archive digest `d70dc7b1...`; no import or model inference): the comparison covered 59 audited files, of which 51 were present in the running tree and all 51 present files matched the local snapshot; 8 audited paths were absent from the running tree (e.g. `models/glm_moe_dsa.py`, `speculative/eagle_worker.py`) [E-ATT, E-ATT2].
- The plugin's own status text keeps the proof boundary explicit: salted prompt replay and radix-cache hits are a cache-reuse experiment, not proof of native latent communication; a `cached_tokens` count, HTTP 200, health result or imported plugin cannot establish that a receiver used a sender's hidden representation [E-EXTREADME].

## 5. Honest status of the audit

- The audit is source-reading evidence about v0.5.21 as pinned in `deps/sglang-v0.5.21`, cross-checked against the deployed runtime's recorded configuration. It is not a behavioural measurement on the deployed server.
- No capture, injection or KV handoff has been executed on the live server under the current approval; the live server currently has no `/occ3/v1/*` routes at all [E-FA1, E-STATE].
- The campaign's own remaining-blockers list states that actual installed worker GPU all-rank state capture/injection/readiness and live native/semantic proof are unexecuted [E-NSO].

## 6. PENDING LIVE EVIDENCE

1. A live demonstration on the deployed server of the salted-radix channel: sender KV insert under `occ3-<channel>` salt, receiver `match_prefix` hit, `cached_tokens == carrier_len` verification.
2. A live capture-hook run under EAGLE adaptive decoding with the required clone-on-forward-stream discipline.
3. Radix hit-rate / eviction behaviour under real agent traffic (the audit names this an open empirical question) [E-LATENT].
4. Salted bigram-key matching stability when adaptive speculation changes widths mid-session [E-LATENT].
5. FP8-KV round-trip stability across many receivers [E-LATENT].
6. The source overlays applied and verified against the exact deployed image (offline dry-run packaging verified; not installed, not imported at runtime) [E-STATE].

## Evidence register

| Id | Path | sha256 |
|---|---|---|
| E-AUDIT | occ3-campaign/research/sglang-audit.md | e48d08488fa65f9bc664f5aba30cedd6f3dd38ec3bcbfbebe14e4878efdb3049 |
| E-LATENT | occ3-campaign/research/latent-methods.md | be2ddecfdc855bfcc7aa1fbe59dec091b136424f4435af97caa5f059ec82b749 |
| E-ENV | occ3-campaign/dgx-inspect/ENVIRONMENT.md | e0667968a38430e9359084242fe0553f0571fad2cdca4a4a4f4a08a345e0afb7 |
| E-FA1 | occ3-campaign/dgx-inspect/OCC3-DEPLOY-FAILURE-ANALYSIS.md | 084be196931164e17293ab3f60241cad8105c4c57a47b8d3894b278d945173d9 |
| E-STATE | occ3-campaign/v3-controlled-deploy/STATE.json | 19a497e2c5682ccd2f03cd6dc364b02ca8e93f8b29dd2cc29939853cd0e5223d |
| E-NSO | occ3-campaign/v3-controlled-deploy/native-source-offline-result.json | d845f80816706122f54a29a51e177e38e4b0f2a9ec711ad258e96e4bbfe9da79 |
| E-ATT | occ3-campaign/v3-controlled-deploy/source-attestation-result-v2.json | 0ae4671372518c9fadb131881f55a2628f501d8555eee18f870186de021d1b53 |
| E-ATT2 | occ3-campaign/v3-controlled-deploy/source-attestation-comparison-v2.json | a529f1b6972d361bce36d3e6a14ec4a339e0029c88c7ca07d8e68ce9018dd0ea |
| E-EXTREADME | extensions/sglang-occ3/README.md | 3fe79fadd0a9e5e55cac27e1feca1ee7431d71ec53579f141a920ac9bd454702 |
| E-PATCH1 | extensions/sglang-occ3/patches/capture-eager-mtp.v1.json | be161cfd2d43aa6f2136a32bee907e68c2938fd552097742214c909bda393961 |
| E-PATCH2 | extensions/sglang-occ3/patches/native-startup.v1.json | 851d78fcd1311f5bfdb9be44cd8941f1dc026ed695f1e97a16bfc4248af6d048 |
| E-PATCH3 | extensions/sglang-occ3/patches/scheduler-cache-ipc.v1.json | e07c4b84407733a277f7135a017d0e505e275846beedd28f68a303ab97fe275c |
