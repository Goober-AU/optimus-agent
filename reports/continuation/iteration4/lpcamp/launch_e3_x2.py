"""LP dev5 campaign phase 3: E3 x2 per dev5 question on the fresh stores.

For each dev5 qid, run the controller twice in typeB mode; the content cache
is warm after phase 1, so each run is a cache-reuse copy-out + skipIngest
re-answer with the E3 read config (rerankCandidates 500, distill/expansion
OFF), scored question-scoped official-lp-v1 (LP_JUDGE_MAX_TOKENS=4096).

Run AFTER all 5 stores are ingested + verified. Sequential; ~5-10 min/run.
Progress: lpcamp-scratch/state/e3-progress.json.
"""
import json
import os
import sys
import time

WT = r"C:/Users/openclawuser/optimus-memory-bench-20261007-040424/imp4b-lpcamp"
IMP4 = r"C:/Users/openclawuser/optimus-memory-bench-20261007-040424/imp4"
ROOT = r"C:/Users/openclawuser/optimus-memory-bench-20261007-040424"
SCRATCH = os.path.join(ROOT, "lpcamp-scratch")
LOG = os.path.join(SCRATCH, "logs", "e3-launcher.log")
STATE = os.path.join(SCRATCH, "state", "e3-progress.json")

os.environ["MEMORY_BENCH_MODEL_PROVIDER"] = "dgx-glm53"
os.environ["MEMORY_BENCH_MODEL_ID"] = "glm-5.3"

_log = open(LOG, "a", buffering=1, encoding="utf-8", newline="\n")
sys.stdout = _log
sys.stderr = _log

sys.path.insert(0, os.path.join(IMP4, "scripts"))
import memory_question_loop as M  # noqa: E402

QIDS = ["lp_232", "lp_118", "lp_18", "lp_159", "lp_69"]


def write_state(payload):
    payload = dict(payload)
    payload["ts"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    tmp = STATE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=1)
    os.replace(tmp, STATE)


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


def main():
    print(f"[launcher] e3 x2 campaign start {time.strftime('%Y-%m-%dT%H:%M:%S')}", flush=True)
    results = {}
    for qid in QIDS:
        for r in ("r1", "r2"):
            run_id = f"lpcamp-e3-{qid.replace('lp_', 'lp')}-{r}"[:20]
            write_state({"phase": "e3", "current": run_id, "done": results})
            print(f"[launcher] starting {run_id}", flush=True)
            t0 = time.time()
            try:
                rc = M.main(controller_args(qid, run_id))
            except Exception as exc:
                print(f"[launcher] {run_id} controller exception: {exc!r}", flush=True)
                results[run_id] = {"rc": None, "error": repr(exc)}
                write_state({"phase": "e3", "current": None, "done": results,
                             "stopped": "exception on " + run_id})
                return 2
            dur_min = round((time.time() - t0) / 60.0, 1)
            print(f"[launcher] {run_id} rc={rc} ({dur_min} min)", flush=True)
            results[run_id] = {"rc": rc, "duration_min": dur_min}
            write_state({"phase": "e3", "current": None, "done": results})
    write_state({"phase": "e3-complete", "done": results})
    print("[launcher] e3 x2 campaign complete", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
