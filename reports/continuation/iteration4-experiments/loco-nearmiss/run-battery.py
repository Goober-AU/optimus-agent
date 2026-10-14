#!/usr/bin/env python3
"""loco-nearmiss battery runner (answer near-miss lane: length-cap guard + instruction refinements).

Usage: python run-battery.py <jobs.json> <out.json> [workers] [--typea|--typeb]
"""
import concurrent.futures as cf
import json, os, subprocess, sys, threading, time

ROOT = "C:/Users/openclawuser/optimus-memory-bench-20261007-040424"
WT = ROOT + "/imp4b-nearmiss"
EXPDIR = WT + "/reports/continuation/iteration4-experiments/loco-nearmiss"
CTRL = WT + "/scripts/memory_question_loop.py"
GATE_MAN = ROOT + "/imp3-gate/reports/continuation/iteration3-inputs/locomo/manifests/locomo-gate3-improved-s0.json"
DRIVER = os.environ.get("NM_DRIVER") or (ROOT + "/imp4-targetR/release/examples/memory_bench.exe")
MODELS_JSON = ROOT + "/reports/continuation/c06-instruction-boundary/inputs/runs/locomo-c06-ib-system_append-q12-s0p1/envs/conv-30/agent/models.json"
JUDGE_MODELS = ROOT + "/reports/continuation/iteration2-inputs/lme-temporal-flip/judge-models.json"
BENCH_VENV = ROOT + "/repo-continuation/bench/.venv/Scripts/python.exe"
SCORING_CWD = ROOT
CACHE_ROOT = ROOT + "/reports/continuation/iteration4/ql-typeb-cache"
LEDGER = ROOT + "/reports/continuation/iteration4/QUESTION-LEDGER.json"

jobs = json.load(open(sys.argv[1], encoding="utf-8"))
out_path = sys.argv[2]
WORKERS = int(sys.argv[3]) if len(sys.argv) > 3 else 3
BASE = os.environ.get("NM_BASE") or (EXPDIR + "/base-det-settings.json")
SETTINGS = os.environ.get("NM_SETTINGS")
ATT_ROOT = os.environ.get("NM_ATT_ROOT") or (ROOT + "/reports/continuation/iteration4/ql-attempts-lconnm")
assert SETTINGS, "NM_SETTINGS required"

ENV = {
    "MEMORY_BENCH_MODEL_PROVIDER": "dgx-glm53",
    "MEMORY_BENCH_MODEL_ID": "glm-5.3",
    "MEMORY_BENCH_ROOT": ROOT,
}

lock = threading.Lock()
results = {}

def ledger_attempt(run_id, qid, tries=8):
    for _ in range(tries):
        with open(LEDGER, encoding="utf-8") as h:
            L = json.load(h)
        for e in L.get("entries", []):
            if e.get("qid") == qid:
                for a in (e.get("attempts") or []):
                    if a.get("run_id") == run_id:
                        return a
        time.sleep(5)
    return None

def run_job(job):
    qid, tag = job["qid"], job["tag"]
    run_id = job["run_id"]
    mode = job.get("mode", "typeA")
    assert len(run_id) <= 20, run_id
    cmd = ["python", CTRL, qid, "--mode", mode,
           "--ledger", LEDGER,
           "--base-settings", BASE, "--settings", SETTINGS,
           "--driver", DRIVER, "--driver-workdir", ROOT + "/imp4",
           "--models-json", MODELS_JSON, "--judge-models-json", JUDGE_MODELS,
           "--bench-venv", BENCH_VENV, "--scoring-cwd", SCORING_CWD,
           "--attempts-root", ATT_ROOT,
           "--run-id", run_id, "--worker", "nearmiss"]
    if mode == "typeB":
        cmd += ["--cache-root", CACHE_ROOT, "--env-from-manifest", GATE_MAN]
    t0 = time.time()
    env = dict(os.environ); env.update(ENV)
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, cwd=WT, timeout=1500, env=env)
        rc, err = r.returncode, (r.stderr or "")[-500:]
    except subprocess.TimeoutExpired:
        rc, err = -9, "runner timeout 1500s"
    dt = time.time() - t0
    att = None
    try:
        att = ledger_attempt(run_id, qid)
    except Exception as exc:
        err = (err + " ledger-read:" + str(exc))[-600:]
    row = {"tag": tag, "qid": qid, "run_id": run_id, "mode": mode, "rc": rc, "err": err or None,
           "score": att.get("score") if att else None,
           "outcome": att.get("outcome") if att else None,
           "answer": att.get("answer_excerpt") if att else None,
           "failure_stage": att.get("failure_stage") if att else None,
           "mode_detail": att.get("mode_detail") if att else None,
           "cache_key": (att.get("cache_key") or "")[:16] if att else None,
           "latency_s": round(dt, 1)}
    with lock:
        results[tag] = row
        with open(out_path, "w", encoding="utf-8", newline="\n") as f:
            json.dump(results, f, indent=1)
            f.write("\n")
    print("[%s] rc=%s score=%s outcome=%s (%.0fs)" % (tag, rc, row["score"], row["outcome"], dt), flush=True)
    return row

with cf.ThreadPoolExecutor(max_workers=WORKERS) as ex:
    futs = [ex.submit(run_job, j) for j in jobs]
    for f in cf.as_completed(futs):
        try:
            f.result()
        except Exception as exc:
            print("job crashed:", exc, flush=True)

ok = sum(1 for r in results.values() if r["score"] is not None)
print("done: %d/%d scored -> %s" % (ok, len(jobs), out_path), flush=True)
