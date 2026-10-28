"""Bounded scalar diagnostics for one StateBridge alignment.

The receiver reads a continuous prefix that is produced inside the worker on
device. To decide whether that prefix carries sender information we need
measurements from real GLM-5.3 states, but raw tensors must never leave the
authorised GPU state lifecycle.

This module therefore returns a fixed, small dictionary of int and float
scalars. It allocates only n x n and rank x rank workspaces; the largest
temporary is the 2n x d stacked matrix that align_hidden_sequence already
builds. No vocabulary gather, no d x d covariance, no tensor leaves the call.

Everything here is pure: torch is passed in, nothing is imported at module
load, and the call never mutates its inputs.
"""
from __future__ import annotations

import math

from .latent_types import LatentError, require

# Fixed key set. The control plane and the receipts may rely on the names and
# on the types; adding a key is a schema change and must be versioned with the
# method identifier.
DIAGNOSTIC_KEYS = (
    "rows", "hidden_dim", "union_rank",
    "hidden_norm_mean", "embedding_norm_mean", "prefix_norm_mean",
    "hidden_eff_rank", "embedding_eff_rank",
    "whitened_hidden_sv_max", "whitened_hidden_sv_min",
    "whitened_embedding_sv_max", "whitened_embedding_sv_min",
    "procrustes_residual", "cos_prefix_to_embedding", "relative_prefix_error",
    "hidden_sensitivity", "shuffled_hidden_sensitivity",
    "nonfinite_count", "anchor_alpha",
)
_FLOATS = ("hidden_norm_mean", "embedding_norm_mean", "prefix_norm_mean",
           "hidden_eff_rank", "embedding_eff_rank",
           "whitened_hidden_sv_max", "whitened_hidden_sv_min",
           "whitened_embedding_sv_max", "whitened_embedding_sv_min",
           "procrustes_residual", "cos_prefix_to_embedding", "relative_prefix_error",
           "hidden_sensitivity", "shuffled_hidden_sensitivity", "anchor_alpha")


def effective_rank(matrix):
    """Participation ratio of the squared singular values, in [1, n]."""
    torch = _torch_of(matrix)
    centred = matrix - matrix.mean(0, keepdim=True)
    gram = centred @ centred.mT
    values = torch.linalg.eigvalsh((gram + gram.mT) * 0.5).clamp_min(0.0)
    total = values.sum()
    require(bool(torch.isfinite(total)) and float(total) > 0, "numerical")
    return float((total * total) / (values * values).sum().clamp_min(torch.finfo(values.dtype).tiny))


def _torch_of(reference):
    import torch
    return torch


def _solve(hidden, embeddings, target_norm, *, ridge, eigen_floor, rank_rtol, torch):
    """Mirror of statebridge.align_hidden_sequence that keeps its intermediates."""
    h, e = hidden.float(), embeddings.float()
    mh, me = h.mean(0, keepdim=True), e.mean(0, keepdim=True)
    hc, ec = h - mh, e - me
    _, singular, vh = torch.linalg.svd(torch.cat((hc, ec), dim=0), full_matrices=False)
    threshold = max(float(singular[0]) * rank_rtol, eigen_floor)
    rank = min(int((singular > threshold).sum()), 2 * (h.shape[0] - 1))
    require(rank > 0, "numerical")
    q = vh[:rank].mT
    bh, be = hc @ q, ec @ q
    eye = torch.eye(rank, dtype=h.dtype, device=h.device)
    ch = bh.mT @ bh / h.shape[0] + ridge * eye
    ce = be.mT @ be / h.shape[0] + ridge * eye
    half = _sym(ce, 0.5, eigen_floor, torch)
    whitened_h = bh @ _sym(ch, -0.5, eigen_floor, torch)
    whitened_e = be @ _sym(ce, -0.5, eigen_floor, torch)
    u, _, v = torch.linalg.svd(whitened_h.mT @ whitened_e, full_matrices=False)
    rotation = u @ v
    z = (whitened_h @ rotation @ half) @ q.mT + me
    norms = z.norm(dim=-1, keepdim=True)
    target = torch.as_tensor(target_norm, device=z.device, dtype=z.dtype)
    require(bool(torch.isfinite(target)) and float(target) > 0, "numerical")
    require(bool((norms > eigen_floor).all()), "numerical")
    prefix = z * (target / norms)
    residual = float(((whitened_h @ rotation - whitened_e).norm() /
                      whitened_e.norm().clamp_min(torch.finfo(z.dtype).tiny)))
    return {"prefix": prefix, "rank": rank, "residual": residual,
            "whitened_h": whitened_h, "whitened_e": whitened_e,
            "embedding": e, "prefix_h": prefix}


def _sym(matrix, power, floor, torch):
    values, vectors = torch.linalg.eigh((matrix + matrix.mT) * 0.5)
    return (vectors * values.clamp_min(floor).pow(power)) @ vectors.mT


def alignment_diagnostics(hidden, embeddings, prefix=None, *, target_norm,
                          ridge=1e-3, eigen_floor=1e-6, rank_rtol=1e-5,
                          anchor_alpha=0.0, sensitivity=True):
    """Return DIAGNOSTIC_KEYS scalars for one alignment. Never raises on content.

    sensitivity=True costs two extra thin solves (row-rolled hidden states and
    row-shuffled hidden states). Both are deterministic permutations, so the
    measurement is reproducible without a random seed.
    """
    torch = _torch_of(hidden)
    require(hidden.ndim == 2 and hidden.shape == embeddings.shape, "profile")
    require(hidden.device == embeddings.device, "profile")
    with torch.no_grad():
        require(bool(torch.isfinite(hidden).all()) and bool(torch.isfinite(embeddings).all()), "numerical")
        main = _solve(hidden, embeddings, target_norm, ridge=ridge, eigen_floor=eigen_floor,
                      rank_rtol=rank_rtol, torch=torch)
        z = main["prefix"] if prefix is None else prefix.float()
        e = main["embedding"]
        target = torch.as_tensor(target_norm, device=e.device, dtype=e.dtype)
        calibrated = e * (target / e.norm(dim=-1, keepdim=True).clamp_min(eigen_floor))
        cos = (z * calibrated).sum(-1) / (z.norm(dim=-1).clamp_min(eigen_floor) *
                                          calibrated.norm(dim=-1).clamp_min(eigen_floor))
        hidden_sensitivity = shuffled_sensitivity = 0.0
        if sensitivity:
            rolled = torch.roll(hidden, 1, 0)
            other = _solve(rolled, embeddings, target_norm, ridge=ridge, eigen_floor=eigen_floor,
                           rank_rtol=rank_rtol, torch=torch)["prefix"]
            hidden_sensitivity = float((other - z).norm() / z.norm().clamp_min(eigen_floor))
            permuted = hidden[torch.randperm(hidden.shape[0], device=hidden.device)]
            other = _solve(permuted, embeddings, target_norm, ridge=ridge, eigen_floor=eigen_floor,
                           rank_rtol=rank_rtol, torch=torch)["prefix"]
            shuffled_sensitivity = float((other - z).norm() / z.norm().clamp_min(eigen_floor))
        wh = main["whitened_h"]
        we = main["whitened_e"]
        result = {
            "rows": int(hidden.shape[0]), "hidden_dim": int(hidden.shape[1]),
            "union_rank": int(main["rank"]),
            "hidden_norm_mean": float(hidden.float().norm(dim=-1).mean()),
            "embedding_norm_mean": float(e.norm(dim=-1).mean()),
            "prefix_norm_mean": float(z.norm(dim=-1).mean()),
            "hidden_eff_rank": effective_rank(hidden),
            "embedding_eff_rank": effective_rank(embeddings),
            "whitened_hidden_sv_max": float(wh.max()),
            "whitened_hidden_sv_min": float(wh.abs().min()),
            "whitened_embedding_sv_max": float(we.max()),
            "whitened_embedding_sv_min": float(we.abs().min()),
            "procrustes_residual": float(main["residual"]),
            "cos_prefix_to_embedding": float(cos.mean()),
            "relative_prefix_error": float((z - calibrated).norm() / calibrated.norm().clamp_min(eigen_floor)),
            "hidden_sensitivity": hidden_sensitivity,
            "shuffled_hidden_sensitivity": shuffled_sensitivity,
            "nonfinite_count": int((~torch.isfinite(z)).sum()),
            "anchor_alpha": float(anchor_alpha),
        }
    validate(result)
    return result


def validate(result):
    """Fixed key set, exact int/float types, finite values, bounded magnitudes."""
    require(isinstance(result, dict) and set(result) == set(DIAGNOSTIC_KEYS), "profile")
    for key in DIAGNOSTIC_KEYS:
        value = result[key]
        require(type(value) in (int, float) and not isinstance(value, bool), "profile")
        if isinstance(value, float):
            require(value == value and abs(value) != float("inf"), "numerical")
    require(result["rows"] >= 2 and result["hidden_dim"] > 2 * result["rows"], "profile")
    require(0 < result["union_rank"] <= 2 * (result["rows"] - 1), "profile")
    require(result["nonfinite_count"] == 0, "numerical")
    return True


def workspace_elements(rows, hidden_dim):
    """Upper bound on any single temporary this module allocates, in elements."""
    require(2 <= rows and hidden_dim > 2 * rows, "profile")
    rank = 2 * (rows - 1)
    return max(2 * rows * hidden_dim, rows * rank, rank * rank, rows * rows)


CALIBRATED_DIAGNOSTIC_KEYS = (
    "rows", "hidden_dim", "hidden_norm_mean", "embedding_norm_mean", "prefix_norm_mean",
    "cos_prefix_to_embedding", "relative_prefix_error", "predicted_r2",
    "hidden_sensitivity", "shuffled_hidden_sensitivity", "copier_flag", "nonfinite_count",
    "anchor_alpha",
)
CALIBRATED_FLOATS = ("hidden_norm_mean", "embedding_norm_mean", "prefix_norm_mean",
                     "cos_prefix_to_embedding", "relative_prefix_error", "predicted_r2",
                     "hidden_sensitivity", "shuffled_hidden_sensitivity", "anchor_alpha")
# LIVE-WINDOW-1 Section 5: any candidate whose cosine to the embedding copier
# stays above this is not a candidate, whatever its other numbers say.
COPIER_COSINE_LIMIT = 0.9


def frozen_map_diagnostics(artifact, hidden, embeddings, *, target_norm=None,
                           anchor_alpha=0.0):
    """DIAGNOSTIC-style scalars for the frozen calibrated map, not the per-message fit.

    The per-message diagnostics re-solve the production operator on the same
    rows, so they cannot report a frozen map's state dependence. Here the prefix
    is apply_calibration() of the artifact, and the sensitivity is the change of
    that output when the hidden rows are rolled or reversed. The result carries
    the pre-registered copier flag so a map that reproduces the embeddings is
    visible in the receipt.
    """
    from .statebridge_calibrated import apply_calibration
    torch = _torch_of(hidden)
    require(hidden.ndim == 2 and hidden.shape == embeddings.shape, "profile")
    require(hidden.shape[0] >= 2 and hidden.device == embeddings.device, "profile")
    require(hidden.shape[1] == artifact.hidden_dim, "profile")
    require(hidden.device == artifact.mean_hidden.device, "profile")
    with torch.no_grad():
        h, e = hidden.float(), embeddings.float()
        require(bool(torch.isfinite(h).all()) and bool(torch.isfinite(e).all()), "numerical")
        norms = e.norm(dim=-1, keepdim=True).clamp_min(torch.finfo(e.dtype).tiny)
        scalar = float(norms.mean()) if target_norm is None else float(target_norm)
        require(math.isfinite(scalar) and scalar > 0.0, "numerical")
        calibrated = e * (scalar / norms)
        prefix = apply_calibration(artifact, h, scalar)
        cos = (prefix * calibrated).sum(-1) / (prefix.norm(dim=-1).clamp_min(1e-6) *
                                              calibrated.norm(dim=-1).clamp_min(1e-6))
        mean_cos = float(cos.mean())
        residual = float((prefix - calibrated).pow(2).sum())
        baseline = float((calibrated - calibrated.mean(0, keepdim=True)).pow(2).sum())
        require(math.isfinite(residual) and baseline > 0.0, "numerical")
        rolled = apply_calibration(artifact, torch.roll(h, 1, 0), scalar)
        reversed_rows = apply_calibration(artifact, torch.flip(h, [0]).contiguous(), scalar)
        floor = float(prefix.norm().clamp_min(1e-6))
        result = {
            "rows": int(h.shape[0]), "hidden_dim": int(h.shape[1]),
            "hidden_norm_mean": float(h.norm(dim=-1).mean()),
            "embedding_norm_mean": float(e.norm(dim=-1).mean()),
            "prefix_norm_mean": float(prefix.norm(dim=-1).mean()),
            "cos_prefix_to_embedding": mean_cos,
            "relative_prefix_error": float((prefix - calibrated).norm() / calibrated.norm().clamp_min(1e-6)),
            "predicted_r2": float(1.0 - residual / baseline),
            "hidden_sensitivity": float((rolled - prefix).norm() / floor),
            "shuffled_hidden_sensitivity": float((reversed_rows - prefix).norm() / floor),
            "copier_flag": int(mean_cos > COPIER_COSINE_LIMIT),
            "nonfinite_count": int((~torch.isfinite(prefix)).sum()),
            "anchor_alpha": float(anchor_alpha),
        }
    validate_calibrated(result)
    return result


def validate_calibrated(result):
    """Fixed key set, exact int/float types, finite values, bounded magnitudes."""
    require(isinstance(result, dict) and set(result) == set(CALIBRATED_DIAGNOSTIC_KEYS), "profile")
    for key in CALIBRATED_DIAGNOSTIC_KEYS:
        value = result[key]
        require(type(value) in (int, float) and not isinstance(value, bool), "profile")
        if isinstance(value, float):
            require(value == value and abs(value) != float("inf"), "numerical")
    require(result["rows"] >= 2 and result["hidden_dim"] > 2, "profile")
    require(result["copier_flag"] in (0, 1), "profile")
    require(result["nonfinite_count"] == 0, "numerical")
    require(-1.0 <= result["cos_prefix_to_embedding"] <= 1.0, "numerical")
    return True
