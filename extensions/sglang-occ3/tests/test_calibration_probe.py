"""Tests for the scalar relation probe used in the live window."""
from __future__ import annotations

import pytest

torch = pytest.importorskip("torch")


def _fixture(rows=64, dim=512, rank=16, kappa=0.75, seed=0):
    generator = torch.Generator().manual_seed(seed)
    left = torch.randn(rank, dim, generator=generator) / rank ** 0.5
    right = torch.randn(rank, dim, generator=generator) / rank ** 0.5
    factor = torch.randn(rows, rank, generator=generator)
    hidden = kappa ** 0.5 * (factor @ left) + (1.0 - kappa) ** 0.5 * torch.randn(rows, dim, generator=generator)
    embeddings = kappa ** 0.5 * (factor @ right) + (1.0 - kappa) ** 0.5 * torch.randn(rows, dim, generator=generator)
    return hidden, embeddings


def test_probe_returns_the_fixed_key_set_and_no_content():
    from occ3_sglang.calibration_probe import PROBE_KEYS, probe_relation
    hidden, embeddings = _fixture()
    result = probe_relation(hidden, embeddings)
    assert set(result) == set(PROBE_KEYS)
    assert isinstance(result["canonical_decile"], list)
    assert all(type(v) in (int, float) for k, v in result.items() if k != "canonical_decile")


def test_canonical_correlations_are_high_when_the_relation_exists():
    from occ3_sglang.calibration_probe import probe_relation
    hidden, embeddings = _fixture(kappa=0.9, seed=3)
    result = probe_relation(hidden, embeddings)
    assert result["canonical_top8"] > 0.5, result["canonical_top8"]


def test_canonical_correlations_do_not_separate_related_from_independent():
    """Whitening makes two full-rank clouds congruent, so the spectrum saturates.

    This is the reason the probe also reports held-out within-turn R^2.
    """
    from occ3_sglang.calibration_probe import probe_relation
    related, related_e = _fixture(kappa=0.9, seed=11)
    generator = torch.Generator().manual_seed(5)
    hidden = torch.randn(64, 512, generator=generator)
    embeddings = torch.randn(64, 512, generator=generator)
    independent = probe_relation(hidden, embeddings)
    paired = probe_relation(related, related_e)
    assert independent["canonical_top8"] > 0.90
    assert abs(independent["canonical_top8"] - paired["canonical_top8"]) < 0.1


def test_heldout_within_turn_r2_separates_related_from_independent():
    from occ3_sglang.calibration_probe import probe_relation
    related, related_e = _fixture(kappa=0.9, seed=11)
    generator = torch.Generator().manual_seed(5)
    hidden = torch.randn(64, 512, generator=generator)
    embeddings = torch.randn(64, 512, generator=generator)
    paired = probe_relation(related, related_e)
    independent = probe_relation(hidden, embeddings)
    assert paired["heldout_r2_within_turn"] > 0.05, paired["heldout_r2_within_turn"]
    assert independent["heldout_r2_within_turn"] < paired["heldout_r2_within_turn"]


def test_probe_detects_the_shipped_prefix_as_a_copy_of_the_embeddings():
    from occ3_sglang.align_diagnostics import alignment_diagnostics
    from occ3_sglang.calibration_probe import probe_relation
    from occ3_sglang.statebridge import align_hidden_sequence
    hidden, embeddings = _fixture(rows=64, dim=512, seed=7)
    target = float(embeddings.norm(dim=-1).mean())
    prefix, _ = align_hidden_sequence(hidden, embeddings, target)
    result = probe_relation(hidden, embeddings, prefix)
    assert result["cos_prefix_to_embedding"] > 0.999
    assert result["hidden_roll_sensitivity"] < 0.5


def test_probe_refuses_a_short_message():
    from occ3_sglang.calibration_probe import probe_relation
    from occ3_sglang.latent_types import LatentError
    hidden, embeddings = _fixture(rows=1)
    with pytest.raises(LatentError):
        probe_relation(hidden, embeddings)
