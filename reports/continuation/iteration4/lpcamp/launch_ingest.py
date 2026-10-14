"""LP dev5 fresh re-ingest campaign launcher (sequential, phase 1).

Re-ingests the 5 dev5 loco_plus stores (lp_232, lp_118, lp_18, lp_159, lp_69)
via the round-4 controller in TYPE B mode (content-addressed cache), using the
fleet binary imp4-targetR (sha b57dc607). E3 read settings on the question
phase (rerankCandidates 500, distill/expansion OFF) so each ingest run also
yields one judged E3 draw. After each store completes, the launcher verifies
cue coverage (>= 90% entries with prospectiveCues) and gold presence; a failed
verification stops the campaign before the next store.

Launched via WMI Win32_Process.Create so the controller + driver survive the
spawning agent session. Progress: lpcamp-scratch/state/ingest-progress.json.
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

QIDS = ["lp_232", "lp_118", "lp_18", "lp_159", "lp_69"]

# gold-presence keyword sets per qid (from the qs evidence_cue)
GOLD_KEYWORDS = {
    "lp_232": ["carbon footprint"],
    "lp_118": ["graduat"],
    "lp_18": ["recovery code"],
    "lp_159": ["fear of germs", "until they crack", "wash my hands"],
    "lp_69": ["GPS tag", "county fair"],
}


def write_state(payload):
    payload = dict(payload)
    payload["ts"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    tmp = STATE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=1)
    os.replace(tmp, STATE)


def find_cache_dir(env_id):
    cache_root = os.path.join(SCRATCH, "cache")
    hits = [d for d in os.listdir(cache_root)
            if d.startswith(env_id + "--") and ".failed-" not in d]
    if len(hits) != 1:
        return None
    return os.path.join(cache_root, hits[0])


def verify_store(env_id, qid):
    """Cue coverage (entries with prospectiveCues) + gold presence."""
    cache_dir = find_cache_dir(env_id)
    if not cache_dir:
        return {"ok": False, "error": "cache dir not found"}
    proj_root = os.path.join(cache_dir, "envs", env_id, "agent", "memory", "projects")
    if not os.path.isdir(proj_root):
        return {"ok": False, "error": "no store projects dir"}
    hs_files = []
    for name in os.listdir(proj_root):
        cand = os.path.join(proj_root, name, "harness_state.json")
        if os.path.isfile(cand):
            hs_files.append(cand)
    if not hs_files:
        return {"ok": False, "error": "no harness_state.json"}
    with open(hs_files[0], "r", encoding="utf-8") as fh:
        hs = json.load(fh)
    entries = (hs.get("entries") or {}).get("memory") or {}
    total = len(entries)
    with_cues = 0
    gold_hits = []
    for eid, e in entries.items():
        cues = ((e.get("metadata") or {}).get("prospectiveCues")) or []
        if cues:
            with_cues += 1
        text = " ".join([str(e.get("title") or ""), str(e.get("content") or "")]).lower()
        for kw in GOLD_KEYWORDS[qid]:
            if kw.lower() in text:
                gold_hits.append(eid)
                break
    coverage = (100.0 * with_cues / total) if total else 0.0
    return {
        "ok": total > 0 and coverage >= 90.0 and len(gold_hits) > 0,
        "cache_dir": cache_dir,
        "entries": total,
        "entries_with_cues": with_cues,
        "cue_coverage_pct": round(coverage, 1),
        "gold_entry_ids": gold_hits[:6],
        "gold_entries": len(gold_hits),
    }


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
    print(f"[launcher] ingest campaign start {time.strftime('%Y-%m-%dT%H:%M:%S')}", flush=True)
    results = {}
    for qid in QIDS:
        env_id = qid
        run_id = ("lpcamp-ing-" + qid.replace("lp_", "lp"))[:20]
        write_state({"phase": "ingest", "current": qid, "run_id": run_id,
                     "done": results, "started": time.strftime("%Y-%m-%dT%H:%M:%S")})
        print(f"[launcher] starting {qid} run_id={run_id}", flush=True)
        t0 = time.time()
        try:
            rc = M.main(controller_args(qid, run_id))
        except Exception as exc:  # controller error -> stop campaign
            print(f"[launcher] {qid} controller exception: {exc!r}", flush=True)
            results[qid] = {"rc": None, "error": repr(exc)}
            write_state({"phase": "ingest", "current": None, "done": results,
                         "stopped": "controller exception on " + qid})
            return 2
        dur_min = round((time.time() - t0) / 60.0, 1)
        print(f"[launcher] {qid} controller rc={rc} ({dur_min} min)", flush=True)
        ver = verify_store(env_id, qid)
        print(f"[launcher] {qid} verify: {json.dumps(ver)}", flush=True)
        results[qid] = {"rc": rc, "duration_min": dur_min, "verify": ver}
        write_state({"phase": "ingest", "current": None, "done": results})
        if not ver.get("ok"):
            print(f"[launcher] {qid} store verification FAILED; stopping campaign", flush=True)
            write_state({"phase": "ingest", "current": None, "done": results,
                         "stopped": "verification failed on " + qid})
            return 3
    write_state({"phase": "ingest-complete", "current": None, "done": results})
    print("[launcher] all stores ingested + verified", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
