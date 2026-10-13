#!/usr/bin/env python3
"""Round-4 question-by-question benchmark controller (memory_bench).

Runs ONE ledger question end-to-end and scores it immediately:

  1. select a question by qid from the round-4 QUESTION-LEDGER
     (schema iteration4-question-ledger/2; concurrent-safe reads,
      atomic temp+replace appends under an exclusive sidecar lock);
  2. prepare the question's isolated memory environment:
       - TYPE A (read-time change): copy the frozen baseline env store
         (envs/<env_id>/agent, memory included) into a scratch output dir,
         rebind projects.json to the scratch workspace, then emit a
         single-env single-question manifest with skipIngest=true and the
         candidate read-time settings; the driver answers against the
         working copy and the baseline store is never touched;
       - TYPE B (write-time change): content-addressed cache dir keyed by
         sha256(source_rev, env-id, importInstructions, extraction
         settings, model); a cold cache ingests once (manifest WITHOUT
         skipIngest, driver output_dir = cache dir), a warm cache is
         copied out per attempt (type-A reuse, no re-ingest);
  3. invoke the driver: memory_bench <manifest> with
     MEMORY_BENCH_MODELS_JSON (DGX GLM-5.3 only), capture driver.log;
  4. score QUESTION-SCOPED with the official bench evaluator (bench venv,
     campaign-root scoring cwd): a scratch qs-dir holding ONLY the target
     question's single qs record and a 1-record run.jsonl extract.
     A partial run is NEVER scored against a full qs-dir (absent
     questions would count as flagged zeros);
  5. append the attempt to the ledger (qid, score, answer excerpt,
     recall ids, tokens, latency, failure stage, run dir);
  6. exit 0 on a correct answer, 3 on an incorrect answer (with a
     DIAGNOSTICS block), 4 on a judge/eval flag, 2 on controller errors.

Gold answers are never fed to the answer path: the manifest question is a
gold-free declaration (qid/env_id/question/protocols/sha256 only) and the
DIAGNOSTICS block reports gold only through evaluator output.

The evaluator, judge profiles and scoring semantics are the existing
official ones; this controller only orchestrates them.

Exit codes: 0 correct | 2 controller/driver error | 3 incorrect |
4 flagged (eval_error / driver_error / empty_answer / missing_record).
"""

from __future__ import annotations

import argparse
import datetime as _dt
import glob
import hashlib
import json
import msvcrt
import os
import re
import shutil
import subprocess
import sys
import time
import uuid

CONTROLLER_NAME = "scripts/memory_question_loop.py"

# --- campaign defaults (overridable via CLI) -------------------------------

DEFAULT_LEDGER = ("C:/Users/openclawuser/optimus-memory-bench-20261007-040424/"
                 "reports/continuation/iteration4/QUESTION-LEDGER.json")
DEFAULT_MODELS_JSON = ("C:/Users/openclawuser/optimus-memory-bench-20261007-040424/"
                       "reports/continuation/c06-instruction-boundary/inputs/runs/"
                       "locomo-c06-ib-system_append-q12-s0p1/envs/conv-30/agent/models.json")
DEFAULT_JUDGE_MODELS_JSON = ("C:/Users/openclawuser/optimus-memory-bench-20261007-040424/"
                             "reports/continuation/iteration2-inputs/lme-temporal-flip/"
                             "judge-models.json")
DEFAULT_BENCH_VENV_PYTHON = ("C:/Users/openclawuser/optimus-memory-bench-20261007-040424/"
                             "repo-continuation/bench/.venv/Scripts/python.exe")
DEFAULT_SCORING_CWD = "C:/Users/openclawuser/optimus-memory-bench-20261007-040424"
DEFAULT_DRIVER = ("C:/Users/openclawuser/optimus-memory-bench-20261007-040424/"
                  "imp4-targetA/release/examples/memory_bench.exe")
DEFAULT_DRIVER_WORKDIR = "C:/Users/openclawuser/optimus-memory-bench-20261007-040424/imp4"
DEFAULT_MANIFEST_DIRS = [
    "C:/Users/openclawuser/optimus-memory-bench-20261007-040424/reports/continuation/iteration3-inputs/manifests",
    "C:/Users/openclawuser/optimus-memory-bench-20261007-040424/reports/continuation/iteration4-inputs/manifests",
]

MODEL = {"provider": "dgx-glm53", "id": "glm-5.3"}  # DGX GLM-5.3 only (user-mandated)

# ledger ordering (replicates the ledger "ordering" note)
STATUS_RANK = {"FAILED": 0, "EVAL_ERROR": 1, "RESOLVED": 2, "UNTESTED": 3}
QUEUE_RANK = {"dev5": 0, "regression": 1, "evidence": 2, "sequential": 3}

# per-benchmark evaluator/judge configuration (from the ledger rescoring block)
FAMILY_CONFIG = {
    "locomo":         {"bench_flag": "locomo",     "judge_profile": "local-f1",       "models_json": False},
    "locomo_plus":    {"bench_flag": "locomo_plus","judge_profile": "official-lp-v1",  "models_json": True},
    "longmemeval_s":  {"bench_flag": "longmemeval","judge_profile": "official-lme-v1", "models_json": True},
    "longmemeval_m":  {"bench_flag": "longmemeval","judge_profile": "official-lme-v1", "models_json": True},
    "lme_v2":         {"bench_flag": "lme_v2",    "judge_profile": "default",         "models_json": True},
}

# Legacy longmemeval common-set rows predate the 2.0.0 protocol annotations:
# raw rows carry eval.params.protocol ("official longmemeval evaluate_qa.py
# yes/no prompts") instead of the annotated family/question_protocol/
# eval.protocol triple the driver declaration requires. Regenerate the
# annotations deterministically, exactly like the converter
# (bench.converters.longmemeval.build_question + annotate_question), so the
# declaration's question_sha256 equals the baseline manifests (verified
# byte-identical against lme_s_full_smoke5_v1's question entries).
LEGACY_PROTOCOL_ANNOTATIONS = {
    "longmemeval": {
        "family": "longmemeval",
        "question_protocol": "optimus-longmemeval-adapter/2.0.0",
        "scoring_protocol": "optimus-longmemeval-dgx-port/2.0.0",
        "params_source": "MIT LongMemEval templates; local DGX adapter",
    },
}


def ensure_protocol_annotations(record: dict, benchmark: str) -> dict:
    """Annotate legacy-format scoring questions in memory (never on disk).

    Already-annotated records pass through unchanged; unknown legacy shapes
    also pass through so the strict declaration contract stays visible.
    """
    if record.get("question_protocol") and (record.get("eval") or {}).get("protocol"):
        return record
    spec = LEGACY_PROTOCOL_ANNOTATIONS.get(FAMILY_CONFIG[benchmark]["bench_flag"])
    if spec is None:
        return record
    annotated = dict(record)
    eval_block = dict(record.get("eval") or {})
    params = {key: value for key, value in (eval_block.get("params") or {}).items()
              if key != "protocol"}
    params["source"] = spec["params_source"]
    eval_block["params"] = params
    eval_block["protocol"] = spec["scoring_protocol"]
    annotated["eval"] = eval_block
    annotated["family"] = spec["family"]
    annotated["question_protocol"] = spec["question_protocol"]
    return annotated


def normalize_judge_profile(value: str) -> str:
    """Ledger entries carry descriptive judge labels; map them to the
    evaluator's --judge-profile choices (default/official-lme-v1/official-lp-v1
    or the locomo local-f1 marker which needs no judge config)."""
    value = (value or "default").strip()
    for token in ("official-lme-v1", "official-lp-v1", "local-f1"):
        if token in value:
            return token
    return "default"


# manifest settings that change the INGESTED STORE (type-B cache-key inputs)
INGEST_AFFECTING_KEYS = (
    "importInstructions", "maxExtractionTokens", "maxImportChunkChars",
    "recallProspectiveIndex", "recallSourceSpans", "lme2ProcedureNotes",
    "captureAxtreeMaxChars", "evidenceTimestamps", "memoryLearning",
)
# settings that only affect the read/answer path (never in the type-B key)
READ_AFFECTING_KEYS = (
    "recallOn", "recallQueryDistillation", "recallRerank", "recallRerankCandidates",
    "recallRerankWithContent", "recallRerankContentChars",
    "recallQueryExtract", "recallQueryExpansion", "recallQueryExpansionDraws",
    "maxRecallChars", "maxRecallEntries", "answerInstruction", "answerMaxTokens",
    "answerThinkingLevel", "questionTimeoutS", "disableTools", "autoRefine",
)

EXIT_OK, EXIT_CONTROLLER, EXIT_INCORRECT, EXIT_FLAGGED = 0, 2, 3, 4

ATTEMPT_SCHEMA = "iteration4-question-attempt/1"


class ControllerError(Exception):
    """Fatal controller problem (exit 2)."""


# --- small utilities --------------------------------------------------------

def now_iso() -> str:
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S+00:00")


def canonical_hash(value) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True,
                         separators=(",", ":"), allow_nan=False).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def atomic_write_json(path: str, payload) -> None:
    path = os.path.abspath(path)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = f"{path}.tmp-{os.getpid()}-{uuid.uuid4().hex[:8]}"
    with open(tmp, "w", encoding="utf-8", newline="\n") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=1)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(tmp, path)


def read_json(path: str):
    with open(path, "r", encoding="utf-8") as handle:
        return json.load(handle)


def read_jsonl(path: str):
    rows = []
    with open(path, "r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def write_jsonl(path: str, rows) -> None:
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def windows_alias(path: str) -> str:
    """The projects.json alias key the Rust driver derives from a cwd.

    project_identity() canonicalizes the workspace and prefixes "path:";
    on Windows canonicalize yields the \\\\?\\ extended form, so the alias is
    "path:" + \\\\?\\ + realpath (true on-disk case).
    """
    real = os.path.realpath(os.path.abspath(path))
    return "path:" + "\\\\?\\" + real


class LedgerLock:
    """Cross-process exclusive lock (msvcrt.locking on Windows)."""

    def __init__(self, path: str, timeout_s: float = 120.0):
        self.path = path + ".lock"
        self.timeout_s = timeout_s
        self._fd = None

    def __enter__(self):
        os.makedirs(os.path.dirname(os.path.abspath(self.path)), exist_ok=True)
        self._fd = os.open(self.path, os.O_RDWR | os.O_CREAT)
        deadline = time.monotonic() + self.timeout_s
        while True:
            try:
                msvcrt.locking(self._fd, msvcrt.LK_NBLCK, 1)
                return self
            except OSError:
                if time.monotonic() > deadline:
                    os.close(self._fd)
                    self._fd = None
                    raise ControllerError(f"ledger lock timeout: {self.path}")
                time.sleep(0.05)

    def __exit__(self, *exc):
        if self._fd is not None:
            try:
                os.lseek(self._fd, 0, os.SEEK_SET)
                msvcrt.locking(self._fd, msvcrt.LK_UNLCK, 1)
            finally:
                os.close(self._fd)
                self._fd = None
        return False


# --- ledger ----------------------------------------------------------------

def load_ledger(path: str) -> dict:
    """Concurrent-safe read (writers use temp+os.replace, so a reader
    always observes one complete generation)."""
    try:
        return read_json(path)
    except FileNotFoundError as exc:
        raise ControllerError(f"ledger not found: {path}") from exc
    except ValueError as exc:
        raise ControllerError(f"ledger is not valid JSON: {path}: {exc}") from exc


def find_entry(ledger: dict, qid: str):
    for entry in ledger.get("entries", []):
        if entry.get("qid") == qid:
            return entry
    return None


def sorted_entries(ledger: dict):
    """Canonical ledger order: (status_rank, benchmark, queue_rank, qid)."""
    def key(entry):
        return (
            STATUS_RANK.get(entry.get("status"), 99),
            entry.get("benchmark", ""),
            QUEUE_RANK.get(entry.get("queue"), 99),
            entry.get("qid", ""),
        )
    return sorted(ledger.get("entries", []), key=key)


def next_entry(ledger: dict, *, benchmark=None, queue=None, status=None,
               retry_failed=False, include_attempted=False, limit=1):
    """Resume selection: entries without attempts first (ledger order);
    when every entry has attempts, the earliest non-passing attempt."""
    picked = []
    benchmarks = set(benchmark) if benchmark else None
    for entry in sorted_entries(ledger):
        if benchmarks and entry.get("benchmark") not in benchmarks:
            continue
        if queue and entry.get("queue") != queue:
            continue
        if status and entry.get("status") != status:
            continue
        attempts = entry.get("attempts") or []
        if attempts and not include_attempted:
            if not retry_failed:
                continue
            latest = attempts[-1].get("outcome")
            if latest == "pass":
                continue
        picked.append(entry)
        if len(picked) >= limit:
            break
    return picked


def append_attempt(ledger_path: str, qid: str, attempt: dict) -> dict:
    """Append one attempt to the entry, atomically.

    The ledger is ALWAYS re-read under the exclusive lock (a stale
    in-memory copy never clobbers concurrent writers), the entry gains
    additive fields only (attempts/attempts_count/latest_outcome/...);
    the original status field and every other original field are left
    untouched so other workers' ordering stays stable.
    """
    with LedgerLock(ledger_path):
        ledger = load_ledger(ledger_path)
        entry = find_entry(ledger, qid)
        if entry is None:
            raise ControllerError(f"qid {qid} disappeared from the ledger")
        entry.setdefault("attempts", []).append(attempt)
        entry["attempts_count"] = len(entry["attempts"])
        entry["latest_outcome"] = attempt.get("outcome")
        entry["latest_score"] = attempt.get("score")
        entry["latest_attempt_ts"] = attempt.get("ts")
        counts = {}
        for item in entry["attempts"]:
            counts[item.get("outcome")] = counts.get(item.get("outcome"), 0) + 1
        entry["attempt_outcomes"] = counts
        atomic_write_json(ledger_path, ledger)
        return ledger


# --- question / env sources --------------------------------------------------

def load_question_record(qs_path: str, qid: str) -> dict:
    """The single scoring-question record for qid (gold stays inside it)."""
    rows = read_jsonl(qs_path)
    hits = [row for row in rows if row.get("qid") == qid]
    if len(hits) != 1:
        raise ControllerError(
            f"expected exactly 1 record for qid {qid} in {qs_path}, found {len(hits)}")
    return hits[0]


def question_declaration(question: dict) -> dict:
    """Gold-free driver question (mirrors bench.evidence.prepare_question_declaration)."""
    visible = question.get("presented_question", question.get("question"))
    if not isinstance(visible, str) or not visible.strip():
        raise ControllerError("question requires explicit presented text")
    eval_block = question.get("eval") or {}
    protocol = eval_block.get("protocol")
    if not protocol:
        raise ControllerError("question record is missing eval.protocol")
    return {
        "qid": question["qid"],
        "env_id": question["env_id"],
        "question": visible,
        "question_protocol": question["question_protocol"],
        "scoring_protocol": protocol,
        "question_sha256": canonical_hash(question),
    }


def discover_source_manifest(entry: dict, manifest_dirs) -> str | None:
    """Locate the baseline run manifest that produced entry.source_run."""
    source_run = entry.get("source_run")
    if not source_run:
        return None
    run_id = os.path.basename(os.path.dirname(source_run.rstrip("/\\")))
    for directory in manifest_dirs:
        candidate = os.path.join(directory, f"{run_id}.json")
        if os.path.isfile(candidate):
            return candidate
        for hit in sorted(glob.glob(os.path.join(directory, f"*{run_id}*.json"))):
            return hit
    return None


def load_env_spec(entry: dict, env_file: str | None, env_from_manifest: str | None,
                  manifest_dirs, source_manifest: str | None):
    """Resolve the EnvSpec (sessions) for a type-B (re-)ingest.

    Priority: --env-file > --env-from-manifest > qs-dir env_<env_id>.json >
    source-manifest envs[].
    """
    env_id = entry["env_id"]
    candidates = []
    if env_file:
        candidates.append(("env-file", env_file))
    if env_from_manifest:
        candidates.append(("env-from-manifest", env_from_manifest))
    qs_dir = os.path.dirname(entry["qs_path"])
    candidates.append(("qs-dir", os.path.join(qs_dir, f"env_{env_id}.json")))
    if source_manifest:
        candidates.append(("source-manifest", source_manifest))
    for label, path in candidates:
        if not path or not os.path.isfile(path):
            continue
        payload = read_json(path)
        envs = payload.get("envs") if isinstance(payload, dict) and "envs" in payload else (
            payload if isinstance(payload, list) else [payload])
        for env in envs:
            if isinstance(env, dict) and env.get("env_id") == env_id:
                return label, path, env
    raise ControllerError(
        f"no env spec (sessions) found for env {env_id}; pass --env-file or "
        "--env-from-manifest (needed for type-B ingestion)")


# --- type A: copy the frozen store ------------------------------------------

SKIP_COPY_SUFFIXES = (".lock", ".lock.guard")


def _copy_tree_filtered(src: str, dst: str) -> int:
    copied = 0
    for root, _dirs, files in os.walk(src):
        rel = os.path.relpath(root, src)
        target_root = os.path.join(dst, rel) if rel != "." else dst
        os.makedirs(target_root, exist_ok=True)
        for name in files:
            if name.endswith(SKIP_COPY_SUFFIXES):
                continue  # transient lock markers; the driver recreates them
            shutil.copy2(os.path.join(root, name), os.path.join(target_root, name))
            copied += 1
    return copied


def store_project_id(store_env_dir: str) -> str:
    """The project id of the single-project baseline store.

    Derived from projects.json aliases and/or the project directories that
    hold a harness_state.json document; exactly one must exist."""
    ids = set()
    projects_json = os.path.join(store_env_dir, "agent", "memory", "projects.json")
    if os.path.isfile(projects_json):
        registry = read_json(projects_json)
        if isinstance(registry, dict):
            ids.update(v for v in registry.values() if isinstance(v, str))
    projects_dir = os.path.join(store_env_dir, "agent", "memory", "projects")
    if os.path.isdir(projects_dir):
        for name in os.listdir(projects_dir):
            if (name.startswith("project_")
                    and os.path.isfile(os.path.join(projects_dir, name, "harness_state.json"))):
                ids.add(name)
    if len(ids) != 1:
        raise ControllerError(
            f"baseline store must hold exactly one project, found {sorted(ids)}: {store_env_dir}")
    return ids.pop()


def copy_store_into_scratch(store_env_dir: str, scratch_output_dir: str, env_id: str) -> dict:
    """Copy envs/<env_id>/agent (memory inside) into the scratch output dir and
    rebind projects.json to the scratch workspace.

    The driver resolves the memory project from the session cwd via the
    projects.json path alias; without the rebind a copied store silently
    resolves to a NEW empty project. The baseline store is never written.
    """
    src_agent = os.path.join(store_env_dir, "agent")
    src_memory = os.path.join(src_agent, "memory")
    if not os.path.isdir(src_memory):
        raise ControllerError(
            f"type-A store has no memory dir (not ingested?): {store_env_dir}")
    project_id = store_project_id(store_env_dir)

    dst_env = os.path.join(scratch_output_dir, "envs", env_id)
    dst_agent = os.path.join(dst_env, "agent")
    if os.path.isdir(dst_agent):
        raise ControllerError(f"scratch env already exists: {dst_agent}")
    copied = _copy_tree_filtered(src_agent, dst_agent)

    workspace = os.path.join(dst_env, "workspace")
    os.makedirs(workspace, exist_ok=True)
    alias = windows_alias(workspace)
    projects_json = os.path.join(dst_agent, "memory", "projects.json")
    registry = read_json(projects_json) if os.path.isfile(projects_json) else {}
    registry[alias] = project_id
    atomic_write_json(projects_json, registry)
    return {"copied_files": copied, "project_id": project_id, "alias": alias,
            "source": store_env_dir}


# --- type B: content-addressed ingest cache ---------------------------------

def typeb_cache_key(env_spec: dict, settings: dict, model: dict) -> str:
    """sha256(source_rev, env-id, importInstructions, extraction settings, model).

    Read-time settings are deliberately excluded: a warm cache is reused
    by every compatible read-time experiment (copy-out, skipIngest=true).
    """
    extraction = {key: settings.get(key) for key in INGEST_AFFECTING_KEYS}
    payload = {
        "source_rev": canonical_hash(env_spec),
        "env_id": env_spec.get("env_id"),
        "importInstructions": settings.get("importInstructions"),
        "extraction_settings": extraction,
        "model": model,
    }
    return canonical_hash(payload)


def cache_state(cache_dir: str, env_id: str) -> dict:
    """A cache entry is warm when the driver recorded the env ingest and the
    store document exists."""
    env_jsonl = os.path.join(cache_dir, "env.jsonl")
    ingested = False
    if os.path.isfile(env_jsonl):
        for row in read_jsonl(env_jsonl):
            if row.get("env_id") == env_id and not row.get("error"):
                ingested = True
    document = os.path.join(cache_dir, "envs", env_id, "agent", "memory",
                             "projects")
    has_store = os.path.isdir(document) and any(
        os.path.isfile(os.path.join(document, name, "harness_state.json"))
        for name in os.listdir(document)) if os.path.isdir(document) else False
    return {"cache_dir": cache_dir, "ingested": ingested, "has_store": has_store,
            "warm": ingested and has_store}


def quarantine_cache(cache_dir: str) -> str:
    """Move a cold/partial cache dir aside so the fresh-output capture gate
    passes on the next ingest attempt."""
    if not os.path.isdir(cache_dir):
        return cache_dir
    stale = f"{cache_dir}.failed-{time.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:6]}"
    os.rename(cache_dir, stale)
    return stale


# --- settings ---------------------------------------------------------------

def resolve_base_settings(entry: dict, manifest_dirs, base_settings_file: str | None) -> dict:
    if base_settings_file:
        return read_json(base_settings_file)
    source_manifest = discover_source_manifest(entry, manifest_dirs)
    if source_manifest:
        settings = read_json(source_manifest).get("settings") or {}
        settings["__source_manifest__"] = source_manifest
        return settings
    raise ControllerError(
        "no baseline settings: pass --base-settings (no source manifest found for "
        f"{entry.get('source_run')})")


def merge_settings(base: dict, overrides: dict | None) -> dict:
    settings = dict(base)
    settings.pop("__source_manifest__", None)
    if overrides:
        unknown = [key for key in overrides
                   if key not in INGEST_AFFECTING_KEYS and key not in READ_AFFECTING_KEYS
                   and key not in ("skipIngest", "importRunRetries", "ingestTimeoutS",
                                   "importRunTimeoutS", "answerInstructionPlacement",
                                   "seed")]
        if unknown:
            raise ControllerError(f"unknown manifest settings keys: {sorted(unknown)}")
        settings.update(overrides)
    return settings


def settings_changed_keys(base: dict, overrides: dict | None):
    """Which override keys are write-time (type B) vs read-time (type A)."""
    if not overrides:
        return set(), set()
    write_keys = {k for k in overrides if k in INGEST_AFFECTING_KEYS}
    read_keys = {k for k in overrides if k in READ_AFFECTING_KEYS}
    return write_keys, read_keys


# --- manifest + driver -------------------------------------------------------

def build_manifest(run_id: str, benchmark: str, output_dir: str, env_spec: dict,
                   question: dict, settings: dict, capture_protocol=None,
                   notes: dict | None = None) -> dict:
    manifest = {
        "run_id": run_id,
        "benchmark": benchmark,
        "variant": "improved",
        "output_dir": output_dir,
        "envs": [{
            "env_id": env_spec["env_id"],
            "sessions": env_spec.get("sessions", []),
            "ground_truth_refs": env_spec.get("ground_truth_refs", []),
        }],
        "questions": [question],
        "model": dict(MODEL),
        "settings": settings,
    }
    if env_spec.get("family"):
        manifest["envs"][0]["family"] = env_spec["family"]
    # Carry regenerated-env capture metadata into the driver manifest: the
    # driver validates notes.axtree_cap_chars (captureAxtreeMaxChars) and the
    # authority_revision/capture_protocol pair against these fields.
    for metadata_key in ("authority_revision", "capture_protocol"):
        if env_spec.get(metadata_key) is not None:
            manifest["envs"][0][metadata_key] = env_spec[metadata_key]
    if isinstance(env_spec.get("notes"), dict) and env_spec["notes"]:
        manifest["envs"][0]["notes"] = env_spec["notes"]
    if notes is not None:
        manifest["envs"][0]["notes"] = notes
    if capture_protocol:
        manifest["captureProtocol"] = capture_protocol
    return manifest


def run_driver(driver: str, manifest_path: str, workdir: str, models_json: str,
               log_path: str, extra_env: dict | None = None) -> dict:
    """memory_bench.exe <manifest> with MEMORY_BENCH_MODELS_JSON."""
    if not os.path.isfile(driver):
        raise ControllerError(f"driver binary not found: {driver}")
    if driver.endswith(".py"):
        cmd = [sys.executable, driver, manifest_path]
    else:
        cmd = [driver, manifest_path]
    env = dict(os.environ)
    env["MEMORY_BENCH_MODELS_JSON"] = models_json
    if extra_env:
        env.update(extra_env)
    os.makedirs(os.path.dirname(os.path.abspath(log_path)), exist_ok=True)
    with open(log_path, "w", encoding="utf-8", newline="\n") as log:
        proc = subprocess.run(cmd, cwd=workdir, env=env, stdout=log,
                              stderr=subprocess.STDOUT)
    return {"exit_code": proc.returncode, "cmd": cmd}


def extract_question_records(run_jsonl: str, qid: str, variant: str = "improved"):
    rows = [row for row in read_jsonl(run_jsonl)
            if row.get("qid") == qid and row.get("variant") == variant]
    if not rows:
        return []
    return [rows[-1]]  # last record wins (driver resume semantics)


# --- scoring -----------------------------------------------------------------

def build_scoring_inputs(attempt_dir: str, question: dict, run_jsonl: str,
                         qid: str) -> tuple:
    """Scratch qs-dir with ONLY the target record + 1-record run extract.

    The evaluator scores EVERY qs file in --qs-dir and flags missing
    records as zeros, so a partial run must never face a full qs-dir.
    """
    scoring = os.path.join(attempt_dir, "scoring")
    qs_dir = os.path.join(scoring, "qs-dir")
    os.makedirs(qs_dir, exist_ok=True)
    with open(os.path.join(qs_dir, f"qs_{qid}.jsonl"), "w", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(question, ensure_ascii=False) + "\n")
    run_one = os.path.join(scoring, "run.jsonl")
    records = extract_question_records(run_jsonl, qid)
    write_jsonl(run_one, records)
    return qs_dir, run_one, records


def run_evaluator(venv_python: str, scoring_cwd: str, bench_flag: str, run_one: str,
                  qs_dir: str, out_json: str, judge_profile: str,
                  models_json: str | None, log_path: str, extra_env: dict | None = None) -> dict:
    if not os.path.isfile(venv_python):
        raise ControllerError(f"bench venv python not found: {venv_python}")
    cmd = [venv_python, "-m", "bench.eval.evaluate",
           "--bench", bench_flag, "--run", run_one, "--variant", "improved",
           "--qs-dir", qs_dir, "--out", out_json]
    if models_json:
        cmd += ["--models-json", models_json]
    # Only the evaluator's actual profiles are valid --judge-profile values;
    # "local-f1" is a marker for deterministic scoring with no judge flag.
    if judge_profile in ("official-lme-v1", "official-lp-v1"):
        cmd += ["--judge-profile", judge_profile]
    env = dict(os.environ)
    env.pop("MEMORY_BENCH_MODELS_JSON", None)  # judge config must be explicit
    if extra_env:
        env.update(extra_env)
    os.makedirs(os.path.dirname(os.path.abspath(log_path)), exist_ok=True)
    with open(log_path, "w", encoding="utf-8", newline="\n") as log:
        proc = subprocess.run(cmd, cwd=scoring_cwd, env=env, stdout=log,
                              stderr=subprocess.STDOUT)
    if proc.returncode != 0 or not os.path.isfile(out_json):
        raise ControllerError(
            f"evaluator failed (exit {proc.returncode}); see {log_path}")
    return read_json(out_json)


def single_result(scores: dict, qid: str) -> dict:
    for row in scores.get("results", []):
        if row.get("qid") == qid:
            return row
    raise ControllerError(f"scores.json has no result for {qid}")


# --- telemetry / diagnostics --------------------------------------------------

def helper_observations(record: dict) -> list:
    realization = record.get("memory_feature_realization") or {}
    out = []
    for name, observations in (realization.get("llm_helper_observations") or {}).items():
        last = observations[-1] if observations else {}
        out.append({
            "helper": name,
            "attempted": last.get("attempted"),
            "outcome": last.get("outcome"),
            "latency_ms": last.get("latencyMs"),
            "usage": last.get("usage"),
        })
    return out


def failure_stage(record: dict | None, result: dict | None, driver_rc: int,
                 mode: str, ingest_ok: bool | None) -> str | None:
    if not ingest_ok and mode == "typeB":
        return "ingest"
    if record is None or record.get("error") or not (record.get("answer_text") or "").strip():
        return "driver"
    if result is None:
        return "scoring"
    if result.get("flag"):
        return "judge"
    if float(result.get("score", 0.0)) < 0.5:
        return "answer"
    return None


def print_diagnostics(entry: dict, question: dict, record: dict, result: dict,
                      telemetry: dict, attempt: dict) -> None:
    qid = entry["qid"]
    gold = None
    for key in ("judge_reason", "judge_raw", "details", "note"):
        value = result.get(key)
        if isinstance(value, str) and value.strip():
            gold = value
            break
    lines = [
        "=== DIAGNOSTICS (incorrect answer) ===",
        f"benchmark: {entry.get('benchmark')}",
        f"qid: {qid}",
        f"category: {entry.get('category')}",
        f"score: {result.get('score')} (flag={result.get('flag')}, "
        f"judge={result.get('judge') or result.get('eval_type')})",
        f"gold (evaluator output only): "
        + ((gold[:300] + "..." if len(gold or "") > 300 else gold)
           if gold else "(evaluator did not expose gold)"),
        f"answer ({len(record.get('answer_text') or '')} chars): "
        + repr((record.get("answer_text") or "")[:300]),
        f"recall_ids ({len(record.get('recall_ids') or [])}): "
        f"{(record.get('recall_ids') or [])[:12]}",
        f"recall_chars: {record.get('recall_chars')}",
        f"direct_recall_ids: {len(record.get('direct_recall_ids') or [])}",
        f"tokens: {record.get('usage')}",
        f"answer latency_ms: {record.get('latency_ms')} "
        f"(stop_reason={record.get('stop_reason')}, "
        f"blank_rescue={(record.get('answer_blank_rescue') or {}).get('outcome')})",
        f"judge latency_ms: {result.get('judge_latency_ms')}",
    ]
    for obs in telemetry.get("helpers", []):
        lines.append(f"helper {obs['helper']}: attempted={obs['attempted']} "
                     f"outcome={obs['outcome']} latency_ms={obs['latency_ms']} "
                     f"usage={obs['usage']}")
    lines += [
        f"ingest: {telemetry.get('ingest')}",
        f"failure_stage: {attempt.get('failure_stage')}",
        f"run_dir: {attempt.get('run_dir')}",
        f"manifest: {attempt.get('manifest')}",
        f"driver.log: {attempt.get('run_dir')}/driver.log",
        "=== END DIAGNOSTICS ===",
    ]
    print("\n".join(lines), flush=True)


# --- orchestration ------------------------------------------------------------

def prepare_typea(entry: dict, attempt_dir: str, settings: dict, run_id: str,
                  question: dict, benchmark: str, family: str | None = None) -> dict:
    store_env_dir = entry.get("store_path")
    if not store_env_dir or not os.path.isdir(store_env_dir):
        raise ControllerError(
            "type-A requires an existing baseline store; ledger store_path="
            f"{store_env_dir!r}")
    copy_info = copy_store_into_scratch(store_env_dir, attempt_dir, entry["env_id"])
    env_spec = {"env_id": entry["env_id"]}
    if family:
        env_spec["family"] = family
    settings = dict(settings)
    settings["skipIngest"] = True
    manifest = build_manifest(run_id, benchmark, attempt_dir, env_spec, question,
                              settings)
    return {"manifest": manifest, "mode_detail": "copy-out", "copy": copy_info}


def prepare_typeb(entry: dict, attempt_dir: str, settings: dict, run_id: str,
                  question: dict, benchmark: str, cache_root: str, env_spec: dict,
                  env_source: str, capture_protocol: str | None = None) -> dict:
    key = typeb_cache_key(env_spec, settings, MODEL)
    cache_dir = os.path.join(cache_root, f"{entry['env_id']}--{key[:16]}")
    state = cache_state(cache_dir, entry["env_id"])
    if state["warm"]:
        # Reuse: copy the ingested store out, answer with skipIngest.
        copy_info = copy_store_into_scratch(
            os.path.join(cache_dir, "envs", entry["env_id"]), attempt_dir,
            entry["env_id"])
        settings = dict(settings)
        settings["skipIngest"] = True
        manifest = build_manifest(run_id, benchmark, attempt_dir,
                                  {"env_id": entry["env_id"],
                                   "family": env_spec.get("family")}, question,
                                  settings)
        return {"manifest": manifest, "mode_detail": "cache-reuse",
                "cache_key": key, "cache_dir": cache_dir, "copy": copy_info}
    if os.path.isdir(cache_dir):
        # A cold or partial cache must not trip the driver's fresh-output
        # capture gate; quarantine and re-ingest from scratch.
        quarantined = quarantine_cache(cache_dir)
        print(f"[typeB] quarantined partial cache -> {quarantined}", flush=True)
    settings = dict(settings)
    settings["skipIngest"] = False
    manifest = build_manifest(run_id, benchmark, cache_dir, env_spec, question,
                              settings, capture_protocol=capture_protocol)
    return {"manifest": manifest, "mode_detail": "ingest", "cache_key": key,
            "cache_dir": cache_dir}


def pick_mode(entry: dict, overrides: dict | None, requested: str) -> tuple:
    """auto: type A when a baseline store exists and no write-time key is
    overridden; otherwise type B (needs an env session source)."""
    write_keys, _ = settings_changed_keys({}, overrides)
    store_ok = bool(entry.get("store_path")) and os.path.isdir(entry["store_path"])
    if requested == "typeA":
        if write_keys:
            raise ControllerError(
                f"type-A cannot apply write-time settings {sorted(write_keys)}; "
                "use --mode typeB")
        return "typeA"
    if requested == "typeB":
        return "typeB"
    if write_keys:
        return "typeB"
    return "typeA" if store_ok else "typeB"


def run_attempt(args, entry: dict) -> int:
    qid = entry["qid"]
    benchmark = entry["benchmark"]
    family = FAMILY_CONFIG[benchmark]
    ledger_dir = os.path.dirname(os.path.abspath(args.ledger))
    attempts_root = args.attempts_root or os.path.join(ledger_dir, "ql-attempts")
    cache_root = args.cache_root or os.path.join(ledger_dir, "ql-typeb-cache")

    question_record = load_question_record(entry["qs_path"], qid)
    question_record = ensure_protocol_annotations(question_record, benchmark)
    question = question_declaration(question_record)

    base_settings = resolve_base_settings(
        entry, args.manifest_dir, args.base_settings)
    source_manifest = base_settings.get("__source_manifest__")
    overrides = read_json(args.settings) if args.settings else None
    settings = merge_settings(base_settings, overrides)
    settings.setdefault("memoryLearning", False)
    settings.setdefault("disableTools", True)

    mode = pick_mode(entry, overrides, args.mode)

    stamp = time.strftime("%Y%m%d-%H%M%S")
    run_id = args.run_id or (
        f"q4-{re.sub(r'[^A-Za-z0-9_.-]+', '_', qid)}-{mode}-{stamp}"
        f"-{uuid.uuid4().hex[:6]}")
    attempt_dir = os.path.join(attempts_root, run_id)
    os.makedirs(attempt_dir, exist_ok=False)

    env_spec = None
    env_source = None
    if mode == "typeA":
        family_tag = None
        if source_manifest and os.path.isfile(source_manifest):
            for env in read_json(source_manifest).get("envs", []):
                if env.get("env_id") == entry["env_id"] and env.get("family"):
                    family_tag = env["family"]
                    break
        prepared = prepare_typea(entry, attempt_dir, settings, run_id, question,
                                 benchmark, family=family_tag)
        cache_key = None
    else:
        env_source, _env_source_path, env_spec = load_env_spec(
            entry, args.env_file, args.env_from_manifest, args.manifest_dir,
            source_manifest)
        capture_protocol = None
        if env_source == "source-manifest" and os.path.isfile(source_manifest):
            capture_protocol = read_json(source_manifest).get("captureProtocol")
        elif env_source == "env-from-manifest" and os.path.isfile(_env_source_path):
            # A regenerated env carried by --env-from-manifest declares its
            # capture protocol in that same manifest; without it the driver
            # falls back to LegacyUndated and rejects versioned captures.
            capture_protocol = read_json(_env_source_path).get("captureProtocol")
        settings.setdefault("evidenceTimestamps", True)
        prepared = prepare_typeb(entry, attempt_dir, settings, run_id, question,
                                 benchmark, cache_root, env_spec, env_source,
                                 capture_protocol=capture_protocol)
        cache_key = prepared.get("cache_key")

    manifest = prepared["manifest"]
    manifest_path = os.path.join(attempt_dir, "manifest.json")
    atomic_write_json(manifest_path, manifest)

    print(f"[{qid}] mode={mode}/{prepared['mode_detail']} run_id={run_id}", flush=True)
    print(f"[{qid}] attempt_dir={attempt_dir}", flush=True)

    # 3. driver
    driver_result = run_driver(args.driver, manifest_path, args.driver_workdir,
                               args.models_json, os.path.join(attempt_dir, "driver.log"))
    output_dir = manifest["output_dir"]
    run_jsonl = os.path.join(output_dir, "run.jsonl")
    records = []
    if os.path.isfile(run_jsonl):
        records = extract_question_records(run_jsonl, qid)
    record = records[0] if records else None

    ingest_ok = True
    if mode == "typeB":
        state = cache_state(output_dir, entry["env_id"])
        ingest_ok = state["ingested"] and state["has_store"]
        if ingest_ok and prepared["mode_detail"] == "ingest":
            atomic_write_json(os.path.join(output_dir, "ingested.json"), {
                "cache_key": cache_key, "env_id": entry["env_id"], "ts": now_iso(),
                "source": env_source, "run_id": run_id})

    if driver_result["exit_code"] != 0 and record is None:
        raise ControllerError(
            f"driver exited {driver_result['exit_code']} with no record for {qid}; "
            f"see {attempt_dir}/driver.log")

    if record is None:
        raise ControllerError(
            f"driver produced no run.jsonl record for {qid}; "
            f"see {attempt_dir}/driver.log")

    # 4. question-scoped scoring
    qs_dir, run_one, _ = build_scoring_inputs(attempt_dir, question_record,
                                              run_jsonl, qid)
    judge_profile = normalize_judge_profile(
        entry.get("judge_profile") or family["judge_profile"])
    use_models_json = family["models_json"] and judge_profile != "local-f1"
    extra_env = {}
    if args.lp_judge_max_tokens:
        extra_env["LP_JUDGE_MAX_TOKENS"] = str(args.lp_judge_max_tokens)
    scores = run_evaluator(
        args.bench_venv, args.scoring_cwd, family["bench_flag"], run_one, qs_dir,
        os.path.join(attempt_dir, "scoring", "scores.json"), judge_profile,
        args.judge_models_json if use_models_json else None,
        os.path.join(attempt_dir, "scoring", "evaluator.log"), extra_env)
    result = single_result(scores, qid)

    score = float(result.get("score", 0.0))
    flag = result.get("flag")
    correct = (not flag) and score >= 0.5
    outcome = ("pass" if correct else
               "eval_error" if flag in ("eval_error",) else
               "flagged" if flag else "fail")
    if flag and flag != "eval_error":
        outcome = "flagged"

    stage = failure_stage(record, result, driver_result["exit_code"], mode, ingest_ok)
    telemetry = {
        "helpers": helper_observations(record),
        "ingest": ("skipped (typeA skipIngest)" if mode == "typeA" else
                   f"cache {'warm' if prepared['mode_detail'] == 'cache-reuse' else 'ingest'} "
                   f"({prepared.get('cache_key') or 'n/a'})"),
    }

    answer_text = record.get("answer_text") or ""
    recall_ids = record.get("recall_ids") or []
    usage = record.get("usage") or {}
    attempt = {
        "schema": ATTEMPT_SCHEMA,
        "ts": now_iso(),
        "controller": CONTROLLER_NAME,
        "worker": args.worker,
        "qid": qid,
        "benchmark": benchmark,
        "mode": mode,
        "mode_detail": prepared["mode_detail"],
        "cache_key": cache_key,
        "run_id": run_id,
        "run_dir": attempt_dir,
        "manifest": manifest_path,
        "driver_exit_code": driver_result["exit_code"],
        "score": score,
        "flag": flag,
        "outcome": outcome,
        "failure_stage": stage,
        "judge_profile": judge_profile,
        "judge_label": result.get("label"),
        "answer_excerpt": answer_text[:200],
        "answer_chars": len(answer_text),
        "recall_ids": recall_ids[:12],
        "recall_ids_count": len(recall_ids),
        "recall_chars": record.get("recall_chars"),
        "tokens": {"input": usage.get("input_tokens"),
                   "output": usage.get("output_tokens"),
                   "total": usage.get("total_tokens")},
        "latency_ms": record.get("latency_ms"),
        "judge_latency_ms": result.get("judge_latency_ms"),
        "settings": settings,
    }
    if mode == "typeA":
        attempt["store_source"] = entry.get("store_path")
    else:
        attempt["cache_dir"] = prepared.get("cache_dir")
        attempt["env_source"] = env_source

    append_attempt(args.ledger, qid, attempt)
    print(f"[{qid}] outcome={outcome} score={score} flag={flag} stage={stage}", flush=True)
    print(f"[{qid}] ledger appended -> {args.ledger}", flush=True)

    if outcome == "pass":
        return EXIT_OK
    print_diagnostics(entry, question_record, record, result, telemetry, attempt)
    if not correct and not flag:
        return EXIT_INCORRECT
    if stage == "driver":
        return EXIT_CONTROLLER  # no valid answer was produced
    return EXIT_FLAGGED


# --- CLI -----------------------------------------------------------------------

def build_parser():
    parser = argparse.ArgumentParser(
        description=__doc__.splitlines()[0],
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("qid", nargs="?", help="ledger qid (or omit with --next)")
    parser.add_argument("--next", action="store_true",
                        help="resume: pick the next unattempted ledger entry")
    parser.add_argument("--retry", action="store_true",
                        help="same question with new settings (explicit intent; "
                             "the attempt is appended like any other)")
    parser.add_argument("--retry-failed", action="store_true",
                        help="with --next: also re-attempt failed/eval_error entries")
    parser.add_argument("--include-attempted", action="store_true",
                        help="with --next: ignore previous attempts")
    parser.add_argument("--benchmark", action="append",
                        help="with --next: filter benchmarks (repeatable)")
    parser.add_argument("--limit", type=int, default=1,
                        help="with --next: how many entries to list (default 1)")
    parser.add_argument("--ledger", default=os.environ.get("MEMORY_QUESTION_LEDGER", DEFAULT_LEDGER))
    parser.add_argument("--mode", choices=["auto", "typeA", "typeB"], default="auto")
    parser.add_argument("--settings", default=None,
                        help="JSON file with manifest settings overrides")
    parser.add_argument("--base-settings", default=None,
                        help="JSON file with the full base settings block "
                             "(default: the ledger entry's source manifest)")
    parser.add_argument("--env-file", default=None,
                        help="type-B: env spec JSON (single env or list)")
    parser.add_argument("--env-from-manifest", default=None,
                        help="type-B: pick the env from this driver manifest")
    parser.add_argument("--driver", default=DEFAULT_DRIVER)
    parser.add_argument("--driver-workdir", default=DEFAULT_DRIVER_WORKDIR)
    parser.add_argument("--models-json", default=DEFAULT_MODELS_JSON,
                        help="MEMORY_BENCH_MODELS_JSON for the driver")
    parser.add_argument("--judge-models-json", default=DEFAULT_JUDGE_MODELS_JSON)
    parser.add_argument("--bench-venv", default=DEFAULT_BENCH_VENV_PYTHON)
    parser.add_argument("--scoring-cwd", default=DEFAULT_SCORING_CWD)
    parser.add_argument("--attempts-root", default=None)
    parser.add_argument("--cache-root", default=None)
    parser.add_argument("--manifest-dir", action="append", default=None,
                        help="manifest search dir for baseline settings "
                             "(repeatable; default iteration3+4 inputs)")
    parser.add_argument("--run-id", default=None)
    parser.add_argument("--worker", default=os.environ.get("COMPUTERNAME", "worker"))
    parser.add_argument("--lp-judge-max-tokens", type=int, default=None,
                        help="LP_JUDGE_MAX_TOKENS passthrough for the LP judge")
    parser.add_argument("--dry-run", action="store_true",
                        help="resolve and print the plan without running anything")
    return parser


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    args.manifest_dir = args.manifest_dir or DEFAULT_MANIFEST_DIRS
    if not args.qid and not args.next:
        print("error: pass a qid or --next", file=sys.stderr)
        return EXIT_CONTROLLER
    ledger = load_ledger(args.ledger)
    if args.next:
        entries = next_entry(ledger, benchmark=args.benchmark,
                             retry_failed=args.retry_failed,
                             include_attempted=args.include_attempted,
                             limit=max(1, args.limit))
        if not entries:
            print("no eligible entries in ledger order", flush=True)
            return EXIT_OK
        for entry in entries:
            print(f"[next] {entry['qid']} benchmark={entry['benchmark']} "
                  f"status={entry.get('status')} queue={entry.get('queue')} "
                  f"attempts={len(entry.get('attempts') or [])}", flush=True)
        if args.limit == 1 and len(entries) == 1:
            args.qid = entries[0]["qid"]
        else:
            return EXIT_OK
    entry = find_entry(ledger, args.qid)
    if entry is None:
        print(f"error: qid {args.qid} not in ledger {args.ledger}", file=sys.stderr)
        return EXIT_CONTROLLER
    if args.dry_run:
        mode = pick_mode(entry, read_json(args.settings) if args.settings else None, args.mode)
        print(f"[dry-run] qid={entry['qid']} benchmark={entry['benchmark']} "
              f"mode={mode} store={entry.get('store_path')} qs={entry['qs_path']}")
        return EXIT_OK
    try:
        return run_attempt(args, entry)
    except ControllerError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_CONTROLLER


if __name__ == "__main__":
    sys.exit(main())
