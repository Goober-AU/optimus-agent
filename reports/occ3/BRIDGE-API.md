# OCC-3 Bridge API

**Worktree:** `optimus-occ3-20261021` · **Date:** 2026-10-21 · **Mode:** offline documentation from existing evidence; no live claims.

**Citation convention.** `occ3-campaign/<path>` refers to the campaign evidence root `C:/Users/openclawuser/Optimus-Assistant/profile/workspace/occ3-campaign/`; worktree files are repository-relative. Every cited file is listed with its sha256 in the evidence register. All APIs below are offline-verified only; the live SGLang control routes are not deployed (the live server returns 404 for `/occ3/v1/*`) [E-FA1].

## 1. Surfaces

The OCC-3 bridge API has five surfaces:

1. The Rust generic bridge (`core/occ/occ3`): types, connector trait, registry, negotiation, envelope codec.
2. The `models.json` capability schema (`Occ3Capability` in `pi-ai/src/types.rs`).
3. The SGLang plugin control-plane HTTP API (`extensions/sglang-occ3/occ3_sglang/api.py`).
4. The native inference attachment wire (request body field `occ3`, response header `x-optimus-occ3-receipt`).
5. The native source-capture wire (request body field `occ3_capture`, response header `x-optimus-occ3-source`, control routes under `/occ3/v1/native/`).

## 2. Rust generic bridge (offline-verified)

### 2.1 Module exports [E-OCC3MOD]

- `codec`: `build_envelope`, `decode_envelope`, `encode_envelope`, `render_preview`, `OCC3_HEADER`.
- `connector`: `CaptureRequest`, `ConsumeRequest`, `Occ3Connector` (plus native enrollment/publication types).
- `negotiation`: `advertisement_from_capability`, `negotiate_occ3`, `Occ3CapabilityAdvertisement`, `Occ3Decision`, `Occ3FallbackReason`, `Occ3NegotiationInput`.
- `registry`: `Occ3ConnectorRegistry`.
- `types`: `Occ3ConnectorCapabilities`, `Occ3DeliveryStatus`, `Occ3Envelope`, `Occ3Error`, `Occ3HandleRef`, `Occ3Method`, `Occ3ModelIdentity`, `Occ3TransferMetrics`, `OCC3_MAX_SUMMARY_CHARS`.

### 2.2 Envelope wire format [E-OCC3CODEC, E-OCC3TYPES]

- Line: `OCC/3 ` followed by one-line JSON of `Occ3Envelope`.
- `Occ3Envelope` fields: `schema` (fixed `optimus.occ3.v1`), `protocolVersion` (1), `messageId`, `senderSessionId`, `receiverSessionId`, `modelFingerprint`, `runtimeId`, `connectorId`, `latentMethod` (`statebridge-prefix` or `kv-handoff`), `stateHandle` (`Occ3HandleRef`), `controlSummary` (bounded, `OCC3_MAX_SUMMARY_CHARS = 2048`), `fallbackReference`.
- `Occ3HandleRef` fields: `handleId`, `connectorId`, `modelFingerprint`, `runtimeGeneration` (u64), `expiresAtEpoch` (u64), `stateSizeBytes` (u64). Opaque by contract: no CUDA pointers, tensor bytes, file paths or credentials.
- Transport budget: the agent-message string bound is 16384 chars; the negotiation ladder estimates 1200 chars of scaffolding plus the control summary and falls back (never truncates) beyond it [E-OCC3NEG, E-DMESS].
- Decode semantics: non-OCC-3 text returns `None` (normal fallback classification); malformed OCC-3 returns `Err` (counted as a strike); unknown schema or protocol version are errors.

### 2.3 Negotiation input and result [E-OCC3NEG]

`Occ3NegotiationInput`: `enabled`, `sender_identity`, `receiver_identity`, `peer_occ3` (`Occ3CapabilityAdvertisement`), `method`, `registry`, `max_envelope_chars`, `control_summary_chars`.

`Occ3CapabilityAdvertisement` (the OCC-3 slice of a peer's `OccCapability`, serde-defaulted for older peers): `supported: bool`, `model_fingerprint: Option<String>`, `connector_id: Option<String>`, `methods: Vec<Occ3Method>`. Older workers never emit it; senders fall back to OCC-1/OCC-2 [E-DMESS].

`Occ3Decision`: `Occ3 { method, connector_id, model_fingerprint }` or `Fallback { reason }` with reasons: disabled-by-policy, sender/receiver capability absent, model identity mismatch, no connector, envelope budget exceeded.

### 2.4 `Occ3Connector` trait (control plane only) [E-OCC3CONN]

| Method | Meaning |
|---|---|
| `connector_id()` / `protocol_version()` | Stable id (e.g. `sglang-occ3`) and OCC-3 protocol version |
| `health()` | Cheap live probe, no latent state created |
| `discover_capabilities()` | Live capability snapshot; `Ok` means the runtime answered and its identity was checked |
| `model_identity()` | Verified live identity; never trusted from configuration alone |
| `capture_latent(CaptureRequest)` | Create a server-side latent state; returns an opaque handle |
| `deliver_latent(&handle)` | Authorise the handle's bound receiver after the bridge re-verified identity |
| `consume_latent(ConsumeRequest)` | Legacy connector-only consumption hook; must never stand in for actual native inference |
| `acknowledge_delivery(&handle)` | Bridge-verified semantic-use acknowledgment |
| `release_handle(&handle)` | Immediate server-side resource release |
| `shutdown()` | Invalidate all handles this connector issued |
| `supports_native_inference()` | Whether this implementation can prepare an actual request-bound native attachment (default false) |
| `enroll_native_session` / `prepare_source_capture` / `publish_native_source` / `retire_native_source` / `prepare_inference` | Native path; all default to `NativeInferenceUnsupported` — capability flags alone cannot opt in |

`CaptureRequest`: `message_id`, `sender_session_id`, `receiver_session_id`, `method`, `capture_context` (small, never tensors), `ttl`. `ConsumeRequest`: `handle`, `receiver_session_id`, `injection_target` [E-OCC3CONN].

### 2.5 Native inference admission [E-OCC3INF, E-NIA]

- `VerifiedInferenceRuntime`: fresh connector-verified complete model identity, access domain/runtime id, generation, methods.
- `PrepareInferenceRequest`: envelope, actual request/session/receiver identity, target, caller-supplied clock.
- `inference_attachment` checks complete exact model identity, envelope/handle consistency, receiver, runtime access domain/generation, expiry and method; fingerprints are consistency checks, not substitutes for full identity comparison.
- `Occ3InferenceInbox`: bounded process-local admission journal; rejects conflicting duplicate ids; claims only ids in the actual native context; refuses replay after ambiguous or already admitted work; one unresolved attachment per receiver; no automatic post-admission text fallback.

## 3. `models.json` capability schema [E-PIAITYPES]

`ModelDefinition.occ3: Option<Occ3Capability>` (camelCase serde, skip-if-none). `Occ3Capability` fields:

| Field | Type / value | Notes |
|---|---|---|
| `protocol` | string, `"occ3-v1"` | `OCC3_PROTOCOL_V1` |
| `connector` | string, e.g. `"sglang"` | Resolved by the generic bridge to a registered connector implementation |
| `endpoint` | string (HTTPS) | Connector base endpoint |
| `apiVersion` | `"v1"` | `OCC3_API_VERSION_V1` |
| `sameModelOnly` | bool, default true | Must be true for v1 |
| `enabled` | bool | Activation gate |
| `validation` | `unverified` / `documentation-verified` / `live-verified` | Only `live-verified` may activate latent communication |
| `fallback` | optional list, e.g. `["occ2", "occ1", "text"]` | Preferred fallback chain |

Model-scoped on purpose (same pattern as `NativeCompactionCapability`): a provider base URL or display name is not evidence that a route hosts a latent connector [E-PIAITYPES, E-REG]. No model in any shipped catalog carries an `occ3` block in this worktree state; normal OCC-3 remains disabled until live gates pass [E-FA1, E-STATE].

## 4. SGLang plugin control-plane HTTP API [E-APIPY]

Routes are mounted under `/occ3/v1` on the same FastAPI app as `/generate`, so the server-level api-key middleware covers them (auth level NORMAL; nothing under `/health` is touched). All routes are control-plane only: no tensors, GPU state or model weights in requests or responses.

| Route | Method | Current behaviour |
|---|---|---|
| `/occ3/v1/status` | GET | Plugin name/version, `enabled: false`, model fingerprint, channel counts, metrics snapshot |
| `/occ3/v1/identity` | GET | `optimus.occ3.identity.v1` identity fields + fingerprint + field order |
| `/occ3/v1/capabilities` | GET | Capability advertisement shaped for the Rust `Occ3CapabilityAdvertisement`: `supported: false`, `reason: "native_latent_receiver_not_implemented"`, experimental baselines listed (`text-radix`, `source-prompt-kv-handle-v1`), `same_model_only: true`, verification `cached_tokens`, salt prefix `occ3-`, eviction `lru` |
| `/occ3/v1/send`, `/recv`, `/release` | POST | `410 legacy_unbound_api_disabled_use_opt_in_handles` — the unbound legacy API is disabled |
| `/occ3/v1/channels` | GET | `available: false`, reason `legacy_unbound_api_disabled` |

The fail-closed state is deliberate: the installed plugin's source-prompt KV routes are an experimental text-plus-radix baseline; a detached `/recv` generation is not a native attachment, so the Rust connector reports state operations unsupported until the request-bound receiver adapter and its receipt exist [E-SGLANGRS, E-EXTREADME].

## 5. Native inference attachment wire [E-NIA]

Request: the ordinary OpenAI-compatible chat/completions payload plus body field `occ3`, added only when a negotiated native resolver supplies a lease:

```
protocol_version: 1
request_id
message_id
connector_id
receiver_session_id
model_fingerprint
runtime_id
runtime_generation: u64
state_handle
expires_at_epoch: u64
method
```

Response: private header `x-optimus-occ3-receipt`, bounded strict JSON, ceiling 2048 bytes:

```
{ "attachment": { <the exact typed attachment> },
  "consumed": true, "fallback_used": false, "injected_state": true }
```

Rules: the attachment path disables redirects and automatic HTTP retries; lease states are Prepared / Submitted / Consumed / Cancelled / Uncertain and are never replayed as text; native core requires a consumed lease before executing returned tools; header validation alone is not consumption — successful finish and exact DONE termination are also required; the receipt header is removed from generic response/logging hooks and must not appear in public evidence.

## 6. Native source-capture wire [E-SPD, E-NSPR]

- Request: distinct negotiated body field `occ3_capture`, schema `occ3.capture.v1`, 13 exact snake_case fields (schema, capture_id, source_session_id, source_request_id, turn_nonce, model_fingerprint, runtime_id, runtime_generation, method, retained_count_limit, max_capture_bytes, expires_at_epoch, span_policy). `retained_count_limit` in 1..128; positive `max_capture_bytes`; span policy fixed `whole_completed_response_tail_v1`; no optional unknown fields.
- Response: private header `x-optimus-occ3-source`, schema `occ3.source.v1`, 21 fields, ceiling 2048 bytes: intent_digest, source_handle (<=128 ASCII), native_rid / response_id / capture_commit_id (<=96), accepted_span_start/end (u64, end > start), retained_count (1..128, <= span), predictor_position_offset fixed -1, representation fixed `final_layer_prenorm_predictor`, verified layer_index / hidden_dim / dtype (enum bfloat16|float16|float32) / layout_digest (64 lowercase hex), ready_ranks / expected_ranks (ready == expected), accepted_sequence_digest, output_sha256, expires_at_epoch, byte_count. A maximum-length fixture serialises to 1241 ASCII bytes [E-NSPR].
- Digest rules: output digest over the exact uncompressed native response body bytes (SSE bytes including terminal frames, or nonstream body), independent of network chunk boundaries; server hashes native uncompressed bytes; the native source path drains the finite buffered body through clean EOF, hashes raw decoded bytes before framing, and requires valid normal finish plus DONE before a usable source lease or handoff tool dispatch [E-SPD, E-NSPR].
- Zero-inference native control routes under `/occ3/v1/native/`: `sessions/enroll`, `publish`, `publication/status`, `retire` — authenticated, strict bounded typed bodies, backend-atomic and idempotent; unknown/lost reply never falls back or issues a new key [E-SPD].
- Byte-count units: `byte_count` is the logical continuous prefix representation size `retained_count * hidden_dim * element_size(dtype)` for ONE complete [N,d] prefix, not multiplied by TP ranks; `max_capture_bytes` bounds the aggregate live retained and staged tensor bytes across all participating TP ranks. Neither wire field alone is a physical GPU-memory proof [E-SPD].
- Initial profile: rejects simultaneous source capture and receiver consume on a single inference; a source published by `occ3_handoff` must have exactly one parsed tool call in total (the eligible handoff); one handoff plus any ordinary tool is refused [E-SPD].

## 7. Implemented vs unimplemented (honest matrix)

| Capability | Code | Offline tests | Live |
|---|---|---|---|
| Envelope codec + negotiation ladder + mock connector | Yes | Yes (16 bridge tests; 401 aggregate owner executions) [E-TBO, E-NSO] | Not deployed |
| models.json occ3 schema + validation | Yes | Yes | No model entry enabled |
| SGLang plugin control routes (fail-closed) | Yes | Yes | Not installed on DGX (404) [E-FA1] |
| Rust SGLang connector health/identity diagnostics | Yes | Yes | No live handshake recorded |
| Native inference attachment (one-shot lease, receipt contract) | Yes (generic Rust) | Yes | No server-side adapter; not live |
| Native source capture/publication wire | Yes (contract + offline adapters) | Yes | Not live |
| Terminal request-bound SSE proof server-side | Designed, not implemented | No | No [E-NIA, E-NSPR] |
| Daemon sender/receiver admission wiring | Absent | No | No [E-NIA] |

## 8. PENDING LIVE EVIDENCE

1. Any successful call to `/occ3/v1/*` on the deployed server (currently 404 with valid auth) [E-FA1].
2. A live `x-optimus-occ3-receipt` / `x-optimus-occ3-source` header produced by the real runtime.
3. Receipt/attachment behaviour under the full native output default (131072 tokens; 262144 ceiling) — current bounded response handling does not prove compatibility with those budgets [E-EXTREADME, E-STATE].
4. End-to-end `occ3` body-field attachment into a real receiver inference (GATE 8).

## Evidence register

| Id | Path | sha256 |
|---|---|---|
| E-OCC3MOD | crates/pi-coding-agent/src/core/occ/occ3/mod.rs | b419cf1538939de18ba2a041dba9080b3afdf60e6eae5107d13cea18ca61a8c0 |
| E-OCC3TYPES | crates/pi-coding-agent/src/core/occ/occ3/types.rs | 336cdcd4bd6005f8ca2a80365ad4c77f9a2a7b8b73b54941584092056566f79a |
| E-OCC3CONN | crates/pi-coding-agent/src/core/occ/occ3/connector.rs | 19248778d0eb0f006d35384ed575b467eb482d1326b58f91a76b84975403888f |
| E-OCC3CODEC | crates/pi-coding-agent/src/core/occ/occ3/codec.rs | 344d9f1fad53b77cb3846a13b5c2fafd399f6941c50c1fb1fd6b0a7a7717ae11 |
| E-OCC3NEG | crates/pi-coding-agent/src/core/occ/occ3/negotiation.rs | 1956cd91398fa334cf553b4de0cbbfb285b2aca3010e0915c7c856b9cc83a8e7 |
| E-OCC3INF | crates/pi-coding-agent/src/core/occ/occ3/inference.rs | 788c9625ceab9079c9533450a1ee4938f4cef95f7a2e5b31eadfab8b90d33498 |
| E-PIAITYPES | crates/pi-ai/src/types.rs | 4c606e40ce7ffdb2de09a3a272f84b64d39e81a843f36485571ad216c247219e |
| E-APIPY | extensions/sglang-occ3/occ3_sglang/api.py | e45b3b77c6fbff798f67815ef5b992066e1f0900d114632bd28f89afb146a9b1 |
| E-SGLANGRS | crates/pi-coding-agent/src/core/occ3_connectors/sglang.rs | 64d8cb468687ec1c084d0aab02e89ff768eca18b1bb201dd0b3326755edd9250 |
| E-EXTREADME | extensions/sglang-occ3/README.md | 3fe79fadd0a9e5e55cac27e1feca1ee7431d71ec53579f141a920ac9bd454702 |
| E-TBO | crates/pi-coding-agent/tests/occ3_bridge_offline.rs | acaa9b8b5a3648c124953b71c07e26efaf8bab2141febbf78bce2868151428bf |
| E-NSO | occ3-campaign/v3-controlled-deploy/native-source-offline-result.json | d845f80816706122f54a29a51e177e38e4b0f2a9ec711ad258e96e4bbfe9da79 |
| E-NIA | occ3-campaign/v3-controlled-deploy/NATIVE-INFERENCE-ARCHITECTURE.md | 0afb026dbf2d6329fd4c50b03265bd46a28b647c12881b063c42f2ea64087651 |
| E-SPD | occ3-campaign/v3-controlled-deploy/SOURCE-PROTOCOL-DECISIONS.md | b90f77916a1126027b7db0a401fda6a6b4ad94bce3eee4f840be3b1e3e9dd5e4 |
| E-NSPR | occ3-campaign/v3-controlled-deploy/NATIVE-SOURCE-PROTOCOL-REVIEW.md | b822c770565495750d351a4b72e96e7917635027d8e20662b1529e012e3e4c6b |
| E-DMESS | occ3-campaign/research/daemon-messaging.md | 23bb1a85eaf0c9b3cfcd6962127400e9d464cf77452b86913ddfb8cffb1c8a25 |
| E-REG | occ3-campaign/research/registry.md | f864a8dfe9850ca2fb19ebf9534c51d139c12419f445be0a9f7b63d19f1530b0 |
| E-STATE | occ3-campaign/v3-controlled-deploy/STATE.json | 19a497e2c5682ccd2f03cd6dc364b02ca8e93f8b29dd2cc29939853cd0e5223d |
| E-FA1 | occ3-campaign/dgx-inspect/OCC3-DEPLOY-FAILURE-ANALYSIS.md | 084be196931164e17293ab3f60241cad8105c4c57a47b8d3894b278d945173d9 |
