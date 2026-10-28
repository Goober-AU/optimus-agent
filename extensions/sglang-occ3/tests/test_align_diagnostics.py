"""Tests for the bounded scalar alignment diagnostics."""
from __future__ import annotations

import sys

import pytest

torch = pytest.importorskip("torch")


def _fixture(n=16, d=64, seed=7, pair=0.24):
    generator = torch.Generator().manual_seed(seed)
    e = torch.randn(n, d, generator=generator)
    h = pair * e + (1.0 - pair) * torch.randn(n, d, generator=generator)
    return h, e


def test_solve_matches_the_production_solver():
    from occ3_sglang.align_diagnostics import _solve
    from occ3_sglang.statebridge import align_hidden_sequence
    h, e = _fixture()
    expected, info = align_hidden_sequence(h, e, 1.0)
    got = _solve(h, e, 1.0, ridge=1e-3, eigen_floor=1e-6, rank_rtol=1e-5, torch=torch)
    assert got["rank"] == info.union_rank
    assert torch.allclose(got["prefix"], expected, atol=1e-6)


def test_diagnostics_are_scalar_only_and_complete():
    from occ3_sglang.align_diagnostics import DIAGNOSTIC_KEYS, alignment_diagnostics
    h, e = _fixture()
    result = alignment_diagnostics(h, e, target_norm=1.0)
    assert set(result) == set(DIAGNOSTIC_KEYS)
    assert all(type(v) in (int, float) and not isinstance(v, bool) for v in result.values())
    assert result["rows"] == 16 and result["hidden_dim"] == 64
    assert result["nonfinite_count"] == 0


def test_prefix_equal_to_reference_embeddings_scores_cosine_one():
    from occ3_sglang.align_diagnostics import alignment_diagnostics
    h, e = _fixture()
    result = alignment_diagnostics(h, e, prefix=e.clone(), target_norm=float(e.norm(dim=-1).mean()))
    assert result["cos_prefix_to_embedding"] == pytest.approx(1.0, abs=1e-6)


def test_production_map_is_nearly_insensitive_to_the_hidden_states():
    """Documents the degeneracy at small scale; see PRODUCTION-SCALE-DEGENERACY.md."""
    from occ3_sglang.align_diagnostics import alignment_diagnostics
    h, e = _fixture(n=32, d=256, seed=5)
    norm = float(e.norm(dim=-1).mean())
    result = alignment_diagnostics(h, e, target_norm=norm, sensitivity=True)
    assert result["cos_prefix_to_embedding"] > 0.999999
    assert result["relative_prefix_error"] < 1e-3
    assert result["hidden_sensitivity"] < 1e-3
    assert result["shuffled_hidden_sensitivity"] < 1e-3


def test_hidden_sensitivity_is_large_when_the_prefix_is_the_state():
    from occ3_sglang.align_diagnostics import alignment_diagnostics
    h, e = _fixture()
    norm = float(h.norm(dim=-1).mean())
    scaled = h * (norm / h.norm(dim=-1, keepdim=True))
    result = alignment_diagnostics(h, e, prefix=scaled, target_norm=norm, sensitivity=True)
    assert result["hidden_sensitivity"] > 0.1


def test_procrustes_residual_is_small_on_matched_spectra_and_large_on_mismatched():
    from occ3_sglang.align_diagnostics import alignment_diagnostics
    h, e = _fixture(seed=11)
    matched = alignment_diagnostics(h, e, target_norm=1.0, sensitivity=False)
    spiked = e.clone()
    spiked[0] *= 40.0
    spiked[1] *= 0.01
    mismatched = alignment_diagnostics(h, spiked, target_norm=1.0, sensitivity=False)
    assert matched["procrustes_residual"] < 0.2
    assert mismatched["procrustes_residual"] > matched["procrustes_residual"]


def test_refuses_nonfinite_input():
    from occ3_sglang.align_diagnostics import alignment_diagnostics
    from occ3_sglang.latent_types import LatentError
    h, e = _fixture()
    h[3, 3] = float("nan")
    with pytest.raises(LatentError):
        alignment_diagnostics(h, e, target_norm=1.0)


def test_refuses_shape_mismatch():
    from occ3_sglang.align_diagnostics import alignment_diagnostics
    from occ3_sglang.latent_types import LatentError
    h, e = _fixture()
    with pytest.raises(LatentError):
        alignment_diagnostics(h, e[:-1], target_norm=1.0)


def test_validate_rejects_an_extra_key_and_a_nonfinite_value():
    from occ3_sglang.align_diagnostics import alignment_diagnostics, validate
    h, e = _fixture()
    result = alignment_diagnostics(h, e, target_norm=1.0, sensitivity=False)
    from occ3_sglang.latent_types import LatentError
    with pytest.raises(LatentError):
        validate(dict(result, unexpected=1.0))
    with pytest.raises(LatentError):
        validate(dict(result, hidden_norm_mean=float("nan")))


def test_workspace_bound_has_no_hidden_dim_squared_term():
    from occ3_sglang.align_diagnostics import workspace_elements
    for rows, dim in ((128, 6144), (16, 64), (2, 8)):
        bound = workspace_elements(rows, dim)
        assert bound <= 2 * rows * dim
        assert bound < dim * dim


# Frozen calibrated map: the copier control and state dependence of a map that
# was fitted on other rows.

def _corpus(rows=200, dim=64, rank=8, kappa=0.9, seed=0, copier=False):
    """One shared hidden-to-embedding model; only the row samples change."""
    model = torch.Generator().manual_seed(1000)
    left = torch.randn(rank, dim, generator=model) / rank ** 0.5
    right = torch.randn(rank, dim, generator=model) / rank ** 0.5
    generator = torch.Generator().manual_seed(seed)
    factor = torch.randn(rows, rank, generator=generator)
    hidden = kappa ** 0.5 * (factor @ left) + (1.0 - kappa) ** 0.5 * torch.randn(rows, dim, generator=generator)
    if copier:
        embeddings = hidden + 1e-3 * torch.randn(rows, dim, generator=generator)
    else:
        embeddings = kappa ** 0.5 * (factor @ right) + (1.0 - kappa) ** 0.5 * torch.randn(rows, dim, generator=generator)
    return hidden, embeddings


def _frozen(copier=False):
    from occ3_sglang.statebridge_calibrated import fit_calibration
    hidden, embeddings = _corpus(copier=copier)
    return fit_calibration(hidden, embeddings)


def test_frozen_map_diagnostics_are_scalar_only_and_complete():
    from occ3_sglang.align_diagnostics import CALIBRATED_DIAGNOSTIC_KEYS, frozen_map_diagnostics
    artifact = _frozen()
    message, embeddings = _corpus(rows=64, seed=2000)
    result = frozen_map_diagnostics(artifact, message, embeddings)
    assert set(result) == set(CALIBRATED_DIAGNOSTIC_KEYS)
    assert all(type(value) in (int, float) and not isinstance(value, bool)
               for value in result.values())
    assert result["rows"] == 64 and result["hidden_dim"] == 64
    assert result["nonfinite_count"] == 0
    assert result["predicted_r2"] > 0.10, result["predicted_r2"]
    assert result["cos_prefix_to_embedding"] > 0.5
    assert result["hidden_sensitivity"] > 0.3, result["hidden_sensitivity"]
    assert result["shuffled_hidden_sensitivity"] > 0.3
    assert result["copier_flag"] == int(result["cos_prefix_to_embedding"] > 0.9)


def test_copier_flag_lights_up_for_a_map_that_reproduces_the_embeddings():
    from occ3_sglang.align_diagnostics import COPIER_COSINE_LIMIT, frozen_map_diagnostics
    artifact = _frozen(copier=True)
    message, embeddings = _corpus(rows=64, seed=2001, copier=True)
    result = frozen_map_diagnostics(artifact, message, embeddings)
    assert result["cos_prefix_to_embedding"] > COPIER_COSINE_LIMIT
    assert result["copier_flag"] == 1


def test_frozen_map_diagnostics_refuses_shape_and_nonfinite_input():
    from occ3_sglang.align_diagnostics import frozen_map_diagnostics
    from occ3_sglang.latent_types import LatentError
    artifact = _frozen()
    message, embeddings = _corpus(rows=64, seed=2002)
    with pytest.raises(LatentError):
        frozen_map_diagnostics(artifact, message, embeddings[:-1])
    with pytest.raises(LatentError):
        frozen_map_diagnostics(artifact, message[:, :-1], embeddings[:, :-1])
    broken = embeddings.clone()
    broken[0, 0] = float("nan")
    with pytest.raises(LatentError):
        frozen_map_diagnostics(artifact, message, broken)


def test_validate_calibrated_rejects_an_extra_key_and_a_nonfinite_value():
    from occ3_sglang.align_diagnostics import frozen_map_diagnostics, validate_calibrated
    from occ3_sglang.latent_types import LatentError
    artifact = _frozen()
    message, embeddings = _corpus(rows=64, seed=2003)
    result = frozen_map_diagnostics(artifact, message, embeddings)
    with pytest.raises(LatentError):
        validate_calibrated(dict(result, unexpected=1.0))
    with pytest.raises(LatentError):
        validate_calibrated(dict(result, predicted_r2=float("inf")))
    with pytest.raises(LatentError):
        validate_calibrated(dict(result, copier_flag=2))
