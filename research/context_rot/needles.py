"""Needle construction.

Four needle families, all with *derived* gold values: the answer never appears
verbatim in the model-visible text, it must be computed (suffix rule, clock
arithmetic, doubling) or assembled from several facts. That keeps the gold
label out of the context by construction and is enforced again by the leak
guard in :mod:`research.context_rot.tasks`.

Every needle carries ``evidence_markers``: short visible substrings that prove
the evidence survived truncation or compaction. Markers are visible text, never
gold text.
"""

import random
from dataclasses import dataclass, field
from typing import Dict, Iterable, Tuple

NEEDLE_KINDS: Tuple[str, ...] = (
    "exact_lexical",
    "semantic_paraphrase",
    "low_overlap_semantic",
    "multi_fact",
)

NEEDLE_DESCRIPTIONS: Dict[str, str] = {
    "exact_lexical": "exact identifier lookup plus a stated suffix rule",
    "semantic_paraphrase": "same fact, different wording; answer derived by clock arithmetic",
    "low_overlap_semantic": "abstract wording, few shared content words; answer derived by doubling",
    "multi_fact": "three derived facts must be combined in one answer",
}

_CODE_LETTERS = "ABCDEFGHJKLMNPQRSTUVWXYZ"

#: Parameter names that hold gold text; never exposed to the visible layer.
_GOLD_PARAM_KEYS = frozenset({"end", "checkpoint_two"})
_CODE_DIGITS = "0123456789"


@dataclass(frozen=True)
class Needle:
    kind: str
    question: str
    needle_text: str
    answer: str                      # gold, primary form
    answer_kind: str                 # code | time | numeric | multi
    aliases: Tuple[str, ...] = ()    # accepted gold surface variants
    facts: Tuple[str, ...] = ()      # gold sub-facts for multi_fact
    evidence_markers: Tuple[str, ...] = ()
    params: Dict[str, object] = field(default_factory=dict)

    @property
    def visible_params(self) -> Dict[str, object]:
        """Params safe to expose: gold-bearing keys are dropped explicitly."""
        return {k: v for k, v in self.params.items() if k not in _GOLD_PARAM_KEYS}

    def marker_survival(self, text: str) -> Tuple[bool, Tuple[str, ...]]:
        """True when every marker is present in ``text``. Returns missing ones."""
        missing = tuple(m for m in self.evidence_markers if m not in text)
        return (not missing, missing)


def _code(rng: random.Random) -> str:
    letters = "".join(rng.choice(_CODE_LETTERS) for _ in range(2))
    digits = "".join(rng.choice(_CODE_DIGITS) for _ in range(4))
    return "%s-%s" % (letters, digits)


def clock_after(start_hhmm: str, minutes: int) -> str:
    """Add minutes to an HH:MM clock value with 24h wraparound."""
    hh, mm = (int(x) for x in start_hhmm.split(":"))
    total = (hh * 60 + mm + minutes) % (24 * 60)
    return "%02d:%02d" % (total // 60, total % 60)


def _build_exact_lexical(seed: int) -> Needle:
    rng = random.Random(seed + 101)
    shard = rng.randrange(10, 40)
    code = _code(rng)
    other = _code(rng)
    while other == code:
        other = _code(rng)
    needle_text = (
        "ROLLOVER NOTICE (shard s%d)\n"
        "  owner: storage group\n"
        "  rollover code: %s\n"
        "  adjacent shard code: %s\n"
        "  reporting rule: the value reported for shard s%d is its rollover code "
        "followed by the literal suffix \"-final\".\n"
    ) % (shard, code, other, shard)
    question = (
        "Using the rollover notice, what exact value must be reported for shard s%d? "
        "Reply with the value only." % shard
    )
    return Needle(
        kind="exact_lexical",
        question=question,
        needle_text=needle_text,
        answer="%s-final" % code,
        answer_kind="code",
        aliases=(code,),
        evidence_markers=("rollover code: %s" % code, "literal suffix \"-final\""),
        params={"shard": shard, "code": code, "adjacent_code": other, "rule": "suffix -final"},
    )


def _build_semantic_paraphrase(seed: int) -> Needle:
    rng = random.Random(seed + 202)
    start_h = rng.randrange(19, 24)
    start_m = rng.choice((5, 10, 15, 20, 25, 30, 35, 40, 45, 50))
    start = "%02d:%02d" % (start_h, start_m)
    drain = rng.randrange(11, 25)
    verify = rng.randrange(4, 12)
    end = clock_after(start, drain + verify)
    needle_text = (
        "Operations bulletin 44b\n"
        "Operators must hold the write lock starting at %s. The cache drain that "
        "runs under the lock needs %d minutes, and a further %d minutes of "
        "verification is required before the lock may be released.\n"
    ) % (start, drain, verify)
    question = (
        "According to the bulletin, at what clock time (HH:MM) may the lock be "
        "released? Reply with the time only."
    )
    return Needle(
        kind="semantic_paraphrase",
        question=question,
        needle_text=needle_text,
        answer=end,
        answer_kind="time",
        aliases=(end + " UTC", "at " + end),
        evidence_markers=("starting at %s" % start, "needs %d minutes" % drain,
                          "a further %d minutes" % verify),
        params={"start": start, "drain_min": drain, "verify_min": verify, "end": end},
    )


def _build_low_overlap_semantic(seed: int) -> Needle:
    rng = random.Random(seed + 303)
    first = rng.randrange(11, 40)
    second = first * 2
    needle_text = (
        "Runbook excerpt, section 9 (intake limiter)\n"
        "The limiter fires at the second checkpoint. Checkpoint one is fixed at %d. "
        "Checkpoint two is not listed directly: the document states only that it is "
        "the doubled value of checkpoint one.\n"
    ) % first
    question = (
        "At what value does the intake limiter fire? Reply with the number only."
    )
    return Needle(
        kind="low_overlap_semantic",
        question=question,
        needle_text=needle_text,
        answer=str(second),
        answer_kind="numeric",
        aliases=(_spell(second), "%d units" % second),
        evidence_markers=("fixed at %d" % first, "doubled value of checkpoint one"),
        params={"checkpoint_one": first, "checkpoint_two": second, "rule": "double"},
    )


_SPELL_SMALL = {
    0: "zero", 1: "one", 2: "two", 3: "three", 4: "four", 5: "five", 6: "six",
    7: "seven", 8: "eight", 9: "nine", 10: "ten", 11: "eleven", 12: "twelve",
    13: "thirteen", 14: "fourteen", 15: "fifteen", 16: "sixteen",
    17: "seventeen", 18: "eighteen", 19: "nineteen", 20: "twenty",
    30: "thirty", 40: "forty", 50: "fifty", 60: "sixty", 70: "seventy",
    80: "eighty", 90: "ninety",
}


def _spell(n: int) -> str:
    """Spell an integer below 100 (best effort for other values)."""
    if n in _SPELL_SMALL:
        return _SPELL_SMALL[n]
    if 20 < n < 100:
        tens, ones = divmod(n, 10)
        return "%s-%s" % (_SPELL_SMALL[tens * 10], _SPELL_SMALL[ones])
    return str(n)


def _build_multi_fact(seed: int) -> Needle:
    lex = _build_exact_lexical(seed + 11)
    par = _build_semantic_paraphrase(seed + 12)
    low = _build_low_overlap_semantic(seed + 13)
    needle_text = "\n".join(
        [
            "Three unrelated notes were pasted into one scratch buffer.",
            "",
            lex.needle_text.strip(),
            "",
            par.needle_text.strip(),
            "",
            low.needle_text.strip(),
        ]
    )
    facts = (low.answer, par.answer, lex.answer)
    question = (
        "Combine all three notes and report three values separated by commas: "
        "(1) the intake limiter value, (2) the time the write lock may be released, "
        "(3) the reported rollover value for shard s%d." % lex.params["shard"]
    )
    return Needle(
        kind="multi_fact",
        question=question,
        needle_text=needle_text,
        answer="|".join(facts),
        answer_kind="multi",
        aliases=(),
        facts=facts,
        evidence_markers=tuple(
            lex.evidence_markers + par.evidence_markers + low.evidence_markers
        ),
        params={
            "shard": lex.params["shard"],
            "code": lex.params["code"],
            "start": par.params["start"],
            "checkpoint_one": low.params["checkpoint_one"],
        },
    )


_BUILDERS = {
    "exact_lexical": _build_exact_lexical,
    "semantic_paraphrase": _build_semantic_paraphrase,
    "low_overlap_semantic": _build_low_overlap_semantic,
    "multi_fact": _build_multi_fact,
}


def make_needle(kind: str, seed: int) -> Needle:
    """Build one needle of ``kind``. Same (kind, seed) -> identical Needle."""
    if kind not in _BUILDERS:
        raise ValueError("unknown needle kind: %r (expected one of %s)"
                         % (kind, ", ".join(NEEDLE_KINDS)))
    return _BUILDERS[kind](int(seed))


def iter_needles(seed: int, per_kind: int = 1) -> Iterable[Needle]:
    """Yield ``per_kind`` needles per family, deterministically ordered."""
    for k_index, kind in enumerate(NEEDLE_KINDS):
        for i in range(per_kind):
            yield make_needle(kind, seed + 1000 * k_index + 17 * i)
