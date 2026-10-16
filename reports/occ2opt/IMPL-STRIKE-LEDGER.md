# IMPL-STRIKE-LEDGER — production wiring of the OCC-2 three-strike ledger

Worker: occ2-arch. Date: 2026-10-16. Worktree: `optimus-occ2opt-20261016`, target `optimus-occ2opt-target-w4` (warm).
Offline, deterministic: no model calls, no network, no git commits, no repo-wide greps.

## 1. Design as-built

**Problem.** `Occ2StrikeLedger` and `decode_agent_session_message_body` (agent_messages.rs) were pure and tested, but no production path invoked them: the daemon never decoded bodies, so no pair was ever disabled in reality, and counts were lost on restart (ARCHITECTURE-REVIEW.md gap 5).

**Single authoritative decode+render path (design item 1).** `render_agent_session_message_delivery` (agent_messages.rs:1471-1550) is now the one production path for model-visible delivery:
- one fallback split + one strict `occ2::decode` (the codebook-amortization render core, extended - design item 8 of the prior task held: no double parse);
- ledger-gated per pair: a failed strict decode records one strike (`record_incident_at`, :1095) and the body is consumed through its embedded OCC-1 section;
- a pair already disabled skips the strict decode entirely (no further strikes) and consumes the OCC-1 section - `DisabledByStrikes`;
- no data loss ever: a body with a broken fallback section is delivered raw (t25);
- the amortized codebook legend/reference applies to the symbolic render as before.
`create_agent_session_message_delivery` (agent_messages.rs:1636) wraps one render into both the visible prompt text and the durable record, so the delivered text and stored content agree, and records the classification in the record's details (below). The pure seams (`agent_session_message_prompt_body*`, `decode_agent_session_message_body`, spawn seam) are unchanged.

**Pair identity (design item 2).** `occ2_pair_key` (agent_messages.rs:1047): `{speaker}->{listener}|{dialect}@{version}` - speaker session id, listener session id, advertised dialect (dialect id parsed from the raw wire header by `occ2_body_dialect_id`, :1459, so an UNKNOWN dialect still strikes its own pair - t24). Three strikes disable the pair for OCC-2 only; OCC-1 continues; nothing is global-off. Selection-side enforcement: `occ2_strike_pair_disabled(session_file, speaker, listener, dialect, now)` (:1321) reads the listener's persisted ledger - the check `occ2_target_send_policy` (subagent_runs.rs, not this worker's file) should call after `select_occ2_dialect`; listener-side enforcement is complete today (the model never sees broken symbolic for a disabled pair).

**Persistence (design item 3).** `occ2-strikes.json` in the listener's session artifact directory (`occ2_strike_ledger_path`, :1207; same layout as the occ.json preference). `save_occ2_strike_ledger` (:1219): atomic tempfile + persist, session_settings precedent. `load_occ2_strike_ledger` (:1260): missing file or any error fails OPEN (strikes degrade a convenience; they never gate delivery); decay applied at load. Restart restores counts (t22) and never double-counts (t27).
- **TTL decay**: `OCC2_STRIKE_DECAY_MS = 24h` (:1043); `decay()` (:1111) drops pairs idle past the window, re-enabling them (t23).
- **/occ 2 on clears**: a preference write (occ.json) with an mtime newer than the strike file clears the ledger and deletes it (sticky) - the toggle re-arming OCC-2 starts the chat from zero (t26). An explicit `occ2_strike_ledger_clear` (:1309) is exported for direct wiring by the /occ command owner; the mtime rule covers it meanwhile. Any /occ write clears (superset of "2 on"; documented here).

**Telemetry (design item 4).** `Occ2DeliveryKind` (:1394): `Symbolic | FallbackConsumed | Occ1Native | DisabledByStrikes | Text` (stable labels via `as_str`). The durable record's details now carry `occ2Delivery { kind, pair, strikeRecorded, pairDisabledNow }` (typed `Occ2DeliveryDetails`, backward compatible via `#[serde(default)]`), computed once per render - the one-decode-per-render rule. `activity.rs` (not this worker's file) can read `details["occ2Delivery"]["kind"]` instead of re-decoding; its classify path is untouched and stays green.

**Daemon wiring.** `accept_agent_session_message` (daemon_mode.rs:15681-15743, this worker's region): resolves the target session once (`session_of`), derives codebook presence, speaker (from.session_id -> active -> client_id -> "unknown") and listener (target.session_id), loads the ledger from the target's session file (fail-open), runs the single delivery render, and persists back only when a strike was recorded or a disable happened (no write amplification on clean traffic).

## 2. Files and hunks (ownership)

Owned, edited freely:
- `crates/pi-coding-agent/src/core/agent_messages.rs` - ledger extension (timestamps, `record_incident_at`, `decay`, `pairs`/`from_pairs`), persistence block (:1031-1336), delivery render + outcome + classification (:1394-1664), `AgentSessionMessageDetails.occ2_delivery` field, `create_agent_session_message_prompt_with_codebooks` refactored onto the shared scaffolding helper `create_agent_session_message_prompt_with_body` (byte-identical output, t06/t19 unchanged).
- `crates/pi-coding-agent/tests/occ2_comm_fidelity.rs` - t20-t27 (:1524-1820) + module doc.
- `.changes/v0.1.34-occ2-strike-ledger-wiring.md` - new fragment.

Announced minimal hunks outside ownership:
1. `modes/daemon/daemon_mode.rs:15681-15743` (the pre-declared delivery region) + import block (:55-60): the wiring described above.
2. `modes/interactive/components/agent_message.rs:282` - one line, `occ2_delivery: None,` in a test struct literal, mechanical fallout of the new details field (compile fix; no behavior).

Not touched: selection/dialect files, session_settings.rs, occ_control.rs, activity.rs, subagent_runs.rs.

## 3. Test evidence (t20-t27, all in occ2_comm_fidelity.rs)

- t20 first failed decode -> exactly one strike, embedded OCC-1 delivered; Symbolic / Occ1Native / Text classifications; no strikes for non-OCC/2 bodies.
- t21 three strikes disable the pair; the 4th delivery is `DisabledByStrikes` with no further strikes; `occ2_strike_pair_disabled` reads the persisted ledger: disabled for (speaker-1, listener-1, sym-v1) only - not other dialects, speakers or listener sessions.
- t22 restart: save with 2 strikes -> fresh load restores 2; the third strike disables; a second restart keeps the verdict.
- t23 TTL: disable holds inside 24h of the last strike; after the window counts are zero and the pair is re-enabled.
- t24 unknown dialect id: strike + fallback, scoped to the ADVERTISED dialect pair.
- t25 corrupt codebook hash prefix: strike + fallback; a body with a broken fallback section too: strike recorded, raw body delivered (no data loss).
- t26 /occ clear: preference marker newer than the strike file -> ledger cleared and file removed (sticky); explicit `occ2_strike_ledger_clear` behaves the same.
- t27 restart mid-strikes: a good delivery after restart consumes symbolically and counts nothing; the durable record carries `occ2Delivery` (kind, pair, strike flags) and keeps the dual verbatim; a restart never double-counts (exactly 3 incidents across two restarts).

Existing tests stay green: t01-t19 (amortization + codec + fault injection t04), the pure three-strike test t10, and all agent_messages unit tests.

## 4. Gate results

| Gate | Command | Result |
|---|---|---|
| 1 | `cargo test -p pi-coding-agent --test occ2_comm_fidelity` | **27 passed / 0 failed** (19 prior + t20-t27) |
| 2 | `cargo test -p pi-coding-agent --test occ_comm_fidelity` | **7 passed / 0 failed** |
| 3 | `cargo test -p pi-coding-agent --lib occ::` | **241 passed / 2 failed** - both failures in sibling in-flight files (`core::occ::selection::tests::constants_match_the_measured_reports`, occ2-codebook's new selection.rs; `core::occ::session_settings::tests::occ_session_resolution_reports_provenance_and_round_trips`, occ2-daemon's, the same known failure as the prior task). My `core::occ::*` and agent-message surface passed. |
| 4 | `cargo test -p pi-coding-agent --lib communication_` | **16 passed / 0 failed** |
| 5 | `cargo test -p pi-coding-agent --lib agent_messages` | **27 passed / 0 failed** |

Concurrent-tree note: gates 1-2 were run and passed; during gate 3 the tree briefly failed to compile from occ2-codebook's mid-edit `selection.rs` (fixed by them before my final run) and one mechanical struct-literal fallout of my details field was fixed in `agent_message.rs` (announced above). At final run all targets compiled; the 2 remaining gate-3 failures are the siblings' in-flight tests, not this change.

## 5. Behavior contract (for reviewers)

- OCC-2 off (`occ2Enabled=false`): no OCC/2 bodies exist, the delivery render is byte-identical OCC-1/Text (t19/t20), the strike file is never written.
- Clean OCC-2 traffic: one strict decode per delivery, symbolic render with amortized codebook, no ledger writes.
- Degraded pair: OCC-1 consumption for that pair only; sender keeps dual on the wire until the selection-side hunk consumes `occ2_strike_pair_disabled`.
- Restart: counts restored; 24h quiet: zeroed; /occ toggle: cleared.
