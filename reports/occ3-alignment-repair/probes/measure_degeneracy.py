#!/usr/bin/env python
"""measure_degeneracy.py -- reproduce the in-sample degeneracy of the per-message
whitened Procrustes fit with the PRODUCTION solver.

Target (read-only): extensions/sglang-occ3/occ3_sglang/statebridge.py
  align_hidden_sequence()  lines 32-70; production call site latent_worker.py:333.

Every reported number comes from running this file.  Two solvers are used:
  PRODUCTION  the shipped torch function, imported unchanged.
  MIRROR      a statement-by-statement copy of the shipped body that also returns
              internals (ch, ce, x, y, R*, whitened singular values).  Parity of
              MIRROR.Z against PRODUCTION.Z is asserted on every production-legal
              run and its max abs difference is reported.

Usage (extension interpreter, from any cwd):
  C:/Users/openclawuser/optimus-occ3-latent-validation/extensions/sglang-occ3/.venv/Scripts/python.exe \
      reports/occ3-alignment-repair/probes/measure_degeneracy.py

Writes reports/occ3-alignment-repair/analysis/degeneracy_measurements.json and
prints one table per axis.  No network, no GPU, no model, no git.
"""
from __future__ import annotations

import json
import math
import os
import sys
import time

import numpy as np

REPO = "C:/Users/openclawuser/optimus-occ3-latent-validation"
EXT = REPO + "/extensions/sglang-occ3"
OUT = REPO + "/reports/occ3-alignment-repair/analysis/degeneracy_measurements.json"
sys.path.insert(0, EXT)

import torch  # noqa: E402
from occ3_sglang.statebridge import align_hidden_sequence  # noqa: E402
from occ3_sglang.latent_types import LatentError  # noqa: E402

DIM = 6144
VOCAB = 512
PAIR_COS = 0.24
DECAY = 0.6


# ---------------------------------------------------------------------------
# PRODUCTION wrapper: the shipped function, called with shipped defaults
# ---------------------------------------------------------------------------
def production(h_np, e_np, target_norm, ridge=1e-3):
    h = torch.from_numpy(np.ascontiguousarray(h_np, dtype=np.float32))
    e = torch.from_numpy(np.ascontiguousarray(e_np, dtype=np.float32))
    z, info = align_hidden_sequence(h, e, float(target_norm), ridge=float(ridge))
    return z.numpy().astype(np.float64), info


# ---------------------------------------------------------------------------
# MIRROR: exact copy of statebridge.py:48-68 with internals returned.
# guard_relaxed lets the mirror run cells the shipped guards refuse (ridge=0,
# n>128).  Such rows are labelled MIRROR in the report; they are never used as
# production evidence.
# ---------------------------------------------------------------------------
def mirror(hidden, embeddings, target_norm, *, ridge=1e-3, eigen_floor=1e-6,
           rank_rtol=1e-5, guard_relaxed=False, dtype=None):
    def sym_power(matrix, power, floor):
        vals, vectors = torch.linalg.eigh((matrix + matrix.mT) * 0.5)
        return (vectors * vals.clamp_min(floor).pow(power)) @ vectors.mT

    dt = dtype or torch.float32
    h, e = hidden.to(dt), embeddings.to(dt)
    n, d = h.shape
    if not guard_relaxed:
        assert 2 <= n <= 128 and d > 2 * n and ridge > 0 and eigen_floor > 0
    mh, me = h.mean(0, keepdim=True), e.mean(0, keepdim=True)
    hc, ec = h - mh, e - me
    _, singular, vh = torch.linalg.svd(torch.cat((hc, ec), dim=0), full_matrices=False)
    threshold = max(float(singular[0]) * rank_rtol, eigen_floor)
    rank = min(int((singular > threshold).sum()), 2 * (n - 1))
    q = vh[:rank].mT
    bh, be = hc @ q, ec @ q
    eye = torch.eye(rank, dtype=h.dtype)
    ch = bh.mT @ bh / n + ridge * eye
    ce = be.mT @ be / n + ridge * eye
    ch_inv, ce_inv, ce_sqrt = (sym_power(ch, -0.5, eigen_floor),
                              sym_power(ce, -0.5, eigen_floor),
                              sym_power(ce, 0.5, eigen_floor))
    x = bh @ ch_inv
    y = be @ ce_inv
    u, _, v = torch.linalg.svd(x.mT @ y, full_matrices=False)
    rot = u @ v
    z0 = (x @ rot @ ce_sqrt) @ q.mT + me
    norms = z0.norm(dim=-1, keepdim=True)
    z = z0 * (torch.as_tensor(target_norm, dtype=z0.dtype) / norms)
    lam_h = torch.linalg.eigvalsh((ch + ch.mT) * 0.5)
    lam_e = torch.linalg.eigvalsh((ce + ce.mT) * 0.5)
    lam_bh = torch.linalg.eigvalsh(bh.double().mT @ bh.double() / n)
    lam_be = torch.linalg.eigvalsh(be.double().mT @ be.double() / n)
    sv_x = torch.linalg.svdvals(x)
    sv_y = torch.linalg.svdvals(y)
    eye_n = torch.eye(n, dtype=torch.float64)
    gx = (x.double() @ x.double().mT)
    gy = (y.double() @ y.double().mT)
    proj = float(n) * (eye_n - torch.ones(n, n, dtype=torch.float64) / n)
    resid = (x @ rot - y).double()
    resid_e = resid @ ce_sqrt.double()
    return dict(
        z=z.numpy().astype(np.float64), z0=z0.numpy().astype(np.float64),
        dtype_used=str(dt),
        rank=int(rank), n=int(n), d=int(d), threshold=float(threshold),
        sigma_union=singular.numpy().astype(np.float64),
        lam_h=lam_h.numpy().astype(np.float64), lam_e=lam_e.numpy().astype(np.float64),
        sv_x=sv_x.numpy().astype(np.float64), sv_y=sv_y.numpy().astype(np.float64),
        lam_bh=lam_bh.numpy().astype(np.float64), lam_be=lam_be.numpy().astype(np.float64),
        gram_dev_x=float(torch.linalg.matrix_norm(gx - proj, ord=2).item()) / n,
        gram_dev_y=float(torch.linalg.matrix_norm(gy - proj, ord=2).item()) / n,
        gram_dev_xy=float(torch.linalg.matrix_norm(gx - gy, ord=2).item()) / n,
        gram_fro_xy=float(torch.linalg.matrix_norm(gx - gy, ord="fro").item()) / n,
        procrustes_residual=float(resid.norm().item() / max(float(y.double().norm()), 1e-30)),
        procrustes_residual_e=float(resid_e.norm().item()
                                   / max(float((y.double() @ ce_sqrt.double()).norm()), 1e-30)),
        sv_y_norm=float(y.double().norm().item()),
        det_rot=float(torch.linalg.det(rot.double()).item()) if rank >= 2 else 1.0,
        floor_binding_h=int((lam_h < eigen_floor).sum().item()),
        floor_binding_e=int((lam_e < eigen_floor).sum().item()),
    )


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------
def decay_sd(dim=DIM, exponent=DECAY):
    var = np.arange(1, dim + 1, dtype=np.float64) ** (-exponent)
    return np.sqrt(var / var.mean())


def draw_rows(rng, n, family, row_norm, sd, direction):
    """One family sample: isotropic = flat coordinates; anisotropic = decaying
    coordinates plus a shared direction tuned to the target pair cosine."""
    z = rng.standard_normal((n, DIM))
    if family == "isotropic":
        x = z
    else:
        rho = PAIR_COS
        base = z * sd
        base /= np.linalg.norm(base, axis=1, keepdims=True)
        x = math.sqrt(rho) * direction + math.sqrt(1.0 - rho) * base
    if row_norm is not None:
        x = x * (row_norm / np.linalg.norm(x, axis=1, keepdims=True))
    return x


def givens_apply(x, rng, pairs=96):
    y = np.array(x, dtype=np.float64, copy=True)
    for _ in range(pairs):
        i, j = rng.integers(0, y.shape[1], size=2)
        if i == j:
            continue
        th = rng.uniform(0.0, 2.0 * math.pi)
        c, s = math.cos(th), math.sin(th)
        a = y[:, i].copy()
        b = y[:, j].copy()
        y[:, i] = c * a - s * b
        y[:, j] = s * a + c * b
    return y


def make_cell(family, scale, n, seed):
    """Return dict with vocab W, ids, E, and the five H variants (float64)."""
    rng = np.random.default_rng(1000 * seed + 17)
    sd = decay_sd()
    direction = rng.standard_normal(DIM) * sd
    direction /= np.linalg.norm(direction)
    w = draw_rows(rng, VOCAB, family, scale, sd, direction)
    ids = rng.choice(VOCAB, n, replace=False)
    e = w[ids].copy()
    h_norm = float(np.linalg.norm(e, axis=1).mean())
    # correct H: an orthogonal feature rotation of E, rescaled, plus a little noise
    rot_e = givens_apply(e, rng)
    noise = draw_rows(rng, n, family, scale, sd, direction)
    h_correct = 2.5 * rot_e + 0.5 * noise
    h_exact = 2.5 * rot_e
    perm = rng.permutation(n)
    unrelated = draw_rows(rng, n, "isotropic", None, sd, direction)
    unrelated *= h_norm / np.linalg.norm(unrelated, axis=1, keepdims=True).mean()
    scale_matched = draw_rows(rng, n, "isotropic", None, sd, direction)
    scale_matched *= float(np.linalg.norm(h_correct)) / float(np.linalg.norm(scale_matched))
    return dict(
        w=w, ids=ids, e=e, perm=perm, target_norm=h_norm, noise=noise,
        h={
            "correct_H": h_correct,
            "unrelated_random_H": unrelated,
            "row_shuffled_H": h_correct[perm],
            "scale_matched_random_H": scale_matched,
            "correct_H_exact_rotation": h_exact,
        },
    )


# ---------------------------------------------------------------------------
# metrics
# ---------------------------------------------------------------------------
def row_cos(a, b):
    na = np.linalg.norm(a, axis=1)
    nb = np.linalg.norm(b, axis=1)
    return (a * b).sum(1) / np.maximum(na * nb, 1e-300)


def pair_cos(a):
    """Mean off-diagonal cosine of the row Gram (the cloud's internal angle)."""
    an = a / np.linalg.norm(a, axis=1, keepdims=True)
    g = an @ an.T
    n = len(a)
    return float((g.sum() - n) / (n * (n - 1)))


def rel_fro(a, b):
    return float(np.linalg.norm(a - b) / max(np.linalg.norm(b), 1e-300))


def erank(a):
    ac = a - a.mean(0)
    lam = np.linalg.eigvalsh(ac @ ac.T / ac.shape[0])[::-1]
    lam = np.clip(lam, 0.0, None)
    tot = lam.sum()
    if tot <= 0:
        return 0.0, 0
    p = lam[lam > 0] / tot
    return float(np.exp(-(p * np.log(p)).sum())), int((lam > 1e-6 * lam[0]).sum())


def retrieval(z, w, ids):
    zn = z / np.maximum(np.linalg.norm(z, axis=1, keepdims=True), 1e-300)
    wn = w / np.linalg.norm(w, axis=1, keepdims=True)
    top = (zn @ wn.T).argmax(1)
    return float((top == ids).mean())


def variant_metrics(z, cell):
    e = cell["e"]
    e_cal = e * (cell["target_norm"] / np.linalg.norm(e, axis=1, keepdims=True))
    c = row_cos(z, e)
    return dict(
        cos_mean=float(c.mean()), cos_median=float(np.median(c)), cos_min=float(c.min()),
        rel_recon_to_normcalibrated_E=rel_fro(z, e_cal),
        retrieval_top1=retrieval(z, cell["w"], cell["ids"]),
        retrieval_top1_of_E=retrieval(e_cal, cell["w"], cell["ids"]),
        nonfinite=int((~np.isfinite(z)).sum()),
        row_norm_mean=float(np.linalg.norm(z, axis=1).mean()),
    )


def trace_metrics(tr, n, ridge):
    root = math.sqrt(n)
    lam_h_live = tr["lam_h"]
    lam_bh, lam_be = tr["lam_bh"], tr["lam_be"]
    live_h = lam_bh > 1e-6 * max(float(lam_bh.max()), 1e-300)
    live_e = lam_be > 1e-6 * max(float(lam_be.max()), 1e-300)
    # whitening weight per live direction: w = lam / (lam + ridge); 1 - w is the
    # relative shrinkage that breaks X X^T = Y Y^T (derivation eq. 12-15).
    wh = lam_bh[live_h] / (lam_bh[live_h] + max(ridge, 0.0))
    we = lam_be[live_e] / (lam_be[live_e] + max(ridge, 0.0))
    r = tr["rank"]
    return dict(
        sv_x_live_min=float(lam_bh[live_h].min() / (lam_bh[live_h].min() + max(ridge, 0.0))),
        w_H_min=float(wh.min()), w_H_median=float(np.median(wh)), w_H_max=float(wh.max()),
        w_E_min=float(we.min()), w_E_median=float(np.median(we)), w_E_max=float(we.max()),
        one_minus_w_H_max=float(1.0 - wh.min()), one_minus_w_E_max=float(1.0 - we.min()),
        bound_gram_dev_x=float(1.0 - wh.min()),
        bound_gram_dev_y=float(1.0 - we.min()),
        bound_gram_dev_xy=float(2.0 - wh.min() - we.min()),
        lam_bh_min_live=float(lam_bh[live_h].min()), lam_bh_median_live=float(np.median(lam_bh[live_h])),
        lam_bh_max=float(lam_bh.max()),
        lam_be_min_live=float(lam_be[live_e].min()), lam_be_max=float(lam_be.max()),
        rank_bh=int(live_h.sum()), rank_be=int(live_e.sum()), union_rank_r=int(r),
        null_directions_bh=int(r - live_h.sum()),
        floor_gain=float(1.0 / math.sqrt(1e-6)),
        eig_ch_min=float(lam_h_live.min()), eig_ch_median=float(np.median(lam_h_live)),
        eig_ch_max=float(lam_h_live.max()),
        eig_ce_min=float(tr["lam_e"].min()), eig_ce_median=float(np.median(tr["lam_e"])),
        eig_ce_max=float(tr["lam_e"].max()),
        floor_binding_h=tr["floor_binding_h"], floor_binding_e=tr["floor_binding_e"],
        n_live_whitened=int(live_h.sum()),
        sv_x_over_sqrtn_min=float(tr["sv_x"][int(live_h.sum()) - 1] / root),
        sv_x_over_sqrtn_median=float(np.median(tr["sv_x"][:int(live_h.sum())]) / root),
        sv_x_over_sqrtn_max=float(tr["sv_x"].max() / root),
        sv_x_null_max_over_sqrtn=float(tr["sv_x"][int(live_h.sum())] / root)
        if int(live_h.sum()) < r else 0.0,
        sv_y_over_sqrtn_min=float(tr["sv_y"][int(live_e.sum()) - 1] / root),
        sv_y_over_sqrtn_max=float(tr["sv_y"].max() / root),
        gram_dev_x=tr["gram_dev_x"], gram_dev_y=tr["gram_dev_y"], gram_dev_xy=tr["gram_dev_xy"],
        procrustes_residual=tr["procrustes_residual"],
        procrustes_residual_in_E_space=tr["procrustes_residual_e"],
        det_rot=tr["det_rot"],
    )


# ---------------------------------------------------------------------------
# one cell: production + mirror for every H variant
# ---------------------------------------------------------------------------
def run_cell(axis, family, scale, n, ridge, seed, relaxed=False, variants=None):
    cell = make_cell(family, scale, n, seed)
    names = variants or list(cell["h"].keys())
    z = {}
    out = []
    parity = 0.0
    for name in names:
        h = cell["h"][name]
        ref = None
        try:
            zp, info = production(h, cell["e"], cell["target_norm"], ridge=ridge)
            mode = "PRODUCTION"
            rank = info.union_rank
        except LatentError as exc:
            out.append(dict(axis=axis, family=family, scale=scale, n=n, ridge=ridge,
                            seed=seed, variant=name, mode="REFUSED",
                            refusal=str(exc)))
            continue
        zm = mirror(torch.from_numpy(h.astype(np.float32)),
                    torch.from_numpy(cell["e"].astype(np.float32)),
                    cell["target_norm"], ridge=ridge, guard_relaxed=relaxed)
        parity = max(parity, float(np.abs(zp - zm["z"]).max()))
        z[name] = zp
        m = variant_metrics(zp, cell)
        m.update(trace_metrics(zm, n, ridge))
        m["max_abs_parity_diff_mirror_vs_production"] = float(np.abs(zp - zm["z"]).max())
        m.update(dict(axis=axis, family=family, scale=scale, n=int(n), ridge=float(ridge),
                      seed=int(seed), variant=name, mode=mode, union_rank=int(rank)))
        m["erank_Hc"], m["raw_rank_Hc"] = erank(h)
        m["erank_Ec"], m["raw_rank_Ec"] = erank(cell["e"])
        out.append(m)
    for rec in out:
        if rec["mode"] != "PRODUCTION":
            continue
        zp = z[rec["variant"]]
        if "correct_H" in z:
            rec["rel_change_vs_correct_H"] = rel_fro(zp, z["correct_H"])
            rec["cos_rows_vs_correct_H"] = float(row_cos(zp, z["correct_H"]).mean())
        if "unrelated_random_H" in z:
            rec["rel_change_vs_random_H"] = rel_fro(zp, z["unrelated_random_H"])
        if rec["variant"] == "row_shuffled_H":
            rec["cos_vs_E_under_permutation"] = float(
                row_cos(zp, cell["e"][cell["perm"]]).mean())
            rec["rel_change_vs_correct_H_under_permutation"] = rel_fro(
                zp, z["correct_H"][cell["perm"]])
    return out, dict(axis=axis, family=family, scale=scale, n=int(n), ridge=float(ridge),
                     seed=int(seed), parity=parity,
                     e_pair_cos=pair_cos(cell["e"]),
                     h_pair_cos=pair_cos(cell["h"]["correct_H"]),
                     unrelated_h_pair_cos=pair_cos(cell["h"]["unrelated_random_H"]),
                     e_row_norm_mean=float(np.linalg.norm(cell["e"], axis=1).mean()),
                     h_row_norm_mean=float(np.linalg.norm(cell["h"]["correct_H"], axis=1).mean()),
                     erank_E=erank(cell["e"])[0], erank_H=erank(cell["h"]["correct_H"])[0])


def main():
    t0 = time.time()
    runs, cells, refusals = [], [], []

    # A -- main matrix: 2 families x 2 scales x 5 H variants x 3 seeds, N=128, ridge shipped
    for family in ("isotropic", "anisotropic"):
        for scale in (1.0, 64.0):
            for seed in (1, 2, 3):
                recs, meta = run_cell("A_main", family, scale, 128, 1e-3, seed)
                runs += [r for r in recs if r["mode"] == "PRODUCTION"]
                refusals += [r for r in recs if r["mode"] == "REFUSED"]
                cells.append(meta)

    # B -- N sweep (anisotropic, both scales, correct + unrelated H)
    for scale in (1.0, 64.0):
        for n in (16, 32, 64, 128, 256):
            recs, meta = run_cell("B_n_sweep", "anisotropic", scale, n, 1e-3, 1,
                                  relaxed=(n > 128), variants=["correct_H", "unrelated_random_H"])
            runs += [r for r in recs if r["mode"] == "PRODUCTION"]
            refusals += [r for r in recs if r["mode"] == "REFUSED"]
            cells.append(meta)
            if n > 128:
                # guard-relaxed mirror, labelled separately: never production evidence
                cell = make_cell("anisotropic", scale, n, 1)
                for name in ("correct_H", "unrelated_random_H"):
                    tr = mirror(torch.from_numpy(cell["h"][name].astype(np.float32)),
                                torch.from_numpy(cell["e"].astype(np.float32)),
                                cell["target_norm"], ridge=1e-3, guard_relaxed=True)
                    m = variant_metrics(tr["z"], cell)
                    m.update(trace_metrics(tr, n, 1e-3))
                    m.update(dict(axis="B_n_sweep", family="anisotropic", scale=scale,
                                  n=int(n), ridge=1e-3, seed=1, variant=name, mode="MIRROR",
                                  union_rank=tr["rank"]))
                    m["erank_Hc"], m["raw_rank_Hc"] = erank(cell["h"][name])
                    m["erank_Ec"], m["raw_rank_Ec"] = erank(cell["e"])
                    runs.append(m)

    # C -- ridge sweep.  Production refuses ridge=0 (statebridge.py:41); the row is
    # recorded as REFUSED and the limit is probed with the guard-relaxed mirror.
    for family in ("isotropic", "anisotropic"):
        for scale in (1.0, 64.0):
            for ridge in (1e-6, 1e-4, 1e-3, 1e-2, 1e-1):
                recs, meta = run_cell("C_ridge_sweep", family, scale, 128, ridge, 1,
                                      variants=["correct_H", "unrelated_random_H",
                                                "correct_H_exact_rotation"])
                runs += [r for r in recs if r["mode"] == "PRODUCTION"]
                refusals += [r for r in recs if r["mode"] == "REFUSED"]
                cells.append(meta)
            for ridge in (0.0, 1e-8):
                recs, meta = run_cell("C_ridge_sweep", family, scale, 128, ridge, 1,
                                      relaxed=True, variants=["correct_H", "unrelated_random_H"])
                refusals += [r for r in recs if r["mode"] == "REFUSED"]
                cell = make_cell(family, scale, 128, 1)
                got, pending = {}, []
                for name in ("correct_H", "unrelated_random_H"):
                    tr = mirror(torch.from_numpy(cell["h"][name].astype(np.float32)),
                                torch.from_numpy(cell["e"].astype(np.float32)),
                                cell["target_norm"], ridge=max(ridge, 0.0), guard_relaxed=True)
                    got[name] = tr["z"]
                    m = variant_metrics(tr["z"], cell)
                    m.update(trace_metrics(tr, 128, max(ridge, 0.0)))
                    m.update(dict(axis="C_ridge_sweep", family=family, scale=scale, n=128,
                                  ridge=float(ridge), seed=1, variant=name, mode="MIRROR",
                                  union_rank=tr["rank"]))
                    m["erank_Hc"], m["raw_rank_Hc"] = erank(cell["h"][name])
                    m["erank_Ec"], m["raw_rank_Ec"] = erank(cell["e"])
                    pending.append((m, name))
                for m, name in pending:
                    m["rel_change_vs_correct_H"] = rel_fro(got[name], got["correct_H"])
                    m["rel_change_vs_random_H"] = rel_fro(got[name], got["unrelated_random_H"])
                    runs.append(m)

    # D -- rank-deficiency control.  The derivation's hypothesis is
    # rank(Hc) = rank(Ec) = n-1; this axis checks that the hypothesis is necessary.
    for scale in (1.0, 64.0):
        for k in (8, 64, 126):
            cell = make_cell("isotropic", scale, 128, 5)
            rng = np.random.default_rng(900 + k)
            a = rng.standard_normal((128, k))
            b = rng.standard_normal((k, DIM)) * (scale * math.sqrt(DIM / k) / math.sqrt(DIM))
            h_low = a @ b
            e_low = cell["e"]
            lam_e = np.linalg.qr(rng.standard_normal((DIM, k)))[0].mT
            e_rank_def = rng.standard_normal((128, k)) @ lam_e * scale
            for tag, hh, ee in (("H_rank_%d" % k, h_low, e_low),
                                ("E_rank_%d" % k, cell["h"]["correct_H"], e_rank_def)):
                tn = float(np.linalg.norm(ee, axis=1).mean())
                try:
                    zp, info = production(hh, ee, tn, ridge=1e-3)
                except LatentError as exc:
                    refusals.append(dict(axis="D_rank", family="isotropic", scale=scale,
                                         n=128, ridge=1e-3, seed=5, variant=tag,
                                         mode="REFUSED", refusal=str(exc)))
                    continue
                tr = mirror(torch.from_numpy(hh.astype(np.float32)),
                            torch.from_numpy(ee.astype(np.float32)), tn, ridge=1e-3)
                m = variant_metrics(zp, dict(e=ee, w=cell["w"], ids=cell["ids"], target_norm=tn))
                m.update(trace_metrics(tr, 128, 1e-3))
                m.update(dict(axis="D_rank", family="isotropic", scale=scale, n=128, ridge=1e-3,
                              seed=5, variant=tag, mode="PRODUCTION",
                              union_rank=int(info.union_rank),
                              max_abs_parity_diff_mirror_vs_production=float(
                                  np.abs(zp - tr["z"]).max())))
                m["erank_Hc"], m["raw_rank_Hc"] = erank(hh)
                m["erank_Ec"], m["raw_rank_Ec"] = erank(ee)
                runs.append(m)

    # E -- numerical-floor diagnosis at the lambda -> 0 limit (mirror only).
    # Two questions: (a) is the ~1e-4 residual floor at realistic scale caused by
    # float32 rounding amplified through eigen_floor on null whitening directions,
    # or by the ridge?  (b) does raising eigen_floor suppress it?
    for scale in (1.0, 64.0):
        cell = make_cell("isotropic", scale, 128, 3)
        hh = torch.from_numpy(cell["h"]["correct_H"].astype(np.float32))
        ee = torch.from_numpy(cell["e"].astype(np.float32))
        for label, kw in (("fp32_floor_1e-6", dict(dtype=torch.float32, eigen_floor=1e-6)),
                          ("fp32_floor_1e-4", dict(dtype=torch.float32, eigen_floor=1e-4)),
                          ("fp32_floor_1e-2", dict(dtype=torch.float32, eigen_floor=1e-2)),
                          ("fp32_floor_1e0", dict(dtype=torch.float32, eigen_floor=1.0)),
                          ("fp64_floor_1e-6", dict(dtype=torch.float64, eigen_floor=1e-6))):
            tr = mirror(hh, ee, cell["target_norm"], ridge=1e-8, guard_relaxed=True, **kw)
            m = variant_metrics(tr["z"], cell)
            m.update(trace_metrics(tr, 128, 1e-8))
            m.update(dict(axis="E_floor", family="isotropic", scale=scale, n=128, ridge=1e-8,
                          seed=3, variant=label, mode="MIRROR", union_rank=tr["rank"],
                          dtype_used=tr["dtype_used"]))
            m["erank_Hc"], m["raw_rank_Hc"] = erank(cell["h"]["correct_H"])
            m["erank_Ec"], m["raw_rank_Ec"] = erank(cell["e"])
            runs.append(m)

    # F -- isometric H.  H = E Q for an orthogonal Q built from 96 Givens rotations:
    # the centred row Grams are equal, so the whitening weights agree exactly and Z must
    # equal E_cal at ANY ridge (corollary 3).  The 2.5x row is the control: a common
    # scale factor does NOT commute with the additive ridge, so the weights differ.
    for scale in (1.0, 64.0):
        for family in ("isotropic", "anisotropic"):
            cell = make_cell(family, scale, 128, 7)
            rng = np.random.default_rng(4242)
            rot = givens_apply(cell["e"], rng)
            for tag, hh in (("isometric_H_same_gram", rot), ("isometric_H_2.5x_scale", 2.5 * rot)):
                for ridge in (1e-3, 1e-1, 1.0, 1e2):
                    try:
                        zp, info = production(hh, cell["e"], cell["target_norm"], ridge=ridge)
                    except LatentError as exc:
                        refusals.append(dict(axis="F_shared", family=family, scale=scale, n=128,
                                             ridge=ridge, seed=7, variant=tag, mode="REFUSED",
                                             refusal=str(exc)))
                        continue
                    tr = mirror(torch.from_numpy(hh.astype(np.float32)),
                                torch.from_numpy(cell["e"].astype(np.float32)),
                                cell["target_norm"], ridge=ridge)
                    m = variant_metrics(zp, cell)
                    m.update(trace_metrics(tr, 128, ridge))
                    m.update(dict(axis="F_shared", family=family, scale=scale, n=128,
                                  ridge=float(ridge), seed=7, variant=tag, mode="PRODUCTION",
                                  union_rank=int(info.union_rank),
                                  max_abs_parity_diff_mirror_vs_production=float(
                                      np.abs(zp - tr["z"]).max())))
                    m["erank_Hc"], m["raw_rank_Hc"] = erank(hh)
                    m["erank_Ec"], m["raw_rank_Ec"] = erank(cell["e"])
                    runs.append(m)

    # G -- is the retained union rank genuine or float32 noise?  Measure the singular
    # spectrum of the concatenated centred pair against the rank_rtol=1e-5 threshold
    # that statebridge.py:55 uses to cut it.  Isometric H has a distinct row space
    # (equal Gram, different subspace), so rank 2(n-1) is expected there as well.
    spectra = []
    for scale in (1.0, 64.0):
        cell = make_cell("isotropic", scale, 128, 9)
        rng = np.random.default_rng(31)
        variants = {"isometric_H": givens_apply(cell["e"], rng),
                    "independent_H": cell["h"]["unrelated_random_H"]}
        for name, hh in variants.items():
            h = torch.from_numpy(hh.astype(np.float32))
            e = torch.from_numpy(cell["e"].astype(np.float32))
            hc, ec = h - h.mean(0, keepdim=True), e - e.mean(0, keepdim=True)
            _, sv, _ = torch.linalg.svd(torch.cat((hc, ec), 0), full_matrices=False)
            svn = sv.numpy().astype(np.float64)
            thr = max(float(svn[0]) * 1e-5, 1e-6)
            spectra.append(dict(axis="G_spectrum", family="isotropic", scale=scale, n=128,
                                variant=name, union_rank_reported=int(min(
                                    int((svn > thr).sum()), 2 * 127)),
                                s0=float(svn[0]), s126=float(svn[126]), s127=float(svn[127]),
                                s128=float(svn[128]), s252=float(svn[252]), s253=float(svn[253]),
                                threshold=float(thr),
                                s127_over_s0=float(svn[127] / svn[0]),
                                s253_over_s0=float(svn[253] / svn[0]),
                                float32_eps=float(np.finfo(np.float32).eps)))

    payload = dict(
        meta=dict(target="extensions/sglang-occ3/occ3_sglang/statebridge.py align_hidden_sequence",
                  command=("extensions/sglang-occ3/.venv/Scripts/python.exe "
                           "reports/occ3-alignment-repair/probes/measure_degeneracy.py"),
                  python=sys.version.split()[0], torch=torch.__version__,
                  dim=DIM, vocab=VOCAB, pair_cos=PAIR_COS, decay=DECAY,
                  prod_ridge=1e-3, prod_eigen_floor=1e-6, prod_rank_rtol=1e-5,
                  runtime_seconds=0.0),
        runs=runs, cells=cells, refusals=refusals, spectra=spectra)
    payload["meta"]["runtime_seconds"] = round(time.time() - t0, 2)
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(payload, fh, indent=1, sort_keys=True)
    print(json.dumps(payload["meta"], indent=1))
    print("runs", len(runs), "refusals", len(refusals))
    worst = max((c["parity"] for c in cells), default=0.0)
    print("max |mirror - production| over all runs:", worst)


if __name__ == "__main__":
    main()
