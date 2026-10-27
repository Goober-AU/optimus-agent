"""Candidate alignment transforms for the OCC3 calibration generalisation study.

Pure functions over torch tensors. METHOD A delegates to the production solver in
``occ3_sglang.statebridge.align_hidden_sequence``; METHODS B, C, D are fitted on
calibration rows and applied frozen to unseen rows.

Conventions
-----------
Rows are observation-major: ``h`` is (n, d) hidden states, ``e`` is (n, d)
reference-token embeddings, ``d`` is the model width and ``n`` the row count.
Every fitted method returns a ``dict`` of frozen parameters plus an ``apply``
callable so that fit and evaluation are separable (that separation is the whole
point of the study).

The production operator (statebridge.py:38-80) is: centre both row sets, take the
top-``rank`` right singular vectors ``q`` of the stacked centred pair, whiten each
side inside ``q``, rotate whitened hidden onto whitened embedding by orthogonal
Procrustes, unwhiten with the embedding square root, map back through ``q``, add
the embedding mean, then rescale every row to a scalar target norm. ``fit`` and
``apply`` below reproduce that operator exactly; ``apply`` accepts unseen rows,
which the production entry point never does.
"""
from __future__ import annotations

import math

import torch

torch.set_grad_enabled(False)

__all__ = [
    "sym_power", "unit_rows", "mean_pair_cosine", "spectrum_report",
    "fit_production", "apply_production", "renorm_rows",
    "fit_whitened_procrustes", "apply_whitened_procrustes",
    "fit_orthogonal_procrustes", "apply_orthogonal_procrustes",
    "fit_dual_ridge", "apply_dual_ridge",
    "fit_null_zero", "apply_null_zero",
    "fit_affine_identity", "apply_affine_identity",
    "fit_direct_reference", "apply_direct_reference",
]


# --------------------------------------------------------------------------- #
# numeric helpers
# --------------------------------------------------------------------------- #
def sym_power(matrix, power, floor):
    """Symmetric matrix power with an eigenvalue floor (statebridge.py:24-27)."""
    vals, vecs = torch.linalg.eigh((matrix + matrix.mT) * 0.5)
    return (vecs * vals.clamp_min(floor).pow(power)) @ vecs.mT


def unit_rows(x):
    return x / x.norm(dim=-1, keepdim=True).clamp_min(1e-30)


def mean_pair_cosine(x):
    """Mean cosine over all ordered pairs i != j."""
    xu = unit_rows(x)
    gram = xu @ xu.mT
    n = x.shape[0]
    if n < 2:
        return float("nan")
    return float((gram.sum() - gram.diagonal().sum()) / (n * (n - 1)))


def spectrum_report(x, top=8):
    """Anisotropy attestation: singular-value shares and participation ratio."""
    xc = x - x.mean(0, keepdim=True)
    s = torch.linalg.svdvals(xc.to(torch.float64))
    total = float((s * s).sum())
    if total <= 0:
        return {"top": [], "participation": 0.0, "effective_rank_99": 0}
    shares = (s * s / total)[:top].tolist()
    p = (s * s / total)
    participation = float(1.0 / (p * p).sum())
    cumulative = torch.cumsum(p, 0)
    eff99 = int(torch.searchsorted(cumulative, torch.tensor(0.99, dtype=cumulative.dtype)).item()) + 1
    return {"top": [round(v, 6) for v in shares], "participation": round(participation, 3),
            "effective_rank_99": eff99, "min_sv_over_max": float(s[-1] / s[0])}


def renorm_rows(z, target_norm):
    """Production tail: every row is rescaled to one scalar norm (statebridge.py:76-79)."""
    return z * (float(target_norm) / z.norm(dim=-1, keepdim=True).clamp_min(1e-30))


def _union_basis(h, e, *, rank_cap, rank_rtol, eigen_floor, seed, niter):
    """Top-r right singular vectors of the stacked centred pair.

    Mirrors statebridge.py:54-62. With ``rank_cap=None`` the singular-value
    threshold and the ``2*(n-1)`` cap are the production rule; with a cap the
    basis is computed by randomised SVD and truncated at ``min(rank_cap, 2*(n-1))``.
    Returns (q, rank, mode, captured_energy_fraction).
    """
    n = h.shape[0]
    hc = h - h.mean(0, keepdim=True)
    ec = e - e.mean(0, keepdim=True)
    union = torch.cat((hc, ec), 0)
    cap = 2 * (n - 1)
    if rank_cap is None:
        _, singular, vh = torch.linalg.svd(union, full_matrices=False)
        threshold = max(float(singular[0]) * rank_rtol, eigen_floor)
        rank = min(int((singular > threshold).sum()), cap)
        q = vh[:rank].mT
        mode = "exact_svd"
        svals = singular[:rank]
    else:
        rank = min(int(rank_cap), cap, union.shape[0], union.shape[1])
        u, s, v = torch.svd_lowrank(union, q=rank, niter=niter)
        s, order = torch.sort(s, descending=True)
        q = v[:, order][:, :rank]
        mode = "randomised_svd"
        svals = s[:rank]
    svals = svals.to(torch.float64)
    total = float((union.to(torch.float64) ** 2).sum())
    energy = float((svals * svals).sum()) / total if total > 0 else 0.0
    return q.contiguous(), rank, mode, energy


# --------------------------------------------------------------------------- #
# METHOD A: production per-message whitened Procrustes
# --------------------------------------------------------------------------- #
def fit_production(h, e, *, ridge=1e-3, eigen_floor=1e-6, rank_rtol=1e-5):
    """Fit the production operator to (h, e); the same rows are normally re-used.

    The returned operator is numerically the map inside
    ``occ3_sglang.statebridge.align_hidden_sequence`` (statebridge.py:38-80).
    Parity with the production call is asserted by
    ``tests/test_alignment_calibration.py``.
    """
    n = h.shape[0]
    q, rank, mode, energy = _union_basis(h, e, rank_cap=None, rank_rtol=rank_rtol,
                                         eigen_floor=eigen_floor, seed=0, niter=0)
    mu_h = h.mean(0, keepdim=True)
    mu_e = e.mean(0, keepdim=True)
    bh = (h - mu_h) @ q
    be = (e - mu_e) @ q
    eye = torch.eye(rank, dtype=h.dtype)
    ch = bh.mT @ bh / n + ridge * eye
    ce = be.mT @ be / n + ridge * eye
    ch_inv_half = sym_power(ch, -0.5, eigen_floor)
    ce_half = sym_power(ce, 0.5, eigen_floor)
    x = bh @ ch_inv_half
    y = be @ sym_power(ce, -0.5, eigen_floor)
    u, _, v = torch.linalg.svd(x.mT @ y, full_matrices=False)
    rot = u @ v
    w = (ch_inv_half @ rot @ ce_half).to(h.dtype)
    return {"method": "A_production", "mu_h": mu_h, "mu_e": mu_e, "q": q, "w": w,
            "rank": rank, "basis_mode": mode, "energy": energy, "n_fit": n,
            "cost": {"svd_shape": [2 * n, h.shape[1]], "rank": rank,
                     "small_solve": [rank, rank], "chol": None}}


def apply_production(params, h):
    z = ((h - params["mu_h"]) @ params["q"]) @ params["w"] @ params["q"].mT
    return z + params["mu_e"]


def apply_production_reference(h, e, target_norm):
    """Reference path: the production solver itself, for parity testing."""
    from occ3_sglang.statebridge import align_hidden_sequence
    z, info = align_hidden_sequence(h, e, target_norm)
    return z, info


# --------------------------------------------------------------------------- #
# METHOD B: frozen whitened Procrustes
# --------------------------------------------------------------------------- #
def fit_whitened_procrustes(h, e, *, rank_cap=256, ridge=1e-3, eigen_floor=1e-6,
                            rank_rtol=1e-5, seed=0, niter=4):
    """Whitened Procrustes fitted on calibration rows, frozen for later rows.

    Identical algebra to the production operator (statebridge.py:38-80); the only
    additions are (a) an explicit basis truncation so that M > 128 calibration
    rows are tractable and (b) the parameter/apply split.
    """
    n = h.shape[0]
    q, rank, mode, energy = _union_basis(h, e, rank_cap=rank_cap, rank_rtol=rank_rtol,
                                        eigen_floor=eigen_floor, seed=seed, niter=niter)
    mu_h = h.mean(0, keepdim=True)
    mu_e = e.mean(0, keepdim=True)
    bh = ((h - mu_h) @ q).to(torch.float64)
    be = ((e - mu_e) @ q).to(torch.float64)
    eye = torch.eye(rank, dtype=torch.float64)
    ch = bh.mT @ bh / n + ridge * eye
    ce = be.mT @ be / n + ridge * eye
    ch_inv_half = sym_power(ch, -0.5, eigen_floor)
    ce_half = sym_power(ce, 0.5, eigen_floor)
    ce_inv_half = sym_power(ce, -0.5, eigen_floor)
    x = bh @ ch_inv_half
    y = be @ ce_inv_half
    u, _, v = torch.linalg.svd(x.mT @ y, full_matrices=False)
    rot = u @ v
    w = (ch_inv_half @ rot @ ce_half).to(torch.float32)
    return {"method": "B_frozen_whitened", "mu_h": mu_h, "mu_e": mu_e,
            "q": q, "w": w, "rank": rank, "basis_mode": mode, "energy": energy, "n_fit": n,
            "cost": {"svd_shape": [2 * n, h.shape[1]], "rank": rank,
                     "small_solve": [rank, rank], "chol": None}}


def apply_whitened_procrustes(params, h):
    z = ((h - params["mu_h"]) @ params["q"]) @ params["w"] @ params["q"].mT
    return z + params["mu_e"]


# --------------------------------------------------------------------------- #
# METHOD C: affine orthogonal Procrustes
# --------------------------------------------------------------------------- #
def fit_orthogonal_procrustes(h, e, *, rank_cap=256, rank_rtol=1e-5, eigen_floor=1e-6,
                              seed=0, niter=4):
    """aligned(z) = mu_E + s * (z - mu_H) R, R orthogonal, s from calibration residuals.

    ``R`` is orthogonal in the ambient space d: R = I + Q (R_r - I) Q^T with
    Q (d x r) orthonormal and R_r (r x r) orthogonal, so R^T R = I exactly. The
    complement is identity because H^T E has rank <= M-1 << d for any realistic
    calibration corpus, so the rotation outside span(Q) is not identified by the
    data and identity is the minimum-distortion completion. The scale s is the
    least-squares optimum for the given R.
    """
    n = h.shape[0]
    q, rank, mode, energy = _union_basis(h, e, rank_cap=rank_cap, rank_rtol=rank_rtol,
                                        eigen_floor=eigen_floor, seed=seed, niter=niter)
    mu_h = h.mean(0, keepdim=True)
    mu_e = e.mean(0, keepdim=True)
    ah = ((h - mu_h) @ q).to(torch.float64)
    be = ((e - mu_e) @ q).to(torch.float64)
    u, _, v = torch.linalg.svd(ah.mT @ be, full_matrices=False)
    rot_r = u @ v                       # ah @ rot_r ~ be  (right multiplier)
    # residual-optimal scale on the calibration rows, in the full ambient space
    a_full = (h - mu_h)
    rot_minus_i = (rot_r - torch.eye(rank, dtype=torch.float64)).to(torch.float32)
    mapped = a_full + (((a_full @ q) @ rot_minus_i) @ q.mT)
    num = float((mapped.to(torch.float64) * (e - mu_e).to(torch.float64)).sum())
    den = float((mapped.to(torch.float64) ** 2).sum())
    scale = num / den if den > 0 else 0.0
    residual = mapped * scale - (e - mu_e)
    return {"method": "C_frozen_orthogonal", "mu_h": mu_h, "mu_e": mu_e, "q": q,
            "rot_r": rot_r, "rot_minus_i": rot_minus_i,
            "scale": scale, "rank": rank, "basis_mode": mode, "energy": energy, "n_fit": n,
            "calib_rel_residual": float(residual.norm() / (e - mu_e).norm()),
            "cost": {"svd_shape": [2 * n, h.shape[1]], "rank": rank,
                     "small_solve": [rank, rank], "chol": None}}


def apply_orthogonal_procrustes(params, h):
    """Ambient map I + Q (R_r - I) Q^T, i.e. orthogonal with an identity complement."""
    a = h - params["mu_h"]
    inside = (a @ params["q"]) @ params["rot_minus_i"] @ params["q"].mT
    return params["mu_e"] + float(params["scale"]) * (a + inside)


# --------------------------------------------------------------------------- #
# METHOD D: dual ridge
# --------------------------------------------------------------------------- #
def fit_dual_ridge(h, e, *, lam_rel=1e-3, dtype=torch.float64):
    """Z = mu_E + (H_new - mu_H) A^T (A A^T + lam I)^{-1} B.

    A = H_cal - mu_H_cal, B = E_cal - mu_E_cal. The M x M Gram matrix is factored
    by Cholesky and never inverted explicitly. The frozen artifact is
    (A, chol_factor, B) = 2*M*d + M*M/max, or the dense d x d map
    A^T (A A^T + lam I)^{-1} B if precomputed.
    """
    n, d = h.shape
    mu_h = h.mean(0, keepdim=True)
    mu_e = e.mean(0, keepdim=True)
    a = (h - mu_h).to(dtype)
    b = (e - mu_e).to(dtype)
    gram = a @ a.mT
    lam = float(lam_rel) * float(gram.diagonal().mean())
    gram = gram + lam * torch.eye(n, dtype=dtype)
    chol = torch.linalg.cholesky(gram)
    coeff = torch.cholesky_solve(b, chol)          # M x d
    return {"method": "D_dual_ridge", "mu_h": mu_h, "mu_e": mu_e,
            "a_t": a.mT.to(torch.float32).contiguous(),
            "coeff": coeff.to(torch.float32), "lam": lam, "lam_rel": float(lam_rel),
            "rank": n, "basis_mode": "none", "n_fit": n,
            "gram_shape": [n, n],
            "cost": {"svd_shape": None, "rank": n, "small_solve": [n, n],
                     "chol": [n, n],
                     "flops": {"gram": 2 * n * n * d, "chol": n ** 3 / 3.0,
                               "solve": 2 * n * n * d}}}


def apply_dual_ridge(params, h):
    delta = (h - params["mu_h"]).to(torch.float32) @ params["a_t"]
    return params["mu_e"] + delta.to(torch.float32) @ params["coeff"]


# --------------------------------------------------------------------------- #
# CONTROLS
# --------------------------------------------------------------------------- #
def fit_null_zero():
    return {"method": "K_zero", "n_fit": 0, "rank": 0, "basis_mode": "none",
            "cost": {"svd_shape": None, "rank": 0, "small_solve": None, "chol": None}}


def apply_null_zero(params, h):
    return torch.zeros_like(h)


def fit_direct_reference(e):
    return {"method": "F_direct_reference", "e": e, "n_fit": 0, "rank": 0,
            "basis_mode": "none",
            "cost": {"svd_shape": None, "rank": 0, "small_solve": None, "chol": None}}


def apply_direct_reference(params, h):
    return params["e"].clone()


# --------------------------------------------------------------------------- #
# CONTROL I: affine identity (mean shift + one scalar, no fitted rotation)
# --------------------------------------------------------------------------- #
def fit_affine_identity(h, e):
    """mu_E + s*(h - mu_H) with s the least-squares scalar on the calibration rows.

    No rotation and no basis: this is what a candidate degenerates to when the retained
    basis is too small to contain the message rows, and it is the null model for "did
    fitting a rotation buy anything".
    """
    n, d = h.shape
    mu_h = h.mean(0, keepdim=True)
    mu_e = e.mean(0, keepdim=True)
    a = (h - mu_h).to(torch.float64)
    b = (e - mu_e).to(torch.float64)
    den = float((a * a).sum())
    scale = float((a * b).sum()) / den if den > 0 else 0.0
    residual = a * scale - b
    return {"method": "I_affine_identity", "mu_h": mu_h, "mu_e": mu_e, "scale": scale,
            "rank": 0, "basis_mode": "none", "n_fit": n,
            "calib_rel_residual": float(residual.norm() / b.norm()),
            "cost": {"svd_shape": None, "rank": 0, "small_solve": None, "chol": None}}


def apply_affine_identity(params, h):
    return params["mu_e"] + float(params["scale"]) * (h - params["mu_h"])
