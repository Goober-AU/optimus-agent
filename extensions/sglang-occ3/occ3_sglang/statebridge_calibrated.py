"""Frozen dual-ridge alignment fitted on independent calibration rows.

The per-message StateBridge fit uses the transmitted rows' own embeddings as the
Procrustes target, so it reproduces those embeddings and discards the state
(see PRODUCTION-SCALE-DEGENERACY.md and MATHEMATICAL-DEGENERACY.md). This module
fits the transform on a calibration corpus collected earlier, freezes it, and
applies it to a message whose embeddings were never part of the fit.

Method (dual form of ridge regression from hidden rows to embedding rows):

    A = H_cal - mean(H_cal)                     M x d
    B = E_cal - mean(E_cal)                     M x d
    K = (H_msg - mean(H_cal)) A^T               N x M
    Z = mean(E_cal) + (K (A A^T + lambda I)^-1) B        N x d

The solve is an M x M Cholesky solve, never an explicit inverse and never a
d x d matrix. With M at most CALIBRATION_ROWS_LIMIT the artifact is a few tens
of megabytes and the per-message cost is O(N M d).

Everything here is pure: torch is passed in, nothing is imported at module load.
The fit refuses a corpus that does not predict its own held-out embeddings
better than the mean, so a no-signal corpus fails closed instead of silently
emitting the mean predictor.

Method identity: this is NOT the shipped per-message solver. Any use must carry
its own method identifier; see CALIBRATED-METHOD.md for the versioning and
integration plan.
"""
from __future__ import annotations

from dataclasses import dataclass

from .latent_types import require

METHOD_ID = "statebridge.glm53.final-prenorm-predictor.calibrated-dual-ridge.v1"
CALIBRATION_ROWS_LIMIT = 2048
MIN_CALIBRATION_ROWS = 64
MIN_HELDOUT_ROWS = 16
DEFAULT_RIDGE_REL = 0.02
MIN_HELDOUT_R2 = 0.10


@dataclass(frozen=True)
class CalibrationArtifact:
    method_id: str
    hidden_dim: int
    rows: int
    ridge: float
    ridge_rel: float
    heldout_r2: float
    mean_hidden: object
    mean_embedding: object
    hidden_residual: object
    embedding_residual: object
    gram: object


def _torch(reference):
    import torch
    return torch


def _centred(matrix):
    return matrix - matrix.mean(0, keepdim=True)


def absolute_ridge(gram, ridge_rel):
    """Ridge as a fraction of the mean centred row energy, never an absolute constant.

    A constant ridge cannot serve two hidden widths: the Gram entries scale with
    the dimension, so a constant that is adequate at d=256 overfits at d=512.
    Measured: held-out R^2 goes from -0.37 at ridge 1e-3 to +0.38 at ridge 10 and
    +0.65 at ridge 100 on the same corpus whose centred row energy is 527.
    """
    require(0.0 < ridge_rel < 1.0, "profile")
    energy = float(gram.diag().mean())
    require(energy > 0.0, "numerical")
    return ridge_rel * energy


def _solve(gram, values, ridge, torch):
    """(A A^T + lambda I)^-1 values, by Cholesky, without forming an inverse."""
    size = gram.shape[0]
    eye = torch.eye(size, dtype=gram.dtype, device=gram.device)
    factor = torch.linalg.cholesky(gram + ridge * eye)
    return torch.cholesky_solve(values.mT, factor).mT


def heldout_r2(hidden, embeddings, ridge, torch, split=0.8):
    """Ridge here is absolute; use absolute_ridge() to derive it from a corpus."""
    """R^2 of the frozen map on rows it was not fitted on, against the mean."""
    rows = hidden.shape[0]
    fit_rows = max(1, int(rows * split))
    require(rows - fit_rows >= MIN_HELDOUT_ROWS, "profile")
    a = _centred(hidden[:fit_rows].float())
    b = _centred(embeddings[:fit_rows].float())
    mu_e = embeddings[:fit_rows].float().mean(0, keepdim=True)
    probe_h = hidden[fit_rows:].float()
    probe_e = embeddings[fit_rows:].float()
    gram = a @ a.mT
    weights = _solve(gram, (probe_h - hidden[:fit_rows].float().mean(0, keepdim=True)) @ a.mT, ridge, torch)
    predicted = mu_e + weights @ b
    residual = (predicted - probe_e).pow(2).sum()
    baseline = (probe_e - mu_e).pow(2).sum()
    require(bool(torch.isfinite(residual)) and float(baseline) > 0, "numerical")
    return float(1.0 - residual / baseline)


def fit_calibration(hidden, embeddings, *, ridge_rel=DEFAULT_RIDGE_REL,
                    min_heldout_r2=MIN_HELDOUT_R2, torch_module=None):
    """Fit and freeze the transform. Refuses a corpus that does not predict."""
    torch = torch_module or _torch(hidden)
    require(hidden.ndim == 2 and hidden.shape == embeddings.shape, "profile")
    rows, dim = hidden.shape
    require(MIN_CALIBRATION_ROWS <= rows <= CALIBRATION_ROWS_LIMIT, "profile")
    require(dim > 2 and 0.0 < ridge_rel < 1.0, "profile")
    require(hidden.device == embeddings.device, "profile")
    with torch.no_grad():
        h, e = hidden.float(), embeddings.float()
        require(bool(torch.isfinite(h).all()) and bool(torch.isfinite(e).all()), "numerical")
        energy = float((h - h.mean(0, keepdim=True)).pow(2).sum(1).mean())
        require(energy > 0.0, "numerical")
        ridge = ridge_rel * energy
        quality = heldout_r2(h, e, ridge, torch)
        require(quality >= min_heldout_r2, "numerical")
        mu_h, mu_e = h.mean(0, keepdim=True), e.mean(0, keepdim=True)
        a = h - mu_h
        b = e - mu_e
        gram = a @ a.mT
        require(bool(torch.isfinite(gram).all()), "numerical")
        smallest = float(torch.linalg.eigvalsh(gram).min())
        require(smallest >= -1e-6 * float(gram.abs().max()), "numerical")
    return CalibrationArtifact(method_id=METHOD_ID, hidden_dim=int(dim), rows=int(rows),
                               ridge=float(ridge), ridge_rel=float(ridge_rel),
                               heldout_r2=float(quality),
                               mean_hidden=mu_h, mean_embedding=mu_e,
                               hidden_residual=a, embedding_residual=b, gram=gram)


def apply_calibration(artifact, hidden, target_norm, *, torch_module=None):
    """Map new hidden rows into the embedding space with the frozen transform."""
    torch = torch_module or _torch(hidden)
    require(isinstance(artifact, CalibrationArtifact) and artifact.method_id == METHOD_ID, "identity")
    require(hidden.ndim == 2 and hidden.shape[1] == artifact.hidden_dim, "profile")
    require(hidden.shape[0] >= 2, "profile")
    require(hidden.device == artifact.mean_hidden.device, "profile")
    with torch.no_grad():
        h = hidden.float()
        require(bool(torch.isfinite(h).all()), "numerical")
        weights = _solve(artifact.gram, (h - artifact.mean_hidden) @ artifact.hidden_residual.mT,
                         artifact.ridge, torch)
        z = artifact.mean_embedding + weights @ artifact.embedding_residual
        norms = z.norm(dim=-1, keepdim=True)
        target = torch.as_tensor(target_norm, device=z.device, dtype=z.dtype)
        require(target.numel() == 1 and bool(torch.isfinite(target)) and float(target) > 0, "numerical")
        require(bool(torch.isfinite(z).all()) and bool((norms > 1e-6).all()), "numerical")
        z = z * (target / norms)
        require(bool(torch.isfinite(z).all()), "numerical")
    return z.to(dtype=hidden.dtype).contiguous()


def artifact_bytes(artifact):
    """Resident size of the frozen transform, for the live-bytes budget."""
    total = 0
    for value in (artifact.mean_hidden, artifact.mean_embedding,
                  artifact.hidden_residual, artifact.embedding_residual, artifact.gram):
        total += value.numel() * value.element_size()
    return total


def describe(artifact):
    """Scalar-only summary; safe for a receipt."""
    return {"method_id": artifact.method_id, "rows": artifact.rows,
            "hidden_dim": artifact.hidden_dim, "ridge": artifact.ridge,
            "ridge_rel": artifact.ridge_rel,
            "heldout_r2": artifact.heldout_r2, "bytes": artifact_bytes(artifact)}
