"""Tests for the frozen dual-ridge calibration map."""
from __future__ import annotations

import pytest

torch = pytest.importorskip("torch")


def _model(rank=32, dim=256, seed=0):
    """The frozen pair of projections that both sides of the fixture share."""
    generator = torch.Generator().manual_seed(seed)
    return (torch.randn(rank, dim, generator=generator) / rank ** 0.5,
            torch.randn(rank, dim, generator=generator) / rank ** 0.5)


def _sample(model, rows=512, kappa=0.5, seed=1):
    """Rows drawn through a shared model; only the factor samples change."""
    left, right = model
    generator = torch.Generator().manual_seed(seed)
    factor = torch.randn(rows, left.shape[0], generator=generator)
    noise_h = torch.randn(rows, left.shape[1], generator=generator) * (1.0 - kappa) ** 0.5
    noise_e = torch.randn(rows, right.shape[1], generator=generator) * (1.0 - kappa) ** 0.5
    return kappa ** 0.5 * (factor @ left) + noise_h, kappa ** 0.5 * (factor @ right) + noise_e


def _fixture(rows=512, dim=256, rank=32, kappa=0.5, seed=0):
    model = _model(rank=rank, dim=dim, seed=1000)
    return _sample(model, rows=rows, kappa=kappa, seed=seed)


def test_fit_refuses_an_independent_corpus():
    """Two unrelated models: no relation exists, so the fit must refuse."""
    from occ3_sglang.latent_types import LatentError
    from occ3_sglang.statebridge_calibrated import fit_calibration
    hidden, _ = _sample(_model(dim=256, seed=3001), rows=512, kappa=0.75, seed=31)
    _, embeddings = _sample(_model(dim=256, seed=3002), rows=512, kappa=0.75, seed=32)
    with pytest.raises(LatentError):
        fit_calibration(hidden, embeddings)


def test_heldout_quality_rises_with_the_signal():
    from occ3_sglang.statebridge_calibrated import heldout_r2
    values = []
    for kappa in (0.0, 0.25, 0.75):
        hidden, embeddings = _fixture(kappa=kappa, seed=11)
        values.append(heldout_r2(hidden, embeddings, 1e-3, torch))
    assert values[0] < values[1] < values[2]
    assert values[2] > 0.10


def test_refuses_a_corpus_below_the_row_floor():
    from occ3_sglang.latent_types import LatentError
    from occ3_sglang.statebridge_calibrated import fit_calibration
    hidden, embeddings = _fixture(rows=32)
    with pytest.raises(LatentError):
        fit_calibration(hidden, embeddings)


def test_refuses_nonfinite_calibration_rows():
    from occ3_sglang.latent_types import LatentError
    from occ3_sglang.statebridge_calibrated import fit_calibration
    hidden, embeddings = _fixture()
    hidden[4, 4] = float("nan")
    with pytest.raises(LatentError):
        fit_calibration(hidden, embeddings)


def test_frozen_map_lands_near_the_embedding_manifold_and_keeps_state_dependence():
    """The two properties the shipped per-message fit cannot hold at once."""
    from occ3_sglang.statebridge_calibrated import apply_calibration, fit_calibration
    model = _model(dim=256, seed=2000)
    hidden, embeddings = _sample(model, rows=512, kappa=0.75, seed=21)
    artifact = fit_calibration(hidden, embeddings)
    message_h, message_e = _sample(model, rows=64, kappa=0.75, seed=22)
    target = float(message_e.norm(dim=-1).mean())
    z = apply_calibration(artifact, message_h, target)
    cosine = float((z * message_e).sum(-1).div(
        z.norm(dim=-1) * message_e.norm(dim=-1)).mean())
    other = apply_calibration(artifact, _sample(model, rows=64, kappa=0.75, seed=23)[0], target)
    change = float((other - z).norm() / z.norm())
    assert cosine > 0.6, f"on-manifold cosine too low: {cosine}"
    assert change > 0.3, f"state dependence too low: {change}"


def test_apply_refuses_a_dimension_mismatch():
    from occ3_sglang.latent_types import LatentError
    from occ3_sglang.statebridge_calibrated import apply_calibration, fit_calibration
    hidden, embeddings = _fixture(rows=256, dim=128, kappa=0.75, seed=31)
    artifact = fit_calibration(hidden, embeddings)
    wrong = torch.randn(32, 64)
    with pytest.raises(LatentError):
        apply_calibration(artifact, wrong, 1.0)


def test_artifact_is_small_and_describable():
    from occ3_sglang.statebridge_calibrated import (CALIBRATION_ROWS_LIMIT, describe,
                                                   fit_calibration)
    hidden, embeddings = _fixture(rows=256, dim=128, kappa=0.75, seed=41)
    artifact = fit_calibration(hidden, embeddings)
    summary = describe(artifact)
    assert summary["rows"] == 256 and summary["hidden_dim"] == 128
    assert summary["bytes"] < 2 * CALIBRATION_ROWS_LIMIT * 6144 * 4
    assert all(type(v) in (int, float, str) for v in summary.values())


def test_output_is_norm_calibrated_and_finite():
    from occ3_sglang.statebridge_calibrated import apply_calibration, fit_calibration
    hidden, embeddings = _fixture(rows=256, dim=128, kappa=0.75, seed=51)
    artifact = fit_calibration(hidden, embeddings)
    z = apply_calibration(artifact, hidden[:32], 2.5)
    assert torch.isfinite(z).all()
    norms = z.norm(dim=-1)
    assert torch.allclose(norms, torch.full_like(norms, 2.5), atol=1e-4)
