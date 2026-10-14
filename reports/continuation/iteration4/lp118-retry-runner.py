#!/usr/bin/env python3
"""lp_118 full-stack E3 re-runs with the rerank parse-retry binary (x3).

S's exact setup: fresh2 store (skipIngest), E3 settings, no-narration addendum,
official-lp-v1 judge with LP_JUDGE_MAX_TOKENS=4096, question-scoped.
Only the driver binary differs: the improve/mem4b-lp-hard retry build.
"""
import importlib.util, json, os, sys, time

CAMP = r"C:/Users/openclawuser/optimus-memory-bench-20261007-040424"
LOOP = CAMP + r"/imp4/scripts/memory_question_loop.py"
DRIVER = os.environ.get(
    "LP118RT_DRIVER",
    CAMP + r"/imp4b-lphard-target/release/examples/memory_bench.exe")
WORKDIR = CAMP + r"/imp4"
SCRATCH = CAMP + r"/lphard-scratch"
ATT = SCRATCH + r"/ql-attempts"
FRESH2_ENV = CAMP + r"/lpq118-scratch/runs/lpq118_v4A_fresh2/envs/lp_118"
V4B_MANIFEST = CAMP + r"/lpq118-scratch/manifests/lpq118_v4B_e3_r1.json"
QS = CAMP + r"/lpq118-scratch/qs/qs_lp_118.jsonl"
MODELS_JSON = CAMP + r"/lpq118-scratch/agent-models.json"
JUDGE_MODELS = CAMP + r"/reports/continuation/iteration2-inputs/lme-temporal-flip/judge-models.json"
BENCH_PY = CAMP + r"/repo-continuation/bench/.venv/Scripts/python.exe"
SCORING_CWD = CAMP
RESULTS = SCRATCH + r"/results-lp118rt.jsonl"

spec = importlib.util.spec_from_file_location("mql", LOOP)
mql = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mql)

v4b = json.load(open(V4B_MANIFEST, encoding="utf-8"))


def log(msg):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def run_one(rep):
    run_id = f"lph-q118rt-{rep}"
    attempt_dir = os.path.join(ATT, run_id)
    if os.path.isdir(attempt_dir):
        log(f"{run_id}: attempt dir exists, skipping")
        return None
    os.makedirs(attempt_dir)
    t0 = time.time()
    copy_info = mql.copy_store_into_scratch(FRESH2_ENV, attempt_dir, "lp_118")
    settings = dict(v4b["settings"])
    question = dict(v4b["questions"][0])
    env_spec = {"env_id": "lp_118", "family": "loco_plus"}
    manifest = mql.build_manifest(run_id, "loco_plus", attempt_dir, env_spec, question, settings)
    mp = os.path.join(attempt_dir, "manifest.json")
    mql.atomic_write_json(mp, manifest)
    drv = mql.run_driver(DRIVER, mp, WORKDIR, MODELS_JSON, os.path.join(attempt_dir, "driver.log"))
    run_jsonl = os.path.join(attempt_dir, "run.jsonl")
    records = mql.extract_question_records(run_jsonl, "lp_118")
    row = {"run_id": run_id, "ts": time.strftime("%Y-%m-%dT%H:%M:%S")}
    if not records:
        row.update({"error": "no record", "driver_exit": drv["exit_code"]})
        with open(RESULTS, "a", encoding="utf-8") as f:
            f.write(json.dumps(row) + "\n")
        log(f"{run_id}: NO RECORD (driver exit {drv['exit_code']})")
        return row
    rec = records[0]
    qs_record = mql.load_question_record(QS, "lp_118")
    qs_record = mql.ensure_protocol_annotations(qs_record, "loco_plus")
    qs_dir, run_one_, _ = mql.build_scoring_inputs(attempt_dir, qs_record, run_jsonl, "lp_118")
    out = os.path.join(attempt_dir, "scoring", "scores.json")
    scores = mql.run_evaluator(BENCH_PY, SCORING_CWD, "locomo_plus", run_one_, qs_dir, out,
                               "official-lp-v1", JUDGE_MODELS,
                               os.path.join(attempt_dir, "scoring", "evaluator.log"),
                               extra_env={"LP_JUDGE_MAX_TOKENS": "4096"})
    res = mql.single_result(scores, "lp_118")
    rids = rec.get("recall_ids") or []
    mfr = rec.get("memory_feature_realization") or {}
    obs = mfr.get("llm_helper_observations") or {}
    rer = [o.get("outcome") for o in (obs.get("recallRerank") or [])]
    rer_retries = [o.get("retries") for o in (obs.get("recallRerank") or [])]
    rer_usage = [o.get("usage") for o in (obs.get("recallRerank") or [])]
    row.update({
        "score": res.get("score"), "flag": res.get("flag"), "label": res.get("label"),
        "judge_reason": (res.get("judge_reason") or "")[:200],
        "rerank_outcomes": rer, "rerank_retries": rer_retries, "rerank_usage": rer_usage,
        "recall_n": len(rids), "recall_ids": rids,
        "gold_recalled": any("son" in r and ("graduat" in r or "proud" in r) for r in rids),
        "answer_excerpt": (rec.get("answer_text") or "")[:200],
        "answer_chars": len(rec.get("answer_text") or ""),
        "driver_latency_ms": rec.get("latency_ms"),
        "driver_exit": drv["exit_code"],
        "wall_s": round(time.time() - t0, 1),
    })
    with open(RESULTS, "a", encoding="utf-8") as f:
        f.write(json.dumps(row) + "\n")
    log(f"{run_id}: score={row['score']} label={row['label']} rerank={rer} retries={rer_retries} gold={row['gold_recalled']} wall={row['wall_s']}s")
    return row


if __name__ == "__main__":
    reps = sys.argv[1:] or ["r1", "r2", "r3"]
    for rep in reps:
        run_one(rep)
    log("LP118RT DONE")
