#!/usr/bin/env python
"""align_audit.py -- CPU numerical audit of the thin StateBridge alignment.

Target (read-only): extensions/sglang-occ3/occ3_sglang/statebridge.py
  align_hidden_sequence  (lines 32-70), anchor_tiled (lines 115-136)

What is measured, on synthetic GLM-shaped states (n=128 rows, d=6144, float32):
  1  parity of an exact numpy port against the torch implementation
  2  union rank and conditioning under realistic spectra
  3  the reference-token confound set (E fixed, H varied)
  4  E row-shuffle and the E-only diagnostic carrier
  5  vocabulary anchoring, alpha 0 vs 0.3
  6  out-of-sample fit (rows 0..63) applied to rows 64..127
  7  sensitivity dZ/dH against dZ/dE

Writes align_audit.json next to this file and prints every table it writes.
No source file is modified.  No real GLM hidden state is measured here; every
number is a synthetic-fixture number produced on CPU.

Run with the extension interpreter:
  extensions/sglang-occ3/.venv/Scripts/python.exe align_audit.py
"""
from __future__ import annotations

import json
import os
import sys
import time

import numpy as np

# --------------------------------------------------------------------------
# fixed audit constants (mirror the target defaults, statebridge.py:32-33)
# --------------------------------------------------------------------------
N_ROWS = 128
DIM = 6144
RIDGE = 1e-3
EIGEN_FLOOR = 1e-6
RANK_RTOL = 1e-5
VOCAB = 4096
PAIR_COS = 0.24        # measured raw hidden->embedding cosine, reports/occ3/SGLANG-SOURCE-AUDIT.md:53
SEED = 20261027
TARGET_NORM = 1.0      # placeholder scale; real value is mean_embedding_norm(word embeddings)

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, "..", "..", ".."))
OUT_JSON = os.path.join(HERE, "align_audit.json")
EXT = os.path.join(REPO, "extensions", "sglang-occ3")


# --------------------------------------------------------------------------
# numpy port of the thin alignment, statement by statement
# --------------------------------------------------------------------------
def sym_power(matrix, power, floor=EIGEN_FLOOR):
    """statebridge.py:28-30 -- eigh, clamp_min(floor), pow, reassemble."""
    a = (matrix + matrix.T) * np.float32(0.5)
    vals, vectors = np.linalg.eigh(a.astype(np.float32))
    vals = np.clip(vals, floor, None).astype(np.float32) ** np.float32(power)
    return (vectors * vals[None, :]) @ vectors.T


def fit(hidden, embeddings, ridge=RIDGE, eigen_floor=EIGEN_FLOOR, rank_rtol=RANK_RTOL,
        rank_cap=None):
    """Fit the map on a set of paired rows.  Mirrors statebridge.py:48-63."""
    h = np.ascontiguousarray(hidden, dtype=np.float32)
    e = np.ascontiguousarray(embeddings, dtype=np.float32)
    n, d = h.shape
    mh, me = h.mean(0, keepdims=True), e.mean(0, keepdims=True)
    hc, ec = h - mh, e - me
    _, singular, vh = np.linalg.svd(np.concatenate((hc, ec), axis=0), full_matrices=False)
    threshold = max(float(singular[0]) * rank_rtol, eigen_floor)
    rank = min(int((singular > threshold).sum()), 2 * (n - 1))
    if rank_cap is not None:                     # hypothesis only: caps the map's freedom
        rank = max(1, min(rank, int(rank_cap)))
    q = np.ascontiguousarray(vh[:rank].T)
    bh, be = hc @ q, ec @ q
    eye = np.eye(rank, dtype=np.float32)
    ch = bh.T @ bh / np.float32(n) + np.float32(ridge) * eye
    ce = be.T @ be / np.float32(n) + np.float32(ridge) * eye
    ch_inv = sym_power(ch, -0.5, eigen_floor)
    ce_inv = sym_power(ce, -0.5, eigen_floor)
    ce_sqrt = sym_power(ce, 0.5, eigen_floor)
    x, y = bh @ ch_inv, be @ ce_inv
    u, _, vt = np.linalg.svd(x.T @ y, full_matrices=False)
    rot = u @ vt
    obj = float(np.linalg.norm(x @ rot - y) / max(np.linalg.norm(y), 1e-30))
    # von Neumann lower bound on the orthogonal-Procrustes residual.  It is attained
    # exactly when both clouds are tight frames (every live singular value sqrt(n)),
    # because then x x^T = n I = y y^T and the clouds are congruent.
    sx = np.linalg.svd(x, compute_uv=False)
    sy = np.linalg.svd(y, compute_uv=False)
    obj_lb = float(np.sqrt(((sx - sy) ** 2).sum()) / max(np.linalg.norm(y), 1e-30))
    state = dict(q=q, mh=mh, me=me, ch=ch, ce=ce, ch_inv=ch_inv, ce_inv=ce_inv,
                 ce_sqrt=ce_sqrt, rot=rot, rank=rank, n=n, d=d, threshold=threshold,
                 singular=singular, obj_whitened=obj, obj_lower_bound=obj_lb,
                 x=x, y=y, sig_x=sx, sig_y=sy,
                 flat_frac_x=flat_fraction(sx, n), flat_frac_y=flat_fraction(sy, n),
                 n_nonzero_x=int((sx > 1e-6 * max(sx.max(), 1e-30)).sum()),
                 n_nonzero_y=int((sy > 1e-6 * max(sy.max(), 1e-30)).sum()),
                 sigma_live_min_x=float(sx[sx > 1e-6 * max(sx.max(), 1e-30)].min())
                 if float(sx.max()) > 0 else 0.0,
                 sigma_live_max_x=float(sx.max()),
                 gram_identity_error_x=float(np.abs(x.astype(np.float64) @ x.T.astype(np.float64)
                                                    - n * np.eye(n)).max() / n)
                 if float(sx.max()) > 0 else 0.0,
                 gram_identity_error_y=float(np.abs(y.astype(np.float64) @ y.T.astype(np.float64)
                                                    - n * np.eye(n)).max() / n))
    return state


def flat_fraction(sig, n):
    """Share of retained whitening directions whose singular value is sqrt(n).

    A whitened cloud with variance far above the ridge has every nonzero singular
    value equal to sqrt(n).  Two such clouds have identical spectra, so by von
    Neumann an orthogonal map carries one exactly onto the other.
    """
    live = sig[sig > 1e-6 * max(float(sig.max()), 1e-30)]
    if live.size == 0:
        return 0.0
    return float((live > 0.90 * np.sqrt(n)).mean())


def apply_map(state, hidden, mean_override=None):
    """Apply the fitted map to any row count.  Mirrors statebridge.py:63."""
    h = np.ascontiguousarray(hidden, dtype=np.float32)
    me = state["me"] if mean_override is None else np.asarray(mean_override, np.float32)
    bh = (h - state["mh"]) @ state["q"]
    x = bh @ state["ch_inv"]
    return (x @ state["rot"] @ state["ce_sqrt"]) @ state["q"].T + me


def align_np(hidden, embeddings, target_norm=TARGET_NORM, ridge=RIDGE,
             eigen_floor=EIGEN_FLOOR, rank_rtol=RANK_RTOL, rank_cap=None):
    """statebridge.py:32-70 in numpy, including the norm calibration at 64-68."""
    state = fit(hidden, embeddings, ridge, eigen_floor, rank_rtol, rank_cap)
    z = apply_map(state, hidden)
    norms = np.linalg.norm(z, axis=1, keepdims=True)
    z = z * (np.float32(target_norm) / norms)
    return np.ascontiguousarray(z), state


# --------------------------------------------------------------------------
# metrics
# --------------------------------------------------------------------------
def _f(a):
    return np.asarray(a, dtype=np.float64)


def rel_fro(a, b):
    return float(np.linalg.norm(_f(a) - _f(b)) / max(np.linalg.norm(_f(b)), 1e-30))


def row_cos(a, b):
    a, b = _f(a), _f(b)
    na = np.linalg.norm(a, axis=1)
    nb = np.linalg.norm(b, axis=1)
    return (a * b).sum(1) / np.maximum(na * nb, 1e-30)


def procrustes_sim(a, b):
    """max over d x d orthogonal Q of <a, bQ> / (|a||b|); 1 == same point cloud."""
    a, b = _f(a), _f(b)
    a = a - a.mean(0)
    b = b - b.mean(0)
    s = np.linalg.svd(a @ b.T, compute_uv=False)
    return float(s.sum() / max(np.linalg.norm(a) * np.linalg.norm(b), 1e-30))


def gram_cos(a, b):
    """cosine of the centred row Gram matrices; no rotation allowed."""
    a, b = _f(a), _f(b)
    a = a - a.mean(0)
    b = b - b.mean(0)
    ga, gb = a @ a.T, b @ b.T
    return float((ga * gb).sum() / max(np.linalg.norm(ga) * np.linalg.norm(gb), 1e-30))


def spectrum(a):
    """Normalised eigenvalue spectrum of the centred covariance (descending)."""
    a = _f(a)
    a = a - a.mean(0)
    lam = np.linalg.eigvalsh(a @ a.T / a.shape[0])[::-1]
    lam = np.clip(lam, 0.0, None)
    total = lam.sum()
    return lam / total if total > 0 else lam


def eff_rank(a):
    lam = spectrum(a)
    lam = lam[lam > 0]
    if lam.size == 0 or lam.sum() <= 0:
        return dict(eff_rank=0.0, stable_rank=0.0, n_components_90pct=0)
    p = lam / lam.sum()
    ent = float(np.exp(-(p * np.log(p)).sum()))
    stable = float(1.0 / np.square(lam).sum())
    cum = np.cumsum(lam)
    n90 = int(np.searchsorted(cum, 0.9) + 1)
    return dict(eff_rank=ent, stable_rank=stable, n_components_90pct=n90)


def pair_retrieval(z, e):
    """Top-1 retrieval of the paired reference row among n candidates."""
    zn = _f(z) / np.maximum(np.linalg.norm(_f(z), axis=1, keepdims=True), 1e-30)
    en = _f(e) / np.maximum(np.linalg.norm(_f(e), axis=1, keepdims=True), 1e-30)
    sims = zn @ en.T
    order = np.argsort(-sims, axis=1)
    accuracy = float((order[:, 0] == np.arange(len(z))).mean())
    ranks = np.array([int(np.where(order[i] == i)[0][0]) + 1 for i in range(len(z))])
    return dict(retrieval_top1=accuracy, partner_rank_median=float(np.median(ranks)))


def variant_metrics(z, e, z_ref=None):
    out = dict(
        rel_recon_to_E=rel_fro(z, e),
        cos_row_to_own_E=float(row_cos(z, e).mean()),
        cos_row_to_own_E_median=float(np.median(row_cos(z, e))),
        procrustes_sim_to_E=procrustes_sim(z, e),
        gram_cos_to_E=gram_cos(z, e),
        spectrum_rel_diff_to_E=rel_fro(spectrum(z), spectrum(e)),
        row_norm_mean=float(np.linalg.norm(_f(z), axis=1).mean()),
    )
    mu = _f(z).mean(0)
    row_energy = float((_f(z) ** 2).sum(1).mean())
    out["share_of_row_energy_in_E_mean_dir"] = float((mu ** 2).sum() / max(row_energy, 1e-30))
    out.update(eff_rank(z))
    out.update(pair_retrieval(z, e))
    ez = _f(e) * (np.linalg.norm(_f(z), axis=1, keepdims=True)
                  / np.maximum(np.linalg.norm(_f(e), axis=1, keepdims=True), 1e-30))
    out["rel_diff_to_normcalibrated_E"] = rel_fro(z, ez)
    if z_ref is not None:
        out["rel_change_vs_correct_H"] = rel_fro(z, z_ref)
        out["gram_cos_vs_correct_H"] = gram_cos(z, z_ref)
        out["procrustes_sim_vs_correct_H"] = procrustes_sim(z, z_ref)
    return out


# --------------------------------------------------------------------------
# synthetic GLM-shaped fixtures
# --------------------------------------------------------------------------
def decay_var(dim, exponent):
    var = np.arange(1, dim + 1, dtype=np.float64) ** (-exponent)
    return (var / var.mean()).astype(np.float32)


def make_vocab(rng, vocab=VOCAB, dim=DIM, row_norm=1.0, exponent=0.6, mean_scale=0.4):
    """Anisotropic vocabulary with a shared, anisotropic mean and varied row norms."""
    sd = np.sqrt(decay_var(dim, exponent))
    w = (rng.standard_normal((vocab, dim)) * sd).astype(np.float32)
    direction = rng.standard_normal(dim).astype(np.float32)
    direction /= np.linalg.norm(direction)
    w += (mean_scale * sd * direction).astype(np.float32)
    w *= np.float32(row_norm / np.linalg.norm(w, axis=1).mean())
    return w


def make_hidden(rng, e_ref, row_norm=1.0, pair_cos=PAIR_COS, exponent=0.6, shared_frac=0.5):
    """Residual-stream-shaped rows: anisotropic, correlated, nonzero mean, paired to E."""
    sd = np.sqrt(decay_var(DIM, exponent))
    g = (rng.standard_normal(e_ref.shape) * sd).astype(np.float32)
    shared = (rng.standard_normal((1, DIM)) * sd).astype(np.float32)
    g = g + np.float32(shared_frac) * shared
    g *= np.float32(row_norm / np.linalg.norm(g, axis=1).mean())
    unit_e = e_ref / np.maximum(np.linalg.norm(e_ref, axis=1, keepdims=True), 1e-30)
    a = np.float32(row_norm * pair_cos / np.sqrt(1.0 - pair_cos ** 2))
    return (g + a * unit_e).astype(np.float32)


def fixture(seed=SEED, h_row_norm=1.0, e_row_norm=1.0, pair_cos=PAIR_COS, n_rows=N_ROWS):
    rng = np.random.default_rng(seed)
    w = make_vocab(rng, row_norm=e_row_norm)
    ids = rng.choice(VOCAB, n_rows, replace=False)
    e = w[ids].copy()
    h = make_hidden(rng, e, row_norm=h_row_norm, pair_cos=pair_cos)
    return dict(rng=rng, vocab=w, ids=ids, e=e, h=h)


# --------------------------------------------------------------------------
# experiment 1 -- parity of the numpy port against the torch implementation
# --------------------------------------------------------------------------
def exp_parity():
    sys.path.insert(0, EXT)
    import torch
    from occ3_sglang.statebridge import align_hidden_sequence as torch_align

    def run(name, hidden, embeddings, note):
        z_np, st = align_np(hidden, embeddings, TARGET_NORM)
        z_t, info = torch_align(torch.from_numpy(np.ascontiguousarray(hidden)),
                                torch.from_numpy(np.ascontiguousarray(embeddings)),
                                float(TARGET_NORM))
        z_t = z_t.numpy()
        diff = np.abs(_f(z_np) - _f(z_t))
        scale = float(np.abs(_f(z_t)).max())
        rec = dict(
            fixture=name, note=note,
            n=int(hidden.shape[0]), d=int(hidden.shape[1]),
            rank_numpy=int(st["rank"]), rank_torch=int(info.union_rank),
            threshold=float(st["threshold"]),
            max_abs_diff=float(diff.max()),
            max_rel_diff_vs_maxabs=float(diff.max() / max(scale, 1e-30)),
            rel_fro_diff=rel_fro(z_np, z_t),
            procrustes_objective=float(st["obj_whitened"]),
            procrustes_objective_from_output=rel_fro(
                _f(z_t) - _f(z_t).mean(0), _f(embeddings) - _f(embeddings).mean(0)),
            rel_recon_numpy=rel_fro(z_np, embeddings),
            rel_recon_torch=rel_fro(z_t, embeddings),
            cos_row_numpy=float(row_cos(z_np, embeddings).mean()),
            cos_row_torch=float(row_cos(z_t, embeddings).mean()),
            procrustes_objective_lower_bound=float(st["obj_lower_bound"]),
            flat_frac_x=float(st["flat_frac_x"]), flat_frac_y=float(st["flat_frac_y"]),
            gram_identity_error_x=float(st["gram_identity_error_x"]),
            gram_identity_error_y=float(st["gram_identity_error_y"]),
            nonzero_dirs_x=int(st["n_nonzero_x"]), nonzero_dirs_y=int(st["n_nonzero_y"]),
            sigma_x_max=float(st["sig_x"].max()), sigma_y_max=float(st["sig_y"].max()),
            sqrt_n=float(np.sqrt(hidden.shape[0])),
            rel_diff_to_normcalibrated_E=float(
                variant_metrics(z_t, embeddings)["rel_diff_to_normcalibrated_E"]),
        )
        return rec

    rng = np.random.default_rng(SEED + 11)
    out = []
    # (a) full-row-rank random data
    h = rng.standard_normal((N_ROWS, DIM)).astype(np.float32)
    e = rng.standard_normal((N_ROWS, DIM)).astype(np.float32)
    out.append(run("full_rank_independent", h, e, "both iid N(0,1); union rank 256 truncated to 254"))
    fx = fixture(seed=SEED + 12)
    out.append(run("full_rank_paired_glm_shape", fx["h"], fx["e"],
                   "GLM-shaped paired fixture, cosine 0.24"))
    # (b) rank-deficient H
    k = 16
    a = rng.standard_normal((N_ROWS, k)).astype(np.float32)
    b = rng.standard_normal((k, DIM)).astype(np.float32)
    hd = (a @ b) * np.float32(np.sqrt(DIM) / np.sqrt(k))
    out.append(run("rank_deficient_H", hd, e, "H = A(128x16) B(16x6144), exact rank 16"))
    hd2 = (a[:, :4] @ b[:4]) * np.float32(np.sqrt(DIM) / 2.0)
    out.append(run("rank_deficient_H4", hd2, e, "H rank 4, E full rank"))
    # degenerate: H = 0
    out.append(run("zero_H", np.zeros_like(e), e, "H all zeros; map collapses to the E mean"))
    return dict(cases=out)


# --------------------------------------------------------------------------
# experiment 2 -- union rank and conditioning under realistic spectra
# --------------------------------------------------------------------------
def exp_conditioning():
    rows = []
    spectra = {
        "lognormal_decay_0.6": 0.6,
        "lognormal_decay_1.2": 1.2,
        "lognormal_decay_2.0": 2.0,
    }
    seeds = [SEED + i for i in range(8)]
    for sname, exponent in spectra.items():
        for s in seeds:
            rng = np.random.default_rng(s)
            sd = np.sqrt(decay_var(DIM, exponent))
            h = (rng.standard_normal((N_ROWS, DIM)) * sd).astype(np.float32)
            h *= np.float32(1.0 / np.linalg.norm(h, axis=1).mean())
            shared = (rng.standard_normal((1, DIM)) * sd).astype(np.float32)
            h = h + np.float32(0.5) * shared        # correlated rows
            e = (rng.standard_normal((N_ROWS, DIM)) * sd).astype(np.float32)
            e *= np.float32(1.0 / np.linalg.norm(e, axis=1).mean())
            st = fit(h, e)
            s_all = st["singular"]
            lam_h = np.linalg.eigvalsh(st["ch"].astype(np.float64))
            lam_e = np.linalg.eigvalsh(st["ce"].astype(np.float64))
            rows.append(dict(
                spectrum=sname, seed=int(s), union_rank=int(st["rank"]),
                s_max=float(s_all[0]), s_retained_min=float(s_all[st["rank"] - 1]),
                s_first_rejected=float(s_all[st["rank"]]),
                s_min=float(s_all[-1]),
                threshold=float(st["threshold"]),
                cov_h_cond=float(lam_h.max() / lam_h.min()),
                cov_e_cond=float(lam_e.max() / lam_e.min()),
                lam_h_max=float(lam_h.max()), lam_h_median=float(np.median(lam_h)),
                lam_h_min=float(lam_h.min()),
                lam_e_max=float(lam_e.max()), lam_e_median=float(np.median(lam_e)),
                lam_e_min=float(lam_e.min()),
                n_dirs_below_ridge_H=int((lam_h < RIDGE).sum()),
                n_dirs_below_ridge_E=int((lam_e < RIDGE).sum()),
                ridge_over_lambda_median_H=float(RIDGE / max(np.median(lam_h), 1e-30)),
                ridge_over_lambda_median_E=float(RIDGE / max(np.median(lam_e), 1e-30)),
                eigen_floor_binding_dirs_H=int((lam_h < EIGEN_FLOOR).sum()),
                eigen_floor_binding_dirs_E=int((lam_e < EIGEN_FLOOR).sum()),
                max_whitening_gain_H=float(1.0 / np.sqrt(max(lam_h.min(), 1e-30))),
                max_whitening_gain_E=float(1.0 / np.sqrt(max(lam_e.min(), 1e-30))),
            ))
    # aggregate per spectrum
    summary = {}
    for sname in spectra:
        sel = [r for r in rows if r["spectrum"] == sname]
        summary[sname] = {
            k: dict(mean=float(np.mean([r[k] for r in sel])),
                    min=float(np.min([r[k] for r in sel])),
                    max=float(np.max([r[k] for r in sel])))
            for k in ("union_rank", "cov_h_cond", "cov_e_cond", "n_dirs_below_ridge_H",
                      "n_dirs_below_ridge_E", "ridge_over_lambda_median_H",
                      "ridge_over_lambda_median_E", "max_whitening_gain_H")
        }
    return dict(runs=rows, summary=summary)


# --------------------------------------------------------------------------
# experiment 2b -- why the Procrustes fit is exact in-sample (mechanism)
# --------------------------------------------------------------------------
def exp_mechanism(seed=SEED):
    """Ridge sweep and rank-cap sweep: what controls the in-sample interpolation.

    The whitened singular value of a direction with variance v is
    s / sqrt(s^2/n + ridge) = sqrt(n) / sqrt(1 + n*ridge/s^2).
    Every direction with s^2/n >> ridge is exactly sqrt(n), so both clouds get the
    same flat spectrum and von Neumann makes the orthogonal fit exact.  Only the
    directions with s^2/n near or below the ridge keep their own scale.
    """
    fx = fixture(seed=seed)
    h, e = fx["h"], fx["e"]
    target = float(np.linalg.norm(_f(e), axis=1).mean())
    rng = np.random.default_rng(seed + 83)
    unrelated = rng.standard_normal(h.shape).astype(np.float32)
    rows = []

    def add(case, data, hh, ee, ridge, cap, note):
        z, st = align_np(hh, ee, float(np.linalg.norm(_f(ee), axis=1).mean()), ridge=ridge, rank_cap=cap)
        zu, stu = align_np(unrelated, ee, float(np.linalg.norm(_f(ee), axis=1).mean()),
                           ridge=ridge, rank_cap=cap)
        m = variant_metrics(z, ee)
        rows.append(dict(
            case=case, data=data, note=note, ridge=float(ridge),
            rank_cap=("" if cap is None else int(cap)), union_rank=int(st["rank"]),
            procrustes_objective=float(st["obj_whitened"]),
            objective_lower_bound_from_spectra=float(st["obj_lower_bound"]),
            gram_identity_error_x=float(st["gram_identity_error_x"]),
            gram_identity_error_y=float(st["gram_identity_error_y"]),
            flat_frac_x=float(st["flat_frac_x"]), flat_frac_y=float(st["flat_frac_y"]),
            sigma_x_max=float(st["sig_x"].max()), sigma_y_max=float(st["sig_y"].max()),
            cos_row_to_E=m["cos_row_to_own_E"],
            rel_diff_to_normcalibrated_E=m["rel_diff_to_normcalibrated_E"],
            rel_change_unrelated_H=rel_fro(zu, z),
            cos_row_unrelated_H_output_to_E=float(row_cos(zu, ee).mean()),
        ))
        return z

    for ridge in (1e-4, 1e-3, 1e-2, 1e-1, 1.0, 10.0):
        add("ridge_sweep", "GLM-shaped paired fixture", h, e, ridge, None,
            "statebridge default is 1e-3")
    # control: two independent iid clouds, same shape, no relation at all
    a = rng.standard_normal(h.shape).astype(np.float32)
    b = (rng.standard_normal(e.shape) * np.float32(78.0)).astype(np.float32)
    add("iid_control", "two independent iid N(0,1) clouds", a, b, RIDGE, None,
        "no pairing, no shared structure; still congruent")
    for cap in (128, 64, 32, 16, 8):
        add("rank_cap_sweep", "GLM-shaped paired fixture", h, e, RIDGE, cap,
            "hypothesis only: cap the map rank below n-1")
    return dict(rows=rows, target_norm=target)


# --------------------------------------------------------------------------
# experiment 2c -- does the interpolation survive smaller capture windows?
# --------------------------------------------------------------------------
def exp_n_sweep(seed=SEED):
    """Union rank is 2(n-1), so the orthogonal rotation carries about rank^2/2 free
    parameters against n*rank numbers of data -- a ratio near 1 at every legal n."""
    rows = []
    for n in (8, 16, 32, 64, 128):
        fx = fixture(seed=seed, n_rows=n)
        h, e = fx["h"], fx["e"]
        rng = np.random.default_rng(seed + 137 + n)
        unrelated = rng.standard_normal(h.shape).astype(np.float32)
        target = float(np.linalg.norm(_f(e), axis=1).mean())
        z, st = align_np(h, e, target)
        zu, _ = align_np(unrelated, e, target)
        m = variant_metrics(z, e)
        rank = int(st["rank"])
        rows.append(dict(
            rows=int(n), union_rank=rank,
            rotation_free_parameters=float(rank * (rank - 1) / 2.0),
            data_entries=float(n * rank),
            parameter_to_data_ratio=float((rank * (rank - 1) / 2.0) / (n * rank)),
            procrustes_objective=float(st["obj_whitened"]),
            flat_frac_x=float(st["flat_frac_x"]), flat_frac_y=float(st["flat_frac_y"]),
            gram_identity_error_x=float(st["gram_identity_error_x"]),
            cos_row_to_E=m["cos_row_to_own_E"],
            rel_diff_to_normcalibrated_E=m["rel_diff_to_normcalibrated_E"],
            rel_change_unrelated_H=rel_fro(zu, z),
            cos_row_unrelated_H_output_to_E=float(row_cos(zu, e).mean()),
        ))
    return dict(rows=rows)


# --------------------------------------------------------------------------
# experiment 3 -- the confound set: E fixed, H varied
# --------------------------------------------------------------------------
def h_variants(rng, h):
    perm = rng.permutation(N_ROWS)
    unrelated = rng.standard_normal(h.shape).astype(np.float32)
    sm = rng.standard_normal(h.shape).astype(np.float32)
    sm *= np.float32(np.linalg.norm(_f(h)) / np.linalg.norm(_f(sm)))
    return {
        "correct_H": (h, "paired H, cosine 0.24 to E"),
        "row_shuffled_H": (h[perm], "same row set, pairing destroyed: mean and covariance identical"),
        "unrelated_random_H": (unrelated, "iid N(0,1) rows, no shared component, no pairing"),
        "scale_matched_random_H": (sm, "iid rows rescaled to ||H||_F of the correct H"),
        "zero_H": (np.zeros_like(h), "all-zero H: the map degenerates to the E mean"),
    }


def exp_confound(seed=SEED, h_row_norm=1.0, e_row_norm=1.0, label="primary"):
    fx = fixture(seed=seed, h_row_norm=h_row_norm, e_row_norm=e_row_norm)
    h, e = fx["h"], fx["e"]
    z_ref, st_ref = align_np(h, e)
    rng = np.random.default_rng(seed + 31)
    out = {}
    for name, (hv, note) in h_variants(rng, h).items():
        z, st = align_np(hv, e)
        m = variant_metrics(z, e, None if name == "correct_H" else z_ref)
        m.update(
            label=name, note=note,
            union_rank=int(st["rank"]),
            cov_h_spectrum_rel_diff_to_correct=rel_fro(spectrum(hv), spectrum(h)),
            h_row_norm_mean=float(np.linalg.norm(_f(hv), axis=1).mean()),
            h_cos_to_E=float(row_cos(hv, e).mean()) if name != "zero_H" else 0.0,
            procrustes_objective=float(st["obj_whitened"]),
        )
        out[name] = m
    ref = dict(
        e_row_norm_mean=float(np.linalg.norm(_f(e), axis=1).mean()),
        target_norm=float(np.linalg.norm(_f(e), axis=1).mean()),
        h_row_norm_mean=float(np.linalg.norm(_f(h), axis=1).mean()),
        h_cos_to_E=float(row_cos(h, e).mean()),
        e_spectrum_eff_rank=eff_rank(e)["eff_rank"],
        e_cos_rows_pairwise_mean=float((row_cos(e, e).sum() - N_ROWS) / (N_ROWS * (N_ROWS - 1))),
    )
    return dict(regime=label, params=dict(h_row_norm=h_row_norm, e_row_norm=e_row_norm, seed=int(seed)),
                reference=ref, variants=out,
                reference_spectrum_top8=[float(x) for x in spectrum(e)[:8]])


# --------------------------------------------------------------------------
# experiment 4 -- E row shuffle and E-only carriers
# --------------------------------------------------------------------------
def exp_target_side(seed=SEED):
    fx = fixture(seed=seed)
    h, e = fx["h"], fx["e"]
    z_ref, st_ref = align_np(h, e)
    rng = np.random.default_rng(seed + 47)
    perm = rng.permutation(N_ROWS)
    out = {}
    zs, sts = align_np(h, e[perm])
    m = variant_metrics(zs, e, z_ref)
    m.update(label="E_row_shuffled", note="H fixed, E row order permuted: E mean and covariance identical",
             union_rank=int(sts["rank"]),
             cos_row_to_permuted_E=float(row_cos(zs, e[perm]).mean()),
             cos_row_to_true_E=float(row_cos(zs, e).mean()))
    out["E_row_shuffled"] = m

    carriers = {}
    # (a) zero H: constant output along the E mean
    z0, st0 = align_np(np.zeros_like(h), e)
    # (b) E carried through itself: the map learns identity
    ze, ste = align_np(e, e)
    # (c) arbitrary whitened-shaped rows pushed through the correct fitted map
    r = rng.standard_normal(h.shape).astype(np.float32)
    r *= np.float32(np.linalg.norm(_f(h), axis=1).mean() / np.linalg.norm(_f(r), axis=1).mean())
    zr = apply_map(st_ref, r)
    zr = zr * np.float32(np.linalg.norm(_f(z_ref), axis=1).mean() / np.linalg.norm(_f(zr), axis=1).mean())
    # (d) rows of E pushed through the correct fitted map (E statistics, not H)
    zec = apply_map(st_ref, e)
    zec = zec * np.float32(np.linalg.norm(_f(z_ref), axis=1).mean() / np.linalg.norm(_f(zec), axis=1).mean())
    for name, (z, note) in {
        "carrier_zero_H": (z0, "H all zeros: output is one repeated row along mu_E"),
        "carrier_E_as_its_own_H": (ze, "map fitted and applied to E: near-identity map"),
        "carrier_random_rows_correct_state": (zr, "iid rows through the correct fitted map"),
        "carrier_E_rows_correct_state": (zec, "E rows through the correct fitted map"),
    }.items():
        m = variant_metrics(z, e, z_ref)
        m.update(label=name, note=note)
        carriers[name] = m
    out["carriers"] = carriers
    return dict(regime="primary", variants=out)


# --------------------------------------------------------------------------
# experiment 5 -- vocabulary anchoring, alpha 0 vs 0.3 (anchor_tiled, 115-136)
# --------------------------------------------------------------------------
def nearest_rows(query, weight, chunk=1024):
    q = _f(query)
    qn = q / np.maximum(np.linalg.norm(q, axis=1, keepdims=True), 1e-30)
    best = np.full(len(q), -np.inf)
    ids = np.zeros(len(q), dtype=np.int64)
    for start in range(0, weight.shape[0], chunk):
        tile = _f(weight[start:start + chunk])
        tile = tile / np.maximum(np.linalg.norm(tile, axis=1, keepdims=True), 1e-30)
        sims = qn @ tile.T
        local = sims.argmax(1)
        local_score = sims[np.arange(len(q)), local]
        better = local_score > best
        best = np.where(better, local_score, best)
        ids = np.where(better, local + start, ids)
    return ids, best


def anchor(prefix, weight, alpha):
    if alpha == 0.0:
        return np.ascontiguousarray(prefix, np.float32), np.zeros(len(prefix), np.int64), None
    ids, cos = nearest_rows(prefix, weight)
    snapped = np.asarray(weight[ids], np.float32)
    blend = ((1.0 - alpha) * _f(prefix) + alpha * _f(snapped)).astype(np.float32)
    return blend, ids, cos


def exp_anchoring(seed=SEED):
    fx = fixture(seed=seed)
    h, e, w = fx["h"], fx["e"], fx["vocab"]
    z_ref, st_ref = align_np(h, e)
    rng = np.random.default_rng(seed + 59)
    variants = {name: hv for name, (hv, _) in h_variants(rng, h).items()}
    zs = {name: align_np(hv, e)[0] for name, hv in variants.items()}

    out = {"alpha_0": {}, "alpha_0.3": {}, "vocab": dict(rows=int(w.shape[0]), dim=int(w.shape[1]),
                                                          mean_row_norm=float(np.linalg.norm(_f(w), axis=1).mean()))}
    ids0, cos0 = nearest_rows(z_ref, w)
    true_ids = fx["ids"]
    out["alpha_0"]["correct_H"] = dict(
        cos_to_nearest_before=float(cos0.mean()),
        cos_to_nearest_median=float(np.median(cos0)),
        nearest_id_equals_paired_token=float((ids0 == true_ids).mean()),
        nearest_id_equals_paired_token_p0=1.0 / w.shape[0],
    )
    blend, idsb, cosb = anchor(z_ref, w, 0.3)
    z_energy = float((_f(z_ref) ** 2).sum(1).mean())
    snapped = _f(w[idsb])
    cross = float((_f(z_ref) * snapped).sum(1).mean())
    b_energy = float((_f(blend) ** 2).sum(1).mean())
    out["alpha_0.3"]["correct_H"] = dict(
        cos_to_nearest_after=float((_f(blend) * snapped).sum(1).mean()
                                   / max(np.linalg.norm(_f(blend), axis=1).mean() * np.linalg.norm(snapped, axis=1).mean(), 1e-30)),
        cos_to_nearest_after_rowwise=float(row_cos(blend, snapped).mean()),
        rel_norm_change=float(np.linalg.norm(_f(blend), axis=1).mean() / np.linalg.norm(_f(z_ref), axis=1).mean()),
        rel_change_from_alpha0=rel_fro(blend, z_ref),
        cos_to_alpha0_output=float(row_cos(blend, z_ref).mean()),
        share_of_blend_energy_from_vocab_row=float(0.09 * float((snapped ** 2).sum(1).mean()) / max(b_energy, 1e-30)),
        share_of_blend_energy_from_unblended=float(0.49 * z_energy / max(b_energy, 1e-30)),
        share_of_blend_energy_cross_term=float(0.42 * cross / max(b_energy, 1e-30)),
        ids_changed_vs_alpha0=float((idsb != ids0).mean()),
    )
    # cross-variant behaviour of the anchor
    common = {}
    for name in zs:
        b, idn, cs = anchor(zs[name], w, 0.3)
        agree = float((idn == idsb).mean())
        common[name] = dict(
            nearest_id_agreement_with_correct_H=agree,
            rel_change_alpha0_vs_correct_H=rel_fro(zs[name], z_ref),
            rel_change_alpha03_vs_correct_H=rel_fro(b, blend),
            contraction_factor=float(rel_fro(b, blend) / max(rel_fro(zs[name], z_ref), 1e-30)),
            cos_to_nearest_after_rowwise=float(row_cos(b, _f(w[idn])).mean()),
            nearest_id_equals_paired_token=float((idn == true_ids).mean()),
        )
    out["cross_variant"] = common
    return out


# --------------------------------------------------------------------------
# experiment 6 -- out-of-sample fit
# --------------------------------------------------------------------------
def calibrate(z, target):
    norms = np.linalg.norm(_f(z), axis=1, keepdims=True)
    return (z * np.float32(target / norms)).astype(np.float32)


def exp_out_of_sample(seed=SEED):
    fx = fixture(seed=seed)
    h, e = fx["h"], fx["e"]
    half = N_ROWS // 2
    h_fit, e_fit, h_ho, e_ho = h[:half], e[:half], h[half:], e[half:]
    target = float(np.linalg.norm(_f(e), axis=1).mean())
    st = fit(h_fit, e_fit)
    z_in = calibrate(apply_map(st, h_fit), target)
    z_ho = calibrate(apply_map(st, h_ho), target)
    z_ref, _ = align_np(h, e, target)
    # constant-mean baseline: predict mu_E of the fit set for every held-out row
    const = np.repeat(st["me"], h_ho.shape[0], axis=0)
    const = calibrate(const, target)
    # shuffled-pairing control on the fit set
    perm = np.random.default_rng(seed + 71).permutation(half)
    st_sh = fit(h_fit[perm], e_fit)
    z_ho_sh = calibrate(apply_map(st_sh, h_ho), target)
    # leaky transductive upper bound: target statistics taken from the held-out E rows
    me_ho = e_ho.mean(0, keepdims=True).astype(np.float32)
    be_ho = (e_ho - me_ho) @ st["q"]
    ce_ho = be_ho.T @ be_ho / np.float32(half) + np.float32(RIDGE) * np.eye(st["rank"], dtype=np.float32)
    z_ho_leak = calibrate(
        ((((h_ho - st["mh"]) @ st["q"]) @ st["ch_inv"]) @ st["rot"] @ sym_power(ce_ho, 0.5, EIGEN_FLOOR)
         ) @ st["q"].T + me_ho, target)
    rec = dict(
        n_fit=int(half), n_holdout=int(N_ROWS - half), union_rank=int(st["rank"]),
        in_sample_rel_recon=rel_fro(z_in, e_fit),
        in_sample_cos=float(row_cos(z_in, e_fit).mean()),
        in_sample_retrieval=pair_retrieval(z_in, e_fit)["retrieval_top1"],
        holdout_rel_recon=rel_fro(z_ho, e_ho),
        holdout_cos=float(row_cos(z_ho, e_ho).mean()),
        holdout_retrieval=pair_retrieval(z_ho, e_ho)["retrieval_top1"],
        holdout_constant_mean_baseline=rel_fro(const, e_ho),
        holdout_shuffled_fit_control=rel_fro(z_ho_sh, e_ho),
        holdout_leaky_target_stats_upper_bound=rel_fro(z_ho_leak, e_ho),
        full_fit_reference_holdout=rel_fro(z_ref[half:], e_ho),
        holdout_shift_vs_full_fit=rel_fro(z_ho, z_ref[half:]),
        holdout_procrustes_sim_to_E=procrustes_sim(z_ho, e_ho),
        holdout_share_of_row_energy_in_E_mean_dir=float(
            ((_f(z_ho).mean(0) ** 2).sum()) / max((_f(z_ho) ** 2).sum(1).mean(), 1e-30)),
        holdout_eff_rank=eff_rank(z_ho)["eff_rank"],
        holdout_spectrum_rel_diff_to_E=rel_fro(spectrum(z_ho), spectrum(e_ho)),
    )
    # plain dual ridge regression H -> E (no whitening, no Procrustes): naive baseline
    lam = 1e-3 * float((_f(h_fit) ** 2).sum(1).mean())
    hc_fit = _f(h_fit) - _f(h_fit).mean(0)
    ec_fit = _f(e_fit) - _f(e_fit).mean(0)
    alpha = np.linalg.solve(hc_fit @ hc_fit.T + lam * np.eye(half), ec_fit)
    z_lin = (_f(h_ho) - _f(h_fit).mean(0)) @ (hc_fit.T @ alpha) + _f(e_fit).mean(0)
    rec["holdout_plain_ridge_regression"] = rel_fro(z_lin, e_ho)
    rec["holdout_plain_ridge_regression_from_centred_E"] = rel_fro(
        z_lin - _f(e_ho).mean(0), _f(e_ho) - _f(e_ho).mean(0))
    rec["heldout_over_insample_residual"] = float(rec["holdout_rel_recon"] / rec["in_sample_rel_recon"])
    return rec


# --------------------------------------------------------------------------
# experiment 7 -- sensitivity dZ/dH against dZ/dE
# --------------------------------------------------------------------------
def exp_sensitivity(seed=SEED):
    fx = fixture(seed=seed)
    h, e = fx["h"], fx["e"]
    z0, st = align_np(h, e)
    rows = []
    for eps in (0.01, 0.10):
        for kind in ("iid", "common_mode"):
            for target in ("H", "E"):
                rng = np.random.default_rng(seed + 101 + int(eps * 1000) + (kind == "common_mode") + 2 * (target == "E"))
                base = h if target == "H" else e
                noise = rng.standard_normal(base.shape)
                noise *= eps * float(np.sqrt((_f(base) ** 2).mean())) / float(noise.std())
                if kind == "common_mode":
                    noise = np.repeat(noise[:1], base.shape[0], axis=0)
                pert = (np.asarray(base, np.float64) + noise).astype(np.float32)
                z = align_np(pert, e)[0] if target == "H" else align_np(h, pert)[0]
                rel = rel_fro(z, z0)
                rows.append(dict(eps=eps, kind=kind, target=target,
                                 rel_output_change=rel,
                                 sensitivity_per_unit_relative_input=rel / eps,
                                 cos_row_to_correct_output=float(row_cos(z, z0).mean()),
                                 procrustes_sim_to_correct_output=procrustes_sim(z, z0)))
    # linear gain of the fitted map, as a supporting number
    gain = np.linalg.svd(st["ch_inv"] @ st["rot"] @ st["ce_sqrt"], compute_uv=False)
    lam_h = np.linalg.eigvalsh(st["ch"].astype(np.float64))
    extra = dict(
        map_rank=int(st["rank"]),
        map_sigma_max=float(gain.max()),
        map_sigma_min=float(gain.min()),
        map_condition=float(gain.max() / max(gain.min(), 1e-30)),
        ch_lambda_max=float(lam_h.max()),
        ch_lambda_min=float(lam_h.min()),
        ch_condition=float(lam_h.max() / lam_h.min()),
    )
    return dict(rows=rows, map_gain=extra)


# --------------------------------------------------------------------------
# verdicts -- explicit, documented thresholds (see the two markdown reports)
# --------------------------------------------------------------------------
def verdicts(res):
    v = {}
    par = res["exp1_parity"]["cases"]
    full = [c for c in par if c["fixture"].startswith("full_rank")]
    rdef = [c for c in par if c["fixture"].startswith("rank_deficient")]
    worst_full = max(c["rel_fro_diff"] for c in full)
    worst_def = max(c["rel_fro_diff"] for c in rdef)
    v["exp1_parity"] = dict(
        worst_full_rank_rel_diff=worst_full,
        worst_rank_deficient_rel_diff=worst_def,
        verdict=("PARITY CONFIRMED on full-rank data" if worst_full < 1e-3 else "PARITY FAILED"),
        second=("CARRIER NOT UNIQUE on rank-deficient data" if worst_def > 1e-2 else "carrier stable"))

    runs = res["exp2_conditioning"]["runs"]
    ranks = [r["union_rank"] for r in runs]
    ridge_dirs = [r["n_dirs_below_ridge_H"] for r in runs]
    floor_hits = sum(r["eigen_floor_binding_dirs_H"] + r["eigen_floor_binding_dirs_E"] for r in runs)
    v["exp2_conditioning"] = dict(
        union_rank_min=int(min(ranks)), union_rank_max=int(max(ranks)),
        eigen_floor_binding_total=int(floor_hits), max_dirs_below_ridge_H=int(max(ridge_dirs)),
        verdict=("RIDGE, NOT THE EIGEN FLOOR, SETS CONDITIONING" if floor_hits == 0 and max(ridge_dirs) > 0
                 else "INCONCLUSIVE"))

    mech = res["exp2b_mechanism"]["rows"]
    glm = [r for r in mech if r["case"] == "ridge_sweep"]
    iid = [r for r in mech if r["case"] == "iid_control"][0]
    caps = [r for r in mech if r["case"] == "rank_cap_sweep"]
    v["exp2b_mechanism"] = dict(
        glm_objective_at_default=[r["procrustes_objective"] for r in glm if r["ridge"] == RIDGE][0],
        iid_control_objective=iid["procrustes_objective"],
        iid_control_cos_row_to_E=iid["cos_row_to_E"],
        rank_cap_16_rel_change_unrelated=[r["rel_change_unrelated_H"] for r in caps if r["rank_cap"] == 16][0],
        verdict=("IN-SAMPLE INTERPOLATION CONFIRMED" if iid["procrustes_objective"] < 1e-3
                 else "INCONCLUSIVE"))

    prim = res["exp3_confound"]["primary"]["variants"]
    rnd = prim["unrelated_random_H"]
    v["exp3_confound"] = dict(
        cos_row_with_unrelated_random_H=rnd["cos_row_to_own_E"],
        rel_change_with_unrelated_random_H=rnd["rel_change_vs_correct_H"],
        relative_diff_to_normcalibrated_E=prim["correct_H"]["rel_diff_to_normcalibrated_E"],
        verdict=("CONFOUND PRESENT"
                 if (rnd["rel_change_vs_correct_H"] < 0.10 or rnd["cos_row_to_own_E"] > 0.90
                     or prim["correct_H"]["rel_diff_to_normcalibrated_E"] < 0.10) else "NOT PRESENT"))

    tgt = res["exp4_target_side"]["variants"]
    v["exp4_target_side"] = dict(
        e_shuffle_cos_to_permuted_E=tgt["E_row_shuffled"]["cos_row_to_permuted_E"],
        e_shuffle_rel_change=tgt["E_row_shuffled"]["rel_change_vs_correct_H"],
        zero_H_share_mu=tgt["carriers"]["carrier_zero_H"]["share_of_row_energy_in_E_mean_dir"],
        verdict=("CONFOUND PRESENT" if (tgt["E_row_shuffled"]["cos_row_to_permuted_E"] > 0.90
                                        or tgt["carriers"]["carrier_zero_H"]["share_of_row_energy_in_E_mean_dir"] > 0.10)
                 else "NOT PRESENT"))

    anc = res["exp5_anchoring"]
    cf = {k: c for k, c in anc["cross_variant"].items() if k != "correct_H"}
    ratios = [c["contraction_factor"] for c in cf.values()]
    v["exp5_anchoring"] = dict(
        cos_to_nearest_before=anc["alpha_0"]["correct_H"]["cos_to_nearest_before"],
        cos_to_nearest_after=anc["alpha_0.3"]["correct_H"]["cos_to_nearest_after_rowwise"],
        paired_token_recall_alpha0=anc["alpha_0"]["correct_H"]["nearest_id_equals_paired_token"],
        share_energy_from_vocab_row=anc["alpha_0.3"]["correct_H"]["share_of_blend_energy_from_vocab_row"],
        contraction_factors=[float(x) for x in ratios],
        verdict=("CONFOUND AMPLIFIED" if max(ratios) < 0.95 else "NOT PRESENT"))

    oos = res["exp6_out_of_sample"]
    v["exp6_out_of_sample"] = dict(
        holdout_rel_recon=oos["holdout_rel_recon"], in_sample_rel_recon=oos["in_sample_rel_recon"],
        ratio=oos["heldout_over_insample_residual"],
        constant_baseline=oos["holdout_constant_mean_baseline"],
        plain_ridge_baseline=oos["holdout_plain_ridge_regression_from_centred_E"],
        verdict=("DOES NOT TRANSFER" if oos["heldout_over_insample_residual"] > 2.0 else "TRANSFERS"))

    sens = {f"{r['target']}_{r['kind']}": r["sensitivity_per_unit_relative_input"]
            for r in res["exp7_sensitivity"]["rows"]}
    sh = max(v2 for k, v2 in sens.items() if k.startswith("H_"))
    se = max(v2 for k, v2 in sens.items() if k.startswith("E_"))
    v["exp7_sensitivity"] = dict(
        max_H_sensitivity=sh, max_E_sensitivity=se, ratio_H_over_E=sh / max(se, 1e-30),
        verdict=("E-DOMINATED" if se > 10.0 * sh else "H-DOMINATED"))
    return v


def jdump(obj):
    if isinstance(obj, dict):
        return {str(k): jdump(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [jdump(v) for v in obj]
    if isinstance(obj, (np.floating, np.integer)):
        return obj.item()
    if isinstance(obj, np.ndarray):
        return jdump(obj.tolist())
    if isinstance(obj, (np.bool_,)):
        return bool(obj)
    return obj


def fmt(rows, cols, widths=None):
    head = "  ".join(c.rjust(widths[i]) if widths else c for i, c in enumerate(cols))
    lines = [head, "-" * len(head)]
    for r in rows:
        cells = []
        for i, c in enumerate(cols):
            val = r.get(c, "")
            if isinstance(val, float):
                s = f"{val:.4g}" if (abs(val) < 1e4 and abs(val) >= 1e-3) else f"{val:.3e}"
            else:
                s = str(val)
            cells.append(s.rjust(widths[i]) if widths else s)
        lines.append("  ".join(cells))
    return "\n".join(lines)


def main():
    t0 = time.time()
    np.random.seed(SEED)
    print("align_audit: n=%d d=%d ridge=%g eigen_floor=%g rank_rtol=%g seed=%d"
          % (N_ROWS, DIM, RIDGE, EIGEN_FLOOR, RANK_RTOL, SEED))

    res = {}
    res["meta"] = dict(
        generated_by="reports/occ3-statebridge-recovery/analysis/align_audit.py",
        target="extensions/sglang-occ3/occ3_sglang/statebridge.py align_hidden_sequence (32-70), anchor_tiled (115-136)",
        dense_reference="occ3-latent-validation/references/StateBridge/methods/state_bridge.py (211-299)",
        n_rows=N_ROWS, dim=DIM, ridge=RIDGE, eigen_floor=EIGEN_FLOOR, rank_rtol=RANK_RTOL,
        vocab=VOCAB, pair_cos=PAIR_COS, seed=SEED, repo=REPO,
        numpy=np.__version__, python=sys.version.split()[0],
        scope="synthetic CPU fixtures; no real GLM hidden state, no DGX run, no source edit",
    )

    res["exp1_parity"] = exp_parity()
    res["exp2_conditioning"] = exp_conditioning()
    res["exp2b_mechanism"] = exp_mechanism()
    res["exp2c_n_sweep"] = exp_n_sweep()
    res["exp3_confound"] = {
        "primary": exp_confound(seed=SEED, h_row_norm=1.0, e_row_norm=1.0, label="h1_e1"),
        "large_H": exp_confound(seed=SEED, h_row_norm=60.0, e_row_norm=1.0, label="h60_e1"),
        "large_both": exp_confound(seed=SEED, h_row_norm=60.0, e_row_norm=60.0, label="h60_e60"),
    }
    res["exp4_target_side"] = exp_target_side()
    res["exp5_anchoring"] = exp_anchoring()
    res["exp6_out_of_sample"] = exp_out_of_sample()
    res["exp7_sensitivity"] = exp_sensitivity()
    res["verdicts"] = verdicts(res)
    res["meta"]["runtime_seconds"] = round(time.time() - t0, 1)

    with open(OUT_JSON, "w", encoding="utf-8") as fh:
        json.dump(jdump(res), fh, indent=1, sort_keys=False)
        fh.write("\n")
    print("wrote", OUT_JSON)

    print("\n[1] parity numpy port vs torch implementation")
    print(fmt(res["exp1_parity"]["cases"],
              ["fixture", "rank_numpy", "rank_torch", "max_abs_diff", "rel_fro_diff",
               "procrustes_objective", "procrustes_objective_lower_bound", "gram_identity_error_x",
               "flat_frac_x", "flat_frac_y", "rel_recon_torch", "cos_row_torch"]))
    print("\n[2] union rank / conditioning")
    srows = []
    for k, s in res["exp2_conditioning"]["summary"].items():
        srows.append(dict(spectrum=k,
                          union_rank_mean=s["union_rank"]["mean"],
                          union_rank_min=s["union_rank"]["min"],
                          union_rank_max=s["union_rank"]["max"],
                          cov_h_cond_mean=s["cov_h_cond"]["mean"],
                          cov_e_cond_mean=s["cov_e_cond"]["mean"],
                          dirs_below_ridge_H=s["n_dirs_below_ridge_H"]["mean"],
                          dirs_below_ridge_E=s["n_dirs_below_ridge_E"]["mean"],
                          ridge_over_lam_med_E=s["ridge_over_lambda_median_E"]["mean"],
                          max_gain_H=s["max_whitening_gain_H"]["mean"]))
    print(fmt(srows, ["spectrum", "union_rank_mean", "union_rank_min", "union_rank_max",
                      "cov_h_cond_mean", "cov_e_cond_mean", "dirs_below_ridge_H",
                      "dirs_below_ridge_E", "ridge_over_lam_med_E", "max_gain_H"]))
    print("\n[2b] mechanism: ridge sweep, iid control, rank-cap sweep")
    print(fmt(res["exp2b_mechanism"]["rows"],
              ["case", "ridge", "rank_cap", "union_rank", "procrustes_objective",
               "objective_lower_bound_from_spectra", "gram_identity_error_x", "gram_identity_error_y",
               "cos_row_to_E", "rel_diff_to_normcalibrated_E", "rel_change_unrelated_H"]))
    print("\n[2c] row-count sweep")
    print(fmt(res["exp2c_n_sweep"]["rows"],
              ["rows", "union_rank", "parameter_to_data_ratio", "procrustes_objective",
               "flat_frac_x", "gram_identity_error_x", "cos_row_to_E", "rel_diff_to_normcalibrated_E",
               "rel_change_unrelated_H"]))
    print("\n[3] confound set (E fixed, H varied)")
    for regime in ("primary", "large_H", "large_both"):
        block = res["exp3_confound"][regime]
        rows = []
        for name, m in block["variants"].items():
            rows.append(dict(variant=name,
                             rel_recon=m["rel_recon_to_E"],
                             cos_row=m["cos_row_to_own_E"],
                             rel_change=m.get("rel_change_vs_correct_H", 0.0),
                             rel_diff_ncal_E=m["rel_diff_to_normcalibrated_E"],
                             eff_rank=m["eff_rank"],
                             share_muE=m["share_of_row_energy_in_E_mean_dir"],
                             proc_sim_E=m["procrustes_sim_to_E"],
                             retrieval=m["retrieval_top1"]))
        print("regime", regime, "params", block["params"])
        print(fmt(rows, ["variant", "rel_recon", "cos_row", "rel_change", "rel_diff_ncal_E",
                         "eff_rank", "share_muE", "proc_sim_E", "retrieval"]))
        print()
    print("[4] E shuffle and E-only carriers")
    rows = []
    for name, m in res["exp4_target_side"]["variants"].items():
        items = m.items() if name == "carriers" else [(name, m)]
        for k, c in items:
            rows.append(dict(variant=k, rel_recon=c["rel_recon_to_E"], cos_row=c["cos_row_to_own_E"],
                             rel_change=c["rel_change_vs_correct_H"], proc_sim_corr=c["procrustes_sim_vs_correct_H"],
                             proc_sim_E=c["procrustes_sim_to_E"], gram_cos_corr=c["gram_cos_vs_correct_H"],
                             eff_rank=c["eff_rank"], share_muE=c["share_of_row_energy_in_E_mean_dir"]))
    print(fmt(rows, ["variant", "rel_recon", "cos_row", "rel_change", "proc_sim_corr",
                     "proc_sim_E", "gram_cos_corr", "eff_rank", "share_muE"]))
    print("  E_row_shuffled cosine to the permuted E rows:", res["exp4_target_side"]["variants"]["E_row_shuffled"]["cos_row_to_permuted_E"])
    print("\n[5] vocabulary anchoring")
    print("alpha=0 :", {k: (round(v, 5) if isinstance(v, float) else v)
                        for k, v in res["exp5_anchoring"]["alpha_0"]["correct_H"].items()})
    print("alpha=.3:", {k: (round(v, 5) if isinstance(v, float) else v)
                        for k, v in res["exp5_anchoring"]["alpha_0.3"]["correct_H"].items()})
    print(fmt(list(res["exp5_anchoring"]["cross_variant"].values()),
              ["nearest_id_agreement_with_correct_H", "rel_change_alpha0_vs_correct_H",
               "rel_change_alpha03_vs_correct_H", "contraction_factor",
               "cos_to_nearest_after_rowwise", "nearest_id_equals_paired_token"]))
    print("\n[6] out-of-sample")
    print({k: (round(v, 5) if isinstance(v, float) else v) for k, v in res["exp6_out_of_sample"].items()})
    print("\n[7] sensitivity")
    print(fmt(res["exp7_sensitivity"]["rows"],
              ["eps", "kind", "target", "rel_output_change", "sensitivity_per_unit_relative_input",
               "cos_row_to_correct_output", "procrustes_sim_to_correct_output"]))
    print("map gain:", {k: round(v, 5) if isinstance(v, float) else v
                        for k, v in res["exp7_sensitivity"]["map_gain"].items()})
    print("\nVERDICTS")
    for k, vv in res["verdicts"].items():
        print(" ", k, "->", vv["verdict"], "|", vv.get("second", ""))
    print("runtime", res["meta"]["runtime_seconds"], "s")


if __name__ == "__main__":
    main()
