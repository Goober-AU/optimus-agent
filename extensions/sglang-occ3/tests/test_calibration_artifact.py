"""Artifact integrity and frozen-transform tests for the calibrated map."""
from __future__ import annotations

import functools
import json

import pytest

torch = pytest.importorskip("torch")


def _model(rank=8, dim=64, seed=1000):
    generator = torch.Generator().manual_seed(seed)
    return (torch.randn(rank, dim, generator=generator) / rank ** 0.5,
            torch.randn(rank, dim, generator=generator) / rank ** 0.5)


def _sample(model, rows=200, kappa=0.9, seed=1):
    left, right = model
    generator = torch.Generator().manual_seed(seed)
    factor = torch.randn(rows, left.shape[0], generator=generator)
    noise_h = torch.randn(rows, left.shape[1], generator=generator) * (1.0 - kappa) ** 0.5
    noise_e = torch.randn(rows, right.shape[1], generator=generator) * (1.0 - kappa) ** 0.5
    return kappa ** 0.5 * (factor @ left) + noise_h, kappa ** 0.5 * (factor @ right) + noise_e


def _provenance():
    from occ3_sglang.statebridge_calibrated import CalibrationProvenance
    return CalibrationProvenance(
        layer_index=77, model_fingerprint="sha256:" + "a" * 64, checkpoint_revision="rev-1",
        tokenizer_fingerprint="sha256:" + "b" * 64, embedding_id="glm53-tp8-vocab",
        capture_method_id="glm53.final-prenorm.capture.v1")


@functools.lru_cache(maxsize=None)
def _fitted(rows=200, dim=64, seed=1, provenance=True):
    """One frozen artifact per configuration; the tests only read it."""
    from occ3_sglang.statebridge_calibrated import fit_calibration
    hidden, embeddings = _sample(_model(dim=dim), rows=rows, seed=seed)
    return fit_calibration(hidden, embeddings, provenance=_provenance() if provenance else None,
                           turn_ids=["turn-1", "turn-2"] if provenance else None)


def _split(raw):
    from occ3_sglang.statebridge_calibrated import ARTIFACT_MAGIC
    length = int.from_bytes(raw[len(ARTIFACT_MAGIC):16], "big")
    return json.loads(raw[16:16 + length].decode("ascii")), raw[16 + length:]


def _rebuild(header, payload):
    """Re-encode a header and recompute its artifact digest, as a correct writer would."""
    from occ3_sglang.statebridge_calibrated import ARTIFACT_MAGIC, _artifact_digest, _canonical
    header = dict(header)
    header["artifact_digest"] = _artifact_digest(header)
    raw_header = _canonical(header)
    return ARTIFACT_MAGIC + len(raw_header).to_bytes(8, "big") + raw_header + payload


def _raw(header, payload):
    """Encode a header verbatim, without recomputing any digest."""
    from occ3_sglang.statebridge_calibrated import ARTIFACT_MAGIC, _canonical
    raw_header = _canonical(header)
    return ARTIFACT_MAGIC + len(raw_header).to_bytes(8, "big") + raw_header + payload


def test_round_trip_preserves_the_frozen_transform():
    from occ3_sglang.statebridge_calibrated import (artifact_digest, deserialize_artifact,
                                                   serialize_artifact)
    artifact = _fitted()
    raw = serialize_artifact(artifact)
    assert serialize_artifact(artifact) == raw
    back = deserialize_artifact(raw, expected_method_id=artifact.method_id,
                               expected_hidden_dim=artifact.hidden_dim,
                               expected_provenance=_provenance(),
                               expected_artifact_digest=artifact_digest(artifact))
    assert (back.rows, back.hidden_dim, back.ridge, back.ridge_rel, back.heldout_r2) == (
        artifact.rows, artifact.hidden_dim, artifact.ridge, artifact.ridge_rel, artifact.heldout_r2)
    for name in ("mean_hidden", "mean_embedding", "hidden_residual", "embedding_residual", "gram"):
        assert torch.equal(getattr(back, name), getattr(artifact, name))
    message, _ = _sample(_model(dim=64), rows=16, seed=9)
    from occ3_sglang.statebridge_calibrated import apply_calibration
    assert torch.equal(apply_calibration(back, message, 2.0), apply_calibration(artifact, message, 2.0))


def test_header_carries_every_required_binding_field():
    from occ3_sglang.statebridge_calibrated import BINDING_FIELDS, HEADER_FIELDS, serialize_artifact
    header, _ = _split(serialize_artifact(_fitted()))
    required = {"method_id", "hidden_dim", "layer_index", "rows", "ridge_rel", "ridge",
                "heldout_r2", "model_fingerprint", "checkpoint_revision",
                "tokenizer_fingerprint", "embedding_id", "capture_method_id",
                "predictor_position_offset", "precision", "corpus_digest", "artifact_digest"}
    assert required <= set(header) == set(HEADER_FIELDS)
    assert required - {"artifact_digest"} <= set(BINDING_FIELDS)


def test_refuses_a_mismatched_method_id():
    from occ3_sglang.latent_types import METHOD, LatentError
    from occ3_sglang.statebridge_calibrated import (CalibrationBinding, deserialize_artifact,
                                                    serialize_artifact)
    artifact = _fitted()
    raw = serialize_artifact(artifact)
    with pytest.raises(LatentError) as caught:
        deserialize_artifact(raw, expected_method_id=METHOD)
    assert caught.value.code == "identity"
    header, payload = _split(raw)
    forged = dict(header, method_id=METHOD)
    with pytest.raises(LatentError) as caught:
        deserialize_artifact(_rebuild(forged, payload))
    assert caught.value.code == "identity"
    scalars = dict(artifact.binding.scalars(), method_id=METHOD)
    with pytest.raises(LatentError) as caught:
        CalibrationBinding(**scalars)
    assert caught.value.code == "identity"


def test_refuses_a_mismatched_hidden_dim():
    from occ3_sglang.latent_types import LatentError
    from occ3_sglang.statebridge_calibrated import deserialize_artifact, serialize_artifact
    artifact = _fitted()
    raw = serialize_artifact(artifact)
    with pytest.raises(LatentError) as caught:
        deserialize_artifact(raw, expected_hidden_dim=artifact.hidden_dim + 1)
    assert caught.value.code == "profile"
    header, payload = _split(raw)
    with pytest.raises(LatentError) as caught:
        deserialize_artifact(_rebuild(dict(header, hidden_dim=artifact.hidden_dim + 1), payload))
    assert caught.value.code == "profile"


@pytest.mark.parametrize("field", ["model_fingerprint", "checkpoint_revision",
                                   "tokenizer_fingerprint", "embedding_id", "capture_method_id"])
def test_refuses_each_mismatched_provenance_field(field):
    from occ3_sglang.latent_types import LatentError
    from occ3_sglang.statebridge_calibrated import CalibrationProvenance, deserialize_artifact, serialize_artifact
    artifact = _fitted()
    raw = serialize_artifact(artifact)
    values = dict(layer_index=77, model_fingerprint="sha256:" + "a" * 64, checkpoint_revision="rev-1",
                  tokenizer_fingerprint="sha256:" + "b" * 64, embedding_id="glm53-tp8-vocab",
                  capture_method_id="glm53.final-prenorm.capture.v1")
    values[field] = "other-value-1"
    with pytest.raises(LatentError) as caught:
        deserialize_artifact(raw, expected_provenance=CalibrationProvenance(**values))
    assert caught.value.code == "identity"


def test_refuses_a_layer_mismatch():
    from occ3_sglang.latent_types import LatentError
    from occ3_sglang.statebridge_calibrated import CalibrationProvenance, deserialize_artifact, serialize_artifact
    raw = serialize_artifact(_fitted())
    values = dict(layer_index=78, model_fingerprint="sha256:" + "a" * 64, checkpoint_revision="rev-1",
                  tokenizer_fingerprint="sha256:" + "b" * 64, embedding_id="glm53-tp8-vocab",
                  capture_method_id="glm53.final-prenorm.capture.v1")
    with pytest.raises(LatentError) as caught:
        deserialize_artifact(raw, expected_provenance=CalibrationProvenance(**values))
    assert caught.value.code == "identity"


def test_refuses_a_tampered_or_missing_digest():
    from occ3_sglang.latent_types import LatentError
    from occ3_sglang.statebridge_calibrated import (artifact_digest, deserialize_artifact,
                                                    serialize_artifact)
    artifact = _fitted()
    raw = serialize_artifact(artifact)
    digest = artifact_digest(artifact)
    header, payload = _split(raw)
    forged = dict(header, artifact_digest=("0" if digest[0] != "0" else "1") + digest[1:])
    with pytest.raises(LatentError) as caught:
        deserialize_artifact(_raw(forged, payload))
    assert caught.value.code == "identity"
    missing = dict(header)
    del missing["artifact_digest"]
    raw_header = json.dumps(missing, sort_keys=True, separators=(",", ":")).encode("ascii")
    from occ3_sglang.statebridge_calibrated import ARTIFACT_MAGIC
    with pytest.raises(LatentError) as caught:
        deserialize_artifact(ARTIFACT_MAGIC + len(raw_header).to_bytes(8, "big") + raw_header + payload)
    assert caught.value.code == "profile"
    tampered_payload = payload[:-1] + bytes([payload[-1] ^ 0xFF])
    with pytest.raises(LatentError) as caught:
        deserialize_artifact(_rebuild(header, tampered_payload))
    assert caught.value.code == "identity"
    with pytest.raises(LatentError) as caught:
        deserialize_artifact(raw, expected_artifact_digest="f" * 64)
    assert caught.value.code == "identity"


def test_refuses_a_resaved_artifact_under_a_pinned_digest():
    """A forged artifact that re-digests itself is still refused by the pinned digest."""
    from occ3_sglang.latent_types import LatentError
    from occ3_sglang.statebridge_calibrated import artifact_digest, deserialize_artifact, serialize_artifact
    original = _fitted(seed=1)
    other = _fitted(seed=2)
    raw_other = serialize_artifact(other)
    with pytest.raises(LatentError) as caught:
        deserialize_artifact(raw_other, expected_artifact_digest=artifact_digest(original))
    assert caught.value.code == "identity"


def test_refuses_a_shape_mismatch_between_gram_and_hidden_dim():
    from occ3_sglang.latent_types import LatentError
    from occ3_sglang.statebridge_calibrated import deserialize_artifact, serialize_artifact
    artifact = _fitted()
    raw = serialize_artifact(artifact)
    header, payload = _split(raw)
    shapes = dict(header["tensor_shapes"], gram=[artifact.rows, artifact.rows + 1])
    with pytest.raises(LatentError) as caught:
        deserialize_artifact(_rebuild(dict(header, tensor_shapes=shapes), payload))
    assert caught.value.code == "profile"
    shapes = dict(header["tensor_shapes"], hidden_residual=[artifact.rows, artifact.hidden_dim + 8])
    with pytest.raises(LatentError) as caught:
        deserialize_artifact(_rebuild(dict(header, tensor_shapes=shapes), payload))
    assert caught.value.code == "profile"


def test_payload_is_read_without_pickle(monkeypatch):
    from occ3_sglang.statebridge_calibrated import deserialize_artifact, serialize_artifact
    seen = {}
    original = torch.load

    def recording(*args, **kwargs):
        seen.update(kwargs)
        return original(*args, **kwargs)

    monkeypatch.setattr(torch, "load", recording)
    deserialize_artifact(serialize_artifact(_fitted()))
    assert seen["weights_only"] is True and seen["map_location"] == "cpu"


def test_serialize_refuses_an_unbound_artifact():
    from occ3_sglang.latent_types import LatentError
    from occ3_sglang.statebridge_calibrated import serialize_artifact
    with pytest.raises(LatentError) as caught:
        serialize_artifact(_fitted(provenance=False))
    assert caught.value.code == "profile"


def test_describe_is_scalar_only_and_never_carries_tensor_content():
    from occ3_sglang.statebridge_calibrated import describe
    bound = describe(_fitted())
    unbound = describe(_fitted(provenance=False))
    for summary in (bound, unbound):
        assert all(type(value) in (int, float, str) for value in summary.values())
        assert all(name not in ("mean_hidden", "mean_embedding", "hidden_residual",
                                "embedding_residual", "gram") for name in summary)
    assert bound["bound"] == 1 and unbound["bound"] == 0
    assert bound["corpus_digest"] != unbound.get("corpus_digest")
    assert {"method_id", "rows", "hidden_dim", "ridge", "ridge_rel", "heldout_r2",
            "bytes"} <= set(bound)
    assert len(bound["corpus_digest"]) == 64


def test_apply_uses_the_frozen_transform():
    """Only the fitted rows change the artifact; applied rows never do."""
    from occ3_sglang.statebridge_calibrated import (apply_calibration, artifact_digest,
                                                    deserialize_artifact, serialize_artifact)
    first = _fitted(seed=1)
    same = _fitted(seed=1)
    other = _fitted(seed=2)
    assert artifact_digest(first) == artifact_digest(same)
    assert artifact_digest(first) != artifact_digest(other)
    assert first.binding.corpus_digest != other.binding.corpus_digest
    before = serialize_artifact(first)
    message_a, _ = _sample(_model(dim=64), rows=24, seed=41)
    message_b, _ = _sample(_model(dim=64), rows=24, seed=42)
    output_a = apply_calibration(first, message_a, 1.5)
    output_b = apply_calibration(first, message_b, 1.5)
    assert serialize_artifact(first) == before
    assert not torch.equal(output_a, output_b)
    assert torch.equal(apply_calibration(first, message_a, 1.5), output_a)
    weights = torch.cholesky_solve(
        ((message_a - first.mean_hidden) @ first.hidden_residual.mT).mT,
        torch.linalg.cholesky(first.gram + first.ridge * torch.eye(first.rows))).mT
    manual = first.mean_embedding + weights @ first.embedding_residual
    manual = manual * (1.5 / manual.norm(dim=-1, keepdim=True))
    assert torch.allclose(output_a, manual, atol=1e-5)
    restored = deserialize_artifact(before)
    assert torch.equal(apply_calibration(restored, message_a, 1.5), output_a)


def test_corpus_digest_binds_the_rows_and_the_turn_ids():
    from occ3_sglang.latent_types import LatentError
    from occ3_sglang.statebridge_calibrated import corpus_digest
    hidden, embeddings = _sample(_model(dim=64), rows=64, seed=71)
    other, _ = _sample(_model(dim=64), rows=64, seed=72)
    base = corpus_digest(hidden, embeddings, turn_ids=["turn-1"])
    assert corpus_digest(hidden.clone(), embeddings.clone(), turn_ids=["turn-1"]) == base
    assert corpus_digest(hidden, embeddings, turn_ids=["turn-1", "turn-2"]) != base
    assert corpus_digest(other, embeddings, turn_ids=["turn-1"]) != base
    assert corpus_digest(hidden, embeddings) != base
    with pytest.raises(LatentError) as caught:
        corpus_digest(hidden, embeddings, turn_ids=["turn-1", "turn-1"])
    assert caught.value.code == "profile"
    with pytest.raises(LatentError):
        corpus_digest(hidden, embeddings, turn_ids=["turn 1"])
    with pytest.raises(LatentError):
        corpus_digest(hidden, embeddings[:-1])


def test_binding_refuses_an_unbound_or_mismatched_scalar():
    from occ3_sglang.latent_types import LatentError
    from occ3_sglang.statebridge_calibrated import CalibrationBinding
    scalars = dict(_fitted().binding.scalars())
    CalibrationBinding(**scalars)
    for changes in ({"rows": 63}, {"hidden_dim": 2}, {"ridge": 0.0}, {"ridge_rel": 1.0},
                    {"heldout_r2": float("nan")}, {"corpus_digest": "short"},
                    {"precision": "bfloat16"}, {"predictor_position_offset": -2},
                    {"model_fingerprint": ""}):
        with pytest.raises(LatentError):
            CalibrationBinding(**dict(scalars, **changes))
