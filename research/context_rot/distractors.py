"""Distractor injection.

Kinds:
  none                - no distractor blocks
  one                 - a single related note with a harmless extra value
  multiple_related    - three related notes, consistent but noise
  contradictory       - near-miss values asserted as true by stale sources
  irrelevant_filler   - short unrelated filler blocks
  topically_similar   - long documents sharing vocabulary, carrying no value

Every distractor is visible context. It must never contain the gold answer;
near-miss alternates are built from *visible* parameters only (the caller passes
``params`` without gold-bearing keys), and the whole-text leak guard in
:mod:`research.context_rot.tasks` re-checks.
"""

import random
from dataclasses import dataclass
from typing import Dict, List, Mapping, Sequence, Tuple

from .corpus import CorpusGenerator
from .needles import _code, clock_after

DISTRACTOR_KINDS: Tuple[str, ...] = (
    "none",
    "one",
    "multiple_related",
    "contradictory",
    "irrelevant_filler",
    "topically_similar",
)

DISTRACTOR_DESCRIPTIONS: Dict[str, str] = {
    "none": "no distractor material",
    "one": "one related note with a harmless additional value",
    "multiple_related": "three consistent but irrelevant related notes",
    "contradictory": "stale notes asserting near-miss values for the same question",
    "irrelevant_filler": "short unrelated filler blocks",
    "topically_similar": "long documents on the same topic that carry no value",
}


@dataclass(frozen=True)
class Distractor:
    kind: str
    text: str
    ordinal: int
    contradicts: bool = False
    label: str = ""

    def as_dict(self) -> dict:
        return {
            "kind": self.kind,
            "ordinal": self.ordinal,
            "chars": len(self.text),
            "contradicts": self.contradicts,
            "label": self.label,
        }


def _forbidden(text: str, forbidden: Sequence[str]) -> bool:
    low = str(text).casefold()
    return any(str(item).casefold() in low for item in forbidden if item)


def _near_miss_variant(answer_kind: str, params: Mapping[str, object], seed: int,
                       salt: int) -> Tuple[str, str, str]:
    rng = random.Random(seed + 909 + 17 * salt)
    if answer_kind == "code":
        code = str(params.get("code", "AB-0000"))
        base = str(params.get("adjacent_code", "CD-1111"))
        if salt and salt % 3 == 0:
            base = _code(rng)
        return ("%s-final" % base, "%s" % code, "stale index entry")
    if answer_kind == "time":
        start = str(params.get("start", "20:00"))
        drain = int(params.get("drain_min", 15))
        return (clock_after(start, drain + 1 + salt),
                clock_after(start, 2 + salt), "pre-verification estimate")
    if answer_kind == "numeric":
        first = int(params.get("checkpoint_one", 10))
        return (str(first + 1 + salt), str(max(2, first - 1 - salt)),
                "first checkpoint value")
    # multi: wrong on every component, derived from visible parameters only
    first = int(params.get("checkpoint_one", 10))
    start = str(params.get("start", "20:00"))
    wrong = [str(first + 2 + salt), clock_after(start, 3 + salt),
             "%s-final" % _code(rng)]
    return (", ".join(wrong), wrong[0], "summary of superseded values")


def _near_miss(answer_kind: str, params: Mapping[str, object], seed: int,
               forbidden: Sequence[str] = ()) -> Tuple[str, str, str]:
    """Two plausible wrong values for this item plus a short description.

    Candidates that collide with a gold string are rejected, so a contradictory
    distractor can never accidentally assert the gold answer.
    """
    for salt in range(40):
        primary, secondary, label = _near_miss_variant(answer_kind, params, seed, salt)
        if not _forbidden(primary, forbidden) and not _forbidden(secondary, forbidden):
            return primary, secondary, label
    raise ValueError("could not build a non-gold contradictory distractor")


def _contradictory(answer_kind: str, params: Mapping[str, object], seed: int,
                   forbidden: Sequence[str] = ()) -> List[Distractor]:
    wrong_primary, wrong_secondary, label = _near_miss(answer_kind, params, seed, forbidden)
    blocks = [
        "SUPERSEDED NOTE (schema v1, retained for audit)\n"
        "An earlier revision of this document listed the %s as %s. That revision "
        "predates the current rule and was never reconciled.\n" % (label, wrong_primary),
        "Reviewer comment (2025-11-02, unresolved)\n"
        "I could not reproduce the %s; my draft copy still shows %s. Treat this "
        "comment as unverified.\n" % (label, wrong_secondary),
    ]
    return [
        Distractor(kind="contradictory", text=text, ordinal=i, contradicts=True,
                   label="superseded_%d" % i)
        for i, text in enumerate(blocks)
    ]


def _related(params: Mapping[str, object], seed: int, count: int, kind: str) -> List[Distractor]:
    rng = random.Random(seed + 707)
    shard = params.get("shard")
    out = []
    for i in range(count):
        topic = rng.choice(("capacity review", "on-call handover", "audit follow-up",
                            "vendor sync", "schema review"))
        out.append(Distractor(
            kind=kind,
            text=("Related note %d (%s): the owner asked whether the material for "
                  "shard s%s still applies, and confirmed that nothing in this note "
                  "changes any recorded value.\n" % (i + 1, topic,
                                                     shard if shard is not None else "17")),
            ordinal=i,
            label=topic,
        ))
    return out


def _irrelevant(seed: int, count: int = 3) -> List[Distractor]:
    gen = CorpusGenerator(seed + 505, number_shift=0)
    out = []
    for i, chunk in enumerate(gen.chunks(count * 400, block_chars=380)):
        if i >= count:
            break
        out.append(Distractor(kind="irrelevant_filler", text=chunk.text,
                              ordinal=i, label=chunk.register))
    return out


def _topically_similar(params: Mapping[str, object], seed: int, count: int = 3) -> List[Distractor]:
    rng = random.Random(seed + 606)
    out = []
    for i in range(count):
        paragraphs = []
        for _ in range(4):
            paragraphs.append(
                "RFC draft %d.%d discusses the limiter and the checkpoint policy in "
                "the abstract: it argues about ordering, ownership and review cadence, "
                "and it deliberately omits every concrete value (%s)."
                % (i, rng.randrange(1, 9), rng.choice(("see appendix", "to be fixed",
                                                       "pending sign-off", "non-normative")))
            )
        text = "Section %d\n%s\n" % (i + 1, "\n\n".join(paragraphs))
        out.append(Distractor(kind="topically_similar", text=text, ordinal=i,
                              label="rfc_draft_%d" % i))
    del params
    return out


def build_distractors(kind: str, answer_kind: str, params: Mapping[str, object],
                      seed: int, forbidden: Sequence[str] = ()) -> List[Distractor]:
    """Return distractor blocks for ``kind``.

    ``params`` must contain visible values only. Deterministic for
    (kind, answer_kind, params, seed).
    """
    if kind not in DISTRACTOR_KINDS:
        raise ValueError("unknown distractor kind: %r (expected one of %s)"
                         % (kind, ", ".join(DISTRACTOR_KINDS)))
    if kind == "none":
        return []
    if kind == "one":
        return _related(params, seed, 1, "one")
    if kind == "multiple_related":
        return _related(params, seed, 3, "multiple_related")
    if kind == "contradictory":
        return _contradictory(answer_kind, params, seed, forbidden)
    if kind == "irrelevant_filler":
        return _irrelevant(seed)
    return _topically_similar(params, seed)
