#!/usr/bin/env python3
"""loco-ans2 verification runner (post-merge binary b57dc607, fresh worktree imp4b-locoans2).

Re-runs the ship instruction FINAL2 on the two bar questions (x3) plus four
regression spot-checks (x1) against the same warm cache d4b542a8, using the
current fleet binary, to confirm the pre-pause battery (old binary c04ab631)
still holds after the upstream merge rebuild.

Usage: python run-verify-b57.py <jobs.json> <out.json> [workers]
"""
import concurrent.futures as cf
import json, os, subprocess, sys, threading, time

ROOT = "C:/Users/openclawuser/optimus-memory-bench-20261007-040424"
WT = ROOT + "/imp4b-locoans2"
EXPDIR = WT + "/reports/continuation/iteration4-experiments/loco-ans2"
CTRL = ROOT + "/imp4/scripts/memory_question_loop.py"
GATE_MAN = ROOT + "/imp3-gate/reports/continuation/iteration3-inputs/locomo/manifests/locomo-gate3-improved-s0.json"
DRIVER = ROOT + "/imp4-targetR/release/examples/memory_bench.exe"
BASE = WT + "/reports/continuation/iteration4-experiments/loco-full/base-det-settings.json"
ATT_ROOT = ROOT + "/reports/continuation/iteration4/ql-attempts-locoans2"
CACHE_ROOT = ROOT + "/reports/continuation/iteration4/ql-typeb-cache"
LEDGER = ROOT + "/reports/continuation/iteration4/QUESTION-LEDGER.json"
MODELS_JSON = ROOT + "/reports/continuation/c06-instruction-boundary/inputs/runs/locomo-c06-ib-system_append-q12-s0p1/envs/conv-30/agent/models.json"
JUDGE_MODELS = ROOT + "/reports/continuation/iteration2-inputs/lme-temporal-flip/judge-models.json"
BENCH_VENV = ROOT + "/repo-continuation/bench/.venv/Scripts/python.exe"
SCORING_CWD = ROOT

jobs = json.load(open(sys.argv[1], encoding="utf-8"))
out_path = sys.argv[2]
WORKERS = int(sys.argv[3]) if len(sys.argv) > 3 else 4

ENV = {
    "MEMORY_BENCH_MODEL_PROVIDER": "dgx-glm53",
    "MEMORY_BENCH_MODEL_ID": "glm-5.3",
    "MEMORY_BENCH_ROOT": ROOT,
}

lock = threading.Lock()
results = {}

def ledger_attempt(run_id, qid):
    with open(LEDGER, encoding="utf-8") as h:
        L = json.load(h)
    for e in L.get("entries", []):
        if e.get("qid") == qid:
            for a in (e.get("attempts") or []):
                if a.get("run_id") == run_id:
                    return a
    return None

def run_job(job):
    qid, tag = job["qid"], job["tag"]
    settings = os.path.join(EXPDIR, job["settings"])
    run_id = job["run_id"]
    assert len(run_id) <= 20, run_id
    cmd = ["python", CTRL, qid, "--mode", "typeB",
           "--ledger", LEDGER,
           "--base-settings", BASE, "--settings", settings,
           "--env-from-manifest", GATE_MAN,
           "--driver", DRIVER, "--driver-workdir", ROOT + "/imp4",
           "--models-json", MODELS_JSON, "--judge-models-json", JUDGE_MODELS,
           "--bench-venv", BENCH_VENV, "--scoring-cwd", SCORING_CWD,
           "--attempts-root", ATT_ROOT, "--cache-root", CACHE_ROOT,
           "--run-id", run_id, "--worker", "locoans2b"]
    t0 = time.time()
    env = dict(os.environ); env.update(ENV)
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, cwd=WT, timeout=900, env=env)
        rc, err = r.returncode, (r.stderr or "")[-500:]
    except subprocess.TimeoutExpired:
        rc, err = -9, "runner timeout 900s"
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
           "recall_n": att.get("recall_ids_count") if att else None,
           "mode_detail": att.get("mode_detail") if att else None,
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
