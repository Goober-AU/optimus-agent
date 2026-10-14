"""LP campaign single-store parallel ingest driver.

Usage: python ingest_one.py <qid>

TypeB ingest of one store with E3 read settings + question draw + judge,
retries up to 3 attempts on driver failure (cache stays cold -> auto
quarantine + re-ingest), verifies cue coverage + gold, writes
state/ingest-<qid>.json {phase: ok|failed, verify: {...}}.
Run under Task Scheduler; safe to run concurrently with other stores'
drivers (distinct cache dirs + attempt dirs).
"""
import json
import os
import sys
import time

WT = r"C:/Users/openclawuser/optimus-memory-bench-20261007-040424/imp4b-lpcamp"
IMP4 = r"C:/Users/openclawuser/optimus-memory-bench-20261007-040424/imp4"
ROOT = r"C:/Users/openclawuser/optimus-memory-bench-20261007-040424"
SCRATCH = os.path.join(ROOT, "lpcamp-scratch")

GOLD_KEYWORDS = {
    "lp_232": ["carbon footprint"],
    "lp_118": ["graduat"],
    "lp_18": ["recovery code"],
    "lp_159": ["fear of germs", "until they crack", "wash my hands"],
    "lp_69": ["GPS tag", "county fair"],
}


def main(qid):
    log_path = os.path.join(SCRATCH, "logs", f"ingest-{qid}.log")
    state_path = os.path.join(SCRATCH, "state", f"ingest-{qid}.json")
    os.environ["MEMORY_BENCH_MODEL_PROVIDER"] = "dgx-glm53"
    os.environ["MEMORY_BENCH_MODEL_ID"] = "glm-5.3"

    _log = open(log_path, "a", buffering=1, encoding="utf-8", newline="\n")
    sys.stdout = _log
    sys.stderr = _log

    sys.path.insert(0, os.path.join(IMP4, "scripts"))
    import memory_question_loop as M

    def log(msg):
        print(f"[one {time.strftime('%H:%M:%S')}] {msg}", flush=True)

    def write_state(payload):
        payload = dict(payload)
        payload["ts"] = time.strftime("%Y-%m-%dT%H:%M:%S")
        tmp = state_path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=1)
        os.replace(tmp, state_path)

    def verify():
        cache_root = os.path.join(SCRATCH, "cache")
        hits = [d for d in os.listdir(cache_root)
                if d.startswith(qid + "--") and ".failed-" not in d]
        if len(hits) != 1:
            return {"ok": False, "error": f"cache dirs: {hits}"}
        cd = os.path.join(cache_root, hits[0])
        if not os.path.isfile(os.path.join(cd, "env.jsonl")):
            return {"ok": False, "error": "no env.jsonl (ingest incomplete)"}
        proj_root = os.path.join(cd, "envs", qid, "agent", "memory", "projects")
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

    def controller_args(run_id):
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

    log(f"single-store ingest start {qid}")
    for attempt in range(1, 4):
        base = f"lpcamp-pin-{qid.replace('lp_', 'lp')}"[:20]
        run_id, n = base, 0
        while os.path.isdir(os.path.join(SCRATCH, "att", run_id)):
            n += 1
            suffix = f"x{n}"
            run_id = (base[:20 - len(suffix)] + suffix)
        write_state({"phase": "ingest", "attempt": attempt, "run_id": run_id})
        log(f"attempt {attempt} run_id={run_id}")
        t0 = time.time()
        try:
            rc = M.main(controller_args(run_id))
        except Exception as exc:
            log(f"attempt {attempt} exception: {exc!r}")
            rc = None
        dur = round((time.time() - t0) / 60.0, 1)
        ver = verify()
        log(f"attempt {attempt} rc={rc} ({dur} min) verify={json.dumps(ver)}")
        write_state({"phase": "attempt-done", "attempt": attempt, "run_id": run_id,
                     "rc": rc, "verify": ver})
        if ver.get("ok"):
            write_state({"phase": "ok", "attempt": attempt, "run_id": run_id, "verify": ver})
            log(f"{qid} OK")
            return 0
        time.sleep(120)
    write_state({"phase": "failed"})
    log(f"{qid} FAILED after 3 attempts")
    return 3


if __name__ == "__main__":
    sys.exit(main(sys.argv[1]))
