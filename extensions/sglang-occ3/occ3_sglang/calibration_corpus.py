"""Pooled calibration corpora and turn-disjoint held-out evaluation.

The live window measured the hidden-to-embedding relation inside single turns,
because that was all it captured: 51 fit rows of 6144 dimensions. A pooled
corpus of whole turns has strictly more capacity, and the only honest way to
score it is to hold out whole turns, never rows.

Two rules define this module:

1. A turn is the split unit. Every row of a turn is either in the calibration
   corpus or in the evaluation set. No adjacent position of one generation can
   appear on both sides.
2. A quality number is reported only when it is measurable. Fewer than
   MIN_EVALUATION_TURNS evaluation turns, or a frozen map that does not beat the
   mean predictor on the held-out turns, raises LatentError instead of returning
   a number.

Turn order is the ascending sha256 of the turn id, never insertion order, so a
row budget truncates the same turns on every run and on every host. The budget
is an upper bound: whole turns are taken from that order until the next turn
would exceed it, and the report states the budget and the rows actually used.

The controls are the campaign's matched arms as a scoring library, not a live
runner: correct state, wrong-sender state, shuffled state, matched-shape null
state, no attachment, and reference-token-only. A later task wires them to the
GPU. They are kept mathematically distinct so a null or shuffled arm cannot be
mistaken for the correct arm.

What one evaluation reports, all scalar:
  eval_r2, eval_relative_error, eval_cosine   quality on whole held-out turns
  fit_heldout_r2                              the fit's own row-order estimate
  refit_sensitivity                           output change when the map is
                                              re-fitted on a rolled calibration
                                              block (correspondence destroyed)
  hidden_roll_sensitivity                     output change when the evaluated
  hidden_permute_sensitivity                  hidden rows are rolled/reversed
  independent_reference_sensitivity/-_cosine  an unrelated sender's hidden rows
                                              through the same frozen map
  token_reference_cosine                      an unrelated turn's token channel
                                              against this turn's embeddings
  separation                                  per-control correct-minus-control
                                              margin and accuracy, if supplied
  corpus_digest, artifact_digest              what was fitted, exactly
A metric that was not measured is None, never zero: absent controls and an
unbound artifact are reported as absent.
"""
from __future__ import annotations

import hashlib
import math
import re
from dataclasses import dataclass

from .latent_types import require
from .statebridge_calibrated import (
    DEFAULT_RIDGE_REL, MIN_CALIBRATION_ROWS, MIN_HELDOUT_R2,
    CalibrationProvenance, apply_calibration, artifact_digest, fit_calibration,
)

M_ROW_BUDGETS = (512, 1024, 2048)
MIN_EVALUATION_TURNS = 4
MIN_TURN_ROWS = 2
DEFAULT_EVAL_STRIDE = 3
TURN_ORDER_DOMAIN = "optimus.occ3.calibration-turn-order.v1"
NORM_FLOOR = 1e-6
# Only the re-fit diagnostic fits a corpus whose quality guard must be off.
REFIT_DIAGNOSTIC_MIN_R2 = -1.0

CONTROL_KINDS = ("correct", "wrong-sender", "shuffled", "null",
                 "no-attachment", "reference-token-only")
CONTROL_SCORE_KEYS = ("kind", "source", "rows", "attached", "cosine", "relative_error")
SOURCE_HIDDEN = "hidden"
SOURCE_TOKEN = "token-reference"
SOURCE_NONE = "none"

EVALUATION_KEYS = (
    "row_budget", "rows_used", "available_fit_turns", "fit_turns", "eval_turns",
    "fit_rows", "eval_rows", "ridge_rel", "ridge", "fit_heldout_r2",
    "eval_r2", "eval_relative_error", "eval_cosine",
    "refit_sensitivity", "hidden_roll_sensitivity", "hidden_permute_sensitivity",
    "independent_reference_sensitivity", "independent_reference_cosine",
    "token_reference_cosine", "corpus_digest", "artifact_digest", "separation",
)
_OPTIONAL_FLOATS = ("independent_reference_sensitivity", "independent_reference_cosine",
                    "token_reference_cosine")
_COUNTS = ("row_budget", "rows_used", "available_fit_turns", "fit_turns", "eval_turns",
           "fit_rows", "eval_rows")
_FLOATS = ("ridge_rel", "ridge", "fit_heldout_r2", "eval_r2", "eval_relative_error",
           "eval_cosine", "refit_sensitivity", "hidden_roll_sensitivity",
           "hidden_permute_sensitivity")
_SEPARATION_KEYS = frozenset({"trials", "delta_margin", "delta_accuracy"})
_IDENTIFIER = re.compile(r"[A-Za-z0-9._:/-]{1,256}\Z")


def _torch():
    import torch
    return torch


def _identifier(value):
    return type(value) is str and _IDENTIFIER.fullmatch(value) is not None


@dataclass(frozen=True)
class TurnBlock:
    """Every row of one captured turn, tagged by its turn id.

    reference_embeddings is the token channel of the same span (no hidden
    state). wrong_sender_hidden is a matched-shape hidden block from a
    different sender. Both are optional because they are controls, and a
    control that was not captured must read as absent, never as zero.
    """
    turn_id: str
    hidden: object
    embeddings: object
    reference_embeddings: object = None
    wrong_sender_hidden: object = None

    def __post_init__(self):
        require(_identifier(self.turn_id), "profile")
        require(getattr(self.hidden, "ndim", None) == 2
                and getattr(self.embeddings, "ndim", None) == 2, "profile")
        require(tuple(int(x) for x in self.hidden.shape)
                == tuple(int(x) for x in self.embeddings.shape), "profile")
        rows, dim = (int(x) for x in self.hidden.shape)
        require(rows >= MIN_TURN_ROWS and dim > 2, "profile")
        for optional in (self.reference_embeddings, self.wrong_sender_hidden):
            if optional is not None:
                require(getattr(optional, "ndim", None) == 2
                        and tuple(int(x) for x in optional.shape) == (rows, dim), "profile")

    @property
    def rows(self):
        return int(self.hidden.shape[0])


def turn_order_key(turn_id):
    """Stable position of one turn in the digest order; independent of arrival."""
    require(_identifier(turn_id), "profile")
    return hashlib.sha256(f"{TURN_ORDER_DOMAIN}|{turn_id}".encode("utf-8")).hexdigest()


def ordered_blocks(blocks):
    """The corpus in digest order, refusing duplicate turn ids."""
    values = tuple(blocks)
    require(len(values) > 0, "profile")
    require(all(isinstance(value, TurnBlock) for value in values), "profile")
    ids = [value.turn_id for value in values]
    require(len(set(ids)) == len(ids), "identity")
    return tuple(sorted(values, key=lambda value: turn_order_key(value.turn_id)))


def split_turns(blocks, *, eval_stride=DEFAULT_EVAL_STRIDE):
    """Partition whole turns: every eval_stride-th turn evaluates, the rest fit.

    Both partitions are non-empty by construction and share no turn id, so no
    row of one generation can reach the calibration corpus and the evaluation
    set at the same time.
    """
    require(type(eval_stride) is int and 2 <= eval_stride <= 16, "profile")
    ordered = ordered_blocks(blocks)
    require(len(ordered) >= eval_stride, "profile")
    fit_blocks = tuple(value for index, value in enumerate(ordered) if index % eval_stride)
    eval_blocks = tuple(value for index, value in enumerate(ordered) if not index % eval_stride)
    require(bool(fit_blocks) and bool(eval_blocks), "profile")
    require(not ({value.turn_id for value in fit_blocks}
                 & {value.turn_id for value in eval_blocks}), "identity")
    return fit_blocks, eval_blocks


def select_calibration_rows(fit_blocks, row_budget):
    """Whole turns from the digest order, as a prefix that fits the budget.

    The first turn that would exceed the budget ends the selection, so the
    result depends only on the ordered turns and on the budget. A corpus
    smaller than the budget is not padded: the caller must report the rows it
    actually got.
    """
    require(type(row_budget) is int and row_budget in M_ROW_BUDGETS, "profile")
    ordered = ordered_blocks(fit_blocks)
    selected, rows = [], 0
    for block in ordered:
        if rows + block.rows > row_budget:
            break
        selected.append(block)
        rows += block.rows
    require(rows >= MIN_CALIBRATION_ROWS, "profile")
    return tuple(selected), rows


def pooled_rows(blocks, *, torch_module=None):
    """One row block over whole turns: hidden rows and their embeddings."""
    torch = torch_module or _torch()
    values = ordered_blocks(blocks)
    hidden = torch.cat([value.hidden for value in values], dim=0)
    embeddings = torch.cat([value.embeddings for value in values], dim=0)
    require(hidden.shape == embeddings.shape and int(hidden.shape[0]) > 0, "profile")
    return hidden.contiguous(), embeddings.contiguous()


def _optional_rows(blocks, name, torch_module):
    """Pooled optional control rows, or None when any chosen turn lacks them."""
    if any(getattr(value, name) is None for value in blocks):
        return None
    torch = torch_module or _torch()
    return torch.cat([getattr(value, name) for value in blocks], dim=0).contiguous()


def _unit(matrix, floor=NORM_FLOOR):
    return matrix / matrix.norm(dim=-1, keepdim=True).clamp_min(floor)


def _cosine(left, right):
    return float((_unit(left) * _unit(right)).sum(-1).mean())


def _scaled_target(embeddings, target_norm):
    """Per-row reference at the scalar target radius, the production convention."""
    norms = embeddings.norm(dim=-1, keepdim=True).clamp_min(NORM_FLOOR)
    require(math.isfinite(target_norm) and target_norm > 0.0, "numerical")
    return embeddings * (target_norm / norms)


def _r2(predicted, target):
    residual = float((predicted - target).pow(2).sum())
    baseline = float((target - target.mean(0, keepdim=True)).pow(2).sum())
    require(math.isfinite(residual) and baseline > 0.0, "numerical")
    return 1.0 - residual / baseline


def _relative_error(predicted, target):
    return float((predicted - target).norm() / target.norm().clamp_min(NORM_FLOOR))


def _change(left, right):
    return float((left - right).norm() / right.norm().clamp_min(NORM_FLOOR))


def _median(values):
    ordered = sorted(values)
    size = len(ordered)
    require(size > 0, "profile")
    middle = size // 2
    return float(ordered[middle]) if size % 2 else float((ordered[middle - 1] + ordered[middle]) / 2.0)


def _scores(values):
    result = [float(value) for value in values]
    require(len(result) >= 1, "profile")
    require(all(math.isfinite(value) for value in result), "numerical")
    return result


def separation(correct_scores, control_scores):
    """Paired correct-arm minus control-arm separation of per-trial margins.

    A score is the pre-registered margin log P(correct label) - max log P(other
    labels) at the first generation position, so a positive score is a correct
    trial and accuracy is the fraction of positive scores.
    """
    correct = _scores(correct_scores)
    control = _scores(control_scores)
    require(len(correct) == len(control), "profile")
    trials = len(correct)
    accuracy = (sum(1 for value in correct if value > 0) / trials
                - sum(1 for value in control if value > 0) / trials)
    return {"trials": trials,
            "delta_margin": _median([left - right for left, right in zip(correct, control)]),
            "delta_accuracy": accuracy}


def separate_candidates(candidate_scores):
    """Per-control separation against the correct arm; empty when not supplied."""
    if candidate_scores is None:
        return {}
    require(type(candidate_scores) is dict and "correct" in candidate_scores, "profile")
    require(set(candidate_scores) <= set(CONTROL_KINDS), "profile")
    controls = sorted(name for name in candidate_scores if name != "correct")
    require(bool(controls), "profile")
    return {name: separation(candidate_scores["correct"], candidate_scores[name])
            for name in controls}


def control_rows(block, kind, *, torch_module=None):
    """Rows of one matched control arm; (source, rows) with rows None when absent."""
    torch = torch_module or _torch()
    require(isinstance(block, TurnBlock) and kind in CONTROL_KINDS, "profile")
    if kind == "correct":
        return SOURCE_HIDDEN, block.hidden
    if kind == "wrong-sender":
        require(block.wrong_sender_hidden is not None, "profile")
        return SOURCE_HIDDEN, block.wrong_sender_hidden
    if kind == "shuffled":
        # Reversal, not a random permutation: reproducible without a seed.
        return SOURCE_HIDDEN, torch.flip(block.hidden, [0]).contiguous()
    if kind == "null":
        return SOURCE_HIDDEN, torch.zeros_like(block.hidden)
    if kind == "reference-token-only":
        require(block.reference_embeddings is not None, "profile")
        return SOURCE_TOKEN, block.reference_embeddings
    return SOURCE_NONE, None


def score_controls(artifact, block, *, target_norm=None, torch_module=None):
    """Score every control arm of one turn against that turn's embeddings.

    target_norm defaults to the turn's own mean row norm because a single turn
    is a diagnostic; the pooled evaluation always passes the calibration mean,
    which is the scalar the frozen map was fitted under. Only the attached arms
    produce a cosine: the no-attachment arm has no rows, so it reports
    attached=0 with no score instead of a fabricated zero.
    """
    torch = torch_module or _torch()
    require(isinstance(block, TurnBlock), "profile")
    with torch.no_grad():
        embeddings = block.embeddings.float()
        require(bool(torch.isfinite(embeddings).all()), "numerical")
        norms = embeddings.norm(dim=-1, keepdim=True).clamp_min(NORM_FLOOR)
        scalar = float(norms.mean()) if target_norm is None else float(target_norm)
        require(math.isfinite(scalar) and scalar > 0.0, "numerical")
        target = _scaled_target(embeddings, scalar)
        result = {}
        for kind in CONTROL_KINDS:
            source, rows = control_rows(block, kind, torch_module=torch)
            if rows is None:
                result[kind] = {"kind": kind, "source": source, "rows": 0, "attached": 0,
                                "cosine": None, "relative_error": None}
                continue
            rows = rows.float()
            require(bool(torch.isfinite(rows).all()), "numerical")
            prefix = (apply_calibration(artifact, rows, scalar, torch_module=torch)
                      if source == SOURCE_HIDDEN else _scaled_target(rows, scalar))
            require(bool(torch.isfinite(prefix).all()), "numerical")
            result[kind] = {"kind": kind, "source": source, "rows": int(prefix.shape[0]),
                            "attached": 1, "cosine": _cosine(prefix, target),
                            "relative_error": _relative_error(prefix, target)}
        for value in result.values():
            require(set(value) == set(CONTROL_SCORE_KEYS), "profile")
        return result


def evaluate_turn_disjoint(blocks, *, row_budget=512, eval_stride=DEFAULT_EVAL_STRIDE,
                           ridge_rel=DEFAULT_RIDGE_REL, min_eval_turns=MIN_EVALUATION_TURNS,
                           min_eval_r2=MIN_HELDOUT_R2, provenance=None,
                           candidate_scores=None, torch_module=None):
    """Fit on whole calibration turns, score on whole held-out turns.

    The artifact is fitted on the selected calibration turns only. The target
    radius is the mean row norm of the calibration embeddings, never of the
    evaluation rows, so no evaluation-side quantity reaches the map or its
    scale. A held-out R^2 below min_eval_r2 raises LatentError("numerical"):
    the corpus has no signal the frozen map can carry.
    """
    torch = torch_module or _torch()
    require(provenance is None or isinstance(provenance, CalibrationProvenance), "profile")
    require(type(min_eval_turns) is int and min_eval_turns >= 1, "profile")
    require(type(min_eval_r2) is float and math.isfinite(min_eval_r2), "profile")
    fit_blocks, eval_blocks = split_turns(blocks, eval_stride=eval_stride)
    require(len(eval_blocks) >= min_eval_turns, "profile")
    selected, rows_used = select_calibration_rows(fit_blocks, row_budget)
    hidden, embeddings = pooled_rows(selected, torch_module=torch)
    turn_ids = None if provenance is None else [value.turn_id for value in selected]
    artifact = fit_calibration(hidden, embeddings, ridge_rel=ridge_rel, provenance=provenance,
                               turn_ids=turn_ids, torch_module=torch)
    target_norm = float(embeddings.norm(dim=-1).mean())
    require(math.isfinite(target_norm) and target_norm > 0.0, "numerical")
    reference_hidden = _optional_rows(eval_blocks, "wrong_sender_hidden", torch)
    reference_tokens = _optional_rows(eval_blocks, "reference_embeddings", torch)
    eval_hidden, eval_embeddings = pooled_rows(eval_blocks, torch_module=torch)
    with torch.no_grad():
        eval_hidden = eval_hidden.float()
        eval_embeddings = eval_embeddings.float()
        require(bool(torch.isfinite(eval_hidden).all())
                and bool(torch.isfinite(eval_embeddings).all()), "numerical")
        target = _scaled_target(eval_embeddings, target_norm)
        predicted = apply_calibration(artifact, eval_hidden, target_norm, torch_module=torch)
        quality = _r2(predicted, target)
        require(quality >= min_eval_r2, "numerical")
        # Re-fit sensitivity: the calibration rows are rolled by one position, so
        # the hidden-to-embedding correspondence is destroyed, and the map is
        # fitted again. The fit's own quality guard would refuse that corpus by
        # design, so it is disabled for this diagnostic only; the resulting map is
        # never returned, serialized or described. A plain re-fit on the same rows
        # in any order reproduces the identical transform, because the means, the
        # Gram and the map are permutation invariant; only the row-order held-out
        # estimate inside the fit is not.
        refit = fit_calibration(torch.roll(hidden, 1, 0), embeddings, ridge_rel=ridge_rel,
                                min_heldout_r2=REFIT_DIAGNOSTIC_MIN_R2, torch_module=torch)
        refit_output = apply_calibration(refit, eval_hidden, target_norm, torch_module=torch)
        rolled = apply_calibration(artifact, torch.roll(eval_hidden, 1, 0), target_norm,
                                   torch_module=torch)
        permuted = apply_calibration(artifact, torch.flip(eval_hidden, [0]).contiguous(),
                                     target_norm, torch_module=torch)
        reference_sensitivity = reference_cosine = None
        if reference_hidden is not None:
            other = apply_calibration(artifact, reference_hidden.float(), target_norm,
                                      torch_module=torch)
            reference_sensitivity = _change(other, predicted)
            reference_cosine = _cosine(other, target)
        token_cosine = None
        if reference_tokens is not None:
            token_cosine = _cosine(_scaled_target(reference_tokens.float(), target_norm), target)
        result = {
            "row_budget": int(row_budget), "rows_used": int(rows_used),
            "available_fit_turns": len(fit_blocks), "fit_turns": len(selected),
            "eval_turns": len(eval_blocks), "fit_rows": int(hidden.shape[0]),
            "eval_rows": int(eval_hidden.shape[0]),
            "ridge_rel": float(artifact.ridge_rel), "ridge": float(artifact.ridge),
            "fit_heldout_r2": float(artifact.heldout_r2), "eval_r2": float(quality),
            "eval_relative_error": _relative_error(predicted, target),
            "eval_cosine": _cosine(predicted, target),
            "refit_sensitivity": _change(refit_output, predicted),
            "hidden_roll_sensitivity": _change(rolled, predicted),
            "hidden_permute_sensitivity": _change(permuted, predicted),
            "independent_reference_sensitivity": reference_sensitivity,
            "independent_reference_cosine": reference_cosine,
            "token_reference_cosine": token_cosine,
            "corpus_digest": None if artifact.binding is None else artifact.binding.corpus_digest,
            "artifact_digest": (None if artifact.binding is None
                                else artifact_digest(artifact, torch_module=torch)),
            "separation": separate_candidates(candidate_scores),
        }
    validate_evaluation(result)
    return result


def validate_evaluation(result):
    """Fixed key set, exact scalar types, bounded values. No tensor content."""
    require(isinstance(result, dict) and set(result) == set(EVALUATION_KEYS), "profile")
    for name in _COUNTS:
        value = result[name]
        require(type(value) is int and not isinstance(value, bool) and value >= 0, "profile")
    require(result["row_budget"] in M_ROW_BUDGETS, "profile")
    require(result["rows_used"] <= result["row_budget"], "profile")
    require(result["fit_turns"] <= result["available_fit_turns"] and result["eval_turns"] >= 1,
            "profile")
    for name in _FLOATS:
        value = result[name]
        require(type(value) is float and not isinstance(value, bool) and math.isfinite(value),
                "numerical")
    require(0.0 < result["ridge_rel"] < 1.0 and result["ridge"] > 0.0, "profile")
    for name in _OPTIONAL_FLOATS:
        value = result[name]
        require(value is None or (type(value) is float and math.isfinite(value)), "numerical")
    for name in ("corpus_digest", "artifact_digest"):
        value = result[name]
        require(value is None or (type(value) is str and len(value) == 64), "profile")
    require((result["corpus_digest"] is None) == (result["artifact_digest"] is None), "profile")
    separation_value = result["separation"]
    require(type(separation_value) is dict and set(separation_value) <= set(CONTROL_KINDS),
            "profile")
    for name, value in separation_value.items():
        require(name != "correct" and type(value) is dict, "profile")
        require(set(value) == _SEPARATION_KEYS, "profile")
        require(type(value["trials"]) is int and value["trials"] >= 1, "profile")
        for key in ("delta_margin", "delta_accuracy"):
            require(type(value[key]) is float and math.isfinite(value[key]), "numerical")
    return True
