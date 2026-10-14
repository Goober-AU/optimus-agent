#!/usr/bin/env python3
"""loco-final10 runner v2. FINAL10 = NN formcons base + MM nearmiss clauses.
MODE typeB (default): conv-26 battery on warm cache d4b542a8e98deca6
(cache key verified). MODE typeA: spot cells via frozen gate store copy-out
(matches NN target runs / MM len-cap + convert cells).
Usage: python run10.py <jobs.json> <out.json> [workers] [typeA|typeB]
"""
import concurrent.futures as cf
import json, os, subprocess, sys, threading, time

ROOT = "C:/Users/openclawuser/optimus-memory-bench-20261007-040424"
WT = ROOT + "/imp4b-resq"
CTRL = WT + "/scripts/memory_question_loop.py"
DRIVER = ROOT + "/imp4b-resq-target/release/examples/memory_bench.exe"
GATE_MAN = ROOT + "/imp3-gate/reports/continuation/iteration3-inputs/locomo/manifests/locomo-gate3-improved-s0.json"
BASE_A = WT + "/reports/continuation/iteration4-experiments/loco-sweep/base-det-settings.json"
BASE_B = WT + "/reports/continuation/iteration4-experiments/loco-full/base-det-settings.json"
SET_A = WT + "/reports/continuation/iteration4-experiments/loco-final10/typea-final10-stem.json"
SET_B = WT + "/reports/continuation/iteration4-experiments/loco-final10/FINAL10.json"
ATT_ROOT = ROOT + "/reports/continuation/iteration4/ql-attempts-f10"
CACHE_ROOT = ROOT + "/reports/continuation/iteration4/ql-typeb-cache"
LEDGER = os.environ.get("F10_LEDGER") or (ROOT + "/reports/continuation/iteration4/QUESTION-LEDGER.json")
MODELS_JSON = ROOT + "/reports/continuation/c06-instruction-boundary/inputs/runs/locomo-c06-ib-system_append-q12-s0p1/envs/conv-30/agent/models.json"
JUDGE_MODELS = ROOT + "/reports/continuation/iteration2-inputs/lme-temporal-flip/judge-models.json"
BENCH_VENV = ROOT + "/repo-continuation/bench/.venv/Scripts/python.exe"
SCORING_CWD = ROOT

jobs = json.load(open(sys.argv[1], encoding="utf-8"))
out_path = sys.argv[2]
WORKERS = int(sys.argv[3]) if len(sys.argv) > 3 else 3
MODE_RAW = sys.argv[4] if len(sys.argv) > 4 else "typeB"
MODE = MODE_RAW.lower()
assert MODE in ("typea", "typeb"), MODE

ENV = {
    "MEMORY_BENCH_MODEL_PROVIDER": "dgx-glm53",
    "MEMORY_BENCH_MODEL_ID": "glm-5.3",
    "MEMORY_BENCH_ROOT": ROOT,
}

lock = threading.Lock()
results = {}

def ledger_attempt(run_id, qid, tries=6):
    for _ in range(tries):
        with open(LEDGER, encoding="utf-8") as h:
            L = json.load(h)
        for e in L.get("entries", []):
            if e.get("qid") == qid:
                hit = None
                for a in (e.get("attempts") or []):
                    if a.get("run_id") == run_id:
                        hit = a
                if hit:
                    return hit
        time.sleep(5)
    return None

def run_job(job):
    qid, tag = job["qid"], job["tag"]
    run_id = job["run_id"]
    assert len(run_id) <= 20, run_id
    cmd = ["python", CTRL, qid, "--mode", MODE_RAW,
           "--ledger", LEDGER,
           "--base-settings", BASE_A if MODE == "typea" else BASE_B,
           "--settings", SET_A if MODE == "typea" else SET_B]
    if MODE == "typeb":
        cmd += ["--env-from-manifest", GATE_MAN, "--cache-root", CACHE_ROOT]
    cmd += ["--driver", DRIVER, "--driver-workdir", ROOT + "/imp4",
            "--models-json", MODELS_JSON, "--judge-models-json", JUDGE_MODELS,
            "--bench-venv", BENCH_VENV, "--scoring-cwd", SCORING_CWD,
            "--attempts-root", ATT_ROOT,
            "--run-id", run_id, "--worker", "final10"]
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
    row = {"tag": tag, "qid": qid, "run_id": run_id, "rc": rc, "err": err or None,
           "score": att.get("score") if att else None,
           "outcome": att.get("outcome") if att else None,
           "answer": att.get("answer_excerpt") if att else None,
           "mode_detail": att.get("mode_detail") if att else None,
           "failure_stage": att.get("failure_stage") if att else None,
           "latency_s": round(dt, 1)}
    with lock:
        results[tag] = row
        with open(out_path, "w", encoding="utf-8", newline="\n") as f:
            json.dump(results, f, indent=1); f.write("\n")
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
