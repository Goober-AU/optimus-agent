#!/usr/bin/env python3
"""lp_232 winning-config regression: lp_18 / lp_159 / lp_69 (direct type-A manifests,
question-scoped official-lp-v1 scoring, LP_JUDGE_MAX_TOKENS=4096).

Mirrors the controller's type-A path (copy store, rebind projects.json, skipIngest)
for ledger-less qids, reusing memory_question_loop functions by import.
"""
import importlib.util, json, os, sys

SCRATCH = r"C:/Users/openclawuser/optimus-memory-bench-20261007-040424/reports/continuation/iteration4/lpq232-scratch"
LOOP = r"C:/Users/openclawususer-placeholder"  # replaced below
LOOP = r"C:/Users/openclawuser/optimus-memory-bench-20261007-040424/imp4/scripts/memory_question_loop.py"
DRIVER = r"C:/Users/openclawuser/optimus-memory-bench-20261007-040424/imp4-targetR/release/examples/memory_bench.exe"
WORKDIR = r"C:/Users/openclawuser/optimus-memory-bench-20261007-040424/imp4"
V3MANIFEST = r"C:/Users/openclawuser/optimus-memory-bench-20261007-040424/reports/continuation/iteration3-inputs/manifests/loco_plus_dev5_improved_v3.json"
FROZEN_ENVS = r"C:/Users/openclawuser/optimus-memory-bench-20261007-040424/reports/continuation/iteration3-inputs/runs/loco_plus_dev5_improved_v3/envs"
QS_DIR = r"C:/Users/openclawuser/optimus-memory-bench-20261007-040424/reports/continuation/locomo-plus-comparison-inputs/scoring"
JUDGE_MODELS = r"C:/Users/openclawuser/optimus-memory-bench-20261007-040424/reports/continuation/iteration2-inputs/lme-temporal-flip/judge-models.json"
MODELS_JSON = r"C:/Users/openclawuser/optimus-memory-bench-20261007-040424/reports/continuation/c06-instruction-boundary/inputs/runs/locomo-c06-ib-system_append-q12-s0p1/envs/conv-30/agent/models.json"
BENCH_PY = r"C:/Users/openclawuser/optimus-memory-bench-20261007-040424/repo-continuation/bench/.venv/Scripts/python.exe"
SCORING_CWD = r"C:/Users/openclawuser/optimus-memory-bench-20261007-040424"

WIN = {"recallRerankCandidates": 500, "recallQueryExpansion": False, "recallQueryDistillation": False}

spec = importlib.util.spec_from_file_location("mql", LOOP)
mql = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mql)

v3 = json.load(open(V3MANIFEST, encoding="utf-8"))
envs = {e["env_id"]: e for e in v3["envs"]}
questions = {q["qid"]: q for q in v3["questions"]}

def run_one(qid):
    attempt_dir = os.path.join(SCRATCH, "ql-attempts", f"lpq232-reg-{qid}")
    if os.path.isdir(attempt_dir):
        raise SystemExit(f"attempt dir exists: {attempt_dir}")
    os.makedirs(attempt_dir)
    store_env_dir = os.path.join(FROZEN_ENVS, qid)
    copy_info = mql.copy_store_into_scratch(store_env_dir, attempt_dir, qid)
    settings = dict(v3["settings"])
    settings.update(WIN)
    settings["skipIngest"] = True
    settings.setdefault("memoryLearning", False)
    settings.setdefault("disableTools", True)
    run_id = f"lpq232-reg-{qid}"
    env_spec = {"env_id": qid, "family": envs[qid].get("family")}
    manifest = mql.build_manifest(run_id, "locomo_plus", attempt_dir, env_spec,
                                  questions[qid], settings)
    manifest["envs"][0]["notes"] = envs[qid].get("notes")
    manifest["envs"][0]["question_protocol"] = envs[qid].get("question_protocol")
    mp = os.path.join(attempt_dir, "manifest.json")
    mql.atomic_write_json(mp, manifest)
    drv = mql.run_driver(DRIVER, mp, WORKDIR, MODELS_JSON, os.path.join(attempt_dir, "driver.log"))
    run_jsonl = os.path.join(attempt_dir, "run.jsonl")
    records = mql.extract_question_records(run_jsonl, qid)
    if not records:
        print(f"[{qid}] NO RECORD; driver exit {drv['exit_code']}")
        return
    qs_record = mql.load_question_record(os.path.join(QS_DIR, f"qs_{qid}.jsonl"), qid)
    qs_record = mql.ensure_protocol_annotations(qs_record, "locomo_plus")
    qs_dir, run_one_, _ = mql.build_scoring_inputs(attempt_dir, qs_record, run_jsonl, qid)
    out = os.path.join(attempt_dir, "scoring", "scores.json")
    scores = mql.run_evaluator(BENCH_PY, SCORING_CWD, "locomo_plus", run_one_, qs_dir, out,
                               "official-lp-v1", JUDGE_MODELS,
                               os.path.join(attempt_dir, "scoring", "evaluator.log"),
                               extra_env={"LP_JUDGE_MAX_TOKENS": "4096"})
    res = mql.single_result(scores, qid)
    rec = records[0]
    rids = rec.get("recall_ids") or []
    mfr = rec.get("memory_feature_realization") or {}
    obs = mfr.get("llm_helper_observations") or {}
    rer = [o.get("outcome") for o in obs.get("recallRerank", [])]
    print(f"[{qid}] score={res.get('score')} label={res.get('label')} flag={res.get('flag')} "
          f"rerank={rer} recall={len(rids)} entries; answer_chars={len(rec.get('answer_text') or '')}")
    print(f"[{qid}] recall_ids[:6]={rids[:6]}")

for qid in ("lp_18", "lp_159", "lp_69"):
    print(f"=== REGRESSION {qid} ===", flush=True)
    run_one(qid)
print("REGRESSIONS DONE", flush=True)
