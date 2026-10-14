"""LP dev5 campaign ingest launcher v2 (crash-safe resume).

Skips stores whose cache is already warm (env.jsonl + store present), uses a
unique attempt dir if a run-id collides, and verifies cue coverage + gold per
store. Queue: lp_118, lp_18, lp_159, lp_69 (lp_232 handled by repair_lp232.py).
"""
import json
import os
import sys
import time

WT = r"C:/Users/openclawuser/optimus-memory-bench-20261007-040424/imp4b-lpcamp"
IMP4 = r"C:/Users/openclawuser/optimus-memory-bench-20261007-040424/imp4"
ROOT = r"C:/Users/openclawuser/optimus-memory-bench-20261007-040424"
SCRATCH = os.path.join(ROOT, "lpcamp-scratch")
LOG = os.path.join(SCRATCH, "logs", "ingest-launcher.log")
STATE = os.path.join(SCRATCH, "state", "ingest-progress.json")

os.environ["MEMORY_BENCH_MODEL_PROVIDER"] = "dgx-glm53"
os.environ["MEMORY_BENCH_MODEL_ID"] = "glm-5.3"

_log = open(LOG, "a", buffering=1, encoding="utf-8", newline="\n")
sys.stdout = _log
sys.stderr = _log

sys.path.insert(0, os.path.join(IMP4, "scripts"))
import memory_question_loop as M  # noqa: E402

QIDS = ["lp_118", "lp_18"]  # lp_159 + lp_69 resharded to glm-writecamp (QQ)

GOLD_KEYWORDS = {
    "lp_18": ["recovery code"],
    "lp_159": ["fear of germs", "until they crack", "wash my hands"],
    "lp_69": ["GPS tag", "county fair"],
    "lp_118": ["graduat"],
}


def write_state(payload):
    payload = dict(payload)
    payload["ts"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    tmp = STATE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=1)
    os.replace(tmp, STATE)


def cache_warm(env_id):
    cache_root = os.path.join(SCRATCH, "cache")
    hits = [d for d in os.listdir(cache_root)
            if d.startswith(env_id + "--") and ".failed-" not in d]
    if len(hits) != 1:
        return False
    st = M.cache_state(os.path.join(cache_root, hits[0]), env_id)
    return st["warm"]


def verify_store(env_id, qid):
    cache_root = os.path.join(SCRATCH, "cache")
    hits = [d for d in os.listdir(cache_root)
            if d.startswith(env_id + "--") and ".failed-" not in d]
    if len(hits) != 1:
        return {"ok": False, "error": "cache dir not found"}
    proj_root = os.path.join(cache_root, hits[0], "envs", env_id,
                             "agent", "memory", "projects")
    for name in os.listdir(proj_root):
        cand = os.path.join(proj_root, name, "harness_state.json")
        if not os.path.isfile(cand):
            continue
        with open(cand, "r", encoding="utf-8") as fh:
            hs = json.load(fh)
        entries = (hs.get("entries") or {}).get("memory") or {}
        total = len(entries)
        with_cues = sum(1 for e in entries.values()
                        if ((e.get("metadata") or {}).get("prospectiveCues")))
        gold = [eid for eid, e in entries.items()
                if any(kw.lower() in " ".join([str(e.get("title") or ""),
                                                str(e.get("content") or "")]).lower()
                       for kw in GOLD_KEYWORDS[qid])]
        cov = (100.0 * with_cues / total) if total else 0.0
        return {"ok": total > 0 and cov >= 90.0 and len(gold) > 0,
                "entries": total, "entries_with_cues": with_cues,
                "cue_coverage_pct": round(cov, 1),
                "gold_entry_ids": gold[:6], "gold_entries": len(gold)}
    return {"ok": False, "error": "no harness_state.json"}


def controller_args(qid, run_id):
    return [
        qid,
        "--ledger", os.path.join(ROOT, "reports/continuation/iteration4/QUESTION-LEDGER.json"),
        "--mode", "typeB",
        "--settings", os.path.join(SCRATCH, "settings/e3-settings.json"),
        "--driver", os.path.join(ROOT, "imp4-targetR/release/examples/memory_bench.exe"),
        "--driver-workdir", IMP4,
        "--models-json", os.path.join(SCRATCH, "agent-models.json"),
        "--judge-models-json", os.path.join(ROOT, "reports/continuation/iteration2-inputs/lme-temporal-flip/judge-models.json"),
        "--bench-venv", os.path.join(ROOT, "repo-continuation/bench/.venv/Scripts/python.exe"),
        "--scoring-cwd", ROOT,
        "--attempts-root", os.path.join(SCRATCH, "att"),
        "--cache-root", os.path.join(SCRATCH, "cache"),
        "--manifest-dir", os.path.join(ROOT, "reports/continuation/iteration3-inputs/manifests"),
        "--run-id", run_id,
        "--lp-judge-max-tokens", "4096",
    ]


def unique_run_id(base):
    run_id, n = base, 1
    while os.path.isdir(os.path.join(SCRATCH, "att", run_id)):
        n += 1
        suffix = f"-{n}"
        run_id = (base[:20 - len(suffix)] + suffix)
    return run_id


def main():
    print(f"[launcher-v2] resume start {time.strftime('%Y-%m-%dT%H:%M:%S')}", flush=True)
    try:
        with open(STATE, "r", encoding="utf-8") as fh:
            prior = json.load(fh)
        results = prior.get("done") or {}
    except (OSError, json.JSONDecodeError):
        results = {}
    for qid in QIDS:
        env_id = qid
        if qid in results and results[qid].get("verify", {}).get("ok"):
            print(f"[launcher-v2] {qid} already done+verified; skip", flush=True)
            continue
        if cache_warm(env_id):
            ver = verify_store(env_id, qid)
            print(f"[launcher-v2] {qid} cache warm; verify={json.dumps(ver)}", flush=True)
            if ver.get("ok"):
                results[qid] = {"rc": "warm-skip", "verify": ver}
                write_state({"phase": "ingest", "current": None, "done": results})
                continue
        run_id = unique_run_id(f"lpcamp-ing-{qid.replace('lp_', 'lp')}"[:20])
        write_state({"phase": "ingest", "current": qid, "run_id": run_id, "done": results,
                     "started": time.strftime("%Y-%m-%dT%H:%M:%S")})
        print(f"[launcher-v2] starting {qid} run_id={run_id}", flush=True)
        t0 = time.time()
        try:
            rc = M.main(controller_args(qid, run_id))
        except Exception as exc:
            print(f"[launcher-v2] {qid} controller exception: {exc!r}", flush=True)
            results[qid] = {"rc": None, "error": repr(exc)}
            write_state({"phase": "ingest", "current": None, "done": results,
                         "stopped": "controller exception on " + qid})
            return 2
        dur_min = round((time.time() - t0) / 60.0, 1)
        print(f"[launcher-v2] {qid} controller rc={rc} ({dur_min} min)", flush=True)
        ver = verify_store(env_id, qid)
        print(f"[launcher-v2] {qid} verify: {json.dumps(ver)}", flush=True)
        results[qid] = {"rc": rc, "duration_min": dur_min, "verify": ver}
        write_state({"phase": "ingest", "current": None, "done": results})
        if not ver.get("ok"):
            print(f"[launcher-v2] {qid} verification FAILED; stopping queue", flush=True)
            write_state({"phase": "ingest", "current": None, "done": results,
                         "stopped": "verification failed on " + qid})
            return 3
    write_state({"phase": "ingest-complete", "current": None, "done": results})
    print("[launcher-v2] queue complete", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
