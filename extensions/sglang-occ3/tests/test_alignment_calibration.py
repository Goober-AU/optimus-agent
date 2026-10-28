"""Offline unit tests for the calibration-alignment candidates.

Under test: ``reports/occ3-alignment-repair/analysis/calibration_methods.py``
(METHOD A production operator, B frozen whitened Procrustes, C affine orthogonal
Procrustes, D dual ridge, controls F and K) and the metric layer of
``calibration_experiment.py``.

Nothing here imports or patches production code: the production solver is used only
as a reference value in the parity test. No GPU, no SGLang, no network.
"""
from __future__ import annotations

import importlib.util
import math
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
ANALYSIS = REPO / "reports" / "occ3-alignment-repair" / "analysis"


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None, f"missing {path}"
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def cm():
    if importlib.util.find_spec("torch") is None:
        pytest.skip("torch not installed")
    if not (ANALYSIS / "calibration_methods.py").exists():
        pytest.skip("offline analysis module not present")
    return _load("occ3_calibration_methods_under_test", ANALYSIS / "calibration_methods.py")


@pytest.fixture(scope="module")
def cx(cm):
    if not (ANALYSIS / "calibration_experiment.py").exists():
        pytest.skip("offline experiment module not present")
    return _load("occ3_calibration_experiment_under_test", ANALYSIS / "calibration_experiment.py")


def _rotation(dim, seed):
    import torch
    g = torch.Generator().manual_seed(seed)
    q, r = torch.linalg.qr(torch.randn(dim, dim, generator=g))
    return q * torch.sign(torch.diagonal(r)).unsqueeze(0)


# --------------------------------------------------------------------------- #
# METHOD A: the harness copy must be the production operator
# --------------------------------------------------------------------------- #
def test_production_operator_matches_reference_solver(cm):
    import torch
    from occ3_sglang.statebridge import align_hidden_sequence
    torch.set_grad_enabled(False)
    g = torch.Generator().manual_seed(23)
    for n, d in ((8, 64), (32, 512), (128, 6144)):
        h = torch.randn(n, d, generator=g)
        e = torch.randn(n, d, generator=g)
        ref, _ = align_hidden_sequence(h, e, 3.0)
        params = cm.fit_production(h, e)
        got = cm.renorm_rows(cm.apply_production(params, h), 3.0)
        assert float((got - ref).abs().max()) < 5e-5, (n, d)
        assert float(got.norm(dim=-1).min()) == pytest.approx(3.0, rel=1e-4)


def test_production_in_sample_output_tracks_target_rows(cm):
    """METHOD A is an in-sample interpolator: its output equals the target rows.

    This is the property that makes in-sample METHOD A numbers meaningless as a
    measure of generalisation, so it is pinned by a test.
    """
    import torch
    torch.set_grad_enabled(False)
    g = torch.Generator().manual_seed(29)
    n, d = 16, 256
    h = torch.randn(n, d, generator=g)
    e = torch.randn(n, d, generator=g)
    z = cm.renorm_rows(cm.apply_production(cm.fit_production(h, e), h), 1.0)
    en = cm.renorm_rows(e, 1.0)
    assert float((cm.unit_rows(z) * cm.unit_rows(en)).sum(-1).mean()) > 0.999
    # the same operator fitted to unrelated hidden rows still returns the target rows
    h2 = torch.randn(n, d, generator=g)
    z2 = cm.renorm_rows(cm.apply_production(cm.fit_production(h2, e), h2), 1.0)
    assert float((z2 - z).norm() / z.norm()) < 0.05


# --------------------------------------------------------------------------- #
# METHOD B: frozen whitened Procrustes
# --------------------------------------------------------------------------- #
def test_frozen_whitened_equals_production_on_the_fit_rows(cm):
    """With no basis truncation B is the production algebra, so fit+apply is identical."""
    import torch
    torch.set_grad_enabled(False)
    g = torch.Generator().manual_seed(31)
    n, d = 24, 192
    h = torch.randn(n, d, generator=g)
    e = torch.randn(n, d, generator=g)
    frozen = cm.fit_whitened_procrustes(h, e, rank_cap=None)
    prod = cm.fit_production(h, e)
    assert frozen["rank"] == prod["rank"]
    a = cm.apply_whitened_procrustes(frozen, h)
    b = cm.apply_production(prod, h)
    assert float((a - b).abs().max()) < 1e-4 * max(float(b.abs().max()), 1.0)


def test_frozen_whitened_transfers_a_scale_only_relation(cm):
    """B transfers when the relation lives inside the retained basis.

    Hidden energy sits in a 48-dim subspace of a 128-dim space, e = 0.25 * h, and the
    basis keeps 64 directions, so the frozen map sees the whole relation. Held-out
    rows must then reproduce the scale.
    """
    import torch
    torch.set_grad_enabled(False)
    g = torch.Generator().manual_seed(37)
    d, r, m, n = 128, 48, 256, 32
    basis = torch.linalg.qr(torch.randn(d, r, generator=g))[0]
    h = (torch.randn(m, r, generator=g) @ basis.mT) * 3.0
    e = 0.25 * h
    params = cm.fit_whitened_procrustes(h, e, rank_cap=64)
    assert params["rank"] == 64
    hn = (torch.randn(n, r, generator=g) @ basis.mT) * 3.0
    en = 0.25 * hn
    zn = cm.apply_whitened_procrustes(params, hn)
    # the 1e-3 ridge in the whitening leaves a small systematic bias; 5e-3 is float32-safe
    assert float((zn - en).norm() / en.norm()) < 5e-3


def test_frozen_whitened_discards_energy_outside_the_retained_basis(cm):
    """The frozen map is rank-limited: centred rows orthogonal to span(q) map to mu_E."""
    import torch
    torch.set_grad_enabled(False)
    g = torch.Generator().manual_seed(38)
    d, r, m = 128, 48, 256
    basis = torch.linalg.qr(torch.randn(d, r, generator=g))[0]
    h = torch.randn(m, r, generator=g) @ basis.mT
    params = cm.fit_whitened_procrustes(h, h, rank_cap=64)
    q = params["q"]
    v = torch.randn(8, d, generator=g)
    v = v - (v @ q) @ q.mT                       # v is orthogonal to the retained basis
    x = params["mu_h"] + v                       # so x - mu_h is orthogonal to span(q)
    z = cm.apply_whitened_procrustes(params, x)
    assert float((z - params["mu_e"]).norm() / v.norm()) < 1e-6


# --------------------------------------------------------------------------- #
# METHOD C: affine orthogonal Procrustes
# --------------------------------------------------------------------------- #
def test_orthogonal_procrustes_recovers_a_known_subspace_rotation(cm):
    import torch
    torch.set_grad_enabled(False)
    g = torch.Generator().manual_seed(43)
    d, r = 96, 32
    basis = torch.linalg.qr(torch.randn(d, r, generator=g))[0]
    rot_r = _rotation(r, 44)
    rot = torch.eye(d) + basis @ (rot_r - torch.eye(r)) @ basis.mT
    h = torch.randn(400, r, generator=g) @ basis.mT
    e = h @ rot
    params = cm.fit_orthogonal_procrustes(h, e, rank_cap=64)
    assert params["scale"] == pytest.approx(1.0, abs=1e-4)
    assert params["calib_rel_residual"] < 1e-4
    hn = torch.randn(16, r, generator=g) @ basis.mT
    en = hn @ rot
    zn = cm.apply_orthogonal_procrustes(params, hn)
    assert float((zn - en).norm() / en.norm()) < 1e-4


def test_orthogonal_procrustes_ambient_map_is_orthogonal(cm):
    import torch
    torch.set_grad_enabled(False)
    g = torch.Generator().manual_seed(47)
    d, m = 64, 40
    h = torch.randn(m, d, generator=g) * (1.0 + torch.arange(d)) / d
    e = torch.randn(m, d, generator=g)
    params = cm.fit_orthogonal_procrustes(h, e, rank_cap=32)
    q, rank = params["q"], params["rank"]
    rot = params["rot_minus_i"] + torch.eye(rank)
    ambient = torch.eye(d) + q @ (rot - torch.eye(rank)) @ q.mT
    eye = ambient.mT @ ambient - torch.eye(d)
    assert float(eye.abs().max()) < 1e-4
    assert float((params["rot_r"].mT @ params["rot_r"] - torch.eye(rank, dtype=torch.float64)).abs().max()) < 1e-6


def test_orthogonal_procrustes_scale_minimises_the_calibration_residual(cm):
    import torch
    torch.set_grad_enabled(False)
    g = torch.Generator().manual_seed(53)
    d, m = 64, 120
    h = torch.randn(m, d, generator=g)
    e = 0.6 * (torch.randn(m, d, generator=g))
    params = cm.fit_orthogonal_procrustes(h, e, rank_cap=16)
    target = e - params["mu_e"]

    def residual(scale):
        a = h - params["mu_h"]
        mapped = a + ((a @ params["q"]) @ params["rot_minus_i"]) @ params["q"].mT
        return float((mapped * scale - target).norm())

    best = residual(params["scale"])
    assert best < residual(params["scale"] * 0.5)
    assert best < residual(params["scale"] * 2.0)


# --------------------------------------------------------------------------- #
# METHOD D: dual ridge
# --------------------------------------------------------------------------- #
def test_dual_ridge_matches_the_explicit_primal_solution(cm):
    import torch
    torch.set_grad_enabled(False)
    g = torch.Generator().manual_seed(59)
    d, m, n = 48, 40, 7
    h = torch.randn(m, d, generator=g)
    e = torch.randn(m, d, generator=g)
    params = cm.fit_dual_ridge(h, e, lam_rel=1e-3)
    a = (h - h.mean(0, keepdim=True)).double()
    b = (e - e.mean(0, keepdim=True)).double()
    kernel = a @ a.mT + params["lam"] * torch.eye(m, dtype=torch.float64)
    primal_map = a.mT @ torch.linalg.solve(kernel, b)
    hn = torch.randn(n, d, generator=g)
    want = (hn - h.mean(0, keepdim=True)).double() @ primal_map + e.mean(0, keepdim=True).double()
    got = cm.apply_dual_ridge(params, hn).double()
    assert float((got - want).abs().max()) < 1e-3 * float(want.abs().max())


def test_dual_ridge_recovers_a_known_linear_map(cm):
    import torch
    torch.set_grad_enabled(False)
    g = torch.Generator().manual_seed(61)
    d, m = 64, 400
    w = torch.randn(d, d, generator=g) / math.sqrt(d)
    b = 0.3 * torch.randn(1, d, generator=g)
    h = torch.randn(m, d, generator=g)
    e = h @ w + b
    params = cm.fit_dual_ridge(h, e, lam_rel=1e-8)
    hn = torch.randn(24, d, generator=g)
    en = hn @ w + b
    got = cm.apply_dual_ridge(params, hn)
    assert float((got - en).norm() / en.norm()) < 1e-2


def test_dual_ridge_never_returns_non_finite(cm):
    import torch
    torch.set_grad_enabled(False)
    g = torch.Generator().manual_seed(67)
    d, m = 32, 200
    h = torch.randn(m, d, generator=g) * 50.0
    e = torch.randn(m, d, generator=g)
    params = cm.fit_dual_ridge(h, e, lam_rel=1e-2)
    assert bool(torch.isfinite(params["coeff"]).all())
    assert bool(torch.isfinite(cm.apply_dual_ridge(params, torch.randn(5, d, generator=g))).all())


# --------------------------------------------------------------------------- #
# controls and metric layer
# --------------------------------------------------------------------------- #
def test_controls_return_reference_rows_and_zero(cm):
    import torch
    torch.set_grad_enabled(False)
    g = torch.Generator().manual_seed(71)
    e = torch.randn(6, 32, generator=g)
    h = torch.randn(6, 32, generator=g)
    assert torch.equal(cm.apply_direct_reference(cm.fit_direct_reference(e), h), e)
    assert float(cm.apply_null_zero(cm.fit_null_zero(), h).abs().max()) == 0.0


def test_renorm_rows_hits_the_scalar_target(cm):
    import torch
    torch.set_grad_enabled(False)
    z = torch.randn(9, 16, generator=torch.Generator().manual_seed(73)) * 5.0
    out = cm.renorm_rows(z, 2.5)
    assert float(out.norm(dim=-1).min()) == pytest.approx(2.5, rel=1e-5)
    assert float(out.norm(dim=-1).max()) == pytest.approx(2.5, rel=1e-5)


def test_metric_layer_separates_target_copying_from_hidden_dependence(cx):
    """A map that copies the targets has zero swap sensitivity; the metric must show it."""
    import torch
    torch.set_grad_enabled(False)
    g = torch.Generator().manual_seed(79)
    n, d = 12, 32
    e_msg = torch.randn(n, d, generator=g)
    e_probe = torch.randn(n, d, generator=g)
    vocab = torch.cat((e_msg, torch.randn(64, d, generator=g)), 0)
    target = 1.0
    pack = {"e_msg_normcal": cm_eval_renorm(cx, e_msg, target),
            "e_probe_normcal": cm_eval_renorm(cx, e_probe, target),
            "vocab": vocab, "target_norm": target}
    z = e_msg.clone()
    # a map that copies the targets ignores its hidden input, so the swap change is 0
    copy = cx.evaluate(z, z.clone(), z.clone(), pack)
    assert copy["swap_rel_change"] == 0.0
    assert copy["shuffle_rel_change"] == 0.0
    assert copy["retrieval_top1"] == pytest.approx(1.0)
    # direction is exact, magnitude is not: raw target rows carry their own norms
    assert copy["rel_recon_dir"] < 1e-6
    assert copy["rel_recon"] > 0.0
    assert copy["norm_sd_over_target"] > 0.0
    zero = cx.evaluate(torch.zeros_like(z), torch.zeros_like(z), torch.zeros_like(z), pack)
    assert zero["zero_state"] is True and zero["nonfinite"] == 0
    assert math.isnan(zero["rel_recon"]) and zero["retrieval_top1"] == 0.0
    delta = 0.3 * torch.randn(n, d, generator=g)
    moved = cx.evaluate(z, z + delta, z, pack)
    assert moved["swap_rel_change"] == pytest.approx(float(delta.norm() / z.norm()), rel=1e-5)
    assert 0.0 <= moved["retrieval_top1"] <= 1.0


def cm_eval_renorm(cx, rows, target):
    return rows * (target / rows.norm(dim=-1, keepdim=True))
