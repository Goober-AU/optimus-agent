"""Task generation, gold records and the visible/gold separation boundary.

Two dataclasses own the boundary:

* :class:`VisibleItem` - everything the model may see (question, evidence block,
  evidence markers, visible distractor parameters).
* :class:`GoldRecord` - the answer, accepted aliases, sub-facts, a fingerprint
  and provenance. Never rendered.

:func:`visible_payload` is the only builder of model-visible text and its
signature accepts visible fields only; :func:`assert_gold_absent` re-checks the
assembled text for the gold string before an item is used.
"""

import hashlib
import re
from dataclasses import dataclass, field
from typing import Dict, List, Mapping, Optional, Sequence, Tuple

from .corpus import (CorpusGenerator, TOKEN_ESTIMATOR, estimate_messages_tokens,
                     estimate_tokens)
from .distractors import DISTRACTOR_KINDS, Distractor, build_distractors
from .needles import Needle, iter_needles

# ---------------------------------------------------------------------------
# Evidence positions
# ---------------------------------------------------------------------------

POSITIONS: Dict[str, float] = {
    "start": 0.02,
    "early": 0.10,
    "middle": 0.50,
    "late": 0.80,
    "end": 0.97,
}

DISTRACTOR_KIND_ORDER: Tuple[str, ...] = DISTRACTOR_KINDS


def resolve_position(label: str) -> float:
    """Map a position label to the block-split fraction."""
    if label in POSITIONS:
        return POSITIONS[label]
    try:
        value = float(label)
    except (TypeError, ValueError):
        raise ValueError("unknown evidence position: %r (expected one of %s)"
                         % (label, ", ".join(POSITIONS)))
    if not 0.0 <= value <= 1.0:
        raise ValueError("evidence position fraction must be in [0, 1]: %r" % (label,))
    return value


# ---------------------------------------------------------------------------
# Gold / visible records
# ---------------------------------------------------------------------------

class GoldLeakError(RuntimeError):
    """Raised when the gold answer would be visible to the model."""


_GOLD_SCAN_RE_CACHE: Dict[str, "re.Pattern"] = {}


def gold_fingerprint(answer: str) -> str:
    """Stable short hash of the gold answer. Records carry this, never the text."""
    normalized = re.sub(r"\s+", " ", str(answer)).strip().casefold()
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:16]


def _gold_pattern(gold: str) -> "re.Pattern":
    """Boundary-aware matcher: '48' must not match inside '1480'."""
    key = gold
    cached = _GOLD_SCAN_RE_CACHE.get(key)
    if cached is not None:
        return cached
    pattern = re.compile(r"(?<![0-9a-z])" + re.escape(gold) + r"(?![0-9a-z])")
    _GOLD_SCAN_RE_CACHE[key] = pattern
    return pattern


def gold_strings(gold: "GoldRecord") -> Tuple[str, ...]:
    """Gold strings that must not appear in visible text (answer plus sub-facts)."""
    out: List[str] = []
    if gold.answer:
        out.append(str(gold.answer))
    for fact in gold.facts:
        if fact and str(fact) not in out:
            out.append(str(fact))
    return tuple(out)


#: Clock literals are masked before scanning numeric golds: a gold value of 45
#: would otherwise "match" the minute field of a synthetic log timestamp, which
#: is not a statement of the value. Time golds (which contain a colon) are
#: scanned against the unmasked text, so a real clock-answer leak still fails.
_CLOCK_RE = re.compile(r"(?<![\d:])\d{1,2}:\d{2}(?::\d{2})?(?![\d:])")


def _mask_clocks(low: str) -> str:
    return _CLOCK_RE.sub(lambda m: " " * len(m.group(0)), low)


def find_gold_leaks(text: str, gold: "GoldRecord") -> Tuple[str, ...]:
    """Return the gold strings found in ``text`` (case-insensitive, boundary aware)."""
    low = str(text).casefold()
    masked = _mask_clocks(low)
    leaks = []
    for gold_str in gold_strings(gold):
        needle = re.sub(r"\s+", " ", gold_str).strip().casefold()
        if not needle:
            continue
        haystack = low if ":" in needle else masked
        if _gold_pattern(needle).search(haystack):
            leaks.append(gold_str)
    return tuple(leaks)


def assert_gold_absent(text: str, gold: "GoldRecord") -> None:
    """Raise :class:`GoldLeakError` when any gold string is present in ``text``."""
    leaks = find_gold_leaks(text, gold)
    if leaks:
        raise GoldLeakError(
            "gold text present in visible payload (item %s): %r"
            % (getattr(gold, "fingerprint", "?"), leaks)
        )


@dataclass(frozen=True)
class GoldProvenance:
    """Where a gold label came from: module, generator, seed, step, rule."""

    module: str
    generator: str
    seed: int
    step: int
    derivation: str
    params: Mapping[str, object] = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {
            "module": self.module,
            "generator": self.generator,
            "seed": int(self.seed),
            "step": int(self.step),
            "derivation": self.derivation,
            "params": dict(self.params),
        }


@dataclass(frozen=True)
class GoldRecord:
    answer: str
    answer_kind: str
    aliases: Tuple[str, ...] = ()
    facts: Tuple[str, ...] = ()
    provenance: Optional[GoldProvenance] = None
    fingerprint: str = ""

    def __post_init__(self) -> None:
        if not self.fingerprint:
            object.__setattr__(self, "fingerprint", gold_fingerprint(self.answer))

    def as_dict(self) -> dict:
        return {
            "answer_kind": self.answer_kind,
            "aliases": list(self.aliases),
            "facts": list(self.facts),
            "fingerprint": self.fingerprint,
            "provenance": self.provenance.as_dict() if self.provenance else None,
        }


@dataclass(frozen=True)
class VisibleItem:
    """Model-visible fields only. Never places a gold text field here."""

    item_id: str
    suite: str
    family: str            # needle | stress
    category: str
    question: str
    needle_text: str
    evidence_markers: Tuple[str, ...]
    answer_kind: str
    distractor_params: Mapping[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class Item:
    visible: VisibleItem
    gold: GoldRecord

    @property
    def item_id(self) -> str:
        return self.visible.item_id

    def as_dict(self) -> dict:
        return {"visible": visible_item_dict(self.visible), "gold": self.gold.as_dict()}


def visible_item_dict(visible: VisibleItem) -> dict:
    """Serialise visible fields only (no gold, no fingerprint)."""
    return {
        "item_id": visible.item_id,
        "suite": visible.suite,
        "family": visible.family,
        "category": visible.category,
        "question": visible.question,
        "needle_text": visible.needle_text,
        "evidence_markers": list(visible.evidence_markers),
        "answer_kind": visible.answer_kind,
        "distractor_params": dict(visible.distractor_params),
    }


# ---------------------------------------------------------------------------
# Model-visible payload + prompt rendering
# ---------------------------------------------------------------------------

VISIBLE_PAYLOAD_KEYS: Tuple[str, ...] = ("context", "question", "answer_kind")

SYSTEM_PROMPT = (
    "You answer questions about a long working context.\n"
    "Use only the CONTEXT block; do not use outside knowledge.\n"
    "If the context does not contain the answer, reply exactly: ABSTAIN\n"
    "Put the final answer on the last line of your reply."
)

FORMAT_HINTS: Dict[str, str] = {
    "numeric": "Reply with the number only, on the last line.",
    "time": "Reply with a 24h clock time as HH:MM, on the last line.",
    "code": "Reply with the exact value only, on the last line.",
    "multi": "Reply with all values separated by commas, on the last line.",
}

JSON_INSTRUCTION = 'Return a JSON object with the single key "answer".'


def visible_payload(visible: VisibleItem, context_text: str) -> dict:
    """Build the model-visible payload.

    Deliberately takes only visible inputs: no Item, no GoldRecord. This is the
    structural half of the gold-isolation guarantee.
    """
    return {
        "context": context_text,
        "question": visible.question,
        "answer_kind": visible.answer_kind,
    }


def render_messages(payload: dict, json_style: bool = False) -> List[dict]:
    """Render a visible payload into chat messages."""
    extra = [FORMAT_HINTS.get(payload.get("answer_kind", ""), "")]
    if json_style:
        extra.append(JSON_INSTRUCTION)
    tail = "\n".join(line for line in extra if line)
    user = "CONTEXT:\n%s\n\nQUESTION:\n%s\n\n%s" % (
        payload["context"], payload["question"], tail)
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user},
    ]


# ---------------------------------------------------------------------------
# Item construction
# ---------------------------------------------------------------------------

_NEEDLE_GENERATORS = {
    "exact_lexical": "_build_exact_lexical",
    "semantic_paraphrase": "_build_semantic_paraphrase",
    "low_overlap_semantic": "_build_low_overlap_semantic",
    "multi_fact": "_build_multi_fact",
}

NEEDLE_DERIVATIONS = {
    "exact_lexical": "append literal suffix -final to the stated rollover code",
    "semantic_paraphrase": "start time + drain minutes + verification minutes (24h wrap)",
    "low_overlap_semantic": "double the stated checkpoint-one value",
    "multi_fact": "combine the three derived facts, comma separated",
}


def item_from_needle(needle: Needle, suite: str, index: int, item_seed: int,
                     item_id: Optional[str] = None) -> Item:
    """Wrap a Needle into an Item with a separated gold record."""
    visible = VisibleItem(
        item_id=item_id or "%s-%s-%03d" % (suite, needle.kind, index),
        suite=suite,
        family="needle",
        category=needle.kind,
        question=needle.question,
        needle_text=needle.needle_text,
        evidence_markers=tuple(needle.evidence_markers),
        answer_kind=needle.answer_kind,
        distractor_params=needle.visible_params,
    )
    gold = GoldRecord(
        answer=needle.answer,
        answer_kind=needle.answer_kind,
        aliases=tuple(needle.aliases),
        facts=tuple(needle.facts),
        provenance=GoldProvenance(
            module="research.context_rot.needles",
            generator=_NEEDLE_GENERATORS[needle.kind],
            seed=item_seed,
            step=index,
            derivation=NEEDLE_DERIVATIONS[needle.kind],
            params=dict(needle.params),
        ),
    )
    item = Item(visible=visible, gold=gold)
    assert_gold_absent(visible.question + "\n" + visible.needle_text, gold)
    return item


def make_item(*, item_id: str, suite: str, family: str, category: str, question: str,
              needle_text: str, evidence_markers: Sequence[str], answer_kind: str,
              answer: str, aliases: Sequence[str] = (), facts: Sequence[str] = (),
              distractor_params: Optional[Mapping[str, object]] = None,
              provenance: GoldProvenance) -> Item:
    """Generic item builder used by :mod:`research.context_rot.stress`."""
    missing = [m for m in evidence_markers if m not in needle_text]
    if missing:
        raise ValueError("evidence marker not in needle text: %r" % (missing,))
    visible = VisibleItem(
        item_id=item_id,
        suite=suite,
        family=family,
        category=category,
        question=question,
        needle_text=needle_text,
        evidence_markers=tuple(evidence_markers),
        answer_kind=answer_kind,
        distractor_params=dict(distractor_params or {}),
    )
    gold = GoldRecord(answer=answer, answer_kind=answer_kind, aliases=tuple(aliases),
                      facts=tuple(facts), provenance=provenance)
    item = Item(visible=visible, gold=gold)
    assert_gold_absent(visible.question + "\n" + visible.needle_text, gold)
    return item


# ---------------------------------------------------------------------------
# Context assembly and truncation
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class InstanceConfig:
    """One context configuration: length target, position, distractors, budget."""

    context_target_len: int = 2000          # ESTIMATED tokens
    chars_per_token: float = 4.0
    evidence_position: str = "middle"
    distractor_kind: str = "none"
    max_prompt_tokens: Optional[int] = None
    truncate_strategy: str = "head_tail"

    def __post_init__(self) -> None:
        if int(self.context_target_len) <= 0:
            raise ValueError("context_target_len must be positive")
        resolve_position(self.evidence_position)
        if self.distractor_kind not in DISTRACTOR_KINDS:
            raise ValueError("unknown distractor kind: %r" % (self.distractor_kind,))
        if self.truncate_strategy not in TRUNCATE_STRATEGIES:
            raise ValueError("unknown truncate strategy: %r" % (self.truncate_strategy,))

    def key(self) -> str:
        return "len=%d|pos=%s|dis=%s|max=%s|trunc=%s" % (
            self.context_target_len, self.evidence_position, self.distractor_kind,
            self.max_prompt_tokens, self.truncate_strategy)

    def as_dict(self) -> dict:
        return {
            "context_target_len": self.context_target_len,
            "chars_per_token": self.chars_per_token,
            "evidence_position": self.evidence_position,
            "distractor_kind": self.distractor_kind,
            "max_prompt_tokens": self.max_prompt_tokens,
            "truncate_strategy": self.truncate_strategy,
        }


TRUNCATE_STRATEGIES: Tuple[str, ...] = ("head_tail", "head_only", "tail_only")

MAX_LEAK_ATTEMPTS = 6


@dataclass(frozen=True)
class ContextBundle:
    text: str
    item_id: str
    config_key: str
    context_target_len: int
    context_chars: int
    block_count: int
    truncated: bool
    tokens_original: int
    tokens_final: int
    truncate_strategy: str
    evidence_survived: bool
    missing_markers: Tuple[str, ...]
    evidence_position: str
    distractor_kind: str
    leak_attempts: int
    distractor_meta: Tuple[dict, ...] = ()
    token_estimator: str = TOKEN_ESTIMATOR
    token_is_estimate: bool = True

    def as_dict(self) -> dict:
        return {
            "item_id": self.item_id,
            "config_key": self.config_key,
            "context_target_len": self.context_target_len,
            "context_chars": self.context_chars,
            "block_count": self.block_count,
            "truncated": self.truncated,
            "tokens_original": self.tokens_original,
            "tokens_final": self.tokens_final,
            "truncate_strategy": self.truncate_strategy,
            "evidence_survived": self.evidence_survived,
            "missing_markers": list(self.missing_markers),
            "evidence_position": self.evidence_position,
            "distractor_kind": self.distractor_kind,
            "leak_attempts": self.leak_attempts,
            "distractor_meta": [dict(d) for d in self.distractor_meta],
            "token_estimator": self.token_estimator,
            "token_is_estimate": self.token_is_estimate,
        }


def truncate_text(text: str, budget_tokens: int, strategy: str = "head_tail") -> Tuple[str, dict]:
    """Shorten ``text`` to at most ``budget_tokens`` estimated tokens.

    Line-aware: cuts on a newline boundary where possible so the result still
    reads as text. Never silently drops text: the caller records the result.
    """
    if strategy not in TRUNCATE_STRATEGIES:
        raise ValueError("unknown truncate strategy: %r" % (strategy,))
    original_tokens = estimate_tokens(text)
    if original_tokens <= budget_tokens:
        return text, {
            "strategy": strategy,
            "truncated": False,
            "tokens_original": original_tokens,
            "tokens_final": original_tokens,
            "chars_original": len(text),
            "chars_final": len(text),
        }
    budget_chars = max(40, int(budget_tokens * 4.0))
    short = text
    for _ in range(24):
        if strategy == "head_only":
            candidate = text[:budget_chars]
        elif strategy == "tail_only":
            candidate = text[-budget_chars:]
        else:
            head = int(budget_chars * 0.6)
            tail = budget_chars - head
            candidate = text[:head] + "\n\n[... elided ...]\n\n" + text[-tail:]
        cut = candidate.rfind("\n")
        if 0 < cut and len(candidate) > 80:
            candidate = candidate[:cut]
        short = candidate
        if estimate_tokens(short) <= budget_tokens:
            break
        budget_chars = int(budget_chars * 0.93)
        if budget_chars < 80:
            break
    return short, {
        "strategy": strategy,
        "truncated": True,
        "tokens_original": original_tokens,
        "tokens_final": estimate_tokens(short),
        "chars_original": len(text),
        "chars_final": len(short),
    }


def _assemble_blocks(item: Item, config: InstanceConfig,
                     attempt: int) -> Tuple[List[Tuple[str, str]], List[Distractor]]:
    fraction = resolve_position(config.evidence_position)
    visible = item.visible
    distractors: List[Distractor] = build_distractors(
        config.distractor_kind, visible.answer_kind, visible.distractor_params,
        seed=_item_seed(item, attempt), forbidden=gold_strings(item.gold))
    # Evidence and distractors count against the length target.
    inserted_chars = len(visible.needle_text) + sum(len(d.text) + 2 for d in distractors)
    target_chars = max(240, int(config.context_target_len * config.chars_per_token)
                       - inserted_chars)
    generator = CorpusGenerator(seed=_item_seed(item, attempt) + 17,
                                number_shift=attempt * 1000)
    blocks: List[Tuple[str, str]] = []
    produced = 0
    inserted = False
    for chunk in generator.chunks(target_chars):
        if not inserted and produced >= target_chars * fraction:
            blocks.append(("needle", visible.needle_text))
            for distractor in distractors:
                blocks.append((distractor.kind, distractor.text))
            inserted = True
        blocks.append((chunk.register, chunk.text))
        produced += len(chunk.text) + 2
    if not inserted:
        blocks.append(("needle", visible.needle_text))
        for distractor in distractors:
            blocks.append((distractor.kind, distractor.text))
    return blocks, distractors


def _item_seed(item: Item, attempt: int) -> int:
    base = int(item.gold.provenance.seed) if item.gold.provenance else 0
    digest = hashlib.sha256(item.item_id.encode("utf-8")).hexdigest()[:8]
    return base * 1000 + int(digest, 16) % 9973 + attempt * 7919


def build_context(item: Item, config: InstanceConfig) -> ContextBundle:
    """Assemble the visible context for one item/config pair.

    Generation retries with shifted synthetic numbers while any gold string is
    detectable in the text (gold-leak guard). Truncation, when the prompt budget
    forces it, is recorded together with evidence survival.
    """
    attempts = 0
    blocks: List[Tuple[str, str]] = []
    distractors: List[Distractor] = []
    text = ""
    for attempt in range(MAX_LEAK_ATTEMPTS):
        attempts = attempt + 1
        blocks, distractors = _assemble_blocks(item, config, attempt)
        text = "\n\n".join(body for _, body in blocks)
        if not find_gold_leaks(text, item.gold):
            break
    else:
        raise GoldLeakError("could not build a leak-free context for item %s" % item.item_id)

    tokens_original = estimate_tokens(text)
    truncated = False
    truncate_info: dict = {
        "strategy": config.truncate_strategy,
        "truncated": False,
        "tokens_original": tokens_original,
        "tokens_final": tokens_original,
        "chars_original": len(text),
        "chars_final": len(text),
    }
    if config.max_prompt_tokens is not None:
        overhead = estimate_messages_tokens(
            render_messages(visible_payload(item.visible, ""), json_style=False))
        budget = int(config.max_prompt_tokens) - overhead
        if estimate_tokens(text) > budget:
            text, truncate_info = truncate_text(text, max(40, budget),
                                                config.truncate_strategy)
            truncated = bool(truncate_info["truncated"])

    missing = tuple(m for m in item.visible.evidence_markers if m not in text)
    survived = not missing
    distractor_meta = tuple(d.as_dict() for d in distractors)
    return ContextBundle(
        text=text,
        item_id=item.item_id,
        config_key=config.key(),
        context_target_len=config.context_target_len,
        context_chars=len(text),
        block_count=len(blocks),
        truncated=truncated,
        tokens_original=int(truncate_info["tokens_original"]),
        tokens_final=int(truncate_info["tokens_final"]),
        truncate_strategy=config.truncate_strategy,
        evidence_survived=bool(survived),
        missing_markers=tuple(missing),
        evidence_position=config.evidence_position,
        distractor_kind=config.distractor_kind,
        leak_attempts=attempts,
        distractor_meta=distractor_meta,
    )


def instance_id(item: Item, config: InstanceConfig) -> str:
    return "%s@%s" % (item.item_id, config.key())


# ---------------------------------------------------------------------------
# Suites
# ---------------------------------------------------------------------------

SUITES = ("needle-smoke", "needles", "stress", "mixed")

SUITE_DESCRIPTIONS: Dict[str, str] = {
    "needle-smoke": "4 items, one per needle family",
    "needles": "8 items, two per needle family",
    "stress": "20 stress categories A..T, one item each",
    "mixed": "needle-smoke plus stress",
}


def generate_suite(name: str, seed: int, limit: Optional[int] = None,
                   per_kind: Optional[int] = None) -> List[Item]:
    """Generate suite items deterministically for ``seed``."""
    seed = int(seed)
    if name == "needle-smoke":
        count = per_kind or 1
        items = [item_from_needle(needle, name, i, seed)
                 for i, needle in enumerate(iter_needles(seed, count))]
    elif name == "needles":
        count = per_kind or 2
        items = [item_from_needle(needle, name, i, seed)
                 for i, needle in enumerate(iter_needles(seed, count))]
    elif name == "stress":
        from .stress import build_stress_items
        items = build_stress_items(seed, limit=None)
    elif name == "mixed":
        count = per_kind or 1
        needle_items = [item_from_needle(needle, name, i, seed)
                        for i, needle in enumerate(iter_needles(seed, count))]
        from .stress import build_stress_items
        items = needle_items + build_stress_items(seed, limit=None)
    else:
        raise ValueError("unknown suite: %r (expected one of %s)"
                         % (name, ", ".join(SUITES)))
    if limit is not None:
        items = items[: int(limit)]
    return items


def suite_plan(name: str, seed: int, configs: Sequence[InstanceConfig],
               limit: Optional[int] = None) -> dict:
    """Dry-run description of what a run would execute."""
    items = generate_suite(name, seed, limit=limit)
    cells = []
    for config in configs:
        for item in items:
            cells.append({"instance_id": instance_id(item, config),
                          "item_id": item.item_id,
                          "category": item.visible.category,
                          "config_key": config.key()})
    return {
        "suite": name,
        "seed": int(seed),
        "items": [i.item_id for i in items],
        "item_count": len(items),
        "configs": [c.as_dict() for c in configs],
        "cell_count": len(cells),
        "cells": cells,
    }
