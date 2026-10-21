# OCC-3 Model Compatibility

**Worktree:** `optimus-occ3-20261021` · **Date:** 2026-10-21 · **Mode:** offline documentation from existing evidence; no live claims.

**Citation convention.** `occ3-campaign/<path>` refers to the campaign evidence root `C:/Users/openclawuser/Optimus-Assistant/profile/workspace/occ3-campaign/`; worktree files are repository-relative. Full hashes in the evidence register.

## 1. The rule

OCC-3 is same-exact-model only. Two agents may use OCC-3 only when their `Occ3ModelIdentity` values compare equal component by component; cross-model handoffs never use OCC-3 [E-OCC3TYPES, E-OCC3NEG]. Identical display names prove nothing: the connector is responsible for verifying identity fields against the live runtime, never trusting a JSON declaration [E-OCC3TYPES]. The `models.json` capability carries `sameModelOnly`, which must be true for v1 [E-PIAITYPES].

## 2. Identity fields

`Occ3ModelIdentity` (eleven canonical fields) [E-OCC3TYPES]:

| Field | Example for the deployed runtime |
|---|---|
| `model_id` | `glm-5.3` |
| `architecture` | `GlmMoeDsaForCausalLM` |
| `checkpoint_revision` | `aca966e4e02791568aa6a4ced368624b3d897f42` |
| `weight_fingerprint` | 141-shard fp8 checkpoint fingerprint (see section 4 scope) |
| `tokenizer_fingerprint` | tokenizer identity |
| `chat_template_fingerprint` | chat-template identity |
| `quantisation` | `fp8` |
| `tensor_geometry` | hidden 6144, 78 hidden layers, head_dim 192 |
| `kv_layout` | DSA-style compressed KV, EAGLE bigram keys |
| `latent_method` | runtime-implemented latent method version |
| `runtime` | engine name + version (`sglang-0.5.21`) |

The fingerprint is `sha256` over the canonical JSON of these fields; the digest algorithm is shared with an independently attested Python mapping, and a cross-language fixture plus a unit test pin the expected digest and reject delimiter-concatenation collisions (changing any single field changes the digest; `"a|b"` + `"c"` never collides with `"a"` + `"b|c"`) [E-OCC3TYPES, E-NSO].

## 3. Enforcement points

1. Negotiation ladder step 3: sender and receiver identities must compare equal in every component, and the peer advertisement's `model_fingerprint` must equal the sender's computed fingerprint; any difference (including same model id, different checkpoint) is `ModelIdentityMismatch` fallback [E-OCC3NEG].
2. The receiver's advertisement must include the exact method [E-OCC3NEG].
3. Connector-side: `model_identity()` is "verified live ... never trusted from configuration alone", and the receiver's connector re-checks binding (receiver, model fingerprint, generation, expiry) at consume time [E-OCC3CONN].
4. Native admission: `inference_attachment` checks complete exact model identity (fingerprints are consistency checks, not substitutes for full identity comparison); the inbox retains only leases bound to the authenticated receiver [E-OCC3INF, E-NIA].
5. `models.json`: `sameModelOnly: true` required for v1; `enabled` additionally requires `validation = live-verified`, which itself requires a successful live capability handshake [E-PIAITYPES].

Offline tests pin the rule: same model id with a different checkpoint revision is rejected; a different model id is rejected; a mismatched advertisement fingerprint is rejected [E-OCC3NEG, E-TBO].

## 4. Verified identity of the deployed runtime (read-only evidence)

Collected read-only from the running deployment on 2026-10-21 [E-MIMV3, E-ARTMETA, E-ORIGSAN]:

- Container `neo-glm53`, id `c4a26b345ec6a5348ad3cab4554e5f58399b83bd23cf581bb5858a979e3c1927`, image `sha256:b1259f3ea3275f66237c498ea388919729018bc9f01c3d638391e06e2cf3f469` (lmsysorg/sglang, 52.4 GB; SGLang 0.5.21) [E-ENV].
- Served model `glm-5.3`, model path and tokenizer path `/data/neo-glm53/model`; launch flags include `--tp-size 8`, `--context-length 1048576`, `--kv-cache-dtype fp8_e4m3`, EAGLE adaptive speculative decoding, reasoning effort max [E-ENV, E-ORIGSAN].
- Architecture `GlmMoeDsaForCausalLM`, model type `glm_moe_dsa`, hidden size 6144, 78 hidden layers, quant method fp8 [E-MIMV3].
- Checkpoint: 141 safetensors shards (152 files total in the model directory), revision `aca966e4e02791568aa6a4ced368624b3d897f42` from model-revision/download metadata; per-shard download etags are sha256-expected-content values [E-MIMV3, E-ARTMETA].
- `model_config_sha256`: `3ac72612095574542f7fff847ada8e59d9199dd8af44bdf625d7e02615572e69` [E-MIMV3].
- Expected cross-language model identity fingerprint for this runtime: `sha256:db2c8830820da8bd3370bbfacf0799e33085ef7b8b1ca27840b88e3e8101b732` (metadata and protocol conformance only; no GPU/readiness proof) [E-NSO, E-STATE].

**Scope limit of this evidence (stated in the metadata itself):** the checkpoint evidence covers existing download metadata and file sizes only; no shard content checksum was recomputed [E-MIMV3]. Identity is therefore verified to download-metadata depth, not byte depth.

## 5. Why the rule is strict (external evidence)

Cross-model latent transfer is the documented dominant failure mode of latent communication:

- OLMo3 KV-transfer across a model boundary retained only 55.1% — raw KV bytes moved across a model boundary are not a durable channel (supplied by the prior StateBridge arm, recorded in the latent-methods research) [E-LATENT].
- AVP (Agent Vector Protocol) quality gates measure projected-latent accuracy collapsing with prompt length: same-family HumanEval 61% at short prompts, 40% mid-length, 19% past 500 tokens [E-LATENT].
- LatentMAS's KV path is same-model only with no layout/vocab negotiation in-repo; its realignment matrix is a hyperparameter, not a universal map; MoE residual streams and FP8 quantisation add calibration risk for weight-space projections [E-LATENT].

The campaign's design response: the primary v1 arm (KV-radix channel on the same server) crosses no representation space at all — same weights, same TP topology, same fp8 recipe, same server — so the receiver consumes exactly the KV it would have computed itself [E-LATENT].

## 6. Compatibility dimension summary (from the SGLang audit)

Identity must also cover runtime-level dimensions, not just weights: two different TP shardings or fp8 recipes of the same checkpoint would wrongly pass a config-only hash [E-LATENT]. The audit additionally records that EAGLE draft KV is packed in the same allocator rows as target KV and keys become bigram views, so a compatible receiver must run the identical speculative configuration; and that `input_embeds` style full replacement would require `--disable-radix-cache`, which the deployed server does not have [E-AUDIT].

## 7. PENDING LIVE EVIDENCE

1. A live `model_identity()` verification against the deployed server (no production connector construction or registration exists in the audited daemon paths; the global registry starts empty) [E-NIA, E-CONNMOD].
2. Full-content weight verification (recomputed shard checksums) — current evidence is download-metadata-depth only [E-MIMV3].
3. A live same-model pair completing negotiation and one live rejection of a mismatched pair.
4. Live verification that the deployed runtime's serving configuration (EAGLE adaptive, fp8 KV) matches the identity tuple the connector attests.

## Evidence register

| Id | Path | sha256 |
|---|---|---|
| E-OCC3TYPES | crates/pi-coding-agent/src/core/occ/occ3/types.rs | 336cdcd4bd6005f8ca2a80365ad4c77f9a2a7b8b73b54941584092056566f79a |
| E-OCC3CONN | crates/pi-coding-agent/src/core/occ/occ3/connector.rs | 19248778d0eb0f006d35384ed575b467eb482d1326b58f91a76b84975403888f |
| E-OCC3NEG | crates/pi-coding-agent/src/core/occ/occ3/negotiation.rs | 1956cd91398fa334cf553b4de0cbbfb285b2aca3010e0915c7c856b9cc83a8e7 |
| E-OCC3INF | crates/pi-coding-agent/src/core/occ/occ3/inference.rs | 788c9625ceab9079c9533450a1ee4938f4cef95f7a2e5b31eadfab8b90d33498 |
| E-PIAITYPES | crates/pi-ai/src/types.rs | 4c606e40ce7ffdb2de09a3a272f84b64d39e81a843f36485571ad216c247219e |
| E-TBO | crates/pi-coding-agent/tests/occ3_bridge_offline.rs | acaa9b8b5a3648c124953b71c07e26efaf8bab2141febbf78bce2868151428bf |
| E-CONNMOD | crates/pi-coding-agent/src/core/occ3_connectors/mod.rs | 0ea5a2bb555fa10f2f5659f7904f5c1ca392b80526fff3232a3d65d11e4b91f8 |
| E-MIMV3 | occ3-campaign/v3-controlled-deploy/model-identity-metadata-v3.json | 11bc9ce88cd24a6cb8dd8473be7fa31f7975658257cf90e3ade9be2273265467 |
| E-ARTMETA | occ3-campaign/v3-controlled-deploy/artifact-metadata.json | 08287cb9acd2a6852acaef154f1d26ce39e886c1995542c0822e468ace0bc141 |
| E-ORIGSAN | occ3-campaign/v3-controlled-deploy/original-sanitized.json | b1e15312a042f39bb2620139b7de5251517f242feb46bc571cf81fbb1cae89f4 |
| E-ENV | occ3-campaign/dgx-inspect/ENVIRONMENT.md | e0667968a38430e9359084242fe0553f0571fad2cdca4a4a4f4a08a345e0afb7 |
| E-NSO | occ3-campaign/v3-controlled-deploy/native-source-offline-result.json | d845f80816706122f54a29a51e177e38e4b0f2a9ec711ad258e96e4bbfe9da79 |
| E-STATE | occ3-campaign/v3-controlled-deploy/STATE.json | 19a497e2c5682ccd2f03cd6dc364b02ca8e93f8b29dd2cc29939853cd0e5223d |
| E-NIA | occ3-campaign/v3-controlled-deploy/NATIVE-INFERENCE-ARCHITECTURE.md | 0afb026dbf2d6329fd4c50b03265bd46a28b647c12881b063c42f2ea64087651 |
| E-LATENT | occ3-campaign/research/latent-methods.md | be2ddecfdc855bfcc7aa1fbe59dec091b136424f4435af97caa5f059ec82b749 |
| E-AUDIT | occ3-campaign/research/sglang-audit.md | e48d08488fa65f9bc664f5aba30cedd6f3dd38ec3bcbfbebe14e4878efdb3049 |
