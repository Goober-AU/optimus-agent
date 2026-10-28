"""Offline calibration-generalisation experiment for the OCC3 StateBridge alignment.

Question: can a FROZEN alignment fitted on independent calibration rows map NEW
hidden states into the receiver embedding space well enough to be useful?

Methods
-------
A  per-message whitened Procrustes, fitted and applied to the same rows (production)
B  whitened Procrustes fitted on calibration rows, frozen
C  affine orthogonal Procrustes, aligned(z) = mu_E + s (z - mu_H) R, frozen
D  dual ridge, frozen
F  direct reference-token embeddings of the message rows (confound ceiling)
K  zero state
O  oracle: the generator-internal predictable part of e (upper bound, not a method)
G  oracle: the true content rotation used as METHOD C's R (ceiling of the C model class)

Every reported number is produced by this script.

  python reports/occ3-alignment-repair/analysis/calibration_experiment.py \
      --out reports/occ3-alignment-repair/analysis/calibration_results.json \
      --tables reports/occ3-alignment-repair/analysis/tables
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys
import time

import torch

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import calibration_methods as cm  # noqa: E402

torch.set_grad_enabled(False)

DEFAULT_SCENARIOS = [
    # name,   shared, f_content, kappa
    ("null", False, 0.55, 0.50),
    ("weak", True, 0.30, 0.50),
    ("mid", True, 0.55, 0.50),
    ("strong", True, 0.85, 0.50),
]

METHOD_ORDER = ["A", "B", "C", "D", "F", "K", "O", "G", "I"]


# --------------------------------------------------------------------------- #
# fixture generator
# --------------------------------------------------------------------------- #
def _orthonormal(blob):
    q, rr = torch.linalg.qr(blob)
    sign = torch.sign(torch.diagonal(rr))
    sign[sign == 0] = 1.0
    return q * sign.unsqueeze(0)


def _unit(d, gen):
    v = torch.randn(1, d, generator=gen)
    return v / v.norm()


def _solve_rho(unit_mean, directions, f, noise_dirs, target, iters=40):
    """Bisect the mean-direction variance share so the mean pair cosine hits target."""
    def pair_cos(rho):
        rows = (math.sqrt(rho) * unit_mean
                + math.sqrt(max(1.0 - rho, 0.0)) * (math.sqrt(f) * directions
                                                    + math.sqrt(1.0 - f) * noise_dirs))
        return cm.mean_pair_cosine(rows)

    lo, hi = 0.0, 0.999
    if pair_cos(hi) < target:
        return hi
    if pair_cos(lo) > target:
        return lo
    for _ in range(iters):
        mid = 0.5 * (lo + hi)
        if pair_cos(mid) < target:
            lo = mid
        else:
            hi = mid
    return 0.5 * (lo + hi)


class World:
    """One generator family: fixed bases, spectrum, mean directions and scales.

    Row construction (each population draw is an independent draw of rows):

        h_i = sqrt(rho_h) mh + sqrt(1-rho_h) ( sqrt(f) u_h,i + sqrt(1-f) vh_i )
        e_i = sqrt(rho_e) me + sqrt(1-rho_e) ( sqrt(f) u_e,i + sqrt(1-f) ve_i )

    with unit content directions

        u_h,i = ( sqrt(1-kappa) Uh c_i + sqrt(kappa) U0 c_i ) / |c_i|
        u_e,i = ( sqrt(1-kappa) Ue Wc c_i + sqrt(kappa) U0 c_i ) / |c_i|

    ``c_i ~ N(0, diag(spec^2))`` is the shared code, so one fixed rank-``content_rank``
    linear map L satisfies u_e,i = L u_h,i. ``kappa`` is the share of the content that
    is shared in the same ambient subspace on both sides; that is what makes a matched
    (h_i, e_i) pair more similar than a random pair. kappa = 0 leaves only the rotated
    path: still exactly linear, but its sample cross-covariance is dominated by noise
    unless M is enormous. Mean directions dominate the pairwise cosine, which stays at
    ``target_pair`` for matched and random pairs alike.
    """

    def __init__(self, *, dim, content_rank, spec_exp, target_pair, f_content, shared,
                 seed, norm_sigma, kappa):
        gen = torch.Generator().manual_seed(10_000 + seed)
        self.dim = dim
        self.content_rank = content_rank
        self.f_content = f_content
        self.shared = shared
        self.kappa = kappa
        self.norm_sigma = norm_sigma
        self.h_scale = 30.0
        self.seed = seed
        self.uh = _orthonormal(torch.randn(dim, content_rank, generator=gen))
        self.ue = _orthonormal(torch.randn(dim, content_rank, generator=gen))
        self.wc = _orthonormal(torch.randn(content_rank, content_rank, generator=gen))
        # U0 must be exactly orthogonal to span([Uh, Ue]); projecting against each basis
        # separately is not enough when the two bases themselves overlap, so project
        # against an orthonormal basis of their joint span.
        joint = _orthonormal(torch.cat((self.uh, self.ue), dim=1))
        blob = torch.randn(dim, content_rank, generator=gen)
        blob = blob - joint @ (joint.mT @ blob)
        self.u0 = _orthonormal(blob)
        spec = (torch.arange(content_rank, dtype=torch.float32) + 1.0).pow(-spec_exp)
        self.spec = spec / spec.norm()
        self.mh = _unit(dim, gen)
        self.me = _unit(dim, gen)
        probe = torch.Generator().manual_seed(20_000 + seed)
        code = torch.randn(1024, content_rank, generator=probe) * self.spec
        uh_rows, ue_rows = self._content_dirs(code)
        vh = cm.unit_rows(torch.randn(1024, dim, generator=probe))
        ve = cm.unit_rows(torch.randn(1024, dim, generator=probe))
        self.rho_h = _solve_rho(self.mh, uh_rows, f_content, vh, target_pair)
        self.rho_e = _solve_rho(self.me, ue_rows, f_content, ve, target_pair)
        # E[c c^T / |c|^2] for the direction-normalised code: not diag(spec^2) unless the
        # code is high dimensional, so it is estimated once by Monte Carlo and cached.
        code_gen = torch.Generator().manual_seed(40_000 + seed)
        codes = torch.randn(65_536, content_rank, generator=code_gen) * self.spec
        norm2 = (codes ** 2).sum(dim=1, keepdim=True).clamp_min(1e-30)
        self.code_second = ((codes.mT @ (codes / norm2)) / codes.shape[0]).to(torch.float64)
        self._draws = 0

    def _content_dirs(self, code):
        scale = code.norm(dim=-1, keepdim=True).clamp_min(1e-30)
        k = self.kappa
        shared = code @ self.u0.mT
        left = (math.sqrt(1.0 - k) * (code @ self.uh.mT) + math.sqrt(k) * shared) / scale
        right = (math.sqrt(1.0 - k) * (code @ self.wc.mT @ self.ue.mT)
                 + math.sqrt(k) * shared) / scale
        return left, right

    def expected_cross_cov(self, q, m_rows):
        """Exact E[sum_i hc_i ec_i^T] in the q basis, for the SNR attestation."""
        k = self.kappa
        s = self.code_second
        q = q.to(torch.float64)
        u0 = self.u0.to(torch.float64)
        ue_w = (self.ue @ self.wc).to(torch.float64)   # rows are code @ (ue @ wc)^T
        a = (q.mT @ self.uh.to(torch.float64)) * math.sqrt(1.0 - k) + (q.mT @ u0) * math.sqrt(k)
        b = (q.mT @ ue_w) * math.sqrt(1.0 - k) + (q.mT @ u0) * math.sqrt(k)
        return (m_rows * self.h_scale * math.sqrt(1.0 - self.rho_h) * math.sqrt(1.0 - self.rho_e)
                * self.f_content * (a @ s @ b.mT))

    def snr(self, h_cal, e_cal, q):
        """Measured Frobenius signal-to-noise of the sample cross-covariance."""
        ah = ((h_cal - h_cal.mean(0, keepdim=True)) @ q).to(torch.float64)
        be = ((e_cal - e_cal.mean(0, keepdim=True)) @ q).to(torch.float64)
        obs = ah.mT @ be
        sig = self.expected_cross_cov(q, h_cal.shape[0])
        noise = obs - sig
        return {"snr_fro": float(sig.norm() / max(noise.norm(), 1e-30)),
                "signal_fro": float(sig.norm()), "noise_fro": float(noise.norm()),
                "signal_top_sv": float(torch.linalg.svdvals(sig)[0]),
                "noise_top_sv": float(torch.linalg.svdvals(noise)[0])}

    def _draw(self, n, independent):
        self._draws += 1
        gen = torch.Generator().manual_seed(30_000 + 977 * self.seed + self._draws)
        code_h = torch.randn(n, self.content_rank, generator=gen) * self.spec
        independent = independent or not self.shared
        code_e = torch.randn(n, self.content_rank, generator=gen) * self.spec if independent else code_h
        uh_rows, _ = self._content_dirs(code_h)
        _, ue_rows = self._content_dirs(code_e)
        vh = cm.unit_rows(torch.randn(n, self.dim, generator=gen))
        ve = cm.unit_rows(torch.randn(n, self.dim, generator=gen))
        f, g = self.f_content, 1.0 - self.f_content
        base_h = (math.sqrt(self.rho_h) * self.mh
                  + math.sqrt(1.0 - self.rho_h) * (math.sqrt(f) * uh_rows + math.sqrt(g) * vh))
        base_e = (math.sqrt(self.rho_e) * self.me
                  + math.sqrt(1.0 - self.rho_e) * (math.sqrt(f) * ue_rows + math.sqrt(g) * ve))
        nf_h = torch.exp(self.norm_sigma * torch.randn(n, 1, generator=gen) - 0.5 * self.norm_sigma ** 2)
        nf_e = torch.exp(self.norm_sigma * torch.randn(n, 1, generator=gen) - 0.5 * self.norm_sigma ** 2)
        h = (base_h * nf_h * self.h_scale).contiguous()
        e = (base_e * nf_e).contiguous()
        pred = (math.sqrt(self.rho_e) * self.me
                + (torch.zeros_like(ue_rows) if independent
                   else math.sqrt(1.0 - self.rho_e) * math.sqrt(f) * ue_rows))
        return h, e, {"pred": cm.unit_rows(pred).contiguous()}

    def msg(self, n):
        return self._draw(n, independent=False)

    def cal(self, n):
        return self._draw(n, independent=False)

    def probe(self, n):
        return self._draw(n, independent=False)

    def background(self, n):
        return self._draw(n, independent=True)

    def oracle_rotation(self, q):
        """Orthogonal Procrustes of the exact content map, expressed in the q basis.

        The exact content map is L = left right^T with orthonormal-column factors, so
        L is a partial isometry: orthogonal on span(right). METHOD C's model class only
        admits an ambient orthogonal R, so the fair ceiling is the polar factor of the
        q-projected map, which is what an oracle would hand to C.
        """
        k = self.kappa
        left = math.sqrt(1.0 - k) * (self.ue @ self.wc) + math.sqrt(k) * self.u0
        right = math.sqrt(1.0 - k) * self.uh + math.sqrt(k) * self.u0
        # exact row map: h_content = c right^T, e_content = c left^T, so e_row = h_row @ W
        # with W = right (right^T right)^-1 left^T, which needs no orthonormality of `right`.
        r64, l64 = right.to(torch.float64), left.to(torch.float64)
        m = r64 @ torch.linalg.pinv(r64.mT @ r64) @ l64.mT
        mq = q.mT.to(torch.float64) @ m @ q.to(torch.float64)
        u, _, v = torch.linalg.svd(mq, full_matrices=False)
        return (u @ v).to(torch.float32)


# --------------------------------------------------------------------------- #
# metrics
# --------------------------------------------------------------------------- #
def _safe_norm(x):
    return float(x.norm()) if torch.isfinite(x).all() else float("nan")


def cross_cosine(a, b):
    """(matched mean cosine, unmatched mean cosine) for two aligned row sets."""
    sim = cm.unit_rows(a) @ cm.unit_rows(b).mT
    n = a.shape[0]
    matched = float(sim.diagonal().mean())
    off = float((sim.sum() - sim.diagonal().sum()) / (n * (n - 1)))
    return matched, off


def evaluate(z, z_probe, z_shuf, pack):
    """All behavioural metrics for one (method, configuration)."""
    e_ref = pack["e_msg_normcal"]
    e_probe_ref = pack["e_probe_normcal"]
    target = pack["target_norm"]
    n, d = z.shape
    finite = bool(torch.isfinite(z).all()) and bool(torch.isfinite(z_probe).all()) and bool(torch.isfinite(z_shuf).all())
    nonfinite = int((~torch.isfinite(z)).sum()) + int((~torch.isfinite(z_probe)).sum()) + int((~torch.isfinite(z_shuf)).sum())
    zero_state = float(z.norm()) <= 1e-12

    out = {"n": n, "d": d, "nonfinite": nonfinite, "zero_state": zero_state}
    if zero_state or not finite:
        out.update({"rel_recon": float("nan"), "rel_recon_dir": float("nan"),
                    "mean_row_cos": float("nan"), "swap_rel_change": float("nan"),
                    "swap_fid_cos": float("nan"), "shuffle_rel_change": float("nan"),
                    "retrieval_top1": 0.0, "norm_mean_over_target": 0.0,
                    "norm_sd_over_target": 0.0})
        return out

    diff = z - e_ref
    out["rel_recon"] = _safe_norm(diff) / _safe_norm(e_ref)
    zu, eu = cm.unit_rows(z), cm.unit_rows(e_ref)
    out["rel_recon_dir"] = _safe_norm(zu - eu) / _safe_norm(eu)
    out["mean_row_cos"] = float((zu * eu).sum(dim=-1).mean())

    delta = z_probe - z
    out["swap_rel_change"] = _safe_norm(delta) / _safe_norm(z)
    dref = e_probe_ref - e_ref
    out["swap_fid_cos"] = float((delta.flatten() @ dref.flatten())
                                / max(_safe_norm(delta) * _safe_norm(dref), 1e-30))
    out["shuffle_rel_change"] = _safe_norm(z_shuf - z) / _safe_norm(z)

    vocab = pack["vocab"]
    sim = cm.unit_rows(z) @ cm.unit_rows(vocab).mT
    hits = sim.argmax(dim=-1) == torch.arange(n)
    out["retrieval_top1"] = float(hits.float().mean())
    out["retrieval_chance"] = 1.0 / float(vocab.shape[0])

    norms = z.norm(dim=-1)
    out["norm_mean_over_target"] = float(norms.mean() / target)
    out["norm_sd_over_target"] = float(norms.std() / target)
    out["norm_p05_over_target"] = float(torch.quantile(norms, 0.05) / target)
    out["norm_p95_over_target"] = float(torch.quantile(norms, 0.95) / target)
    return out


# --------------------------------------------------------------------------- #
# cost accounting
# --------------------------------------------------------------------------- #
def _flops_svd(m, n, rank=None, niter=4):
    if rank is None:
        return 4 * min(m, n) ** 2 * max(m, n)
    return 2 * m * n * rank * (2 * niter + 3)


def _frozen_bytes(method, n_fit, rank, dim):
    """Bytes the frozen artifact must keep resident (float32)."""
    if method in ("A", "B", "C", "G"):
        return 4 * (2 * dim + dim * rank + rank * rank + 1)
    if method == "D":
        return 4 * (2 * dim + 2 * n_fit * dim + n_fit * n_fit)
    if method == "I":
        return 4 * (2 * dim + 1)
    return 0


def cost_record(method, params, n_msg, dim, niter):
    """Cost of one fit plus one apply, in the conventions stated in the reports."""
    n_fit = params["n_fit"]
    rank = params["rank"]
    rec = {"n_fit": n_fit, "rank": rank, "basis_mode": params["basis_mode"],
           "shapes": params["cost"], "frozen_bytes": _frozen_bytes(method, n_fit, rank, dim)}
    if method in ("A", "B", "G"):
        base = _flops_svd(2 * n_fit, dim, None, niter) if method == "A" else \
            _flops_svd(2 * n_fit, dim, rank, niter)
        apply_flops = 2 * n_msg * dim * rank + 2 * n_msg * rank * rank
        extra = 2 * n_fit * dim * rank
    elif method == "C":
        base = _flops_svd(2 * n_fit, dim, rank, niter)
        apply_flops = 2 * n_msg * dim * rank
        extra = 2 * n_fit * dim * rank + 4 * n_fit * dim
    elif method == "D":
        base = 2 * n_fit * n_fit * dim
        apply_flops = 2 * n_msg * n_fit * dim
        extra = n_fit ** 3 / 3.0 + 2 * n_fit * n_fit * dim
    else:
        base = apply_flops = extra = 0.0
    rec["svd_flops"] = base
    rec["extra_flops"] = extra
    rec["apply_flops"] = apply_flops
    rec["flops"] = base + extra + apply_flops
    return rec


# --------------------------------------------------------------------------- #
# one configuration
# --------------------------------------------------------------------------- #
def run_config(*, scenario, shared, f_content, kappa, seed, m_values, dim, content_rank,
               spec_exp, target_pair, norm_sigma, rank_cap, lambdas, n_msg, n_bg,
               niter, methods, ridge=1e-3):
    world = World(dim=dim, content_rank=content_rank, spec_exp=spec_exp,
                  target_pair=target_pair, f_content=f_content, shared=shared,
                  seed=seed, norm_sigma=norm_sigma, kappa=kappa)
    h_msg, e_msg, msg_extra = world.msg(n_msg)
    h_probe, e_probe, probe_extra = world.probe(n_msg)
    h_cal_full, e_cal_full, _ = world.cal(max(m_values))
    bg = world.background(n_bg)[1]
    vocab = torch.cat((e_msg, bg), 0).contiguous()
    target_norm = float(vocab.norm(dim=-1).mean())
    e_msg_n = cm.renorm_rows(e_msg, target_norm)
    e_probe_n = cm.renorm_rows(e_probe, target_norm)
    pack = {"e_msg_normcal": e_msg_n, "e_probe_normcal": e_probe_n,
            "vocab": vocab, "target_norm": target_norm}

    matched, unmatched = cross_cosine(h_msg, e_msg)
    att = {"scenario": scenario, "seed": seed, "dim": dim, "n_msg": n_msg,
           "content_rank": content_rank, "spec_exp": spec_exp, "f_content": f_content,
           "kappa": kappa, "shared": shared, "target_pair_cos": target_pair,
           "rho_h": world.rho_h, "rho_e": world.rho_e,
           "pair_cos_msg_h": cm.mean_pair_cosine(h_msg),
           "pair_cos_msg_e": cm.mean_pair_cosine(e_msg),
           "pair_cos_probe_h": cm.mean_pair_cosine(h_probe),
           "pair_cos_probe_e": cm.mean_pair_cosine(e_probe),
           "cross_cos_matched": matched, "cross_cos_unmatched": unmatched,
           "target_norm": target_norm, "vocab_size": int(vocab.shape[0]),
           "spectrum_msg_h": cm.spectrum_report(h_msg),
           "spectrum_msg_e": cm.spectrum_report(e_msg), "cal": []}

    rows = []
    perm = torch.randperm(n_msg, generator=torch.Generator().manual_seed(90_000 + seed))
    h_perm = h_msg[perm]

    def record(method, params, fit_rows_note):
        """Evaluate one method. METHOD A is refitted per hidden-row set: it never freezes."""
        t0 = time.time()
        if method == "A":
            z = cm.apply_production(params, h_msg)
            z_probe = cm.apply_production(cm.fit_production(h_probe, e_msg), h_probe)
            z_shuf = cm.apply_production(cm.fit_production(h_perm, e_msg), h_perm)
        else:
            apply_fn = {"B": cm.apply_whitened_procrustes, "C": cm.apply_orthogonal_procrustes,
                        "G": cm.apply_orthogonal_procrustes, "D": cm.apply_dual_ridge,
                        "F": cm.apply_direct_reference, "K": cm.apply_null_zero,
                        "O": cm.apply_null_zero, "I": cm.apply_affine_identity}[method]
            if method == "O":
                z = params["pred_msg"] * target_norm
                z_probe = params["pred_probe"] * target_norm
                z_shuf = params["pred_msg"][perm] * target_norm
            else:
                z, z_probe, z_shuf = (apply_fn(params, h_msg), apply_fn(params, h_probe),
                                      apply_fn(params, h_perm))
        cost = cost_record(method, params, n_msg, dim, niter) if method not in ("F", "K", "O", "I") else {
            "n_fit": 0, "rank": 0, "basis_mode": params["basis_mode"], "shapes": {},
            "flops": 0, "svd_flops": 0, "extra_flops": 0, "apply_flops": 0, "frozen_bytes": 0}
        primary = evaluate(z, z_probe, z_shuf, pack)
        renormed = evaluate(cm.renorm_rows(z, target_norm), cm.renorm_rows(z_probe, target_norm),
                            cm.renorm_rows(z_shuf, target_norm), pack)
        rows.append({"scenario": scenario, "seed": seed, "method": method,
                     "M": fit_rows_note, "lam_rel": params.get("lam_rel"),
                     "rank": params["rank"], "basis_mode": params["basis_mode"],
                     "energy_captured": params.get("energy"),
                     "calib_rel_residual": params.get("calib_rel_residual"),
                     "scale": params.get("scale"),
                     "metrics": primary, "metrics_renorm": renormed,
                     "cost": cost, "seconds": time.time() - t0})

    if "A" in methods:
        record("A", cm.fit_production(h_msg, e_msg), m_values[0])
    if "F" in methods:
        record("F", cm.fit_direct_reference(e_msg), m_values[0])
    if "K" in methods:
        record("K", cm.fit_null_zero(), m_values[0])
    if "I" in methods:
        record("I", cm.fit_affine_identity(h_msg, e_msg), m_values[0])
    if "O" in methods:
        record("O", {"method": "O_oracle_linear", "n_fit": 0, "rank": 0,
                     "basis_mode": "oracle", "cost": {},
                     "pred_msg": cm.unit_rows(msg_extra["pred"]),
                     "pred_probe": cm.unit_rows(probe_extra["pred"])}, m_values[0])

    for m in m_values:
        h_cal, e_cal = h_cal_full[:m].contiguous(), e_cal_full[:m].contiguous()
        cal_att = {"M": m,
                   "pair_cos_cal_h": cm.mean_pair_cosine(h_cal),
                   "pair_cos_cal_e": cm.mean_pair_cosine(e_cal),
                   "spectrum_cal_h": cm.spectrum_report(h_cal),
                   "spectrum_cal_e": cm.spectrum_report(e_cal)}
        if "B" in methods:
            params_b = cm.fit_whitened_procrustes(h_cal, e_cal, rank_cap=rank_cap,
                                                  niter=niter, ridge=ridge)
            cal_att["snr"] = world.snr(h_cal, e_cal, params_b["q"])
            cal_att["rank"] = params_b["rank"]
            cal_att["energy_captured"] = params_b["energy"]
            record("B", params_b, m)
        if "C" in methods or "G" in methods:
            params_c = cm.fit_orthogonal_procrustes(h_cal, e_cal, rank_cap=rank_cap, niter=niter)
            cal_att.setdefault("rank", params_c["rank"])
            if "C" in methods:
                record("C", params_c, m)
            if "G" in methods:
                q, rank = params_c["q"], params_c["rank"]
                rot_r = world.oracle_rotation(q)
                a_cal = h_cal - h_cal.mean(0, keepdim=True)
                mapped = a_cal + (((a_cal @ q) @ (rot_r - torch.eye(rank)).to(torch.float32)) @ q.mT)
                be = (e_cal - e_cal.mean(0, keepdim=True)).to(torch.float64)
                num = float((mapped.to(torch.float64) * be).sum())
                den = float((mapped.to(torch.float64) ** 2).sum())
                scale = num / den if den > 0 else 0.0
                params_g = dict(params_c)
                params_g.update({"method": "G_oracle_orthogonal",
                                 "rot_minus_i": (rot_r - torch.eye(rank)).to(torch.float32),
                                 "scale": scale,
                                 "calib_rel_residual": float((mapped * scale - be).norm() / be.norm())})
                record("G", params_g, m)
            del params_c
        if "D" in methods:
            for lam in lambdas:
                record("D", cm.fit_dual_ridge(h_cal, e_cal, lam_rel=lam), m)
        att["cal"].append(cal_att)
    del h_cal_full, e_cal_full, vocab, bg
    return rows, att


# --------------------------------------------------------------------------- #
# markdown tables
# --------------------------------------------------------------------------- #
def _f(x, nd=4):
    if x is None:
        return "n/a"
    if isinstance(x, float) and math.isnan(x):
        return "nan"
    if isinstance(x, float):
        return f"{x:.{nd}f}"
    return str(x)


def write_tables(rows, atts, out_dir, command):
    os.makedirs(out_dir, exist_ok=True)
    scenarios = []
    for r in rows:
        if r["scenario"] not in scenarios:
            scenarios.append(r["scenario"])

    def emit(name, title, header, body):
        with open(os.path.join(out_dir, name), "w", encoding="utf-8", newline="\n") as f:
            f.write(f"# {title}\n\n")
            f.write(f"Command: `{command}`\n\n")
            f.write("| " + " | ".join(header) + " |\n")
            f.write("|" + "|".join(["---"] * len(header)) + "|\n")
            for line in body:
                f.write("| " + " | ".join(str(v) for v in line) + " |\n")
            f.write("\n")

    body = []
    for a in atts:
        cal = a["cal"][-1]
        body.append([a["scenario"], a["seed"], _f(a["f_content"], 2), _f(a["kappa"], 2),
                     _f(a["pair_cos_msg_h"]), _f(a["pair_cos_msg_e"]),
                     _f(cal["pair_cos_cal_h"]), _f(cal["pair_cos_cal_e"]),
                     _f(a["cross_cos_matched"]), _f(a["cross_cos_unmatched"]),
                     a["spectrum_msg_h"]["effective_rank_99"],
                     _f(a["spectrum_msg_h"]["participation"], 1),
                     _f(cal["snr"]["snr_fro"], 2), _f(a["target_norm"], 3)])
    emit("table_fixture.md", "Fixture attestation (pairwise cosine 0.24; cross-covariance SNR at max M)",
         ["scenario", "seed", "f", "kappa", "pair_cos_msg_h", "pair_cos_msg_e",
          "pair_cos_cal_h", "pair_cos_cal_e", "cos_matched_he", "cos_unmatched_he",
          "eff_rank99_h", "participation_h", "snr_crosscov", "target_norm"], body)

    body = []
    for a in atts:
        for c in a["cal"]:
            body.append([a["scenario"], a["seed"], c["M"], c["rank"],
                         _f(c["energy_captured"], 3), _f(c["snr"]["snr_fro"], 2),
                         _f(c["snr"]["signal_top_sv"], 1), _f(c["snr"]["noise_top_sv"], 1),
                         c["spectrum_cal_h"]["effective_rank_99"]])
    emit("table_snr.md", "Calibration-set cross-covariance SNR per M (measured)",
         ["scenario", "seed", "M", "basis_rank", "energy_captured", "snr_fro",
          "signal_top_sv", "noise_top_sv", "eff_rank99_cal_h"], body)

    for scenario in scenarios:
        body = []
        for method in METHOD_ORDER:
            for r in rows:
                if r["scenario"] != scenario or r["method"] != method:
                    continue
                m = r["metrics"]
                body.append([method, r["M"], r["seed"], _f(m["rel_recon"]),
                             _f(m["rel_recon_dir"]), _f(m["mean_row_cos"]),
                             _f(m["swap_rel_change"]), _f(m["swap_fid_cos"]),
                             _f(m["shuffle_rel_change"]), _f(m["retrieval_top1"], 3),
                             _f(m["norm_mean_over_target"], 3),
                             _f(m["norm_sd_over_target"], 3), m["nonfinite"]])
        emit(f"table_main_{scenario}.md", f"Behavioural metrics: scenario {scenario}",
             ["method", "M", "seed", "rel_recon", "rel_recon_dir", "mean_row_cos",
              "swap_rel_change", "swap_fid_cos", "shuffle_rel_change", "retrieval_top1",
              "norm_mean/target", "norm_sd/target", "nonfinite"], body)

    body = []
    for r in rows:
        m = r["metrics_renorm"]
        body.append([r["scenario"], r["method"], r["M"], _f(m["rel_recon"]),
                     _f(m["rel_recon_dir"]), _f(m["mean_row_cos"]),
                     _f(m["retrieval_top1"], 3), _f(m["norm_sd_over_target"], 3)])
    emit("table_renorm.md", "Production-parity variant: every output row rescaled to the scalar target norm",
         ["scenario", "method", "M", "rel_recon", "rel_recon_dir", "mean_row_cos",
          "retrieval_top1", "norm_sd/target"], body)

    body = []
    for r in rows:
        c = r["cost"]
        svd = c["shapes"].get("svd_shape")
        chol = c["shapes"].get("chol")
        body.append([r["method"], r["M"], c["n_fit"], c["rank"], c["basis_mode"],
                     "none" if not svd else f"{svd[0]}x{svd[1]}",
                     "none" if not chol else f"{chol[0]}x{chol[1]}",
                     _f(r.get("scale"), 4) if r.get("scale") is not None else "n/a",
                     f"{c['flops']/1e9:.3f}", f"{c['frozen_bytes']/2**20:.2f}"])
    emit("table_cost.md", "Cost of each candidate (matrix sizes solved, float32 resident artifact)",
         ["method", "M", "n_fit", "rank", "basis", "svd_shape", "chol_shape", "scale",
          "flops_est_1e9", "frozen_MiB"], body)

    agg = {}
    for r in rows:
        agg.setdefault((r["scenario"], r["method"], r["M"]), []).append(r)
    body = []
    for (scenario, method, m), rs in agg.items():
        def mean(key):
            vals = [x["metrics"][key] for x in rs if not math.isnan(x["metrics"][key])]
            return sum(vals) / len(vals) if vals else float("nan")
        body.append([scenario, method, m, _f(mean("swap_rel_change")),
                     _f(mean("rel_recon")), _f(mean("rel_recon_dir")),
                     _f(mean("mean_row_cos")), _f(mean("swap_fid_cos")),
                     _f(mean("retrieval_top1"), 3)])
    emit("table_tradeoff.md", "Trade-off: hidden-state dependence versus manifold landing (mean over seeds)",
         ["scenario", "method", "M", "swap_rel_change", "rel_recon", "rel_recon_dir",
          "mean_row_cos", "swap_fid_cos", "retrieval_top1"], body)
    return sorted(os.listdir(out_dir))


# --------------------------------------------------------------------------- #
# main
# --------------------------------------------------------------------------- #
def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="calibration_results.json")
    ap.add_argument("--tables", default="tables")
    ap.add_argument("--dim", type=int, default=6144)
    ap.add_argument("--content-rank", type=int, default=64)
    ap.add_argument("--spec-exp", type=float, default=0.7)
    ap.add_argument("--target-pair", type=float, default=0.24)
    ap.add_argument("--norm-sigma", type=float, default=0.15)
    ap.add_argument("--kappa", type=float, default=0.5)
    ap.add_argument("--n-msg", type=int, default=128)
    ap.add_argument("--n-bg", type=int, default=4096)
    ap.add_argument("--ms", default="128,512,2048")
    ap.add_argument("--seeds", default="0,1,2")
    ap.add_argument("--scenarios", default="null,weak,mid,strong")
    ap.add_argument("--rank-cap", type=int, default=256)
    ap.add_argument("--niter", type=int, default=4)
    ap.add_argument("--ridge", type=float, default=1e-3)
    ap.add_argument("--lambdas", default="1e-4,1e-3,1e-2")
    ap.add_argument("--methods", default="A,B,C,D,F,K,O,G,I")
    ap.add_argument("--threads", type=int, default=0)
    ap.add_argument("--no-tables", action="store_true")
    ap.add_argument("--label", default="")
    args = ap.parse_args(argv)
    if args.threads:
        torch.set_num_threads(args.threads)

    m_values = [int(x) for x in args.ms.split(",") if x]
    seeds = [int(x) for x in args.seeds.split(",") if x]
    methods = [x for x in args.methods.split(",") if x]
    lambdas = [float(x) for x in args.lambdas.split(",") if x]
    wanted = [x for x in args.scenarios.split(",") if x]
    scenarios = [s for s in DEFAULT_SCENARIOS if s[0] in wanted]

    command = "python reports/occ3-alignment-repair/analysis/calibration_experiment.py " + " ".join(argv)
    rows, atts = [], []
    t0 = time.time()
    for scenario, shared, f_content, kappa_default in scenarios:
        kappa = args.kappa if args.kappa >= 0 else kappa_default
        for seed in seeds:
            ts = time.time()
            r, a = run_config(scenario=scenario, shared=shared, f_content=f_content,
                              kappa=kappa, seed=seed, m_values=m_values, dim=args.dim,
                              content_rank=args.content_rank, spec_exp=args.spec_exp,
                              target_pair=args.target_pair, norm_sigma=args.norm_sigma,
                              rank_cap=args.rank_cap, lambdas=lambdas, n_msg=args.n_msg,
                              n_bg=args.n_bg, niter=args.niter, methods=methods,
                              ridge=args.ridge)
            rows.extend(r)
            atts.append(a)
            print(f"[{scenario} kappa={kappa} seed={seed}] {len(r)} rows in {time.time()-ts:.1f}s", flush=True)
    payload = {"label": args.label, "command": command, "argv": argv,
               "elapsed_s": time.time() - t0, "torch": torch.__version__,
               "threads": torch.get_num_threads(),
               "config": {"dim": args.dim, "content_rank": args.content_rank,
                          "spec_exp": args.spec_exp, "target_pair": args.target_pair,
                          "norm_sigma": args.norm_sigma, "kappa": args.kappa,
                          "n_msg": args.n_msg, "n_bg": args.n_bg, "m_values": m_values,
                          "seeds": seeds, "rank_cap": args.rank_cap, "lambdas": lambdas,
                          "niter": args.niter, "ridge": args.ridge, "methods": methods},
               "fixture": atts, "results": rows}
    with open(args.out, "w", encoding="utf-8", newline="\n") as f:
        json.dump(payload, f, indent=1)
        f.write("\n")
    print(f"wrote {args.out} ({len(rows)} rows) in {payload['elapsed_s']:.1f}s", flush=True)
    if not args.no_tables:
        print("tables:", write_tables(rows, atts, args.tables, command), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
