#!/usr/bin/env python3
"""loco-ansres round-5 re-run: FINAL9 answer-only runs on the 10 warm clause-set stores.

One job = one question, typeB cache-reuse (skipIngest copy-out), FINAL9 answer
instruction, local-F1 scoring (controller-managed). Groups: R = fail-latest
FINAL7-measured cells (1 draw; failing -> FINAL9-confirmed residual); F =
pass-latest STABLE/battery cells (flip candidates; a failing draw gets one
redraw a2 before the cell is left FAILED).

Usage: python run5.py <jobs.json> <out.json>
"""
import json, os, subprocess, sys, time

ROOT = "C:/Users/openclawuser/optimus-memory-bench-20261007-040424"
WT = ROOT + "/imp4b-ansres"
CTRL = WT + "/scripts/memory_question_loop.py"
DRIVER = ROOT + "/imp4b-target8k/release/examples/memory_bench.exe"
EXP = WT + "/reports/continuation/iteration4-experiments/loco-ansres"
BASE = EXP + "/base-det-settings.json"
SETTINGS = EXP + "/fullstack-final9-stem.json"
ATT_ROOT = ROOT + "/reports/continuation/iteration4/ql-attempts-ansres"
CACHE_ROOT = ROOT + "/reports/continuation/iteration4/ql-typeb-cache"
LEDGER = ROOT + "/reports/continuation/iteration4/QUESTION-LEDGER.json"
MODELS_JSON = ROOT + "/reports/continuation/c06-instruction-boundary/inputs/runs/locomo-c06-ib-system_append-q12-s0p1/envs/conv-30/agent/models.json"
JUDGE_MODELS = ROOT + "/reports/continuation/iteration2-inputs/lme-temporal-flip/judge-models.json"
BENCH_VENV = ROOT + "/repo-continuation/bench/.venv/Scripts/python.exe"
SCORING_CWD = ROOT

jobs = json.load(open(sys.argv[1], encoding="utf-8"))
out_path = sys.argv[2]

ENV = {
    "MEMORY_BENCH_MODEL_PROVIDER": "dgx-glm53",
    "MEMORY_BENCH_MODEL_ID": "glm-5.3",
    "MEMORY_BENCH_ROOT": ROOT,
}

results = {}

def ledger_attempt(run_id, qid, tries=10):
    for _ in range(tries):
        try:
            with open(LEDGER, encoding="utf-8") as h:
                L = json.load(h)
        except Exception:
            time.sleep(4)
            continue
        for e in L.get("entries", []):
            if e.get("qid") == qid:
                for a in (e.get("attempts") or []):
                    if a.get("run_id") == run_id:
                        return a
        time.sleep(4)
    return None

def invoke(qid, run_id, manifest):
    cmd = [BENCH_VENV, CTRL, qid, "--mode", "typeB",
           "--ledger", LEDGER,
           "--base-settings", BASE, "--settings", SETTINGS,
           "--env-from-manifest", manifest,
           "--driver", DRIVER, "--driver-workdir", ROOT + "/imp4",
           "--models-json", MODELS_JSON, "--judge-models-json", JUDGE_MODELS,
           "--bench-venv", BENCH_VENV, "--scoring-cwd", SCORING_CWD,
           "--attempts-root", ATT_ROOT, "--cache-root", CACHE_ROOT,
           "--run-id", run_id, "--worker", "ansres"]
    env = dict(os.environ); env.update(ENV)
    t0 = time.time()
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, cwd=WT, timeout=2400, env=env)
        rc, err = r.returncode, (r.stderr or "")[-600:]
    except subprocess.TimeoutExpired:
        rc, err = -9, "runner timeout 2400s"
    dt = time.time() - t0
    att = None
    try:
        att = ledger_attempt(run_id, qid)
    except Exception as exc:
        err = (str(err) + " ledger-read:" + str(exc))[-600:]
    row = {"run_id": run_id, "rc": rc, "err": err or None,
           "score": att.get("score") if att else None,
           "outcome": att.get("outcome") if att else None,
           "answer": att.get("answer_excerpt") if att else None,
           "failure_stage": att.get("failure_stage") if att else None,
           "mode_detail": att.get("mode_detail") if att else None,
           "cache_key": (att.get("cache_key") or "")[:16] if att else None,
           "latency_s": round(dt, 1)}
    return row

def run_job(job):
    qid, group = job["qid"], job["group"]
    base_run = job["run_id"]
    manifest = job["manifest"]
    draws = []
    row = None
    for attempt in range(3):
        row = invoke(qid, base_run, manifest)
        if row["rc"] in (0, 3, 4) or attempt == 2:
            break
        time.sleep(15)
    draws.append(row)
    if group == "F" and row["score"] is not None and row["score"] < 0.5:
        a2_id = base_run[:-3] + "-a2"
        for attempt in range(3):
            row2 = invoke(qid, a2_id, manifest)
            if row2["rc"] in (0, 3, 4) or attempt == 2:
                break
            time.sleep(15)
        draws.append(row2)
    results[qid] = {"group": group, "draws": draws}
    with open(out_path, "w", encoding="utf-8", newline="\n") as f:
        json.dump(results, f, indent=1)
        f.write("\n")
    best = max((d["score"] or 0) for d in draws)
    print("[%s/%s] draws=%d best=%.3f mode=%s" % (
        qid, group, len(draws), best, draws[0]["mode_detail"]), flush=True)

for j in jobs:
    run_job(j)

ok = sum(1 for r in results.values() if r["draws"] and r["draws"][0]["score"] is not None)
with open(out_path + ".done", "w", encoding="utf-8") as f:
    json.dump({"finished": True, "scored": ok, "total": len(jobs)}, f)
print("done: %d/%d scored -> %s" % (ok, len(jobs), out_path), flush=True)
