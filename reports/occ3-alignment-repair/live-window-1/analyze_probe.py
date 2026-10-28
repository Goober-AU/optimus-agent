#!/usr/bin/env python3
"""OCC-3 probe analyser (window 2026-10-27b). Reads one probe JSONL, prints scalar tables,
writes a Markdown summary and a verdict JSON next to the input file.

INPUT
-----
One JSON object per line, with exactly these keys:
  * the 24 keys of extensions/sglang-occ3/occ3_sglang/calibration_probe.py PROBE_KEYS
  * anchor_alpha and max_prefix_tokens, added by the worker hook that writes the file
    (latent_worker writes PROBE_KEYS, then result["anchor_alpha"], result["max_prefix_tokens"]).
A key that is absent from a line is printed as MISSING and is never replaced by a guess.
Any value that is not an int, a float, or (for canonical_decile only) a flat list of float
is refused: this analyser never stores or prints tensor data.

WHAT IT COMPUTES
----------------
1. A per-turn table of scalars at 4 significant figures.
2. Cross-turn median, min and max for every scalar key.
3. An intrinsic-rank estimate from the canonical spectrum and the within-turn held-out R^2,
   using the canonical bound stated in section 3 of the Markdown output.
4. A pre-registered decision rule FEASIBLE / NOT-FEASIBLE / INCONCLUSIVE.

Stdlib only. No imports of the extension, no torch, no network.

Usage:
    python analyze_probe.py <probe.jsonl> [--label NAME] [--out-dir DIR]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import statistics
import sys
from datetime import datetime, timezone

ANALYZER_VERSION = "occ3.probe.analyser.v1"
VERDICT_SCHEMA = "occ3.probe.verdict.v1"

# The 24 keys of calibration_probe.PROBE_KEYS, in source order. Kept as a literal so the
# analyser runs without the extension and without torch.
PROBE_KEYS = (
    "rows", "hidden_dim", "union_rank",
    "hidden_eff_rank", "embedding_eff_rank",
    "canonical_top1", "canonical_top8", "canonical_top32", "canonical_mean",
    "canonical_participation", "canonical_above_090", "canonical_above_095",
    "canonical_above_099", "canonical_decile",
    "heldout_r2_within_turn", "procrustes_residual",
    "cos_prefix_to_embedding", "relative_prefix_to_embedding",
    "hidden_roll_sensitivity", "hidden_permute_sensitivity",
    "prefix_norm_mean", "hidden_norm_mean", "embedding_norm_mean",
    "nonfinite_count",
)
EXTRA_KEYS = ("anchor_alpha", "max_prefix_tokens")
EXPECTED_KEYS = PROBE_KEYS + EXTRA_KEYS
INT_KEYS = ("rows", "hidden_dim", "union_rank", "canonical_above_090", "canonical_above_095",
            "canonical_above_099", "nonfinite_count", "max_prefix_tokens")
DECISIVE_KEYS = ("heldout_r2_within_turn", "cos_prefix_to_embedding")
LIST_GROUPS = 10  # calibration_probe._LIST_GROUPS

# ---------------------------------------------------------------------------------------
# Pre-registered decision rule. Fixed before the window was run.
# ---------------------------------------------------------------------------------------
R2_FEASIBLE = 0.30          # median heldout_r2_within_turn must reach this
R2_REJECT = 0.10            # median below this is NOT-FEASIBLE
COS_MAX = 0.30              # median cos_prefix_to_embedding must stay at or below this
THRESHOLDS = {
    "r2_feasible": R2_FEASIBLE,
    "r2_reject": R2_REJECT,
    "cos_max": COS_MAX,
    "statistic": "cross-turn median of the per-turn value",
    "precedence": [
        "1. any decisive key missing on any turn -> INCONCLUSIVE (no verdict on partial evidence)",
        "2. median heldout_r2_within_turn >= 0.30 and median cos_prefix_to_embedding <= 0.30 -> FEASIBLE",
        "3. median heldout_r2_within_turn < 0.10 -> NOT-FEASIBLE",
        "4. otherwise -> INCONCLUSIVE",
    ],
}
THRESHOLD_REASONS = {
    "r2_feasible": (
        "A frozen hidden->embedding map must explain at least 30% of the held-out embedding "
        "variance of a turn to be worth the window, and 0.30 is three times the artifact's own "
        "ship gate; the level was fixed before the data, not fitted to it."),
    "r2_reject": (
        "0.10 is exactly statebridge_calibrated.MIN_HELDOUT_R2, the level below which "
        "fit_calibration refuses to freeze a calibration artifact, so a corpus that scores "
        "below it cannot produce a usable map at all."),
    "cos_max": (
        "The shipped solver fits the rotation on the very rows it is scored on, so "
        "cos_prefix_to_embedding saturates near 1 for independent rows as well (proven: "
        "A == target copier, statebridge.py:58-63; measured 0.99999995 on independent 40x96 rows). "
        "A cosine above 0.30 therefore cannot certify that the prefix carries embedding content, "
        "while a cosine at or below 0.30 proves the prefix is measurably not that in-sample copier."),
}

MISSING = "MISSING"
FLOAT_FMT = ".4g"


# ---------------------------------------------------------------------------------------
# Loading and validation
# ---------------------------------------------------------------------------------------
class ProbeError(Exception):
    pass


def _is_number(value):
    return type(value) in (int, float) and not isinstance(value, bool)


def load_probe(path):
    """Return (turns, problems, missing). Refuses non-scalar payloads."""
    turns, problems, missing = [], [], []
    with open(path, "r", encoding="utf-8") as handle:
        for number, line in enumerate(handle, start=1):
            text = line.strip()
            if not text:
                continue
            try:
                payload = json.loads(text)
            except json.JSONDecodeError as error:
                raise ProbeError(f"line {number}: not JSON ({error.msg})") from None
            if not isinstance(payload, dict):
                raise ProbeError(f"line {number}: not a JSON object")
            absent = [key for key in EXPECTED_KEYS if key not in payload]
            extra = sorted(set(payload) - set(EXPECTED_KEYS))
            for key, value in payload.items():
                if key == "canonical_decile":
                    if not isinstance(value, list) or not value:
                        raise ProbeError(f"line {number}: canonical_decile is not a non-empty list")
                    if not all(_is_number(item) for item in value):
                        raise ProbeError(f"line {number}: canonical_decile holds a non-numeric element")
                    if len(value) > LIST_GROUPS:
                        problems.append(f"line {number}: canonical_decile has {len(value)} entries")
                    continue
                if key not in EXPECTED_KEYS:
                    # An extra key is reported, never interpreted. A nested value could carry a
                    # tensor, so it is refused outright rather than stored.
                    if isinstance(value, (list, tuple, dict, set)):
                        raise ProbeError(
                            f"line {number}: unexpected key {key!r} carries a nested value; "
                            "refusing to read tensor data")
                    continue
                if not _is_number(value):
                    raise ProbeError(
                        f"line {number}: key {key!r} is not a scalar; refusing to read tensor data")
            if extra:
                problems.append(f"line {number}: unexpected keys {extra}")
            if absent:
                missing.append((len(turns) + 1, absent))
            for key in absent:
                payload[key] = None
            payload["_line"] = number
            turns.append(payload)
    if not turns:
        raise ProbeError("no records found")
    return turns, problems, missing


# ---------------------------------------------------------------------------------------
# Formatting helpers
# ---------------------------------------------------------------------------------------
def fmt(value, key=None):
    """4 significant figures; MISSING for an absent value; integers for count keys."""
    if value is None:
        return MISSING
    if key in INT_KEYS:
        return str(int(value))
    if isinstance(value, float):
        if not math.isfinite(value):
            return "nonfinite"
        text = format(0.0 if value == 0.0 else value, FLOAT_FMT)
        return text
    return str(value)


def column_table(headers, rows):
    widths = [len(head) for head in headers]
    for row in rows:
        widths = [max(width, len(cell)) for width, cell in zip(widths, row)]
    head = "  ".join(head.rjust(width) for head, width in zip(headers, widths))
    rule = "  ".join("-" * width for width in widths)
    body = ["  ".join(cell.rjust(width) for cell, width in zip(row, widths)) for row in rows]
    return [head, rule, *body]


def present(turns, key):
    return [turn[key] for turn in turns if turn.get(key) is not None]


def summarise(turns, key):
    if key == "canonical_decile":
        return {"median": None, "min": None, "max": None, "n": 0}
    values = present(turns, key)
    if not values:
        return {"median": None, "min": None, "max": None, "n": 0}
    return {"median": statistics.median(values), "min": min(values), "max": max(values),
            "n": len(values)}


# ---------------------------------------------------------------------------------------
# Canonical-spectrum reconstruction from the decile means
# ---------------------------------------------------------------------------------------
def group_layout(union_rank, deciles):
    """Reproduce calibration_probe's grouping: step = max(1, size//10), starts 0, step, ... truncated to 10."""
    step = max(1, union_rank // LIST_GROUPS)
    count = min(len(deciles), len(range(0, union_rank, step)))
    groups = []
    for index in range(count):
        start = index * step
        stop = min(start + step, union_rank)
        groups.append((start, stop, float(deciles[index])))
    coverage = groups[-1][1] if groups else 0
    return step, groups, coverage


def top_k_sum(entry, k):
    """Sum of the squared canonical correlations over the top k directions.

    Returns (value, status) with status in {"exact", "under", "tail"}:
      exact - k ends on a decile group boundary, so the group means give the sum exactly;
      under - k falls inside a group, so the group mean is used and the sum is a lower bound;
      tail  - k reaches past the reported groups, so only the covered part is known.
    """
    groups = entry.get("groups") or []
    if not groups or k is None:
        return None, None
    total = 0.0
    for start, stop, mean in groups:
        if k >= stop:
            total += mean * mean * (stop - start)
        elif k > start:
            return total + mean * mean * (k - start), "under"
        else:
            return total, "exact"
    return total, ("exact" if k == groups[-1][1] else "tail")


def render_sum(entry, k):
    if entry.get("union_rank") is not None and k > int(entry["union_rank"]):
        return "-"
    value, status = top_k_sum(entry, k)
    if value is None:
        return MISSING
    text = fmt(value)
    return text if status == "exact" else text + "*"


def spectrum_tables(turns):
    """Per-turn k_min and the prefix sums of the squared canonical correlations."""
    out = []
    for number, turn in enumerate(turns, start=1):
        entry = {"turn": number, "union_rank": turn.get("union_rank"),
                 "r2": turn.get("heldout_r2_within_turn"), "k_min": None,
                 "k_status": MISSING, "note": "", "groups": [], "coverage": None,
                 "step": None, "sum_covered": None}
        rank, deciles = turn.get("union_rank"), turn.get("canonical_decile")
        if rank is None or deciles is None:
            entry["note"] = "union_rank or canonical_decile missing"
            out.append(entry)
            continue
        step, groups, coverage = group_layout(int(rank), deciles)
        entry.update(step=step, coverage=coverage, groups=groups,
                     sum_covered=sum(mean * mean * (stop - start)
                                     for start, stop, mean in groups))
        if entry["r2"] is None:
            entry["note"] = "heldout_r2_within_turn missing"
            out.append(entry)
            continue
        target = max(float(entry["r2"]), 0.0)
        k_min = None
        running = 0.0
        for start, stop, mean in groups:
            for offset in range(1, stop - start + 1):
                if running + mean * mean * offset >= target:
                    k_min = start + offset
                    break
            if k_min is not None:
                break
            running += mean * mean * (stop - start)
        if k_min is None:
            entry["note"] = (f"R2 exceeds the covered spectrum: sum "
                             f"{entry['sum_covered']:.4g} over {coverage} of {rank} directions")
            out.append(entry)
            continue
        boundaries = {stop for _, stop, _ in groups}
        entry["k_min"] = k_min
        if k_min == 1:
            entry["k_status"] = "exact"
            entry["note"] = ("R2 <= 0: the bound is met trivially at k = 1" if entry["r2"] <= 0.0
                             else "k = 1 is the floor of the domain")
        elif k_min in boundaries and (k_min - 1) in boundaries:
            entry["k_status"] = "exact"
            entry["note"] = "the bound is met exactly on a decile boundary"
        else:
            entry["k_status"] = "upper bound"
            entry["note"] = "inside a decile group: the sum is a lower bound, so k_min is an upper bound"
        out.append(entry)
    return out


def decile_summaries(turns):
    """Cross-turn median, min and max of each decile group."""
    columns = {}
    depth = max((len(turn.get("canonical_decile") or []) for turn in turns), default=0)
    for index in range(depth):
        values = [turn["canonical_decile"][index] for turn in turns
                  if turn.get("canonical_decile") and len(turn["canonical_decile"]) > index]
        if values:
            columns[index] = {"median": statistics.median(values), "min": min(values),
                              "max": max(values), "n": len(values)}
        else:
            columns[index] = {"median": None, "min": None, "max": None, "n": 0}
    return columns


def rank_report(turns, spectrum):
    """Cross-turn rank estimate and its limits."""
    ks = [entry["k_min"] for entry in spectrum if entry["k_min"] is not None]
    ranks = [int(entry["union_rank"]) for entry in spectrum if entry["union_rank"] is not None]
    at_one = []
    for entry in spectrum:
        value, _ = top_k_sum(entry, 1)
        if value is not None and entry["r2"] is not None and max(entry["r2"], 0.0) <= value:
            at_one.append(entry["turn"])
    return {
        "median_k_min": statistics.median(ks) if ks else None,
        "min_k_min": min(ks) if ks else None,
        "max_k_min": max(ks) if ks else None,
        "turns_with_k": len(ks),
        "turns_total": len(spectrum),
        "median_union_rank": statistics.median(ranks) if ranks else None,
        "k_min_equals_one": len(at_one),
        "k_min_one_turns": at_one,
        "coverage": [entry.get("coverage") for entry in spectrum],
    }


# ---------------------------------------------------------------------------------------
# Decision rule
# ---------------------------------------------------------------------------------------
def _interpretation(rank):
    """Data-driven reading of the rank estimate."""
    if not rank["turns_total"] or not rank["turns_with_k"]:
        return "no turn carried a usable spectrum, so no rank estimate is possible"
    if rank["k_min_equals_one"] == rank["turns_total"]:
        return ("the bound is satisfied at k = 1 in every turn, so the canonical spectrum does "
                "not resolve the intrinsic rank; the estimate is a floor set by quantisation, "
                "and only held-out R2 separates a relation from none")
    return (f"the bound is met above k = 1 in {rank['turns_total'] - rank['k_min_equals_one']} of "
            f"{rank['turns_total']} turns, so the spectrum does order the directions there; "
            f"each k_min is an upper bound, because a prefix sum taken inside a decile group is "
            f"a lower bound on the true sum")


def evaluate(turns, missing):
    medians = {key: summarise(turns, key)["median"] for key in DECISIVE_KEYS}
    absent = sorted({key for _, keys in missing for key in keys if key in DECISIVE_KEYS})
    incomplete = bool(absent) or any(medians[key] is None for key in DECISIVE_KEYS)
    r2, cos = medians["heldout_r2_within_turn"], medians["cos_prefix_to_embedding"]
    detail = {
        "median_heldout_r2_within_turn": r2,
        "median_cos_prefix_to_embedding": cos,
        "missing_decisive_keys": absent,
        "turns": len(turns),
        "turns_with_missing_decisive": sum(
            1 for turn in turns
            if any(turn.get(key) is None for key in DECISIVE_KEYS)),
    }
    if incomplete:
        detail["reason"] = ("decisive key missing: "
                            + (", ".join(absent) if absent else "no usable value")
                            + "; no verdict is issued on partial evidence")
        return "INCONCLUSIVE", detail
    if r2 >= R2_FEASIBLE and cos <= COS_MAX:
        detail["reason"] = (f"median R2 {r2:.4g} >= {R2_FEASIBLE} and median cos "
                            f"{cos:.4g} <= {COS_MAX}")
        return "FEASIBLE", detail
    if r2 < R2_REJECT:
        detail["reason"] = f"median R2 {r2:.4g} < {R2_REJECT}: no out-of-sample relation"
        return "NOT-FEASIBLE", detail
    if r2 >= R2_FEASIBLE and cos > COS_MAX:
        detail["reason"] = (f"relation present (median R2 {r2:.4g}) but median cos {cos:.4g} > "
                            f"{COS_MAX}: the prefix saturates as the in-sample copier, so the "
                            f"window cannot separate the copier from a real relation")
        return "INCONCLUSIVE", detail
    detail["reason"] = (f"median R2 {r2:.4g} sits between {R2_REJECT} and {R2_FEASIBLE}; "
                        f"the pre-registered rule has no verdict in this band")
    return "INCONCLUSIVE", detail


# ---------------------------------------------------------------------------------------
# Markdown
# ---------------------------------------------------------------------------------------
def build_markdown(path, turns, problems, missing, spectrum, verdict, digest):
    medians = {key: summarise(turns, key) for key in EXPECTED_KEYS}
    rank = rank_report(turns, spectrum)
    lines = []
    add = lines.append
    add(f"# Probe analysis — `{os.path.basename(path)}`")
    add("")
    add(f"- input: `{os.path.abspath(path)}`")
    add(f"- sha256: `{digest}`")
    add(f"- turns: {len(turns)}")
    add(f"- generated: {datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')}")
    add(f"- analyser: `{ANALYZER_VERSION}`")
    add("")
    add("Scalar values only. The analyser refuses any payload that is not an int, a float, or "
        "a flat list of floats (for `canonical_decile`), so no tensor, hidden state, embedding "
        "or token can appear in this file. Floats are printed to 4 significant figures, so a "
        "value shown as `1` can be a value just below 1; count keys are printed as integers.")
    add("")

    add("## 1. Per-turn scalars")
    add("")
    add("### 1a. Shape, spectrum and profile")
    headers = ["turn", "rows", "hidden_dim", "union_rank", "hidden_eff_rank", "embedding_eff_rank",
               "canonical_top1", "canonical_top8", "canonical_top32", "canonical_mean",
               "canonical_participation", "n>0.90", "n>0.95", "n>0.99", "anchor_alpha",
               "max_prefix_tokens"]
    keys = ["rows", "hidden_dim", "union_rank", "hidden_eff_rank", "embedding_eff_rank",
            "canonical_top1", "canonical_top8", "canonical_top32", "canonical_mean",
            "canonical_participation", "canonical_above_090", "canonical_above_095",
            "canonical_above_099", "anchor_alpha", "max_prefix_tokens"]
    body = [[str(number)] + [fmt(turn.get(key), key) for key in keys]
            for number, turn in enumerate(turns, start=1)]
    add("```")
    lines.extend(column_table(headers, body))
    add("```")
    add("")
    add("### 1b. Relation statistics")
    headers = ["turn", "heldout_r2_within_turn", "cos_prefix_to_embedding",
               "relative_prefix_to_embedding", "procrustes_residual", "hidden_roll_sensitivity",
               "hidden_permute_sensitivity", "prefix_norm_mean", "hidden_norm_mean",
               "embedding_norm_mean", "nonfinite_count"]
    keys = ["heldout_r2_within_turn", "cos_prefix_to_embedding", "relative_prefix_to_embedding",
            "procrustes_residual", "hidden_roll_sensitivity", "hidden_permute_sensitivity",
            "prefix_norm_mean", "hidden_norm_mean", "embedding_norm_mean", "nonfinite_count"]
    body = [[str(number)] + [fmt(turn.get(key), key) for key in keys]
            for number, turn in enumerate(turns, start=1)]
    add("```")
    lines.extend(column_table(headers, body))
    add("```")
    add("")
    add("### 1c. Canonical deciles (group means of the retained canonical correlations)")
    add("")
    width = max((len(turn.get("canonical_decile") or []) for turn in turns), default=0)
    headers = ["turn"] + [f"d{index + 1}" for index in range(width)]
    body = []
    for number, turn in enumerate(turns, start=1):
        deciles = turn.get("canonical_decile")
        cells = [MISSING] * width
        if deciles:
            cells[:len(deciles)] = [fmt(value) for value in deciles]
        body.append([str(number)] + cells)
    add("```")
    lines.extend(column_table(headers, body))
    add("```")
    add("")

    add("## 2. Cross-turn statistics")
    add("")
    headers = ["key", "median", "min", "max", "n"]
    body = []
    for key in EXPECTED_KEYS:
        if key == "canonical_decile":
            continue
        stats = medians[key]
        body.append([key, fmt(stats["median"], key), fmt(stats["min"], key),
                     fmt(stats["max"], key), str(stats["n"])])
    for index, stats in sorted(decile_summaries(turns).items()):
        body.append([f"canonical_decile[d{index + 1}]", fmt(stats["median"]),
                     fmt(stats["min"]), fmt(stats["max"]), str(stats["n"])])
    add("```")
    lines.extend(column_table(headers, body))
    add("```")
    add("")
    add(f"Medians, minima and maxima are taken over the turns that carry the key "
        f"(`n` of {len(turns)}). A key absent from every turn prints {MISSING}.")
    add("")

    add("## 3. Intrinsic rank from the canonical spectrum")
    add("")
    add("### 3a. The bound")
    add("")
    add("Canonical correlation analysis of the centred hidden rows `H` and the centred embedding")
    add("rows `E` gives correlations `rho_1 >= rho_2 >= ... >= rho_r` on the retained subspace of")
    add("dimension `r = union_rank`. For any `k <= r`, the in-sample canonical `R^2` of the best")
    add("linear predictor that uses `k` directions is `sum_{i<=k} rho_i^2`, and no predictor of the")
    add("embedding from the hidden rows can exceed it. Hence the stated bound")
    add("")
    add("```")
    add("    R2_heldout_within_turn  <=  sum_{i=1..k} rho_i^2        for the retained dimension k <= union_rank")
    add("    R2_heldout_within_turn  <=  sum_{i=1..union_rank} rho_i^2 = in-sample canonical R2")
    add("```")
    add("")
    add("The intrinsic rank estimate is the smallest `k` that the measured held-out `R2` does not")
    add("already exhaust:")
    add("")
    add("```")
    add("    k_min = min { k : sum_{i<=k} rho_i^2 >= max(R2_heldout_within_turn, 0) }")
    add("```")
    add("")
    add("`k_min` is a floor on the number of directions the relation needs, not a measurement of")
    add("its rank: a larger `R2` can only push `k_min` up, never down.")
    add("")
    add("### 3b. Per-turn estimate")
    add("")
    headers = ["turn", "union_rank", "heldout_r2_within_turn", "k_min", "k_min_status",
               "sum_top1", "sum_top8", "sum_top32", "sum_covered", "note"]
    body = []
    for entry in spectrum:
        body.append([str(entry["turn"]), fmt(entry["union_rank"], "union_rank"),
                     fmt(entry["r2"]), fmt(entry["k_min"], "union_rank"),
                     entry["k_status"], render_sum(entry, 1), render_sum(entry, 8),
                     render_sum(entry, 32), fmt(entry.get("sum_covered")), entry["note"] or "-"])
    add("```")
    lines.extend(column_table(headers, body))
    add("```")
    add("")
    add("A `*` marks a prefix sum taken inside a decile group, where the group mean is used and the")
    add("sum is therefore a lower bound.")
    add("")
    add("### 3c. Cross-turn estimate and its limits")
    add("")
    add(f"- median `k_min` = {fmt(rank['median_k_min'], 'union_rank')}, "
        f"min = {fmt(rank['min_k_min'], 'union_rank')}, max = {fmt(rank['max_k_min'], 'union_rank')} "
        f"over {rank['turns_with_k']} of {rank['turns_total']} turns")
    add(f"- median `union_rank` = {fmt(rank['median_union_rank'], 'union_rank')}")
    add(f"- `k_min` = 1 in {rank['k_min_equals_one']} of {rank['turns_total']} turns "
        f"(turns {rank['k_min_one_turns']})")
    add(f"- covered deciles per turn (out of `union_rank`): {rank['coverage']}")
    add("")
    add("Reading of the estimate:")
    add("")
    if rank["turns_total"] and rank["k_min_equals_one"] == rank["turns_total"]:
        add("1. The single-direction sum `rho_1^2` already covers the measured `R2` in every turn, "
            "so the bound is satisfied at `k = 1` throughout and the canonical spectrum does not "
            "resolve the rank of the relation. `k_min` therefore carries no information beyond "
            "that floor. Whitening makes two full-rank clouds congruent: this is the same "
            "saturation that `calibration_probe.canonical_spectrum` documents, and it holds for "
            "independent rows too.")
    else:
        add(f"1. The bound is met above `k = 1` in {rank['turns_total'] - rank['k_min_equals_one']} "
            f"of {rank['turns_total']} turns, so the spectrum does order the directions in those "
            "turns; read `k_min` as an upper bound, because a prefix sum taken inside a decile "
            "group is a lower bound on the true sum.")
    add("2. Only `heldout_r2_within_turn` separates a relation from no relation. The spectrum "
        "cannot, and a large `canonical_top8` is not evidence of one.")
    add("3. The probe reports group means, so a prefix sum is exact only when `k` ends a decile "
        f"group (`step = union_rank // {LIST_GROUPS}`); inside a group the group mean is used, "
        "which underestimates the prefix sum and therefore overestimates `k_min`. Such entries "
        "are marked `under` in `k_min_status` and with a `*` in the sum columns.")
    add(f"4. The probe keeps at most {LIST_GROUPS} decile groups, so a turn with "
        f"`union_rank > {LIST_GROUPS} * step` has an unquantised tail; `sum_covered` reports "
        "what is actually known.")
    add("")
    add("")
    add("## 4. Pre-registered decision rule")
    add("")
    add(f"- FEASIBLE needs `median heldout_r2_within_turn >= {R2_FEASIBLE}`. "
        f"{THRESHOLD_REASONS['r2_feasible']}")
    add(f"- NOT-FEASIBLE when `median heldout_r2_within_turn < {R2_REJECT}`. "
        f"{THRESHOLD_REASONS['r2_reject']}")
    add(f"- FEASIBLE also needs `median cos_prefix_to_embedding <= {COS_MAX}`. "
        f"{THRESHOLD_REASONS['cos_max']}")
    add("")
    add("The rule is applied to the cross-turn median of every turn in the file, in this order:")
    add("")
    for step in THRESHOLDS["precedence"]:
        add(f"- {step}")
    add("")
    add("Why the cosine bound belongs to this rule: the shipped fit solves a Procrustes rotation")
    add("on the transmitted rows and scores it on those same rows, so `prefix` reproduces the")
    add("target embedding by construction (`statebridge.py:58-63`, `A == target copier`). The")
    add("degeneracy is proven, not estimated, so a large cosine is expected even when the two row")
    add("sets are unrelated. A cosine at or below 0.30 is the only reading that cannot be produced")
    add("by the copier, which is why FEASIBLE requires it; a large cosine leaves the window unable")
    add("to separate the copier from a real relation, which is INCONCLUSIVE, not FEASIBLE.")
    add("")
    add("## 5. Verdict")
    add("")
    add(f"**{verdict[0]}** — {verdict[1]['reason']}")
    add("")
    add("```")
    add(f"median heldout_r2_within_turn = {fmt(verdict[1]['median_heldout_r2_within_turn'])}")
    add(f"median cos_prefix_to_embedding = {fmt(verdict[1]['median_cos_prefix_to_embedding'])}")
    add(f"turns = {verdict[1]['turns']}")
    add(f"turns with a missing decisive key = {verdict[1]['turns_with_missing_decisive']}")
    add("```")
    add("")
    add("## 6. Missing values and refusals")
    add("")
    if missing:
        add("```")
        lines.extend(column_table(["turn", "missing keys"],
                                  [[str(number), ", ".join(keys)] for number, keys in missing]))
        add("```")
    else:
        add("No key is missing: every turn carries all "
            f"{len(EXPECTED_KEYS)} expected keys.")
    add("")
    if problems:
        add("Other observations:")
        add("")
        for note in problems:
            add(f"- {note}")
        add("")
    add("## 7. Limits of this analysis")
    add("")
    add("1. `heldout_r2_within_turn` is produced by a fixed 50/50 split of the turn's own rows, so "
        "it is a within-turn estimate and it is noisy when a turn has few rows.")
    add("2. `hidden_permute_sensitivity` depends on `torch.randperm` and therefore differs between "
        "runs on the same input; it is reported but it is not part of any threshold.")
    add("3. The analyser reads scalars only. It cannot re-derive, re-fit or re-score a turn, and it "
        "does not claim to.")
    add("4. `cos_prefix_to_embedding` is an in-sample statistic by construction. It can show that "
        "the prefix is not the copier; it cannot show that the relation generalises. Only "
        "`heldout_r2_within_turn` speaks to generalisation.")
    add("")
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------------------
def analyse(path, label=None):
    turns, problems, missing = load_probe(path)
    spectrum = spectrum_tables(turns)
    verdict_text, verdict_detail = evaluate(turns, missing)
    digest = hashlib.sha256(open(path, "rb").read()).hexdigest()
    stats = {key: summarise(turns, key) for key in EXPECTED_KEYS}
    rank = rank_report(turns, spectrum)
    verdict = {
        "schema": VERDICT_SCHEMA,
        "analyzer": ANALYZER_VERSION,
        "label": label or os.path.splitext(os.path.basename(path))[0],
        "input": os.path.abspath(path),
        "input_sha256": digest,
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "turns": len(turns),
        "expected_keys": list(EXPECTED_KEYS),
        "missing": [{"turn": number, "keys": keys} for number, keys in missing],
        "verdict": verdict_text,
        "rule": THRESHOLDS,
        "threshold_reasons": THRESHOLD_REASONS,
        "detail": verdict_detail,
        "statistics": {key: {name: value for name, value in values.items()}
                       for key, values in stats.items() if key != "canonical_decile"},
        "canonical_decile_medians": [statistics.median(column) for column in zip(
            *[turn["canonical_decile"] for turn in turns if turn.get("canonical_decile")])],
        "rank": {
            "bound": ("R2_heldout_within_turn <= sum_{i<=k} rho_i^2 for the retained dimension "
                      "k <= union_rank"),
            "k_min_definition": ("min k such that sum_{i<=k} rho_i^2 >= max(R2_heldout_within_turn, 0)"),
            "median_k_min": rank["median_k_min"],
            "min_k_min": rank["min_k_min"],
            "max_k_min": rank["max_k_min"],
            "k_min_equals_one_turns": rank["k_min_one_turns"],
            "median_union_rank": rank["median_union_rank"],
            "per_turn": [{key: entry.get(key) for key in
                          ("turn", "union_rank", "r2", "k_min", "k_status", "coverage", "step",
                           "sum_covered", "note")}
                         for entry in spectrum],
            "interpretation": _interpretation(rank),
        },
        "provenance": {
            "input_keys": "calibration_probe.PROBE_KEYS (24) plus anchor_alpha and max_prefix_tokens",
            "tensor_data": "none; scalars only, non-scalar payloads refused",
            "offline": True,
        },
    }
    return turns, problems, missing, spectrum, (verdict_text, verdict_detail), verdict, digest


def main(argv=None):
    parser = argparse.ArgumentParser(description="Analyse one OCC-3 probe JSONL (scalars only).")
    parser.add_argument("jsonl", help="probe JSONL written by the OCC3_ALIGN_PROBE hook")
    parser.add_argument("--label", default=None, help="label for the verdict JSON")
    parser.add_argument("--out-dir", default=None,
                        help="write the summary and verdict here instead of next to the JSONL")
    arguments = parser.parse_args(argv)
    path = arguments.jsonl
    try:
        turns, problems, missing, spectrum, verdict, payload, digest = analyse(path, arguments.label)
    except (ProbeError, OSError) as error:
        print(f"analyze_probe.py: {error}", file=sys.stderr)
        return 2

    medians = {key: summarise(turns, key) for key in EXPECTED_KEYS}
    print("=" * 100)
    print(f"probe: {os.path.abspath(path)}")
    print(f"sha256: {digest}")
    print(f"turns: {len(turns)}  keys per turn: {len(EXPECTED_KEYS)}")
    print("=" * 100)
    print("\nPER-TURN: shape, spectrum, profile")
    headers = ["turn", "rows", "dim", "rank", "hid_eff", "emb_eff", "top1", "top8", "top32",
               "mean", "part", "n>.90", "n>.95", "n>.99", "anchor", "max_pref"]
    keys = ["rows", "hidden_dim", "union_rank", "hidden_eff_rank", "embedding_eff_rank",
            "canonical_top1", "canonical_top8", "canonical_top32", "canonical_mean",
            "canonical_participation", "canonical_above_090", "canonical_above_095",
            "canonical_above_099", "anchor_alpha", "max_prefix_tokens"]
    body = [[str(number)] + [fmt(turn.get(key), key) for key in keys]
            for number, turn in enumerate(turns, start=1)]
    print("\n".join(column_table(headers, body)))
    print("\nPER-TURN: relation statistics")
    headers = ["turn", "R2_heldout", "cos_pref", "rel_pref", "procrustes", "roll", "perm",
               "|pref|", "|hid|", "|emb|", "nonfin"]
    keys = ["heldout_r2_within_turn", "cos_prefix_to_embedding", "relative_prefix_to_embedding",
            "procrustes_residual", "hidden_roll_sensitivity", "hidden_permute_sensitivity",
            "prefix_norm_mean", "hidden_norm_mean", "embedding_norm_mean", "nonfinite_count"]
    body = [[str(number)] + [fmt(turn.get(key), key) for key in keys]
            for number, turn in enumerate(turns, start=1)]
    print("\n".join(column_table(headers, body)))
    print("\nPER-TURN: canonical deciles")
    width = max((len(turn.get("canonical_decile") or []) for turn in turns), default=0)
    headers = ["turn"] + [f"d{index + 1}" for index in range(width)]
    body = []
    for number, turn in enumerate(turns, start=1):
        deciles = turn.get("canonical_decile")
        cells = [MISSING] * width
        if deciles:
            cells[:len(deciles)] = [fmt(value) for value in deciles]
        body.append([str(number)] + cells)
    print("\n".join(column_table(headers, body)))
    print("\nINTEGRITY")
    if missing:
        for number, keys in missing:
            print(f"  turn {number}: {MISSING} {', '.join(keys)}")
    else:
        print("  no missing keys")
    for note in problems:
        print(f"  note: {note}")

    print("\nCROSS-TURN: median, min, max")
    headers = ["key", "median", "min", "max", "n"]
    body = []
    for key in EXPECTED_KEYS:
        if key == "canonical_decile":
            continue
        stats = medians[key]
        body.append([key, fmt(stats["median"], key), fmt(stats["min"], key),
                     fmt(stats["max"], key), str(stats["n"])])
    for index, stats in sorted(decile_summaries(turns).items()):
        body.append([f"canonical_decile[d{index + 1}]", fmt(stats["median"]),
                     fmt(stats["min"]), fmt(stats["max"]), str(stats["n"])])
    print("\n".join(column_table(headers, body)))

    print("\nRANK: intrinsic rank of the hidden->embedding relation")
    print("  bound: R2_heldout_within_turn <= sum_{i<=k} rho_i^2 for k <= union_rank")
    print("  k_min: min k such that sum_{i<=k} rho_i^2 >= max(R2_heldout, 0)")
    headers = ["turn", "union_rank", "R2_heldout", "k_min", "k_min_status", "S(1)", "S(8)",
               "S(32)", "S(covered)", "note"]
    body = []
    for entry in spectrum:
        body.append([str(entry["turn"]), fmt(entry["union_rank"], "union_rank"), fmt(entry["r2"]),
                     fmt(entry["k_min"], "union_rank"), entry["k_status"],
                     render_sum(entry, 1), render_sum(entry, 8), render_sum(entry, 32),
                     fmt(entry.get("sum_covered")), entry["note"] or "-"])
    print("\n".join(column_table(headers, body)))
    print("  S(k) = sum of the squared canonical correlations over the top k directions;")
    print("  * marks a lower bound taken inside a decile group.")
    rank = rank_report(turns, spectrum)
    print(f"  median k_min = {fmt(rank['median_k_min'], 'union_rank')}  "
          f"min = {fmt(rank['min_k_min'], 'union_rank')}  "
          f"max = {fmt(rank['max_k_min'], 'union_rank')}  "
          f"(k_min = 1 in {rank['k_min_equals_one']} of {rank['turns_total']} turns)")

    print("\nRULE (pre-registered)")
    print(f"  FEASIBLE      median heldout_r2_within_turn >= {R2_FEASIBLE} "
          f"and median cos_prefix_to_embedding <= {COS_MAX}")
    print(f"  NOT-FEASIBLE  median heldout_r2_within_turn < {R2_REJECT}")
    print("  INCONCLUSIVE  otherwise, or when a decisive key is missing")
    print(f"\nVERDICT: {verdict[0]}")
    print(f"  {verdict[1]['reason']}")

    base = os.path.splitext(os.path.basename(path))[0]
    directory = arguments.out_dir or os.path.dirname(os.path.abspath(path))
    os.makedirs(directory, exist_ok=True)
    markdown_path = os.path.join(directory, base + ".analysis.md")
    verdict_path = os.path.join(directory, base + ".verdict.json")
    with open(markdown_path, "w", encoding="utf-8") as handle:
        handle.write(build_markdown(path, turns, problems, missing, spectrum, verdict, digest))
    with open(verdict_path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, sort_keys=True)
        handle.write("\n")
    print(f"\nwrote {markdown_path}")
    print(f"wrote {verdict_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
