"""Method dispatch: exactly one aligner per negotiated method id, never a substitute.

The per-message method keeps the shipped union-subspace Procrustes solver. The
independent-calibration method serves a frozen dual-ridge artifact and refuses
anything else: no artifact, an artifact fitted for another method id, or an
artifact whose hidden dimension is not this profile's. There is deliberately no
fallback path from the calibrated method to the per-message solver, so a
decline can never be answered with per-message output.

Tensor-free at import: torch is reached only through the solver that runs.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
import os

from . import statebridge, statebridge_calibrated
from .latent_types import LatentError, RuntimeProfile, require
from .native_profile import CALIBRATED_METHOD, PER_MESSAGE_METHOD, method_descriptor

MAX_ARTIFACT_FILE_BYTES = statebridge_calibrated.MAX_ARTIFACT_BYTES


class AlignmentRefusal(LatentError):
    """Named refusal for a profile and artifact that one method cannot serve.

    Codes are the existing bounded LatentError codes: "unavailable" when no
    artifact is supplied or the file is unreadable, "identity" for a method or
    digest mismatch, "profile" for any other inconsistency, "quota" for a file
    beyond the artifact bound.
    """


@dataclass(frozen=True)
class CalibratedAlignmentInfo:
    """What the calibrated aligner used, for the capture record. Scalars only."""

    method: str
    rows: int
    hidden_dim: int
    artifact_digest: str


def _refuse(code):
    raise AlignmentRefusal(code)


def _descriptor_or_refuse(method):
    try:
        return method_descriptor(method)
    except Exception:
        return _refuse("profile")


def frozen_artifact_reference(profile):
    """The validated (path, digest) pair this profile names, or None."""
    require(isinstance(profile, RuntimeProfile), "profile")
    if not profile.calibration_path:
        return None
    return profile.calibration_path, profile.calibration_digest


def load_frozen_artifact(profile, *, torch_module=None):
    """Resolve and validate the frozen artifact this profile is bound to.

    Returns None for a profile that names no reference. A named reference is
    read only after the size bound, and the bytes are accepted only through
    statebridge_calibrated.deserialize_artifact with the profile's own method id,
    hidden dimension and expected artifact digest, so a swapped or truncated
    file refuses instead of loading.
    """
    reference = frozen_artifact_reference(profile)
    if reference is None:
        return None
    path, expected = reference
    try:
        size = os.stat(path).st_size
    except OSError:
        _refuse("unavailable")
    if not 0 < size <= MAX_ARTIFACT_FILE_BYTES:
        _refuse("quota")
    try:
        with open(path, "rb") as handle:
            raw = handle.read(MAX_ARTIFACT_FILE_BYTES + 1)
    except OSError:
        _refuse("unavailable")
    if len(raw) != size or len(raw) > MAX_ARTIFACT_FILE_BYTES:
        _refuse("identity")
    try:
        return statebridge_calibrated.deserialize_artifact(
            raw, expected_method_id=profile.method,
            expected_hidden_dim=profile.hidden_dim,
            expected_artifact_digest=expected, torch_module=torch_module)
    except LatentError as error:
        raise AlignmentRefusal(error.code) from None


def _on_device(artifact, device):
    """Move the frozen tensors once; the fit parameters are never recomputed."""
    fields = ("mean_hidden", "mean_embedding", "hidden_residual", "embedding_residual", "gram")
    if artifact.mean_hidden.device == device:
        return artifact
    return replace(artifact, **{name: getattr(artifact, name).to(device) for name in fields})


def _calibrated_aligner(profile, artifact):
    digest = statebridge_calibrated.artifact_digest(artifact)
    holder = {"artifact": artifact}

    def align(hidden, embeddings, target_norm):
        frozen = _on_device(holder["artifact"], hidden.device)
        holder["artifact"] = frozen
        prefix = statebridge_calibrated.apply_calibration(frozen, hidden, target_norm)
        return prefix, CalibratedAlignmentInfo(method=profile.method, rows=frozen.rows,
                                               hidden_dim=frozen.hidden_dim,
                                               artifact_digest=digest)

    align.artifact_digest = digest
    align.rows = artifact.rows
    return align


def _per_message_aligner():
    def align(hidden, embeddings, target_norm):
        return statebridge.align_hidden_sequence(hidden, embeddings, target_norm)

    return align


def aligner_for(profile, artifact=None):
    """Return (hidden, embeddings, target_norm) -> (prefix, info) for this method.

    The per-message method keeps the shipped solver and ignores any artifact. The
    calibrated method serves the frozen artifact and refuses a missing artifact, a
    method-id mismatch or a hidden-dimension mismatch. The pair is resolved once,
    so a misconfiguration declines before any rank enters the solver path.
    """
    require(isinstance(profile, RuntimeProfile), "profile")
    descriptor = _descriptor_or_refuse(profile.method)
    if not descriptor["requires_calibration_artifact"]:
        return _per_message_aligner()
    if artifact is None:
        _refuse("unavailable")
    if not isinstance(artifact, statebridge_calibrated.CalibrationArtifact):
        _refuse("profile")
    if artifact.method_id != profile.method:
        _refuse("identity")
    if artifact.hidden_dim != profile.hidden_dim:
        _refuse("profile")
    if profile.calibration_digest and (
            statebridge_calibrated.artifact_digest(artifact) != profile.calibration_digest):
        _refuse("identity")
    return _calibrated_aligner(profile, artifact)
