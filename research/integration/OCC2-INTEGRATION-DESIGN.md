# OCC-2 Integration Design for Optimus

Worker: occ2-integration. Worktree `improve/occ2-occ3-communication` (base v0.1.32). Design only; no code
changes. Every claim about existing behavior cites a file:line read in this pass
(core/occ/{mod,occ1,occ2,dialect,contract,activity}.rs, agent_messages.rs,
agent_session/{native_subagents,subagent_runs}.rs, rlm/occ1.py, agent-message SKILL.md, occ-campaign
FINAL-REPORT.md, symbolic_comm RESEARCH-NOTES.md, research/tasks/BASELINE.md, tests/occ_comm_fidelity.rs).

Goal: Phase 6 (integration) executes quickly and safely once the Rust codec (occ2.rs + dialect.rs, present in
this worktree) and any evolved dialect cards land. This resolves the runtime-integration half of
FINAL-REPORT.md section J item 1 (reports/occ-campaign/FINAL-REPORT.md:473-477).

## 0. Ground rules

- Daemon transport stays authoritative; sender identity is daemon-established, never trusted from message text
  (contract.rs:10-12; SKILL.md:101). OCC-2 is an encoding of the payload body, not a parallel lifecycle
  (mod.rs:5-8).
- OCC levels are interchangeable encodings of one typed payload (mod.rs:7-8). OCC-2 encodes the same
  `optimus.agent-comm.v1` contract (contract.rs:28) as OCC-1, with no information loss (occ2.rs:10-18).
- Non-goals: no change to queueing, steering, rate limiting, family scoping, receipts, or the Python OCC-1
  codec (rlm/occ1.py:10-13 stays the only model-facing kernel codec in v1). Codec calls keep the occ campaign
  rules: pure, deterministic, offline-testable (mod.rs:36-38).

## 1. The seam: one payload body, N encodings

The agent-message payload carries the body as one plain string, and two consumers derive from that one field:

- `AgentSessionMessagePayload.message` (agent_messages.rs:265) is the body; every other field (id, source,
  from, from_relationship, target; agent_messages.rs:262-272) is transport metadata the daemon owns.
- The recipient-visible prompt, `create_agent_session_message_prompt` (agent_messages.rs:654-691), renders
  fixed header lines, a blank line, then `payload.message` verbatim (agent_messages.rs:686-690).
- The durable record, `create_agent_session_message` (agent_messages.rs:693-711), stores the same string in
  `AgentSessionMessageDetails.message` (agent_messages.rs:278) inside a `CustomAgentMessage` of type
  `agent_message` (agent_messages.rs:48).

An OCC-2 encoding is therefore a pure string substitution at exactly one field; identity, queueing, delivery,
and family scoping read other fields and are untouched. The precise seam is the boundary between the sender
agent's composed body text and the string stored in `AgentSessionMessagePayload.message`.

## 2. Send path

Two seams carry all structured inter-agent text; both funnel the body through one string.

- S1, kernel host request `agent_message.send` (model-originated sends, child replies, sibling messages):
  entry `handle_agent_message_send` (agent_messages.rs:1007-1141, registered at agent_messages.rs:1002); body
  extracted verbatim at agent_messages.rs:1013-1019; target/role validation first
  (agent_messages.rs:1028-1130); direct send via `controller.send_agent_message(AgentSessionMessageSendInput)`
  at agent_messages.rs:1132-1140, the session-side controller forwarding unchanged to
  `AgentSession::send_agent_message` (subagent_runs.rs:37-46); broadcast clones the body per roster entry
  (agent_messages.rs:1046-1052; see R6).
- S2, child task spawn (parent composes the prompt): `start_rlm_child_run` (subagent_runs.rs:111) stores the
  prompt; `execute_child_run` (subagent_runs.rs:406) delivers it via `child.prompt_and_wait` with content
  `"[task from parent]\n\n{prompt}"` (subagent_runs.rs:476) and `details.message = current.prompt` in an
  `agent_message` custom message (subagent_runs.rs:478-489). Child replies return through S1.

Design: one pure helper called at both seams, after the target/prompt is known and before the payload or
custom message is built:

  wrap_outgoing_body(body: &str, policy: &Occ2SendPolicy) -> String

Policy inputs: feature flag, per-target negotiated dialect (section 4), card metrics gate (section 5),
incident state (section 3). Behavior:

1. Flag off, no negotiated dialect, or pair disabled by the 3-strike rule: return the body unchanged
   (identity).
2. Parse the body as a typed payload: OCC-1 contract message (`Occ1Message::decode` occ1.rs:261 then
   `AgentCommMessage::from_occ1` contract.rs:464) or OCC-1 delta (`Delta::from_occ1`; the detection shape
   classify() uses at activity.rs:44-47). Plain English fails both parses and passes through unchanged.
3. Encode with `occ2::encode_dual` (occ2.rs:230-246): symbolic section, `#FALLBACK-OCC1` sentinel
   (occ2.rs:68), full OCC-1 text. Strict decode verifies both sections agree (occ2.rs:264-273), so a dual
   payload is self-checking.
4. On any encode error (bounds occ2.rs:71-77; non-positional evidence references occ2.rs:357-379; card
   validation dialect.rs:303-334): return the body unchanged. Compression is optional; the message is never
   lost or altered (RESEARCH-NOTES.md:393-396).

Daemon-side transcode, not model-side: the sender model keeps writing OCC-1 (SKILL.md:50-57); the daemon owns
the symbolic layer, so there are no sender prompt changes, no card tokens in the sender context, and
negotiation uses knowledge only the daemon has.

Must NOT change: `normalize_agent_session_message` (agent_messages.rs:569-581),
`assert_agent_message_queue_capacity` (agent_messages.rs:595-606), the rate limiter (agent_messages.rs:858),
the receipt constructor (agent_messages.rs:754-777), roster/reach checks (agent_messages.rs:394-442, 520-565). The
transcode happens before these, on the body string only, and the wrapped body must still pass the same bounds
(R1).

## 3. Receive path

Principle: persist the wire, decode lazily, never block delivery on a codec.

Persistence (before decode): the durable record (`AgentSessionMessageDetails.message`, agent_messages.rs:278)
stores the dual payload verbatim, because (a) it stays decodable across daemon restarts with no registry -
`occ1_fallback` re-parses the embedded OCC-1 section when present (occ2.rs:283-292); (b) it stays strictly
decodable whenever the card is registered - `occ2::decode` resolves the header against a `DialectRegistry`
(occ2.rs:252-280, resolve at occ2.rs:656-676); (c) compaction and resume never need the dialect card to
reconstruct the typed payload.

Prompt build (what the recipient model sees): the token saving is in the listener context, so the prompt
carries the symbolic section only. Change shape in `create_agent_session_message_prompt`
(agent_messages.rs:654-691): after the fixed header lines and blank line, append the symbolic section when the
body is a dual payload - split at the first line equal to `OCC2_FALLBACK_SENTINEL` (occ2.rs:68; private
splitter `split_fallback`, occ2.rs:759-775; Phase 6 exposes it or a thin wrapper). With no sentinel
(OCC-0/OCC-1, or feature off) the output is byte-identical to today.

Delivery, queueing, receipts: no decode. Steering, delivered/queued receipts (agent_messages.rs:754-777) and
the spawn path's `parent_reply_count` bookkeeping (subagent_runs.rs:477, 505-508) are encoding-agnostic
strings.

Fallback tiers (RESEARCH-NOTES.md:388-398, mapped to code):

- Tier 1 (default) OCC-1: `FallbackPolicy::Occ1` is the default on every card (dialect.rs:232-242);
  `OccCapability::here()` always reports occ1=true (dialect.rs:698-706).
- Tier 2 (pair gating): OCC-2 only when the negotiated card covers the (speaker, listener, task-family) triple
  with HOLDOUT fidelity 1.0 - enforced by the send policy from `DialectCard` fields `speaker_models`,
  `listener_models`, `task_types`, `metrics.round_trip_fidelity` (dialect.rs:263-269, 217-225); unevaluated
  cards (0 samples, dialect.rs:213-215) are refused unless overridden in dev.
- Tier 3 (runtime failure): on `Resolved::Unknown` / `Resolved::HashMismatch` (dialect.rs:552-580) consume via
  `occ1_fallback`; on any decode error or `DualMismatch` (occ2.rs:271) count an incident, re-send the body as
  OCC-1 through the same seam, and after three incidents in a session disable OCC-2 for that pair for the
  session. Never silent: incidents are logged and, for evolved dialects, feed the card failure log
  (RESEARCH-NOTES.md:396-398).

## 4. Dialect negotiation

Structure: `OccCapability` (dialect.rs:687-695) - `occ1: bool` plus `BTreeMap<dialect_id, BTreeSet<version>>`,
serde-serializable. Deterministic negotiation exists: highest shared version, tie-break lexicographically
smallest id (dialect.rs:718-737); `render()` is the compact log form (dialect.rs:739-748).

- Per-session, daemon-side. All Rust sessions in one daemon share the codec build, so in v1 the capability is
  `OccCapability::here()` (sym-v1@1) when the flag is on.
- Discovery rides the existing family listing, not the roster: `AgentSessionMessageAgentSummary`
  (agent_messages.rs:154-184, returned by `agent_message.list_agents`, agent_messages.rs:986-995) gains one
  optional `occ_capability` field with `skip_serializing_if`, matching the existing optional-field style
  (agent_messages.rs:165-183). The roster (`build_agent_family_roster`, agent_messages.rs:394) stays untouched
  in v1 to avoid perturbing the family-scope surface.
- Conservative default: a session that reports nothing, or a capability without the dialect, yields
  `negotiate() == None` (dialect.rs:718-737) and the send helper falls back to identity - OCC-1.
- Pin per conversation: after a first successful dual send, the sender pins (dialect_id, version) for that
  target until the capability changes; renegotiation is automatic on change. The pin prevents mid-conversation
  dialect flapping (section 7).

## 5. Codebook and dialect distribution

- Built-in dialect: `DialectCard::sym_v1()` (dialect.rs:280-334), registered via
  `DialectRegistry::with_sym_v1()` (dialect.rs:593-601). No distribution needed.
- Evolved dialects (research/symbolic_comm; DIALECT/1 card grammar at RESEARCH-NOTES.md:253-278):
  `DialectCard` is Serialize/Deserialize (dialect.rs:248-249), so a card ships as a JSON artifact next to the
  research outputs. The daemon loads card files at session start into a per-daemon registry;
  `DialectRegistry::insert` validates fully (dialect.rs:604-607), including stored sha256 == recomputed
  canonical codebook hash (dialect.rs:528-534, `codebook_sha256` dialect.rs:543).
- Version pinning: the wire header pins `D=<dialect_id>@<version>` plus a 12-hex sha256 prefix (occ2.rs:15-16;
  `CODEBOOK_PREFIX_LEN` dialect.rs:51). The registry is keyed by (dialect_id, version); duplicates are
  rejected and a changed codebook must be a new version (dialect.rs:608-617). Multiple versions coexist, so a
  pinned old version keeps decoding after upgrades.
- Inline vs pre-shared: the payload is inline-dual (the OCC-1 fallback travels with every message,
  occ2.rs:230-246); the codebook is pre-shared (registry). The decoder needs the card before decode, a full
  card per message would erase the byte savings, and hash-pinned header resolution (dialect.rs:638-664)
  already detects unknown, stale, or tampered codebooks without transferring them.
- Never-compress classes (RESEARCH-NOTES.md:269-270: BLOCKED, CANCELLED, FAILED, CORRECTION, ACK,
  verification=fail, contradictions) are always dual-encoded, never symbolic-only, so the OCC-1 section
  preserves them byte-for-byte. This mirrors the never-suppress rule in the prune spec (mod.rs:250-252) on top
  of the structural losslessness (occ2.rs:16-18).

## 6. Telemetry: a real SYM signal

Current state: `classify(text)` (activity.rs:42-50) returns `Occ1` only for OCC/1 header text that passes the
standard dialect or is a valid delta (activity.rs:44-47), else `Text`; `Symbolic`/`Latent` are reserved and
"never inferred from text" (activity.rs:17-19). `from_admitted_messages` (activity.rs:53-72) scans the last
128 admitted transcript messages (TRANSCRIPT_SCAN_LIMIT, activity.rs:10), matches `agent_message` customs
targeted at this session, dedupes, caps at 32 deliveries (activity.rs:9). The pinned test at activity.rs:88-97
asserts "OCC/2\nSYMBOL=x" is Text (activity.rs:93).

Change shape (described, not coded): `classify` gains a registry parameter (or a small `Classifier` struct
holding the session registry) and one new arm before the final else:

- Text starts with "OCC/2" and `occ2::decode` with the registry succeeds (strict path, occ2.rs:252-280):
  `CommunicationKind::Symbolic`. This is the only way to get `Symbolic` - it certifies the message actually
  decoded through the symbolic path.
- Strict decode fails (unknown dialect, hash mismatch, malformed symbolic section, DualMismatch) but
  `occ2::occ1_fallback` succeeds (occ2.rs:284-292): `CommunicationKind::Occ1`, because that is how the message
  was consumed.
- Otherwise `Text`. "OCC/2\nSYMBOL=x" still fails the three-token header grammar (parse_header,
  occ2.rs:714-742) and stays `Text`, so the pinned case at activity.rs:93 remains valid.

`from_admitted_messages` needs no structural change beyond passing the registry through; it stays content-free
and bounded (pinned at activity.rs:99-110). The reservation comment (activity.rs:17-19) is updated to say
Symbolic is reported only for OCC/2 messages that decoded through the symbolic path. `Latent` stays reserved:
OCC-3 is out of scope here (mod.rs:18-19).

## 7. Session lifecycle

- Multi-turn: the negotiated (dialect, version) pin per target (section 4) lives in sender session state; each
  turn re-checks the target capability cheaply and renegotiates only on change.
- Compaction: the model-visible context contains symbolic bodies; compaction summaries are ordinary English,
  so symbolic text exists only in original transcript entries, which are dual and decodable (section 3). The
  durable record never depends on the compacted context. OQ1 tracks the compactor input path.
- Resume / daemon restart: transcript entries carry the dual payload; the registry rebuilds from built-in
  sym-v1 plus loaded card files. Even with an empty registry, `occ1_fallback` reconstructs the typed payload
  (occ2.rs:284-292). The codec is deterministic (mod.rs:36-38), so strict decode after restart yields the
  identical contract for any still-registered card.
- Codebook mismatch mid-conversation: the header hash prefix pins the exact codebook. A registered dialect
  with a non-matching prefix is `Resolved::HashMismatch` - "tampering or corruption, not a different dialect"
  (dialect.rs:563-575) - and is consumed via tier 3 (re-send OCC-1, incident, 3-strike disable). Registries
  are append-only per daemon version: `insert` rejects duplicate (id, version) pairs but keeps every distinct
  version (dialect.rs:604-617), so an in-flight conversation pinned to an older version keeps decoding after
  new versions register. Dropping a version requires a deliberate daemon release note.
- Child spawn and collect: the spawn prompt (S2, subagent_runs.rs:476-489) and child replies (S1) both flow
  through the same wrap/unwrap helpers, so a full delegation round trip exercises both directions. `collect`
  (native_subagents.rs:65-70) reads run state, not message bodies, and needs no change.

## 8. Test plan (Phase 6)

Regression bar (hard): `cargo test -p pi-coding-agent --test occ_comm_fidelity` stays 7/7
(BASELINE.md:118-127; command and env at BASELINE.md:107-113). The seven tests t01-t07 (BASELINE.md:120-123;
defined at occ_comm_fidelity.rs:409, 589, 678, 814, 977, 1088, 1153) are not modified.

Unit tests (in-tree, next to the changed code):

- u1 classify (extend the activity.rs test module after activity.rs:97): dual sym-v1 payload -> Symbolic;
  valid symbolic-only -> Symbolic; unknown dialect id with embedded fallback -> Occ1; hash-mismatch dual ->
  Occ1; malformed OCC/2 (including the existing "OCC/2\nSYMBOL=x" case, activity.rs:93) -> Text.
- u2 prompt seam (agent_messages.rs tests): a dual body renders header lines plus the symbolic section only;
  an OCC-1 or plain body is byte-identical to current behavior; the prompt-id parser still round-trips
  (existing tests at agent_messages.rs:1207-1227) because the id line offsets are above the body
  (agent_messages.rs:609-649).
- u3 wrap_outgoing_body: identity when the flag is off; OCC-1 body -> dual; plain English unchanged;
  non-positional evidence references (occ2.rs:357-379) fall back to identity; the dual output round-trips
  through both `decode` and `occ1_fallback` to the same contract; delta-shaped bodies per the OQ4 decision.
- u4 registry lifecycle: loading an evolved card file validates and registers; a duplicate is rejected
  (dialect.rs:608-617); a tampered sha256 fails `DialectCard::validate` (dialect.rs:528-534).

Fidelity-suite style (new `tests/occ2_comm_fidelity.rs`, mirroring the structure and holdout discipline of
occ_comm_fidelity.rs:1-38):

- f01 fixture shape: loads `research/tasks/msgs_corpus.json` (69 typed optimus.agent-comm.v1 messages,
  schema-compatible with the scenarios fixture, BASELINE.md:88-105); holdout stories (18 of 69) never tune
  thresholds.
- f02 round trip: `AgentCommMessage` -> `encode_dual` (sym-v1) -> strict `decode` == source; `occ1_fallback`
  on the same text == source.
- f03 size comparison: symbolic vs OCC-1 vs JSON vs plain English (mirrors t03 at occ_comm_fidelity.rs:678;
  corpus baselines plain 23,505 / JSON 43,673 / OCC-1 24,361 bytes, BASELINE.md:99-101).
- f04 fault injection: corrupt header, dialect id, version, hash prefix, symbol lines; never decodes equal,
  never panics (mirrors t04 at occ_comm_fidelity.rs:814).
- f05 semantic faults: dangling evidence references, duplicate scalars, index gaps, incomplete verification
  groups are typed errors (mirrors t05 at occ_comm_fidelity.rs:977; error inventory occ2.rs:83-213).
- f06 latency: mean encode/decode under the 5 ms budget over 100 iterations (mirrors t06 at
  occ_comm_fidelity.rs:1088; constants at occ_comm_fidelity.rs:43-48).
- f07 aggregate report incl. compression ratio and per-kind coverage (mirrors t07 at
  occ_comm_fidelity.rs:1153).

Session-level round trips (integration):

- s1 spawn/collect: parent spawns a child with a dual-encoded OCC-1 task body; the child transcript prompt
  shows the symbolic section and `details.message` holds the dual payload; the child replies via
  `agent_message.send` and the reply transcodes symmetrically; the parent decodes both.
- s2 fallback: force an unknown dialect id (or empty registry); the child still completes the task from the
  embedded OCC-1 section (tier 1/3 path).
- s3 durability: persist, rebuild with an empty registry, resume; the record still decodes via
  `occ1_fallback`.
- s4 three-strike: forced decode failures disable OCC-2 for the pair for the session; messages still deliver
  as OCC-1 (RESEARCH-NOTES.md:393-396).

## 9. Rollout

Config flag: one boolean settings key, default OFF in production, e.g. `agentCommunication.occ2Enabled`. Read
once at session start into the send policy and session registry; absent/unknown -> OFF. Exact settings
plumbing (config.rs / settings_manager.rs) was not read in this pass; Phase 6 step (OQ6).

OFF must mean byte-identical OCC-0/OCC-1:

1. `wrap_outgoing_body` is the identity function (section 2, rule 1).
2. No dual payloads exist, so the sentinel split in the prompt builder never fires; prompt output is
   byte-identical (section 3).
3. `classify` never sees OCC/2 text; even hostile text fails the strict arm and falls to Text/Occ1 exactly as
   pinned today (activity.rs:88-97).
4. Receipts, roster, queueing, rate limits, prompt-id framing: no code changes in those paths (section 2 "must
   NOT change" list).
5. Python side unchanged: `rlm.occ1` remains the kernel codec (SKILL.md:87-91); no new model-facing surface.

Enable path: flag on in dev -> f-suite green on the corpus -> task-suite evaluation (research/tasks,
BASELINE.md:18-41) -> tier-2 gating by card metrics (RESEARCH-NOTES.md:390-392) -> production only for cards
with HOLDOUT fidelity 1.0. The agent-message SKILL.md gains an OCC-2 section only when the flag ships
enabled-by-config (it documents OCC-1 only today, SKILL.md:50-91).

## 10. Risks and open questions

- R1 size: a dual payload is larger than its OCC-1 form (symbolic + sentinel + full OCC-1). Bodies near the
  transport bound (DEFAULT_AGENT_MESSAGE_MAX_CHARS 16,384 chars, agent_messages.rs:53) can fail normalization
  after wrapping, though the codec bound is 128 KiB (occ2.rs:77). Mitigation: on any bound failure, send the
  unwrapped OCC-1 body (compression optional).
- R2 12-hex prefix: 48 bits of codebook binding (CODEBOOK_PREFIX_LEN, dialect.rs:51; tradeoff documented at
  dialect.rs:45-50). Acceptable because the transport is daemon-local and sender identity is
  daemon-established (contract.rs:10-12); the full sha256 is verified at card load (dialect.rs:528-534).
- R3 listener comprehension: a symbolic body is only as good as its card for that listener model; sym-v1 is
  explicitly "a starting point only" (dialect.rs:280-284). Gated by tiers 2-3 (RESEARCH-NOTES.md:388-398) and
  the model-pair matrix discipline (RESEARCH-NOTES.md:358-379).
- R4 classify cost: strict decode per admitted message on each activity refresh, bounded by the 128-message
  scan (activity.rs:10) and the 64 KiB symbolic bound (occ2.rs:75). If measurable, cache the verdict by
  message id.
- R5 prompt framing: the body must land after the blank line following the "Message id:" line
  (agent_messages.rs:686-690) with the OCC/2 header intact on the first body line;
  `parse_agent_session_message_prompt_id` (agent_messages.rs:609-649) must keep parsing - covered by u2 and
  the existing round-trip tests (agent_messages.rs:1207-1227).
- R6 broadcast: per-recipient encoding may differ when capabilities differ; the receipt has no encoding field
  (agent_messages.rs:289-306). v1 transcodes per entry (agent_messages.rs:1046-1052) and logs; no receipt
  change.
- OQ1: does the compactor consume custom-message `content` (symbolic) or `details` (dual)?
  pi-ai/src/compaction.rs was outside this read pass. If content, add a compaction note that agent-message
  bodies decode via `occ1_fallback`.
- OQ2: exact body of `AgentSession::send_agent_message` (agent_session.rs, outside the read map; controller
  forward at subagent_runs.rs:37-46). Phase 6 confirms the single normalize/queue call site before hooking.
- OQ3: capability on `AgentSessionMessageAgentSummary` vs a separate `agent_message.capabilities` host
  request. Summary field is fewer round trips; a separate request is more isolated. Decide in Phase 6 review.
- OQ4: deltas (`Delta::from_occ1`, activity.rs:46): the OCC-2 codec encodes `AgentCommMessage` only, not
  `Delta`. Either exclude deltas from wrapping (v1) or extend the codec (follow-up). OQ5 is its policy twin:
  does the campaign want deltas symbolic at all, given `DeltaTracker` already minimizes bytes?
- OQ6: settings key name and plumbing location (config.rs / settings_manager.rs not read in this pass).
- OQ7: telemetry surfacing of fallback incidents (counter location, log shape): reuse the card failure-log
  concept (RESEARCH-NOTES.md:396-398) or a daemon metric; decide with the activity/observability owner.
