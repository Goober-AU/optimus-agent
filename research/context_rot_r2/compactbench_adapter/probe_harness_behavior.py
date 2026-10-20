"""Reproduce the harness-binary findings the adapter design depends on.

Offline, deterministic, no model calls. Run with any Python (no compactbench
needed):

    python research/context_rot_r2/compactbench_adapter/probe_harness_behavior.py

Findings against the FIXED binary (commit 49f473d8d: continue_recent session
persistence, non-zero usage anchors, flush-on-append); the F3 threshold
sweep was re-pinned at HEAD for D5 (commit 3b08e3f2e: the clamped rows now
pin the no-op). Findings marked REVERSED contradict the W6 probe that ran
against the pre-fix binary:

F1  [REVERSED] Sessions persist across invocations. continue_recent reopens
    the most recent session file in the session dir; entries accumulate
    (message/compaction interleaving) and the turn-map sidecar stays in sync.
    Constraint: the session entry's `cwd` must equal the session dir, so a
    COPIED session dir is not continuable — a naive clone silently forks a
    fresh session (verified below).
F2  [unchanged] The compaction cut must immediately precede a USER turn;
    otherwise the pipeline rejects the handoff ("unusable handoff" error).
F3  [refined] Tail sizing: walking backwards, message j joins the kept tail
    iff the estimated size of the tail WITHOUT j (cum[j+1], chars/4 + 0.6
    per message) is <= keepRecentTokens; the cut sits before the first
    message that does not fit. On a session with a previous compaction the
    walk CLAMPS at the previous firstKeptEntryId: an oversized
    keepRecentTokens empties the summarize window, which since D5
    (3b08e3f2e) resolves to a no-op (status ok, "no compaction prepared"
    warning, nothing summarized, nothing appended) instead of the pre-D5
    empty-window summary-of-summary.
F4  [REVERSED] tokensBefore/compactedContextTokens are non-zero: assistant
    turns carry a conservative ceil(chars/4) usage anchor (the estimator
    anchors on the last assistant turn, undercounting prior user turns).
F5  [REVERSED] Appends are flushed and the turn map stored BEFORE the
    compaction runs, so an error-path exit leaves the session holding the
    new turns deterministically: a follow-up invocation with newTurns=[] and
    a valid keepRecentTokens compacts the extended session (this is what
    makes same-dir ladder retries safe).
D1  Delta-append round trip: sequential invocations on one session dir with
    continuing turn ids re-compact the live session; summarized+kept
    partition the cumulative turn map at the cut (which precedes a user
    turn), the ledger carries constraints forward across calls, and the
    summary embeds the decayed previous summary (repeated-compaction
    semantics).
"""

from __future__ import annotations

import json
import os
import shutil
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

CONTINUATIONS = [
    ("user", "What should we tackle next?"),
    ("assistant", "Given the constraints, I suggest we start with the pricing tiers."),
    ("user", "Anything else to wire in before we move on?"),
    ("assistant", "The rate-limit rule and the pricing tiers remain the open threads."),
    ("user", "Which risk concerns you most right now?"),
    ("assistant", "Ignoring the rate limits remains the top risk."),
]

_FAILED: list[str] = []


def check(condition: bool, message: str) -> None:
    if condition:
        print(f"    PASS: {message}")
    else:
        print(f"    FAIL: {message}")
        _FAILED.append(message)


def request(session_dir: str, turns: list[tuple[str, str]], keep_recent: float,
            first_id: int = 0) -> dict:
    return {
        "sessionDir": session_dir,
        "newTurns": [
            {"role": role, "content": content, "turnId": first_id + index}
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


def turn_map(root: str) -> list[int]:
    path = Path(root) / ".rot2-turn-map.json"
    return json.loads(path.read_text()) if path.is_file() else []


def cum_estimates(turns: list[tuple[str, str]]) -> list[float]:
    """cum[j] = estimated tokens of turns[j:] at chars/4 + 0.6 per message."""
    cumulative = [0.0] * (len(turns) + 1)
    for index in range(len(turns) - 1, -1, -1):
        cumulative[index] = cumulative[index + 1] + len(turns[index][1]) / 4 + 0.6
    return cumulative


def finding_f1_sessions_persist(root: str) -> None:
    print("F1 [REVERSED]: session persistence across invocations in one session dir")
    case = os.path.join(root, "f1").replace("\\", "/")
    first = invoke(request(case, STARTER_SHAPE, 32.5))
    second = invoke(request(case, CONTINUATIONS[:2], 32.5, first_id=8))
    files = session_files(case)
    print(f"  call 1: status={first['status']} summarized={first['summarizedTurnIds']} "
          f"kept={first['keptTurnIds']}")
    print(f"  call 2 (+2 turns, ids 8,9): status={second['status']} "
          f"summarized={second['summarizedTurnIds']} kept={second['keptTurnIds']}")
    for path in files:
        print(f"  session file {path.name}: entries={entry_types(path)}")
    check(len(files) == 1, "one session file after two invocations (was: a new file per call)")
    check(
        entry_types(files[0])[:9] == ["session"] + ["message"] * 8,
        "the reopened session contains the original 8 messages",
    )
    check("compaction" in entry_types(files[0]), "a compaction entry exists")
    check(turn_map(case) == list(range(10)), "turn-map sidecar holds all 10 appended ids")

    print("  clone check: a copied session dir must NOT be continuable")
    clone = os.path.join(root, "f1-clone").replace("\\", "/")
    shutil.copytree(case, clone)
    third = invoke(request(clone, CONTINUATIONS[2:], 32.5, first_id=10))
    clone_files = session_files(clone)
    print(f"  call 3 (on clone): status={third['status']} "
          f"kept={third['keptTurnIds']} warnings={third['warnings']}")
    check(len(clone_files) == 2, "the clone forked a NEW session (cwd mismatch is honored)")
    print()


def finding_f2_cut_must_precede_user(root: str) -> None:
    print("F2 [unchanged]: cut-point alignment (starter-shaped transcript, keepRecentTokens sweep)")
    outcomes = {}
    for keep_recent in (20.0, 30.0, 40.0):
        case = os.path.join(root, f"f2-{keep_recent:.0f}").replace("\\", "/")
        resp = invoke(request(case, STARTER_SHAPE, keep_recent))
        outcomes[keep_recent] = resp
        outcome = (
            f"kept={resp['keptTurnIds']}"
            if resp["status"] == "ok"
            else f"ERROR: {str(resp['error'])[:60]}"
        )
        print(f"  keepRecentTokens={keep_recent:.0f}: status={resp['status']} {outcome}")
    check(outcomes[20.0]["status"] == "error", "20 lands before an assistant turn -> error")
    check(outcomes[30.0]["status"] == "ok" and outcomes[30.0]["keptTurnIds"] == [5, 6, 7],
          "30 lands before turn 5 (user) -> ok")
    check(outcomes[40.0]["status"] == "error", "40 lands before an assistant turn -> error")
    print()


def finding_f3_tail_estimator(root: str) -> None:
    print("F3 [refined]: tail sizing = 'message j joins the tail iff cum[j+1] <= keepRecentTokens'")
    for boundary in (7, 5, 3):
        case = os.path.join(root, f"f3-{boundary}").replace("\\", "/")
        cumulative = cum_estimates(STARTER_SHAPE)
        keep_recent = (cumulative[boundary] + cumulative[boundary + 1]) / 2
        resp = invoke(request(case, STARTER_SHAPE, keep_recent))
        expected = list(range(boundary, len(STARTER_SHAPE)))
        check(resp["keptTurnIds"] == expected,
              f"midpoint for boundary {boundary} keeps exactly {expected}")

    print("  threshold sweep on a delta session (live window = turns 5..9):")
    # Build the state "call-1 compacted (window 5..7), delta turns 8,9 appended,
    # not yet compacted" by extending with a deliberately bad keepRecentTokens
    # (error path: appends are flushed, no compaction entry - see F5).
    case = os.path.join(root, "f3-delta").replace("\\", "/")
    invoke(request(case, STARTER_SHAPE, 32.5))
    extended = STARTER_SHAPE + CONTINUATIONS[:2]
    cumulative = cum_estimates(extended)
    bad = (cumulative[9] + cumulative[10]) / 2 - 5.0
    invoke(request(case, CONTINUATIONS[:2], bad, first_id=8))
    # Row kinds: "error" = unusable handoff; "tail" = lands with that kept
    # tail; "noop" = the clamped cut empties the summarize window, which
    # since D5 (3b08e3f2e) resolves to a no-op instead of the pre-D5
    # empty-window summary-of-summary.
    for keep_recent, kind, expected_kept in (
        (cumulative[9] - 0.5, "error", None),       # below cum[9]: tail=[9] (assistant) -> error
        (cumulative[9] + 0.5, "tail", [8, 9]),      # above cum[9]: tail = [8, 9]
        (cumulative[8] + 0.5, "tail", [7, 8, 9]),   # above cum[8]: tail = [7, 8, 9]
        (cumulative[6] + 0.5, "noop", None),        # above cum[6]: clamped at window start
        (500.0, "noop", None),                      # far beyond: still clamped
    ):
        probe = os.path.join(root, f"f3-delta-{keep_recent:.2f}").replace("\\", "/")
        shutil.copytree(case, probe)
        # repair the cloned session's cwd so it is continuable (see F1)
        for path in session_files(probe):
            with open(path, encoding="utf-8") as handle:
                lines = handle.read().splitlines()
            first = json.loads(lines[0])
            first["cwd"] = probe
            lines[0] = json.dumps(first)
            with open(path, "w", encoding="utf-8", newline="\n") as handle:
                handle.write("\n".join(lines) + "\n")
        resp = invoke(request(probe, [], keep_recent, first_id=8))
        if kind == "error":
            check(resp["status"] == "error",
                  f"K={keep_recent:.2f} errors (cut lands before the new assistant turn)")
        elif kind == "tail":
            check(resp["status"] == "ok" and resp["keptTurnIds"] == expected_kept,
                  f"K={keep_recent:.2f} keeps {expected_kept}")
        else:
            whole_session = list(range(10))
            check(resp["status"] == "ok" and resp["summarizedTurnIds"] == []
                  and resp["summary"] == "" and resp["keptTurnIds"] == whole_session,
                  f"K={keep_recent:.2f} is a D5 no-op: nothing summarized, "
                  f"the whole session stays live")
            check(any("no compaction prepared" in w for w in resp["warnings"])
                  and entry_types(session_files(probe)[0]).count("compaction") == 1,
                  f"K={keep_recent:.2f} warns no-compaction-prepared and appends nothing")
    print()


def finding_f4_usage_anchors(root: str) -> None:
    print("F4 [REVERSED]: token accounting is non-zero with the fixture")
    case = os.path.join(root, "f4").replace("\\", "/")
    first = invoke(request(case, STARTER_SHAPE, 32.5))
    second = invoke(request(case, CONTINUATIONS[:2], 32.5, first_id=8))
    print(f"  call 1: tokensBefore={first['tokensBefore']} "
          f"compactedContextTokens={first['compactedContextTokens']}")
    print(f"  call 2: tokensBefore={second['tokensBefore']} "
          f"compactedContextTokens={second['compactedContextTokens']}")
    check(first["tokensBefore"] > 0, "call 1 tokensBefore > 0 (was 0.0 pre-fix)")
    check(second["tokensBefore"] > 0, "call 2 tokensBefore > 0")
    check(first["compactedContextTokens"] > 0, "call 1 compactedContextTokens > 0")
    print()


def finding_f5_error_path_durability(root: str) -> None:
    print("F5 [REVERSED]: appends survive an error-path exit (flush-on-append)")
    case = os.path.join(root, "f5").replace("\\", "/")
    invoke(request(case, STARTER_SHAPE, 32.5))
    cumulative = cum_estimates(STARTER_SHAPE + CONTINUATIONS[:2])
    bad = (cumulative[9] + cumulative[10]) / 2 - 5.0  # cut before the new assistant turn
    failed = invoke(request(case, CONTINUATIONS[:2], bad, first_id=8))
    print(f"  errored call (+2 turns): status={failed['status']} "
          f"error={str(failed['error'])[:60]}")
    check(failed["status"] == "error", "the bad cut errors out (unusable handoff)")
    check(turn_map(case) == list(range(10)),
          "the turn-map sidecar already holds the 2 new turns after the error")
    recovered = invoke(request(case, [], 20.525, first_id=8))
    print(f"  retry (newTurns=[], valid K): status={recovered['status']} "
          f"summarized={recovered['summarizedTurnIds']} kept={recovered['keptTurnIds']}")
    check(recovered["status"] == "ok", "a same-dir retry with zero new turns compacts")
    check(recovered["keptTurnIds"] == [8, 9], "the retry keeps the new pair")
    check(len(session_files(case)) == 1, "still exactly one session file")
    print()


def finding_d1_delta_round_trip(root: str) -> None:
    print("D1: delta-append round trip over three calls (repeated compaction)")
    case = os.path.join(root, "d1").replace("\\", "/")
    turns = list(STARTER_SHAPE)
    sent = 0
    for call in range(3):
        delta = CONTINUATIONS[2 * call:2 * call + 2] if call else turns
        resp = invoke(request(case, delta, 20.525 if call else 32.5, first_id=sent))
        turns = turns + list(delta)
        sent += len(delta)
        expected_map = list(range(sent))
        partition = resp["summarizedTurnIds"] + resp["keptTurnIds"]
        print(f"  call {call}: delta={len(delta)} status={resp['status']} "
              f"summarized={resp['summarizedTurnIds']} kept={resp['keptTurnIds']} "
              f"tokensBefore={resp['tokensBefore']}")
        check(resp["status"] == "ok", f"call {call} compacted")
        check(turn_map(case) == expected_map, f"call {call} turn map = {expected_map}")
        check(sorted(partition) == expected_map,
              f"call {call} summarized+kept partition the cumulative map")
        check(partition == expected_map, f"call {call} partition is prefix|suffix ordered")
        kept_head = resp["keptTurnIds"][0]
        check(turns[kept_head][0] == "user", f"call {call} cut precedes a user turn")
    ledger = resp["ledger"] or {}
    constraints = [e.get("text", "") for e in ledger.get("constraints", [])]
    print(f"  final ledger constraints: {constraints}")
    check(any("rate limits" in c for c in constraints),
          "the turn-2 constraint survives three compactions via the ledger")
    check("(decayed prior summary)" in resp["summary"],
          "the third summary embeds the decayed previous summary")
    check(len(session_files(case)) == 1, "one session file across all three calls")
    print()


def main() -> int:
    if not HARNESS.is_file():
        print(f"harness binary not found: {HARNESS}", file=sys.stderr)
        return 1
    with tempfile.TemporaryDirectory(prefix="rot2-w12-probe-") as root:
        finding_f1_sessions_persist(root)
        finding_f2_cut_must_precede_user(root)
        finding_f3_tail_estimator(root)
        finding_f4_usage_anchors(root)
        finding_f5_error_path_durability(root)
        finding_d1_delta_round_trip(root)
    if _FAILED:
        print(f"{len(_FAILED)} probe check(s) FAILED:")
        for message in _FAILED:
            print(f"  - {message}")
        return 1
    print("all probe checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
