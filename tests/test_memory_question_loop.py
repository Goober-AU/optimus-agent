"""Controller tests for scripts/memory_question_loop.py.

Mock driver + mock evaluator; no Rust build, no network, no LLM calls.
Covers: question-scoped scoring, ledger append/next/resume (atomicity),
type-A store copy + project rebinding, type-B cache-key stability and
cold/warm cache flow, gold-free manifests, exit-code contract.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
import threading

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO_ROOT)

import importlib.util

_spec = importlib.util.spec_from_file_location(
    "memory_question_loop", os.path.join(REPO_ROOT, "scripts", "memory_question_loop.py"))
mql = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mql)


# --------------------------------------------------------------------------
# fixtures
# --------------------------------------------------------------------------

MOCK_DRIVER = r'''
import json, os, sys
manifest = json.load(open(sys.argv[1], encoding="utf-8"))
out = manifest["output_dir"]
os.makedirs(out, exist_ok=True)
env = manifest["envs"][0]
memory_root = os.path.join(out, "envs", env["env_id"], "agent", "memory", "projects")
if not manifest["settings"].get("skipIngest"):
    project_dir = os.path.join(memory_root, "project_ingested01")
    os.makedirs(project_dir, exist_ok=True)
    with open(os.path.join(project_dir, "harness_state.json"), "w", encoding="utf-8") as h:
        json.dump({"memory": {"projectId": "project_ingested01", "revision": 1},
                   "entries": {"memory": {}}}, h)
    with open(os.path.join(out, "env.jsonl"), "a", encoding="utf-8") as h:
        h.write(json.dumps({"run_id": manifest["run_id"], "benchmark": manifest["benchmark"],
                            "variant": "improved", "env_id": env["env_id"]}) + "\n")
else:
    # The driver answers against the copied store: probe that it survived.
    assert os.path.isdir(memory_root), "type-A copy missing at answer time"
q = manifest["questions"][0]
answer = os.environ.get("MOCK_ANSWER", "mock answer text")
record = {
    "run_id": manifest["run_id"], "benchmark": manifest["benchmark"],
    "variant": "improved", "env_id": q["env_id"], "qid": q["qid"],
    "question": q["question"], "answer_text": answer,
    "recall_ids": ["mem_a", "mem_b"], "recall_chars": 123,
    "latency_ms": 42, "stop_reason": "stop", "answer": None, "evidence": [],
    "usage": {"input_tokens": 10, "output_tokens": 5, "total_tokens": 15},
    "memory_feature_realization": {"llm_helper_observations": {
        "recallRerank": [{"attempted": True, "outcome": "parsed", "latencyMs": 9,
                          "usage": {"input": 5, "output": 2}}]}},
}
with open(os.path.join(out, "run.jsonl"), "a", encoding="utf-8") as h:
    h.write(json.dumps(record) + "\n")
'''

MOCK_EVALUATOR = r'''
import argparse, json, os
ap = argparse.ArgumentParser()
ap.add_argument("--run", required=True)
ap.add_argument("--bench", required=True)
ap.add_argument("--out", required=True)
ap.add_argument("--qs-dir", required=True)
ap.add_argument("--variant", default=None)
ap.add_argument("--models-json", default=None)
ap.add_argument("--judge-profile", dest="judge_profile", default="default")
ap.add_argument("--workers", type=int, default=1)
ap.add_argument("--limit", type=int, default=0)
ap.add_argument("--full", action="store_true")
args = ap.parse_args()
qs_files = sorted(f for f in os.listdir(args.qs_dir) if f.startswith("qs_"))
run_rows = [json.loads(l) for l in open(args.run, encoding="utf-8") if l.strip()]
probe = {"qs_files": qs_files, "run_records": run_rows,
         "judge_profile": args.judge_profile, "variant": args.variant,
         "models_json": args.models_json, "bench": args.bench,
         "evaluator_module": os.path.abspath(__file__)}
with open(os.environ["EVAL_PROBE"], "w", encoding="utf-8") as h:
    json.dump(probe, h)
score = float(os.environ.get("EVAL_SCORE", "1.0"))
flag = os.environ.get("EVAL_FLAG") or None
with open(os.path.join(args.qs_dir, qs_files[0]), encoding="utf-8") as h:
    question = json.loads(h.readline())
results = [{"qid": question["qid"], "env_id": question.get("env_id"),
            "category": question.get("category"),
            "eval_type": (question.get("eval") or {}).get("type"),
            "score": score, "flag": flag, "judge": "mock-judge",
            "judge_reason": "mock gold echo from evaluator"}]
with open(args.out, "w", encoding="utf-8") as h:
    json.dump({"schema": "memory-bench-scores/1", "results": results,
               "overall": {"n": 1}}, h)
print("ok")
'''
def _write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(text)
    return path


def make_baseline_store(root, env_id="lp_18", project_id="project_base0001"):
    """A fake ingested store: agent/memory/projects/<id>/harness_state.json."""
    env_dir = os.path.join(root, "runs", "baseline", "envs", env_id)
    projects_dir = os.path.join(env_dir, "agent", "memory", "projects", project_id)
    os.makedirs(projects_dir, exist_ok=True)
    with open(os.path.join(projects_dir, "harness_state.json"), "w", encoding="utf-8") as h:
        json.dump({"memory": {"projectId": project_id, "revision": 7},
                   "entries": {"memory": {"m1": {"id": "m1"}}}}, h)
    original_workspace = os.path.join(env_dir, "workspace")
    os.makedirs(original_workspace, exist_ok=True)
    with open(os.path.join(env_dir, "agent", "memory", "projects.json"), "w",
              encoding="utf-8") as h:
        json.dump({mql.windows_alias(original_workspace): project_id}, h)
    with open(os.path.join(env_dir, "agent", "memory.lock.guard"), "w") as h:
        h.write("")  # transient lock artifact: must not be copied
    with open(os.path.join(env_dir, "agent", "settings.json"), "w", encoding="utf-8") as h:
        json.dump({"memory": {"recall": True}}, h)
    return env_dir


def make_qs_file(root, qid="lp_18", env_id="lp_18", answer="gold secret",
                 question="What is the answer?", category="causal|x"):
    path = os.path.join(root, "qs", f"qs_{qid}.jsonl")
    record = {
        "qid": qid, "env_id": env_id, "question": question, "answer": answer,
        "evidence": ["cue_1"], "category": category,
        "eval": {"type": "llm_judge", "params": {"evidence_cue": "cue"}, "protocol": "optimus-local-scoring/1.0.0"},
        "family": "locomo_plus", "question_protocol": "optimus-normalized-cues/1.0.0",
    }
    return _write(path, json.dumps(record, ensure_ascii=False) + "\n")


def make_manifest_dir(root, run_id="baseline_run", env_id="lp_18", import_instructions="base import"):
    settings = {
        "recallOn": True, "memoryLearning": False, "answerMaxTokens": 8192,
        "maxRecallChars": 24000, "maxRecallEntries": 20, "disableTools": True,
        "skipIngest": False, "importInstructions": import_instructions,
        "maxExtractionTokens": 32000, "maxImportChunkChars": 15000,
        "answerInstruction": "answer directly", "questionTimeoutS": 300,
    }
    manifest = {
        "run_id": run_id, "benchmark": "locomo_plus", "variant": "improved",
        "output_dir": os.path.join(root, "runs", run_id),
        "captureProtocol": "optimus-memory-capture/temporal-authority/1.0.0",
        "envs": [{"env_id": env_id, "sessions": [{"session_id": "s1", "events": []}],
                  "ground_truth_refs": [], "family": "locomo_plus"}],
        "questions": [], "model": {"provider": "dgx-glm53", "id": "glm-5.3"},
        "settings": settings,
    }
    return _write(os.path.join(root, "manifests", f"{run_id}.json"),
                  json.dumps(manifest, indent=1))


def make_env_file(root, env_id="lp_18"):
    env = {"env_id": env_id, "family": "locomo_plus",
           "sessions": [{"session_id": "s1", "events": []}],
           "ground_truth_refs": [], "notes": {"k": 1}}
    return _write(os.path.join(root, "qs", f"env_{env_id}.json"), json.dumps(env))


def make_ledger(path, entries):
    ledger = {
        "schema": "iteration4-question-ledger/2",
        "created": "2026-10-13T00:00:00+00:00",
        "ordering": "(status_rank, benchmark, queue_rank, qid)",
        "status_semantics": {}, "summary": {}, "entries": entries,
    }
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(ledger, handle, indent=1)
    return ledger


def base_entry(root, qid="lp_18", env_id="lp_18", benchmark="locomo_plus",
               status="FAILED", queue="evidence", store=True, source_run=True):
    entry = {
        "baseline": 0.0, "benchmark": benchmark, "category": "causal|x",
        "env_id": env_id, "judge_profile": "official-lp-v1", "qid": qid,
        "qs_path": os.path.join(root, "qs", f"qs_{qid}.jsonl"), "queue": queue,
        "status": status, "store_path": None, "note": "test entry",
    }
    if store:
        entry["store_path"] = os.path.join(root, "runs", "baseline", "envs", env_id)
    if source_run:
        entry["source_run"] = os.path.join(root, "runs", "baseline_run", "run.jsonl")
    return entry


@pytest.fixture
def world(tmp_path):
    """A full controller world: ledger, store, qs, manifests, mock driver,
    mock evaluator package, scratch roots."""
    root = str(tmp_path)
    store_dir = make_baseline_store(root)
    qs_path = make_qs_file(root)
    make_manifest_dir(root)
    driver = _write(os.path.join(root, "mocks", "memory_bench.py"), MOCK_DRIVER)
    scoring_cwd = os.path.join(root, "scoring")
    _write(os.path.join(scoring_cwd, "bench", "__init__.py"), "")
    _write(os.path.join(scoring_cwd, "bench", "eval", "__init__.py"), "")
    _write(os.path.join(scoring_cwd, "bench", "eval", "evaluate.py"), MOCK_EVALUATOR)
    judge_models = _write(os.path.join(root, "judge-models.json"),
                          json.dumps({"providers": {"dgx-glm53": {"baseUrl": "http://x", "apiKey": "k"}}}))
    ledger_path = os.path.join(root, "QUESTION-LEDGER.json")
    make_ledger(ledger_path, [base_entry(root)])
    return {
        "root": root, "store_dir": store_dir, "qs_path": qs_path,
        "driver": driver, "scoring_cwd": scoring_cwd, "judge_models": judge_models,
        "ledger": ledger_path,
        "attempts": os.path.join(root, "attempts"),
        "cache": os.path.join(root, "cache"),
        "probe": os.path.join(root, "eval_probe.json"),
    }


def cli_args(world, qid=None, extra=None, settings=None, mode=None):
    args = ["--ledger", world["ledger"], "--driver", world["driver"],
            "--driver-workdir", world["root"],
            "--models-json", world["judge_models"],
            "--judge-models-json", world["judge_models"],
            "--bench-venv", sys.executable,
            "--scoring-cwd", world["scoring_cwd"],
            "--attempts-root", world["attempts"],
            "--cache-root", world["cache"],
            "--manifest-dir", os.path.join(world["root"], "manifests")]
    if qid:
        args.append(qid)
    if settings:
        args += ["--settings", settings]
    if mode:
        args += ["--mode", mode]
    if extra:
        args += extra
    return args


def run_cli(world, qid=None, settings=None, mode=None, extra=None, env=None):
    keys = ("EVAL_PROBE", "EVAL_SCORE", "EVAL_FLAG", "MOCK_ANSWER")
    saved = {k: os.environ.get(k) for k in keys}
    for k in keys:
        os.environ.pop(k, None)
    os.environ["EVAL_PROBE"] = world["probe"]
    if env:
        os.environ.update(env)
    try:
        return mql.main(cli_args(world, qid=qid, settings=settings, mode=mode, extra=extra))
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def load_ledger(world):
    return json.load(open(world["ledger"], encoding="utf-8"))


# --------------------------------------------------------------------------
# type-A store copy
# --------------------------------------------------------------------------

class TestTypeAStoreCopy:
    def test_copy_rebinds_project_and_skips_locks(self, world):
        scratch = os.path.join(world["root"], "scratchout")
        info = mql.copy_store_into_scratch(world["store_dir"], scratch, "lp_18")
        assert info["project_id"] == "project_base0001"
        doc = os.path.join(scratch, "envs/lp_18/agent/memory/projects",
                           "project_base0001", "harness_state.json")
        assert os.path.isfile(doc)
        registry = json.load(open(os.path.join(scratch, "envs/lp_18/agent/memory/projects.json"),
                                  encoding="utf-8"))
        alias = mql.windows_alias(os.path.join(scratch, "envs/lp_18/workspace"))
        assert registry[alias] == "project_base0001"
        assert len(registry) == 2  # original + rebound
        copied = [os.path.join(dp, f) for dp, _dn, fn in os.walk(scratch) for f in fn]
        assert not any(f.endswith(".lock.guard") for f in copied)

    def test_baseline_store_never_mutated(self, world):
        before = {p: hashlib.sha256(open(p, "rb").read()).hexdigest()
                  for p in [os.path.join(dp, f) for dp, _dn, fn in
                            os.walk(world["store_dir"]) for f in fn]}
        mql.copy_store_into_scratch(world["store_dir"],
                                    os.path.join(world["root"], "scratchout2"), "lp_18")
        after = {p: hashlib.sha256(open(p, "rb").read()).hexdigest()
                 for p in [os.path.join(dp, f) for dp, _dn, fn in
                           os.walk(world["store_dir"]) for f in fn]}
        assert before == after

    def test_missing_memory_dir_fails_closed(self, world):
        empty = os.path.join(world["root"], "empty_env", "lp_18")
        os.makedirs(empty, exist_ok=True)
        with pytest.raises(mql.ControllerError):
            mql.copy_store_into_scratch(empty, os.path.join(world["root"], "x"), "lp_18")


# --------------------------------------------------------------------------
# type-B cache key
# --------------------------------------------------------------------------

class TestTypeBCacheKey:
    def env_spec(self):
        return {"env_id": "lp_18", "family": "locomo_plus",
                "sessions": [{"session_id": "s1", "events": [{"text": "hi"}]}],
                "ground_truth_refs": [], "notes": {"a": 1}}

    def settings(self, **over):
        base = {"importInstructions": "imp v1", "maxExtractionTokens": 32000,
                "maxImportChunkChars": 15000, "recallProspectiveIndex": True,
                "memoryLearning": False}
        base.update(over)
        return base

    def test_same_inputs_same_key(self):
        key1 = mql.typeb_cache_key(self.env_spec(), self.settings(), mql.MODEL)
        key2 = mql.typeb_cache_key(self.env_spec(), self.settings(), mql.MODEL)
        assert key1 == key2 and len(key1) == 64

    def test_read_time_changes_reuse_cache(self):
        key1 = mql.typeb_cache_key(self.env_spec(), self.settings(), mql.MODEL)
        key2 = mql.typeb_cache_key(
            self.env_spec(), self.settings(maxRecallChars=999, maxRecallEntries=3,
                                           recallRerank=False, answerInstruction="x"),
            mql.MODEL)
        assert key1 == key2

    def test_write_time_changes_new_key(self):
        key1 = mql.typeb_cache_key(self.env_spec(), self.settings(), mql.MODEL)
        for over in ({"importInstructions": "imp v2"},
                     {"maxExtractionTokens": 16000},
                     {"maxImportChunkChars": 8000},
                     {"recallProspectiveIndex": False}):
            assert mql.typeb_cache_key(self.env_spec(), self.settings(**over),
                                       mql.MODEL) != key1, over

    def test_source_change_new_key(self):
        env2 = self.env_spec()
        env2["sessions"][0]["events"][0]["text"] = "changed"
        assert (mql.typeb_cache_key(env2, self.settings(), mql.MODEL)
                != mql.typeb_cache_key(self.env_spec(), self.settings(), mql.MODEL))

    def test_model_change_new_key(self):
        assert (mql.typeb_cache_key(self.env_spec(), self.settings(),
                                    {"provider": "x", "id": "y"})
                != mql.typeb_cache_key(self.env_spec(), self.settings(), mql.MODEL))


# --------------------------------------------------------------------------
# ledger append / next / resume
# --------------------------------------------------------------------------

class TestLedger:
    def attempt(self, outcome="fail", score=0.0):
        return {"schema": mql.ATTEMPT_SCHEMA, "ts": mql.now_iso(),
                "qid": "lp_18", "outcome": outcome, "score": score,
                "run_dir": "x", "manifest": "y", "mode": "typeA"}

    def test_append_attempt_additive_and_atomic(self, world):
        before = load_ledger(world)
        mql.append_attempt(world["ledger"], "lp_18", self.attempt("fail", 0.0))
        after = load_ledger(world)
        entry = mql.find_entry(after, "lp_18")
        assert entry["attempts_count"] == 1
        assert entry["latest_outcome"] == "fail"
        assert entry["status"] == "FAILED"  # original fields untouched
        assert entry["baseline"] == 0.0
        assert after["schema"] == before["schema"] == "iteration4-question-ledger/2"

    def test_append_re_reads_under_lock_no_lost_update(self, world):
        mql.append_attempt(world["ledger"], "lp_18", self.attempt("fail"))
        mql.append_attempt(world["ledger"], "lp_18", self.attempt("pass", 1.0))
        entry = mql.find_entry(load_ledger(world), "lp_18")
        assert entry["attempts_count"] == 2
        assert entry["attempts"][0]["outcome"] == "fail"
        assert entry["attempts"][1]["outcome"] == "pass"
        assert entry["latest_outcome"] == "pass"

    def test_concurrent_appends(self, world):
        errors = []

        def worker(i):
            try:
                mql.append_attempt(world["ledger"], "lp_18",
                                   self.attempt("fail", float(i)))
            except Exception as exc:  # pragma: no cover
                errors.append(exc)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert not errors
        entry = mql.find_entry(load_ledger(world), "lp_18")
        assert entry["attempts_count"] == 8
        assert len({a["score"] for a in entry["attempts"]}) == 8

    def test_next_respects_ordering_and_attempts(self, world):
        extra = [
            base_entry(world["root"], qid="aa_1", env_id="lp_1", status="UNTESTED",
                       queue="sequential", store=False, source_run=False),
            base_entry(world["root"], qid="zz_9", env_id="lp_9", status="FAILED",
                       queue="dev5", store=True),
            base_entry(world["root"], qid="mm_5", env_id="lp_5", benchmark="longmemeval_s",
                       status="FAILED", queue="evidence", store=True,
                       source_run=False),
        ]
        ledger = load_ledger(world)
        ledger["entries"].extend(extra)
        with open(world["ledger"], "w", encoding="utf-8") as h:
            json.dump(ledger, h, indent=1)
        picked = mql.next_entry(load_ledger(world))
        assert picked[0]["qid"] == "zz_9"  # FAILED, locomo_plus, dev5 ranks first
        mql.append_attempt(world["ledger"], "zz_9", self.attempt("fail"))
        picked = mql.next_entry(load_ledger(world))
        # benchmark A-Z: locomo_plus < longmemeval_s, so lp_18 precedes mm_5
        assert picked[0]["qid"] == "lp_18"
        mql.append_attempt(world["ledger"], "lp_18", self.attempt("fail"))
        picked = mql.next_entry(load_ledger(world))
        assert picked[0]["qid"] == "mm_5"  # FAILED lme_s evidence
        mql.append_attempt(world["ledger"], "mm_5", self.attempt("fail"))
        picked = mql.next_entry(load_ledger(world))
        assert picked[0]["qid"] == "aa_1"  # UNTESTED ranks after FAILED
        picked = mql.next_entry(load_ledger(world), retry_failed=True)
        assert picked[0]["qid"] == "zz_9"  # earliest failed first
        for qid in ("aa_1",):
            mql.append_attempt(world["ledger"], qid, self.attempt("fail"))
        mql.append_attempt(world["ledger"], "zz_9", self.attempt("pass", 1.0))
        picked = mql.next_entry(load_ledger(world), retry_failed=True)
        assert picked[0]["qid"] == "lp_18"  # passing zz_9 is skipped

    def test_next_prints_and_runs_single(self, world, capsys):
        rc = run_cli(world, extra=["--next", "--benchmark", "locomo_plus"])
        assert rc in (0, 2, 3, 4)  # the attempt actually ran
        out = capsys.readouterr().out
        assert "[next] lp_18" in out


# --------------------------------------------------------------------------
# end-to-end with mocks
# --------------------------------------------------------------------------

class TestEndToEnd:
    def test_type_a_correct_answer_exit_zero(self, world):
        rc = run_cli(world, "lp_18", env={"MOCK_ANSWER": "good answer",
                                          "EVAL_SCORE": "1.0"})
        assert rc == mql.EXIT_OK
        entry = mql.find_entry(load_ledger(world), "lp_18")
        assert entry["attempts_count"] == 1
        attempt = entry["attempts"][0]
        assert attempt["outcome"] == "pass"
        assert attempt["mode"] == "typeA"
        assert attempt["failure_stage"] is None
        assert attempt["score"] == 1.0
        assert attempt["answer_excerpt"] == "good answer"
        assert attempt["recall_ids"] == ["mem_a", "mem_b"]
        assert attempt["tokens"]["total"] == 15
        assert attempt["latency_ms"] == 42

    def test_type_a_incorrect_answer_exit_three_with_diagnostics(self, world, capsys):
        rc = run_cli(world, "lp_18", env={"MOCK_ANSWER": "wrong answer",
                                          "EVAL_SCORE": "0.0"})
        assert rc == mql.EXIT_INCORRECT
        out = capsys.readouterr().out
        assert "DIAGNOSTICS (incorrect answer)" in out
        assert "benchmark: locomo_plus" in out
        assert "qid: lp_18" in out
        assert "wrong answer" in out
        assert "failure_stage: answer" in out
        assert "recall_chars: 123" in out
        assert "mock gold echo from evaluator" in out  # gold via evaluator only
        assert "gold secret" not in out  # never the qs answer field

    def test_eval_error_exit_four(self, world):
        rc = run_cli(world, "lp_18", env={"MOCK_ANSWER": "x", "EVAL_SCORE": "0.0",
                                          "EVAL_FLAG": "eval_error"})
        assert rc == mql.EXIT_FLAGGED
        entry = mql.find_entry(load_ledger(world), "lp_18")
        assert entry["attempts"][0]["outcome"] == "eval_error"
        assert entry["attempts"][0]["failure_stage"] == "judge"

    def test_question_scoped_scoring_contract(self, world):
        run_cli(world, "lp_18", env={"EVAL_SCORE": "1.0"})
        probe = json.load(open(world["probe"], encoding="utf-8"))
        assert probe["qs_files"] == ["qs_lp_18.jsonl"]
        assert len(probe["run_records"]) == 1
        assert probe["run_records"][0]["qid"] == "lp_18"
        assert probe["run_records"][0]["variant"] == "improved"
        assert probe["judge_profile"] == "official-lp-v1"
        assert probe["variant"] == "improved"
        assert probe["models_json"] == world["judge_models"]
        assert probe["bench"] == "locomo_plus"
        assert probe["evaluator_module"].startswith(os.path.abspath(world["scoring_cwd"]))

    def test_manifest_is_gold_free_and_settings_applied(self, world, tmp_path):
        settings_file = str(tmp_path / "over.json")
        with open(settings_file, "w", encoding="utf-8") as h:
            json.dump({"maxRecallEntries": 33, "recallRerank": False}, h)
        run_cli(world, "lp_18", settings=settings_file, env={"EVAL_SCORE": "1.0"})
        attempts = sorted(os.listdir(world["attempts"]))
        manifest = json.load(open(os.path.join(world["attempts"], attempts[0],
                                               "manifest.json"), encoding="utf-8"))
        raw = json.dumps(manifest)
        assert "gold secret" not in raw  # qs gold never reaches the driver
        question = manifest["questions"][0]
        assert set(question) == {"qid", "env_id", "question", "question_protocol",
                                 "scoring_protocol", "question_sha256"}
        assert manifest["settings"]["maxRecallEntries"] == 33
        assert manifest["settings"]["recallRerank"] is False
        assert manifest["settings"]["skipIngest"] is True
        assert manifest["variant"] == "improved"
        assert manifest["model"] == mql.MODEL
        assert len(manifest["envs"]) == 1 and len(manifest["questions"]) == 1
        qs = json.loads(open(world["qs_path"], encoding="utf-8").readline())
        assert question["question_sha256"] == mql.canonical_hash(qs)

    def test_type_a_answers_against_the_copied_store(self, world):
        run_cli(world, "lp_18", env={"EVAL_SCORE": "1.0"})
        baseline_run_root = os.path.normpath(os.path.join(world["store_dir"], "..", ".."))
        assert not os.path.exists(os.path.join(baseline_run_root, "run.jsonl"))
        attempts = sorted(os.listdir(world["attempts"]))
        doc = os.path.join(world["attempts"], attempts[0], "envs", "lp_18",
                           "agent", "memory", "projects", "project_base0001",
                           "harness_state.json")
        assert os.path.isfile(doc)

    def test_type_b_cold_ingest_then_warm_reuse(self, world):
        ledger = load_ledger(world)
        mql.find_entry(ledger, "lp_18")["store_path"] = None
        with open(world["ledger"], "w", encoding="utf-8") as h:
            json.dump(ledger, h, indent=1)
        make_env_file(world["root"])
        rc = run_cli(world, "lp_18", mode="typeB", env={"EVAL_SCORE": "1.0"})
        assert rc == mql.EXIT_OK
        caches = os.listdir(world["cache"])
        assert len(caches) == 1
        cache_dir = os.path.join(world["cache"], caches[0])
        manifest1 = json.load(open(os.path.join(world["attempts"],
                                               sorted(os.listdir(world["attempts"]))[0],
                                               "manifest.json"), encoding="utf-8"))
        assert manifest1["settings"]["skipIngest"] is False
        assert manifest1["output_dir"] == cache_dir
        assert os.path.isfile(os.path.join(cache_dir, "ingested.json"))
        assert os.path.isfile(os.path.join(cache_dir, "env.jsonl"))
        entry = mql.find_entry(load_ledger(world), "lp_18")
        assert entry["attempts"][0]["mode"] == "typeB"
        assert entry["attempts"][0]["mode_detail"] == "ingest"

        rc = run_cli(world, "lp_18", mode="typeB", env={"EVAL_SCORE": "1.0"})
        assert rc == mql.EXIT_OK
        # ledger order, not sorted(dir) names: two runs inside one wallclock
        # second share a stamp and the uuid suffix can invert name order.
        entry = mql.find_entry(load_ledger(world), "lp_18")
        manifest2 = json.load(open(os.path.join(
            world["attempts"], os.path.basename(entry["attempts"][1]["run_dir"]),
            "manifest.json"), encoding="utf-8"))
        assert manifest2["settings"]["skipIngest"] is True
        assert manifest2["output_dir"].startswith(world["attempts"])
        assert manifest2["output_dir"] != cache_dir
        entry = mql.find_entry(load_ledger(world), "lp_18")
        assert entry["attempts"][1]["mode_detail"] == "cache-reuse"
        assert entry["attempts"][1]["cache_key"] == entry["attempts"][0]["cache_key"]
        assert len(os.listdir(world["cache"])) == 1

    def test_type_b_different_import_instructions_new_cache(self, world):
        ledger = load_ledger(world)
        mql.find_entry(ledger, "lp_18")["store_path"] = None
        with open(world["ledger"], "w", encoding="utf-8") as h:
            json.dump(ledger, h, indent=1)
        make_env_file(world["root"])
        run_cli(world, "lp_18", mode="typeB", env={"EVAL_SCORE": "1.0"})
        settings_file = os.path.join(world["root"], "over2.json")
        with open(settings_file, "w", encoding="utf-8") as h:
            json.dump({"importInstructions": "different import instructions"}, h)
        run_cli(world, "lp_18", mode="typeB", settings=settings_file,
                env={"EVAL_SCORE": "1.0"})
        assert len(os.listdir(world["cache"])) == 2

    def test_write_time_override_forces_type_b(self, world):
        settings_file = os.path.join(world["root"], "over3.json")
        with open(settings_file, "w", encoding="utf-8") as h:
            json.dump({"importInstructions": "new import"}, h)
        make_env_file(world["root"])
        rc = run_cli(world, "lp_18", settings=settings_file, env={"EVAL_SCORE": "1.0"})
        assert rc == mql.EXIT_OK
        entry = mql.find_entry(load_ledger(world), "lp_18")
        assert entry["attempts"][0]["mode"] == "typeB"

    def test_type_a_rejects_write_time_overrides(self, world):
        settings_file = os.path.join(world["root"], "over4.json")
        with open(settings_file, "w", encoding="utf-8") as h:
            json.dump({"maxExtractionTokens": 1000}, h)
        rc = run_cli(world, "lp_18", settings=settings_file, mode="typeA")
        assert rc == mql.EXIT_CONTROLLER

    def test_unknown_qid(self, world):
        rc = run_cli(world, "nope_1")
        assert rc == mql.EXIT_CONTROLLER

    def test_retry_appends_second_attempt(self, world):
        run_cli(world, "lp_18", env={"EVAL_SCORE": "0.0", "MOCK_ANSWER": "bad"})
        settings_file = os.path.join(world["root"], "retry.json")
        with open(settings_file, "w", encoding="utf-8") as h:
            json.dump({"maxRecallEntries": 9}, h)
        rc = run_cli(world, "lp_18", settings=settings_file,
                     extra=["--retry"], env={"EVAL_SCORE": "1.0"})
        assert rc == mql.EXIT_OK
        entry = mql.find_entry(load_ledger(world), "lp_18")
        assert entry["attempts_count"] == 2
        assert entry["attempts"][0]["outcome"] == "fail"
        assert entry["attempts"][1]["outcome"] == "pass"
        assert entry["attempts"][1]["settings"]["maxRecallEntries"] == 9

    def test_locomo_local_f1_family_uses_no_judge_config(self, world):
        # locomo entry: local-f1 judge label, deterministic scoring, no network
        ledger = load_ledger(world)
        entry = mql.find_entry(ledger, "lp_18")
        entry["benchmark"] = "locomo"
        entry["judge_profile"] = "local-f1 (optimus-local-scoring/1.0.0, no LLM judge)"
        with open(world["ledger"], "w", encoding="utf-8") as h:
            json.dump(ledger, h, indent=1)
        rc = run_cli(world, "lp_18", env={"EVAL_SCORE": "1.0"})
        assert rc == mql.EXIT_OK
        probe = json.load(open(world["probe"], encoding="utf-8"))
        assert probe["bench"] == "locomo"
        assert probe["judge_profile"] == "default"  # no --judge-profile flag
        assert probe["models_json"] is None  # no judge models.json either
        attempt = mql.find_entry(load_ledger(world), "lp_18")["attempts"][0]
        assert attempt["judge_profile"] == "local-f1"

    def test_multi_record_qs_file_selects_single_record(self, world):
        other = {"qid": "other_q", "env_id": "lp_18", "question": "other",
                 "answer": "other gold", "evidence": [], "category": "c",
                 "eval": {"type": "llm_judge", "params": {}, "protocol": "p"},
                 "family": "locomo_plus", "question_protocol": "x"}
        with open(world["qs_path"], "a", encoding="utf-8") as h:
            h.write(json.dumps(other) + "\n")
        rc = run_cli(world, "lp_18", env={"EVAL_SCORE": "1.0"})
        assert rc == mql.EXIT_OK
        probe = json.load(open(world["probe"], encoding="utf-8"))
        assert probe["qs_files"] == ["qs_lp_18.jsonl"]
        scratch_qs = os.path.join(world["attempts"],
                                  sorted(os.listdir(world["attempts"]))[0],
                                  "scoring", "qs-dir", "qs_lp_18.jsonl")
        rows = [json.loads(l) for l in open(scratch_qs, encoding="utf-8") if l.strip()]
        assert len(rows) == 1 and rows[0]["qid"] == "lp_18"


# --------------------------------------------------------------------------
# legacy longmemeval annotation (raw common-set rows)
# --------------------------------------------------------------------------

class TestLegacyProtocolAnnotations:
    RAW_LME_S_ROW = {
        "qid": "00ca467f", "env_id": "00ca467f",
        "question": "How many doctor's appointments did I go to in March?",
        "answer": "2",
        "evidence": ["answer_39900a0a_3", "answer_39900a0a_2", "answer_39900a0a_1"],
        "category": "multi-session",
        "eval": {"type": "llm_judge", "params": {
            "prompt_variant": "multi-session", "abstention": False,
            "judge_model": "glm-5.3",
            "protocol": "official longmemeval evaluate_qa.py yes/no prompts"}},
        "question_date": "2023/03/27 (Mon) 23:35", "abstention": False,
    }
    # canonical_hash of the converter-rebuilt annotated record, equal to the
    # lme_s_full_smoke5_v1 manifest's question_sha256 for this qid.
    ANNOTATED_SHA256 = ("4ee9c7b3b277883ceb76f6331352f5f453f73024591e3780db"
                        "07035cd81ba82e")

    def test_raw_longmemeval_row_annotated_like_the_converter(self):
        annotated = mql.ensure_protocol_annotations(
            json.loads(json.dumps(self.RAW_LME_S_ROW)), "longmemeval_s")
        assert annotated["family"] == "longmemeval"
        assert annotated["question_protocol"] == "optimus-longmemeval-adapter/2.0.0"
        assert annotated["eval"]["protocol"] == "optimus-longmemeval-dgx-port/2.0.0"
        assert annotated["eval"]["params"]["source"] == (
            "MIT LongMemEval templates; local DGX adapter")
        assert "protocol" not in annotated["eval"]["params"]
        # gold-free declaration now builds and binds the baseline hash
        declaration = mql.question_declaration(annotated)
        assert declaration["question_sha256"] == self.ANNOTATED_SHA256
        assert "answer" not in declaration

    def test_already_annotated_record_passes_through(self):
        record = {"qid": "q", "env_id": "q", "question": "?", "answer": "a",
                  "evidence": [], "category": "c",
                  "eval": {"type": "llm_judge", "params": {}, "protocol": "p"},
                  "family": "longmemeval", "question_protocol": "x"}
        assert mql.ensure_protocol_annotations(record, "longmemeval_s") is record

    def test_unknown_legacy_family_passes_through(self):
        record = {"qid": "q", "env_id": "q", "question": "?", "answer": "a",
                  "evidence": [], "category": "c", "eval": {"type": "f1"}}
        assert mql.ensure_protocol_annotations(record, "locomo") is record

    def test_raw_row_declaration_matches_baseline_manifest(self, world):
        # a raw longmemeval_s qs row flows through the declaration builder
        # and reproduces the baseline manifest's question_sha256.
        with open(world["qs_path"], "w", encoding="utf-8") as h:
            h.write(json.dumps(self.RAW_LME_S_ROW) + "\n")
        record = mql.load_question_record(world["qs_path"], "00ca467f")
        annotated = mql.ensure_protocol_annotations(record, "longmemeval_s")
        declaration = mql.question_declaration(annotated)
        assert declaration["question_sha256"] == self.ANNOTATED_SHA256
        assert declaration["qid"] == "00ca467f"
