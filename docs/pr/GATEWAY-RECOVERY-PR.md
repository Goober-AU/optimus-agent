# fix(refinement): recover the local model gateway and fail loudly

## Problem

This host routes Optimus Agent's model traffic through a local gateway:
`http://127.0.0.1:43120/azure-openai/v1` and sibling routes. The gateway runs
as a Windows scheduled task (`OptimusAgentCloudModelGateway`, `node.exe
cloud-model-gateway\gateway.mjs`). It has no boot trigger and no restart
policy.

Timeline of the 2026-10-13 incident:

- **2026-10-13T10:56Z** — a machine reboot killed the scheduled task
  (`0xC000013A`). It never restarted. Port 43120 went dark. Other local
  gateway services were also down from the same reboot; they are unrelated to
  Optimus Agent.
- Provider calls then hung until they timed out (~30 s each).
- The refinement reviewer (`/refine`) calls its model through that provider.
  Every refinement from **2026-10-13T14:10Z** failed in the
  "Reviewing refinement" stage after ~30 s.
- **55 consecutive failures, zero lessons saved.** Last successful save:
  **2026-10-13T13:40Z**.
- The failures were silent in daemon/RLM sessions. The `RefineFailed` event
  is wire-only today (evidence below). No record landed in `refinements.jsonl`.
  Nothing was written to the session transcript. The agent kept answering
  "refinement queued" with no error. The two-day gap was found by hand after
  the fact.

Recovery was trivial once noticed. `Start-ScheduledTask` brought the gateway
back in ~1-2 s, and an HTTP 401 on the port confirmed liveness. The failure
mode was not that the gateway is hard to start. It was that nothing detected
the dead port, and nothing recorded the lost refinements.

## Root cause

Two gaps compound each other.

1. **A single hard dependency with no liveness handling.** The only gateway
   Optimus Agent uses is a scheduled task with no boot trigger and no restart
   policy, so a reboot leaves the port dead until someone intervenes. When
   the port is dead, every call waits out the full timeout. Nothing detects
   "loopback provider is down", nothing starts the gateway, nothing retries
   the call.

2. **`RefineFailed` is wire-only.** The event reaches attached clients but is
   never persisted (citations in the Evidence section):

   - `emit_refine_failed`
     (`crates/pi-coding-agent/src/core/agent_session.rs:18649-18653`) only
     calls `self.emit`, and `emit`
     (`crates/pi-coding-agent/src/core/agent_session.rs:3629-3637`) is an
     in-process listener fan-out with no store write.
   - The success path persists the refinement outcome, the model-facing
     notice, and a `refinements.jsonl` history line
     (`crates/pi-coding-agent/src/core/agent_session.rs:15438-15458`). The
     failure path performs none of those writes.
   - `refinements.jsonl` is written only by `append_global_refinement`
     (`crates/pi-coding-agent/src/core/refinement/refinement.rs:1090-1110`),
     which is called only from the success path at
     `crates/pi-coding-agent/src/core/agent_session.rs:15444`.

## Fix summary

- **`gateway_health` module** — detects loopback provider base URLs, runs the
  configured start command, waits a bounded time for the port to accept
  connections, and wraps the model call with a single retry. Cancelled
  requests are never retried.
- **Typed `localGateway` settings** — four keys under `settings.json`
  (table below). `autoStart` defaults to `false`, so recovery is opt-in.
- **Failure persistence** — a failed refinement writes a `status: failed`
  record to the harness refinement history (`refinements.jsonl`) and a
  persisted transcript notice, in addition to the existing wire event. The
  attached daemon CLI already printed `RefineFailed`; that behavior is
  unchanged.

## Settings

| Key | Default | Meaning |
| --- | --- | --- |
| `localGateway.autoStart` | `false` | Run the start command when a loopback provider call fails. Opt-in. |
| `localGateway.startCommand` | *(none)* | Shell command that starts the local gateway. Without it, auto-start stays inert. |
| `localGateway.probeTimeoutMs` | `1500` | TCP connect probe timeout against the gateway port, in milliseconds. |
| `localGateway.startTimeoutMs` | `20000` | Bounded wait after running the start command for the port to accept connections, in milliseconds. |

A missing `localGateway` object resolves to these defaults. An existing
object round-trips verbatim on unrelated settings saves, including unknown
keys.

## Testing

- **Loopback truth table** — which host/port combinations count as a loopback
  provider base URL.
- **Connection-failure classifier** — which failures are "gateway down" and
  eligible for recovery, versus errors that keep the single-attempt contract.
- **`AlreadyUp` / `Recovered` / `Failed` / `NotApplicable` outcomes** —
  exercised against ephemeral TCP listeners, so no real gateway is needed.
- **Failure-record persistence round-trip** — a failed refinement produces a
  `status: failed` record that reads back from the harness refinement
  history.
- **Notice-on-failure** — a failed refinement produces a persisted transcript
  notice.
- **Settings defaults and round-trip** — defaults with no `localGateway`
  object; a missing object stays missing when unrelated settings save; an
  existing object survives a save verbatim with unknown keys; global and
  project scopes deep-merge per field.

## Rollout note

Gateway auto-start is opt-in. `localGateway.autoStart` defaults to `false`,
and without a `startCommand` the feature stays inert, so existing deployments
are unaffected until configured. Failure persistence is always on: failed
refinements are recorded regardless of the `localGateway` settings.

## Evidence

All citations are against this PR's base (`upstream/main` @ `159ce68cc`),
i.e. the state the fix changes.

**`RefineFailed` is emitted but never persisted:**

- Event definition: `crates/pi-coding-agent/src/core/agent_session.rs:837-839`
  (`AgentSessionEvent::RefineFailed { error }`; type name `"refine_failed"`
  at `:874`).
- Emission helper: `crates/pi-coding-agent/src/core/agent_session.rs:18649-18653`
  (`emit_refine_failed` → `self.emit` only).
- `emit` is listener fan-out with no store write:
  `crates/pi-coding-agent/src/core/agent_session.rs:3629-3637`.
- Emission sites: manual `/refine` error path
  `crates/pi-coding-agent/src/core/agent_session.rs:12036`; serialized
  background-plan failure/skip/apply-error
  `crates/pi-coding-agent/src/core/agent_session.rs:8066-8108`; the second
  finish path's apply-error and explicit-failure arms
  `crates/pi-coding-agent/src/core/agent_session.rs:15765` and `:15811`;
  auto-refine failure `crates/pi-coding-agent/src/core/agent_session.rs:18640`.

**The success path persists; the failure path does not:**

- Success path writes harness state, a `refinements.jsonl` line, a session
  custom entry, the durable outcome message, the model-facing notice, then
  emits `RefineComplete`:
  `crates/pi-coding-agent/src/core/agent_session.rs:15438-15458`
  (`append_global_refinement_reported` at `:15444`,
  `append_custom_entry` at `:15448-15451`,
  `record_refinement_outcome` at `:15452`, `record_refinement_notice` at
  `:15453`).
- `refinements.jsonl` writer is success-only:
  `crates/pi-coding-agent/src/core/refinement/refinement.rs:1090-1110`
  (`append_global_refinement`), called only from
  `crates/pi-coding-agent/src/core/agent_session.rs:15444`.
- Durable refine messages go through
  `append_durable_refine_message`
  (`crates/pi-coding-agent/src/core/agent_session.rs:16188-16198`), reached
  only from `record_refinement_outcome` (`:16179`) and
  `record_refinement_notice` (`:15463`) — both success-only.

**The event is wire-only after emission:**

- In-process adapter serializes `AgentSessionEvent` into the wire
  `AgentConnectionSessionEvent` and sends it on a channel:
  `crates/pi-coding-agent/src/core/agent_session_runtime/in_process_adapter.rs:557-569`.
- Wire event definition:
  `crates/pi-coding-agent/src/modes/agent_connection/types.rs:1066-1067`;
  JSON shape `{"error": ...}`:
  `crates/pi-coding-agent/src/core/agent_session/runtime_bridge.rs:275`.
- Daemon connection forwards the event to the attached client and excludes
  it from streaming observation:
  `crates/pi-coding-agent/src/modes/agent_connection/daemon_agent_connection.rs:2436-2463`
  (the `refine_complete`/`refine_failed` exclusion at `:2438`).
- Consumers are display-only: TUI clears the progress indicator and shows the
  error (`crates/pi-coding-agent/src/modes/interactive/native_host.rs:4881-4886`
  and `:5819-5824`); the attached daemon CLI prints it
  (`crates/pi-coding-agent/src/cli/daemon_command.rs:1974-1977`); ACP maps it
  to an ephemeral `session_info_update`
  (`crates/pi-coding-agent/src/modes/acp/acp_events.rs:461-473`).
- The daemon's owned-session worker subscribes to session events only to
  track the recovery descriptor, and only for `message_start` and
  `session_info_changed`:
  `crates/pi-coding-agent/src/cli/owned_session_worker.rs:305-318`.

**The incident's failing stage is real:** the "Reviewing refinement" progress
stage is set at
`crates/pi-coding-agent/src/core/agent_session.rs:15131`.
