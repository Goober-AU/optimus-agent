# IMPL-TELEMETRY-GAPS.md - Part A: production gap closures

Task: `occ2opt-telemetry-and-combined-gates`, Part A. Two closures: the
daemon-bridged send policy and the double-decode in activity telemetry.
All gates for the owning lanes are green at the time of writing; Part B
(the quiesced-tree combined run) is pending sibling completion and is
recorded separately in COMBINED-GATES.md.

## 1. Daemon-bridged sends now use the selection path

### Problem
`DaemonAgentMessageController` (daemon_mode.rs) implemented
`AgentSessionMessageController` without an `occ2_send_policy` override,
so every daemon-bridged `send_agent_message` fell back to the trait
default: OCC-1, always, regardless of evaluation data, strikes, or the
`/occ 2` opt-in. The in-process seam (`AgentSession::occ2_selected_send_policy`)
had the full two-stage selection; the daemon bridge had none.

### Change
- `agent_messages.rs:2410` - new free function
  `occ2_send_policy_for_send(peer, listener, message, speaker_session_file,
  speaker_model, speaker_session_id, experimental) -> Occ2SendPolicy`.
  This is the one shared send-selection assembly: it negotiates the
  dialect, reads the strike ledger and evaluation store scoped to the
  speaker's session file, builds the `SelectionContext`
  (`codebook_in_context = false` - the sender cannot cheaply know the
  listener's transcript) and applies `occ2_send_policy_from_selection`.
- `subagent_runs.rs:141` - `AgentSession::occ2_selected_send_policy` is now
  a thin resolver that gathers this session's inputs (session file, model,
  session id) and delegates to the shared function. The body moved, the
  behavior did not: the same inputs feed the same decision order.
- `agent_messages.rs:2389` - `occ2_message_kind` moved out of
  `subagent_runs.rs` and published as `occ2_message_kind_hint` next to the
  function that consumes it.
- `daemon_mode.rs:16830` - `DaemonAgentMessageController::occ2_send_policy`
  override. It resolves the daemon-side inputs the in-process seam gets
  for free: the speaker's session from the daemon's session map
  (`session_of(&current)`), the `/occ` toggle
  (`session_settings::enabled(speaker_file)`), the peer capability and
  the canonical listener session id from the same listing the send handler
  resolves targets with (`create_agent_message_list_result`). Then it
  calls the shared function. Missing current state or an unresolvable
  target returns `Occ2SendPolicy::disabled()` (OCC-1), matching the
  fail-open posture: the policy is an optimization, never a gate on
  delivery.

### Test coverage
`t28_shared_send_selection_end_to_end_with_real_artifacts`
(occ2_comm_fidelity.rs) drives the shared function over real on-disk
artifacts: cold production stays OCC-1 (unevaluated), the experimental
flag selects OCC-2, a persisted strike (recorded at the real wall clock so
the 24h decay window does not eat it) flips even the experimental send
back to OCC-1, a cleared strike plus a validated evaluation record
(samples 30, fidelity 1.0) selects OCC-2 in production, a missing session
file reads as no strikes and no evaluations, and an unknown listener
stays OCC-1.

A direct test of the controller override needs a live `AgentDaemon` with
an active session state; no such harness exists in the crate today (the
daemon integration tests boot through the client protocol, which does
not expose the controller). The override is a pure resolver around the
tested function, so coverage is at the seam it feeds.

## 2. Telemetry reads the recorded classification

### Problem
`activity.rs::from_admitted_messages_with_registry` classified every
delivery by re-decoding the message body (`classify_with_registry`), even
though the delivery render path already recorded the definitive
classification once, in `details.occ2Delivery.kind`. The double decode
wasted cycles and could disagree with the recorded truth (a corrupt
symbolic body that was consumed via fallback still classifies as
whatever the partial decode says).

### Change
`activity.rs:109` - `recorded_delivery_kind(details)` reads the recorded
field: `symbolic` -> Symbolic, the OCC-1-consumed kinds
(`fallback_consumed`, `occ1_native`, `occ2_disabled_by_strikes`) -> Occ1,
`text` -> Text. Legacy records without the field, unknown labels, or
non-string values return `None` and the caller falls back to body
classification exactly as before. The scan stays content-free and bounded
(TRANSCRIPT_SCAN_LIMIT / MAX_ACTIVITY_MESSAGES unchanged).

### Test coverage
`t30_telemetry_reads_recorded_delivery_kind` covers all four paths:
- a symbolic delivery reports Symbolic even when the supplied registry is
  empty (body decode would fail) - the recorded field wins;
- a fallback_consumed delivery reports Occ1 even though the body carries
  a (corrupt) symbolic section - the recorded truth wins over a partial
  decode;
- legacy records without the field classify from the body (Symbolic for a
  dual body, Text for plain english);
- a tampered unknown label (`not-a-label`) falls back to body
  classification.

`t29_message_kind_hint_parses_kinds` pins the moved KIND parser.

## Gates (owning lanes, at Part A completion)

| Gate | Result |
|---|---|
| `--test occ2_comm_fidelity` | 30 passed / 0 failed |
| `--test occ_comm_fidelity` | 9 passed / 0 failed |
| `--test occ2_selection_policy` | 7 passed / 0 failed |
| `--test occ2_selection_wiring` | 7 passed / 0 failed |
| `--lib occ:: communication_ agent_messages` | 291 passed / 1 failed |

The single lib failure is `session_settings::occ_session_resolution_reports_provenance_and_round_trips`,
pre-existing in the occ2-daemon sibling's in-flight lane (verified before
this task started). No file outside this task's ownership was touched.

## Files touched
- `crates/pi-coding-agent/src/core/agent_messages.rs` (shared function,
  kind hint; +~100 lines)
- `crates/pi-coding-agent/src/core/agent_session/subagent_runs.rs`
  (delegate; net -80 lines)
- `crates/pi-coding-agent/src/core/occ/activity.rs` (recorded kind read)
- `crates/pi-coding-agent/src/modes/daemon/daemon_mode.rs` (controller
  override + `Occ2SendPolicy` import)
- `crates/pi-coding-agent/tests/occ2_comm_fidelity.rs` (t28-t30)
