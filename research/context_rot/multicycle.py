"""Compaction-cycle simulator.

A :class:`Compactor` is pluggable. The bundled
:class:`DeterministicTruncatingCompactor` needs no model: it keeps the system
message, a truncated summary of everything it drops, and the last ``keep_tail``
messages. That is enough to reproduce the failure mode this harness measures -
evidence that survives turn 0 but is gone by cycle 3.

Every cycle records whether the evidence markers are still present in the
rendered prompt. If they are gone the cell is INVALID (``correct=null``), never
scored wrong.
"""

import json
import os
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Protocol, Sequence, Tuple, runtime_checkable

from .adapters import AdapterError, ModelAdapter
from .corpus import CorpusGenerator, estimate_messages_tokens
from .scoring import score_answer
from .tasks import InstanceConfig, Item, build_context

SUMMARY_PREFIX = "COMPACTION SUMMARY (deterministic truncation, no model)"


@dataclass(frozen=True)
class CompactionResult:
    messages: List[dict]
    dropped_messages: int
    summary_text: str
    tokens_before: int
    tokens_after: int
    strategy: str = "truncating"

    def as_dict(self) -> dict:
        return {
            "dropped_messages": self.dropped_messages,
            "tokens_before": self.tokens_before,
            "tokens_after": self.tokens_after,
            "strategy": self.strategy,
        }


@runtime_checkable
class Compactor(Protocol):
    name: str

    def compact(self, messages: Sequence[dict], budget_tokens: int) -> CompactionResult:
        ...


class DeterministicTruncatingCompactor:
    """Keep system + head-of-dropped summary + last ``keep_tail`` messages."""

    name = "deterministic-truncating"

    def __init__(self, keep_tail: int = 2, summary_chars: int = 1200,
                 keep_head_chars: int = 400):
        self.keep_tail = int(keep_tail)
        self.summary_chars = int(summary_chars)
        self.keep_head_chars = int(keep_head_chars)

    def compact(self, messages: Sequence[dict], budget_tokens: int) -> CompactionResult:
        messages = list(messages)
        tokens_before = estimate_messages_tokens(messages)
        if len(messages) <= self.keep_tail + 1:
            return CompactionResult(messages, 0, "", tokens_before, tokens_before,
                                    "noop-below-threshold")
        head = messages[:1]
        tail = messages[-self.keep_tail:] if self.keep_tail else []
        dropped = messages[1:len(messages) - len(tail)]
        summary_body = "\n\n".join(str(m.get("content", "")) for m in dropped)
        summary_text = "%s\n%s" % (SUMMARY_PREFIX, summary_body[:self.summary_chars])
        summary_message = {"role": "user", "content": summary_text}
        compacted = head + [summary_message] + tail
        return CompactionResult(compacted, len(dropped), summary_text,
                                tokens_before, estimate_messages_tokens(compacted),
                                "keep_tail=%d" % self.keep_tail)


@dataclass(frozen=True)
class MultiCycleConfig:
    cycles: int = 3
    budget_tokens: int = 2500
    turn_filler_tokens: int = 120
    context_target_len: int = 6000
    evidence_position: str = "early"
    distractor_kind: str = "none"
    keep_tail: int = 2
    requested_json_style: bool = False

    def instance_config(self) -> InstanceConfig:
        return InstanceConfig(context_target_len=self.context_target_len,
                              evidence_position=self.evidence_position,
                              distractor_kind=self.distractor_kind)

    def as_dict(self) -> dict:
        return {
            "cycles": self.cycles,
            "budget_tokens": self.budget_tokens,
            "turn_filler_tokens": self.turn_filler_tokens,
            "context_target_len": self.context_target_len,
            "evidence_position": self.evidence_position,
            "distractor_kind": self.distractor_kind,
            "keep_tail": self.keep_tail,
            "requested_json_style": self.requested_json_style,
        }


def _prompt_text(messages: Sequence[dict]) -> str:
    return "\n".join(str(m.get("content", "")) for m in messages)


def _evidence_present(item: Item, messages: Sequence[dict]) -> Tuple[bool, Tuple[str, ...]]:
    text = _prompt_text(messages)
    missing = tuple(marker for marker in item.visible.evidence_markers if marker not in text)
    return (not missing), missing


def _work_turn(seed: int, cycle: int, filler_tokens: int) -> List[dict]:
    """One unrelated working turn, deterministic in (seed, cycle)."""
    chars = max(200, int(filler_tokens * 4))
    text = CorpusGenerator(seed + 31 * cycle + 7).text(chars, block_chars=320)
    return [
        {"role": "user", "content": "Unrelated maintenance turn %d:\n%s" % (cycle, text)},
        {"role": "assistant", "content": "Noted; continuing."},
    ]


def run_cycles(item: Item, adapter: ModelAdapter, compactor: Compactor,
               config: MultiCycleConfig, run_meta: Optional[Dict[str, Any]] = None,
               temperature: float = 0.0,
               max_tokens: Optional[int] = None) -> List[dict]:
    """Simulate ``config.cycles`` compaction cycles, asking the item each cycle."""
    run_meta = dict(run_meta or {})
    instance = config.instance_config()
    bundle = build_context(item, instance)
    messages: List[dict] = [
        {"role": "system", "content": "You are a careful assistant working in a long session."},
        {"role": "user", "content": "CONTEXT:\n%s\n\nQUESTION:\n%s"
         % (bundle.text, item.visible.question)},
        {"role": "assistant", "content": "Working on it."},
    ]
    records: List[dict] = []
    for cycle in range(config.cycles):
        messages.extend(_work_turn(hash_seed(item), cycle, config.turn_filler_tokens))
        compacted = False
        dropped = 0
        tokens_before = estimate_messages_tokens(messages)
        tokens_after = tokens_before
        if tokens_before > config.budget_tokens:
            result = compactor.compact(messages, config.budget_tokens)
            messages = list(result.messages)
            compacted = result.dropped_messages > 0
            dropped = result.dropped_messages
            tokens_after = result.tokens_after

        survived, missing = _evidence_present(item, messages)
        ask = list(messages) + [{"role": "user", "content": "QUESTION:\n%s" % item.visible.question}]
        prompt_tokens = estimate_messages_tokens(ask)
        raw_answer = ""
        usage: Dict[str, Any] = {}
        latency_ms = 0.0
        invalid_reason: Optional[str] = None
        error = ""
        try:
            response = adapter.complete(
                [{"role": m["role"], "content": m["content"]} for m in ask],
                json_style=config.requested_json_style, temperature=temperature,
                max_tokens=max_tokens)
            raw_answer = response.text
            usage = dict(response.usage or {})
            latency_ms = float(response.latency_ms)
        except AdapterError as exc:
            invalid_reason = "adapter_error"
            error = str(exc)
        except Exception as exc:  # adapter bug
            invalid_reason = "adapter_error"
            error = "%s: %s" % (type(exc).__name__, exc)
        if invalid_reason is None and not survived:
            invalid_reason = "compaction_lost_evidence"

        score = score_answer(raw_answer, item.gold.answer, item.gold.answer_kind,
                             aliases=item.gold.aliases, facts=item.gold.facts,
                             context_text=bundle.text,
                             json_style=config.requested_json_style,
                             invalid_reason=invalid_reason)
        records.append({
            "mode": "multicycle",
            "run_id": run_meta.get("run_id"),
            "suite": item.visible.suite,
            "item_id": item.item_id,
            "instance_id": "%s@cycles=%d|budget=%d|pos=%s" % (
                item.item_id, config.cycles, config.budget_tokens, config.evidence_position),
            "category": item.visible.category,
            "answer_kind": item.visible.answer_kind,
            "cycle": cycle,
            "cycles_total": config.cycles,
            "seed": int(run_meta.get("seed", 0)),
            "model": run_meta.get("model"),
            "provider": run_meta.get("provider"),
            "compactor": getattr(compactor, "name", "unknown"),
            "context_target_len": config.context_target_len,
            "evidence_position": config.evidence_position,
            "distractor_kind": config.distractor_kind,
            "requested_json_style": bool(config.requested_json_style),
            "compacted": compacted,
            "dropped_messages": dropped,
            "tokens_original_est": tokens_before,
            "tokens_final_est": tokens_after,
            "truncated": bool(compacted),
            "evidence_survived": bool(survived),
            "missing_markers": list(missing),
            "prompt_tokens": prompt_tokens,
            "usage": usage,
            "latency_ms": round(latency_ms, 3),
            "extraction_rule": score.extraction_rule,
            "extraction_detail": score.extraction_detail,
            "correct": score.correct,
            "invalid_reason": score.invalid_reason,
            "abstained": score.abstained,
            "unsupported_claim": score.unsupported_claim,
            "matched_facts": list(score.matched_facts),
            "missing_facts": list(score.missing_facts),
            "raw_answer": raw_answer,
            "gold_fingerprint": item.gold.fingerprint,
            "token_estimator": "estimate_v1:ceil(chars/4 + digits/12 + newlines/2)",
            "token_is_estimate": True,
            "adapter_synthetic": bool(getattr(adapter, "provider", "") == "fake"),
            "error": error,
        })
        messages = ask  # keep the asked question in the transcript, like a real session
    return records


def hash_seed(item: Item) -> int:
    """Stable small integer derived from the item id (for turn filler)."""
    total = 0
    for index, char in enumerate(item.item_id):
        total += (index + 1) * ord(char)
    return total % 100003


def summarize_cycles(records: Sequence[dict]) -> List[dict]:
    """Per-cycle accuracy and evidence retention."""
    by_cycle: Dict[int, List[dict]] = {}
    for record in records:
        by_cycle.setdefault(int(record.get("cycle", 0)), []).append(record)
    rows = []
    for cycle in sorted(by_cycle):
        group = by_cycle[cycle]
        scored = [r for r in group if r.get("correct") is not None]
        rows.append({
            "cycle": cycle,
            "cells": len(group),
            "valid": len(scored),
            "accuracy": (sum(1 for r in scored if r.get("correct")) / len(scored))
            if scored else None,
            "evidence_retained": sum(1 for r in group if r.get("evidence_survived")) / len(group),
            "compacted": sum(1 for r in group if r.get("compacted")),
            "dropped_messages": sum(int(r.get("dropped_messages") or 0) for r in group),
        })
    return rows


def write_cycles(path: str, records: Sequence[dict]) -> str:
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as handle:
        for record in records:
            handle.write(json.dumps(record, sort_keys=True) + "\n")
    return path


def render_multicycle_markdown(records: Sequence[dict], config: MultiCycleConfig) -> str:
    """Small markdown table for the compaction section of a report."""
    lines = ["## Compaction cycles (deterministic compactor)", ""]
    lines.append("Config: cycles=%d, budget_tokens=%d, context_target_len=%d, "
                 "evidence_position=%s" % (config.cycles, config.budget_tokens,
                                           config.context_target_len,
                                           config.evidence_position))
    lines.append("")
    lines.append("| cycle | cells | valid | accuracy | evidence retained | compacted cells |")
    lines.append("| --- | --- | --- | --- | --- | --- |")
    for row in summarize_cycles(records):
        accuracy = "n/a" if row["accuracy"] is None else "%.3f" % row["accuracy"]
        lines.append("| %d | %d | %d | %s | %.3f | %d |" % (
            row["cycle"], row["cells"], row["valid"], accuracy,
            row["evidence_retained"], row["compacted"]))
    lines.append("")
    return "\n".join(lines)
