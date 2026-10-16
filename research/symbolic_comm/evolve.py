"""OCC-2 dialect discovery loop (RESEARCH-NOTES S2 pseudocode + S4 protocol).

Population-based evolution over DIALECT/1 cards:

- Generation 0: hand-designed sym-v1 variants, an OCC-1-informed candidate,
  and up to two model-synthesized candidates (glm-5.3 proposer, temp 0.9,
  CLSR App. B synthesis prompt structure).
- Per generation, every candidate is evaluated on a FRESH EVOLVE group
  (CLSR Sec 2.3): (a) deterministic fidelity oracle (encode->decode equals
  the original contract; any loss disqualifies), (b) real listener
  comprehension on a K-message sample (glm-5.3-flash decodes the wire back
  to typed JSON; strict parse + contract equality), (c) wire tokens via the
  gateway's real tokenizer (usage.prompt_tokens minus the measured
  empty-prompt baseline), (d) listener latency.
- Selection: fidelity floor 1.0 (deterministic oracle), then listener
  fidelity, then median wire tokens, then p95 latency. Elitism: top 5.
- Mutations: deterministic (alias shortening, symbol/table pruning,
  default induction, microtemplate induction, template removal, packing
  toggle) and model-assisted (recombination, rule repair from failure
  traces, verification-tag proposal) per S4.2.
- Budget: up to 10 generations; stop after 3 generations without frontier
  improvement. Every generation is logged to evolution-log.jsonl.

Never touches the HOLDOUT split. DEV split is used only for the saturation
check (no candidate is selected on HOLDOUT).
"""

import argparse
import copy
import json
import pathlib
import random
import re
import statistics
import sys
import time

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import contract as C
import corpus as CP
import dialect as D
import gateway as G
import qa as QA
from baselines import occ1_text

LOG_PATH = HERE / "evolution-log.jsonl"

LISTENER_MODEL = "glm-5.3-flash"        # parent steer: cheap listener in-loop
MEASURE_MODEL = "deepseek-v4.1-flash"     # fast, non-reasoning; token counts only
POPULATION = 8
ELITE_COUNT = 5
MAX_GENERATIONS = 10
STAGNATION_LIMIT = 3
K_COMPREHENSION = 10
WORKERS = 8

LISTENER_SYSTEM = """You are Agent B, the receiver in a two-agent protocol. Agent A sends messages encoded in the D/1 dialect defined by the attached card. Reconstruct the typed JSON payload of each message exactly.

Output ONLY one compact JSON object (no spaces, no markdown, no commentary) with these keys when present (omit absent): message_id, task_id, parent_task_id, trace_id, sender_identity, recipient_identity, kind, status, findings, evidence, artifacts, verification, dependencies, next_actions, sequence, timestamp, expiry.

Rules:
- kind, status, verification.status, evidence[].kind: full contract values per the card symbol tables.
- findings: list of {"statement": str, "confidence": number-or-omit, "evidence": list of evidence ID strings like ["E1","E2"] (omit when empty)}. Findings are ordered i1, i2, ... on the wire.
- evidence: list of evidence records e1, e2, ... on the wire, each {"id": "E1", "kind": str, "locator": str, "line_range": [start,end], "digest": str, "revision": str, "verified": bool} - omit absent fields, but "id" is REQUIRED and sequential (E1, E2, ...).
- next_actions: ordered list from the packed n= line (split on |).
- verification: {"claim", "status", "method", "receipt"-or-omit}.
- Restore omitted default fields exactly as the card specifies.
- Timestamps: RFC 3339 with seconds and Z (e.g. 2026-10-16T09:34:00Z)."""


# ---------------------------------------------------------------------------
# Seed cards
# ---------------------------------------------------------------------------

ALIASES_V1 = {
    "MSGID": "m", "TASK": "t", "PARENT": "p", "TRACE": "x", "SENDER": "f",
    "RECIPIENT": "r", "KIND": "k", "STATUS": "s", "SEQ": "q",
    "V": "vc", "VS": "vs", "VM": "vm", "VR": "vr",
    "NEXT": "n", "TS": "w", "EXP": "xp",
}
FAMILIES_V1 = {
    "F": "i", "FC": "c", "FE": "v", "E": "e", "EL": "l", "ED": "d",
    "ER": "g", "EV": "y", "ART": "h", "DEP": "u",
}
# OCC-1-informed: readable 2-char mnemonics from the OCC-1 key names.
ALIASES_OCC1 = {
    "MSGID": "mi", "TASK": "ta", "PARENT": "pa", "TRACE": "tr", "SENDER": "se",
    "RECIPIENT": "re", "KIND": "ki", "STATUS": "st", "SEQ": "sq",
    "V": "ve", "VS": "vs", "VM": "vm", "VR": "vr",
    "NEXT": "nx", "TS": "ts", "EXP": "ex",
}
FAMILIES_OCC1 = {
    "F": "fi", "FC": "fc", "FE": "fe", "E": "ev", "EL": "el", "ED": "ed",
    "ER": "er", "EV": "ef", "ART": "ar", "DEP": "de",
}
SYMBOLS_V1 = {
    "STATUS": {"pending": "0", "in_progress": "1", "done": "2", "partial": "3",
               "blocked": "4", "failed": "5", "cancelled": "6"},
    "KIND": {"TASK": "T", "PROGRESS": "P", "RESULT": "R", "EVIDENCE": "E",
             "REQUEST_DETAIL": "Q", "ACK": "A", "CORRECTION": "C",
             "BLOCKED": "B", "CANCELLED": "X"},
    "VERIFY": {"pass": "+", "fail": "-", "unknown": "?"},
    "EVIDENCE_VERIFIED": {"pass": "1", "fail": "0"},
    "EVIDENCE_KIND": {"src": "s", "commit": "c", "log": "l", "tool": "t",
                      "build": "b", "artifact": "a", "lines": "n", "finding": "f"},
}
ALL_PACKINGS = ["E", "FE", "NEXT", "DEP", "ART", "FC"]


def microtemplates_v1():
    return [
        {"id": "MT1", "field": "TRACE", "type": "prefix", "match": "trace-", "emit": "%"},
        {"id": "MT2", "field": "SENDER", "type": "prefix", "match": "agent://", "emit": "a:"},
        {"id": "MT3", "field": "SENDER", "type": "prefix", "match": "daemon://", "emit": "d:"},
        {"id": "MT4", "field": "RECIPIENT", "type": "prefix", "match": "agent://", "emit": "a:"},
        {"id": "MT5", "field": "RECIPIENT", "type": "prefix", "match": "daemon://", "emit": "d:"},
        {"id": "MT6", "field": "TS", "type": "regex",
         "match": r"(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})Z",
         "emit": r"\1\2\3\4\5\6",
         "decode_match": r"(\d{4})(\d{2})(\d{2})(\d{2})(\d{2})(\d{2})",
         "decode_emit": r"\1-\2-\3T\4:\5:\6Z"},
        {"id": "MT7", "field": "EXP", "type": "regex",
         "match": r"(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})Z",
         "emit": r"\1\2\3\4\5\6",
         "decode_match": r"(\d{4})(\d{2})(\d{2})(\d{2})(\d{2})(\d{2})",
         "decode_emit": r"\1-\2-\3T\4:\5:\6Z"},
        {"id": "MT8", "field": "E", "type": "prefix", "match": "crates/pi-coding-agent/", "emit": "^"},
        {"id": "MT9", "field": "E", "type": "prefix", "match": "logs/", "emit": "g:"},
        {"id": "MT10", "field": "E", "type": "prefix", "match": "receipts/", "emit": "j:"},
        {"id": "MT11", "field": "E", "type": "prefix", "match": "reports/", "emit": "q:"},
        {"id": "MT12", "field": "E", "type": "prefix", "match": "target/", "emit": "y:"},
        {"id": "MT13", "field": "E", "type": "prefix", "match": "research/", "emit": "z:"},
        {"id": "MT14", "field": "E", "type": "prefix", "match": "docs/", "emit": "o:"},
        {"id": "MT15", "field": "ART", "type": "prefix", "match": "crates/pi-coding-agent/", "emit": "^"},
        {"id": "MT16", "field": "ART", "type": "prefix", "match": "logs/", "emit": "g:"},
        {"id": "MT17", "field": "ART", "type": "prefix", "match": "receipts/", "emit": "j:"},
        {"id": "MT18", "field": "ART", "type": "prefix", "match": "reports/", "emit": "q:"},
        {"id": "MT19", "field": "ART", "type": "prefix", "match": "target/", "emit": "y:"},
        {"id": "MT20", "field": "ART", "type": "prefix", "match": "research/", "emit": "z:"},
    ]


def seed_cards():
    """Generation-0 pool: sym-v1 variants + OCC-1-informed + max-compress."""
    cards = []
    base = dict(
        key_aliases=ALIASES_V1, family_aliases=FAMILIES_V1,
        value_symbols=SYMBOLS_V1, microtemplates=microtemplates_v1(),
        usage_rules=("Findings and evidence stay positional (i1..in, e1..en). "
                     "Elided fields equal their defaults. Contradictions and "
                     "blocked payloads travel verbatim."),
    )
    cards.append(D.DialectCard(card_id="sym-v1", generation=0, packings=ALL_PACKINGS,
                               field_defaults={"RECIPIENT": "daemon://coordinator",
                                               "SENDER": "daemon://coordinator",
                                               "SEQ": "1"}, **base))
    cards.append(D.DialectCard(card_id="sym-v1-nopack", generation=0, packings=[],
                               field_defaults={"RECIPIENT": "daemon://coordinator",
                                               "SENDER": "daemon://coordinator",
                                               "SEQ": "1"}, **base))
    cards.append(D.DialectCard(card_id="sym-occ1", generation=0, packings=[],
                               key_aliases=ALIASES_OCC1, family_aliases=FAMILIES_OCC1,
                               value_symbols=SYMBOLS_V1, microtemplates=[],
                               field_defaults={}, usage_rules="OCC-1-style readable keys."))
    cards.append(D.DialectCard(card_id="sym-max", generation=0, packings=ALL_PACKINGS,
                               key_aliases=ALIASES_V1, family_aliases=FAMILIES_V1,
                               value_symbols=SYMBOLS_V1, microtemplates=microtemplates_v1(),
                               field_defaults={"RECIPIENT": "daemon://coordinator",
                                               "SENDER": "daemon://coordinator",
                                               "SEQ": "1", "STATUS": "done",
                                               "TRACE": "trace-occ2-01"},
                               usage_rules="Maximum compression variant."))
    cards.append(D.DialectCard(card_id="sym-pack-e", generation=0, packings=["E", "FE"],
                               field_defaults={"RECIPIENT": "daemon://coordinator",
                                               "SENDER": "daemon://coordinator",
                                               "SEQ": "1"}, **base))
    return cards


# ---------------------------------------------------------------------------
# Evaluation
# ---------------------------------------------------------------------------

def deterministic_fidelity(card, items):
    """Encode->decode == original for every item. Returns (rate, failures)."""
    failures = []
    for item in items:
        try:
            wire = card.encode(item["message"])
            back = card.decode(wire, strict=True)
            if not C.equal(item["message"], back):
                failures.append(item["id"])
        except Exception as error:
            failures.append("%s:%s" % (item["id"], str(error)[:60]))
    return (1.0 if not failures else 1.0 - len(failures) / len(items)), failures


def comprehension_call(gateway, card, item, model=LISTENER_MODEL):
    wire = card.encode(item["message"])
    user = card.card_text() + "\n\nMESSAGE:\n" + wire + (
        "\n\nReconstruct the JSON payload. Output ONLY the compact JSON object.")
    result = gateway.chat(
        model,
        [{"role": "system", "content": LISTENER_SYSTEM},
         {"role": "user", "content": user}],
        max_tokens=1024, temperature=0.0)
    record = {
        "id": item["id"], "ok": False, "error": None,
        "latency": result["latency"],
        "completion_tokens": result["usage"].get("completion_tokens"),
    }
    try:
        parsed, _fenced = C.payload_from_listener_json(result["content"])
        record["ok"] = C.equal(parsed, item["message"].to_payload())
        if not record["ok"]:
            record["error"] = _diff_summary(parsed, item["message"].to_payload())
    except Exception as error:
        record["error"] = "%s: %s" % (type(error).__name__, str(error)[:120])
    return record


def _diff_summary(got, want):
    got_n, want_n = C._normalize(got), C._normalize(want)
    diffs = []
    for key in sorted(set(list(got_n.keys()) + list(want_n.keys()))):
        if got_n.get(key) != want_n.get(key):
            diffs.append(key)
    return "fields differ: %s" % ",".join(diffs) if diffs else "value-level mismatch"


def wire_tokens(gateway, model, text):
    return gateway.measure_tokens(model, text)["tokens"]


def evaluate_candidate(gateway, card, group, sample, tokens_model=MEASURE_MODEL):
    """Full per-candidate evaluation on one fresh group."""
    fid_rate, fid_failures = deterministic_fidelity(card, group)
    token_texts = [card.encode(item["message"]) for item in sample]
    token_results, token_errors = gateway.run_concurrent(
        lambda text: gateway.measure_tokens(tokens_model, text),
        token_texts, workers=WORKERS)
    tokens = [r["tokens"] if r else None for r in token_results]
    tokens = [x for x in tokens if x is not None]
    comp_records, comp_errors = gateway.run_concurrent(
        lambda item: comprehension_call(gateway, card, item), sample, workers=WORKERS)
    for record, error in zip(comp_records, comp_errors):
        if error:
            comp_records[comp_records.index(record)] = {
                "id": None, "ok": False, "error": error,
                "latency": None, "completion_tokens": None}
    # keep original sample ids
    for index, (record, error) in enumerate(zip(comp_records, comp_errors)):
        if error:
            record["id"] = sample[index]["id"]
    comp_ok = [r["ok"] for r in comp_records if r is not None]
    latencies = [r["latency"] for r in comp_records
                 if r is not None and r.get("latency")]
    metrics = {
        "card_id": card.card_id,
        "checksum": card.checksum(),
        "deterministic_fidelity": round(fid_rate, 4),
        "deterministic_failures": fid_failures[:10],
        "listener_fidelity": (sum(1 for ok in comp_ok if ok) / len(comp_ok))
                             if comp_ok else 0.0,
        "median_wire_tokens": statistics.median(tokens) if tokens else None,
        "mean_wire_tokens": round(statistics.mean(tokens), 2) if tokens else None,
        "p95_listener_latency": round(sorted(latencies)[
            min(len(latencies) - 1, int(0.95 * len(latencies)))], 3)
        if latencies else None,
        "listener_errors": [r.get("error") for r in comp_records
                            if r is not None and r.get("error")][:5],
        "comprehension_detail": [
            {"id": r["id"], "ok": r["ok"],
             "error": r.get("error")} for r in comp_records if r is not None],
    }
    return metrics


def score_key(metrics):
    """Selection order: fidelity floor, then listener fidelity, then tokens,
    then latency (all ascending; higher fidelity is better, so fidelities
    are negated to keep one sort direction)."""
    det = metrics["deterministic_fidelity"]
    lis = metrics["listener_fidelity"]
    tokens = metrics["median_wire_tokens"] if metrics["median_wire_tokens"] is not None else 1e9
    latency = metrics["p95_listener_latency"] if metrics["p95_listener_latency"] is not None else 1e9
    return (-det, -lis, tokens, latency)


def dominates(a, b):
    """Pareto dominance on (listener_fidelity, -tokens, -latency)."""
    ax = (a["listener_fidelity"], -a["median_wire_tokens"], -a["p95_listener_latency"])
    bx = (b["listener_fidelity"], -b["median_wire_tokens"], -b["p95_listener_latency"])
    return all(x >= y for x, y in zip(ax, bx)) and any(
        x > y for x, y in zip(ax, bx))


def pareto_frontier(metrics_list):
    frontier = []
    for candidate in metrics_list:
        if candidate["deterministic_fidelity"] < 1.0:
            continue
        if not any(dominates(other, candidate) for other in metrics_list
                   if other is not candidate and other["deterministic_fidelity"] >= 1.0):
            frontier.append(candidate)
    return frontier


# ---------------------------------------------------------------------------
# Mutations
# ---------------------------------------------------------------------------

def _unused_aliases(card):
    used = set(card.key_aliases.values()) | set(card.family_aliases.values())
    pool = [chr(c) for c in range(ord("a"), ord("z") + 1) if chr(c) not in used]
    pool += [str(d) for d in range(10)]
    return pool


def mutate_alias_shorten(card, rng):
    """Deterministic: shorten one 2-char alias to an unused 1-char key."""
    child = copy.deepcopy(card)
    targets = [(k, v) for k, v in list(child.key_aliases.items())
               + list(child.family_aliases.items()) if len(v) == 2]
    if not targets:
        return None
    key, alias = rng.choice(targets)
    pool = _unused_aliases(child)
    if not pool:
        return None
    new_alias = pool[0]
    if key in child.key_aliases:
        child.key_aliases[key] = new_alias
    else:
        child.family_aliases[key] = new_alias
    child.validate()
    child.card_id = card.card_id + "-as"
    child.lineage = card.lineage + ["alias_shorten:%s->%s" % (alias, new_alias)]
    return child


def mutate_symbol_prune(card, usage_stats):
    """Deterministic: retire microtemplates that never matched in the last
    generation (symbol tables must stay total per the S4.1 invariant)."""
    child = copy.deepcopy(card)
    kept = [m for m in child.microtemplates
            if usage_stats.get(m["id"] if isinstance(m, dict) else m.id, 0) > 0]
    if len(kept) == len(child.microtemplates):
        return None
    child.microtemplates = kept
    child.validate()
    child.card_id = card.card_id + "-sp"
    child.lineage = card.lineage + ["symbol_prune:%d->%d"
                                    % (len(card.microtemplates), len(kept))]
    return child


def mutate_default_induction(card, group):
    """Deterministic: promote the most frequent value of a fixed field to a
    default when it is not already the default and it saves tokens."""
    child = copy.deepcopy(card)
    best = None
    for key in D.FIXED_KEYS:
        counts = {}
        for item in group:
            for fkey, value in item["message"].occ1_fields():
                if fkey == key:
                    counts[value] = counts.get(value, 0) + 1
        if not counts:
            continue
        value, count = max(counts.items(), key=lambda kv: kv[1])
        if count / len(group) < 0.25:
            continue
        if child.field_defaults.get(key) == value:
            continue
        if key in ("STATUS",) and value not in C.STATUSES:
            continue
        # candidate must be a plain scalar string; sequence values are strings
        best = best or (key, value)
    if best is None:
        return None
    key, value = best
    child.field_defaults[key] = value
    child.validate()
    child.card_id = card.card_id + "-di"
    child.lineage = card.lineage + ["default_induction:%s=%s" % (key, value)]
    return child


def mutate_mt_induction(card, group, rng):
    """Deterministic: mine repeated structural prefixes in free values and
    add a reversible prefix microtemplate."""
    child = copy.deepcopy(card)
    candidates = {}
    for item in group:
        for key, value in item["message"].occ1_fields():
            if key.startswith("F") or key == "V" or key.startswith("NEXT"):
                continue
            family = child._mt_field(key)
            if family in ("E",):
                if ":" in value:
                    value = value.split(":", 1)[1]
            for size in (6, 8, 10):
                prefix = value[:size]
                if len(value) > size + 2:
                    candidates[(family, prefix)] = candidates.get(
                        (family, prefix), 0) + 1
    best = None
    for (family, prefix), count in sorted(candidates.items(), key=lambda kv: -kv[1]):
        if count < max(2, len(group) // 8):
            continue
        if any(m.field == family and m.type == "prefix"
               and m.match == prefix for m in child.microtemplates):
            continue
        best = (family, prefix, count)
        break
    if best is None:
        return None
    family, prefix, count = best
    sigil = rng.choice(["%", "$", "&", "*", ";", "^"])
    child.microtemplates.append(D.Microtemplate(
        id="MT%d" % (len(child.microtemplates) + 1), field=family,
        type="prefix", match=prefix, emit=sigil + ":"))
    child.validate()
    child.card_id = card.card_id + "-mi"
    child.lineage = card.lineage + ["mt_induction:%s %r x%d" % (family, prefix, count)]
    return child


def mutate_template_removal(card, failure_traces):
    """Deterministic: retire microtemplates whose decode ever mismatched."""
    bad_fields = set()
    for trace in failure_traces:
        if trace.get("type") == "mt_decode_mismatch":
            bad_fields.add(trace["field"])
    if not bad_fields:
        return None
    child = copy.deepcopy(card)
    kept = [m for m in child.microtemplates if m.field not in bad_fields]
    if not kept:
        return None
    child.microtemplates = kept
    child.validate()
    child.card_id = card.card_id + "-tr"
    child.lineage = card.lineage + ["template_removal:%s" % sorted(bad_fields)]
    return child


def mutate_packing_toggle(card, rng):
    """Deterministic: flip one packing family on or off."""
    child = copy.deepcopy(card)
    family = rng.choice(D.PACKINGS)
    if family in child.packings:
        child.packings = [p for p in child.packings if p != family]
        action = "off"
    else:
        child.packings = child.packings + [family]
        action = "on"
    child.validate()
    child.card_id = card.card_id + "-pt"
    child.lineage = card.lineage + ["packing_toggle:%s=%s" % (family, action)]
    return child


# -- model-assisted ----------------------------------------------------------

SCHEMA_HINT = """Card JSON schema:
{"key_aliases": {"TASK": "t", ...}         # OCC-1 fixed key -> 1-2 char [a-z0-9] alias
 "family_aliases": {"F": "i", ...}          # indexed family -> 1-2 char alias
 "value_symbols": {"STATUS": {"pending": "0", ...}, "KIND": {...}, "VERIFY": {...},
                   "EVIDENCE_VERIFIED": {...}, "EVIDENCE_KIND": {...}}  # TOTAL tables
 "microtemplates": [{"id": "MT1", "field": "E", "type": "prefix"|"regex",
                     "match": "...", "emit": "...",
                     "decode_match": "...", "decode_emit": "..."}]  # regex only
 "field_defaults": {"SEQ": "1", ...}        # fixed keys only
 "packings": subset of ["E", "FE", "NEXT", "DEP", "ART", "FC"]
 "never_compress_kinds": subset of ["BLOCKED", "CANCELLED", "CORRECTION", "ACK"]
 "usage_rules": "free text"}
Constraints: alias/symbol uniqueness; enum tables total over the closed enums
(statuses pending/in_progress/done/partial/blocked/failed/cancelled; kinds
TASK/PROGRESS/RESULT/EVIDENCE/REQUEST_DETAIL/ACK/CORRECTION/BLOCKED/CANCELLED;
verify pass/fail/unknown; evidence kinds src/commit/log/build/tool/artifact/lines/finding);
symbols 1-2 chars, never equal to a raw value; microtemplates must be reversible."""


def synth_prompt(exemplars):
    """Compact CLSR-style synthesis prompt.

    NOTE: the gateway (build 20260918.1) hangs on multi-exemplar synthesis
    prompts (3 exemplars + full schema hint never returned in 240s at
    temperature 0 or 0.9, on both glm-5.3 and glm-5.3-flash), so this prompt
    is deliberately minimal: one exemplar, compact schema, verified to
    return in ~20s. Recorded as a protocol deviation in EVOLUTION-REPORT.md.
    """
    rows = [
        "Design a compact D/1 dialect card for typed agent messages. Goal: "
        "minimize wire tokens while keeping exact reconstruction.",
        "",
        "Card JSON fields:",
        "- key_aliases: OCC-1 key -> 1-2 char lowercase alias (keys: MSGID TASK "
        "PARENT TRACE SENDER RECIPIENT KIND STATUS SEQ V VS VM VR NEXT TS EXP)",
        "- family_aliases: family -> 1-2 char alias (families: F FC FE E EL ED "
        "ER EV ART DEP)",
        "- value_symbols: total injective tables mapping STATUS (pending "
        "in_progress done partial blocked failed cancelled), KIND (TASK PROGRESS "
        "RESULT EVIDENCE REQUEST_DETAIL ACK CORRECTION BLOCKED CANCELLED), "
        "VERIFY (pass fail unknown), EVIDENCE_VERIFIED (pass fail), "
        "EVIDENCE_KIND (src commit log build tool artifact lines finding) "
        "to 1-2 char symbols",
        "- packings: subset of [\"E\",\"FE\",\"NEXT\",\"DEP\",\"ART\",\"FC\"]",
        "- field_defaults: fixed key -> default value",
        "- usage_rules: one line",
        "",
        "Example typed payload:",
        json.dumps(exemplars[0]["message"].to_payload(), separators=(",", ":")),
        "",
        "Output ONLY the JSON object.",
    ]
    return "\n".join(rows)


def recombine_prompt(parent_a, parent_b, traces):
    rows = [
        "Recombine two parent dialect cards into one child. Keep conventions that "
        "support correct concise reconstruction, remove redundant notation, repair "
        "ambiguous rules. Failure traces from the last generation are listed; avoid "
        "rules that caused them.",
        "",
        SCHEMA_HINT,
        "",
        "PARENT A:",
        json.dumps(parent_a.body(), separators=(",", ":")),
        "",
        "PARENT B:",
        json.dumps(parent_b.body(), separators=(",", ":")),
        "",
        "RECENT FAILURE TRACES:",
        json.dumps(traces[:8], separators=(",", ":")),
        "",
        "Output ONLY the child card JSON object.",
    ]
    return "\n".join(rows)


def repair_prompt(card, failure_traces):
    rows = [
        "Repair the ambiguous or failing rules of this dialect card, given listener "
        "failure traces (field-level mismatches between the listener reconstruction "
        "and the true payload). Keep the compression where it works.",
        "",
        SCHEMA_HINT,
        "",
        "CURRENT CARD:",
        json.dumps(card.body(), separators=(",", ":")),
        "",
        "FAILURE TRACES:",
        json.dumps(failure_traces[:6], separators=(",", ":")),
        "",
        "Output ONLY the repaired card JSON object.",
    ]
    return "\n".join(rows)


def parse_proposed_card(text, parent_lineage, card_id):
    """Parse a model-proposed card; returns None on any violation."""
    try:
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if match is None:
            return None
        payload = json.loads(match.group(0))
        card = D.DialectCard(
            key_aliases=payload.get("key_aliases", {}),
            family_aliases=payload.get("family_aliases", {}),
            value_symbols=payload.get("value_symbols", {}),
            microtemplates=payload.get("microtemplates", []),
            field_defaults=payload.get("field_defaults", {}),
            usage_rules=payload.get("usage_rules", ""),
            never_compress_kinds=payload.get("never_compress_kinds"),
            never_compress_verification_fail=payload.get(
                "never_compress_verification_fail", True),
            packings=payload.get("packings", []),
            card_id=card_id, generation=0, lineage=parent_lineage)
        return card
    except Exception:
        return None


PROPOSER_TIMEOUT_S = 90.0
# glm-5.3 enters unbounded hidden reasoning on long structured prompts
# (measured: >240s with no response at temp 0 and 0.9, gateway build
# optimus-gateway-monitoring-repair-20260918.1, 2026-10-16). glm-5.3-flash
# returns in 20-30s on the same prompts, so flash is the primary proposer
# and glm-5.3 is the fallback. Recorded as a protocol deviation (S4.2 says
# glm-5.3 proposer) in EVOLUTION-REPORT.md.
PROPOSER_MODEL = "glm-5.3-flash"
PROPOSER_FALLBACK_MODEL = "glm-5.3"

_FALLBACK_LOG = []


def _note_fallback(record):
    """Records a proposer fallback; flushed into the run log at the next
    generation boundary (proposer_call runs before the run-scoped log
    closure exists for the gen-0 synthesis call)."""
    _FALLBACK_LOG.append(record)


def proposer_call(gateway, prompt):
    """CLSR-style proposer call: glm-5.3 at temp 0.9 with a bounded timeout;
    on timeout or transport failure, one glm-5.3-flash fallback attempt
    (recorded as a fallback, not a protocol deviation)."""
    try:
        return gateway.chat(
            PROPOSER_MODEL, [{"role": "user", "content": prompt}],
            max_tokens=2048, temperature=0.9, retries=1,
            timeout=PROPOSER_TIMEOUT_S)
    except Exception as error:
        _note_fallback({"event": "proposer_fallback", "model": PROPOSER_MODEL,
                        "error": "%s: %s" % (type(error).__name__, str(error)[:120]),
                        "fallback_model": PROPOSER_FALLBACK_MODEL})
        try:
            return gateway.chat(
                PROPOSER_FALLBACK_MODEL, [{"role": "user", "content": prompt}],
                max_tokens=2048, temperature=0.9, retries=2,
                timeout=PROPOSER_TIMEOUT_S)
        except Exception as fallback_error:
            _note_fallback({"event": "proposer_failed",
                            "error": "%s: %s" % (type(fallback_error).__name__,
                                                 str(fallback_error)[:120])})
            return None


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------

def run(generations=MAX_GENERATIONS, log_path=LOG_PATH, seed=20261016):
    rng = random.Random(seed)
    gateway = G.GatewayClient()
    health = gateway.health()
    items = CP.load_corpus()
    evolve_split, dev_split, holdout = CP.split_corpus(items)
    log_file = pathlib.Path(log_path)
    log_file.parent.mkdir(parents=True, exist_ok=True)

    def log(record):
        with log_file.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, default=str) + "\n")

    log({"event": "run_start", "health": {"status": health["status"],
                                          "buildId": health["buildId"]},
         "splits": {"EVOLVE": len(evolve_split), "DEV": len(dev_split),
                    "HOLDOUT": len(holdout)},
         "listener_model": LISTENER_MODEL, "proposer_model": PROPOSER_MODEL,
         "measure_model": MEASURE_MODEL, "seed": seed})

    # Generation 0: seeds + model synthesis.
    pool = seed_cards()
    exemplars = CP.fresh_group(evolve_split, generation=0, size=6)
    synth = proposer_call(gateway, synth_prompt(exemplars))
    proposed = (parse_proposed_card(synth["content"], ["model_synth"], "sym-synth-0")
                if synth else None)
    if proposed is not None:
        fid, _ = deterministic_fidelity(proposed, evolve_split)
        log({"event": "model_synthesis", "accepted": fid == 1.0,
             "fidelity": fid, "checksum": proposed.checksum()})
        if fid == 1.0:
            pool.append(proposed)
    else:
        log({"event": "model_synthesis", "accepted": False,
             "reason": "proposal failed card parse or validation"})
    while len(pool) < POPULATION:
        mutant = mutate_packing_toggle(pool[len(pool) % len(pool)], rng)
        if mutant is None:
            break
        pool.append(mutant)
    pool = pool[:POPULATION]

    usage_stats = {}
    failure_traces = []
    best_score = None
    stagnation = 0
    history = []

    for generation in range(generations):
        group = CP.fresh_group(evolve_split, generation=generation, size=12)
        sample = group[:K_COMPREHENSION]
        started = time.time()
        results = []
        for card in pool:
            metrics = evaluate_candidate(gateway, card, group, sample)
            results.append((card, metrics))
        # usage stats for symbol pruning: count MT hits on this group
        for card, _metrics in results:
            for template in card.microtemplates:
                hits = 0
                for item in group:
                    for key, value in item["message"].occ1_fields():
                        family = card._mt_field(key)
                        if template.field in (family, "*"):
                            _new, matched = template.encode_value(value)
                            hits += 1 if matched else 0
                usage_stats[template.id] = max(usage_stats.get(template.id, 0), hits)
        # listener failures -> traces
        failure_traces = []
        for card, metrics in results:
            for detail in metrics.get("comprehension_detail", []):
                if detail.get("error"):
                    failure_traces.append({
                        "type": "listener_mismatch", "card": card.card_id,
                        "item": detail["id"], "error": detail["error"]})

        scored = sorted(results, key=lambda pair: score_key(pair[1]))
        frontier = pareto_frontier([m for _c, m in results])
        winner = scored[0]
        current_best = (winner[1]["deterministic_fidelity"],
                        winner[1]["listener_fidelity"],
                        winner[1]["median_wire_tokens"])
        improved = best_score is None or (
            current_best[0] >= best_score[0] and current_best[1] >= best_score[1]
            and current_best[2] <= best_score[2] and current_best != best_score)
        if improved:
            best_score = current_best
            stagnation = 0
        else:
            stagnation += 1

        generation_record = {
            "event": "generation", "generation": generation,
            "duration_s": round(time.time() - started, 1),
            "frontier": [{"card_id": m["card_id"], "checksum": m["checksum"],
                          "listener_fidelity": m["listener_fidelity"],
                          "median_wire_tokens": m["median_wire_tokens"],
                          "p95_listener_latency": m["p95_listener_latency"]}
                         for m in frontier],
            "candidates": [m for _c, m in results],
            "stagnation": stagnation,
        }
        while _FALLBACK_LOG:
            log(_FALLBACK_LOG.pop(0))
        log(generation_record)
        history.append(generation_record)
        print("gen %d (%.0fs) best=%s det_fid=%.2f lis_fid=%.2f tokens=%.0f "
              "frontier=%d stag=%d" % (
                  generation, generation_record["duration_s"],
                  winner[0].card_id, winner[1]["deterministic_fidelity"],
                  winner[1]["listener_fidelity"], winner[1]["median_wire_tokens"] or -1,
                  len(frontier), stagnation))

        if stagnation >= STAGNATION_LIMIT or generation == generations - 1:
            break

        # Next generation: elites + mutated children.
        elites = [card for card, _m in scored[:ELITE_COUNT]]
        children = []
        # Deterministic mutations on random elites.
        operators = [
            lambda c: mutate_alias_shorten(c, rng),
            lambda c: mutate_symbol_prune(c, usage_stats),
            lambda c: mutate_default_induction(c, group),
            lambda c: mutate_mt_induction(c, group, rng),
            lambda c: mutate_packing_toggle(c, rng),
        ]
        if failure_traces:
            operators.append(lambda c: mutate_template_removal(c, failure_traces))
        # S4.2 mutation mix: deterministic operators fill the first two
        # child slots; the remaining slots are reserved for model-assisted
        # children (recombine, repair), so the model-assisted operators
        # always run each generation.
        attempts = 0
        while len(children) < min(2, POPULATION - ELITE_COUNT) and attempts < 24:
            parent = rng.choice(elites)
            operator = rng.choice(operators)
            child = operator(parent)
            attempts += 1
            if child is None:
                continue
            child.generation = generation + 1
            fid, _f = deterministic_fidelity(child, evolve_split)
            if fid < 1.0:
                continue
            children.append(child)
        # Model-assisted mutations (CLSR App. B structure).
        model_attempts = 0
        while len(children) < POPULATION - ELITE_COUNT and model_attempts < 4:
            model_attempts += 1
            # Alternate recombination and repair (repair only when traces exist).
            if failure_traces and model_attempts % 2 == 0:
                repair_target = rng.choice(elites)
                rep = proposer_call(gateway, repair_prompt(repair_target, failure_traces))
                child = (parse_proposed_card(
                    rep["content"], repair_target.lineage + ["model_repair"],
                    "%s-r%d" % (repair_target.card_id, generation + 1))
                    if rep else None)
                kind = "model_repair"
            else:
                parent_a, parent_b = (rng.sample(elites, 2)
                                       if len(elites) >= 2 else (elites[0], elites[0]))
                recomb = proposer_call(gateway, recombine_prompt(
                    parent_a, parent_b, failure_traces))
                child = (parse_proposed_card(
                    recomb["content"],
                    parent_a.lineage + parent_b.lineage + ["model_recombine"],
                    "%s-x%d" % (parent_a.card_id, generation + 1))
                    if recomb else None)
                kind = "model_recombine"
            if child is None:
                log({"event": kind, "accepted": False,
                     "reason": "proposal unparseable or proposer call failed"})
                continue
            fid, _f = deterministic_fidelity(child, evolve_split)
            log({"event": kind, "accepted": fid == 1.0, "fidelity": fid})
            if fid == 1.0:
                child.generation = generation + 1
                children.append(child)
        pool = elites + children
        pool = pool[:POPULATION]

    final = scored[0]
    log({"event": "run_end", "winner": final[0].card_id,
         "checksum": final[0].checksum()})
    return final[0], history


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--generations", type=int, default=MAX_GENERATIONS)
    parser.add_argument("--seed", type=int, default=20261016)
    args = parser.parse_args()
    winner, history = run(generations=args.generations, seed=args.seed)
    out = HERE / "evolution-winner.json"
    out.write_text(json.dumps(winner.to_json(), indent=2), encoding="utf-8")
    print("winner:", winner.card_id, "checksum", winner.checksum()[:12])
    print("saved:", out)
