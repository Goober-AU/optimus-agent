"""OCC-2 dialect v2 evolution (deterministic, offline, local tokenizer).

Evolves DIALECT/2 cards (dialect2.Dialect2Card) over the corpus2 EVOLVE
split (research/tasks2/msgs_corpus2_evolve.json, 90 messages). Mirrors the
v1 loop (research/symbolic_comm/evolve.py) with the model-assisted parts
removed per the v2 mandate:

- Proposer seeds are replaced by a deterministic feature ladder over the
  frozen v1 winner plus a greedy prefix-microtemplate miner.
- Listener comprehension is dropped (v1 already showed 1.0 listener
  fidelity for deterministic wires; the deterministic oracle remains the
  hard gate).
- Wire tokens are measured with the local Qwen2.5-0.5B-Instruct
  tokenizer (same one used for CODEBOOK-COST), not the gateway.

Fitness: exact typed fidelity 1.0 (hard gate) -> mean wire TOKENS ->
mean wire bytes. Secondary tracked metric: mean tokens on the key-dense
subprofile (top third of the evolve split by structural density:
occ1-field items per value character). HOLDOUT is never touched.

Selection: elitism 4 of population 10, up to 10 generations, stop after
3 stagnant. All randomness flows from one seed.
"""

import copy
import json
import pathlib
import random
import statistics
import sys
import time

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "symbolic_comm"))

import contract as C
import dialect as D
import dialect2 as D2

# evolve.py imports gateway.py, which imports `requests` at module level but
# only uses it inside methods. The research venv has no requests (v2 runs
# fully offline), so a stub keeps the import side-effect free.
try:
    import requests  # noqa: F401
except ImportError:
    import types as _types
    _stub = _types.ModuleType("requests")

    def _offline(*_args, **_kwargs):
        raise RuntimeError("offline: gateway stub")

    _stub.post = _offline
    _stub.get = _offline
    sys.modules["requests"] = _stub

import evolve as E  # v1 operators + fidelity oracle
import corpus as CP
from baselines import occ1_text

LOG_PATH = HERE / "evolution2-log.jsonl"
WINNER_PATH = HERE / "evolution2-winner.json"
V1_CARD_PATH = HERE.parent / "symbolic_comm" / "dialect-card.json"

POPULATION = 10
ELITE_COUNT = 4
MAX_GENERATIONS = 10
STAGNATION_LIMIT = 3
GROUP_SIZE = 30
KEY_DENSE_SIZE = 27
SEED = 20261017

MODELS = pathlib.Path(r"C:/Users/openclawuser/optimus-occ23-models/hub")

# 1-token emit sigils for mined microtemplates. The corpus is ASCII-only,
# so any non-ASCII emit is collision-free; strict decode proves it.
SIGILS = ["\u00a7", "\u00b6", "\u00b5", "\u00bf", "\u00a1", "\u00aa",
          "\u00ba", "\u00ab", "\u00bb", "\u00c6", "\u00d8", "\u00a4"]
HEADER_POOL = ["d", "D", "2", "#"]


# ---------------------------------------------------------------------------
# Local tokenizer
# ---------------------------------------------------------------------------

_TOKENIZER = None


def tokenizer():
    global _TOKENIZER
    if _TOKENIZER is None:
        from transformers import AutoTokenizer
        snap = next((MODELS / "models--Qwen--Qwen2.5-0.5B-Instruct" / "snapshots").glob("*"))
        _TOKENIZER = AutoTokenizer.from_pretrained(str(snap), local_files_only=True)
    return _TOKENIZER


def count_tokens(texts):
    if not texts:
        return []
    out = []
    batch = 32
    for i in range(0, len(texts), batch):
        chunk = texts[i:i + batch]
        enc = tokenizer()(chunk, add_special_tokens=False)["input_ids"]
        out.extend(len(ids) for ids in enc)
    return out


def ntok(text):
    return count_tokens([text])[0]


# ---------------------------------------------------------------------------
# Corpus2 loading + profiles
# ---------------------------------------------------------------------------

def load_split(split):
    path = HERE.parent / "tasks2" / ("msgs_corpus2_%s.json" % split)
    raw = json.loads(path.read_text(encoding="utf-8"))
    items = []
    for entry in raw["messages"]:
        items.append({
            "id": entry["id"],
            "holdout": split == "holdout",
            "occ0": entry.get("plain_english", ""),
            "payload": entry["payload"],
            "message": C.AgentCommMessage.from_payload(entry["payload"]),
        })
    return items


def structural_density(message):
    fields = message.occ1_fields()
    chars = sum(len(v) for _k, v in fields) + 1
    return len(fields) / chars


def key_dense_subset(items, size=KEY_DENSE_SIZE):
    """Top-`size` messages by structural density (field items per value
    character): the SILO-style key-dense profile of the split."""
    ranked = sorted(items, key=lambda it: (-structural_density(it["message"]), it["id"]))
    return ranked[:size]


# ---------------------------------------------------------------------------
# Deterministic miner (replaces the v1 model proposer)
# ---------------------------------------------------------------------------

MINED_FAMILIES = ("E", "ART", "DEP", "NEXT", "F", "FE",
                  "MSGID", "TASK", "TRACE", "SENDER", "RECIPIENT")


def _miner_values(card, message):
    """(family, raw value) pairs the miner may template, with E/ART values
    reduced to their locator (the part microtemplates see)."""
    out = []
    never = card.is_never_compress(message)
    if never:
        return out
    for key, value in message.occ1_fields():
        if card.field_defaults.get(key) == value:
            continue
        family = card._mt_field(key)
        if family in ("E", "ART") and ":" in value:
            value = value.split(":", 1)[1]
        if family in MINED_FAMILIES:
            out.append((family, value))
    return out


def mine_microtemplates(card, group, budget=12, min_count=6):
    """Greedy deterministic prefix-microtemplate miner with EXACT savings.

    Candidates are all prefixes (length 3..40) of raw family values with
    count >= min_count on the group. Savings are measured with the real
    tokenizer as the difference between the value's token cost under the
    card's current first-match chain and under that chain with the new
    template at the FRONT (mined templates fire before inherited ones).
    Chosen templates skip prefix-overlapping siblings of the same family,
    so at most one chosen template matches any value and the greedy
    accounting stays exact. Emits are 1-token non-ASCII sigils (ASCII-only
    corpus, collision-free; strict decode proves it). Returns (card,
    added); (card, []) when nothing clears the threshold.
    """
    child = copy.deepcopy(card)
    family_emits = {}
    for template in card.microtemplates:
        if template.type == "prefix":
            family_emits.setdefault(template.field, set()).add(template.emit)
    values = {}
    for item in group:
        for family, value in _miner_values(card, item["message"]):
            values[(family, value)] = values.get((family, value), 0) + 1
    counts = {}
    for (family, value), n in values.items():
        limit = min(len(value) - 1, 40)
        for size in range(3, limit + 1):
            key = (family, value[:size])
            counts[key] = counts.get(key, 0) + n
    existing = {(m.field, m.match) for m in card.microtemplates}
    candidates = [(f, p) for (f, p), n in counts.items()
                  if n >= min_count and (f, p) not in existing]
    if not candidates:
        return card, []

    def apply_chain(templates, family, value):
        for template in templates:
            if template.field in (family, "*"):
                new_value, matched = template.encode_value(value)
                if matched:
                    return new_value
        return value

    chain = list(card.microtemplates)
    probe = {}  # (family, prefix) -> template, evaluated front-of-chain

    def template_for(family, prefix):
        key = (family, prefix)
        if key not in probe:
            probe[key] = D.Microtemplate(id="cand", field=family, type="prefix",
                                          match=prefix, emit="\u2603")
        return probe[key]

    scored = []
    for family, prefix in sorted(set(candidates)):
        total = 0
        candidate = template_for(family, prefix)
        for (fam, value), n in values.items():
            if fam != family or not value.startswith(prefix):
                continue
            before = apply_chain(chain, fam, value)
            after = apply_chain([candidate] + chain, fam, value)
            total += n * (ntok(before) - ntok(after))
        if total > 0:
            scored.append((total, family, prefix))
    scored.sort(key=lambda row: (-row[0], row[1], row[2]))

    added = []
    chosen = []
    for saving, family, prefix in scored:
        if len(added) >= budget:
            break
        if any(family == f2 and (prefix.startswith(p2) or p2.startswith(prefix))
               for f2, p2 in chosen):
            continue
        taken = family_emits.setdefault(family, set())
        pool = [s for s in SIGILS if s not in taken]
        if not pool:
            continue
        sigil = pool[0]
        taken.add(sigil)
        nums = [int(m.id[3:]) for m in child.microtemplates
                if m.id.startswith("V2M") and m.id[3:].isdigit()]
        index = (max(nums) + 1 + len(added)) if nums else (1 + len(added))
        template = D.Microtemplate(
            id="V2M%d" % index, field=family, type="prefix",
            match=prefix, emit=sigil)
        child.microtemplates.insert(0, template)
        chain.insert(0, template)
        chosen.append((family, prefix))
        added.append({"id": template.id, "field": family, "match": prefix,
                      "emit": sigil, "saving": saving})
    if not added:
        return card, []
    child.validate()
    child.card_id = card.card_id + "-mine"
    child.lineage = card.lineage + ["mt_mine:%d" % len(added)]
    return child, added


# ---------------------------------------------------------------------------
# v2 mutations (beyond the v1 operator set)
# ---------------------------------------------------------------------------

def mutate_encoding_toggle(card, rng):
    """Deterministic: add or remove one value encoding."""
    child = copy.deepcopy(card)
    fields = [f for f in D2._ENCODING_FIELDS]
    have = set(child.value_encodings)
    addable = [f for f in fields if f not in have]
    if have and (not addable or rng.random() < 0.5):
        field = rng.choice(sorted(have))
        del child.value_encodings[field]
        action = "off"
    elif addable:
        field = rng.choice(addable)
        child.value_encodings[field] = D2._ENCODINGS[field]
        action = "on"
    else:
        return None
    child.validate()
    child.card_id = card.card_id + "-ve"
    child.lineage = card.lineage + ["value_encoding:%s=%s" % (field, action)]
    return child


def mutate_header(card, rng):
    """Deterministic: shorten or restore the wire header."""
    child = copy.deepcopy(card)
    if child.wire_header == D.DIALECT_HEADER:
        child.wire_header = rng.choice(HEADER_POOL)
        action = child.wire_header
    else:
        child.wire_header = D.DIALECT_HEADER
        action = "v1"
    child.validate()
    child.card_id = card.card_id + "-wh"
    child.lineage = card.lineage + ["wire_header:%s" % action]
    return child


def mutate_scalar_pack(card, rng):
    """Deterministic: toggle scalar packing (MSGID/TASK/TRACE/TS on one
    space-joined line)."""
    child = copy.deepcopy(card)
    child.scalar_pack = not child.scalar_pack
    child.validate()
    child.card_id = card.card_id + ("-sp" if child.scalar_pack else "-spx")
    child.lineage = card.lineage + ["scalar_pack:%s" % child.scalar_pack]
    return child


def mutate_status_default(card, group):
    """Deterministic: promote/demote the STATUS default."""
    child = copy.deepcopy(card)
    if "STATUS" in child.field_defaults:
        del child.field_defaults["STATUS"]
        action = "off"
    else:
        counts = {}
        for item in group:
            for key, value in item["message"].occ1_fields():
                if key == "STATUS":
                    counts[value] = counts.get(value, 0) + 1
        if not counts:
            return None
        value, count = max(sorted(counts.items()), key=lambda kv: kv[1])
        if count < 0.25 * len(group):
            return None
        child.field_defaults["STATUS"] = value
        action = "%s" % value
    child.validate()
    child.card_id = card.card_id + "-sd"
    child.lineage = card.lineage + ["status_default:%s" % action]
    return child


# ---------------------------------------------------------------------------
# Evaluation
# ---------------------------------------------------------------------------

def evaluate(card, group, dense):
    fidelity, failures = E.deterministic_fidelity(card, group)
    wires = [card.encode(item["message"]) for item in group]
    tokens = count_tokens(wires)
    sizes = [len(w) for w in wires]
    dense_wires = [card.encode(item["message"]) for item in dense]
    dense_tokens = count_tokens(dense_wires)
    return {
        "card_id": card.card_id,
        "checksum": card.checksum(),
        "fidelity": fidelity,
        "mean_tokens": statistics.mean(tokens),
        "median_tokens": statistics.median(tokens),
        "mean_bytes": statistics.mean(sizes),
        "dense_mean_tokens": statistics.mean(dense_tokens),
        "failures": failures[:4],
    }


def score_key(metrics):
    """fidelity floor first, then tokens, then bytes."""
    return (-metrics["fidelity"], metrics["mean_tokens"], metrics["mean_bytes"])


# ---------------------------------------------------------------------------
# Seeds
# ---------------------------------------------------------------------------

def seed_cards(group):
    """Deterministic feature ladder over the frozen v1 winner."""
    v1 = D.DialectCard.load(str(V1_CARD_PATH))
    seeds = []
    base = D2.Dialect2Card.promote(v1)
    base.card_id = "d2-base"
    seeds.append(base)
    ladder = [
        ("d2-ts", {"TS": "epoch36"}),
        ("d2-ts-exp", {"TS": "epoch36", "EXP": "epoch36"}),
        ("d2-ts-ed", {"TS": "epoch36", "EXP": "epoch36", "ED": "b36"}),
        ("d2-all-enc", {"TS": "epoch36", "EXP": "epoch36", "ED": "b36",
                        "ER": "b36", "FC": "b36"}),
    ]
    for card_id, encodings in ladder:
        card = D2.Dialect2Card.promote(v1)
        card.card_id = card_id
        card.value_encodings = dict(encodings)
        card.validate()
        seeds.append(card)
    full = D2.Dialect2Card.promote(v1)
    full.card_id = "d2-full"
    full.value_encodings = {"TS": "epoch36", "EXP": "epoch36", "ED": "b36",
                           "ER": "b36", "FC": "b36"}
    full.wire_header = "d"
    full.validate()
    seeds.append(full)
    packed = copy.deepcopy(full)
    packed.card_id = "d2-full-sp"
    packed.scalar_pack = True
    packed.validate()
    seeds.append(packed)
    status = copy.deepcopy(full)
    status.card_id = "d2-full-status"
    status.field_defaults["STATUS"] = "in_progress"
    status.validate()
    seeds.append(status)
    mined, added = mine_microtemplates(full, group)
    if added:
        mined.card_id = "d2-full-mined"
        seeds.append(mined)
    combo = copy.deepcopy(mined if added else full)
    combo.card_id = "d2-full-mined-status"
    combo.field_defaults["STATUS"] = "in_progress"
    combo.validate()
    seeds.append(combo)
    # v1 gen-0 anchors for the frontier (no v2 features)
    for v1_seed in E.seed_cards()[:2]:
        promoted = D2.Dialect2Card.promote(v1_seed)
        seeds.append(promoted)
    return seeds


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------

def _mine_op(card, group):
    mined, added = mine_microtemplates(card, group, budget=4)
    return mined if added else None


def run(generations=MAX_GENERATIONS, log_path=LOG_PATH, seed=SEED):
    rng = random.Random(seed)
    evolve_split = load_split("evolve")
    dense = key_dense_subset(evolve_split)
    log_file = pathlib.Path(log_path)
    log_file.parent.mkdir(parents=True, exist_ok=True)

    def log(record):
        with log_file.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, default=str) + "\n")

    log({"event": "run_start", "evolve": len(evolve_split),
         "key_dense": [i["id"] for i in dense],
         "population": POPULATION, "elite": ELITE_COUNT,
         "group_size": GROUP_SIZE, "seed": seed,
         "tokenizer": "Qwen2.5-0.5B-Instruct (local, add_special_tokens=False)"})

    pool = seed_cards(evolve_split)[:POPULATION]
    best_score = None
    stagnation = 0
    history = []
    scored = []

    for generation in range(generations):
        started = time.time()
        # v2 deviation from v1: fitness is deterministic, so every candidate
        # is scored on the FULL evolve split each generation (v1's fresh
        # groups existed to keep the model listener honest; they only added
        # +-4 tok of group noise here, which masked real improvements).
        # Mining still draws a fresh group so induced templates must repeat
        # across different message subsets to be kept by selection.
        group = evolve_split
        results = [(card, evaluate(card, group, dense)) for card in pool]
        scored = sorted(results, key=lambda pair: score_key(pair[1]))
        winner = scored[0]
        current_best = (winner[1]["fidelity"], winner[1]["mean_tokens"],
                        winner[1]["mean_bytes"])
        improved = (best_score is None
                    or (current_best[0] >= best_score[0]
                        and current_best[1] <= best_score[1]
                        and current_best[2] <= best_score[2]
                        and current_best != best_score))
        if improved:
            best_score = current_best
            stagnation = 0
        else:
            stagnation += 1
        record = {
            "event": "generation", "generation": generation,
            "duration_s": round(time.time() - started, 1),
            "group": "full-evolve-split",
            "candidates": [m for _c, m in results],
            "stagnation": stagnation,
        }
        log(record)
        history.append(record)
        print("gen %d (%.1fs) best=%s fid=%.2f tok=%.2f B=%.1f dense=%.2f stag=%d"
              % (generation, record["duration_s"], winner[0].card_id,
                 winner[1]["fidelity"], winner[1]["mean_tokens"],
                 winner[1]["mean_bytes"], winner[1]["dense_mean_tokens"],
                 stagnation), flush=True)
        if stagnation >= STAGNATION_LIMIT or generation == generations - 1:
            break

        mine_group = CP.fresh_group(evolve_split, generation=generation, size=GROUP_SIZE)
        elites = [card for card, _m in scored[:ELITE_COUNT]]
        usage_stats = {}
        for card, _m in results:
            for template in card.microtemplates:
                hits = 0
                for item in group:
                    for key, value in item["message"].occ1_fields():
                        family = card._mt_field(key)
                        if template.field in (family, "*"):
                            _new, matched = template.encode_value(value)
                            hits += 1 if matched else 0
                usage_stats[template.id] = max(usage_stats.get(template.id, 0), hits)
        children = []
        operators = [
            lambda c: E.mutate_alias_shorten(c, rng),
            lambda c: E.mutate_symbol_prune(c, usage_stats),
            lambda c: E.mutate_default_induction(c, group),
            lambda c: E.mutate_mt_induction(c, group, rng),
            lambda c: E.mutate_packing_toggle(c, rng),
            lambda c: mutate_encoding_toggle(c, rng),
            lambda c: mutate_header(c, rng),
            lambda c: mutate_scalar_pack(c, rng),
            lambda c: mutate_status_default(c, mine_group),
            lambda c: _mine_op(c, mine_group),
        ]
        attempts = 0
        while len(children) < POPULATION - ELITE_COUNT and attempts < 60:
            attempts += 1
            parent = rng.choice(elites)
            operator = rng.choice(operators)
            child = operator(parent)
            if child is None or any(child.checksum() == c.checksum() for c in pool):
                continue
            child.generation = generation + 1
            fid, _f = E.deterministic_fidelity(child, group)
            if fid < 1.0:
                continue
            children.append(child)
        pool = (elites + children)[:POPULATION]

    final_card, final_metrics = scored[0]
    log({"event": "run_end", "winner": final_card.card_id,
         "checksum": final_card.checksum(), "metrics": final_metrics})
    final_card.save(str(WINNER_PATH))
    print("winner:", final_card.card_id, "checksum", final_card.checksum()[:12])
    print("saved:", WINNER_PATH)
    return final_card, history


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--generations", type=int, default=MAX_GENERATIONS)
    parser.add_argument("--seed", type=int, default=SEED)
    args = parser.parse_args()
    run(generations=args.generations, seed=args.seed)
