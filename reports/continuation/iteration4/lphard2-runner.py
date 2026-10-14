#!/usr/bin/env python3
"""lp_146 read-time hardening hypotheses (H1/H3) + regression set.

Type-A re-answers (skipIngest) on frozen stores with the E3 fleet config
plus hypothesis overrides, question-scoped official-lp-v1 judging
(LP_JUDGE_MAX_TOKENS=4096). Mirrors lp-e3-broader-runner.py.
"""
import importlib.util, json, os, sys, time

CAMP = r"C:/Users/openclawuser/optimus-memory-bench-20261007-040424"
LOOP = CAMP + r"/imp4/scripts/memory_question_loop.py"
DRIVER = os.environ.get("LPHARD_DRIVER", CAMP + r"/imp4-targetR/release/examples/memory_bench.exe")
WORKDIR = CAMP + r"/imp4"
SCRATCH = CAMP + r"/lphard-scratch"
ATT = SCRATCH + r"/ql-attempts"
V3MANIFEST = CAMP + r"/reports/continuation/iteration3-inputs/manifests/loco_plus_dev5_improved_v3.json"
QS_COMMON = CAMP + r"/reports/continuation/locomo-plus-comparison-inputs/common/locomo_plus"
JUDGE_MODELS = CAMP + r"/reports/continuation/iteration2-inputs/lme-temporal-flip/judge-models.json"
MODELS_JSON = CAMP + r"/reports/continuation/c06-instruction-boundary/inputs/runs/locomo-c06-ib-system_append-q12-s0p1/envs/conv-30/agent/models.json"
BENCH_PY = CAMP + r"/repo-continuation/bench/.venv/Scripts/python.exe"
SCORING_CWD = CAMP
RESULTS = SCRATCH + r"/results.jsonl"
RUNS_SHORT = os.path.join(CAMP, "runs", "LOCOMO~1")

E3 = {"recallRerankCandidates": 500, "recallQueryExpansion": False, "recallQueryDistillation": False}

# frozen base-era stores (short-name path; runs/loco_plus long name is a ghost dir on this host)
BASE_SHARDS = {
    "lp_146": "s3", "lp_147": "s0", "lp_192": "s1", "lp_20": "s0",
}
# frozen v3 dev5 stores (1.0 bars)
V3_STORES = os.path.join(CAMP, "reports/continuation/iteration3-inputs/runs/loco_plus_dev5_improved_v3/envs")

spec = importlib.util.spec_from_file_location("mql", LOOP)
mql = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mql)

v3 = json.load(open(V3MANIFEST, encoding="utf-8"))

def log(msg):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)

def store_dir(qid):
    if qid in BASE_SHARDS:
        return os.path.join(RUNS_SHORT, BASE_SHARDS[qid], "envs", qid)
    return os.path.join(V3_STORES, qid)

def run_one(qid, tag, rep, overrides):
    run_id = f"lph-{tag}-{qid.split('_')[1]}-{rep}"
    attempt_dir = os.path.join(ATT, run_id)
    if os.path.isdir(attempt_dir):
        log(f"{run_id}: attempt dir exists, skipping")
        return None
    os.makedirs(attempt_dir)
    t0 = time.time()
    copy_info = mql.copy_store_into_scratch(store_dir(qid), attempt_dir, qid)
    settings = dict(v3["settings"])
    settings.update(E3)
    if overrides:
        settings.update(overrides)
    settings["skipIngest"] = True
    settings.setdefault("memoryLearning", False)
    settings.setdefault("disableTools", True)
    qs_record = mql.load_question_record(os.path.join(QS_COMMON, f"qs_{qid}.jsonl"), qid)
    qs_record = mql.ensure_protocol_annotations(qs_record, "loco_plus")
    question = mql.question_declaration(qs_record)
    env_spec = {"env_id": qid, "family": "loco_plus"}
    manifest = mql.build_manifest(run_id, "loco_plus", attempt_dir, env_spec, question, settings)
    mp = os.path.join(attempt_dir, "manifest.json")
    mql.atomic_write_json(mp, manifest)
    drv = mql.run_driver(DRIVER, mp, WORKDIR, MODELS_JSON, os.path.join(attempt_dir, "driver.log"))
    run_jsonl = os.path.join(attempt_dir, "run.jsonl")
    records = mql.extract_question_records(run_jsonl, qid)
    row = {"qid": qid, "tag": tag, "rep": rep, "run_id": run_id,
           "overrides": overrides,
           "ts": time.strftime("%Y-%m-%dT%H:%M:%S")}
    if not records:
        row.update({"error": "no record", "driver_exit": drv["exit_code"]})
        with open(RESULTS, "a", encoding="utf-8") as f:
            f.write(json.dumps(row) + "\n")
        log(f"{run_id}: NO RECORD (driver exit {drv['exit_code']})")
        return row
    rec = records[0]
    qs_dir, run_one_, _ = mql.build_scoring_inputs(attempt_dir, qs_record, run_jsonl, qid)
    out = os.path.join(attempt_dir, "scoring", "scores.json")
    scores = mql.run_evaluator(BENCH_PY, SCORING_CWD, "locomo_plus", run_one_, qs_dir, out,
                               "official-lp-v1", JUDGE_MODELS,
                               os.path.join(attempt_dir, "scoring", "evaluator.log"),
                               extra_env={"LP_JUDGE_MAX_TOKENS": "4096"})
    res = mql.single_result(scores, qid)
    rids = rec.get("recall_ids") or []
    mfr = rec.get("memory_feature_realization") or {}
    obs = mfr.get("llm_helper_observations") or {}
    rer = [o.get("outcome") for o in (obs.get("recallRerank") or [])]
    rer_usage = [o.get("usage") for o in (obs.get("recallRerank") or [])]
    row.update({
        "score": res.get("score"), "flag": res.get("flag"), "label": res.get("label"),
        "judge_reason": (res.get("judge_reason") or "")[:200],
        "rerank_outcomes": rer, "rerank_usage": rer_usage, "recall_n": len(rids),
        "recall_chars": rec.get("recall_chars"),
        "recall_ids": rids,
        "gold_recalled": any("allerg" in r for r in rids),
        "answer_chars": len(rec.get("answer_text") or ""),
        "answer_excerpt": (rec.get("answer_text") or "")[:200],
        "driver_latency_ms": rec.get("latency_ms"),
        "driver_exit": drv["exit_code"],
        "wall_s": round(time.time() - t0, 1),
    })
    with open(RESULTS, "a", encoding="utf-8") as f:
        f.write(json.dumps(row) + "\n")
    log(f"{run_id}: score={row['score']} label={row['label']} rerank={rer} gold={row['gold_recalled']} wall={row['wall_s']}s")
    return row

if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "hyp"
    if mode == "hyp":
        tasks = [
            ("lp_146", "h1a", "r1", {"recallRerankWithContent": True, "recallRerankContentChars": 240}),
            ("lp_146", "h1b", "r1", {"recallRerankWithContent": True, "recallRerankContentChars": 120}),
        ]
        for qid, tag, rep, ov in tasks:
            run_one(qid, tag, rep, ov)
        log("HYP DONE")
    elif mode == "one":
        # one qid, tag, rep, then overrides JSON
        qid, tag, rep = sys.argv[2], sys.argv[3], sys.argv[4]
        ov = json.loads(sys.argv[5]) if len(sys.argv) > 5 else None
        run_one(qid, tag, rep, ov)
        log("ONE DONE")
    elif mode == "many":
        # tasks JSON file: list of [qid, tag, rep, overrides]
        for qid, tag, rep, ov in json.load(open(sys.argv[2], encoding="utf-8")):
            run_one(qid, tag, rep, ov)
        log("MANY DONE")


def summarize(results_path):
    rows = [json.loads(l) for l in open(results_path, encoding="utf-8")]
    print(f"== {results_path} ({len(rows)} rows) ==")
    from collections import defaultdict
    by = defaultdict(list)
    for r in rows:
        by[(r.get("qid"), r.get("tag"))].append(r)
    for (qid, tag), rs in sorted(by.items()):
        scores = [r.get("score") for r in rs]
        rer = [r.get("rerank_outcomes") for r in rs]
        gold = [r.get("gold_recalled") for r in rs]
        print(f"  {qid} {tag}: scores={scores} rerank={rer} gold={gold}")
    return rows
