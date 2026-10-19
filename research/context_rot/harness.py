"""Run orchestration: one cell, whole suites, persistence, resumability.

Guarantees:
  * a run writes to ``runs/<utc-stamp>-<slug>/`` and refuses an existing dir;
  * ``manifest.json`` records git HEAD, interpreter, args and seeds;
  * ``results.jsonl`` is appended and flushed per cell, so a crash loses at most
    the in-flight cells and ``--resume`` continues from what is on disk;
  * an INVALID cell (truncated evidence, adapter error) is recorded with
    ``correct=null`` and never converted into a wrong answer.
"""

import json
import os
import platform
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from . import __version__
from .adapters import AdapterError, FakeAdapter, ModelAdapter
from .corpus import TOKEN_ESTIMATOR, estimate_messages_tokens
from .report import write_csv, write_summary
from .scoring import score_answer
from .tasks import (GoldLeakError, InstanceConfig, Item, build_context,
                    generate_suite, instance_id, render_messages, visible_payload)

PACKAGE_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(os.path.dirname(PACKAGE_DIR))
DEFAULT_RUNS_ROOT = os.path.join(PACKAGE_DIR, "runs")


class RunDirExistsError(RuntimeError):
    """A run never overwrites an earlier run directory."""


def utc_stamp(now: Optional[datetime] = None) -> str:
    moment = now or datetime.now(timezone.utc)
    return moment.strftime("%Y%m%dT%H%M%SZ")


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _git(args: Sequence[str]) -> Optional[str]:
    try:
        proc = subprocess.run(["git"] + list(args), cwd=REPO_ROOT,
                              capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0:
        return None
    return proc.stdout.strip()


def git_info() -> dict:
    head = _git(["rev-parse", "HEAD"])
    branch = _git(["rev-parse", "--abbrev-ref", "HEAD"])
    dirty = _git(["status", "--porcelain"])
    return {
        "git_head": head,
        "git_branch": branch,
        "git_dirty": bool(dirty) if dirty is not None else None,
        "repo_root": REPO_ROOT,
    }


def new_run_dir(runs_root: str, slug: str,
                now: Optional[datetime] = None, max_suffix: int = 50) -> str:
    """Create ``<runs_root>/<utc-stamp>-<slug>``.

    Never writes into an existing directory: when two runs land on the same
    second the id gets a numeric suffix (``-2``, ``-3``, ...). Raises
    :class:`RunDirExistsError` only if every suffixed name is taken.
    """
    safe_slug = "".join(c if (c.isalnum() or c in "-_.") else "-" for c in slug)
    base_id = "%s-%s" % (utc_stamp(now), safe_slug)
    os.makedirs(runs_root, exist_ok=True)
    for attempt in range(1, max_suffix + 1):
        run_id = base_id if attempt == 1 else "%s-%d" % (base_id, attempt)
        path = os.path.join(runs_root, run_id)
        try:
            os.makedirs(path, exist_ok=False)
        except FileExistsError:
            continue
        return path
    raise RunDirExistsError("could not create a free run directory for %s" % base_id)


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class RunConfig:
    suite: str = "needle-smoke"
    seed: int = 1234
    model: str = "fake-adapter"
    provider: str = "fake"
    adapter_kind: str = "fake"
    context_target_lens: Tuple[int, ...] = (2000, 8000)
    evidence_positions: Tuple[str, ...] = ("middle",)
    distractor_kinds: Tuple[str, ...] = ("none",)
    requested_json_style: bool = False
    max_prompt_tokens: Optional[int] = None
    truncate_strategy: str = "head_tail"
    concurrency: int = 1
    limit: Optional[int] = None
    runs_root: str = DEFAULT_RUNS_ROOT
    slug: str = "run"
    temperature: float = 0.0
    max_tokens: Optional[int] = None

    def instance_configs(self) -> List[InstanceConfig]:
        configs: List[InstanceConfig] = []
        for length in self.context_target_lens:
            for position in self.evidence_positions:
                for distractor in self.distractor_kinds:
                    configs.append(InstanceConfig(
                        context_target_len=int(length),
                        evidence_position=position,
                        distractor_kind=distractor,
                        max_prompt_tokens=self.max_prompt_tokens,
                        truncate_strategy=self.truncate_strategy,
                    ))
        return configs

    def as_dict(self) -> dict:
        return {
            "suite": self.suite,
            "seed": int(self.seed),
            "model": self.model,
            "provider": self.provider,
            "adapter_kind": self.adapter_kind,
            "context_target_lens": list(self.context_target_lens),
            "evidence_positions": list(self.evidence_positions),
            "distractor_kinds": list(self.distractor_kinds),
            "requested_json_style": bool(self.requested_json_style),
            "max_prompt_tokens": self.max_prompt_tokens,
            "truncate_strategy": self.truncate_strategy,
            "concurrency": int(self.concurrency),
            "limit": self.limit,
            "runs_root": self.runs_root,
            "slug": self.slug,
            "temperature": float(self.temperature),
            "max_tokens": self.max_tokens,
        }


# ---------------------------------------------------------------------------
# One cell
# ---------------------------------------------------------------------------

def run_one(item: Item, instance: InstanceConfig, adapter: ModelAdapter,
            run_meta: Dict[str, Any], json_style: bool = False,
            temperature: float = 0.0, max_tokens: Optional[int] = None) -> dict:
    """Run a single (item, context config) cell. Never raises for model errors."""
    started = time.perf_counter()
    iid = instance_id(item, instance)
    record: Dict[str, Any] = {
        "run_id": run_meta.get("run_id"),
        "suite": item.visible.suite,
        "item_id": item.item_id,
        "instance_id": iid,
        "category": item.visible.category,
        "family": item.visible.family,
        "answer_kind": item.visible.answer_kind,
        "seed": int(run_meta.get("seed", 0)),
        "model": run_meta.get("model"),
        "provider": run_meta.get("provider"),
        "adapter_kind": run_meta.get("adapter_kind"),
        "context_target_len": instance.context_target_len,
        "evidence_position": instance.evidence_position,
        "distractor_kind": instance.distractor_kind,
        "requested_json_style": bool(json_style),
        "config_key": instance.key(),
        "gold_fingerprint": item.gold.fingerprint,
        "token_estimator": TOKEN_ESTIMATOR,
        "token_is_estimate": True,
        "prompt_tokens_source": "estimate",
        "timestamp": utc_now_iso(),
    }

    try:
        bundle = build_context(item, instance)
    except GoldLeakError as exc:
        record.update({
            "correct": None,
            "invalid_reason": "gold_leak",
            "raw_answer": "",
            "normalized_answer": "",
            "extraction_rule": "none",
            "extraction_detail": {},
            "matched": False,
            "abstained": False,
            "unsupported_claim": False,
            "unsupported_reason": "",
            "matched_facts": [],
            "missing_facts": [],
            "prompt_tokens": 0,
            "usage": {},
            "latency_ms": 0.0,
            "error": str(exc),
            "truncated": False,
            "evidence_survived": False,
        })
        return record

    messages = render_messages(visible_payload(item.visible, bundle.text),
                               json_style=json_style)
    prompt_tokens = estimate_messages_tokens(messages)
    record.update({
        "prompt_tokens": prompt_tokens,
        "prompt_chars": sum(len(str(m.get("content", ""))) for m in messages),
        "context_chars": bundle.context_chars,
        "context_target_len": bundle.context_target_len,
        "context_tokens_est": bundle.tokens_final,
        "tokens_original_est": bundle.tokens_original,
        "tokens_final_est": bundle.tokens_final,
        "truncated": bundle.truncated,
        "truncate_strategy": bundle.truncate_strategy,
        "evidence_survived": bundle.evidence_survived,
        "missing_markers": list(bundle.missing_markers),
        "block_count": bundle.block_count,
        "leak_attempts": bundle.leak_attempts,
        "distractor_blocks": len(bundle.distractor_meta),
    })

    invalid_reason: Optional[str] = None
    raw_answer = ""
    usage: Dict[str, Any] = {}
    latency_ms = 0.0
    error = ""
    synthetic = bool(run_meta.get("adapter_synthetic", False))
    try:
        response = adapter.complete(messages, json_style=json_style,
                                    temperature=temperature, max_tokens=max_tokens)
        raw_answer = response.text
        usage = dict(response.usage or {})
        latency_ms = float(response.latency_ms)
        synthetic = bool(response.synthetic)
    except AdapterError as exc:
        invalid_reason = "adapter_error"
        error = str(exc)
        usage = {"status": exc.status, "body": (exc.body or "")[:400], "source": "error"}
    except Exception as exc:  # adapter bug: record, do not kill the run
        invalid_reason = "adapter_error"
        error = "%s: %s" % (type(exc).__name__, exc)

    if invalid_reason is None and bundle.truncated and not bundle.evidence_survived:
        invalid_reason = "truncated_evidence"

    score = score_answer(
        raw_answer,
        item.gold.answer,
        item.gold.answer_kind,
        aliases=item.gold.aliases,
        facts=item.gold.facts,
        context_text=bundle.text,
        json_style=json_style,
        invalid_reason=invalid_reason,
    )

    record.update({
        "usage": usage,
        "latency_ms": round(latency_ms, 3),
        "wall_ms": round((time.perf_counter() - started) * 1000.0, 3),
        "adapter_synthetic": synthetic,
        "raw_answer": raw_answer,
        "normalized_answer": score.normalized_answer,
        "extraction_rule": score.extraction_rule,
        "extraction_detail": score.extraction_detail,
        "correct": score.correct,
        "matched": score.matched,
        "invalid_reason": score.invalid_reason,
        "abstained": score.abstained,
        "unsupported_claim": score.unsupported_claim,
        "unsupported_reason": score.unsupported_reason,
        "matched_facts": list(score.matched_facts),
        "missing_facts": list(score.missing_facts),
        "error": error,
    })
    return record


# ---------------------------------------------------------------------------
# Suite runs
# ---------------------------------------------------------------------------

def _read_jsonl(path: str) -> List[dict]:
    if not os.path.exists(path):
        return []
    records = []
    with open(path, "r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except ValueError:
                continue
    return records


def load_results(run_dir: str) -> List[dict]:
    return _read_jsonl(os.path.join(run_dir, "results.jsonl"))


def _write_manifest(run_dir: str, manifest: dict) -> None:
    path = os.path.join(run_dir, "manifest.json")
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as handle:
        json.dump(manifest, handle, indent=2, sort_keys=True)
        handle.write("\n")
    os.replace(tmp, path)


def _jsonable(value: Any) -> Any:
    """Best-effort conversion so manifest args never break the write."""
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    return repr(value)


def build_manifest(config: RunConfig, run_dir: str, adapter: ModelAdapter,
                   args: Optional[Dict[str, Any]] = None,
                   item_count: int = 0, cell_count: int = 0) -> dict:
    manifest = {
        "run_id": os.path.basename(run_dir),
        "slug": config.slug,
        "run_dir": run_dir,
        "suite": config.suite,
        "seed": int(config.seed),
        "seeds": {
            "suite": int(config.seed),
            "bootstrap": 1234,
            "note": "all generation is seeded; no wall clock or global RNG is used",
        },
        "model": config.model,
        "provider": config.provider,
        "adapter_kind": config.adapter_kind,
        "adapter_name": getattr(adapter, "name", None),
        "harness_version": __version__,
        "python_version": sys.version.split()[0],
        "python_implementation": platform.python_implementation(),
        "platform": platform.platform(),
        "token_estimator": TOKEN_ESTIMATOR,
        "config": config.as_dict(),
        "instance_configs": [c.as_dict() for c in config.instance_configs()],
        "item_count": item_count,
        "cell_count": cell_count,
        "started_at": utc_now_iso(),
        "args": _jsonable(dict(args or {})),
    }
    manifest.update(git_info())
    return manifest


def run_suite(config: RunConfig, adapter: ModelAdapter,
              runs_root: Optional[str] = None, run_dir: Optional[str] = None,
              resume: bool = False, items: Optional[Sequence[Item]] = None,
              progress: Optional[Callable[[dict], None]] = None,
              args: Optional[Dict[str, Any]] = None) -> dict:
    """Execute every (item, instance config) cell and persist the run."""
    if run_dir is None:
        root = runs_root or config.runs_root
        run_dir = new_run_dir(root, config.slug)
    elif resume:
        if not os.path.isdir(run_dir):
            raise RunDirExistsError("cannot resume, directory missing: %s" % run_dir)
    else:
        if os.path.isdir(run_dir) and os.listdir(run_dir):
            raise RunDirExistsError("run directory not empty: %s" % run_dir)
        os.makedirs(run_dir, exist_ok=True)

    suite_items = list(items) if items is not None else generate_suite(
        config.suite, config.seed, limit=config.limit)
    instances = config.instance_configs()
    pairs: List[Tuple[Item, InstanceConfig]] = [
        (item, instance) for instance in instances for item in suite_items]

    results_path = os.path.join(run_dir, "results.jsonl")
    done = {record.get("instance_id") for record in _read_jsonl(results_path)} if resume else set()

    manifest = build_manifest(config, run_dir, adapter, args=args,
                              item_count=len(suite_items), cell_count=len(pairs))
    manifest["status"] = "running"
    manifest["resumed"] = bool(resume)
    manifest["cells_already_done"] = len(done)
    _write_manifest(run_dir, manifest)

    run_meta = {
        "run_id": os.path.basename(run_dir),
        "seed": config.seed,
        "model": config.model,
        "provider": config.provider,
        "adapter_kind": config.adapter_kind,
        "adapter_synthetic": isinstance(adapter, FakeAdapter) or config.adapter_kind != "openai",
    }

    lock = threading.Lock()
    written = 0
    started = time.perf_counter()
    with open(results_path, "a", encoding="utf-8", newline="\n") as handle:
        def _emit(record: dict) -> None:
            with lock:
                handle.write(json.dumps(record, sort_keys=True) + "\n")
                handle.flush()
                os.fsync(handle.fileno())
                if progress:
                    progress(record)

        pending = [(item, instance) for item, instance in pairs
                   if instance_id(item, instance) not in done]
        if config.concurrency <= 1:
            for item, instance in pending:
                record = run_one(item, instance, adapter, run_meta,
                                 json_style=config.requested_json_style,
                                 temperature=config.temperature,
                                 max_tokens=config.max_tokens)
                _emit(record)
                written += 1
        else:
            with ThreadPoolExecutor(max_workers=int(config.concurrency)) as pool:
                futures = {
                    pool.submit(run_one, item, instance, adapter, run_meta,
                                config.requested_json_style, config.temperature,
                                config.max_tokens): (item, instance)
                    for item, instance in pending
                }
                for future in as_completed(futures):
                    record = future.result()
                    _emit(record)
                    written += 1

    records = _read_jsonl(results_path)
    records.sort(key=lambda r: (str(r.get("item_id")), str(r.get("config_key"))))
    manifest["finished_at"] = utc_now_iso()
    manifest["status"] = "complete"
    manifest["duration_s"] = round(time.perf_counter() - started, 3)
    manifest["cells_written_this_invocation"] = written
    manifest["cells_total_on_disk"] = len(records)
    scores = [r.get("correct") for r in records if r.get("correct") is not None]
    manifest["summary"] = {
        "cells": len(records),
        "valid": len(scores),
        "correct": int(sum(1 for s in scores if s)),
        "accuracy": (sum(1 for s in scores if s) / len(scores)) if scores else None,
        "invalid": sum(1 for r in records if r.get("correct") is None),
        "synthetic_adapter": bool(run_meta["adapter_synthetic"]),
    }
    _write_manifest(run_dir, manifest)
    write_csv(os.path.join(run_dir, "results.csv"), records)
    write_summary(os.path.join(run_dir, "summary.md"), records, manifest)
    return {"run_dir": run_dir, "records": records, "manifest": manifest}
