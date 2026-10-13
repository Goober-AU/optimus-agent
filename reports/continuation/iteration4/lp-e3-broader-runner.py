#!/usr/bin/env python3
"""E3 fleet validation on the LP base-population sample (20 blind-selected qids).

Type-A re-answers (skipIngest) on frozen base-era stores with the E3 read-time
config (pool 500 + expansion OFF + distillation OFF over the v3 LP base),
question-scoped official-lp-v1 judging (LP_JUDGE_MAX_TOKENS=4096).
Mirrors glm-lpq232's regression pattern (direct memory_question_loop import).
"""
import importlib.util, json, os, sys, threading, time

CAMP = r"C:/Users/openclawuser/optimus-memory-bench-20261007-040424"
LOOP = CAMP + r"/imp4/scripts/memory_question_loop.py"
DRIVER = CAMP + r"/imp4-targetR/release/examples/memory_bench.exe"
WORKDIR = CAMP + r"/imp4"
SCRATCH = CAMP + r"/lpe3-scratch"
ATT = SCRATCH + r"/ql-attempts"
V3MANIFEST = CAMP + r"/reports/continuation/iteration3-inputs/manifests/loco_plus_dev5_improved_v3.json"
QS_COMMON = CAMP + r"/reports/continuation/locomo-plus-comparison-inputs/common/locomo_plus"
JUDGE_MODELS = CAMP + r"/reports/continuation/iteration2-inputs/lme-temporal-flip/judge-models.json"
MODELS_JSON = CAMP + r"/reports/continuation/c06-instruction-boundary/inputs/runs/locomo-c06-ib-system_append-q12-s0p1/envs/conv-30/agent/models.json"
BENCH_PY = CAMP + r"/repo-continuation/bench/.venv/Scripts/python.exe"
SCORING_CWD = CAMP
RESULTS = SCRATCH + r"/broader-results.jsonl"
BASE_SCORES = CAMP + r"/scores/locomo_plus_base_full.json"

WIN = {"recallRerankCandidates": 500, "recallQueryExpansion": False, "recallQueryDistillation": False}
REPS = ["r1", "r2"]

# shard map for the frozen base stores (first intact project dir wins)
SHARDS = {
    "lp_157": "s0", "lp_317": "s3b", "lp_230": "s0", "lp_146": "s3", "lp_192": "s1",
    "lp_124": "s0_8h", "lp_327": "s1", "lp_378": "s2b", "lp_291": "s1", "lp_201": "s1",
    "lp_277": "s3b", "lp_261": "s0", "lp_62": "s2b", "lp_20": "s0", "lp_207": "s0",
    "lp_31": "s1", "lp_260": "s3b", "lp_344": "s0", "lp_147": "s0", "lp_60": "s1",
}

spec = importlib.util.spec_from_file_location("mql", LOOP)
mql = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mql)

v3 = json.load(open(V3MANIFEST, encoding="utf-8"))
base_scores = {r["qid"]: r for r in json.load(open(BASE_SCORES, encoding="utf-8"))["results"]}
write_lock = threading.Lock()

def log(msg):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)

def run_one(qid, rep):
    run_id = f"lpe3b-{qid[3:]}-{rep}"
    attempt_dir = os.path.join(ATT, run_id)
    if os.path.isdir(attempt_dir):
        log(f"{run_id}: attempt dir exists, skipping")
        return
    os.makedirs(attempt_dir)
    t0 = time.time()
    store_env_dir = os.path.join(CAMP, "runs/locomo_plus", SHARDS[qid], "envs", qid)
    copy_info = mql.copy_store_into_scratch(store_env_dir, attempt_dir, qid)
    settings = dict(v3["settings"])
    settings.update(WIN)
    settings["skipIngest"] = True
    settings.setdefault("memoryLearning", False)
    settings.setdefault("disableTools", True)
    qs_record = mql.load_question_record(os.path.join(QS_COMMON, f"qs_{qid}.jsonl"), qid)
    qs_record = mql.ensure_protocol_annotations(qs_record, "locomo_plus")
    question = mql.question_declaration(qs_record)
    env_spec = {"env_id": qid, "family": "locomo_plus"}
    manifest = mql.build_manifest(run_id, "locomo_plus", attempt_dir, env_spec, question, settings)
    mp = os.path.join(attempt_dir, "manifest.json")
    mql.atomic_write_json(mp, manifest)
    drv = mql.run_driver(DRIVER, mp, WORKDIR, MODELS_JSON, os.path.join(attempt_dir, "driver.log"))
    run_jsonl = os.path.join(attempt_dir, "run.jsonl")
    records = mql.extract_question_records(run_jsonl, qid)
    row = {"qid": qid, "rep": rep, "run_id": run_id, "ts": time.strftime("%Y-%m-%dT%H:%M:%S")}
    if not records:
        row.update({"error": "no record", "driver_exit": drv["exit_code"]})
        with write_lock:
            with open(RESULTS, "a", encoding="utf-8") as f:
                f.write(json.dumps(row) + "\n")
        log(f"{run_id}: NO RECORD (driver exit {drv['exit_code']})")
        return
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
    row.update({
        "score": res.get("score"), "flag": res.get("flag"), "label": res.get("label"),
        "judge_reason": (res.get("judge_reason") or "")[:200],
        "base_score": base_scores.get(qid, {}).get("score"),
        "rerank_outcomes": rer, "recall_n": len(rids),
        "recall_chars": rec.get("recall_chars"),
        "recall_ids": rids,
        "answer_chars": len(rec.get("answer_text") or ""),
        "answer_excerpt": (rec.get("answer_text") or "")[:200],
        "driver_latency_ms": rec.get("latency_ms"),
        "driver_exit": drv["exit_code"],
        "wall_s": round(time.time() - t0, 1),
    })
    with write_lock:
        with open(RESULTS, "a", encoding="utf-8") as f:
            f.write(json.dumps(row) + "\n")
    log(f"{run_id}: score={row['score']} label={row['label']} rerank={rer} recall={len(rids)} wall={row['wall_s']}s")

if __name__ == "__main__":
    qids = list(SHARDS)
    if len(sys.argv) > 1 and sys.argv[1] != "all":
        qids = sys.argv[1].split(",")
    RESULTS = SCRATCH + f"/broader-results-{qids[0]}.jsonl"
    globals()["RESULTS"] = RESULTS
    tasks = [(q, r) for q in qids for r in REPS]
    for qid, rep in tasks:
        run_one(qid, rep)
    log("RUNNER DONE")
