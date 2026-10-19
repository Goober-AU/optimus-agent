"""Deterministic filler / distractor text generation.

No network access, no wall clock, no global RNG. Everything derives from an
explicit seed so that ``generate(seed) == generate(seed)`` byte for byte on the
same interpreter (CPython 3.11 ``random.Random`` streams are stable).

Registers: prose, logs, code, json, table. Real long contexts mix registers,
which is why the filler mixes them too.
"""

import math
import random
from dataclasses import dataclass
from typing import Iterable, List, Sequence

# ---------------------------------------------------------------------------
# Token estimation (requirement 5)
# ---------------------------------------------------------------------------

#: Identifier for the heuristic. Stamped into every record so a reader can tell
#: an estimate from a tokenizer measurement.
TOKEN_ESTIMATOR = "estimate_v1:ceil(chars/4 + digits/12 + newlines/2)"

#: Human-readable description used by the CLI, README and reports.
TOKEN_ESTIMATOR_NOTE = (
    "Token counts are ESTIMATES, never tokenizer measurements: "
    "base = chars/4.0, plus digits/12.0 (ids, hex, line numbers pack denser), "
    "plus newlines/2.0 (per-line structural overhead), rounded up. "
    "API-reported usage is stored separately as usage.prompt_tokens."
)


def estimate_tokens_detailed(text: str) -> dict:
    """Return the token estimate plus its components. Always flagged as estimate."""
    if not text:
        return {
            "text_tokens": 0,
            "chars": 0,
            "digits": 0,
            "newlines": 0,
            "method": TOKEN_ESTIMATOR,
            "is_estimate": True,
        }
    chars = len(text)
    digits = sum(1 for c in text if c.isdigit())
    newlines = text.count("\n")
    raw = chars / 4.0 + digits / 12.0 + newlines / 2.0
    return {
        "text_tokens": int(math.ceil(raw)),
        "chars": chars,
        "digits": digits,
        "newlines": newlines,
        "method": TOKEN_ESTIMATOR,
        "is_estimate": True,
    }


def estimate_tokens(text: str) -> int:
    """Documented heuristic token estimate. See TOKEN_ESTIMATOR_NOTE."""
    return estimate_tokens_detailed(text)["text_tokens"]


def estimate_messages_tokens(messages: Sequence[dict]) -> int:
    """Estimate a chat prompt: message bodies plus fixed per-message framing."""
    total = 0
    for msg in messages:
        total += estimate_tokens(str(msg.get("content", "")))
        total += estimate_tokens(str(msg.get("role", "")))
        total += 4  # role/separator framing allowance
    return total


# ---------------------------------------------------------------------------
# Deterministic vocabulary
# ---------------------------------------------------------------------------

_WORDS = (
    "deployment pipeline shard replica latency throughput operator window "
    "ingest rotate checksum manifest quota lease backoff probe rollout "
    "registry artifact upstream cluster ingress egress buffer snapshot "
    "directive baseline cadence handoff milestone segment ledger audit "
    "ticket batch queue payload retry cutover drain throttle"
).split()

_FILLER = (
    "the a of and to in for with on by from that this which it is was were "
    "before after during while since under over between into across"
).split()

_TOPICS = (
    "storage tiering", "release gating", "cache warmup", "schema drift",
    "credential rotation", "blue/green cutover", "index compaction",
    "trace sampling", "quota arbitration", "incremental backup",
    "log shipping", "image promotion", "canary weighting", "clock skew",
)

_SUBJECTS = (
    "the platform team", "the on-call engineer", "the release captain",
    "the storage group", "the network squad", "the audit rota",
)

_VERBS = (
    "confirmed", "recorded", "revisited", "deferred", "scheduled",
    "rejected", "annotated", "validated",
)

_OBJECTS = (
    "the rollout checklist", "the migration plan", "the replica set",
    "the retry policy", "the retention table", "the access review",
    "the alert route", "the failover drill",
)

_LEVELS = ("INFO", "WARN", "DEBUG", "ERROR")
_SERVICES = ("ingest", "indexer", "gateway", "compactor", "scheduler", "audit", "relay")
_SHARDS = ("s0", "s1", "s2", "s3", "s4", "s5", "s6", "s7", "s8", "s9")

#: Date base for fake log timestamps; kept constant so output is clock-free.
_LOG_DAY = "2026-10-"


@dataclass(frozen=True)
class Chunk:
    """One filler block. ``ordinal`` is the deterministic emission order."""

    ordinal: int
    register: str
    text: str

    def as_dict(self) -> dict:
        return {"ordinal": self.ordinal, "register": self.register, "chars": len(self.text)}


def _sentence(rng: random.Random) -> str:
    """Grammatical filler sentence; structure varies but words stay generic."""
    topic = rng.choice(_TOPICS)
    subject = rng.choice(_SUBJECTS)
    build = rng.random()
    if build < 0.40:
        return "%s %s %s during %s." % (
            subject.capitalize(), rng.choice(_VERBS), rng.choice(_OBJECTS), topic)
    if build < 0.70:
        return "After %s, %s %s %s and moved on to %s." % (
            topic, subject, rng.choice(_VERBS), rng.choice(_OBJECTS), rng.choice(_TOPICS))
    if build < 0.88:
        return "%s stayed %s while the %s team reviewed %s." % (
            rng.choice(_OBJECTS).capitalize(), rng.choice(_FILLER),
            rng.choice(_WORDS), topic)
    return "The %s %s was %s before the %s window, so %s %s it." % (
        rng.choice(_WORDS), rng.choice(_WORDS), rng.choice(_VERBS),
        rng.choice(_WORDS), subject, rng.choice(_VERBS))


def _prose(rng: random.Random, target_chars: int, number_shift: int = 0) -> str:
    out: List[str] = []
    size = 0
    while size < target_chars:
        n = rng.randrange(3, 7)
        para = " ".join(_sentence(rng) for _ in range(n))
        out.append(para)
        size += len(para) + 2
    return "\n\n".join(out)


def _timestamp(rng: random.Random, number_shift: int) -> str:
    day = rng.randrange(1, 10)  # single-digit days avoid colliding with 2-digit gold values
    hour = rng.randrange(0, 24)
    minute = rng.randrange(0, 60)
    second = rng.randrange(0, 60)
    return "%s%dT%02d:%02d:%02dZ" % (_LOG_DAY, day, hour, minute, second)


def _logs(rng: random.Random, target_chars: int, number_shift: int) -> str:
    out: List[str] = []
    size = 0
    while size < target_chars:
        line = "%s %-5s svc=%s shard=%s step=%s elapsed_ms=%d attempt=%d msg=%s" % (
            _timestamp(rng, number_shift),
            rng.choice(_LEVELS),
            rng.choice(_SERVICES),
            rng.choice(_SHARDS),
            rng.choice(("prepare", "apply", "verify", "flush", "seal", "rollback")),
            rng.randrange(12, 4800) + number_shift,
            rng.randrange(1, 6),
            rng.choice(_WORDS) + "-" + rng.choice(_WORDS),
        )
        out.append(line)
        size += len(line) + 1
    return "\n".join(out)


def _code(rng: random.Random, target_chars: int, number_shift: int) -> str:
    out: List[str] = []
    size = 0
    while size < target_chars:
        fn = "fn_%s_%s" % (rng.choice(_WORDS), rng.choice(_WORDS))
        limit = rng.randrange(4, 40) + number_shift
        body = [
            "def %s(items, limit=%d):" % (fn, limit),
            "    # %s guard" % rng.choice(_TOPICS),
            "    kept = []",
            "    for item in items:",
            "        if item.get(\"%s\") is None:" % rng.choice(_WORDS),
            "            continue",
            "        if len(kept) >= limit:",
            "            break",
            "        kept.append(item)",
            "    return kept[:%d]" % rng.randrange(1, 9),
        ]
        block = "\n".join(body)
        out.append(block)
        size += len(block) + 2
    return "\n\n".join(out)


def _json(rng: random.Random, target_chars: int, number_shift: int) -> str:
    out: List[str] = []
    size = 0
    while size < target_chars:
        block = "{\n" + "\n".join(
            [
                "  \"service\": \"%s\"," % rng.choice(_SERVICES),
                "  \"shard\": \"%s\"," % rng.choice(_SHARDS),
                "  \"retries\": %d," % (rng.randrange(1, 9)),
                "  \"interval_ms\": %d," % (rng.randrange(10, 900) + number_shift),
                "  \"mode\": \"%s\"," % rng.choice(("strict", "lenient", "shadow")),
                "  \"owner\": \"%s\"" % rng.choice(_SUBJECTS),
            ]
        ) + "\n}"
        out.append(block)
        size += len(block) + 2
    return "\n".join(out)


def _table(rng: random.Random, target_chars: int, number_shift: int) -> str:
    rows = ["| service | shard | window | state |", "| --- | --- | --- | --- |"]
    size = sum(len(r) + 1 for r in rows)
    while size < target_chars:
        row = "| %s | %s | %d | %s |" % (
            rng.choice(_SERVICES),
            rng.choice(_SHARDS),
            rng.randrange(5, 240) + number_shift,
            rng.choice(("open", "sealed", "draining", "queued")),
        )
        rows.append(row)
        size += len(row) + 1
    return "\n".join(rows)


_REGISTER_BUILDERS = {
    "prose": _prose,
    "logs": _logs,
    "code": _code,
    "json": _json,
    "table": _table,
}

REGISTERS = tuple(_REGISTER_BUILDERS)

#: Default register mixing pattern; repeats deterministically.
DEFAULT_REGISTER_CYCLE = ("prose", "logs", "code", "json", "table", "prose", "logs")


class CorpusGenerator:
    """Seeded filler generator.

    ``number_shift`` is a deterministic escape hatch used by the gold-leak guard
    in :mod:`research.context_rot.tasks`: shifting every synthetic number makes a
    digit collision with a derived gold value overwhelmingly unlikely without
    changing the structure of the text.
    """

    def __init__(self, seed: int, number_shift: int = 0,
                 register_cycle: Sequence[str] = DEFAULT_REGISTER_CYCLE):
        self.seed = int(seed)
        self.number_shift = int(number_shift)
        self.register_cycle = tuple(register_cycle) or DEFAULT_REGISTER_CYCLE
        for reg in self.register_cycle:
            if reg not in _REGISTER_BUILDERS:
                raise ValueError("unknown register: %r" % (reg,))
        self._rng = random.Random(self.seed)
        self._ordinal = 0

    def reset(self) -> None:
        self._rng = random.Random(self.seed)
        self._ordinal = 0

    def chunks(self, target_chars: int, block_chars: int = 620) -> Iterable[Chunk]:
        """Yield chunks until ``target_chars`` of text has been emitted."""
        produced = 0
        while produced < target_chars:
            register = self.register_cycle[self._ordinal % len(self.register_cycle)]
            builder = _REGISTER_BUILDERS[register]
            text = builder(self._rng, block_chars, self.number_shift)
            self._ordinal += 1
            produced += len(text) + 2
            yield Chunk(ordinal=self._ordinal, register=register, text=text)

    def text(self, target_chars: int, block_chars: int = 620) -> str:
        return "\n\n".join(chunk.text for chunk in self.chunks(target_chars, block_chars))


def generate_filler(seed: int, target_chars: int, block_chars: int = 620,
                    number_shift: int = 0) -> List[Chunk]:
    """Convenience wrapper: full list of chunks for a seed and size."""
    return list(CorpusGenerator(seed, number_shift).chunks(target_chars, block_chars))
