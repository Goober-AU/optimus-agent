#!/usr/bin/env python3
"""Offline reproduction of the OCC-2 daemon/client compatibility defects.

Worktree: optimus-occ2opt-20261016 (post PR #159, commit cddf0cde1..HEAD).
No daemon process, no network, no model calls. This script mirrors the exact
path math and storage semantics of the Rust sources, cited per function:

  - preference_path / get_session_artifact_path_for_file
        crates/pi-coding-agent/src/core/occ/session_settings.rs:17-19
        crates/pi-coding-agent/src/core/session_manager.rs:3183-3230
  - session_settings::load / save (occ.json schema, ancestry walk, bounds)
        crates/pi-coding-agent/src/core/occ/session_settings.rs:25-105
  - PR#158-era client write path (global settings.json, client-resolved profile)
        git show 0c8167c17:crates/pi-coding-agent/src/modes/interactive/occ_control.rs
        settings_manager.rs:1007 (create), :819-826 (get_agent_dir), :1914-1919 (set)
  - PR#158-era daemon read path (project-then-global agentCommunication.occ2Enabled)
        git show 0c8167c17:crates/pi-coding-agent/src/core/agent_session/subagent_runs.rs:69-76
  - daemon hello advertisement (schema 40 + occ2_communication, identical pre/post PR#159)
        crates/pi-coding-agent/src/modes/daemon/daemon_server.rs:78-88
        crates/pi-coding-agent/src/modes/daemon/daemon_protocol.rs:80-81, 223-255
        (git diff 0c8167c17..HEAD -- daemon_protocol.rs is EMPTY)

Each scenario prints OBSERVED behavior and compares it with the behavior the
user sees (/occ reports success). "DEFECT REPRODUCED" means the silent-no-op or
wrong-profile write happens exactly as the PR review threads describe.
"""

import json
import os
import sys
import tempfile
from pathlib import Path, PureWindowsPath

IS_WINDOWS = os.name == "nt"
MAIN_SEPARATOR = "\\" if IS_WINDOWS else "/"

OCC_SCHEMA = "optimus.occ-session.v1"
CONFIG_DIR_NAME = ".prime/agent"          # settings_manager.rs:28
AGENT_COMMUNICATION_KEY = "agentCommunication"
OCC2_ENABLED_KEY = "occ2Enabled"           # settings_manager.rs:868

RESULTS = []


def result(name, observed, detail, kind_kw="defect"):
    """kind_kw: 'defect' (expected failure mode reproduced), 'works' (expected
    correct behavior observed), 'info' (structural observation)."""
    RESULTS.append((name, observed, detail, kind_kw))
    if not observed:
        tag = "BEHAVIOR CHANGED - INVESTIGATE"
    elif kind_kw == "defect":
        tag = "DEFECT REPRODUCED"
    elif kind_kw == "works":
        tag = "EXPECTED BEHAVIOR"
    else:
        tag = "OBSERVED"
    print(f"[{tag}] {name}")
    print(f"    {detail}")
    print()


# ---------------------------------------------------------------- path math
def host_join(base: str, leaf: str) -> str:
    """Rust Path::join string semantics (host separator, keeps existing parts)."""
    return str(PureWindowsPath(base) / leaf) if IS_WINDOWS else str(Path(base) / leaf)


def dirname(p: str) -> str:
    d = str(PureWindowsPath(p).parent) if IS_WINDOWS else str(Path(p).parent)
    return d


def basename(p: str) -> str:
    return (PureWindowsPath(p) if IS_WINDOWS else Path(p)).name


def to_posix_path(value: str) -> str:
    return value.replace(MAIN_SEPARATOR, "/")


def join_posix(base: str, leaf: str) -> str:
    """session_manager.rs:3208-3218 - POSIX-normalised join for artifact paths."""
    base = to_posix_path(base)
    if base == "":
        return leaf
    if base.endswith("/") or leaf == "":
        return base + leaf
    return f"{base}/{leaf}"


def get_session_artifacts_root(session_dir: str) -> str:
    """session_manager.rs:3183-3191 - host join(dirname(session_dir), 'session-artifacts')."""
    return host_join(dirname(session_dir), "session-artifacts")


def get_session_artifact_path_for_file(session_file: str) -> str:
    """session_manager.rs:3224-3230 with session_id=None."""
    name = basename(session_file)
    sid = name[: -len(".jsonl")] if name.endswith(".jsonl") else name
    return join_posix(get_session_artifacts_root(dirname(session_file)), sid)


def preference_path(session_file: str) -> str:
    """session_settings.rs:17-19 - PathBuf::from(artifact_path).join("occ.json")."""
    return host_join(get_session_artifact_path_for_file(session_file), "occ.json")


# ------------------------------------------------- session_settings (PR#159)
def occ_load(session_file: str):
    """session_settings.rs:25-87 load(): nearest explicit occ.json wins,
    ancestry walk bounded at 64 with cycle detection, missing file -> False."""
    f = Path(session_file)
    visited = set()
    for _ in range(64):
        key = str(f)
        if key in visited:
            return ("err", "OCC session ancestry contains a cycle")
        visited.add(key)
        pref = preference_path(str(f))
        if Path(pref).is_file():
            data = Path(pref).read_bytes()
            if len(data) > 4096:
                return ("err", "OCC session preference is too large")
            try:
                value = json.loads(data)
            except Exception as e:
                return ("err", f"parse: {e}")
            if value.get("schema") != OCC_SCHEMA:
                return ("err", "Unsupported OCC session preference schema")
            return ("ok", bool(value["occ2Enabled"]))
        if not f.is_file():
            return ("ok", False)
        with open(f, "rb") as fh:
            header = fh.readline(65537)
        if len(header) > 65536:
            return ("err", "OCC session header is too large")
        try:
            hdr = json.loads(header)
        except Exception as e:
            return ("err", f"header parse: {e}")
        # Forks keep depth 0: independent chats never inherit (session_settings.rs:44-46)
        if int(hdr.get("rlmDepth") or 0) == 0:
            return ("ok", False)
        parent = hdr.get("parentSession")
        if not isinstance(parent, str) or not parent:
            return ("ok", False)
        p = Path(parent)
        f = p if p.is_absolute() else f.parent / p
    return ("err", "OCC session ancestry exceeds the supported depth")


def occ_enabled(session_file) -> bool:
    """session_settings.rs:96-99 enabled(): runtime sends fail closed."""
    if not session_file:
        return False
    kind, value = occ_load(session_file)
    return value if kind == "ok" else False


def occ_save(session_file: str, enabled: bool):
    """session_settings.rs:101-105 save(): atomic temp-file rename in the artifact dir."""
    if not session_file:
        return ("err", "This chat has no saved session identity")
    path = Path(preference_path(session_file))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"schema": OCC_SCHEMA, "occ2Enabled": enabled}) + "\n")
    return ("ok", None)


# ------------------------------------------------- SettingsManager semantics
def settings_read_global(agent_dir: str):
    p = Path(agent_dir) / "settings.json"
    if not p.is_file():
        return {}
    try:
        return json.loads(p.read_text())
    except Exception:
        return {}


def old_client_occ_write(cwd: str, client_profile: str, enabled: bool):
    """PR#158 occ_control.rs: SettingsManager::create(&state.cwd, None) with
    None -> get_agent_dir() == client process profile (settings_manager.rs:819-826).
    set_agent_communication_occ2_enabled writes GLOBAL scope only
    (settings_manager.rs:1914-1919), i.e. <client_profile>/settings.json,
    read-modify-write preserving unrelated keys."""
    p = Path(client_profile) / "settings.json"
    data = {}
    if p.is_file():
        try:
            data = json.loads(p.read_text())
        except Exception:
            data = {}
    data.setdefault(AGENT_COMMUNICATION_KEY, {})[OCC2_ENABLED_KEY] = enabled
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, indent=2))
    return str(p)


def old_daemon_occ_read(cwd: str, daemon_agent_dir: str) -> bool:
    """PR#158 subagent_runs.rs:69-76: project scope then global scope,
    key agentCommunication.occ2Enabled, default False.
    Project file lives at <cwd>/<CONFIG_DIR_NAME>/settings.json (settings_manager.rs:594,605)."""
    project = Path(cwd) / CONFIG_DIR_NAME / "settings.json"
    for candidate in (project, Path(daemon_agent_dir) / "settings.json"):
        if candidate.is_file():
            try:
                data = json.loads(candidate.read_text())
                value = data.get(AGENT_COMMUNICATION_KEY, {}).get(OCC2_ENABLED_KEY)
                if isinstance(value, bool):
                    return value
            except Exception:
                continue
    return False


def nested_agent_communication(path: Path):
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text()).get(AGENT_COMMUNICATION_KEY)
    except Exception:
        return None


# ------------------------------------------------- daemon hello advertisement
def daemon_hello(capabilities, schema_revision=40, schema_id="protocol-7-schema-40-occ2-communication"):
    """daemon_server.rs:78-88 frame shape."""
    return {
        "type": "daemon_hello",
        "protocol": {"name": "optimus-agent-daemon", "version": 7},
        "schemaId": schema_id,
        "schemaRevision": schema_revision,
        "appVersion": "0.1.33",
        "serverCapabilities": capabilities,
    }


def client_supports(hello, capability: str) -> bool:
    """daemon_client.rs:122-125 hello.supports() -> occ_control gate occ_control.rs:36."""
    return capability in hello["serverCapabilities"]


# The capability list is byte-identical between the PR#158 build (0c8167c17)
# and HEAD (verified: git diff 0c8167c17..HEAD -- daemon_protocol.rs is empty;
# both list DaemonServerCapability::Occ2Communication at daemon_protocol.rs:254).
CURRENT_SERVER_CAPABILITIES = [
    "attach_snapshot", "event_sequence", "extension_ui", "slim_attach",
    "chunked_snapshot", "history_ranges", "client_owned_sessions",
    "delete_rlm_subagent", "heartbeat_catalog", "heartbeat_management",
    "model_catalog", "side_question_transcript", "transient_bash",
    "session_input_admission", "prompt_admission_cancellation",
    "owned_prompt_cancellation", "queue_message_mutation",
    "authoritative_child_roster", "owned_session_recovery_context",
    "rlm_quiescence_barrier", "session_input_pause", "acp_mcp_servers",
    "jev_control", "jev_features", "jev_dynamic", "jev_memory",
    "execution_mode", "node_execution_mode", "clang_execution_mode",
    "agent_communication_activity", "occ2_communication",
]


def make_session(root: Path, sid: str, parent=None, depth=0, sessions_dir="sessions"):
    d = root / sessions_dir
    d.mkdir(parents=True, exist_ok=True)
    p = d / f"{sid}.jsonl"
    p.write_text(json.dumps({"type": "session", "id": sid, "rlmDepth": depth,
                             "parentSession": parent}) + "\n")
    return str(p)


def main():
    root = Path(tempfile.mkdtemp(prefix="occ2opt-repro-"))
    print(f"scratch root: {root}\n")

    # Shared topology: a daemon profile (foreign agentDir), a client profile,
    # a workspace cwd, one root chat + one child subagent.
    daemon_dir = root / "daemon-profile"          # daemon agentDir (foreign)
    client_dir = root / "client-profile"           # TUI process default profile
    workspace = root / "workspace"
    daemon_dir.mkdir(); client_dir.mkdir(); workspace.mkdir()

    hello_old = daemon_hello(CURRENT_SERVER_CAPABILITIES)   # PR#158 build
    hello_new = daemon_hello(CURRENT_SERVER_CAPABILITIES)   # HEAD build (identical)

    # ---- Scenario A: NEW CLIENT -> OLD DAEMON -----------------------------
    chat_a = make_session(root, "chatA", None, 0)
    gate = client_supports(hello_old, "occ2_communication")
    kind, err = occ_save(chat_a, True)                     # client-side write
    old_effective = old_daemon_occ_read(str(workspace), str(daemon_dir))
    result(
        "A. new client -> old daemon (v0.1.33.0/PR#158): /occ 2 on",
        gate and kind == "ok" and old_effective is False,
        f"capability gate passed={gate}; occ.json written at {preference_path(chat_a)}; "
        f"old daemon effective OCC-2={old_effective}. /occ printed the success status panel; "
        "the old daemon only reads agentCommunication.occ2Enabled from its settings.json, "
        "so the toggle is silently lost.",
    )

    # ---- Scenario B: OLD CLIENT -> NEW DAEMON ----------------------------
    chat_b = make_session(root, "chatB", None, 0)          # a different chat
    gate = client_supports(hello_new, "occ2_communication")
    written = old_client_occ_write(str(workspace), str(client_dir), True)
    new_effective = occ_enabled(chat_b)
    pollution = nested_agent_communication(Path(client_dir) / "settings.json")
    result(
        "B. old client -> new daemon (HEAD/PR#159): /occ 2 on",
        gate and new_effective is False and pollution == {OCC2_ENABLED_KEY: True},
        f"capability gate passed={gate}; old client wrote {written}; new daemon effective "
        f"OCC-2={new_effective} (it only reads occ.json). The stale write also left "
        f"agentCommunication.occ2Enabled=true in the client's own profile "
        f"({pollution}), which can re-enable OCC-2 for OTHER old daemons sharing it.",
    )

    # ---- Scenario C1: OLD CLIENT, FOREIGN agentDir (PR#158 review P1) -----
    daemon_before = nested_agent_communication(daemon_dir / "settings.json")
    written = old_client_occ_write(str(workspace), str(client_dir), True)
    daemon_after = nested_agent_communication(daemon_dir / "settings.json")
    daemon_effective = old_daemon_occ_read(str(workspace), str(daemon_dir))
    result(
        "C1. old client, daemon with foreign agentDir: /occ 2 on (PR#158 P1)",
        daemon_before is None and daemon_after is None and daemon_effective is False,
        f"client wrote {written}; daemon profile settings.json agentCommunication before="
        f"{daemon_before} after={daemon_after}; daemon-effective OCC-2={daemon_effective}. "
        "The daemon never sees the toggle; an unrelated local profile was mutated.",
    )

    # ---- Scenario C2: NEW CLIENT, FOREIGN agentDir, local daemon ----------
    chat_c = make_session(root, "chatC", None, 0)
    chat_c_child = make_session(root, "chatC-child", "chatC.jsonl", 1)
    state_session_file = chat_c                       # daemon-reported absolute path
    kind, err = occ_save(state_session_file, True)
    daemon_effective = occ_enabled(state_session_file)
    child_effective = occ_enabled(chat_c_child)
    result(
        "C2. new client, foreign agentDir, LOCAL daemon (absolute session file)",
        kind == "ok" and daemon_effective is True and child_effective is True,
        f"client wrote {preference_path(state_session_file)} derived from the daemon-reported "
        "sessionFile; on one machine this IS the daemon's artifact dir, so the daemon (and its "
        "subagent via ancestry) sees the toggle. PR#159 fixes the foreign-agentDir case for "
        "local daemons only.",
        kind_kw="works",
    )

    # ---- Scenario C3: NEW CLIENT, RELATIVE session file ------------------
    rel = "sessions/relchat.jsonl"
    pref = preference_path(rel)
    result(
        "C3. new client, daemon started with a RELATIVE agentDir/session file",
        not Path(pref).is_absolute(),
        f"preference_path('{rel}') = '{pref}' - resolved against the CLIENT process CWD, not "
        "the daemon CWD. If the two CWDs differ, the client creates a bogus local "
        "session-artifacts tree and the daemon never sees occ.json (silent no-op).",
    )

    # ---- Scenario C4: NEW CLIENT, REMOTE daemon --------------------------
    client_fs = root / "client-fs"; daemon_fs = root / "daemon-fs"
    remote_session = make_session(daemon_fs, "remotechat", None, 0)
    # The client sees the same STRING, but its filesystem is a different mount.
    local_view = client_fs / "sessions" / "remotechat.jsonl"
    occ_save(str(local_view), True)                   # client writes on ITS fs
    daemon_effective = occ_enabled(remote_session)     # daemon reads on ITS fs
    result(
        "C4. new client, remote daemon (different filesystem view)",
        daemon_effective is False,
        f"client wrote {preference_path(str(local_view))} on the client filesystem; daemon "
        f"reads {preference_path(remote_session)} on its own; daemon-effective={daemon_effective}. "
        "The write is invisible to the daemon (silent no-op).",
    )

    # ---- Scenario D: schema/capability advertisement parity --------------
    same_rev = hello_old["schemaRevision"] == hello_new["schemaRevision"] == 40
    same_id = hello_old["schemaId"] == hello_new["schemaId"]
    both_advertise = client_supports(hello_old, "occ2_communication") and \
                     client_supports(hello_new, "occ2_communication")
    result(
        "D. schema 40 + occ2_communication cannot distinguish storage semantics",
        same_rev and same_id and both_advertise,
        "PR#158 build and PR#159/HEAD build advertise identical schemaRevision (40), "
        "identical schemaId, and both list occ2_communication, while their /occ storage "
        "semantics are incompatible (global settings.json vs per-chat occ.json). The "
        "client-side gate (occ_control.rs:36) therefore passes in BOTH cross-version "
        "directions and cannot prevent A/B.",
    )

    print("=" * 72)
    defects = sum(1 for r in RESULTS if r[3] == "defect" and r[1])
    print(f"scenarios: {len(RESULTS)}; defects reproduced: {defects}")
    print("Conclusion: both mixed-version directions and the foreign-agentDir/relative/"
          "remote cases are silent no-ops today; only same-build, same-machine, "
          "absolute-path pairs work.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
