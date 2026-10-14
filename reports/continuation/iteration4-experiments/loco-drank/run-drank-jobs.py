#!/usr/bin/env python3
"""loco-drank: D-class window lane runner (typeB warm-cache, one job = one question).

Usage: python run-drank-jobs.py <jobs.json> <out.json> [workers]

jobs.json: [{"qid": ..., "run_id": ..., "tag": ..., "manifest": ..., "base": ..., "settings": ..., "ledger": optional}]
"""
import concurrent.futures as cf
import json, os, subprocess, sys, threading, time

ROOT = "C:/Users/openclawuser/optimus-memory-bench-20261007-040424"
WT = ROOT + "/imp4b-drank"
CTRL = WT + "/scripts/memory_question_loop.py"
DRIVER = ROOT + "/imp4b-target8k/release/examples/memory_bench.exe"
EXP = WT + "/reports/continuation/iteration4-experiments/loco-drank"
ATT_ROOT = ROOT + "/reports/continuation/iteration4/ql-attempts-drank"
CACHE_ROOT = ROOT + "/reports/continuation/iteration4/ql-typeb-cache"
LEDGER = ROOT + "/reports/continuation/iteration4/QUESTION-LEDGER.json"
MODELS_JSON = ROOT + "/reports/continuation/c06-instruction-boundary/inputs/runs/locomo-c06-ib-system_append-q12-s0p1/envs/conv-30/agent/models.json"
JUDGE_MODELS = ROOT + "/reports/continuation/iteration2-inputs/lme-temporal-flip/judge-models.json"
BENCH_VENV = ROOT + "/repo-continuation/bench/.venv/Scripts/python.exe"
SCORING_CWD = ROOT

jobs = json.load(open(sys.argv[1], encoding="utf-8"))
out_path = sys.argv[2]
WORKERS = int(sys.argv[3]) if len(sys.argv) > 3 else 1

ENV = {
    "MEMORY_BENCH_MODEL_PROVIDER": "dgx-glm53",
    "MEMORY_BENCH_MODEL_ID": "glm-5.3",
    "MEMORY_BENCH_ROOT": ROOT,
}

lock = threading.Lock()
results = {}

def ledger_attempt(ledger, run_id, qid, tries=10):
    for _ in range(tries):
        try:
            with open(ledger, encoding="utf-8") as h:
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

def run_job(job):
    qid, tag = job["qid"], job["tag"]
    run_id = job["run_id"]
    manifest = job.get("manifest")
    ledger = job.get("ledger") or LEDGER
    base = job.get("base") or (EXP + "/base-wc-det.json")
    settings = job.get("settings") or (EXP + "/drank-w32-final9-stem.json")
    assert len(run_id) <= 20, run_id
    for attempt in range(3):
        row = _run_once(job, qid, tag, run_id, manifest, ledger, base, settings, attempt)
        if row["rc"] in (0, 3, 4) or attempt == 2:
            break
        time.sleep(15)
    return row

def _run_once(job, qid, tag, run_id, manifest, ledger, base, settings, attempt):
    cmd = [BENCH_VENV, CTRL, qid, "--mode", "typeB",
           "--ledger", ledger,
           "--base-settings", base, "--settings", settings,
           "--env-from-manifest", manifest,
           "--driver", DRIVER, "--driver-workdir", ROOT + "/imp4",
           "--models-json", MODELS_JSON, "--judge-models-json", JUDGE_MODELS,
           "--bench-venv", BENCH_VENV, "--scoring-cwd", SCORING_CWD,
           "--attempts-root", ATT_ROOT, "--cache-root", CACHE_ROOT,
           "--run-id", run_id, "--worker", "drank"]
    t0 = time.time()
    env = dict(os.environ); env.update(ENV)
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, cwd=WT, timeout=24000, env=env)
        rc, err = r.returncode, (r.stderr or "")[-600:]
    except subprocess.TimeoutExpired:
        rc, err = -9, "runner timeout 24000s"
    if rc not in (0, 3, 4):
        err = ("try%d " % attempt) + (err or "")
    dt = time.time() - t0
    att = None
    try:
        att = ledger_attempt(ledger, run_id, qid)
    except Exception as exc:
        err = (str(err) + " ledger-read:" + str(exc))[-600:]
    row = {"tag": tag, "qid": qid, "run_id": run_id, "rc": rc, "err": err or None,
           "score": att.get("score") if att else None,
           "outcome": att.get("outcome") if att else None,
           "answer": att.get("answer_excerpt") if att else None,
           "recall_n": att.get("recall_ids_count") if att else None,
           "failure_stage": att.get("failure_stage") if att else None,
           "mode_detail": att.get("mode_detail") if att else None,
           "cache_key": (att.get("cache_key") or "")[:16] if att else None,
           "latency_s": round(dt, 1)}
    with lock:
        results[tag] = row
        with open(out_path, "w", encoding="utf-8", newline="\n") as f:
            json.dump(results, f, indent=1)
            f.write("\n")
    print("[%s] rc=%s score=%s outcome=%s mode=%s (%.0fs)" % (
        tag, rc, row["score"], row["outcome"], row["mode_detail"], dt), flush=True)
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
