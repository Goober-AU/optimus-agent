"""Wire/proof parsing tests. Synthetic evidence never proves CUDA use."""
import json
from dataclasses import replace

import pytest

from occ3_sglang.attachment_contract import (
    AttachmentError, CaptureIntent, InferenceAttachment, MAX_RECEIPT_BYTES,
    RECEIPT_HEADER, canonical, parse_opt_in,
)
from occ3_sglang.use_receipts import NativeOutputAudit, receipt_bytes
from test_chat_attachment import attachment, call, harness


@pytest.mark.parametrize("change", [
    {"protocol_version": True}, {"protocol_version": 2}, {"runtime_generation": -1},
    {"runtime_generation": 2**64}, {"expires_at_epoch": 1.5},
    {"state_handle": "bad secret space"}, {"state_handle": "a" * 257},
    {"message_id": "x\r\n"}, {"method": "é"}, {"request_id": ""},
])
def test_typed_wire_rejects_invalid_fields(change):
    with pytest.raises(AttachmentError): attachment(**change)


@pytest.mark.parametrize("kind", ["null", "unknown", "duplicate", "root_duplicate", "missing", "capture"])
def test_strict_opt_in(kind):
    obj = attachment().wire()
    if kind == "unknown": obj["source_text"] = "must not be accepted"
    if kind == "missing": del obj["message_id"]
    body = canonical({"occ3": obj})
    if kind == "null": body = b'{"occ3":null}'
    if kind == "duplicate": body = body.replace(b'"protocol_version":1', b'"protocol_version":1,"protocol_version":1')
    if kind == "root_duplicate": body = b'{"model":"x","model":"y","occ3":' + canonical(obj) + b'}'
    if kind == "capture": body = b'{"occ3_capture":{}}'
    with pytest.raises(AttachmentError): parse_opt_in(body)


def test_escaped_opt_in_key_is_not_silently_ignored():
    body = rb'{"occ\u0033":' + canonical(attachment().wire()) + b'}'
    assert parse_opt_in(body) is not None


def test_shared_receipt_size_limit_before_generation():
    assert MAX_RECEIPT_BYTES == 2048
    fields = attachment().wire()
    for key in fields:
        if isinstance(fields[key], str): fields[key] = "a" * 256
    with pytest.raises(AttachmentError, match="buffer_limit"):
        receipt_bytes(InferenceAttachment.parse(fields))


@pytest.mark.asyncio
@pytest.mark.parametrize("field,value", [
    ("attempt_id", "wrong"), ("attachment_digest", "wrong"), ("native_rid", "wrong"),
    ("principal_digest", "wrong"), ("runtime_id", "wrong"), ("runtime_generation", 0),
    ("model_fingerprint", "wrong"), ("receiver_session_id", "wrong"),
    ("source_capture_commit_id", ""), ("injection_commit_id", ""),
    ("output_sha256", "wrong"), ("injected_prefix_count", 0), ("injected_prefix_count", True),
    ("ready_ranks", 7), ("expected_ranks", 0), ("completed", False),
    ("fallback_used", True), ("num_retractions", 1), ("private_cache_namespace", False),
    ("gpu_resident", False),
])
async def test_exact_terminal_evidence_required(harness, field, value):
    harness.backend.mutate_evidence = lambda e: replace(e, **{field: value})
    response, _, _ = await call(harness)
    assert response.status_code != 200
    assert RECEIPT_HEADER not in response.headers
    assert len(harness.tm.calls) == 1 and len(harness.backend.retires) == 1


def event(rid="r", finish=None, delta=None):
    return b"data: " + canonical({"id": rid, "choices": [{"index": 0, "delta": delta or {}, "finish_reason": finish}]}) + b"\n\n"


@pytest.mark.parametrize("body", [
    b"data: [DONE]\n\n", event(finish="stop"),
    event(finish="stop") + b"data: [DONE]garbage\n\n",
    event(finish="stop") + b"data: [DONE]\n\ndata: [DONE]\n\n",
    event(finish="stop") + b'data: {"error":{"message":"failed"}}\n\ndata: [DONE]\n\n',
    event(finish="error") + b"data: [DONE]\n\n",
    event(finish="stop") + event(rid="other") + b"data: [DONE]\n\n",
    event(finish="stop") + event(delta={"content": "after-finish"}) + b"data: [DONE]\n\n",
])
def test_invalid_sse_cannot_prove_completion(body):
    audit = NativeOutputAudit(65536)
    with pytest.raises(AttachmentError):
        audit.feed(body)
        audit.finish("r")


def test_sse_arbitrary_byte_splits_and_exact_digest():
    import hashlib
    body = event(delta={"reasoning_content": "before tool"}) + event(finish="tool_calls") + b"data: [DONE]\n\n"
    audit = NativeOutputAudit(65536)
    for byte in body: audit.feed(bytes([byte]))
    result = audit.finish("r")
    assert result.output_sha256 == hashlib.sha256(body).hexdigest()
    assert result.output_bytes == len(body)


@pytest.mark.asyncio
async def test_native_error_plus_done_is_not_success(harness):
    harness.chat.stream_tail = ['data: {"error":{"message":"not success"}}\n\n', 'data: [DONE]\n\n']
    response, _, _ = await call(harness)
    assert response.status_code != 200
    assert RECEIPT_HEADER not in response.headers
    assert not harness.backend.finishes


def test_capture_intent_is_owner_only_negotiated_directive():
    intent = CaptureIntent("capture", "source-session", "source-request", "turn", "model", "runtime",
                           1, "statebridge", 128, 128 * 1024 * 1024, 1000)
    assert "source-session" not in repr(intent)
    with pytest.raises(AttachmentError):
        replace(intent, retained_count_limit=129)
    with pytest.raises(AttachmentError):
        replace(intent, span_policy="guessed-tool-substring")
