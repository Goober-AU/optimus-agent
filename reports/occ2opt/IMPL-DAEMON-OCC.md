# IMPL-DAEMON-OCC: daemon-owned /occ preferences, as built

Worktree: `optimus-occ2opt-20261016`. Lane: verify + gate + document the
capability-gated daemon-owned `/occ` implementation per
`reports/occ2opt/DAEMON-COMPAT.md` (design) after a predecessor stall.
No model calls, no network, no git commits; scoped file review only.

## As-built vs design

The predecessor landed the design end to end; this pass verified it, fixed
the one failing unit test, extended one coverage gap, fixed the two failing
live-daemon tests (a missing supervisor-side command registration; see the
last two sections), and ran the gates.

| Design item | Status | Notes |
|---|---|---|
| 4.1 capability `OccSessionPreferences` (wire `occ_session_preferences`), schema revision 41, `DAEMON_SCHEMA_ID = protocol-7-schema-41-occ-session-preferences` | done | `daemon_protocol.rs`: revision-history comment, capability enum variant, advertised list swap (`Occ2Communication` out, `OccSessionPreferences` in; array size stays 31). |
| 4.1 stop advertising `occ2_communication`; keep the enum variant for old hellos | done | Old hello frames still parse (pinned by `hello_frames_distinguish_the_storage_semantics_across_versions`). |
| 4.2 `occ_get_preferences` / `occ_set_preferences` in the command table + compatibility map + session plane | done | `daemon_command_compatibility` gates both on `Capability::OccSessionPreferences`; `daemon_command_plane` -> "session"; `DAEMON_COMMAND_TYPES` (103 -> 105); `occ_get_preferences` added to `READ_ONLY_DAEMON_COMMANDS` (36 -> 37), setter deliberately absent. |
| 4.2 daemon handler arms keyed by `activeSessionId` | done | `daemon_mode.rs` arms next to the jev arms: `get_session_state(active_session_id)?` (unknown sessions fail loudly), `session_of(&state).session_file()`, then `session_settings::preferences` / `set_preferences`. `occ_set_preferences` rejects a missing/non-boolean `occ2Enabled`. |
| 4.2 get surfaces load errors (cycle/oversize/schema) | done | `preferences()` propagates `resolve` errors; live daemon test pins corrupt + oversized failures. |
| 4.2 set is a daemon-side `save` (correct profile by construction, remote/foreign-agentDir safe) | done | `set_preferences` = atomic `save` + fresh `resolve` echo (`applied`, `scope`, `effectiveForNewMessages`, `sourceSessionId`). Live test asserts the artifact lands inside the daemon profile and `settings.json` keeps no `agentCommunication` key. |
| 4.2 optional footer/roster refresh after set | omitted (optional per design) | The roster's `occ_capability` is computed live per listing (`occ_capability_for` reads `session_settings::enabled` with no cache), so a refresh is a UI nicety, not a correctness need. |
| 4.3 client rewrite with capability routing; status renders daemon truth incl. provenance | done, one deliberate deviation | `occ_control.rs` routes: `Remote` (daemon owns get/set via `get_occ_preferences`/`set_occ_preferences`), `LegacyLocal` (daemon advertises `occ2_communication` but not `occ_session_preferences`: historical client-side write keyed on the daemon-reported session file + a visible warning that it may not take effect; never a silent success), `Unsupported` (visible warning, no write). The design's stricter "no write at all on old daemons" was relaxed to "fallback write + visible warning" so same-machine v0.1.33 daemons keep working; the changelog documents this. |
| 4.3 trait methods `AgentConnection::{get_occ_preferences, set_occ_preferences}` with `not supported` defaults | done | `types.rs` defaults; `in_process_agent_connection.rs` calls `session_settings::{preferences, set_preferences}` directly (same process, same filesystem); `daemon_agent_connection.rs` sends the RPCs via `request_data` with the session-changed guard (pattern `get_jev_status`). |
| 4.4 version matrix | done | See the test mapping below; the new daemon never advertises `occ2_communication` (pinned live + offline). |
| 4.5 occ.json stays the storage; `get_connection_state`/roster `occ_capability` unchanged | done | Storage format untouched; `occ_capability_for` unchanged. |

Design test list coverage (section 5 of DAEMON-COMPAT.md):

| # | Required | Where |
|---|---|---|
| 1 | new client + old daemon: warning, no silent success | `occ_control_routes_by_host_capability_and_never_succeeds_silently_on_old_daemons` (routing) + LegacyLocal warning text; hello-frame test builds the v0.1.33 frame offline. |
| 2 | forced send to a non-advertising daemon | capability gate pinned in `occ_session_preferences_gate_old_peers_and_keep_legacy_commands` + `revision_41_advertises_daemon_owned_occ_session_preferences` (DAEMON_COMMAND_TYPES registration: an old daemon answers "Unknown daemon command"). |
| 3 | old client + new daemon: no `occ2_communication` advertisement | `daemon_owns_occ_preferences_across_chats_failures_and_restart` (live), `revision_41_...` (offline), `native_cli.rs` hello assertions. |
| 4 | old client + old daemon baseline | not runnable offline (needs old binaries); routing unit test pins the LegacyLocal path shape. |
| 5 | get/set round-trip, daemon-authoritative echo | live `daemon_owns_occ_preferences_...` + unit `occ_session_resolution_reports_provenance_and_round_trips`. |
| 6 | no bound session -> failure | live (unknown `activeSessionId` fails both commands) + in-process "no saved session identity" path. |
| 7 | corrupt/oversized/cyclic -> get failure | unit + live (cyclic pinned by `resolve` cycle detection unit path; corrupt + oversized pinned live and in unit). |
| 8 | two chats isolated | live (second chat stays off) + unit `occ_sessions_isolate_chats_...`. |
| 9 | nested inheritance, child override, re-inherit after override removal, forks excluded | unit `occ_sessions_isolate_chats_...` (incl. the re-inherit case added in this pass) + `preference_resolution_isolates_chats_and_inherits_down_subagent_chains` + live `daemon_reports_inherited_preferences_for_subagent_chats`. |
| 10 | forks at depth 0 do not inherit | unit + offline integration (fork stays off). |
| 11 | live toggle without restart | structural: `enabled()` and `occ_capability_for` read with no process cache; live test reads back the toggle in the same daemon turn. |
| 12 | daemon restart durability | live: restart + reattach through `sessionPath` keeps the value. |
| 13 | compaction stability | not covered by a dedicated test (session file path is stable across compaction by construction; `preference_path` is derived from the file path only). |
| 14 | resume keeps preference, new chat starts off | live restart test (resume) + live second chat (off). |
| 15 | foreign agentDir: write lands in the daemon artifact dir | live: `first_occ.starts_with(fixture.root.path())`, daemon `settings.json` untouched. |
| 16 | relative-agentDir / remote daemon | structural: the RPC write is daemon-side; no client path math remains in the Remote route. Not testable offline beyond that. |
| 17 | hello lists `occ_session_preferences`, not `occ2_communication`; schema id asserts revision 41 | offline + live + `keeps_the_advertised_schema_identity`. |
| 18 | `/occ status` renders daemon truth | Completed in this pass: `read_preference` now returns the full host payload; `status_of` renders the toggle plus an "Inherited from chat <id>" line when the host reports `inherited`/`sourceSessionId`; the set confirmation renders the same daemon-authoritative echo. Pinned by the unit test `occ_status_renders_host_truth_with_provenance`. |

## Sibling-lane integration

Sibling lanes kept editing shared files while this lane verified: `core/occ/selection.rs` (14:36),
`core/agent_messages.rs` + `core/agent_session/subagent_runs.rs` (15:07), `modes/daemon/daemon_mode.rs`
(15:09, the occ2 send-policy override ~:16830), and a content-identical touch of
`core/occ/session_settings.rs` (15:48). Nothing was reverted; the final gate run below compiled the
combined tree. This lane's own late addition (`modes/interactive/occ_control.rs` provenance render)
was made after the first gate attempt and is covered by the same final run.

## The failing test: root cause and fix

`core::occ::session_settings::tests::occ_session_resolution_reports_provenance_and_round_trips`
failed at its own assertion
`assert!(set_preferences(&a, true).is_err())` after the test had planted a
corrupt `occ.json` ("not json") on chat `a`:

- `set_preferences` = `save` (atomic temp-file rename) + fresh `resolve`
  echo. `save` overwrites the corrupt file with a valid preference, so the
  echo resolves cleanly and the call returns `Ok` - the assertion expected
  `Err`.
- The expectation contradicted both the design (DAEMON-COMPAT.md 4.2: set is
  `session_settings::save(...)`; section 5 item 7 requires only *get* to
  fail on corrupt storage) and the lane's own live-daemon contract
  (`occ2_daemon_compat.rs`: "A set heals the storage and reports the stored
  value back").

Fix (unit test only; production code unchanged): the assertion now pins the
heal-on-set semantics (`occ2Enabled`/`applied` echo, `load` true afterwards)
and adds a genuine write-failure case - the preference path occupied by a
directory so the atomic rename in `save` cannot complete - proving set
failures still propagate loudly instead of degrading to a silent value.
Read failures keep their loud path (`preferences`/`load` are `Err` on the
corrupt and oversized files).

Also added (same unit test): removing a child's override re-inherits the
parent dynamically (design section 5 item 9), the one coverage gap found.


## The two failing live tests: root cause and fix

The suite stood at 3 offline tests green and both live-daemon tests failing
at their first OCC command with
`Some("Unknown daemon command: occ_get_preferences")` /
`...occ_set_preferences...`. The worker registration was complete
(`daemon_mode.rs::DAEMON_COMMAND_TYPES` listed both, and the handler arms
existed), so the predecessor suspected the wire path and started building a
raw frame-format probe helper (`[u32 header_len][u32 payload_len][header
_json][payload_json]`). No such helper was needed and none of it landed; the
frames were never the problem.

Root cause: the test connects to the real `--mode daemon` process, which is
the *native supervisor* (`native_supervisor.rs`), not a bare worker. The
supervisor fronts every client connection and validates each command against
its own list, `daemon_supervisor::DAEMON_COMMAND_TYPES` (110 entries at the
time), *before* dispatching; `native_supervisor.rs:3451` answers anything
unlisted with `Unknown daemon command: <kind>` and never forwards it. The OCC
commands were registered in the worker's list (`daemon_mode.rs`, 105 entries)
but missing from the supervisor's list, so the commands died at the public
boundary and the worker arms were unreachable dead code for real clients.
The two failures were one bug, not two: both live tests fail on their first
OCC RPC, so `daemon_owns_occ_preferences_...` (first call is a get) and
`daemon_reports_inherited_preferences_...` (first call is a set) fell over
at the same check.

Fix (one production line plus its comment): register both commands in
`daemon_supervisor.rs::DAEMON_COMMAND_TYPES` (110 -> 112), placed next to the
optional Jev surface. The supervisor serves no OCC state itself - its
generic `find worker by activeSessionId` forward hands the command to the
owning worker, where the existing `daemon_mode.rs` arms resolve
`session_settings::preferences` / `set_preferences` against the worker's own
session file. This is the same forwarding shape `get_connection_state`
already uses, which is why every non-OCC step of the two tests passed before
the fix. `occ_set_preferences` counts as mutating (`READ_ONLY_DAEMON_COMMANDS`
excludes it), so the supervisor's command-recovery journaling applies to it
exactly like `set_model`-class session writes.

Also added (test only): the offline contract test
`revision_41_advertises_daemon_owned_occ_session_preferences` now asserts the
supervisor list carries both commands too, so the worker-registered /
supervisor-unregistered split that caused this failure cannot silently
return.

## Final gates

All gates run offline with `CARGO_BUILD_JOBS=3` and the shared target dir
`C:/Users/openclawuser/optimus-occ2opt-target-w7`; logs in
`reports/occ2opt/gates/`.

| Gate | Result | Log |
|---|---|---|
| `cargo test --offline -p pi-coding-agent --test occ2_daemon_compat` | **5 passed, 0 failed** (3 offline + both live daemon tests, real `optimus-rust` supervisor + worker behind a named pipe) | `gates/occ2_daemon_compat.log` |
| `cargo test --offline -p pi-coding-agent --lib session_settings` | **3 passed, 0 failed** (the previously fixed unit suite; no regression) | `gates/lib_sessionsettings.log` |

Observed but out of scope: `jev_ui_tests::the_daemon_surface_is_capability_
gated_and_read_only_where_it_gets` fails on the combined tree, and it also
fails at HEAD (`git show HEAD:...daemon_protocol.rs` already has the jev arm
split that the test's literal source-pinning assertion rejects) - it is the
Jev lane's stale pin, not an OCC regression, and this lane did not touch it.
The supervisor-list edit adds no behavior beyond accepting the two already
specified commands; no other daemon path changed.
