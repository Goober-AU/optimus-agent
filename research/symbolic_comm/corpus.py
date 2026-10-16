"""Corpus loading and the EVOLVE / DEV / HOLDOUT split.

Primary corpus: research/tasks/msgs_corpus.json (69 typed messages with
plain_english OCC-0 renderings). The corpus generator flagged 18 messages
as holdout; that flag is honored verbatim (never re-randomized). The
remaining 51 messages are split stratified by kind into EVOLVE (evolution
rollouts, fresh group per generation) and DEV (selection), fixed seed.
"""

import json
import pathlib
import random

from contract import AgentCommMessage

WORKTREE = pathlib.Path(__file__).resolve().parents[2]
CORPUS_PATH = WORKTREE / "research" / "tasks" / "msgs_corpus.json"
SEED = 20261016


def load_corpus(path=CORPUS_PATH):
    raw = json.loads(pathlib.Path(path).read_text(encoding="utf-8"))
    items = []
    for entry in raw:
        message = AgentCommMessage.from_payload(entry["payload"])
        items.append({
            "id": entry["id"],
            "holdout": bool(entry.get("holdout", False)),
            "occ0": entry.get("plain_english", ""),
            "payload": entry["payload"],
            "message": message,
        })
    return items


def split_corpus(items, evolve_size=30, seed=SEED):
    """Returns (evolve, dev, holdout) lists of corpus items."""
    holdout = [item for item in items if item["holdout"]]
    rest = [item for item in items if not item["holdout"]]
    by_kind = {}
    for item in rest:
        by_kind.setdefault(item["message"].kind, []).append(item)
    rng = random.Random(seed)
    evolve, dev = [], []
    for kind in sorted(by_kind):
        group = sorted(by_kind[kind], key=lambda item: item["id"])
        rng.shuffle(group)
        take = round(evolve_size * len(group) / len(rest))
        take = min(take, len(group))
        evolve.extend(group[:take])
        dev.extend(group[take:])
    # Top up to the requested size (or trim) while keeping splits disjoint.
    if len(evolve) < evolve_size and dev:
        rng.shuffle(dev)
        evolve.extend(dev[:evolve_size - len(evolve)])
        dev = dev[evolve_size - len(evolve):]
    evolve.sort(key=lambda item: item["id"])
    dev.sort(key=lambda item: item["id"])
    holdout.sort(key=lambda item: item["id"])
    return evolve, dev, holdout


def fresh_group(evolve, generation, size=10, seed=SEED):
    """CLSR-style fresh exemplar group per generation (cycled, no overlap
    within a generation)."""
    rng = random.Random(seed + generation)
    pool = list(evolve)
    rng.shuffle(pool)
    return pool[:size]
