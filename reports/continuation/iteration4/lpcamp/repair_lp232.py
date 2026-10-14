"""LP campaign repair: re-ingest lp_232 (first attempt crashed at session_30).

The first lp_232 ingest lost its final session to two consecutive refinement
budget-exhaustion failures (endpoint contention from parallel workers); the
cache has no env.jsonl (cold) so the E3 phase would quarantine + re-ingest it
anyway. This driver waits for the ingest launcher to finish the queue, then
re-runs lp_232 alone with identical controller args (fresh quarantine +
re-ingest + E3 question draw + verification). If the retry fails the same
way, it retries up to 3 times with backoff, then records failure.
"""
import json
import os
import sys
import time

WT = r"C:/Users/openclawuser/optimus-memory-bench-20261007-040424/imp4b-lpcamp"
IMP4 = r"C:/Users/openclawuser/optimus-memory-bench-20261007-040424/imp4"
ROOT = r"C:/Users/openclawuser/optimus-memory-bench-20261007-040424"
SCRATCH = os.path.join(ROOT, "lpcamp-scratch")
LOG = os.path.join(SCRATCH, "logs", "repair-lp232.log")
STATE = os.path.join(SCRATCH, "state", "repair-lp232.json")
INGEST_STATE = os.path.join(SCRATCH, "state", "ingest-progress.json")

os.environ["MEMORY_BENCH_MODEL_PROVIDER"] = "dgx-glm53"
os.environ["MEMORY_BENCH_MODEL_ID"] = "glm-5.3"

_log = open(LOG, "a", buffering=1, encoding="utf-8", newline="\n")
sys.stdout = _log
sys.stderr = _log

sys.path.insert(0, os.path.join(IMP4, "scripts"))
import memory_question_loop as M  # noqa: E402


def write_state(payload):
    payload = dict(payload)
    payload["ts"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    tmp = STATE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=1)
    os.replace(tmp, STATE)


def log(msg):
    print(f"[repair {time.strftime('%H:%M:%S')}] {msg}", flush=True)


def controller_args(run_id):
    return [
        "lp_232",
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


def verify():
    cache_root = os.path.join(SCRATCH, "cache")
    hits = [d for d in os.listdir(cache_root)
            if d.startswith("lp_232--") and ".failed-" not in d]
    if len(hits) != 1:
        return {"ok": False, "error": f"cache dirs: {hits}"}
    cd = os.path.join(cache_root, hits[0])
    envj = os.path.join(cd, "env.jsonl")
    if not os.path.isfile(envj):
        return {"ok": False, "error": "no env.jsonl (ingest incomplete)"}
    proj_root = os.path.join(cd, "envs", "lp_232", "agent", "memory", "projects")
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
                if "carbon footprint" in " ".join(
                    [str(e.get("title") or ""), str(e.get("content") or "")]).lower()]
        return {"ok": total > 0 and (100.0 * with_cues / total if total else 0) >= 90.0
                and len(gold) > 0 and os.path.isfile(envj),
                "entries": total, "cue_pct": round(100.0 * with_cues / total if total else 0, 1),
                "gold": gold[:3], "env_jsonl": True}
    return {"ok": False, "error": "no harness_state.json"}


def launch_pipeline():
    """Relaunch the E3 + finalize driver (WMI) after repair."""
    import subprocess
    ps1 = os.path.join(WT, "reports/continuation/iteration4/lpcamp/wmi-launch-pipeline2.ps1")
    cp = subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
                         "-File", ps1], capture_output=True, text=True, timeout=60)
    log(f"pipeline relaunch: {(cp.stdout or '').strip()} {(cp.stderr or '').strip()}"[:300])


def main():
    log("waiting for ingest queue to finish")
    write_state({"phase": "waiting"})
    while True:
        try:
            with open(INGEST_STATE, "r", encoding="utf-8") as fh:
                st = json.load(fh)
            if st.get("phase") == "ingest-complete":
                break
            if st.get("stopped"):
                log("ingest launcher stopped; repair proceeds anyway for lp_232")
                break
        except (OSError, json.JSONDecodeError):
            pass
        time.sleep(60)
    log("queue done; repairing lp_232")
    for attempt in range(1, 4):
        run_id = f"lpcamp-rep232-{attempt}"
        n = 0
        while os.path.isdir(os.path.join(SCRATCH, "att", run_id)):
            n += 1
            suffix = f"x{n}"
            run_id = (f"lpcamp-rep232-{attempt}"[:20 - len(suffix)] + suffix)
        write_state({"phase": "repair", "attempt": attempt, "run_id": run_id})
        log(f"repair attempt {attempt} ({run_id})")
        t0 = time.time()
        try:
            rc = M.main(controller_args(run_id))
        except Exception as exc:
            log(f"repair {run_id} exception: {exc!r}")
            rc = None
        dur = round((time.time() - t0) / 60.0, 1)
        ver = verify()
        log(f"repair {run_id} rc={rc} ({dur} min) verify={json.dumps(ver)}")
        write_state({"phase": "attempt-done", "attempt": attempt, "run_id": run_id,
                     "rc": rc, "verify": ver})
        if ver.get("ok"):
            log("lp_232 repair OK")
            write_state({"phase": "complete", "attempt": attempt, "run_id": run_id,
                         "rc": rc, "verify": ver})
            launch_pipeline()
            return 0
        time.sleep(120)
    log("lp_232 repair FAILED after 3 attempts")
    write_state({"phase": "failed"})
    launch_pipeline()
    return 3


if __name__ == "__main__":
    sys.exit(main())
