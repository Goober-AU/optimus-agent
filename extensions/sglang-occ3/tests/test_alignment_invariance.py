"""Characterisation tests for the per-message StateBridge degeneracy.

These tests pin the CURRENT behaviour of align_hidden_sequence so that any
future repair has to update them deliberately. They are not a specification of
correct behaviour: they record a defect.

Statement under test
--------------------
With Hc, Ec centred, Sigma_X = Xc^T Xc / n + lambda I, whitened coordinates
Xw = Hc Sigma_H^-1/2 and Yw = Ec Sigma_E^-1/2, R = U V^T from the SVD of
Xw^T Yw, the output is

    Z = (Xw R) Sigma_E^1/2 Q^T + mu_E,  then row-norm calibrated.

Because R is chosen per message to minimise ||Xw R - Yw||, the product Xw R is
the orthogonal projection of Xw onto Yw's row space. Whenever the two whitened
Gram matrices agree -- which they do for centred matrices of equal row-space
rank under small ridge -- the residual is zero and Z equals the norm-calibrated
reference embeddings E, independently of the content of H. Permuting the rows
of H does not change Z, because the refitted R absorbs the permutation.
"""
from __future__ import annotations

import pytest

torch = pytest.importorskip("torch")


def _fixture(n, d, seed, pair=0.24):
    generator = torch.Generator().manual_seed(seed)
    e = torch.randn(n, d, generator=generator)
    h = pair * e + (1.0 - pair) * torch.randn(n, d, generator=generator)
    return h, e


def _align(h, e):
    from occ3_sglang.statebridge import align_hidden_sequence
    target = float(e.norm(dim=-1).mean())
    return align_hidden_sequence(h, e, target)[0], target


def test_output_is_the_norm_calibrated_reference_embeddings():
    h, e = _fixture(64, 1024, 3)
    z, target = _align(h, e)
    calibrated = e * (target / e.norm(dim=-1, keepdim=True))
    relative = float((z - calibrated).norm() / calibrated.norm())
    assert relative < 1e-3, f"expected degenerate reconstruction, got {relative}"


def test_permuting_the_hidden_rows_does_not_change_the_output():
    h, e = _fixture(64, 1024, 4)
    z, _ = _align(h, e)
    order = torch.randperm(h.shape[0], generator=torch.Generator().manual_seed(9))
    z_perm, _ = _align(h[order], e)
    assert torch.allclose(z_perm, z, atol=1e-3), "the carrier follows the row correspondence"
    assert not torch.allclose(z_perm, z[order], atol=1e-2), "trivially permuted output was expected elsewhere"


def test_replacing_the_hidden_states_with_noise_barely_moves_the_output():
    h, e = _fixture(64, 1024, 5)
    z, _ = _align(h, e)
    noise = torch.randn(h.shape, generator=torch.Generator().manual_seed(6))
    z_noise, _ = _align(noise, e)
    relative = float((z_noise - z).norm() / z.norm())
    assert relative < 5e-2, f"unrelated hidden states changed the carrier by {relative}"


def test_shuffling_the_reference_rows_moves_the_output_completely():
    """Control: the output does follow E, so E row order is visible."""
    h, e = _fixture(64, 1024, 7)
    z, _ = _align(h, e)
    order = torch.randperm(e.shape[0], generator=torch.Generator().manual_seed(8))
    z_e, _ = _align(h, e[order])
    relative = float((z_e - z).norm() / z.norm())
    assert relative > 0.5


def test_degeneracy_weakens_when_the_spectra_do_not_match():
    """A rank-deficient H cannot be whitened onto the full-rank E row space."""
    h, e = _fixture(64, 1024, 11)
    low = h.clone()
    low[:, 32:] = 0.0
    z_full, target = _align(h, e)
    z_low, _ = _align(low, e)
    calibrated = e * (target / e.norm(dim=-1, keepdim=True))
    full_error = float((z_full - calibrated).norm() / calibrated.norm())
    low_error = float((z_low - calibrated).norm() / calibrated.norm())
    assert low_error > full_error, "spectrum mismatch must reduce the reconstruction"
