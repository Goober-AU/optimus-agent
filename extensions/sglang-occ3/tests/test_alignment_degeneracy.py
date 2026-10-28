"""Degeneracy of the per-message whitened Procrustes fit (statebridge.py:32-70).

Each test encodes one claim that is derived with numbered equations in
reports/occ3-alignment-repair/MATHEMATICAL-DEGENERACY.md and measured, with the
production function, in
reports/occ3-alignment-repair/analysis/degeneracy_measurements.json.

Tolerances are the measured float32 values at N=128, D=6144 with the stated margin.
A claim that holds only in the lambda -> 0 limit is marked LIMIT and carries the
measured float32 floor in its comment instead of an exact equality.
"""
from __future__ import annotations

import math

import pytest

from occ3_sglang.statebridge import align_hidden_sequence
from occ3_sglang.latent_types import LatentError

N, DIM = 128, 6144
PAIR_COS = 0.24
DECAY = 0.6
FLOOR = 1e-6
RTOL = 1e-5


@pytest.fixture
def real_torch():
    """Actual Torch only: a stub cannot produce a numerical claim."""
    try:
        import importlib.util
        spec = importlib.util.find_spec("torch")
    except (ValueError, ImportError):
        spec = None
    if spec is None:
        pytest.skip("Actual Torch not installed; no numerical claim from stubs")
    import torch
    if not hasattr(torch, "linalg"):
        pytest.skip("Existing suite Torch stub is not numerical validation")
    return torch


# ---------------------------------------------------------------------------
# fixtures (deterministic; no network, no GPU, no model weights)
# ---------------------------------------------------------------------------
def _decay(dim=DIM, exponent=DECAY):
    import numpy as np
    var = np.arange(1, dim + 1, dtype=np.float64) ** (-exponent)
    return np.sqrt(var / var.mean())


def _rows(rng, count, family, scale, sd, direction):
    import numpy as np
    z = rng.standard_normal((count, DIM))
    if family == "isotropic":
        x = z
    else:
        base = z * sd
        base /= np.linalg.norm(base, axis=1, keepdims=True)
        x = math.sqrt(PAIR_COS) * direction + math.sqrt(1.0 - PAIR_COS) * base
    return x * (scale / np.linalg.norm(x, axis=1, keepdims=True))


def _givens(x, rng, pairs=96):
    """Orthogonal feature rotation: preserves the centred row Gram exactly."""
    import numpy as np
    y = np.array(x, dtype=np.float64, copy=True)
    for _ in range(pairs):
        i, j = rng.integers(0, y.shape[1], size=2)
        if i == j:
            continue
        th = rng.uniform(0.0, 2.0 * math.pi)
        c, s = math.cos(th), math.sin(th)
        a, b = y[:, i].copy(), y[:, j].copy()
        y[:, i] = c * a - s * b
        y[:, j] = s * a + c * b
    return y


def fixture(seed=1, family="anisotropic", scale=64.0, n=N):
    import numpy as np
    rng = np.random.default_rng(1000 * seed + 17)
    sd = _decay()
    direction = rng.standard_normal(DIM) * sd
    direction /= np.linalg.norm(direction)
    vocab = _rows(rng, 512, family, scale, sd, direction)
    ids = rng.choice(512, n, replace=False)
    e = vocab[ids].copy()
    target = float(np.linalg.norm(e, axis=1).mean())
    noise = _rows(rng, n, family, scale, sd, direction)
    h_correct = 2.5 * _givens(e, rng) + 0.5 * noise
    unrelated = _rows(rng, n, "isotropic", None or 1.0, sd, direction)
    unrelated *= target / float(np.linalg.norm(unrelated, axis=1).mean())
    other = _rows(rng, 1, "isotropic", None or 1.0, sd, direction) * 0.0
    other = _rows(rng, n, "isotropic", 1.0, sd, direction)
    other *= target / float(np.linalg.norm(other, axis=1).mean())
    perm = rng.permutation(n)
    return dict(vocab=vocab, ids=ids, e=e, target=target, h=h_correct,
                h_unrelated=unrelated, h_other=other, h_permuted=h_correct[perm])


def align(torch, hidden, embeddings, target, ridge=None):
    h = torch.from_numpy(hidden.astype("float32") if hasattr(hidden, "astype") else hidden)
    e = torch.from_numpy(embeddings.astype("float32") if hasattr(embeddings, "astype") else embeddings)
    kwargs = {} if ridge is None else {"ridge": float(ridge)}
    z, info = align_hidden_sequence(h, e, float(target), **kwargs)
    return z.numpy().astype("float64"), int(info.union_rank)


# ---------------------------------------------------------------------------
# metrics and the internals mirror
# ---------------------------------------------------------------------------
def _row_cos(a, b):
    import numpy as np
    return float(((a * b).sum(1) / np.maximum(
        np.linalg.norm(a, axis=1) * np.linalg.norm(b, axis=1), 1e-300)).mean())


def _rel(a, b):
    import numpy as np
    return float(np.linalg.norm(a - b) / max(np.linalg.norm(b), 1e-300))


def _cos_to_calibrated(z, e, target):
    import numpy as np
    e_cal = e * (target / np.linalg.norm(e, axis=1, keepdims=True))
    return _row_cos(z, e_cal), _rel(z, e_cal)


def _power(m, power, torch):
    vals, vectors = torch.linalg.eigh((m + m.mT) * 0.5)
    return (vectors * vals.clamp_min(FLOOR).pow(power)) @ vectors.mT


def internals(torch, hidden, embeddings, ridge):
    """Statement-by-statement mirror of statebridge.py:48-63 returning the Gram data.

    Only quantities the shipped function computes and discards are exposed: the
    centred blocks in the union coordinates, their whitening weights and X X^T.
    """
    h = torch.from_numpy(hidden.astype("float32"))
    e = torch.from_numpy(embeddings.astype("float32"))
    n = h.shape[0]
    hc, ec = h - h.mean(0, keepdim=True), e - e.mean(0, keepdim=True)
    _, singular, vh = torch.linalg.svd(torch.cat((hc, ec), 0), full_matrices=False)
    threshold = max(float(singular[0]) * RTOL, FLOOR)
    rank = min(int((singular > threshold).sum()), 2 * (n - 1))
    q = vh[:rank].mT
    bh, be = hc @ q, ec @ q
    eye = torch.eye(rank)
    ch = bh.mT @ bh / n + ridge * eye
    ce = be.mT @ be / n + ridge * eye
    x = bh @ _power(ch, -0.5, torch)
    y = be @ _power(ce, -0.5, torch)
    u, _, v = torch.linalg.svd(x.mT @ y, full_matrices=False)
    rot = u @ v
    z = (x @ rot @ _power(ce, 0.5, torch)) @ q.mT + e.mean(0, keepdim=True)
    proj = n * (torch.eye(n, dtype=torch.float64)
                - torch.ones(n, n, dtype=torch.float64) / n)
    gx = x.double() @ x.double().mT
    lam = torch.linalg.eigvalsh(bh.double().mT @ bh.double() / n).numpy()
    lam_e = torch.linalg.eigvalsh(be.double().mT @ be.double() / n).numpy()
    live = lam > 1e-6 * lam.max()
    live_e = lam_e > 1e-6 * lam_e.max()
    w = lam[live] / (lam[live] + ridge)
    w_e = lam_e[live_e] / (lam_e[live_e] + ridge)
    import numpy as np
    return dict(
        n=n, rank=rank, rank_bh=int(live.sum()), lam_min=float(lam[live].min()),
        w_min=float(w.min()), w_max=float(w.max()),
        w_e_min=float(w_e.min()), w_e_max=float(w_e.max()),
        gram_dev_x=float(torch.linalg.matrix_norm(gx - proj, ord=2).item()) / n,
        gram_dev_xy=float(torch.linalg.matrix_norm(
            gx - y.double() @ y.double().mT, ord=2).item()) / n,
        procrustes=float(np.linalg.norm((x @ rot - y).double().numpy())
                         / max(float(y.double().norm()), 1e-30)),
        n_null=int(rank - live.sum()),
        nonfinite=int((~torch.isfinite(z)).sum().item()),
    )


# ---------------------------------------------------------------------------
# claim 1 -- theorem 1 and theorem 2: independent H and E
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("scale", [1.0, 64.0])
def test_independent_h_collapses_to_e_at_small_ridge(real_torch, scale):
    """LIMIT claim: eq. 9-11. Any full-rank H reproduces E once lambda -> 0.

    ridge=1e-6 is the smallest value the shipped guard accepts (statebridge.py:43
    requires ridge > 0); lambda=0 itself is refused, so this is a limit proxy.
    Measured at N=128, D=6144, lambda=1e-6 (Table 3 of the report): cos(Z_i,E_i)
    is 1.0 to float32 and rel_change between two unrelated H is 1.6e-5 (scale 1)
    and 6.3e-4 (scale 64).  The scale-64 number is the float32 floor
    eps32/sqrt(eigen_floor) = 1.192e-7/1e-3 = 1.2e-4, not the ridge, so the
    tolerance below is 2e-3, i.e. 3.2x the largest measured value.
    """
    fx = fixture(family="anisotropic", scale=scale)
    z_a, _ = align(real_torch, fx["h"], fx["e"], fx["target"], ridge=1e-6)
    z_b, _ = align(real_torch, fx["h_unrelated"], fx["e"], fx["target"], ridge=1e-6)
    z_c, _ = align(real_torch, fx["h_other"], fx["e"], fx["target"], ridge=1e-6)
    for z in (z_a, z_b, z_c):
        cos, recon = _cos_to_calibrated(z, fx["e"], fx["target"])
        assert cos >= 1.0 - 1e-4, cos          # measured 1.0 (Table 3)
        assert recon <= 1e-3, recon            # measured 3.5e-5 .. 1.2e-4 (Table 3)
    # H is a gauge: two different unrelated H give the same output.
    assert _rel(z_b, z_a) <= 2e-3              # measured 6.3e-4 (Table 3)
    assert _rel(z_c, z_a) <= 2e-3              # measured 6.3e-4 (Table 3)


def test_row_permutation_of_h_does_not_permute_the_output(real_torch):
    """Eq. 12: the fit reproduces E, so it cannot follow a row permutation of H.

    Measured (Table 2, seed 1, anisotropic, scale 64, ridge shipped): Z(H[perm])
    differs from Z(H) by 5.5e-6 while cos(Z_i, E[perm(i)]) is 0.24, i.e. chance
    level.  Tolerance 1e-4 = 1.8x the measured value.
    """
    import numpy as np
    fx = fixture(family="anisotropic", scale=64.0)
    perm = np.random.default_rng(5).permutation(N)
    z_plain, _ = align(real_torch, fx["h"], fx["e"], fx["target"])
    z_perm, _ = align(real_torch, fx["h"][perm], fx["e"], fx["target"])
    assert _rel(z_perm, z_plain) <= 1e-4        # measured 5.5e-6, 6.0e-6, 6.1e-6
    followed = _row_cos(z_perm, fx["e"][perm])
    assert followed < 0.5                       # measured 0.242 (not 1.0)


# ---------------------------------------------------------------------------
# claim 2 -- finite ridge: the weight bound is attained
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("ridge", [1e-4, 1e-3, 1e-2])
def test_finite_ridge_deviation_equals_the_weight_bound(real_torch, ridge):
    """Eq. 14-17: the deviation from the lambda -> 0 identity is exactly max(1 - w).

    ||X X^T - N(I - 11^T/N)||_2 / N == max_i (1 - w_i) = lambda/(lambda_min + lambda)
    <= lambda/lambda_min, with lambda_min the smallest live eigenvalue of
    Hc^T Hc/N.  Both sides are measured, so the bound is asserted two-sided:
    attained from above (1.001 slack for float32) and not wasteful (>= 0.5x).
    Measured at scale 1: 2.568e-2 at lambda=1e-3 against lambda/lambda_min = 2.635e-2
    (Table 10).
    """
    fx = fixture(family="anisotropic", scale=1.0)
    tr = internals(real_torch, fx["h"], fx["e"], ridge)
    predicted = 1.0 - tr["w_min"]
    assert tr["n_null"] == N - 1, tr["n_null"]   # half of the 2(N-1) coords are null
    assert tr["gram_dev_x"] <= predicted * (1.0 + 1e-3), (tr["gram_dev_x"], predicted)
    assert tr["gram_dev_x"] >= 0.5 * predicted, (tr["gram_dev_x"], predicted)
    assert tr["gram_dev_x"] <= ridge / tr["lam_min"] * (1.0 + 1e-3)
    # eq. 16: the two-cloud mismatch is bounded by the sum of both weight losses.
    # Measured at scale 1, lambda=1e-3: 1.762e-1 against a bound of 2.562e-1 (Table 10).
    assert tr["gram_dev_xy"] <= ((1.0 - tr["w_min"]) + (1.0 - tr["w_e_min"])) * (1.0 + 1e-3)


def test_deviation_grows_with_ridge_and_vanishes_below_it(real_torch):
    """Eq. 15 monotonicity, and the lambda -> 0 identity to float32 on its own.

    Measured (Table 3, anisotropic, scale 1): 4.29e-5, 4.18e-3, 4.03e-2, 2.96e-1
    for lambda = 1e-6, 1e-4, 1e-3, 1e-2.
    """
    fx = fixture(family="anisotropic", scale=1.0)
    devs = [internals(real_torch, fx["h"], fx["e"], r)["gram_dev_x"]
            for r in (1e-6, 1e-4, 1e-3, 1e-2)]
    assert devs == sorted(devs), devs
    assert devs[0] <= 1e-4, devs[0]              # measured 4.29e-5 at lambda=1e-6


def test_ridge_is_irrelevant_when_the_row_grams_agree(real_torch):
    """Corollary 3a (eq. 18): H = E Q with Q orthogonal gives Z = E at ANY ridge.

    The weights w_i agree exactly because the centred row Grams are equal, so the
    whitening weight cancels instead of the ridge being small.  Measured (Table 8,
    scale 1 and scale 64, lambda up to 1e2): cos = 1.0 and recon = 2.1e-6 .. 3.5e-6
    (the float32 floor).  Tolerance 1e-4 = 28x the measured floor.
    """
    fx = fixture(family="anisotropic", scale=1.0)
    rotation = _givens(fx["e"], __import__("numpy").random.default_rng(4242))
    for ridge in (1e-3, 1.0, 100.0):
        z, _ = align(real_torch, rotation, fx["e"], fx["target"], ridge=ridge)
        cos, recon = _cos_to_calibrated(z, fx["e"], fx["target"])
        assert cos >= 1.0 - 1e-5, (ridge, cos)   # measured 1.0
        assert recon <= 1e-4, (ridge, recon)     # measured 2.2e-6 .. 2.5e-6


# ---------------------------------------------------------------------------
# claim 3 -- which regime the shipped configuration is in
# ---------------------------------------------------------------------------
def test_shipped_configuration_is_in_the_degenerate_regime(real_torch):
    """Shipped defaults (statebridge.py:32-33) are degenerate, not informative.

    ridge=1e-3 is not small relative to a unit-amplitude fixture: measured
    max_i(1 - w_i) = 4.03e-2 (Table 10, anisotropic, scale 1) yet cos(Z_i, E_i) is
    still 0.99943 and the recon error 3.38e-2 (Table 2, worst of 3 seeds).  At a
    hidden-state-scale fixture (row norm 64) the same ridge is inert: cos = 1.0,
    recon 9.6e-6, rel_change between unrelated H 1.65e-5 (Table 2).
    So the fit is degenerate in both regimes, for two different reasons: at unit
    scale the weights are nearly uniform (a pure scale error, eq. 19), at hidden
    scale they are 1 to 6.4e-6.
    """
    import numpy as np
    for scale, cos_floor, recon_cap, change_cap in (
            (1.0, 0.999, 5e-2, 5e-2),        # measured 0.99943 / 3.38e-2 / 2.32e-2
            (64.0, 1.0 - 1e-6, 1e-4, 1e-4)):  # measured 1.0 / 9.6e-6 / 1.65e-5
        fx = fixture(family="anisotropic", scale=scale)
        z, rank = align(real_torch, fx["h"], fx["e"], fx["target"])   # shipped ridge
        z_r, _ = align(real_torch, fx["h_unrelated"], fx["e"], fx["target"])
        cos, recon = _cos_to_calibrated(z, fx["e"], fx["target"])
        assert rank == 2 * (N - 1), rank    # the 2(N-1) cap binds, not rank_rtol
        assert cos >= cos_floor, (scale, cos)
        assert recon <= recon_cap, (scale, recon)
        assert _rel(z_r, z) <= change_cap, (scale, _rel(z_r, z))
        # retrieval of the paired reference token is unaffected by the degeneracy
        zn = z / np.linalg.norm(z, axis=1, keepdims=True)
        wn = fx["vocab"] / np.linalg.norm(fx["vocab"], axis=1, keepdims=True)
        assert float(((zn @ wn.T).argmax(1) == fx["ids"]).mean()) == 1.0


def test_control_large_ridge_breaks_the_degeneracy(real_torch):
    """Red-first control: the same assertions must fail when the regime changes.

    At ridge=1e-1 the weight loss max_i(1 - w_i) reaches 8.08e-1, so Z stops being
    E: measured cos(Z_i,E_i) = 0.9637 and recon 0.2693 (Table 3, anisotropic,
    scale 1).  A regression that made the shipped ridge large, or that removed the
    whitening, would trip the cos/recon thresholds used above.
    """
    fx = fixture(family="anisotropic", scale=1.0)
    z, _ = align(real_torch, fx["h"], fx["e"], fx["target"], ridge=1e-1)
    cos, recon = _cos_to_calibrated(z, fx["e"], fx["target"])
    assert cos <= 0.99, cos            # measured 0.9637
    assert recon >= 0.1, recon         # measured 0.2693
    tr = internals(real_torch, fx["h"], fx["e"], 1e-1)
    assert 1.0 - tr["w_min"] >= 0.5    # measured 0.808


def test_rank_deficiency_is_the_one_thing_that_breaks_it(real_torch):
    """Eq. 22: the hypothesis rank(Hc) = N-1 in theorem 1 is not decorative.

    A rank-8 H makes X X^T a rank-8 projector, so the deviation from
    N(I - 11^T/N) is 1 and Z no longer reproduces E: measured cos(Z_i,E_i) = 0.259
    and recon 1.22 at rank 8, 0.711/0.761 at rank 64 (Table 7).
    """
    import numpy as np
    fx = fixture(family="isotropic", scale=64.0)
    rng = np.random.default_rng(900 + 8)
    low = rng.standard_normal((N, 8)) @ (rng.standard_normal((8, DIM)) * 0.1)
    z, _ = align(real_torch, low, fx["e"], fx["target"])
    cos, recon = _cos_to_calibrated(z, fx["e"], fx["target"])
    assert cos < 0.5, cos          # measured 0.281
    assert recon > 0.9, recon      # measured 1.199
    tr = internals(real_torch, low, fx["e"], 1e-3)
    assert tr["rank_bh"] == 8 and tr["gram_dev_x"] >= 0.999   # measured 1.0


def test_guards_refuse_the_two_edges_the_derivation_uses(real_torch):
    """statebridge.py:43 refuses ridge=0 and n>128, so both are limit statements.

    Recorded so the report's refusal rows have a test witness.
    """
    fx = fixture(family="isotropic", scale=64.0)
    with pytest.raises(LatentError):
        align(real_torch, fx["h"], fx["e"], fx["target"], ridge=0.0)
    with pytest.raises(LatentError):
        align(real_torch, fx["h"][:127].repeat(2, 0)[:256], fx["e"][:127].repeat(2, 0)[:256],
              fx["target"])
