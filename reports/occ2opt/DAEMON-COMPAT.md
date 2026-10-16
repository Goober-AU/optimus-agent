# OCC-2 offline daemon/session compatibility investigation

Worktree: `optimus-occ2opt-20261016` (HEAD `1863b5dea`; OCC line: PR #155 → #158 `0c8167c17` → #159 `a5d74ea3d` + `cddf0cde1`).
Method: offline source audit + git archaeology + an executable Python reproduction (`research/occ2opt_analysis/repro_daemon_compat.py`, 7 scenarios, no daemon/network/model) + a ready-to-run Rust integration test (`tests/occ2opt_scratch.rs` + `tests/occ2opt_scratch/occ_daemon_compat_repro.rs`; no Rust toolchain exists in this sandbox, so the Rust file is unexecuted here — run with `cargo test -p pi-coding-agent --test occ2opt_scratch`).

Verdict up front:
1. `/occ` today is **client-side storage mutation** keyed on strings the daemon reports. It is correct only for same-build, same-machine, absolute-path client/daemon pairs.
2. Both mixed-version directions are **silent no-ops**: `/occ` prints a success panel while the daemon's encoding never changes (reproduced A, B).
3. The PR #158 wrong-profile write is real for the PR #158 client (reproduced C1) and was **fixed for local daemons** by PR #159's occ.json (reproduced C2), but residue failures remain for relative session files (C3) and remote/mismatched-filesystem daemons (C4).
4. Nothing in the protocol distinguishes the two storage semantics: schema 40, `DAEMON_SCHEMA_ID`, and the `occ2_communication` capability are **byte-identical** across the PR #158 → PR #159 semantic change (`git diff 0c8167c17..HEAD -- daemon_protocol.rs` is empty).

---

## 1. End-to-end flow map (file:line per hop)

### 1.1 TUI command entry
- `/occ` dispatch: `modes/interactive/native_host_commands.rs:558` → `occ_control::run` (module included at `native_host_commands.rs:6-7`).
- Argument parsing and menu: `modes/interactive/occ_control.rs:10-33`.

### 1.2 Client-side gate and state fetch
- Capability gate: `occ_control.rs:36-38` calls `connection.supports_occ2_communication()`.
  - Trait default `false`: `modes/agent_connection/types.rs:1347`.
  - Daemon impl = advertised server capability: `modes/agent_connection/daemon_agent_connection.rs:2840-2842` (`self.client.supports_server_capability("occ2_communication")`).
  - In-process impl = always `true`: `modes/agent_connection/in_process_agent_connection.rs:381`.
  - The capability comes from the daemon_hello handshake: `modes/daemon/daemon_server.rs:78-88` sends `schemaId`, `schemaRevision`, `serverCapabilities`; parsed by `modes/daemon/daemon_client.rs:99-125` (`DaemonHello::supports`).
- Session identity: `occ_control.rs:39-40` — `connection.get_state()` → daemon RPC `get_connection_state` (`daemon_agent_connection.rs:2812-2829`) → daemon handler `modes/daemon/daemon_mode.rs:5842-5849` → `create_connection_state` (`daemon_mode.rs:9831-9859`) → `snapshot::create_agent_connection_state` (`modes/agent_connection/snapshot.rs:55-85`, `session_file` at :73) — the **daemon-side absolute session file path** (built as `<agent_dir>/sessions/<id>.jsonl`, `core/session_manager.rs:3153-3158`; `agent_dir` from daemon config, `daemon_mode.rs:2415-2428`; tilde-expanded but not absolutized by the launcher, `cli/daemon_launch.rs:1062-1073`).

### 1.3 Client-side write (PR #159, current)
- Read current value: `occ_control.rs:41` → `core/occ/session_settings.rs:25-87` (`load`: nearest `occ.json` in the ancestry, bounded 4 KiB reads, cycle detection, max 64 hops; `rlmDepth == 0` or no `parentSession` → default off; forks at depth 0 never inherit, `session_settings.rs:44-46`).
- Write: `occ_control.rs:53` → `session_settings::save` (`session_settings.rs:101-105`): atomic temp-file rename into `preference_path(session_file)` = `dirname(dirname(session_file))/session-artifacts/<id>/occ.json` (`session_settings.rs:17-19`, `core/session_manager.rs:3183-3230`; artifacts root is a **sibling of the sessions dir**, i.e. `<agent_dir>/session-artifacts`).
- The TUI process performs this write **on the client's filesystem view**. Same machine + absolute path → it is the daemon's real artifact dir. Relative path → resolved against the **client CWD**; remote daemon → invisible to the daemon.

### 1.4 Client-side write (PR #158, still-running old clients)
- `git show 0c8167c17:modes/interactive/occ_control.rs:39-70`: `SettingsManager::create(&state.cwd, None)` → agent_dir `None` resolves in the **client process** to `PRIME_AGENT_CODING_AGENT_DIR` or `~/.prime/agent` (`core/settings_manager.rs:819-826`); `set_agent_communication_occ2_enabled` writes the **global scope only** (`settings_manager.rs:1914-1919`) → `<client-resolved profile>/settings.json`, key `agentCommunication.occ2Enabled` (`settings_manager.rs:868`).

### 1.5 Daemon read (per send, no cache)
- Live sessions: `core/agent_session/subagent_runs.rs:70-72` `occ2_enabled()` = `session_settings::enabled(self.session_file())`, called from:
  - direct sends: `core/agent_messages.rs:1405-1406` (`occ2_send_policy` → `wrap_outgoing_body`);
  - broadcasts: `agent_messages.rs:1314-1315` (per-recipient policy);
  - S2 spawn seam (prompt to a freshly spawned child): `subagent_runs.rs:551-559`.
  - Controller plumbing: `subagent_runs.rs:53-65` (`SessionMessageController::occ2_send_policy`), default disabled (`agent_messages.rs:1236-1239`).
- PR #158 daemon read the global/project settings instead: `git show 0c8167c17:core/agent_session/subagent_runs.rs:69-76` (`settings_manager.get_agent_communication_occ2_enabled()`); current tree keeps the getter (`settings_manager.rs:1910-1912`, merged global+project view) but **no production reader remains** (only `daemon_session_list.rs:931` asserts it is off in a test).

### 1.6 Per-chat isolation and subagent inheritance
- Isolation: preference is keyed by session file; two chats in one daemon/profile do not share it — asserted in `tests/native_cli.rs:815-830` (real daemon, two sessions, `session_settings::save/load` direct) and unit tests `session_settings.rs:108-134`.
- Inheritance: dynamic ancestry walk at each read (`session_settings.rs:46-60`): child reads its own `occ.json`, else follows `parentSession` while `rlmDepth > 0`; nearest explicit preference wins; forks (depth 0 with parent) do not inherit; no process cache → existing workers see the next toggle without restart (`session_settings.rs:23-24`, `subagent_runs.rs:69-72`).
- Peer advertisement (S1 negotiation input): family roster summaries carry `occ_capability` only when the target chat (or its ancestor chain) has the preference on — live sessions `daemon_mode.rs:13709-13713` (used at :13783), saved/passive subagents `daemon_mode.rs:13824-13826` (used at :13855). Dialect selection: `subagent_runs.rs:94-105` (`select_occ2_dialect(local, peer, registry, true)`), spawn seam negotiates `OccCapability::here()` against itself (`subagent_runs.rs:108-123`).
- Receive side: `agent_messages.rs:777-812` splits dual payloads at `#FALLBACK-OCC1` and validates before the symbolic section is shown to the model (`agent_session_message_prompt_body`), so a chat whose toggle is off still decodes incoming dual bodies via the OCC-1 fallback (codec-level safety is independent of the toggle).

---

## 2. Reproduction results

Runnable offline: `python research/occ2opt_analysis/repro_daemon_compat.py` (mirrors the exact path math and read/write semantics of the cited Rust code; one cosmetic deviation: Windows path separators are normalized where Rust would keep mixed separators — same resolved directory). Rust mirror: `cargo test -p pi-coding-agent --test occ2opt_scratch`.

| # | Scenario | Result |
|---|----------|--------|
| A | NEW CLIENT → OLD DAEMON (v0.1.33.0/PR #158): `/occ 2 on` | **Silent no-op reproduced.** Gate passes (old daemon advertises `occ2_communication`); client writes `occ.json`; old daemon reads only `agentCommunication.occ2Enabled` from its settings.json → effective OCC-2 stays OFF while `/occ` prints the success panel. |
| B | OLD CLIENT → NEW DAEMON (HEAD/PR #159): `/occ 2 on` | **Silent no-op + pollution reproduced.** Gate passes; old client writes `agentCommunication.occ2Enabled=true` into the **client's own default profile** settings.json; new daemon reads only `occ.json` → effective OFF. The stale write can re-enable OCC-2 for *other old daemons* sharing that profile. |
| C1 | OLD CLIENT, daemon with foreign agentDir (PR #158 review P1) | **Wrong-profile write reproduced.** Daemon profile settings.json untouched (byte-identical), daemon-effective OFF, unrelated client profile mutated. |
| C2 | NEW CLIENT, foreign agentDir, LOCAL daemon, absolute session file | **Works** (expected behavior): write keyed on the daemon-reported sessionFile lands in the daemon's own `session-artifacts` dir; subagent inherits. PR #159 fixed the foreign-agentDir case for local daemons. |
| C3 | NEW CLIENT, daemon started with a RELATIVE agentDir/session file | **Silent no-op reproduced.** `preference_path("sessions/x.jsonl")` stays relative → resolved against the **client** CWD; with differing CWDs the client creates a bogus local `session-artifacts` tree the daemon never reads. |
| C4 | NEW CLIENT, remote daemon (different filesystem view) | **Silent no-op reproduced.** Client writes occ.json on its own filesystem; daemon reads its own path → effective OFF. |
| D | Schema/capability discrimination | **No discriminator.** PR #158 build and HEAD advertise identical `schemaRevision` 40, identical `schemaId` `"protocol-7-schema-40-occ2-communication"`, and both list `occ2_communication`, while storage semantics are incompatible. The client gate (`occ_control.rs:36`) therefore passes in both cross-version directions. |

Written reproduction trace for a live daemon (if one is run later): TUI `/occ 2 on` → `daemon_hello` lists `occ2_communication` → `get_connection_state` returns the daemon's `sessionFile` → client writes `<agent_dir>/session-artifacts/<id>/occ.json` → (old daemon) encoding decision at `subagent_runs.rs:70-72` of the old build reads settings.json only → next `agent_message.send` from that chat stays OCC-1 → the neon flash and `/occ status` (client-side read of the same occ.json) show ON. That is the silent divergence, end to end.

---

## 3. Schema check

- Declaration: `modes/daemon/daemon_protocol.rs:80-81` — `DAEMON_SCHEMA_REVISION = 40`, `DAEMON_SCHEMA_ID = "protocol-7-schema-40-occ2-communication"`. Revision-history comment for 40 at `daemon_protocol.rs:78-79`: "advertises optional OCC-2 support… No new commands or startup requirement."
- Sent in the handshake: `daemon_server.rs:78-88` (`schemaId`, `schemaRevision`, `serverCapabilities`).
- **No version gate exists for OCC preference semantics.** `Occ2Communication` (`daemon_protocol.rs:172`, listed at :254) gates only whether the *client* shows `/occ`. It was introduced in PR #158 and its meaning silently changed in PR #159 (global settings.json → per-chat occ.json) with zero protocol change: `git diff 0c8167c17..HEAD -- crates/pi-coding-agent/src/modes/daemon/daemon_protocol.rs` is **empty**.
- Old daemons and unknown **fields**: bodies are parsed as loose JSON and handlers read known keys only (e.g. `daemon_mode.rs:5392-5411`), so additive response/request fields are ignored, not errors.
- Old daemons and unknown **commands**: rejected loudly — `daemon_mode.rs:4337-4345` replies `success:false, "Unknown daemon command: <name>"` (also proven by `daemon_server.rs:239-246`). This is the safety property a daemon-owned RPC can rely on.
- Conclusion: a schema bump alone cannot fix this (40 means both things), and the capability name must not be reused for changed semantics.

---

## 4. Proposed design (DESIGN ONLY — not implemented)

Goal: `/occ` get/set becomes **daemon-owned**, capability-gated, and degrades visibly on old daemons. OCC-1 stays the default and fallback everywhere.

### 4.1 New negotiated capability
- Add `DaemonServerCapability::OccSessionPreferences` (wire name `occ_session_preferences`) to `DAEMON_DEFAULT_SERVER_CAPABILITIES` (`daemon_protocol.rs:223-255`) and bump `DAEMON_SCHEMA_REVISION` to 41 with `DAEMON_SCHEMA_ID = "protocol-7-schema-41-occ-session-preferences"` plus a revision-history comment (pattern: revisions 34-40 comments, `daemon_protocol.rs:70-79`).
- **Remove `Occ2Communication` from the advertised list** (it has no other consumer — its only reader is the `/occ` gate, `daemon_agent_connection.rs:2840-2842`). Consequence: an old client attached to a new daemon sees `/occ` as unsupported (visible warning, no write) instead of a silent no-op. Keep the enum variant so old hello frames still parse.
- `supports_occ2_communication()` is replaced by `supports_occ_session_preferences()` on the connection trait (default `false`, `types.rs:1347` pattern; in-process returns `true`).

### 4.2 New session-plane commands (follow the `jev_set_session_mode` precedent, `daemon_mode.rs:5392-5436`)
Register in the command table and compatibility map (`daemon_protocol.rs:2148-2193`, session plane list at :2234-2246):

```
"occ_get_preferences" | "occ_set_preferences" =>
    DaemonCommandCompatibility::capability(Capability::OccSessionPreferences)
```

Request/response shapes (camelCase wire, `activeSessionId` keyed like every session-plane command):

```jsonc
// occ_get_preferences
{ "type": "occ_get_preferences", "id": "<req-id>", "activeSessionId": "<active-session-id>" }
// -> success
{ "id": "<req-id>", "success": true, "occ2Enabled": true,
  "inherited": false,            // true when the value comes from an ancestor chat
  "sourceSessionId": "<id-or-null>" } // which chat's occ.json decided, null = default off
// -> failure (no bound session / unreadable preference)
{ "id": "<req-id>", "success": false, "error": "This chat has no saved session identity" }

// occ_set_preferences
{ "type": "occ_set_preferences", "id": "<req-id>", "activeSessionId": "<active-session-id>",
  "occ2Enabled": true }
// -> success (daemon-authoritative echo)
{ "id": "<req-id>", "success": true, "occ2Enabled": true, "applied": true,
  "scope": "this chat",          // fixed value; subagents inherit, other chats unchanged
  "effectiveForNewMessages": true }
```

Daemon handler (new arm in the `daemon_mode.rs` command match, near the jev arms):
- Resolve the bound session exactly like `jev_session_id`/`get_bound_session_state` (`daemon_mode.rs:5399-5411`); error if none.
- get: `session_settings::load(session.session_file())` (`session_settings.rs:25`) — surface load errors (cycle/oversize/schema) as failure, matching `occ_control.rs:41`'s current error surfacing.
- set: `session_settings::save(session.session_file(), value)` (`session_settings.rs:101`) — the **daemon** writes into its own artifact dir: correct profile by construction, works for foreign agentDir, remote-safe (no client filesystem assumption), and relative-agentDir daemons resolve against their own CWD.
- Optionally publish a footer/roster refresh in the same turn (pattern: `publish_jev_attach_footer`, `daemon_mode.rs:5411-5413`) so the TUI reflects the toggle immediately.

### 4.3 Client flow (rewritten `occ_control.rs`)
1. `if !connection.supports_occ_session_preferences()` → Warning: "This daemon does not support OCC-2 controls. Restart it with Optimus Agent v0.1.34 or later; OCC-1 remains available." (no storage touched).
2. Status/menu: `occ_get_preferences` and render from the daemon response.
3. Set: `occ_set_preferences` and render the echoed effective value; a failure is surfaced as an error (never a success panel).
- Add trait methods `AgentConnection::{get_occ_preferences, set_occ_preferences}` (default `Err("not supported")`, `types.rs:1330-1358` pattern). In-process impl calls `session_settings::{load,save}` directly; daemon impl sends the RPC via `request_data` (pattern: `get_jev_status`, `daemon_agent_connection.rs:2851-2880`, including the session-changed guard).
- This removes the client-side path math entirely; `preference_path` stays daemon-internal.

### 4.4 Version matrix after the change
| Pair | Behavior |
|------|----------|
| new client → new daemon | RPC get/set; correct profile always; remote/foreign agentDir safe. |
| new client → old daemon (PR #158/159 builds) | `occ_session_preferences` absent → visible warning, **no write**. Backstop: even a bug that sent the RPC gets `"Unknown daemon command"` (`daemon_mode.rs:4337-4345`). |
| old client (PR #158) → new daemon | `occ2_communication` no longer advertised → visible "not supported" warning, **no wrong-profile write, no pollution**. |
| old client (PR #159) → new daemon | Same as above (its gate also checks `occ2_communication`). |
| old client → old daemon | Unchanged (global settings semantics both sides). |
- Old daemons stay safely on OCC-1: they never receive the new commands (capability-gated clients), and if they do, they fail loudly. New fields in existing responses are ignored by old clients (loose JSON parsing).

### 4.5 Notes
- Do **not** reuse `occ2_communication` for the new semantics; do not rely on `schemaRevision` alone (40 is ambiguous across the semantic change; capabilities are the discriminant clients actually parse, `daemon_client.rs:110-125`).
- Keep `occ.json` on disk as the storage (restart/compaction/resume survive); the RPC is a transport change, not a storage change.
- The `get_connection_state`/family-roster `occ_capability` advertisement (`daemon_mode.rs:13709-13713`, `13824-13826`) is unchanged and remains the S1 negotiation input.

---

## 5. Required test list

Mixed versions (simulate old peers by hand-built `daemon_hello` frames, pattern `daemon_client.rs:1608-1626`):
1. New client + old daemon (capabilities without `occ_session_preferences`): `/occ` shows the warning; **no occ.json and no settings.json is written**.
2. New client sends `occ_set_preferences` to a daemon that does not advertise the capability → client-side gate blocks it; forced-send test gets `"Unknown daemon command"` failure (extend `daemon_server.rs:239-246` style test).
3. Old client (PR #158 behavior: writes global settings.json) + new daemon: daemon-effective OCC-2 stays off; daemon profile and client profile both unpolluted by the new build (regression: the new daemon must not advertise `occ2_communication`).
4. Old client + old daemon: unchanged global-settings behavior (compatibility baseline, pinned via the PR #158 read path).

Daemon-owned RPC:
5. `occ_get_preferences` before/after `occ_set_preferences` on the same chat (round-trip; echo is daemon-authoritative).
6. `occ_set_preferences` with no bound session → failure "no saved session identity".
7. Corrupt/oversized/cyclic `occ.json` → get returns a failure (not a silent false).

Scope semantics (extend `session_settings.rs:108-134` unit tests through the RPC):
8. Independent chats: two chats on one daemon/profile; toggling one leaves the other off (today only via direct library calls, `tests/native_cli.rs:815-830`; add the protocol-level version).
9. Nested subagents: child and grandchild inherit; child override wins for its subtree; removing the child override re-inherits the parent.
10. Forks at `rlmDepth == 0` do not inherit.
11. Live toggle: an already-running subagent's **next** `agent_message.send` uses the new preference without restart (no-cache property through the RPC path).

Durability:
12. Daemon restart: preference survives (occ.json on disk; re-attach shows the same value).
13. Compaction: preference unaffected (session file path is stable across compaction; assert after a compact).
14. Session resume/fork via `switch_session`/`fork`: resumed chat keeps its preference; a new chat starts off.

Transport/paths (the residue defects C3/C4):
15. Daemon with a foreign agentDir: client never computes the path; RPC succeeds; preference lands in the daemon's artifact dir (assert via a daemon-side read RPC, not the client filesystem).
16. Relative-agentDir daemon (or simulated remote daemon): RPC still works (write happens daemon-side); the old client-side write path is gone.

Advertisement:
17. Hello handshake lists `occ_session_preferences` and does **not** list `occ2_communication`; `DAEMON_SCHEMA_ID` asserts the new revision (extend `daemon_protocol.rs:3099-3160` tests).
18. `/occ status` renders daemon-truth (including `inherited`/`sourceSessionId`) rather than a client-local read.

---

## Appendix: files read beyond the task list
The read list covered the core files. The following were read minimally (specific symbols only) to trace hops and verify the design precedent, per the "file:line for every hop" requirement: `modes/agent_connection/{types.rs,daemon_agent_connection.rs,in_process_agent_connection.rs}` (trait + capability gate + state fetch), `modes/agent_connection/snapshot.rs:55-85` (session_file in connection state), `core/session_manager.rs:3153-3230` (session file and artifact path math), `modes/daemon/{daemon_client.rs,daemon_server.rs}` (hello handshake, unknown-command failure), `core/agent_messages.rs:740-812,1226-1416` (send policy consumption, dual-body split), `core/agent_session.rs:8593-8596` (session_file getter), `core/agent_session_runtime/daemon_adapter.rs:601-605`, `modes/interactive/{jev_host.rs,jev_menu.rs}` and `cli/daemon_launch.rs:1062-1073` (design precedent, agentDir resolution). No production file was modified; new files only: this report, `research/occ2opt_analysis/repro_daemon_compat.py`, `tests/occ2opt_scratch.rs`, `tests/occ2opt_scratch/occ_daemon_compat_repro.rs`.
