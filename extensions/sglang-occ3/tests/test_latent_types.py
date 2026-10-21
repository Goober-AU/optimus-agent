"""Stdlib contract tests; no installed SGLang, Torch, GPU or model."""
from dataclasses import asdict, replace
import math
import pytest

from occ3_sglang.latent_types import (LatentError, RuntimeProfile, bounded_payload,
    control_request, validate_control_payload, decode_descriptor)
from occ3_sglang.attachment_contract import CaptureWorkerDescriptor


def profile(**changes):
    return RuntimeProfile("epoch", 9, "model-hash", "glm", **changes)


def capture(**changes):
    values = dict(attempt_id="attempt", reservation_id="reserve", native_rid="rid",
        principal_digest="a" * 64, runtime_epoch="epoch", model_fingerprint="model-hash",
        method_id=profile().method, deadline=1000.0, capture_id="capture", source_session_id="source",
        source_turn_nonce="turn", runtime_generation=9, span_policy="whole_completed_response_tail_v1",
        max_prefix_tokens=8, max_capture_bytes=128 * 1024 * 1024)
    values.update(changes)
    return CaptureWorkerDescriptor(**values)


@pytest.mark.parametrize("value", [object(), {"x": object()}, {"x": float("nan")}, {"x": 2**64}, [0]*129])
def test_no_arbitrary_objects_in_control(value):
    with pytest.raises(LatentError):
        bounded_payload(value)


def test_exact_control_envelope():
    req = control_request("id", "health", {})
    assert validate_control_payload(req) == req
    with pytest.raises(LatentError):
        validate_control_payload({**req, "tokens": [1]})
    reply = dict(schema="occ3.control.result.v1", command_id="id", ok=True, result={}, error="")
    assert validate_control_payload(reply, result=True) == reply
    with pytest.raises(LatentError):
        validate_control_payload({**reply, "error": "oops"}, result=True)


def test_descriptor_is_chat_canonical():
    original = capture()
    assert decode_descriptor(asdict(original)) == original
    assert decode_descriptor(None) is None
    assert profile().tile_rows == 256
    with pytest.raises(LatentError):
        profile(expected_ranks=1)
    with pytest.raises(LatentError):
        profile(max_active=2)


@pytest.mark.parametrize("field", ["max_active","max_records","max_live_bytes","workspace_bytes","tile_rows"])
def test_profile_boolean_not_integer(field):
    with pytest.raises((LatentError,ValueError)):
        profile(**{field:True})
