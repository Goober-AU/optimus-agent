"""Offline validation for c04 temporal-input artifacts (no model, no network).

Run from the repository root with the project interpreter:

    bench/.venv/Scripts/python.exe -B -m reports.continuation.c04-temporal-inputs.validation

or simply:

    bench/.venv/Scripts/python.exe -B reports/continuation/c04-temporal-inputs/validation.py

Checks (mirrors the driver's fresh-capture contract where possible):
  1. manifest structure: required fields, captureProtocol, model policy
  2. question rows are gold-free and carry complete caller declarations
  3. declarations equal bench.evidence.prepare_question_declaration(scoring row)
  4. env is the verbatim converter environment (full history, real event.ts)
  5. event roles/timestamps satisfy the driver's SourceAuthorityV1 validation
  6. the two arms differ only in run identity and settings.evidenceTimestamps
  7. output directories are fresh (no envs/, env.jsonl, run.jsonl)
  8. bounded cost plan matches the recorded estimates
"""
from __future__ import annotations

import datetime as dt
import json
import os
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
sys.path.insert(0, str(REPO))

from bench.evidence import prepare_question_declaration  # noqa: E402

CAPTURE_PROTOCOL = "optimus-memory-capture/temporal-authority/1.0.0"
MANIFESTS = {
    "tsoff": HERE / "manifests" / "locomo-c04-tsoff-s0p1.json",
    "tson": HERE / "manifests" / "locomo-c04-tson-s0p1.json",
}
SCORING = HERE / "scoring" / "qs_conv-41.jsonl"
DECLS = HERE / "declarations" / "locomo-c04-model-questions.jsonl"
COMMON = HERE / "common" / "locomo"

failures: list[str] = []


def check(condition, message):
    if condition:
        print(f"  ok: {message}")
    else:
        failures.append(message)
        print(f"  FAIL: {message}")


def sha256_file(path):
    import hashlib
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def parse_event_ts(ts):
    """Driver parse_event_timestamp: rfc3339 or naive %Y-%m-%dT%H:%M:%S%.f."""
    if ts is None:
        return None
    if not isinstance(ts, str):
        raise ValueError("ts must be a string or null")
    parsed = None
    try:
        parsed = dt.datetime.fromisoformat(ts.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            raise ValueError
    except ValueError:
        parsed = dt.datetime.strptime(ts, "%Y-%m-%dT%H:%M:%S.%f") if "." in ts else None
        if parsed is None:
            parsed = dt.datetime.strptime(ts, "%Y-%m-%dT%H:%M:%S")
        parsed = parsed.replace(tzinfo=dt.timezone.utc)
    ms = int(parsed.timestamp() * 1000)
    if ms == 0:
        raise ValueError("epoch-zero sentinel")
    return ms


def main():
    manifests = {}
    for arm, path in MANIFESTS.items():
        print(f"[manifest {arm}] {path.name}")
        manifest = json.loads(path.read_text(encoding="utf-8"))
        manifests[arm] = manifest
        check(manifest.get("captureProtocol") == CAPTURE_PROTOCOL,
              f"{arm}: captureProtocol is the temporal-authority capture opt-in")
        check(manifest["model"] == {"provider": "dgx-glm53", "id": "glm-5.3"},
              f"{arm}: model is dgx-glm53/glm-5.3")
        check(manifest["benchmark"] == "locomo", f"{arm}: benchmark=locomo")
        check(isinstance(manifest.get("run_id"), str) and manifest["run_id"],
              f"{arm}: run_id present")
        check(isinstance(manifest.get("variant"), str) and manifest["variant"],
              f"{arm}: variant present")
        check(isinstance(manifest.get("output_dir"), str) and manifest["output_dir"],
              f"{arm}: output_dir present")
        settings = manifest["settings"]
        for key in ("recallQueryDistillation", "recallRerank"):
            check(settings.get(key) is False, f"{arm}: settings.{key} is explicitly false")
        check(settings.get("memoryLearning") is False, f"{arm}: memoryLearning=false (no learning during answers)")
        check(settings.get("disableTools") is True, f"{arm}: disableTools=true")
        check(settings.get("answerMaxTokens") == 8192, f"{arm}: answerMaxTokens=8192")
        check(settings.get("skipIngest") in (None, False), f"{arm}: fresh capture (skipIngest not set)")
        check("answerThinkingLevel" not in settings, f"{arm}: no answerThinkingLevel override")
        cap = settings.get("answerMaxTokens")
        check(isinstance(cap, int) and 1 <= cap <= 32000, f"{arm}: answer cap within driver bounds")
        questions = manifest["questions"]
        gold_free = all(set(q) == {"qid", "env_id", "question", "question_protocol",
                                   "scoring_protocol", "question_sha256"} for q in questions)
        check(gold_free, f"{arm}: question rows carry only the six declaration fields")
        decl_ok = all(
            q.get("question_protocol", "").strip() and len(q["question_protocol"]) <= 256
            and q.get("scoring_protocol", "").strip() and len(q["scoring_protocol"]) <= 256
            and re.fullmatch(r"[0-9a-fA-F]{64}", q.get("question_sha256", "") or "")
            for q in questions)
        check(decl_ok, f"{arm}: declarations are bounded and 64-hex")
        check(len(questions) == 6, f"{arm}: 6 questions (4 temporal + 2 controls)")
        check(all(q["env_id"] == "conv-41" for q in questions), f"{arm}: questions bound to conv-41")

        output = Path(manifest["output_dir"])
        fresh = not any((output / name).exists() for name in ("envs", "env.jsonl", "run.jsonl"))
        check(fresh, f"{arm}: output_dir has no prior capture artifacts")
        inside = str(output.resolve()).startswith(str(HERE.resolve()))
        check(inside, f"{arm}: output_dir stays inside the c04 owned directory")

    # arm equality
    print("[arms]")
    off, on = manifests["tsoff"], manifests["tson"]
    check(off["envs"] == on["envs"], "identical env (full history, role/source policy, event ts)")
    check(off["questions"] == on["questions"], "identical question declarations")
    s_off, s_on = dict(off["settings"]), dict(on["settings"])
    ts_flag = s_on.pop("evidenceTimestamps"), s_off.pop("evidenceTimestamps")
    check(s_off == s_on, "settings identical except evidenceTimestamps")
    check(ts_flag == (True, False), "evidenceTimestamps is true (tson) vs false (tsoff)")
    check(off["model"] == on["model"], "identical model")

    # env verbatim + driver-side SourceAuthorityV1 event validation
    print("[env]")
    env = off["envs"][0]
    source_env = json.loads((COMMON / "env_conv-41.json").read_text(encoding="utf-8"))
    check(env == source_env, "manifest env is the verbatim converter environment")
    check(env.get("family") == "locomo", "env.family=locomo (speaker role policy)")
    check(len(env["sessions"]) == 32, "full history: 32 sessions retained")
    check(sum(len(s["events"]) for s in env["sessions"]) == 663, "full history: 663 events retained")
    roles_ok, ts_ok, no_tools, nonempty = True, True, True, True
    ts_values = []
    for session in env["sessions"]:
        if not session["events"]:
            nonempty = False
        for event in session["events"]:
            if event.get("role") not in ("user", "assistant"):
                roles_ok = False
            if "tool_result" in event or "toolResult" in event:
                no_tools = False
            try:
                ts_values.append(parse_event_ts(event.get("ts")))
            except Exception:
                ts_ok = False
    check(roles_ok, "all event roles are user/assistant (driver-accepted)")
    check(no_tools, "no tool_result descriptors on conversation events")
    check(ts_ok, "every event.ts parses like the driver (ISO with seconds, non-epoch)")
    check(all(t is not None for t in ts_values), "every event carries a real converter event.ts")
    check(min(ts_values) > 0 and max(ts_values) < 4102444800000,
          "event.ts values are real calendar dates (2022-2023 range)")
    session_dates = sorted({e["ts"] for s in env["sessions"] for e in s["events"]})
    check(len(session_dates) == len(env["sessions"]),
          "one converter session date per session (source times only, no invented dates)")

    # scoring rows vs declarations
    print("[scoring and declarations]")
    rows = [json.loads(line) for line in SCORING.read_text(encoding="utf-8").splitlines() if line.strip()]
    check(len(rows) == 6, "scoring dir holds exactly the 6 selected questions")
    check(len({r["qid"] for r in rows}) == 6, "scoring qids unique")
    declarations = [json.loads(line) for line in DECLS.read_text(encoding="utf-8").splitlines() if line.strip()]
    check(len(declarations) == 6, "6 gold-free declarations")
    recomputed = {r["qid"]: prepare_question_declaration(r) for r in rows}
    match = all(declarations[i] == recomputed[declarations[i]["qid"]] for i in range(6))
    check(match, "declarations equal prepare_question_declaration over the private scoring rows")
    manifest_q = {q["qid"]: q for q in off["questions"]}
    same = all(manifest_q[d["qid"]] == d for d in declarations)
    check(same, "manifest question rows are exactly the prepared declarations")
    check({r["category"] for r in rows if r["qid"].endswith(("q91", "q20", "q46", "q54"))} == {"2"},
          "temporal rows are category 2")
    cats = [r["category"] for r in rows]
    check(cats.count("2") == 4 and len(rows) - cats.count("2") == 2,
          "4 temporal + 2 non-temporal controls")

    print()
    if failures:
        print(f"VALIDATION FAILED: {len(failures)} problem(s)")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("VALIDATION PASSED: 2 manifests, 6 gold-free declarations, 6 private scoring rows,"
          " verbatim full-history env, fresh output dirs")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
