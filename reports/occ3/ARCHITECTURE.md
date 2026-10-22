# OCC-3 Architecture

**Worktree:** `optimus-occ3-20261021` (branch `feat/occ3-generic-sglang-bridge`, 9 commits ahead of `origin/main` plus uncommitted offline-hardening edits) · **Date:** 2026-10-21 · **Mode:** offline documentation from existing evidence only; no live claims.

**Citation convention.** Evidence outside this repository is cited as `occ3-campaign/<relative path>` (root: `C:/Users/openclawuser/Optimus-Assistant/profile/workspace/occ3-campaign/`) with its sha256. Worktree files are cited by repository-relative path with the sha256 of the current working-tree state (includes the uncommitted edits). Full register at the end. Everything cited exists; nothing here is speculative.

## 1. Scope and honesty statement

This report describes the OCC-3 latent-communication architecture as it exists in the worktree at the time of writing: a generic, engine-agnostic Rust bridge with an SGLang connector and a Python SGLang-side extension, all verified offline only. No DGX deployment, no GPU state transfer, no live capability handshake and no semantic-fidelity measurement have been performed under the current approval [E-STATE]. Where a claim would require live evidence, this report says so explicitly in section 8.

The generic bridge passes offline tests without SGLang or DGX (mock connector), which is the offline portion of the mission's GATE 1 [E-NSO, E-TBO].

## 2. Position within the OCC family

OCC is a layered encoding family over one typed contract [E-ARCH]:

- OCC-0: ordinary English, always available, default for prose.
- OCC-1: compact structured English over the typed `AgentCommMessage` contract.
- OCC-2: symbolic dialects over the same contract, with strict decode and a conservative OCC-1 fallback.
- OCC-3: true latent communication between compatible local models. Hosted black-box APIs cannot support it.

The core design constraint, stated in the OCC module tree and repeated in the OCC-3 module: every level encodes the same typed `AgentCommMessage` payload, so task/result semantics do not fork per representation, and `contract.rs` needs no OCC-3 change [E-ARCH]. OCC-3 adds one more encode/decode of the frozen typed payload, not a new message lifecycle.

Campaign rules pinned for `core/occ/`: pure, deterministic, offline-testable; no network, no model APIs, no filesystem access; never drop contradictory evidence; never invent missing values; ordered iteration only [E-ARCH]. The consequence for OCC-3: the bridge core must stay pure and the SGLang/GPU connector must live behind a trait implemented outside that tree [E-OCC3MOD].

## 3. Generic bridge: `crates/pi-coding-agent/src/core/occ/occ3/`

The module doc declares the split [E-OCC3MOD]:

| Module | Role |
|---|---|
| `types.rs` | Model identity, handles, envelope, delivery status, error taxonomy |
| `connector.rs` | Versioned `Occ3Connector` trait; implementations live outside this tree |
| `registry.rs` | Live connector lookup with cached capability snapshots from real handshakes |
| `negotiation.rs` | Deterministic eligibility ladder; strict same-exact-model rule |
| `codec.rs` | Textual envelope that rides the ordinary agent-message string (same seam as OCC-2) |
| `mock.rs` | Deterministic fake connector for offline bridge tests; reference implementation |
| `inference.rs` | Request-bound native inference admission (verified runtime, inbox) |

### 3.1 Model identity and handles (`types.rs`)

- `Occ3ModelIdentity`: eleven canonical fields (model id, architecture, checkpoint revision, weight fingerprint, tokenizer fingerprint, chat-template fingerprint, quantisation, tensor geometry, KV layout, latent method, runtime). Two agents may use OCC-3 only when identities compare equal under the strict same-model rule; identical display names prove nothing [E-OCC3TYPES].
- `Occ3ModelIdentity::fingerprint()`: canonical-JSON sha256 over the eleven fields, shared with an independently attested Python mapping (cross-language fixture pins the digest algorithm; a test pins a fixed expected digest and rejects delimiter-concatenation collisions) [E-OCC3TYPES].
- `Occ3HandleRef`: opaque server-side state reference (handle id, connector id, bound model fingerprint, runtime generation, expiry, size class). By contract it never contains CUDA pointers, tensor bytes, file paths or credentials [E-OCC3TYPES].
- `Occ3DeliveryStatus`: Prepared, Captured, Admitted, Delivered, Consumed, Acknowledged, Failed, Expired, Uncertain [E-OCC3TYPES].
- `Occ3Method`: `statebridge-prefix` (continuous latent prefix in input-embedding space) and `kv-handoff` (KV-cache working-memory handoff) [E-OCC3TYPES].
- `Occ3Error`: twelve variants, each fallback-classified by the negotiation module (incompatible model, unauthorized, connector unreachable/timeout, handle expired/already consumed, runtime generation changed, transfer failed, native inference unsupported, inference outcome uncertain, disabled, unknown connector) [E-OCC3TYPES].

### 3.2 Connector contract (`connector.rs`)

`Occ3Connector` is an async trait; the trait surface never mentions tensors, GPU pointers, CUDA or any engine [E-OCC3CONN]. Methods:

- Control plane: `connector_id`, `protocol_version`, `health`, `discover_capabilities`, `model_identity`, `shutdown`.
- Legacy latent channel: `capture_latent(CaptureRequest)`, `deliver_latent(handle)`, `consume_latent(ConsumeRequest)`, `acknowledge_delivery(handle)`, `release_handle(handle)`.
- Native path (default-unsupported, fail-closed): `supports_native_inference()`, `enroll_native_session`, `prepare_source_capture`, `publish_native_source`, `retire_native_source`, `prepare_inference`. Every native default returns `NativeInferenceUnsupported`; no capability flag can enable them — legacy metrics-only connectors must not enter the admission path [E-OCC3CONN].

### 3.3 Negotiation ladder (`negotiation.rs`)

Deterministic gate order, each rejection surfaced as an `Occ3FallbackReason`, never silent [E-OCC3NEG]:

1. OCC-3 disabled by configuration or policy -> fall back.
2. Sender or receiver capability absent -> fall back.
3. Model identities differ in ANY component -> fall back (strict same-exact-model rule; no cross-model projection).
4. No connector registered with verified capabilities for the identity and method -> fall back.
5. Envelope budget exceeded (estimated 1200 chars scaffolding + control summary vs transport bound) -> fall back (never truncate).
6. All gates pass -> OCC-3 with the negotiated method.

### 3.4 Envelope codec (`codec.rs`)

- Wire form: `OCC/3 ` + one-line JSON; self-describing header so receivers can classify without a registry [E-OCC3CODEC].
- Envelope schema `optimus.occ3.v1`, protocol version 1; carries message ids, model fingerprint, runtime id, connector id, method, the opaque `Occ3HandleRef`, a bounded OCC-1 control summary (max 2048 chars) and a fallback reference [E-OCC3TYPES, E-OCC3CODEC].
- Decode is strict: `None` for non-OCC-3 text (callers fall back to normal classification), `Err` for malformed OCC-3 (a strike, not a silent fallback) [E-OCC3CODEC].
- The envelope never contains tensors: the control summary survives handle expiry so fallback never loses task identity [E-OCC3TYPES].

### 3.5 Connector implementations (outside the pure tree)

`crates/pi-coding-agent/src/core/occ3_connectors/` performs the I/O: a process-wide `Occ3ConnectorRegistry` cell populated by the embedding host only after a successful handshake; OCC-3 stays unavailable until then [E-CONNMOD]. The SGLang connector (`sglang.rs`, connector id `sglang-occ3`) currently reports native state operations explicitly unsupported and refuses the unsupported boundary instead of sending plaintext to `/send` or discarding a detached `/recv` answer; health and identity diagnostics remain, and never advertise native capability [E-SGLANGRS].

## 4. Transport integration

The envelope rides the ordinary agent-message string — the same seam OCC-2 uses — so sender identity, queueing, receipts, rate limits and family scoping are untouched [E-DMESS]. Constraints inherited from the transport audit: UTF-8 JSON inside a 16384-char bound; the at-most-once transport invariant means an OCC-3 handoff whose state lives GPU-side must treat `uncertain` as "the receiver may or may not have it"; handle liveness is connector-side, driven by delivery classification, not transport retries [E-DMESS].

## 5. SGLang extension: `extensions/sglang-occ3/`

A pip package (`occ3-bridge`) registering entry point `occ3_bridge` in the `sglang.srt.plugins` group [E-DEPREADME]. Status and proof boundary, in the extension's own words: the native hidden-state path is experimental and disabled by default; salted prompt replay and radix-cache hits are a separate cache-reuse experiment, not proof of native latent communication; a `cached_tokens` count, HTTP 200 or imported plugin cannot establish that a receiver used a sender's hidden representation [E-EXTREADME].

Intended native path (six steps): authorize the source against the authenticated broker and an explicit server-owned session enrollment; capture accepted target-model final-layer pre-normalization predictor states during the original native generation; retain bounded rank-local GPU state behind an owner-only opaque handle; publish an authorized one-shot message through the native family controller preserving uncertainty after a lost reply; align and inject a continuous prefix into one native receiver inference keeping private KV pages out of shared-cache reuse; verify matching native terminal output and backend evidence before making a source lease or receiver completion available to tools [E-EXTREADME].

Native installation requires reviewed raw-hash-pinned source overlays for the exact original SGLang source snapshot; the packaged overlay set in this tree covers capture/eager-MTP, native startup and scheduler cache IPC [E-EXTREADME, E-PATCH1, E-PATCH2, E-PATCH3].

## 6. Native inference attachment (generic Rust infrastructure)

The offline native-inference increment [E-NIA]:

- `pi_ai::inference_attachment`: `InferenceStateTarget`, bounded typed `InferenceAttachment` control metadata (never tensors or credentials), process-local one-shot `InferenceStateLease` (no serde), `StreamOptions.inference_state` serde-skipped so ordinary inference has no extra wire field.
- The OpenAI-compatible request gains body field `occ3` only when a negotiated native resolver supplies a lease; the attachment path disables redirects and automatic retries; lease states are Prepared, Submitted, Consumed, Cancelled, Uncertain (never replay as text); clones share one state; reconstructing a fresh lease from an old message is not a retry; native core requires a consumed lease before executing returned tools [E-NIA].
- Receipt contract: successful attached responses must carry private header `x-optimus-occ3-receipt` with bounded strict JSON echoing the exact typed attachment plus `consumed`, `fallback_used`, `injected_state`; 2048-byte ceiling; header validation alone is not consumption (successful finish and exact DONE termination are also required); this is an assertion from the authenticated target runtime, not cryptographic attestation or semantic-use acknowledgment [E-NIA].
- Source side: distinct negotiated `occ3_capture` body field and `x-optimus-occ3-source` completed-capture header (also 2048 bytes), zero-inference native control routes under `/occ3/v1/native/`, idempotent publication semantics, and byte-count definitions that separate logical prefix size from aggregate all-rank GPU bytes [E-SPD, E-NSPR].
- Receiver admission: `Occ3InferenceInbox` — bounded process-local admission journal that checks authenticated identities, rejects conflicting duplicate ids, claims only ids in the actual native context, refuses replay after ambiguous or already admitted work, and supports one unresolved attachment per receiver [E-NIA].

Known integration gaps recorded by the same document: daemon sender/admission wiring absent; the previously existing sender-helper fabricated envelope ids and would fail the strict receiver admission; terminal request-bound SSE proof is designed but not implemented server-side [E-NIA, E-NSPR].

## 7. Offline verification status

- `tests/occ3_bridge_offline.rs` (16 tests): full lifecycle capture -> envelope -> deliver -> consume -> acknowledge -> release with the mock connector, plus every fallback and failure edge; no SGLang, DGX, GPU or network [E-TBO].
- `tests/occ3_native_inference.rs` and `tests/occ3_source_handoff.rs`, and `examples/occ3_native_handoff.rs` (offline mode runs a synthetic native controller; 3 model requests, 1 admitted message) [E-NSO].
- Aggregate owner test executions in the frozen native-source increment: 401 passed, 0 failed, 2 ignored (aggregate executions; examples repeat integration fixtures, not 401 unique cases); final chain exit 0 in 304.9 s; production not enabled, no live calls, no settings changes, no keys generated [E-NSO].
- Root repository gate `bash scripts/check.sh`: exit 0, cargo workspace all targets passed, 282 packaging unit tests passed (10 skipped), native resource layout passed, zero new message/path warning candidates; existing repository warnings retained [E-STATE, E-ROOTCHECK].
- Latest offline v3 integration run (campaign log `root-nine-v3-integration.txt`): 489 tests passed, 1 warning, exit 0 [E-STATE].

## 8. PENDING LIVE EVIDENCE

The following architectural claims have no live evidence and must not be presented as working until produced:

1. Live connector handshake and capability discovery against the deployed SGLang server (registry is empty in production; no production connector construction or registration exists in the audited daemon paths) [E-NIA].
2. Actual GPU-resident capture/ownership and injection on the exact deployed model/runtime source; all-rank state; any claim of a true latent handoff (GATE 8 remains BLOCKED in campaign state) [E-STATE, E-NSO].
3. Native SGLang chat adapter and terminal request-bound proof, including full-turn buffering limits and cancellation [E-NIA, E-NSPR].
4. Deployed extension on the DGX: the live server currently returns 404 for `/occ3/v1/status` and `/occ3/v1/capabilities` (routes absent; plugin not installed); both 2026-10-21 deployment attempts failed and were rolled back [E-FA1, E-REC2].
5. Daemon sender/receiver admission wiring and real multi-agent native test (GATE 13).
6. Any semantic fidelity, benchmark or production performance numbers (GATEs 9-11).

## Evidence register

| Id | Path | sha256 |
|---|---|---|
| E-ARCH | occ3-campaign/research/arch.md | 616d1e2e77cc80548282001af5634edf1d963adf95216591d44ac5933b56f001 |
| E-DMESS | occ3-campaign/research/daemon-messaging.md | 23bb1a85eaf0c9b3cfcd6962127400e9d464cf77452b86913ddfb8cffb1c8a25 |
| E-STATE | occ3-campaign/v3-controlled-deploy/STATE.json | 19a497e2c5682ccd2f03cd6dc364b02ca8e93f8b29dd2cc29939853cd0e5223d |
| E-NSO | occ3-campaign/v3-controlled-deploy/native-source-offline-result.json | d845f80816706122f54a29a51e177e38e4b0f2a9ec711ad258e96e4bbfe9da79 |
| E-NIA | occ3-campaign/v3-controlled-deploy/NATIVE-INFERENCE-ARCHITECTURE.md | 0afb026dbf2d6329fd4c50b03265bd46a28b647c12881b063c42f2ea64087651 |
| E-SPD | occ3-campaign/v3-controlled-deploy/SOURCE-PROTOCOL-DECISIONS.md | b90f77916a1126027b7db0a401fda6a6b4ad94bce3eee4f840be3b1e3e9dd5e4 |
| E-NSPR | occ3-campaign/v3-controlled-deploy/NATIVE-SOURCE-PROTOCOL-REVIEW.md | b822c770565495750d351a4b72e96e7917635027d8e20662b1529e012e3e4c6b |
| E-FA1 | occ3-campaign/dgx-inspect/OCC3-DEPLOY-FAILURE-ANALYSIS.md | 084be196931164e17293ab3f60241cad8105c4c57a47b8d3894b278d945173d9 |
| E-REC2 | occ3-campaign/recovery-only/SECOND-DEPLOYMENT-RECOVERY.md | 5e0a9c06f4c9a5f8e91322fcb20258964a753e333d5d00351f0aef68390b80a7 |
| E-ROOTCHECK | occ3-campaign/v3-controlled-deploy/root-native-final-check.txt | 1a592bb82bc5f8b92b5f87c9bf9f4b90a04d20d1c723300b722469c814a8d447 |
| E-OCC3MOD | crates/pi-coding-agent/src/core/occ/occ3/mod.rs | b419cf1538939de18ba2a041dba9080b3afdf60e6eae5107d13cea18ca61a8c0 |
| E-OCC3TYPES | crates/pi-coding-agent/src/core/occ/occ3/types.rs | 336cdcd4bd6005f8ca2a80365ad4c77f9a2a7b8b73b54941584092056566f79a |
| E-OCC3CONN | crates/pi-coding-agent/src/core/occ/occ3/connector.rs | 19248778d0eb0f006d35384ed575b467eb482d1326b58f91a76b84975403888f |
| E-OCC3NEG | crates/pi-coding-agent/src/core/occ/occ3/negotiation.rs | 1956cd91398fa334cf553b4de0cbbfb285b2aca3010e0915c7c856b9cc83a8e7 |
| E-OCC3CODEC | crates/pi-coding-agent/src/core/occ/occ3/codec.rs | 344d9f1fad53b77cb3846a13b5c2fafd399f6941c50c1fb1fd6b0a7a7717ae11 |
| E-CONNMOD | crates/pi-coding-agent/src/core/occ3_connectors/mod.rs | 0ea5a2bb555fa10f2f5659f7904f5c1ca392b80526fff3232a3d65d11e4b91f8 |
| E-SGLANGRS | crates/pi-coding-agent/src/core/occ3_connectors/sglang.rs | 64d8cb468687ec1c084d0aab02e89ff768eca18b1bb201dd0b3326755edd9250 |
| E-EXTREADME | extensions/sglang-occ3/README.md | 3fe79fadd0a9e5e55cac27e1feca1ee7431d71ec53579f141a920ac9bd454702 |
| E-DEPREADME | extensions/sglang-occ3/deploy/README.md | c363a736f85530156553f42fb7bfff6b506c085e8dd5d305f4f71f0479d102c1 |
| E-TBO | crates/pi-coding-agent/tests/occ3_bridge_offline.rs | acaa9b8b5a3648c124953b71c07e26efaf8bab2141febbf78bce2868151428bf |
| E-PATCH1 | extensions/sglang-occ3/patches/capture-eager-mtp.v1.json | be161cfd2d43aa6f2136a32bee907e68c2938fd552097742214c909bda393961 |
| E-PATCH2 | extensions/sglang-occ3/patches/native-startup.v1.json | 851d78fcd1311f5bfdb9be44cd8941f1dc026ed695f1e97a16bfc4248af6d048 |
| E-PATCH3 | extensions/sglang-occ3/patches/scheduler-cache-ipc.v1.json | e07c4b84407733a277f7135a017d0e505e275846beedd28f68a303ab97fe275c |
