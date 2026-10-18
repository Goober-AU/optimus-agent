# w6-providers — providers/streaming, session persistence, compaction, settings

Owner: w6-providers. Worktree C:/Users/openclawuser/optimus-fh2-20261018
(branch improve/harness-validation-r2 @ 5feb47596). Private target dir
C:/Users/openclawuser/fh2-t-w6 (pi-coding-agent lib test binary built once,
19m19s). All cargo work goes through C:/Users/openclawuser/fh2-build-queue/fh2q.py.

STATUS: review-only handoff. The parent stopped worker builds ("do not build";
"hand back job specs only") after my first test binary build, so nothing in my
area is test-verified by me. Everything below marked STATIC is a code-reading
verdict, not a green run. Job specs are in section 6.

## 1. Job history (both aborted, no product failure recorded)

J1 `cargo test --locked -p pi-coding-agent --lib -- throughput_ --test-threads=1 -j 2`
  exit 101. Cause: the campaign queue template puts `-j 2` AFTER `--`, so libtest
  receives it and exits with `error: Unrecognized option: 'j'`. Cargo itself
  built the lib test binary successfully. Not a product defect, but the template
  must be `cargo ... -j 2 -- --test-threads=1`. Parent confirmed the fix.
  log: artifacts/logs/w6-throughput.log

J1b same filter with correct flag order, submitted; killed on the parent's
  "do not build" instruction to free the shared permit. It had rebuilt from
  scratch because other workers changed sources in the shared worktree.

Consequence: my area has NO green run yet. Section 6 lists the exact jobs.

## 2. Priority 1 — PR #174 streaming TPS family sum (STATIC: cannot double count)

Chain read end to end:

1. pi-agent-core `agent_loop.rs:2851` records `StreamingMs` per provider attempt
   (w1 area, not edited here).
2. `core/performance_monitor/throughput.rs` `Counter::record` accepts an event
   only when operation is `ProviderAttempt`, `provider_attempt_id` is non-empty,
   `usage.source == "provider"`, `output_tokens` is finite and > 0, and
   `StreamingMs` is finite and > 0. There is no fallback to dispatch time, total
   time, or to request duration.
   - `seen` (VecDeque, cap 256) makes a replayed terminal a no-op, and every
     terminal is remembered even when it carries no usage or no timing, so a late
     `progress()` cannot resurrect a discarded estimate.
   - The live estimate lives only in `pending` and is added in `current()`, so it
     is never also inside `snapshot`.
   - `progress()` is idempotent for an identical (id, tokens, ms) pair.
3. `modes/daemon/daemon_session_list.rs:496` publishes `Snapshot` as
   `streamingThroughput` on the roster row.
4. Client-side per-session state
   (`modes/interactive/generation_throughput.rs`) is keyed by `session_id` in a
   `BTreeMap`, and `contributions()` yields at most one sample per session id:
   - alias keys (`active:`, `session:`, `<file identity>`) are namespaced by
     `agents_view_state::get_parent_keys`, so they only widen family discovery and
     never add a term;
   - duplicate roster rows collapse in the map, and traversal is order independent
     (`visited` set, children indexed by `get_parent_keys`);
   - a restart is a new `generation`: the old generation is retired, that
     session's `finalized` baseline and live overlay are dropped, and a retired
     generation is ignored even at a higher sequence;
   - stale/duplicate sequences are rejected; with sequence 0 on both sides it
     falls back to monotonic counter comparison and then to value equality;
   - live overlays expire before the same-sequence rejection, so a vanishment can
     not be undone by a replayed row;
   - past `MAX_SESSIONS` (4096) the total becomes unavailable rather than partial,
     and stays unavailable until the selected chat changes;
   - `sample_rate` drops non-finite rates; `total_rate` drops a non-finite total;
   - an unsupported daemon clears all state and shows `STREAM TPS -`.

Verdict STATIC: no parent/descendant, retry, restart, alias or overflow path can
add one session's rate twice. The sum is per-session duration-weighted rate, as
documented.

## 3. Priority 1 — capability gating of the new daemon protocol fields (STATIC: gated)

- `daemon_protocol.rs:274` advertises `StreamingThroughput` in
  `DAEMON_DEFAULT_SERVER_CAPABILITIES`; schema id is
  `protocol-7-schema-44-streaming-throughput`; `GenerationThroughput` is retained
  only so pre-44 hello frames parse and is never advertised (line 189, 3191).
- Client roster projection (`agent_connection/roster_subscription.rs:207`)
  requires `connected && schema_revision >= 44 && capability
  "streaming_throughput"`, and always strips legacy `generationThroughput`
  (`project_throughput`, line 212).
- Routed client (`daemon/daemon_routed_client.rs`
  `routed_server_capability_supported`) requires the CONTROL PLANE (supervisor)
  to be connected and to advertise the capability, so a new worker behind an old
  supervisor cannot switch the number on.
- Client state additionally self-clears when the field disappears
  (`Throughput::observe` supported check), so a capability loss cannot leave the
  last value on screen.
- AGENTS.md compliance: optional metadata, capability gated, `DAEMON_SCHEMA_REVISION`
  bumped to 44, both new-client/old-daemon and old-client/new-daemon tests added
  (`daemon_protocol.rs:3159` and `:3189`), and `.changes/streaming-tps-family-sum.md`
  exists. No startup dependency and no new command were added.

## 4. Review findings (no source edits made; items 1-3 are risks, not proven defects)

R1 (P4, static) `generation_throughput.rs:139-145` `retired_generations` and
`family_keys` grow without pruning inside one root (one generation string per
restart per session, one key per alias). Memory only; no effect on the number.

R2 (design note, not a defect) `contributions()` keeps a completed descendant's
last `finalized` rate after its roster row disappears, so the on-screen sum can
include a finished agent until the selected chat changes. This is the stated
PR #174 design ("Keep completed observed descendants when they leave the roster"),
so it is only flagged for the record.

R3 (P4, static, not reproduced) `providers/anthropic.rs:572-589` checks the
cancellation token before awaiting the next chunk, and the wait races only the
idle timeout (default 120 s, `utils/sse_frames.rs:75`). A cancel that arrives
during a silent stall is therefore seen at the provider reader only on the next
chunk or after the idle timeout. In practice this is masked because prompt
cancellation aborts the producer task
(`utils/event_stream.rs:121 request_cancel` -> `abort.abort()`), which drops the
reader future. Reproducer, if anyone wants proof: loopback SSE fixture that sends
one chunk, then nothing, cancel the token mid-stall, and measure the time to the
`aborted` terminal. Not verified here.

R4 (coverage note) `core/performance_monitor/throughput.rs` `seen` is capped at
256 terminal attempts. A duplicate terminal for an attempt older than 256 would
be counted again. Only reachable if the recorder delivers the same attempt id
twice across a 256-attempt window, which the agent loop does not do today.

## 5. Items deliberately not touched

- `crates/pi-agent-core/**` (StreamingMs producer, agent loop): w1 area.
- `crates/pi-coding-agent/src/modes/daemon/**` and `modes/agent_connection/**`:
  read only, to verify gating; not edited.
- Compaction and session-manager internals: round 1 already fixed the lease
  guards, checked fork writes and torn-head quarantine; I did not re-litigate
  them and did not edit them.
- The 13 `tests/compaction_*.rs` suites and the two large parity suites:
  specified as jobs, not run.

## 6. Job specs for the parent (in priority order)

Flag order matters: cargo flags before `--`, libtest flags after.

J1 TPS family sum + counter + protocol gating (highest value):
  cargo test --locked -p pi-coding-agent --lib -j 2 -- throughput_ --test-threads=1
  Expected matches: 16 `generation_throughput` tests, 5
  `performance_monitor::throughput` tests, 2 `daemon_protocol` streaming-compat
  tests, 2 routed-client tests, 1 `roster_subscription` projection test.

J2 Provider streaming fixtures, whole pi-ai lib (micro-benchmarks are `#[ignore]`,
so they do not run):
  cargo test --locked -p pi-ai --lib -j 2 -- --test-threads=1

J3 Session persistence, slash matrix, settings limits (all offline, temp dirs):
  cargo test --locked -p pi-coding-agent -j 2 --test session_manager_torn_head_recovery --test slash_command_matrix --test session_metadata_allocations --test configured_output_limits --test scenario_stream_persist -- --test-threads=1

J4 Compaction recovery subset (drop the two parity suites unless they are already
in the gate):
  cargo test --locked -p pi-coding-agent -j 2 --test compaction_recovery --test compaction_fidelity --test compaction_summary_guard --test compaction_parallel_fallback --test compaction_model_threshold -- --test-threads=1

J5 Native CLI, which PR #174 edited (contains throughput assertions):
  cargo test --locked -p pi-coding-agent -j 2 --test native_cli -- --test-threads=1

## 7. Untracked file that must be run or removed

`crates/pi-coding-agent/tests/scenario_stream_persist.rs` (638 lines, untracked,
header says "Campaign section 35 end-to-end scenarios owned by w6-providers").
It comes from the interrupted worker in my area and is NOT verified. It covers
scenario K (provider dies mid tool call: the engine must not execute the partial
call and must not record a fabricated tool result, engine level and through the
real `AgentSession`, plus reopen) and scenario L (failed session write keeps the
previous file byte-identical, reports the error, rolls back memory, then recovers).

I checked every API it uses against the source (create_agent_session_services and
create_agent_session_from_services option fields, AgentSession fields,
AgentHandle::messages, SessionManager::create/open/append_custom_entry_with_rollback/
get_entries (SessionEntry = Map<String, Value>), AssistantMessageEvent variants,
AgentTool/AgentToolResult, agent_loop arity, pi_agent_core ContentBlock::text).
It is compile-plausible; it needs the J3 run.

One weakness to fix when it is run: line 634 asserts
`outcome.is_err() || outcome.is_ok()`, which is a tautology. Replace it with the
observed outcome once the run shows what `prompt_and_wait` actually returns for an
interrupted stream.

## 8. Static consistency check performed (no build)

`tests/slash_command_matrix.json` has 48 rows = 43 canonical commands in
`core/slash_commands.rs::canonical_builtin_slash_commands` plus 5 aliases
(`clear->new`, `usage->context`, `thinking->effort`, `rename->name`, `side->btw`)
from `builtin_slash_command_aliases`. Counters 33 OK + 15 UNVERIFIED = 48, 0
DIVERGES, 0 UNHANDLED. No canonical command is missing from the matrix and no
matrix row lacks a canonical source. The runtime guard is J3.
