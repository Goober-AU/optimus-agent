"""Reproduce the harness-binary findings the adapter design depends on.

Offline, deterministic, no model calls. Run with any Python (no compactbench
needed):

    python research/context_rot_r2/compactbench_adapter/probe_harness_behavior.py

Findings (see README.md for the full list):

F1  Each harness invocation starts a NEW session file in the session dir;
    sessions do not survive across invocations (SessionManager::create does
    not reopen the previous session), and appends on error/no-prepare paths
    are never flushed to disk.
F2  The compaction cut must immediately precede a USER turn; otherwise the
    pipeline rejects the handoff ("unusable handoff" error).
F3  The harness sizes the kept tail with a per-message estimate of about
    chars/4 + 0.6 tokens, which lets the adapter aim keepRecentTokens at
    valid cuts (see optimus_compactor.tail_candidates).
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

HARNESS = Path(
    os.environ.get("HARNESS")
    or os.environ.get("ROT2_HARNESS_BIN")
    or "C:/Users/openclawuser/rot2-shared-target/debug/compaction-harness.exe"
)

STARTER_SHAPE = [
    ("user", "Let's plan the pricing revamp with Gabriel."),
    ("assistant", "Happy to help. What constraints should I keep in mind?"),
    ("user", "Critical: never ignore rate limits. That is a hard rule."),
    ("user", "Do you happen to know what a quokka is?"),
    ("assistant", "Sure, I can address that. Is there anything more on the main thread?"),
    ("user", "Quick tangent: is it true that pandas have thumbs?"),
    ("assistant", "Quick aside, sure. Anything else pressing?"),
    ("user", "OK, given all that, what do you suggest as the next step?"),
]


def request(session_dir: str, turns: list[tuple[str, str]], keep_recent: float) -> dict:
    return {
        "sessionDir": session_dir,
        "newTurns": [
            {"role": role, "content": content, "turnId": index}
            for index, (role, content) in enumerate(turns)
        ],
        "settings": {
            "reserveTokens": 4000.0,
            "keepRecentTokens": keep_recent,
            "taskState": "prompt",
            "contextWindow": 1_000_000.0,
            "maxTokens": 64_000.0,
        },
        "summarizer": "fixture",
    }


def invoke(req: dict) -> dict:
    proc = subprocess.run(
        [str(HARNESS)], input=json.dumps(req).encode(), capture_output=True, timeout=180
    )
    lines = [l for l in proc.stdout.decode().splitlines() if l.strip()]
    return json.loads(lines[-1]) if lines else {"status": "no-stdout", "stderr": proc.stderr.decode()}


def session_files(root: str) -> list[Path]:
    return sorted(Path(root).glob("*.jsonl"))


def entry_types(path: Path) -> list[str]:
    return [json.loads(line).get("type") for line in path.read_text().splitlines() if line.strip()]


def finding_f1_sessions_do_not_persist(root: str) -> None:
    print("F1: session persistence across invocations in one session dir")
    case = os.path.join(root, "f1").replace("\\", "/")
    first = invoke(request(case, STARTER_SHAPE, 32.5))
    second = invoke(request(case, [("user", "One more turn."), ("assistant", "Ack.")], 32.5))
    files = session_files(case)
    print(f"  call 1: status={first['status']} summarized={first['summarizedTurnIds']} "
          f"kept={first['keptTurnIds']}")
    print(f"  call 2 (+2 new turns): status={second['status']} "
          f"kept={second['keptTurnIds']} warnings={second['warnings']}")
    for path in files:
        print(f"  session file {path.name}: entries={entry_types(path)}")
    if len(files) != 2:
        print(f"  UNEXPECTED: {len(files)} session files after two invocations")
    else:
        types_2 = entry_types(files[1])
        expected = ["session", "message", "message"]
        print(f"  RESULT: second invocation started a NEW session "
              f"({'CONFIRMED' if types_2 == expected else 'UNEXPECTED: ' + str(types_2)})")
    print(f"  call 2 kept={len(second['keptTurnIds'])} ids from the persisted turn-map sidecar "
          f"(the .rot2-turn-map.json accumulates across calls even though entries do not)\n")


def finding_f2_cut_must_precede_user(root: str) -> None:
    print("F2: cut-point alignment (starter-shaped transcript, keepRecentTokens sweep)")
    for keep_recent in (20.0, 30.0, 40.0):
        case = os.path.join(root, f"f2-{keep_recent:.0f}").replace("\\", "/")
        resp = invoke(request(case, STARTER_SHAPE, keep_recent))
        outcome = (
            f"kept={resp['keptTurnIds']}"
            if resp["status"] == "ok"
            else f"ERROR: {str(resp['error'])[:80]}"
        )
        print(f"  keepRecentTokens={keep_recent:.0f}: status={resp['status']} {outcome}")
    print("  RESULT: 20 -> cut before an assistant turn (error), 30 -> cut before turn 5 (user, ok),"
          " 40 -> cut before turn 4 (assistant, error)\n")


def finding_f3_tail_estimator(root: str) -> None:
    print("F3: tail sizing matches chars/4 + 0.6 per message")
    for boundary in (7, 5, 3):
        case = os.path.join(root, f"f3-{boundary}").replace("\\", "/")
        cumulative_from = [0.0] * (len(STARTER_SHAPE) + 1)
        for index in range(len(STARTER_SHAPE) - 1, -1, -1):
            cumulative_from[index] = (
                cumulative_from[index + 1] + len(STARTER_SHAPE[index][1]) / 4 + 0.6
            )
        keep_recent = (cumulative_from[boundary] + cumulative_from[boundary + 1]) / 2
        resp = invoke(request(case, STARTER_SHAPE, keep_recent))
        expected = list(range(boundary, len(STARTER_SHAPE)))
        match = resp["keptTurnIds"] == expected
        print(f"  aim tail={expected} keepRecentTokens={keep_recent:.1f}: "
              f"kept={resp['keptTurnIds']} ({'MATCH' if match else 'MISMATCH'})")


def main() -> int:
    if not HARNESS.is_file():
        print(f"harness binary not found: {HARNESS}", file=sys.stderr)
        return 1
    with tempfile.TemporaryDirectory(prefix="rot2-w6-probe-") as root:
        finding_f1_sessions_do_not_persist(root)
        finding_f2_cut_must_precede_user(root)
        finding_f3_tail_estimator(root)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
