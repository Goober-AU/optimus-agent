# OCC-3 Production Integration Design for Optimus

Worker: occ3-prodesign. Worktree `improve/occ2-occ3-communication` (base v0.1.32). Design only; no code
changes. Every claim about existing behavior cites a file:line read in this pass
(core/occ/{mod,activity,contract}.rs, agent_messages.rs, agent_session/{native_subagents,subagent_runs}.rs,
prime-agent-runtime/src/rlm/__init__.py, research/integration/OCC2-INTEGRATION-DESIGN.md,
research/latent_handoff/RESEARCH-NOTES.md).

OCC-3 is latent communication between compatible local models, explicitly "not implemented" today
(crates/pi-coding-agent/src/core/occ/mod.rs:19-20). The research harness in research/latent_handoff/
(another worker, still iterating) is the empirical basis; its measured results decide production
integration. This document therefore has two halves: a go/no-go decision framework first (section 1), then
an integration design (sections 2-10) that stays safe to leave unimplemented for as long as the evidence
says no. No part of sections 2-10 may land before section 1 records GO.

## 0. Ground rules

- The daemon transport stays authoritative; sender identity is daemon-established, never trusted from
  message text (contract.rs:10-12). OCC-3 adds no identity surface.
- OCC levels are interchangeable encodings of one typed payload (mod.rs:7-8). For OCC-3, "encoding" means
  receiver consumption, not the wire: the wire body stays OCC-1 text in every case, and the latent artifact
  is a parallel adjunct the receiver's model may consume instead of re-prefilling that text. This is
  strictly less invasive than OCC-2, which substitutes the body string at the send seams
  (wrap_outgoing_body, agent_messages.rs:749).
- The occ/ Rust tree stays pure, deterministic and offline-testable: no network, no model APIs, no
  filesystem access inside those modules (mod.rs:36-38), and delta/consolidation/prune are frozen
  (mod.rs:30-34). All OCC-3 runtime code (process management, tensors, IPC) lives outside that tree, the
  boundary the research notes already fixed (RESEARCH-NOTES.md:399-403).
- The text body always travels (the OCC-2 invariant "the message is never lost, altered or truncated",
  OCC2-INTEGRATION-DESIGN.md:72-74; agent_messages.rs:747-749). Every latent failure degrades to today's exact behavior.
- Non-goals: no change to queueing, steering, rate limits, receipts, family scoping or the Python OCC-1
  kernel codec; no new mandatory dependency anywhere; no GPU requirement.

## 1. Go/no-go decision framework

Evidence source: the harness protocol v2 (experiment A, RESEARCH-NOTES.md:330-349; experiment B,
RESEARCH-NOTES.md:351+). First measured results are 0/10 in every condition including the full-text
control, diagnosed as probe defects rather than concept failure (RESEARCH-NOTES.md:301-320). The framework
therefore separates probe validity (V gates) from the decision itself (G criteria), and the thresholds are
pre-registered here before the harness reruns, so no threshold is tuned to the results.

### 1.1 Validity gates (all must hold before any comparison counts)

- V1 positive control: the full-text condition reaches >= 80% (the harness's own "gate all handoff claims
  on F >= 8/10" for the 10-trial smoke, RESEARCH-NOTES.md:311-315; protocol v2 runs 100 seeded trials,
  RESEARCH-NOTES.md:340). If the receiver cannot answer with the answer in plain text, nothing downstream
  measures handoff.
- V2 determinism: two repeat runs of one seeded condition agree trial-for-trial (greedy, fixed seeds,
  thread count recorded; RESEARCH-NOTES.md:378).
- V3 specificity: the correct cache beats the perturbed-cache conditions - zeroed, Gaussian,
  covariance-preserving, Haar rotation, shuffled order, unrelated task, truncated; the condition list at
  RESEARCH-NOTES.md:343-347 - with non-overlapping paired bootstrap CIs. A win that also survives cache
  destruction is not evidence about cache content.

### 1.2 Decision criteria

Measured on the pinned pairs the harness uses (SmolLM2-135M-Instruct revision
12fd25f77366fa6b3b4b768ec3050bf629380bac, then Qwen2.5-0.5B-Instruct; RESEARCH-NOTES.md:288-292):

- G1 accuracy (primary, required): with-cache accuracy >= OCC-1-text accuracy minus 5 percentage points,
  AND with-cache > no-cache with a paired bootstrap CI at 95%. This is the harness's own pre-registered gate
  (RESEARCH-NOTES.md:341-342); production only adds the OCC-1-text condition, already in the harness
  condition list (RESEARCH-NOTES.md:346-347), as the comparison baseline.
- G2 latency (one optional path): at transferred contexts >= 4096 tokens, receiver wall-clock for the
  with-cache condition (transfer + receiver prefill of its own turn) <= 0.8x the full-text condition
  (receiver re-prefills the whole context) on >= 90% of trials. Receiver latency and artifact bytes are
  harness measures (RESEARCH-NOTES.md:348-349). At small scale there is no latency case: the measured
  deepcopy is 9.4 ms and a 40-token cache is 1.89 MB against ~200 text bytes (RESEARCH-NOTES.md:301-302,
  :388), so accuracy (G1) or unique capability (G4) must carry.
- G3 resource envelope (required): sidecar peak RSS <= the 1 GiB default budget and per-handoff cache bytes
  <= the 128 MiB cap while G1/G2 hold. Peak RSS is a harness measure (RESEARCH-NOTES.md:349). Cost anchors:
  45 KiB/token fp32 for SmolLM2 and 24 KiB/token for Qwen2.5-0.5B (RESEARCH-NOTES.md:292-293), so a
  4096-token context is ~90-180 MB fp32 - the caps are the binding constraint, not the model.
- G4 unique capability (the other optional path): the transferred context exceeds the text transport bound
  (DEFAULT_AGENT_MESSAGE_MAX_CHARS = 16,384 chars, agent_messages.rs:57), and with-cache accuracy on
  over-bound contexts beats the truncated-text baseline by >= 10 percentage points. This is the only
  criterion that can justify integration without G2, because it moves work the text channel cannot do.

### 1.3 Branches

- GO: V1-V3 plus G1 plus G3 plus (G2 or G4) on at least one pinned pair. Integrate per sections 2-10. The
  feature flag still ships default OFF (section 9); GO authorizes implementation, not enablement.
- NO-GO: V1-V3 hold but G1 fails, or neither G2 nor G4 holds. Stay research-only. Negative results are
  documented in research/latent_handoff/RESULTS-*.md with the exact criterion numbers; the module doc keeps
  saying "not implemented" (mod.rs:19-20); this design is parked. Revisit triggers: a stable cross-instance
  cache API in transformers, a trained receiver head (the Interlat pattern, RESEARCH-NOTES.md:193-195), or
  a concrete production need for over-bound context transfer.
- INVALID: any V gate fails. That is a harness defect, not a decision; fix the probe (the three known
  defects at RESEARCH-NOTES.md:309-320 are exactly this class) and rerun. No decision is drawn from an
  invalid run.

Recording: the decision is a dated addendum to this file listing the result artifacts (the harness results
JSON paths) and the branch taken. No code path reads or encodes the decision.

## 2. Architecture: the latent backend sidecar

Components:

- L1 Rust daemon-side `LatentHandoffManager`: owns the feature flag, sidecar lifecycle, eligibility,
  resource caps and receipts. Coordination only; no model calls, no tensors.
- L2 Python sidecar process (latent backend), one per daemon: loads the pinned model, serves the
  sender/receiver inference turns for eligible sessions, owns every tensor.
- L3 The activity receipt seam (section 6).

Reuse vs a dedicated process: reusing the per-session Python REPL kernels is rejected. The kernel is the
agent's model-facing workspace bridged by host_request (rlm/__init__.py:115-131); loading torch plus a 0.5B
model into every session kernel would multiply RSS by session count, couple the agent tool environment to a
heavy optional stack, and leave model state inside each agent's own execution environment. A single
dedicated process starts lazily, dies independently, and is supervised like other daemon-owned local
resources: the RLM child machinery already demonstrates the required discipline - admission and disposed
checks (subagent_runs.rs:190-250), execution (subagent_runs.rs:485), settlement and cleanup
(subagent_runs.rs:660-835), cancellation (subagent_runs.rs:985), abort wiring (subagent_runs.rs:364-370).
Decision: one dedicated sidecar per daemon, lazy start on the first eligible request, idle shutdown after
15 minutes.

Properties:

- CPU-first: the harness itself runs fp32 CPU, greedy, seeded (RESEARCH-NOTES.md:299); no GPU code path in
  v1.
- Optional dependencies: the sidecar probes `import torch` / `import transformers` at startup. Missing =>
  it reports unavailable with a reason; the manager drops latent capability; ordinary installs never
  install torch and never crash (section 9).
- v1 eligibility scope: both the sender turn and the receiver turn of a handoff must be served by the
  sidecar itself on the same pinned model. Hosted and gateway-served models expose request/response
  surfaces with no KV-cache access - mod.rs:19-20 already rules out hosted black-box APIs, and the
  campaign's local gateway (127.0.0.1:43120) is OpenAI-shaped, i.e. the same class of black box. Cross-stack
  handoff is out of scope.
- The sidecar never writes durable session state; transcripts, prompts and receipts stay daemon-side.

## 3. Model identity verification and session isolation

`ModelDescriptor`, agreed by daemon and sidecar and carried in every handoff offer:

- model id and revision (git sha; e.g. the pinned SmolLM2 revision above, RESEARCH-NOTES.md:288-290);
- config fingerprint: architecture, layer count, num_kv_heads (GQA: 3 KV vs 9 query heads on SmolLM2,
  RESEARCH-NOTES.md:283), head_dim, rope_theta, dtype (the values that fix KV tensor shapes,
  RESEARCH-NOTES.md:288-292);
- tokenizer fingerprint: sha256 over the canonical tokenizer.json;
- runtime fingerprint: transformers version plus a cache format id ("dynamic-cache-v1", the DynamicCache
  API of the pinned stack, RESEARCH-NOTES.md:209-211).

Verification rules, all before any handoff:

1. Sender and receiver descriptors must be byte-equal (model id + revision + tokenizer fingerprint +
   config fingerprint + cache format). Any mismatch means no latent offer at all; the text message already
   sent is the entire communication. No partial matching, no "close enough".
2. Cache format compatibility is an allowlist in the manager: (transformers version, cache format id)
   pairs the sidecar build was validated against. The 4.x -> 5.x migration broke cache APIs wholesale
   (removed to_legacy_cache/from_legacy_cache, cache_position, tuple caches; RESEARCH-NOTES.md:260-277), so
   caches are never assumed portable across stack versions.
3. The sidecar re-verifies the descriptor at consumption time (defense in depth for reload/upgrade races;
   section 7).

Session isolation:

- Latent state never crosses sessions. The handle registry is keyed (receiver_session_id, handle_id); an
  offer names its single receiver, and any other session presenting the handle is rejected.
- No raw pointers or tensor bytes in durable records, ever. The daemon-side record gains at most
  content-free metadata: {latent_handle_id, descriptor digest, cache_bytes, sha256, transferred,
  consumed}. Handles are ids; tensors live only in sidecar process memory.
- Process death discards all latent state by design; daemon restart never attempts recovery. Latent state
  is never durable, so resume/compaction paths never see it.
- A session's handles die with the session: session dispose releases handles, following the settled-run
  cleanup discipline (subagent_runs.rs:660-835).

## 4. Handoff protocol: the Rust/Python boundary

Sequence (v1, same-process sidecar - no cache bytes cross a process boundary at all):

1. The sender's turn completes on the sidecar at a chat-template turn boundary. Mid-turn caches make
   instruct models continue the sender's turn (RESEARCH-NOTES.md:284-285, :336); the sidecar snapshots
   {session_id, descriptor, cache_len, turn_closed} and refuses to snapshot otherwise.
2. The sender's message travels the normal seams completely unchanged: S1, the kernel host request
   `agent_message.send` (direct arm at agent_messages.rs:1393-1397; broadcast arm at
   agent_messages.rs:1302-1306) and S2, child spawn (subagent_runs.rs:555-571). Key inversion vs OCC-2: the
   latent layer is NOT in the send seam. The manager issues the offer after (or in parallel with) the text
   send, so no latent step can delay, drop or reorder the message.
3. For an eligible receiver (section 5) the manager sends the sidecar a `HandoffOffer`: {message_id,
   sender_session, receiver_session, descriptor, truncation policy}. The sidecar deep-copies the cache (any
   forward with use_cache extends the passed cache in place; RESEARCH-NOTES.md:274-275 - the harness's own
   defect 2, RESEARCH-NOTES.md:316-320, is the failure mode), applies the truncation policy (default: keep
   the sender turn's own tokens, mirroring the sequential_info_only / latent_only semantics,
   RESEARCH-NOTES.md:75-78), and registers a handle.
4. The sidecar replies {handle_id, cache_bytes, checksum, seq_len} or a typed error.
5. On the receiver's next model call through the sidecar, the manager passes the handle id. The sidecar
   re-verifies the descriptor, splices cache + receiver turn (attention mask width = cache_len + q_len,
   RESEARCH-NOTES.md:276; position ids derive from the cache length and are never reused,
   RESEARCH-NOTES.md:277), generates, and emits a consumption receipt {message_id, consumed: true,
   prefill_tokens_saved}.
6. Receipts flow to the activity seam (section 6). No receipt, no LAT.

Cross-process / serialized form (specified now, built only when needed): cache bytes move through a shared
temp file under the daemon data dir with restrictive permissions, a sha256 checksum, a size check before
read and delete-after-load; control traffic is type-keyed JSON-RPC over stdio with ok/error replies, the
same shape as the kernel host bridge (rlm/__init__.py:115-131). Cache bytes never travel through the
message transport or durable records.

Resource limits (settings keys, defaults):

- maxCacheBytesPerHandoff: 128 MiB, enforced before the offer (45 KiB/token fp32 SmolLM2 means a
  4096-token context is ~180 MB; RESEARCH-NOTES.md:292-293).
- maxTotalCacheBytes (sidecar budget): 1 GiB, LRU eviction across handles.
- maxConcurrentTransfers: 2 (transfers are CPU/memory-bound at the measured 9.4 ms deepcopy scale,
  RESEARCH-NOTES.md:301-302).
- handoffTimeoutMs: 10,000; expiry is a typed error and text fallback (the text is already delivered).
- maxHandlesPerSession: 4. Sidecar idle shutdown: 15 minutes.

Cleanup:

- Session end: release that session's handles (dispose discipline, subagent_runs.rs:660-835).
- Sidecar crash: heartbeat loss invalidates every handle; the manager restarts the sidecar only on the
  next eligible request (lazy).
- Daemon restart: all latent state is gone by design (process memory only); no recovery, no agent-visible
  warning beyond the next capability listing.
- Cancellation during transfer: the manager cancels through the same signal discipline tool calls use
  (tokio::select! on signal.cancelled(), native_subagents.rs:69-71). The offer is abandoned, the handle
  discarded, and the text message is unaffected because the text path never waited on the latent path.

## 5. Capability negotiation

- The family listing's `AgentSessionMessageAgentSummary` (agent_messages.rs:159-192) gains one optional
  `latent_capability` field styled exactly like `occ_capability` (agent_messages.rs:187-191): absent means
  "reports nothing", which yields no offers. Shape: {available: bool, model: {id, revision}, runtime:
  sidecar build id, cache_format}.
- Daemon-side `LatentSendPolicy` is analogous to `Occ2SendPolicy` (agent_messages.rs:676-686): enabled flag
  plus eligibility plus the descriptor. Eligibility = flag on AND both sessions' current model calls are
  served by the sidecar on the identical pinned descriptor AND the receiver session is alive. Anything
  missing => no offer, zero behavior change.
- Conservative default OFF: settings key `agentCommunication.occ3Enabled`, mirroring
  `agentCommunication.occ2Enabled` (flag read at subagent_runs.rs:71-77). Absent or unknown => OFF.
- Only eligible same-model local pairs, per the ground rule that latent transport needs compatible local
  models (mod.rs:19-20). Cross-model transfer (Interlat's qwen2llama results, RESEARCH-NOTES.md:185-186)
  requires a trained receiver head and stays out of scope (OQ3).

## 6. Telemetry: a real LAT signal

Current state: `CommunicationKind` reserves `Latent` for OCC-3 latent transports, "never inferred from
text" (activity.rs:23-24). classify(text) is purely text-driven (activity.rs:47-56); the pinned tests
assert that "latent vector" classifies as Text (activity.rs:117-125, the literal at :122). The projection
scans the last 128 admitted messages, dedupes, and caps at 32 deliveries (activity.rs:11-12, :75-88), and is
content-free by contract (activity.rs:1).

Change shape (described, not coded):

- `classify` and `classify_with_registry` are UNCHANGED: Latent stays unreachable from text, and the
  pinned tests (activity.rs:117-125) keep passing byte-for-byte.
- `from_admitted_messages_with_registry` (activity.rs:82-88) gains one optional parameter: a bounded map
  of latent receipts keyed by message id, populated by the daemon from sidecar confirmations
  ({message_id, transferred, consumed} - content-free). A delivery reports Latent ONLY when both hold:
  (a) the backend confirmed the transfer (handle registered, checksum verified), and (b) the backend
  confirmed receiver consumption (a forward pass ran with that cache, for that receiver session, for that
  message id). Otherwise the kind is exactly what the text says (Occ1/Symbolic/Text): a delivered but
  unconsumed latent offer never reports Latent.
- Content-free: receipts carry ids and booleans only - no tensor metadata, no prompt content, no model
  internals - matching the content-free projection contract (activity.rs:1).
- Session-scoped: only receipts whose receiver session matches the projected session are consulted.
- Bounded: the receipt map is capped (LRU, 256 entries) and rides inside the existing 128-message scan and
  32-delivery bounds (activity.rs:11-12).

## 7. Failure and fallback matrix

Universal rule first: the text body has already traveled the normal seams (section 4, step 2). No latent
failure may delay, drop, alter or truncate it; every row degrades to exactly today's behavior.

| Failure | Detection | Required behavior |
|---|---|---|
| Runtime unavailable (sidecar absent or deps missing) | startup probe or spawn failure | capability absent; no offers; text only; one log line per daemon run, not per message |
| Model unload/reload in sidecar | descriptor mismatch at offer or consumption | invalidate affected handles; no offer; text only; log model ids |
| Receiver restart | session gone or handle receiver mismatch | handle invalid; no LAT; renegotiate from the next capability listing |
| Sender termination mid-offer | offer references a dead session's cache | sidecar discards the snapshot; typed error to the manager; text already delivered |
| OOM (sidecar or manager) | allocation failure or budget cap | abort transfer, evict LRU handles, stop new offers until under budget; text only; log |
| Invalid handle (unknown id, wrong session, consumed) | registry lookup fails | typed error; no LAT; text only |
| Stale cache (cache_len or checksum disagrees with offer metadata) | verification at consumption | discard handle; no LAT; text only; log checksums |
| Revision mismatch (model or tokenizer fingerprint differs) | descriptor equality, section 3 | no offer at all; text only; log both descriptor digests |
| Cancellation during transfer | signal.cancelled() (native_subagents.rs:69-71) | abandon offer, discard handle; text unaffected (never blocked) |
| Daemon restart | process death | all latent state gone by design; capabilities re-advertised on next listing; no recovery attempt |

Never silent: every row logs. Never fatal: no row may panic the sidecar or the daemon. LAT is never
reported for any failed row.

## 8. Test plan

No-regression gates (hard):

- cargo test -p pi-coding-agent --test occ_comm_fidelity stays 7/7 (the OCC-2 bar,
  OCC2-INTEGRATION-DESIGN.md:212-213).
- tests/occ2_comm_fidelity.rs stays green (OCC2-INTEGRATION-DESIGN.md:231): OCC-2 behavior must not shift
  when OCC-3 code exists but is OFF.
- The pinned activity tests stay valid: "latent vector" is Text (activity.rs:117-125) and the projection
  stays content-free and bounded (activity.rs:170+).
- Runtime suite unaffected when torch is absent: install without the optional extra (section 9); the
  kernel shim imports no torch (rlm/__init__.py:1-14); the sidecar probe is the only torch-aware code.

Unit tests (Rust manager, Python sidecar):

- u1 descriptor equality: id / revision / tokenizer / config / cache-format mismatches each refuse the
  offer.
- u2 handle registry: cross-session use rejected; single-consumer enforcement; LRU and byte-cap eviction.
- u3 receipt seam: Latent reported only for transfer AND consumption; delivered-but-unconsumed stays the
  text kind; map bounded; classify untouched on identical inputs.
- u4 limits: caps enforced before offers; timeout; concurrent-transfer cap.

Integration tests per matrix row (fake sidecar: a scripted JSON-RPC server):

- i1-i10: one test per row of section 7; each asserts (a) the text message is delivered byte-identical,
  (b) no LAT in the projected activity, (c) the failure is surfaced in logs.
- i11 happy path: eligible pair; handoff plus consumption; LAT appears for exactly that message; a
  second, non-handoff message in the same projection does not report LAT.
- i12 flag OFF: the sidecar never spawns (lazy start never triggered); transcripts byte-identical.

Fidelity (post-GO, harness-linked):

- f1 accuracy parity on the shared task set: latent-consumed receiver vs text receiver, threshold from G1.
- f2 restart: kill the sidecar mid-conversation; the conversation continues on text; no LAT for the failed
  offers.

## 9. Rollout

- Config: `agentCommunication.occ3Enabled`, default OFF everywhere until section 1 records GO; read once at
  session start into the manager policy (mirroring the occ2 flag, subagent_runs.rs:71-77). The settings
  plumbing location is OQ1 (config.rs / settings_manager.rs were not read in this pass - the same gap the
  OCC-2 design recorded as its OQ6, OCC2-INTEGRATION-DESIGN.md:316).
- OFF must mean byte-identical: no sidecar process starts, no offers are made, no receipt map is built,
  classify and prompts are untouched (sections 4-6); transcripts are identical to today.
- Dependency isolation: torch and transformers live ONLY in an optional extra of the runtime package
  (e.g. `prime-agent-runtime[latent]`) or a standalone requirements file documented beside the sidecar. The
  core dependency set, Cargo.toml and the default install path gain nothing; absence is a benign degraded
  state by the startup probe (section 2).
- Docs: the agent-message SKILL.md gains an OCC-3 paragraph only when the flag ships enabled-by-config
  (mirroring the OCC-2 stance, OCC2-INTEGRATION-DESIGN.md:280-282). Until then this design plus the go/no-go
  addendum are the source of truth.
- Phasing after GO: Phase A sidecar + manager + flag, no telemetry (unit and integration suites only);
  Phase B receipts + LAT; Phase C docs and opt-in guidance for local-model users.

## 10. Risks and open questions

- R1 bytes: KV artifacts dwarf text (1.89 MB for 40 tokens vs ~200 text bytes; RESEARCH-NOTES.md:301-302,
  :388). Latent wins only on accuracy or unique capability; the caps and truncation policy bound the cost.
- R2 correctness divergence: a latent-conditioned receiver may answer differently than the text-conditioned
  receiver. G1 gates it, and the never-compress message classes stay text-only (BLOCKED, CANCELLED, FAILED,
  CORRECTION, ACK, verification failures - mirroring the OCC-2 rule,
  OCC2-INTEGRATION-DESIGN.md:158-159): v1 excludes those kinds from latent offers entirely.
- R3 cache mutation bugs: use_cache extends caches in place (RESEARCH-NOTES.md:274-275; harness defect 2
  at :316-320). Deepcopy at snapshot and at consumption; probes never share caches.
- R4 turn-boundary corruption (RESEARCH-NOTES.md:284-285, :336): snapshot only at closed turns; refuse
  otherwise.
- R5 the sidecar is a single point of failure for the latent path only; text never depends on it
  (section 4). One loaded model at a time in v1 bounds RSS.
- R6 security: cache bytes are derived model state and can leak prompt content. They stay in
  daemon-local process memory or restrictive temp files, delete-after-load, never in durable records or
  the message transport (sections 3-4) - the same trust domain as the daemon transport itself
  (contract.rs:10-12).
- R7 stack pinning: transformers 4.x -> 5.x broke cache APIs wholesale (RESEARCH-NOTES.md:260-277); the
  descriptor allowlist (section 3) turns stack drift into a clean refusal, but upgrades require re-running
  the harness gates.
- OQ1: settings key plumbing location (not read; mirror of the OCC-2 OQ6 gap).
- OQ2: provider routing - can the receiver's model call be routed through the sidecar without new
  provider-surface plumbing? The model provider abstraction was outside this read map.
- OQ3: cross-model transfer with trained receiver heads (Interlat, RESEARCH-NOTES.md:193-195): out of
  scope for v1; needs its own harness and G1-style gates.
- OQ4: fp16 cache transfer fidelity - the harness records fp32/fp16 artifact bytes
  (RESEARCH-NOTES.md:349) but not fp16-consumed accuracy; measure before any serialized-transfer path.
- OQ5: the over-bound-context condition for G4 is not yet in protocol v2 (the transport bound is 16,384
  chars, agent_messages.rs:57); add the experiment before G4 can be a GO path.
- OQ6: receipt retention for activity refreshes (bounded LRU proposed in section 6); coordinate with the
  observability owner, as the OCC-2 design already flagged for its incident counters
  (OCC2-INTEGRATION-DESIGN.md:317-318).
