"""LP dev5 campaign — rest-of-pipeline driver (detached, self-driving).

Waits for the ingest launcher (phase 1) to reach ingest-complete (all 5
stores ingested + cue/gold verified), then:
  phase 3: runs the controller E3 x2 per dev5 question on the warm cache
           (cache-reuse copy-out, skipIngest, E3 read config, official-lp-v1
           judge, LP_JUDGE_MAX_TOKENS=4096);
  finalize: collects every lpcamp-* attempt from the ledger + attempt dirs,
           classifies rerank flake exposure, compares against CC's frozen-
           store E3 reference, writes results JSON + report.md, copies
           evidence into the worktree and commits on improve/mem4b-lp-camp.

If the ingest campaign stops early (verification failure), this driver
aborts without running E3; the state file records the abort.
"""
import json
import os
import shutil
import subprocess
import sys
import time

WT = r"C:/Users/openclawuser/optimus-memory-bench-20261007-040424/imp4b-lpcamp"
IMP4 = r"C:/Users/openclawuser/optimus-memory-bench-20261007-040424/imp4"
ROOT = r"C:/Users/openclawuser/optimus-memory-bench-20261007-040424"
SCRATCH = os.path.join(ROOT, "lpcamp-scratch")
LOG = os.path.join(SCRATCH, "logs", "pipeline-rest.log")
INGEST_STATE = os.path.join(SCRATCH, "state", "ingest-progress.json")
STATE = os.path.join(SCRATCH, "state", "pipeline-progress.json")

os.environ["MEMORY_BENCH_MODEL_PROVIDER"] = "dgx-glm53"
os.environ["MEMORY_BENCH_MODEL_ID"] = "glm-5.3"

_log = open(LOG, "a", buffering=1, encoding="utf-8", newline="\n")
sys.stdout = _log
sys.stderr = _log

sys.path.insert(0, os.path.join(IMP4, "scripts"))
import memory_question_loop as M  # noqa: E402

QIDS = ["lp_232", "lp_118", "lp_18", "lp_159", "lp_69"]

GOLD_KEYWORDS = {
    "lp_232": ["carbon footprint"],
    "lp_118": ["graduat"],
    "lp_18": ["recovery code"],
    "lp_159": ["fear of germs", "until they crack", "wash my hands"],
    "lp_69": ["GPS tag", "county fair"],
}

# CC's frozen-store E3 reference (lp-e3-fleet-results.json, imp4 worktree)
FROZEN_E3 = {
    "lp_18": {"v3_baseline": 1.0, "e3_runs": [1.0], "e3_median": 1.0},
    "lp_69": {"v3_baseline": 1.0, "e3_runs": [1.0], "e3_median": 1.0},
    "lp_118": {"v3_baseline": 0.0, "e3_runs": [1.0, 0.0, 1.0], "e3_median": 1.0},
    "lp_159": {"v3_baseline": 1.0, "e3_runs": [1.0], "e3_median": 1.0},
    "lp_232": {"v3_baseline": 1.0, "e3_runs": [1.0, 0.0, 1.0], "e3_median": 1.0},
}
FROZEN_FLAKE = {"runs": 40, "rerank_used": 32, "flakes": 8,
                "flake_runs_scored_1": 5, "flake_runs_scored_0": 3}


def write_state(payload):
    payload = dict(payload)
    payload["ts"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    tmp = STATE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=1)
    os.replace(tmp, STATE)


def log(msg):
    print(f"[pipeline {time.strftime('%H:%M:%S')}] {msg}", flush=True)


def wait_for_ingest(timeout_s=16 * 3600):
    """Block until the ingest launcher reports ingest-complete (or aborts)."""
    t0 = time.time()
    logged = {}
    while True:
        if not os.path.isfile(INGEST_STATE):
            time.sleep(60)
            continue
        try:
            with open(INGEST_STATE, "r", encoding="utf-8") as fh:
                st = json.load(fh)
        except (json.JSONDecodeError, OSError):
            time.sleep(60)
            continue
        phase = st.get("phase")
        cur = st.get("current")
        if cur and logged.get("current") != cur:
            logged["current"] = cur
            log(f"ingest in progress: {cur} (done: {list((st.get('done') or {}).keys())})")
        if st.get("stopped"):
            return ("stopped", st)
        if phase == "ingest-complete":
            return ("complete", st)
        if time.time() - t0 > timeout_s:
            return ("timeout", st)
        time.sleep(60)


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


def gold_ids_for(qid):
    """Entry ids in the fresh store matching the gold cue keywords."""
    cache_root = os.path.join(SCRATCH, "cache")
    hits = [d for d in os.listdir(cache_root)
            if d.startswith(qid + "--") and ".failed-" not in d]
    if not hits:
        return set()
    proj_root = os.path.join(cache_root, hits[0], "envs", qid,
                             "agent", "memory", "projects")
    ids = set()
    for name in os.listdir(proj_root):
        cand = os.path.join(proj_root, name, "harness_state.json")
        if not os.path.isfile(cand):
            continue
        with open(cand, "r", encoding="utf-8") as fh:
            hs = json.load(fh)
        for eid, e in ((hs.get("entries") or {}).get("memory") or {}).items():
            text = " ".join([str(e.get("title") or ""), str(e.get("content") or "")]).lower()
            if any(kw.lower() in text for kw in GOLD_KEYWORDS[qid]):
                ids.add(eid)
    return ids


def read_attempt_detail(run_dir, qid, golds):
    """Parse one attempt's run.jsonl for rerank outcome + gold recall."""
    detail = {"run_dir": run_dir, "rerank_outcome": None, "gold_recalled": None,
              "recall_ids": [], "answer_chars": 0}
    rj = os.path.join(run_dir, "run.jsonl")
    if not os.path.isfile(rj):
        return detail
    rows = [json.loads(l) for l in open(rj, "r", encoding="utf-8") if l.strip()]
    qrows = [r for r in rows if r.get("qid") == qid and r.get("variant") == "improved"]
    if not qrows:
        return detail
    rec = qrows[-1]
    detail["recall_ids"] = rec.get("recall_ids") or []
    detail["answer_chars"] = len(rec.get("answer_text") or "")
    mfr = rec.get("memory_feature_realization") or {}
    obs = mfr.get("llm_helper_observations") or {}
    rr = obs.get("recallRerank") or []
    if rr:
        detail["rerank_outcome"] = rr[-1].get("outcome")
    if golds:
        detail["gold_recalled"] = any(
            rid.split(":")[-1] in golds for rid in detail["recall_ids"])
    return detail


def collect_results():
    """All lpcamp-* attempts for the 5 qids from the ledger + attempt dirs."""
    ledger = M.load_ledger(os.path.join(
        ROOT, "reports/continuation/iteration4/QUESTION-LEDGER.json"))
    out = {}
    for qid in QIDS:
        entry = M.find_entry(ledger, qid)
        golds = gold_ids_for(qid)
        runs = []
        for a in (entry.get("attempts") or []):
            rid = a.get("run_id") or ""
            if not rid.startswith("lpcamp-"):
                continue
            detail = read_attempt_detail(a.get("run_dir") or "", qid, golds)
            runs.append({
                "run_id": rid,
                "phase": "ingest" if rid.startswith("lpcamp-ing-") else "e3",
                "score": a.get("score"),
                "flag": a.get("flag"),
                "outcome": a.get("outcome"),
                "judge_label": a.get("judge_label"),
                "answer_excerpt": a.get("answer_excerpt"),
                "mode_detail": a.get("mode_detail"),
                "latency_ms": a.get("latency_ms"),
                **detail,
            })
        out[qid] = {"gold_ids": sorted(golds), "runs": runs}
    return out


def classify(results):
    """dev5 table + flake exposure for the fresh-store campaign."""
    table = {}
    for qid in QIDS:
        runs = results[qid]["runs"]
        e3 = [r for r in runs if r["phase"] == "e3"]
        ing = [r for r in runs if r["phase"] == "ingest"]
        scores = [r["score"] for r in e3]
        pair_pass = None
        if len(scores) >= 2:
            s1, s2 = scores[0], scores[1]
            both = (s1 == 1.0 and s2 == 1.0)
            one_plus_flake = ((s1 == 1.0 and s2 == 0.0) or (s1 == 0.0 and s2 == 1.0)) and any(
                r["rerank_outcome"] not in (None, "used") and r["score"] == 0.0 for r in e3)
            pair_pass = bool(both or one_plus_flake)
        table[qid] = {
            "ingest_draws": [(r["run_id"], r["score"]) for r in ing],
            "e3_pair": [(r["run_id"], r["score"], r["rerank_outcome"],
                         r["gold_recalled"]) for r in e3],
            "e3_scores": scores,
            "pair_pass": pair_pass,
        }
    all_runs = [r for qid in QIDS for r in results[qid]["runs"]]
    used = [r for r in all_runs if r["rerank_outcome"] == "used"]
    flakes = [r for r in all_runs if r["rerank_outcome"] not in (None, "used")]
    flake_1 = [r for r in flakes if r["score"] == 1.0]
    flake_gold = [r for r in flakes if r.get("gold_recalled")]
    return {
        "table": table,
        "n_runs": len(all_runs),
        "rerank_used": len(used),
        "flakes": len(flakes),
        "flake_runs_scored_1": len(flake_1),
        "flake_runs_gold_recalled": len(flake_gold),
        "flake_detail": [{"run_id": r["run_id"], "score": r["score"],
                          "rerank": r["rerank_outcome"],
                          "gold_recalled": r.get("gold_recalled")} for r in flakes],
    }


def write_report(results, cls, verdict_lines):
    rep_dir = os.path.join(WT, "reports/continuation/iteration4/lpcamp")
    res_path = os.path.join(rep_dir, "results.json")
    with open(res_path, "w", encoding="utf-8", newline="\n") as fh:
        json.dump({"schema": "lpcamp-results/1",
                   "created": time.strftime("%Y-%m-%d"),
                   "results": results, "classification": cls,
                   "frozen_e3_reference": FROZEN_E3,
                   "frozen_flake_reference": FROZEN_FLAKE}, fh, indent=1)
        fh.write("\n")
    md = ["# LP dev5 fresh re-ingest campaign — results", "",
          "Worker: glm-lpcamp. Branch `improve/mem4b-lp-camp`. Binary imp4-targetR (sha b57dc607).",
          "Model dgx-glm53/glm-5.3 only. Judge official-lp-v1, LP_JUDGE_MAX_TOKENS=4096, question-scoped.", ""]
    md += ["## Per-store ingest stats", "",
           "| qid | entries | cue coverage | gold entries | verify |",
           "| --- | --- | --- | --- | --- |"]
    for qid in QIDS:
        v = (results.get("_ingest_verify") or {}).get(qid) or {}
        md.append(f"| {qid} | {v.get('entries', '?')} | {v.get('cue_coverage_pct', '?')}% | "
                  f"{v.get('gold_entries', '?')} | {'OK' if v.get('ok') else 'FAIL'} |")
    md += ["", "## dev5 table (fresh E3 vs frozen E3 vs baseline)", "",
           "| qid | v3 baseline | frozen E3 | fresh E3 x2 | pair-pass |",
           "| --- | --- | --- | --- | --- |"]
    for qid in QIDS:
        t = cls["table"][qid]
        md.append(f"| {qid} | {FROZEN_E3[qid]['v3_baseline']} | "
                  f"{FROZEN_E3[qid]['e3_median']} (median) | "
                  f"{' / '.join(str(s) for s in t['e3_scores'])} | {t['pair_pass']} |")
    md += ["", "## Flake exposure", "",
           f"- fresh: {cls['flakes']}/{cls['n_runs']} runs rerank-invalid"
           f" ({cls['flake_runs_scored_1']} still scored 1.0;"
           f" {cls['flake_runs_gold_recalled']} recalled gold via lexical fallback)",
           f"- frozen (CC, 40 runs): {FROZEN_FLAKE['flakes']}/{FROZEN_FLAKE['runs']} "
           f"({FROZEN_FLAKE['flake_runs_scored_1']} still 1.0; 3 scored 0.0)", ""]
    md += ["## Verdict", ""] + [f"- {l}" for l in verdict_lines] + [""]
    with open(os.path.join(rep_dir, "report.md"), "w", encoding="utf-8", newline="\n") as fh:
        fh.write("\n".join(md))
    return res_path


def copy_evidence(results):
    ev_root = os.path.join(WT, "reports/continuation/iteration4/lpcamp/evidence")
    copied = []
    for qid in QIDS:
        for r in results[qid]["runs"]:
            rd = r.get("run_dir")
            if not rd or not os.path.isdir(rd):
                continue
            dst = os.path.join(ev_root, r["run_id"])
            os.makedirs(dst, exist_ok=True)
            for name in ("manifest.json", "driver.log"):
                src = os.path.join(rd, name)
                if os.path.isfile(src) and os.path.getsize(src) < 200_000:
                    shutil.copyfile(src, os.path.join(dst, name))
            sc = os.path.join(rd, "scoring", "scores.json")
            if os.path.isfile(sc):
                shutil.copyfile(sc, os.path.join(dst, "scores.json"))
            copied.append(r["run_id"])
    return copied


def git_commit():
    try:
        subprocess.run(["git", "-C", WT, "add",
                       "reports/continuation/iteration4/lpcamp"], check=True)
        cp = subprocess.run(["git", "-C", WT, "commit", "-q", "-m",
                             "bench(lpcamp): fresh-store E3 x2 results — dev5 table, flake exposure, verdict"],
                            capture_output=True, text=True, timeout=120)
        return cp.returncode, (cp.stdout or "") + (cp.stderr or "")
    except Exception as exc:
        return None, repr(exc)


def main():
    log("rest-of-pipeline driver start")
    write_state({"phase": "waiting-for-ingest"})
    status, st = wait_for_ingest()
    log(f"ingest phase ended: {status}")
    if status != "complete":
        write_state({"phase": "aborted", "reason": "ingest " + status,
                     "ingest_state": st})
        log(f"aborting: ingest {status}")
        return 3
    write_state({"phase": "e3"})
    results = {"_ingest_verify": {q: (st.get("done") or {}).get(q, {}).get("verify")
                                  for q in QIDS}}
    for qid in QIDS:
        for r in ("r1", "r2"):
            run_id = f"lpcamp-e3-{qid.replace('lp_', 'lp')}-{r}"[:20]
            log(f"starting {run_id}")
            t0 = time.time()
            try:
                rc = M.main(controller_args(qid, run_id))
            except Exception as exc:
                log(f"{run_id} controller exception: {exc!r}")
                rc = None
            log(f"{run_id} rc={rc} ({round((time.time()-t0)/60.0, 1)} min)")
            write_state({"phase": "e3", "done_run": run_id, "rc": rc})
    write_state({"phase": "finalizing"})
    log("collecting results")
    results.update(collect_results())
    cls = classify(results)
    n_pass = sum(1 for q in QIDS if cls["table"][q]["pair_pass"])
    verdict = [
        f"dev5 pair-pass: {n_pass}/5 (target 5/5).",
        f"flake exposure fresh: {cls['flakes']}/{cls['n_runs']} rerank-invalid runs; "
        f"{cls['flake_runs_gold_recalled']} of those still recalled gold via lexical fallback.",
        "frozen reference (CC): 8/40 rerank-invalid; 5/8 still 1.0 — see report.md table for the durable-verdict comparison.",
    ]
    log("writing report + evidence")
    write_report(results, cls, verdict)
    copied = copy_evidence(results)
    rc, out = git_commit()
    log(f"git commit rc={rc}: {out.strip()[:200]}")
    write_state({"phase": "complete", "evidence_runs": copied, "git_rc": rc,
                 "pair_pass": n_pass})
    log("pipeline complete")
    return 0


if __name__ == "__main__":
    sys.exit(main())
