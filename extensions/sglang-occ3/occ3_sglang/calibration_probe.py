"""Scalar-only probe of the hidden-to-embedding relation, for a live window.

The shipped alignment is fitted on the transmitted rows, so it cannot answer the
question the calibrated method depends on: how much of the embedding of a
token is predictable from the pre-norm state that predicts it, and at what rank.
That quantity is the canonical-correlation spectrum between the hidden rows and
the embedding rows of the same tokens.

Everything here returns integers and floats, or a short bounded list of floats
with no positional information. Nothing is written that could reconstruct a
hidden state, and no token id is recorded.
"""
from __future__ import annotations

from .calibration_corpus import (DEFAULT_EVAL_STRIDE, EVALUATION_KEYS,
                                 evaluate_turn_disjoint, split_turns)
from .latent_types import require

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
_LIST_GROUPS = 10


def _sym(matrix, power, floor, torch):
    values, vectors = torch.linalg.eigh((matrix + matrix.mT) * 0.5)
    return (vectors * values.clamp_min(floor).pow(power)) @ vectors.mT


def canonical_spectrum(hidden, embeddings, *, ridge=1e-3, eigen_floor=1e-6,
                        rank_rtol=1e-5, torch_module=None):
    """Canonical correlations between the hidden rows and the embedding rows.

    The shipped solver already computes them: the singular values of
    Xw^T Yw / n are the canonical correlations of the two whitened row sets.
    """
    torch = torch_module or _import_torch()
    rows = hidden.shape[0]
    with torch.no_grad():
        h, e = hidden.float(), embeddings.float()
        hc = h - h.mean(0, keepdim=True)
        ec = e - e.mean(0, keepdim=True)
        _, singular, vh = torch.linalg.svd(torch.cat((hc, ec), dim=0), full_matrices=False)
        threshold = max(float(singular[0]) * rank_rtol, eigen_floor)
        rank = min(int((singular > threshold).sum()), 2 * (rows - 1))
        require(rank > 0, "numerical")
        q = vh[:rank].mT
        bh, be = hc @ q, ec @ q
        eye = torch.eye(rank, dtype=h.dtype, device=h.device)
        ch = bh.mT @ bh / rows + ridge * eye
        ce = be.mT @ be / rows + ridge * eye
        x = bh @ _sym(ch, -0.5, eigen_floor, torch)
        y = be @ _sym(ce, -0.5, eigen_floor, torch)
        values = torch.linalg.svdvals(x.mT @ y) / rows
        values = values.clamp(0.0, 1.0)
        rotation_u, _, rotation_v = torch.linalg.svd(x.mT @ y, full_matrices=False)
        rotation = rotation_u @ rotation_v
        half = _sym(ce, 0.5, eigen_floor, torch)
        z = (x @ rotation @ half) @ q.mT + e.mean(0, keepdim=True)
        residual = float(((x @ rotation - y).norm() / y.norm().clamp_min(1e-12)))
    return {"rank": int(rank), "canonical": values, "prefix": z, "residual": residual,
            "whitened_hidden": x, "whitened_embedding": y, "embedding": e}


def _import_torch():
    import torch
    return torch


def _participation(values):
    total, squares = values.sum(), (values * values).sum()
    return float((total * total) / squares.clamp_min(1e-12))


def _eff_rank(matrix, torch):
    centred = matrix.float() - matrix.float().mean(0, keepdim=True)
    gram = centred @ centred.mT
    values = torch.linalg.eigvalsh((gram + gram.mT) * 0.5).clamp_min(0.0)
    total = values.sum()
    require(bool(torch.isfinite(total)) and float(total) > 0, "numerical")
    return float((total * total) / (values * values).sum().clamp_min(1e-30))


def probe_relation(hidden, embeddings, prefix=None, *, ridge=1e-3, eigen_floor=1e-6,
                   rank_rtol=1e-5, torch_module=None):
    """Fixed-key scalar summary of one captured turn."""
    torch = torch_module or _import_torch()
    require(hidden.ndim == 2 and hidden.shape == embeddings.shape, "profile")
    require(2 <= hidden.shape[0] <= 128 and hidden.shape[1] > 2 * hidden.shape[0], "profile")
    with torch.no_grad():
        require(bool(torch.isfinite(hidden).all()) and bool(torch.isfinite(embeddings).all()), "numerical")
        main = canonical_spectrum(hidden, embeddings, ridge=ridge, eigen_floor=eigen_floor,
                                   rank_rtol=rank_rtol, torch_module=torch)
        z = main["prefix"] if prefix is None else prefix.float()
        e = main["embedding"]
        target = float(e.norm(dim=-1).mean())
        calibrated = e * (target / e.norm(dim=-1, keepdim=True).clamp_min(eigen_floor))
        cos = float((z * calibrated).sum(-1).div(
            z.norm(dim=-1).clamp_min(eigen_floor) * calibrated.norm(dim=-1).clamp_min(eigen_floor)).mean())
        relative = float((z - calibrated).norm() / calibrated.norm().clamp_min(eigen_floor))
        rolled = canonical_spectrum(torch.roll(hidden, 1, 0), embeddings, ridge=ridge,
                                    eigen_floor=eigen_floor, rank_rtol=rank_rtol,
                                    torch_module=torch)["prefix"]
        permuted = canonical_spectrum(hidden[torch.randperm(hidden.shape[0], device=hidden.device)],
                                      embeddings, ridge=ridge, eigen_floor=eigen_floor,
                                      rank_rtol=rank_rtol, torch_module=torch)["prefix"]
        values = main["canonical"]
        # The canonical spectrum alone CANNOT separate a real relation from none:
        # whitening makes two full-rank clouds congruent, so it saturates near 1
        # for independent data too. What separates them is out-of-sample
        # prediction inside the turn: fit on the first half of the rows and
        # predict the embeddings of the second half.
        from .statebridge_calibrated import heldout_r2
        heldout = heldout_r2(hidden, embeddings, 0.02 * float(
            (hidden.float() - hidden.float().mean(0, keepdim=True)).pow(2).sum(1).mean()), torch,
            split=0.5)
        size = values.numel()
        grouped = [float(values[start:start + max(1, size // _LIST_GROUPS)].mean())
                   for start in range(0, size, max(1, size // _LIST_GROUPS))][:_LIST_GROUPS]
        result = {
            "rows": int(hidden.shape[0]), "hidden_dim": int(hidden.shape[1]),
            "union_rank": int(main["rank"]),
            "hidden_eff_rank": _eff_rank(hidden, torch),
            "embedding_eff_rank": _eff_rank(embeddings, torch),
            "canonical_top1": float(values[:1].mean()),
            "canonical_top8": float(values[:8].mean()),
            "canonical_top32": float(values[:32].mean()),
            "canonical_mean": float(values.mean()),
            "canonical_participation": _participation(values),
            "canonical_above_090": int((values > 0.90).sum()),
            "canonical_above_095": int((values > 0.95).sum()),
            "canonical_above_099": int((values > 0.99).sum()),
            "canonical_decile": grouped,
            "heldout_r2_within_turn": float(heldout),
            "procrustes_residual": float(main["residual"]),
            "cos_prefix_to_embedding": cos,
            "relative_prefix_to_embedding": relative,
            "hidden_roll_sensitivity": float((rolled - z).norm() / z.norm().clamp_min(eigen_floor)),
            "hidden_permute_sensitivity": float((permuted - z).norm() / z.norm().clamp_min(eigen_floor)),
            "prefix_norm_mean": float(z.norm(dim=-1).mean()),
            "hidden_norm_mean": float(hidden.float().norm(dim=-1).mean()),
            "embedding_norm_mean": float(e.norm(dim=-1).mean()),
            "nonfinite_count": int((~torch.isfinite(z)).sum()),
        }
    validate_probe(result)
    return result


def validate_probe(result):
    require(isinstance(result, dict) and set(result) == set(PROBE_KEYS), "profile")
    for key in PROBE_KEYS:
        value = result[key]
        if key == "canonical_decile":
            require(isinstance(value, list) and 1 <= len(value) <= _LIST_GROUPS, "profile")
            require(all(type(x) is float and x == x for x in value), "numerical")
            continue
        require(type(value) in (int, float) and not isinstance(value, bool), "profile")
        if isinstance(value, float):
            require(value == value and abs(value) != float("inf"), "numerical")
    require(result["nonfinite_count"] == 0, "numerical")
    require(0 <= result["canonical_top1"] <= 1.0, "numerical")
    return True


DEFAULT_POOLED_ROW_BUDGET = 512
# One schema: the pooled probe reports exactly the pooled evaluation keys.
POOLED_PROBE_KEYS = EVALUATION_KEYS


def probe_pooled_turn_disjoint(blocks, *, row_budget=DEFAULT_POOLED_ROW_BUDGET,
                              eval_stride=DEFAULT_EVAL_STRIDE, provenance=None):
    """Turn-disjoint pooled probe: the number LIVE-WINDOW-1 Section 5 asks for.

    PROBE_KEYS is the per-turn schema of one capture and stays unchanged; a
    pooled corpus needs whole-turn held-out turns, so it is reported under its
    own fixed key set. Per-turn capture limits are the same as probe_relation:
    no turn is pooled outside the size the capture path can produce.
    """
    values = tuple(blocks)
    require(len(values) >= 2, "profile")
    for block in values:
        require(2 <= block.rows <= 128 and block.hidden.shape[1] > 2 * block.rows, "profile")
        require(block.embeddings.shape == block.hidden.shape, "profile")
    split_turns(values, eval_stride=eval_stride)
    return evaluate_turn_disjoint(values, row_budget=row_budget, eval_stride=eval_stride,
                                  provenance=provenance)
