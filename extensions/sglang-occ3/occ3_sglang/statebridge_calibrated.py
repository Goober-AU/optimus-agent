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

Artifact format. A saved artifact is one byte string:

    MAGIC(8) | header_length(8, big-endian) | header(ASCII JSON) | payload

The header is a flat dictionary of scalars plus two bounded shape/digest maps.
It carries the full binding (method id, dimensions, row count, ridge, held-out
quality, model/checkpoint/tokenizer/embedding/capture identity, predictor
position offset, precision, corpus digest) and two content digests. It is
validated in full before any tensor byte is interpreted: schema, exact field
set, identifier and digest syntax, expected method id, expected provenance,
tensor shapes against hidden_dim and rows, payload length and sha256, and the
artifact digest. The payload is a torch.save archive of plain tensors only and
is always read with torch.load(weights_only=True, map_location="cpu"); this
module never calls torch.load with pickle weights_only=False. The payload bytes
are little-endian float32, so a big-endian host is refused instead of
reinterpreting them.
"""
from __future__ import annotations

import array
import hashlib
import io
import json
import math
import re
import sys
from dataclasses import dataclass

from .latent_types import LatentError, require

METHOD_ID = "statebridge.glm53.final-prenorm-predictor.calibrated-dual-ridge.v1"
CALIBRATION_ROWS_LIMIT = 2048
MIN_CALIBRATION_ROWS = 64
MIN_HELDOUT_ROWS = 16
DEFAULT_RIDGE_REL = 0.02
MIN_HELDOUT_R2 = 0.10

ARTIFACT_SCHEMA = "optimus.occ3.calibration-artifact.v1"
CORPUS_DIGEST_DOMAIN = "optimus.occ3.calibration-corpus.v1"
ARTIFACT_MAGIC = b"OCC3CAL1"
PRECISION = "float32"
PREDICTOR_POSITION_OFFSET = -1
MAX_HEADER_BYTES = 8192
MAX_ARTIFACT_BYTES = 256 * 1024 * 1024
MAX_CORPUS_TURNS = 4096
TENSOR_NAMES = ("mean_hidden", "mean_embedding", "hidden_residual",
                "embedding_residual", "gram")
BINDING_FIELDS = frozenset({
    "method_id", "hidden_dim", "rows", "layer_index", "ridge", "ridge_rel",
    "heldout_r2", "model_fingerprint", "checkpoint_revision",
    "tokenizer_fingerprint", "embedding_id", "capture_method_id",
    "predictor_position_offset", "precision", "corpus_digest",
})
HEADER_FIELDS = BINDING_FIELDS | frozenset({
    "schema", "byte_order", "tensor_dtype", "tensor_shapes", "tensor_digest",
    "payload_sha256", "payload_bytes", "artifact_digest",
})
DIGEST_EXCLUDED = frozenset({"artifact_digest", "payload_sha256", "payload_bytes"})
_HEX = re.compile(r"[0-9a-f]{64}\Z")
_IDENTIFIER = re.compile(r"[A-Za-z0-9._:/-]{1,256}\Z")

@dataclass(frozen=True)
class CalibrationProvenance:
    """Caller-supplied identity of the model that produced the corpus.

    None of these values is derived from the corpus rows: the caller has to
    supply what the runtime negotiates, and deserialization compares the saved
    copy against it. precision and predictor_position_offset are fixed because
    the fit and the capture convention are fixed.
    """
    layer_index: int
    model_fingerprint: str
    checkpoint_revision: str
    tokenizer_fingerprint: str
    embedding_id: str
    capture_method_id: str
    predictor_position_offset: int = PREDICTOR_POSITION_OFFSET
    precision: str = PRECISION

    def __post_init__(self):
        require(_uint(self.layer_index, 0, 2**32 - 1), "profile")
        for name in ("model_fingerprint", "checkpoint_revision", "tokenizer_fingerprint",
                     "embedding_id", "capture_method_id"):
            require(_identifier(getattr(self, name)), "profile")
        require(self.predictor_position_offset == PREDICTOR_POSITION_OFFSET, "profile")
        require(self.precision == PRECISION, "profile")


@dataclass(frozen=True)
class CalibrationBinding:
    """Content-independent identity of one fitted transform."""
    method_id: str
    hidden_dim: int
    rows: int
    layer_index: int
    ridge: float
    ridge_rel: float
    heldout_r2: float
    model_fingerprint: str
    checkpoint_revision: str
    tokenizer_fingerprint: str
    embedding_id: str
    capture_method_id: str
    predictor_position_offset: int
    precision: str
    corpus_digest: str

    def __post_init__(self):
        require(self.method_id == METHOD_ID, "identity")
        require(_uint(self.hidden_dim, 3, 2**20) and _uint(self.rows, MIN_CALIBRATION_ROWS,
                                                          CALIBRATION_ROWS_LIMIT), "profile")
        require(_uint(self.layer_index, 0, 2**32 - 1), "profile")
        require(_finite(self.ridge) and self.ridge > 0.0, "numerical")
        require(_finite(self.ridge_rel) and 0.0 < self.ridge_rel < 1.0, "profile")
        require(_finite(self.heldout_r2), "numerical")
        for name in ("model_fingerprint", "checkpoint_revision", "tokenizer_fingerprint",
                     "embedding_id", "capture_method_id"):
            require(_identifier(getattr(self, name)), "identity")
        require(self.predictor_position_offset == PREDICTOR_POSITION_OFFSET, "profile")
        require(self.precision == PRECISION, "profile")
        require(_digest(self.corpus_digest), "identity")

    def scalars(self):
        """The flat scalar header of the binding; no tensor content."""
        return {"method_id": self.method_id, "hidden_dim": self.hidden_dim, "rows": self.rows,
                "layer_index": self.layer_index, "ridge": self.ridge,
                "ridge_rel": self.ridge_rel, "heldout_r2": self.heldout_r2,
                "model_fingerprint": self.model_fingerprint,
                "checkpoint_revision": self.checkpoint_revision,
                "tokenizer_fingerprint": self.tokenizer_fingerprint,
                "embedding_id": self.embedding_id,
                "capture_method_id": self.capture_method_id,
                "predictor_position_offset": self.predictor_position_offset,
                "precision": self.precision, "corpus_digest": self.corpus_digest}


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
    binding: object = None


def _uint(value, minimum=0, maximum=(1 << 64) - 1):
    return type(value) is int and minimum <= value <= maximum


def _finite(value):
    return type(value) is float and math.isfinite(value)


def _digest(value):
    return type(value) is str and _HEX.fullmatch(value) is not None


def _identifier(value):
    return type(value) is str and _IDENTIFIER.fullmatch(value) is not None


def _canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=True, allow_nan=False).encode("ascii")


def _byte_order():
    require(sys.byteorder == "little", "profile")
    return sys.byteorder


def _tensor_bytes(matrix, torch):
    """Little-endian float32 image of one matrix, as bytes.

    torch.storage.UntypedStorage.__bytes__ is a per-byte loop (measured 4.7 s
    per MiB on torch 2.11), so the storage never reaches the digest. numpy gives
    a memcpy and is already part of the serving image; the array fallback keeps
    the digest defined when it is absent. Both paths produce the same bytes.
    """
    require(getattr(matrix, "ndim", 0) == 2, "profile")
    flat = matrix.detach().to(dtype=torch.float32, device="cpu", copy=True).contiguous()
    require(int(flat.numel()) == int(matrix.numel()), "profile")
    try:
        import numpy
    except ImportError:
        raw = array.array("f", flat.view(-1).tolist()).tobytes()
    else:
        raw = bytes(memoryview(numpy.ascontiguousarray(flat.numpy())))
    require(len(raw) == int(flat.numel()) * 4, "numerical")
    return raw


def _matrix_digest(matrix, torch, tag):
    value = hashlib.sha256()
    value.update(f"{tag}:{int(matrix.shape[0])}x{int(matrix.shape[1])}:{PRECISION}:".encode("ascii"))
    value.update(_tensor_bytes(matrix, torch))
    return value.hexdigest()


def _tensor_set_digest(artifact, torch):
    value = hashlib.sha256()
    for name in TENSOR_NAMES:
        value.update(_matrix_digest(getattr(artifact, name), torch, name).encode("ascii"))
    return value.hexdigest()


def corpus_digest(hidden, embeddings, *, turn_ids=None, torch_module=None):
    """Digest of the calibration rows and the ordered turn ids they came from.

    The digest binds content, so a different corpus cannot be presented under a
    saved artifact, and it stores no row and no turn row count that could be
    inverted into a hidden state.
    """
    torch = torch_module or _torch(hidden)
    require(hidden.ndim == 2 and embeddings.ndim == 2 and hidden.shape == embeddings.shape, "profile")
    require(int(hidden.shape[0]) > 0 and int(hidden.shape[1]) > 2, "profile")
    ids = () if turn_ids is None else tuple(turn_ids)
    require(len(ids) <= MAX_CORPUS_TURNS, "profile")
    require(all(_identifier(value) for value in ids), "profile")
    require(len(set(ids)) == len(ids), "profile")
    value = {"domain": CORPUS_DIGEST_DOMAIN, "rows": int(hidden.shape[0]),
             "hidden_dim": int(hidden.shape[1]), "dtype": PRECISION,
             "byte_order": _byte_order(), "turn_ids": list(ids),
             "hidden": _matrix_digest(hidden, torch, "hidden"),
             "embeddings": _matrix_digest(embeddings, torch, "embeddings")}
    return hashlib.sha256(_canonical(value)).hexdigest()


def _expected_shapes(rows, hidden_dim):
    return {"mean_hidden": [1, hidden_dim], "mean_embedding": [1, hidden_dim],
            "hidden_residual": [rows, hidden_dim],
            "embedding_residual": [rows, hidden_dim], "gram": [rows, rows]}


def _require_consistent(artifact, torch, *, check_values=True):
    """Every scalar, the tensor shapes and (optionally) the tensor values agree."""
    require(isinstance(artifact, CalibrationArtifact) and artifact.method_id == METHOD_ID, "identity")
    require(_uint(artifact.hidden_dim, 3, 2**20), "profile")
    require(_uint(artifact.rows, MIN_CALIBRATION_ROWS, CALIBRATION_ROWS_LIMIT), "profile")
    require(_finite(artifact.ridge) and artifact.ridge > 0.0, "numerical")
    require(_finite(artifact.ridge_rel) and 0.0 < artifact.ridge_rel < 1.0, "profile")
    require(_finite(artifact.heldout_r2), "numerical")
    shapes = _expected_shapes(artifact.rows, artifact.hidden_dim)
    for name, shape in shapes.items():
        value = getattr(artifact, name)
        require(tuple(int(x) for x in value.shape) == tuple(shape), "profile")
        require(value.dtype == torch.float32, "profile")
        if check_values:
            require(bool(torch.isfinite(value).all()), "numerical")
    binding = artifact.binding
    if binding is not None:
        require(isinstance(binding, CalibrationBinding), "identity")
        require(binding.method_id == artifact.method_id, "identity")
        require(binding.hidden_dim == artifact.hidden_dim and binding.rows == artifact.rows, "profile")
        require((binding.ridge, binding.ridge_rel, binding.heldout_r2)
                == (artifact.ridge, artifact.ridge_rel, artifact.heldout_r2), "identity")
    return artifact


def _binding_header(binding):
    header = dict(binding.scalars())
    require(set(header) == set(BINDING_FIELDS), "profile")
    header["schema"] = ARTIFACT_SCHEMA
    header["byte_order"] = _byte_order()
    header["tensor_dtype"] = PRECISION
    header["tensor_shapes"] = _expected_shapes(binding.rows, binding.hidden_dim)
    return header


def _artifact_digest(header):
    body = {name: value for name, value in header.items() if name not in DIGEST_EXCLUDED}
    return hashlib.sha256(_canonical(body)).hexdigest()


def artifact_digest(artifact, *, torch_module=None):
    """Content digest of a bound artifact: binding, shapes and tensor values.

    O(tensor bytes): this reads every tensor once and is not part of describe().
    """
    torch = torch_module or _torch(None)
    _require_consistent(artifact, torch)
    require(artifact.binding is not None, "profile")
    header = _binding_header(artifact.binding)
    header["tensor_digest"] = _tensor_set_digest(artifact, torch)
    return _artifact_digest(header)
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
    """R^2 of the frozen map on rows it was not fitted on, against the mean.

    Ridge here is absolute; use absolute_ridge() to derive it from a corpus.
    """
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
                    min_heldout_r2=MIN_HELDOUT_R2, provenance=None, turn_ids=None,
                    torch_module=None):
    """Fit and freeze the transform. Refuses a corpus that does not predict.

    provenance is optional so an offline study can fit without model identity,
    but such an artifact cannot be serialized: a saved artifact always carries
    the full binding, including the digest of the rows it was fitted on.
    """
    torch = torch_module or _torch(hidden)
    require(provenance is None or isinstance(provenance, CalibrationProvenance), "profile")
    require(turn_ids is None or provenance is not None, "profile")
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
        # A rank-deficient Gram is expected when rows exceed the hidden width: its
        # zero eigenvalues carry float32 noise of order eps * lambda_max, which can
        # be far above eps * max|G|. The ridge makes the solve positive definite
        # regardless, so the guard only has to reject a Gram that is PSD-broken.
        values = torch.linalg.eigvalsh(gram)
        largest = float(values.max())
        require(largest > 0.0 and float(values.min()) >= -1e-4 * largest, "numerical")
    binding = None
    if provenance is not None:
        binding = CalibrationBinding(
            method_id=METHOD_ID, hidden_dim=int(dim), rows=int(rows), layer_index=provenance.layer_index,
            ridge=float(ridge), ridge_rel=float(ridge_rel), heldout_r2=float(quality),
            model_fingerprint=provenance.model_fingerprint,
            checkpoint_revision=provenance.checkpoint_revision,
            tokenizer_fingerprint=provenance.tokenizer_fingerprint,
            embedding_id=provenance.embedding_id,
            capture_method_id=provenance.capture_method_id,
            predictor_position_offset=provenance.predictor_position_offset,
            precision=provenance.precision,
            corpus_digest=corpus_digest(h, e, turn_ids=turn_ids, torch_module=torch))
    return CalibrationArtifact(method_id=METHOD_ID, hidden_dim=int(dim), rows=int(rows),
                               ridge=float(ridge), ridge_rel=float(ridge_rel),
                               heldout_r2=float(quality),
                               mean_hidden=mu_h, mean_embedding=mu_e,
                               hidden_residual=a, embedding_residual=b, gram=gram,
                               binding=binding)


def apply_calibration(artifact, hidden, target_norm, *, torch_module=None):
    """Map new hidden rows into the embedding space with the frozen transform.

    Only the frozen parameters are used. The applied rows never enter the fit,
    so replacing them changes the output and never changes the artifact.
    """
    torch = torch_module or _torch(hidden)
    _require_consistent(artifact, torch, check_values=False)
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
    """Scalar-only summary; safe for a receipt. Never reads tensor content."""
    summary = {"method_id": artifact.method_id, "rows": artifact.rows,
               "hidden_dim": artifact.hidden_dim, "ridge": artifact.ridge,
               "ridge_rel": artifact.ridge_rel,
               "heldout_r2": artifact.heldout_r2, "bytes": artifact_bytes(artifact),
               "bound": 1 if artifact.binding is not None else 0}
    if artifact.binding is not None:
        summary.update(artifact.binding.scalars())
    return summary



def _payload(artifact, torch):
    buffer = io.BytesIO()
    torch.save({name: getattr(artifact, name).detach().to("cpu").contiguous()
                for name in TENSOR_NAMES}, buffer)
    return buffer.getvalue()


def serialize_artifact(artifact, *, torch_module=None):
    """Bind, hash and encode one artifact. Returns the exact saved byte string."""
    torch = torch_module or _torch(None)
    _require_consistent(artifact, torch)
    require(artifact.binding is not None, "profile")
    header = _binding_header(artifact.binding)
    header["tensor_digest"] = _tensor_set_digest(artifact, torch)
    payload = _payload(artifact, torch)
    header["payload_sha256"] = hashlib.sha256(payload).hexdigest()
    header["payload_bytes"] = len(payload)
    header["artifact_digest"] = _artifact_digest(header)
    require(set(header) == set(HEADER_FIELDS), "profile")
    raw_header = _canonical(header)
    require(len(raw_header) <= MAX_HEADER_BYTES, "quota")
    raw = ARTIFACT_MAGIC + len(raw_header).to_bytes(8, "big") + raw_header + payload
    require(len(raw) <= MAX_ARTIFACT_BYTES, "quota")
    return raw


def _decode_header(raw):
    require(0 < len(raw) <= MAX_HEADER_BYTES, "profile")

    def pairs(items):
        result = {}
        for key, value in items:
            require(type(key) is str and key not in result, "profile")
            result[key] = value
        return result

    def constant(_value):
        require(False, "profile")

    try:
        value = json.loads(raw.decode("ascii"), object_pairs_hook=pairs, parse_constant=constant)
    except LatentError:
        raise
    except Exception:
        raise LatentError("profile") from None
    require(type(value) is dict, "profile")
    return value


def _validate_header(header):
    """Every header field, then the shape binding. Touches no tensor."""
    require(set(header) == set(HEADER_FIELDS), "profile")
    require(header["schema"] == ARTIFACT_SCHEMA, "profile")
    require(header["byte_order"] == _byte_order() and header["tensor_dtype"] == PRECISION, "profile")
    require(_identifier(header["method_id"]), "identity")
    require(_uint(header["hidden_dim"], 3, 2**20), "profile")
    require(_uint(header["rows"], MIN_CALIBRATION_ROWS, CALIBRATION_ROWS_LIMIT), "profile")
    require(header["tensor_shapes"] == _expected_shapes(header["rows"], header["hidden_dim"]), "profile")
    for name in ("tensor_digest", "payload_sha256", "artifact_digest", "corpus_digest"):
        require(_digest(header[name]), "identity")
    require(_uint(header["payload_bytes"], 1, MAX_ARTIFACT_BYTES), "profile")
    return CalibrationBinding(**{name: header[name] for name in BINDING_FIELDS})


def deserialize_artifact(raw, *, expected_method_id=None, expected_hidden_dim=None,
                         expected_provenance=None, expected_artifact_digest=None,
                         torch_module=None):
    """Validate the header in full, then and only then read the tensor payload.

    Every refusal is LatentError: "identity" for a method, provenance or digest
    mismatch, "profile" for a malformed header or shape, "numerical" for a
    non-finite tensor. The method id defaults to this module's METHOD_ID, so a
    per-message artifact is refused rather than silently reinterpreted.
    """
    torch = torch_module or _torch(None)
    require(isinstance(raw, (bytes, bytearray)), "profile")
    raw = bytes(raw)
    require(len(raw) <= MAX_ARTIFACT_BYTES, "quota")
    require(raw[:len(ARTIFACT_MAGIC)] == ARTIFACT_MAGIC and len(raw) >= 16, "profile")
    header_length = int.from_bytes(raw[len(ARTIFACT_MAGIC):16], "big")
    require(0 < header_length <= MAX_HEADER_BYTES and 16 + header_length < len(raw), "profile")
    header = _decode_header(raw[16:16 + header_length])
    binding = _validate_header(header)
    negotiated = METHOD_ID if expected_method_id is None else expected_method_id
    require(_identifier(negotiated), "profile")
    require(header["method_id"] == negotiated, "identity")
    if expected_hidden_dim is not None:
        require(header["hidden_dim"] == expected_hidden_dim, "profile")
    if expected_provenance is not None:
        require(isinstance(expected_provenance, CalibrationProvenance), "profile")
        for name in ("layer_index", "model_fingerprint", "checkpoint_revision",
                     "tokenizer_fingerprint", "embedding_id", "capture_method_id",
                     "predictor_position_offset", "precision"):
            require(header[name] == getattr(expected_provenance, name), "identity")
    require(header["artifact_digest"] == _artifact_digest(header), "identity")
    if expected_artifact_digest is not None:
        require(_digest(expected_artifact_digest)
                and header["artifact_digest"] == expected_artifact_digest, "identity")
    payload = raw[16 + header_length:]
    require(len(payload) == header["payload_bytes"], "identity")
    require(hashlib.sha256(payload).hexdigest() == header["payload_sha256"], "identity")
    try:
        loaded = torch.load(io.BytesIO(payload), weights_only=True, map_location="cpu")
    except Exception:
        raise LatentError("profile") from None
    require(type(loaded) is dict and set(loaded) == set(TENSOR_NAMES), "profile")
    for name in TENSOR_NAMES:
        value = loaded[name]
        require(isinstance(value, torch.Tensor) and value.dtype == torch.float32, "profile")
        require(list(int(x) for x in value.shape) == header["tensor_shapes"][name], "profile")
        require(bool(torch.isfinite(value).all()), "numerical")
    artifact = CalibrationArtifact(method_id=header["method_id"], hidden_dim=header["hidden_dim"],
                                   rows=header["rows"], ridge=header["ridge"],
                                   ridge_rel=header["ridge_rel"], heldout_r2=header["heldout_r2"],
                                   mean_hidden=loaded["mean_hidden"],
                                   mean_embedding=loaded["mean_embedding"],
                                   hidden_residual=loaded["hidden_residual"],
                                   embedding_residual=loaded["embedding_residual"],
                                   gram=loaded["gram"], binding=binding)
    _require_consistent(artifact, torch)
    require(_tensor_set_digest(artifact, torch) == header["tensor_digest"], "identity")
    return artifact
