#!/usr/bin/env python3
"""writecamp-assist: LP dev5 single-store typeB ingest for glm-lpcamp's campaign.

Mirrors imp4b-lpcamp/.../lpcamp/ingest_one.py (controller args, verify logic,
state schema) with run-ids lpcamp-ing-l159 / lpcamp-ing-l69 per the campaign
spec. Driver: imp4-targetR (sha b57dc607). Cache root: lpcamp-scratch/cache
(shared campaign gate). Init-death retry: ONE retry after 5 min if the first
attempt dies within 3 min; mid-run deaths are reported, not retried.
"""
import json
import os
import sys
import time

# env vars BEFORE controller import (MODEL constant is read at import time)
os.environ["MEMORY_BENCH_MODEL_PROVIDER"] = "dgx-glm53"
os.environ["MEMORY_BENCH_MODEL_ID"] = "glm-5.3"

ROOT = r"C:/Users/openclawuser/optimus-memory-bench-20261007-040424"
IMP4 = os.path.join(ROOT, "imp4")
SCRATCH = os.path.join(ROOT, "lpcamp-scratch")
DRIVER = os.path.join(ROOT, "imp4-targetR/release/examples/memory_bench.exe")

RUN_ID_BASE = {"lp_159": "lpcamp-ing-l159", "lp_69": "lpcamp-ing-l69"}
GOLD_KEYWORDS = {
    "lp_159": ["fear of germs", "until they crack", "wash my hands"],
    "lp_69": ["GPS tag", "county fair"],
}


class Tee:
    def __init__(self, *streams):
        self.streams = streams
    def write(self, data):
        for s in self.streams:
            s.write(data)
            s.flush()
    def flush(self):
        for s in self.streams:
            s.flush()


def main(qid):
    log_path = os.path.join(SCRATCH, "logs", f"ingest-{qid}.log")
    state_path = os.path.join(SCRATCH, "state", f"ingest-{qid}.json")
    os.makedirs(os.path.dirname(log_path), exist_ok=True)
    os.makedirs(os.path.dirname(state_path), exist_ok=True)
    _log = open(log_path, "a", buffering=1, encoding="utf-8", newline="\n")
    sys.stdout = Tee(sys.stdout, _log)
    sys.stderr = sys.stdout

    sys.path.insert(0, os.path.join(IMP4, "scripts"))
    import memory_question_loop as M

    def log(msg):
        print(f"[wc-assist {time.strftime('%H:%M:%S')}] {msg}", flush=True)

    def write_state(payload):
        payload = dict(payload)
        payload["ts"] = time.strftime("%Y-%m-%dT%H:%M:%S")
        tmp = state_path + ".tmp"
        with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
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
            "--driver", DRIVER,
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

    def unique_run_id(base_rid):
        run_id, n = base_rid, 0
        while os.path.isdir(os.path.join(SCRATCH, "att", run_id)):
            n += 1
            suffix = f"x{n}"
            run_id = base_rid[:20 - len(suffix)] + suffix
        return run_id

    log(f"single-store ingest start {qid} (writecamp-assist, driver=imp4-targetR)")
    attempt = 0
    while attempt < 2:
        attempt += 1
        run_id = unique_run_id(RUN_ID_BASE[qid])
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
        if attempt == 1 and dur < 3.0:
            log(f"init-class death ({dur} min); retrying once after 5 min")
            time.sleep(300)
            continue
        write_state({"phase": "failed", "attempt": attempt, "run_id": run_id,
                     "rc": rc, "verify": ver})
        log(f"{qid} FAILED (no further retries)")
        return 3
    return 3


if __name__ == "__main__":
    sys.exit(main(sys.argv[1]))
