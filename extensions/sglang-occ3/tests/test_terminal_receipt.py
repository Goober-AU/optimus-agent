"""Terminal receipt mode tests: offline, format-only, no GPU/SGLang install.

Covers the negotiated occ3_receipt_mode=terminal-sse-v1 server path: strict
admission, byte-preserving passthrough, single private event, held DONE,
backend-proof gating, cancellation windows and quota lifetime. Synthetic
backend fixtures are NOT real device evidence. Ordinary/legacy behavior is
regression-tested by the existing suites.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import time
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

from occ3_sglang.attachment_contract import (
    AttachmentError, TERMINAL_ACK_HEADER, TERMINAL_MODE, canonical, parse_terminal_mode,
)
from occ3_sglang.chat_adapter import ChatAttachmentAdapter, BufferProfile, TerminalProfile
from occ3_sglang.use_receipts import (
    TerminalOutputAudit, TerminalUseEvidence, parse_terminal_event, terminal_envelope,
    terminal_event_bytes,
)
from test_chat_attachment import FixtureBackend, attachment, chat_request, harness, raw_request
from test_chat_source_capture import SourceFixtureBackend, intent, source_call, source_raw
from test_native_startup_patch import _load_patched_glm47

# The source harness fixture lives in test_chat_source_capture; importing the
# factory keeps the same pinned converter/backend stubs.
from test_chat_source_capture import source_harness  # noqa: F401  (fixture import)


@pytest.fixture(autouse=True)
def patched_glm47_chain():
    """Install the prepared patched detector (strict overlay) for the whole suite.

    Nothing here imports a real SGLang runtime or touches a real checkout: the
    module chain is compiled from the pinned snapshot bytes with stubbed deps.
    """
    import sys as _sys
    saved = {k: v for k, v in _sys.modules.items()
             if k.startswith(("sglang", "orjson", "partial_json_parser", "xgrammar"))}
    from test_native_startup_patch import _install_glm47_chain as _install
    glm = _install()
    yield glm
    for key in [k for k in _sys.modules if k.startswith(("sglang", "orjson", "partial_json_parser", "xgrammar"))]:
        del _sys.modules[key]
    _sys.modules.update(saved)

FIXTURE = json.loads((Path(__file__).parent / "fixtures/occ3_terminal_v1.json").read_text())
DONE = bytes.fromhex(FIXTURE["done_frame_hex"])


def terminal_profile(**kw):
    base = dict(max_frame_bytes=4096, max_native_bytes=1 << 20,
                max_json_depth=16, max_json_nodes=4096, max_terminal_active=2)
    base.update(kw)
    return TerminalProfile(**base)


def receiver_terminal_body(a=None):
    value = {"occ3": (a or attachment()).wire(), "occ3_receipt_mode": TERMINAL_MODE}
    return canonical(value)


@pytest.fixture
def terminal_harness(harness):
    harness.adapter.terminal_profile = terminal_profile()
    return harness


def terminal_call(h, a=None, request=None, body=None):
    request = request or chat_request()
    raw, _ = raw_request(body if body is not None else receiver_terminal_body(a))
    raw.scope["headers"].append((b"accept-encoding", b"identity"))
    return raw


async def drive_terminal(h, raw, request=None):
    """Run handle_request and collect the streamed client bytes."""
    response = await h.chat.handle_request(request or chat_request(), raw)
    assert response.status_code == 200
    assert response.headers.get(TERMINAL_ACK_HEADER) == TERMINAL_MODE
    assert response.headers.get("content-encoding") == "identity"
    sent = []

    async def send(event):
        sent.append(event)

    await response(raw.scope, raw.receive, send)
    body = b"".join(event.get("body", b"") for event in sent if event["type"] == "http.response.body")
    return response, sent, body


class GatedChat:
    """Pinned-harness chat whose native stream can be paused mid-flight."""


def test_fixture_canonical_bytes_and_hashes():
    for kind in ("receiver", "source"):
        case = FIXTURE[kind]
        P = bytes.fromhex(case["p_hex"])
        body = P + DONE
        assert hashlib.sha256(body).hexdigest() == case["body_sha256"]
        assert len(body) == case["native_body_bytes"]
        assert len(DONE) == FIXTURE["done_frame_len"] == 14
        event = bytes.fromhex(case["event_hex"])
        assert event.startswith(b"event: optimus.occ3.receipt\ndata: ")
        assert event.endswith(b"\n\n")
        envelope = parse_terminal_event(event)
        assert envelope["body_sha256"] == case["body_sha256"]
        assert envelope["native_body_bytes"] == case["native_body_bytes"]
        assert canonical(envelope) == case["envelope_canonical"].encode("ascii")


def test_mode_field_strict_negatives():
    assert parse_terminal_mode(b"{}") is None
    assert parse_terminal_mode(b'{"occ3":{"x":1}}') is None
    with pytest.raises(AttachmentError):
        parse_terminal_mode(b'{"occ3_receipt_mode":"header-v1"}')
    with pytest.raises(AttachmentError):
        parse_terminal_mode(b'{"occ3_receipt_mode":1}')
    with pytest.raises(AttachmentError):
        parse_terminal_mode(b'{"occ3_receipt_mode":"a","occ3_receipt_mode":"terminal-sse-v1"}')
    with pytest.raises(AttachmentError):
        parse_terminal_mode(b'{"occ3_receipt_mode":null}')


@pytest.mark.asyncio
async def test_terminal_mode_without_profile_refuses_before_dispatch(terminal_harness):
    terminal_harness.adapter.terminal_profile = None
    raw = terminal_call(terminal_harness)
    response = await terminal_harness.chat.handle_request(chat_request(), raw)
    assert response.status_code == 503
    assert not terminal_harness.tm.calls and not terminal_harness.backend.reserves


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["no_attachment", "non_stream", "encoding", "encoding_absent",
                                    "logprobs", "output_ids", "stream_usage", "continuous_usage",
                                    "hidden_states", "meta_info"])
async def test_terminal_admission_refusals(terminal_harness, change):
    h = terminal_harness
    request = chat_request()
    body = receiver_terminal_body()
    encoding = b"identity"
    if change == "no_attachment":
        body = canonical({"occ3_receipt_mode": TERMINAL_MODE})
    if change == "non_stream":
        request = chat_request(stream=False)
    if change == "encoding":
        encoding = b"gzip"
    if change == "encoding_absent":
        encoding = None
    if change == "logprobs":
        request.logprobs = True
    if change == "output_ids":
        request.return_output_ids_in_sglext = True
    if change == "stream_usage":
        request.stream_options = NS(include_usage=True, continuous_usage_stats=False)
    if change == "continuous_usage":
        request.stream_options = NS(include_usage=False, continuous_usage_stats=True)
    if change == "hidden_states":
        request.return_hidden_states = True
    if change == "meta_info":
        request.return_meta_info = True
    raw, _ = raw_request(body)
    if encoding is not None:
        raw.scope["headers"].append((b"accept-encoding", encoding))
    response = await h.chat.handle_request(request, raw)
    assert response.status_code != 200
    assert not h.tm.calls and not h.backend.reserves


@pytest.mark.asyncio
async def test_terminal_receiver_flow_bytes_event_and_done(terminal_harness):
    h = terminal_harness
    raw = terminal_call(h)
    response, sent, body = await drive_terminal(h, raw)
    all_frames = b"".join(h.chat.frames)
    # chat.frames includes P and the trailing DONE; P is everything before DONE.
    P = all_frames[:-len(DONE)]
    # P forwarded unchanged and in order; then one private event; then exact D; clean EOF.
    assert body.startswith(P)
    event = body[len(P):-len(DONE)]
    assert body[-len(DONE):] == DONE
    envelope = parse_terminal_event(event)
    assert envelope["kind"] == "receiver"
    assert envelope["native_rid"] == h.tm.calls[0].rid
    assert envelope["body_sha256"] == hashlib.sha256(P + DONE).hexdigest()
    assert envelope["native_body_bytes"] == len(P) + len(DONE)
    assert envelope["receipt"]["consumed"] is True
    assert envelope["receipt"]["fallback_used"] is False
    # Exactly one backend finish with the same digest; no aborts; quota released.
    assert len(h.backend.finishes) == 1
    assert h.backend.finishes[0].output_sha256 == envelope["body_sha256"]
    assert not h.tm.aborts
    assert h.adapter._active == 0
    assert h.adapter._terminal_active == 0
    assert h.adapter._terminal_attempts == set()


@pytest.mark.asyncio
async def test_terminal_suppression_until_backend_proof(terminal_harness):
    h = terminal_harness
    gate = h.backend.finish_gate = asyncio.Event()
    raw = terminal_call(h)
    response = await h.chat.handle_request(chat_request(), raw)
    sent = []

    async def send(event):
        sent.append(event)

    task = asyncio.create_task(response(raw.scope, raw.receive, send))
    for _ in range(200):
        if h.backend.finishes:
            break
        await asyncio.sleep(0)
    # Backend proof pending: original P bytes may flow, but no DONE and no event yet.
    body_so_far = b"".join(e.get("body", b"") for e in sent if e["type"] == "http.response.body")
    assert DONE not in body_so_far
    assert b"optimus.occ3.receipt" not in body_so_far
    assert len(h.backend.finishes) == 1
    gate.set()
    await task
    body = b"".join(e.get("body", b"") for e in sent if e["type"] == "http.response.body")
    assert body.count(DONE) == 1 and b"event: optimus.occ3.receipt" in body


@pytest.mark.asyncio
async def test_terminal_disconnect_midstream_no_event_no_done(terminal_harness):
    h = terminal_harness
    raw = terminal_call(h)
    response = await h.chat.handle_request(chat_request(), raw)

    gate = h.tm.first_gate = asyncio.Event()

    async def receive():
        await gate.wait()
        return {"type": "http.disconnect"}

    sent = []

    async def send(event):
        sent.append(event)

    task = asyncio.create_task(response(raw.scope, receive, send))
    for _ in range(200):
        if sent:
            break
        await asyncio.sleep(0)
    gate.set()
    await task
    # Starlette cancels the stream on disconnect; our passthrough finally must
    # have settled ownership: no DONE, no event, cleanup ran, quota released.
    await asyncio.sleep(0.05)
    body = b"".join(e.get("body", b"") for e in sent if e["type"] == "http.response.body")
    assert DONE not in body
    assert b"event: optimus.occ3.receipt" not in body
    assert h.adapter._terminal_active == 0
    assert h.adapter._terminal_attempts == set()
    assert h.tm.aborts or h.backend.retires


@pytest.mark.asyncio
async def test_terminal_quota_and_attempts(terminal_harness):
    h = terminal_harness
    h.adapter.terminal_profile = TerminalProfile(max_frame_bytes=4096, max_native_bytes=1 << 20,
                                                  max_json_depth=16, max_json_nodes=4096,
                                                  max_terminal_active=1, max_attempts=2)
    raw1 = terminal_call(h)
    response, _, _ = await drive_terminal(h, raw1)
    assert h.adapter._terminal_active == 0
    a2 = attachment(request_id="second-request", state_handle="PRIVATE_HANDLE_2")
    raw2 = terminal_call(h, a2)
    response, _, _ = await drive_terminal(h, raw2)
    assert h.adapter._terminal_attempts == set()


@pytest.mark.asyncio
async def test_terminal_response_never_consumed_releases_quota(terminal_harness):
    """handle_request returns the StreamingResponse but ASGI never iterates it.

    Repair contract (review P1): abandoning a never-started response must
    reach REAL tracked cleanup — abort, retire and iterator close — not just
    release the quota.
    """
    h = terminal_harness
    raw = terminal_call(h)
    response = await h.chat.handle_request(chat_request(), raw)
    assert response.status_code == 200
    ctx = getattr(raw.state, "_optimus_occ3_chat_context", None)
    assert ctx is not None and ctx.terminal_handed_off and ctx.dispatched
    # Abandon without consuming: explicit aclose of the never-started wrapper.
    await response.body_iterator.aclose()
    await asyncio.sleep(0.05)
    assert h.adapter._terminal_active == 0
    assert h.adapter._terminal_attempts == set()
    # Tracked cleanup ran for the already-dispatched work:
    assert h.tm.aborts == [ctx.native_rid]
    assert len(h.backend.retires) == 1
    assert ctx.cleanup_started is True
    assert h.tm.closed == 1  # the native generator was closed, not left dangling
    assert h.adapter._active == 0


@pytest.mark.asyncio
async def test_terminal_source_flow_event_and_done(source_harness):
    h = source_harness
    h.adapter.terminal_profile = terminal_profile()
    a = intent()
    raw, _ = source_raw(a, canonical({"occ3_capture": a.wire(), "occ3_receipt_mode": TERMINAL_MODE}))
    response = await h.chat.handle_request(chat_request(), raw)
    assert response.status_code == 200
    assert response.headers.get(TERMINAL_ACK_HEADER) == TERMINAL_MODE
    sent = []

    async def send(event):
        sent.append(event)

    await response(raw.scope, raw.receive, send)
    body = b"".join(e.get("body", b"") for e in sent if e["type"] == "http.response.body")
    all_frames = b"".join(h.chat.frames)
    P = all_frames[:-len(DONE)]
    assert body.startswith(P)
    event = body[len(P):-len(DONE)]
    envelope = parse_terminal_event(event)
    assert envelope["kind"] == "source"
    assert envelope["body_sha256"] == hashlib.sha256(P + DONE).hexdigest()
    assert envelope["receipt"]["intent_digest"] == a.binding_digest
    assert body[-len(DONE):] == DONE
    assert len(h.backend.source_finishes) == 1
    assert h.adapter._terminal_active == 0


@pytest.mark.asyncio
async def test_terminal_generator_error_plus_done_is_refused(terminal_harness):
    """Even if the native generator synthesizes error+DONE, no event and no forwarded DONE."""
    h = terminal_harness
    h.chat.stream_tail = [
        "data: " + json.dumps({"id": "err", "error": {"message": "boom"}, "choices": []}) + "\n\n",
        "data: [DONE]\n\n",
    ]
    raw = terminal_call(h)
    response = await h.chat.handle_request(chat_request(), raw)
    sent = []

    async def send(event):
        sent.append(event)

    # The stream must terminate without a valid receipt event or DONE emission.
    with pytest.raises(Exception):
        await response(raw.scope, raw.receive, send)
    body = b"".join(e.get("body", b"") for e in sent if e["type"] == "http.response.body")
    assert b"event: optimus.occ3.receipt" not in body
    assert DONE not in body


def test_audit_split_points_every_boundary():
    case = FIXTURE["receiver"]
    P = bytes.fromhex(case["p_hex"])
    stream = P + DONE
    for split in case["split_points"]:
        audit = TerminalOutputAudit(max_frame_bytes=4096, max_native_bytes=1 << 20,
                                    max_json_depth=16, max_json_nodes=4096)
        forwarded = b""
        left, right = stream[:split], stream[split:]
        for i in range(len(left)):
            forwarded += b"".join(audit.feed(left[i:i+1]))
        for i in range(len(right)):
            forwarded += b"".join(audit.feed(right[i:i+1]))
        completion = audit.finish(case["rid"])
        assert completion.output_sha256 == case["body_sha256"]
        assert completion.output_bytes == case["native_body_bytes"]
        assert forwarded == P


@pytest.mark.parametrize("name", list(FIXTURE["negatives"]))
def test_audit_negatives(name):
    stream = bytes.fromhex(FIXTURE["negatives"][name])
    audit = TerminalOutputAudit(max_frame_bytes=4096, max_native_bytes=1 << 20,
                                max_json_depth=16, max_json_nodes=4096)
    with pytest.raises(AttachmentError):
        for i in range(0, len(stream), 3):
            audit.feed(stream[i:i+3])
        audit.finish("term-rid-receiver-01")


def test_envelope_and_event_bounds():
    case = FIXTURE["receiver"]
    envelope = terminal_envelope("receiver", case["rid"], case["rid"], case["body_sha256"],
                                 case["native_body_bytes"], json.loads(case["envelope_canonical"])["receipt"])
    event = terminal_event_bytes(envelope)
    assert event == bytes.fromhex(case["event_hex"])
    assert len(event) <= 4096
    with pytest.raises(AttachmentError):
        terminal_envelope("invalid", case["rid"], case["rid"], case["body_sha256"],
                         case["native_body_bytes"], {"x": 1})
    with pytest.raises(AttachmentError):
        terminal_envelope("receiver", case["rid"], case["rid"], "nothex",
                         case["native_body_bytes"], {"x": 1})
    with pytest.raises(AttachmentError):
        terminal_event_bytes({"schema": "occ3.terminal.v1", "kind": "receiver",
                              "native_rid": case["rid"], "response_id": case["rid"],
                              "body_sha256": case["body_sha256"], "native_body_bytes": 0,
                              "receipt": {k: "y" * 200 for k in range(30)}} if False else
                             dict(terminal_envelope("receiver", case["rid"], case["rid"],
                                                    case["body_sha256"], case["native_body_bytes"],
                                                    {"attachment": "z" * 3000})))


def test_terminal_tools_admissible_classification(terminal_harness, patched_glm47_chain):
    h = terminal_harness
    strict_schema = {"type": "object", "properties": {"x": {"type": "number"}},
                     "oneOf": [{"properties": {"kind": {"const": "a"}, "y": {"type": "string"}}},
                               {"properties": {"kind": {"const": "b"}, "y": {"type": "number"}}}]}
    flat_schema = {"type": "object", "additionalProperties": False,
                   "properties": {"receiver_role": {"type": "string"},
                                  "receiver_name": {"type": "string"}}}
    from sglang.srt.function_call.glm47_moe_detector import _needs_argument_context
    assert _needs_argument_context(strict_schema) is True
    assert _needs_argument_context(flat_schema) is False
    Tool = type("F", (), {"name": "probe", "parameters": strict_schema, "strict": False})
    tool = NS(function=Tool())
    assert h.adapter._terminal_tools_admissible([tool]) is True
    assert h.adapter._terminal_strict_flag() is patched_glm47_chain._OCC3_STRICT_STREAM


# ---------------------------------------------------------------------------
# Lifetime repair regressions (review findings P1-P6 + root repair task F7).
# All waits are bounded; no gc()/__del__ reliance; every spawned task settles.


def fast_profile(turn=5.0, cleanup=0.1):
    """Fast BufferProfile that still passes the reservation margin check."""
    return BufferProfile(max_buffer_bytes=65536, total_working_bytes=524288,
                         max_turn_seconds=turn, cleanup_seconds=cleanup,
                         disconnect_interval=0.002, deadline_margin_seconds=0.05)


async def collect(response, raw):
    """Drive the ASGI response and return (body_bytes, events)."""
    sent = []

    async def send(event):
        sent.append(event)

    await response(raw.scope, raw.receive, send)
    body = b"".join(e.get("body", b"") for e in sent if e["type"] == "http.response.body")
    return body, sent


def body_of(sent):
    return b"".join(e.get("body", b"") for e in sent if e["type"] == "http.response.body")


async def settle_wait(predicate, timeout):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        await asyncio.sleep(0.01)
    return predicate()


@pytest.mark.asyncio
async def test_terminal_charges_retained_until_retire_settles_then_released_once(terminal_harness):
    """Review P2 / repair F4: unknown retire retains both charge domains.

    The stream is abandoned mid-flight (no proof/native op is left pending),
    and the retire resists the bounded cancel: it is the ONLY unresolved
    required operation. Both charge domains stay retained, a second request
    is refused at the limits, and a LATER positive completion of the retire
    releases exactly once.
    """
    h = terminal_harness
    h.adapter.profile = fast_profile(turn=8.0, cleanup=0.1)
    h.adapter.terminal_profile = terminal_profile(max_terminal_active=1)
    entered = asyncio.Event()
    release = asyncio.Event()
    cancelled = {"n": 0}
    orig_retire = h.backend.retire

    async def resistant_retire(attempt_id, native_rid, dispatched):
        entered.set()
        try:
            await release.wait()
        except asyncio.CancelledError:
            cancelled["n"] += 1
            await release.wait()  # resistant: the bounded cancel cannot finish it
        return await orig_retire(attempt_id, native_rid, dispatched)

    h.backend.retire = resistant_retire
    raw = terminal_call(h)
    response = await h.chat.handle_request(chat_request(), raw)
    assert response.status_code == 200
    sent = []

    async def send(event):
        sent.append(event)
        if event["type"] == "http.response.body" and event.get("body"):
            await asyncio.Event().wait()  # abandon at the first body frame

    task = asyncio.create_task(response(raw.scope, raw.receive, send))
    ctx = getattr(raw.state, "_optimus_occ3_chat_context", None)
    assert await settle_wait(lambda: bool(sent), 2)  # first frame reached the consumer
    task.cancel()  # abandon the emission; the passthrough stays at its yield
    with pytest.raises(asyncio.CancelledError):
        await task
    await asyncio.wait_for(entered.wait(), 1)
    # The bounded settle finished with the retire still unknown (quarantined):
    await asyncio.sleep(0.35)
    assert cancelled["n"] >= 1
    # BOTH charge domains are retained while the retire is unresolved:
    assert h.adapter._active == 1
    assert h.adapter._terminal_active == 1
    assert len(h.adapter._terminal_attempts) == 1
    assert h.backend.retires == []  # never delivered yet
    # A second request is refused at the limits while the first is unknown:
    h.backend.retire = orig_retire
    a2 = attachment(request_id="second", state_handle="PRIVATE_HANDLE_2")
    raw2 = terminal_call(h, a2)
    response2 = await h.chat.handle_request(chat_request(), raw2)
    assert response2.status_code == 429
    assert h.adapter._terminal_active == 1  # refusal did not touch the count
    # Late positive completion releases exactly once:
    release.set()
    assert await settle_wait(lambda: h.adapter._terminal_active == 0, 2)
    assert h.adapter._active == 0
    assert h.adapter._terminal_attempts == set()
    assert len(h.backend.retires) == 1


@pytest.mark.asyncio
async def test_terminal_failed_retire_retains_charges_fail_closed(terminal_harness):
    """Repair F4: a FAILED (raised) retire is not positive settlement."""
    h = terminal_harness
    h.adapter.profile = fast_profile(turn=8.0, cleanup=0.1)
    h.adapter.terminal_profile = terminal_profile(max_terminal_active=1)

    async def failing_retire(attempt_id, native_rid, dispatched):
        raise RuntimeError("retire rejected")

    h.backend.retire = failing_retire
    h.backend.secret_failure = "finish"
    raw = terminal_call(h)
    response = await h.chat.handle_request(chat_request(), raw)
    assert response.status_code == 200
    with pytest.raises(RuntimeError):
        await collect(response, raw)
    await asyncio.sleep(0.1)
    # Charges remain held (fail-closed); the stream is over but the backend
    # reservation state is unknown, so capacity must not be re-admitted.
    assert h.adapter._active == 1
    assert h.adapter._terminal_active == 1
    assert len(h.adapter._terminal_attempts) == 1
    a2 = attachment(request_id="second", state_handle="PRIVATE_HANDLE_2")
    raw2 = terminal_call(h, a2)
    response2 = await h.chat.handle_request(chat_request(), raw2)
    assert response2.status_code == 429


@pytest.mark.asyncio
async def test_terminal_cancelled_retire_retains_charges_fail_closed(terminal_harness):
    """Repair F4: a CANCELLED retire is not positive settlement either."""
    h = terminal_harness
    h.adapter.profile = fast_profile(turn=8.0, cleanup=0.05)
    h.adapter.terminal_profile = terminal_profile(max_terminal_active=1)
    orig_retire = h.backend.retire

    async def cancellable_retire(attempt_id, native_rid, dispatched):
        await asyncio.Event().wait()  # lets the bounded cancel win
        return await orig_retire(attempt_id, native_rid, dispatched)

    h.backend.retire = cancellable_retire
    h.backend.secret_failure = "finish"
    raw = terminal_call(h)
    response = await h.chat.handle_request(chat_request(), raw)
    assert response.status_code == 200
    with pytest.raises(RuntimeError):
        await collect(response, raw)
    await asyncio.sleep(0.3)
    assert h.adapter._terminal_active == 1
    assert h.adapter._active == 1
    assert len(h.adapter._terminal_attempts) == 1


@pytest.mark.asyncio
async def test_terminal_stalled_native_next_is_deadline_bounded(terminal_harness):
    """Review P3a / repair F5: a stalled native __anext__ is watchdog-bounded."""
    h = terminal_harness
    h.adapter.profile = fast_profile(turn=0.4, cleanup=0.1)
    raw = terminal_call(h)
    response = await h.chat.handle_request(chat_request(), raw)
    assert response.status_code == 200
    gate = h.chat.buffer_gate = asyncio.Event()  # stall the native stream
    sent = []

    async def send(event):
        sent.append(event)

    task = asyncio.create_task(response(raw.scope, raw.receive, send))
    await asyncio.sleep(0.1)
    assert not task.done()
    with pytest.raises(AttachmentError):
        await asyncio.wait_for(task, 3)  # bounded: the deadline fires during the stall
    body = body_of(sent)
    assert b"event: optimus.occ3.receipt" not in body
    assert DONE not in body
    # The cancelled native op is an UNKNOWN outcome: fail-closed retention
    # keeps both charge domains held (no release on non-positive settlement).
    await asyncio.sleep(0.1)
    assert h.adapter._terminal_active == 1
    assert h.adapter._active == 1
    assert h.tm.aborts or h.backend.retires


@pytest.mark.asyncio
async def test_terminal_proof_wait_is_deadline_bounded(terminal_harness):
    """Review P3b / repair F5: the backend proof wait is deadline-bounded."""
    h = terminal_harness
    h.adapter.profile = fast_profile(turn=0.4, cleanup=0.1)
    gate = h.backend.finish_gate = asyncio.Event()
    raw = terminal_call(h)
    response = await h.chat.handle_request(chat_request(), raw)
    assert response.status_code == 200
    sent = []

    async def send(event):
        sent.append(event)

    task = asyncio.create_task(response(raw.scope, raw.receive, send))
    await asyncio.sleep(0.1)
    assert len(h.backend.finishes) == 1  # proof pending at the gate
    with pytest.raises(AttachmentError):
        await asyncio.wait_for(task, 3)  # never released: still bounded
    body = body_of(sent)
    assert b"event: optimus.occ3.receipt" not in body
    assert DONE not in body
    # The cancelled proof op is UNKNOWN: fail-closed retention holds the
    # charges (positive settlement was never established).
    await asyncio.sleep(0.1)
    assert h.adapter._terminal_active == 1
    assert h.adapter._active == 1


@pytest.mark.asyncio
async def test_terminal_late_returning_proof_cannot_mint_success(terminal_harness):
    """Review P3b / repair F5: an expired-but-returning proof yields no success.

    The proof double resists cancellation and returns only after the
    deadline has passed; no private event and no DONE may be emitted.
    """
    h = terminal_harness
    h.adapter.profile = fast_profile(turn=0.4, cleanup=0.1)
    release = asyncio.Event()
    orig_finish = h.backend.finish

    async def resistant_finish(reservation, completion):
        try:
            await release.wait()
        except asyncio.CancelledError:
            await release.wait()  # resistant: returns only when released
        return await orig_finish(reservation, completion)

    h.backend.finish = resistant_finish
    raw = terminal_call(h)
    response = await h.chat.handle_request(chat_request(), raw)
    assert response.status_code == 200
    sent = []

    async def send(event):
        sent.append(event)

    task = asyncio.create_task(response(raw.scope, raw.receive, send))
    await asyncio.sleep(0.15)
    with pytest.raises(AttachmentError):
        await asyncio.wait_for(task, 3)
    release.set()  # the proof completes only now, after the failure
    await asyncio.sleep(0.1)
    body = body_of(sent)
    assert b"event: optimus.occ3.receipt" not in body
    assert DONE not in body
    # The late-returning proof op still ended with an exception (the
    # post-finish cancel check): non-positive outcome, so fail-closed
    # retention holds the charges; success was never minted.
    await asyncio.sleep(0.1)
    assert h.adapter._terminal_active == 1
    assert h.adapter._active == 1
    assert h.tm.aborts or h.backend.retires


@pytest.mark.asyncio
async def test_terminal_missing_overlay_refuses_before_dispatch(terminal_harness):
    """Review P4 / repair F6: a baseline (unpatched) runtime refuses with ZERO dispatch."""
    h = terminal_harness
    import sys
    glm = sys.modules["sglang.srt.function_call.glm47_moe_detector"]
    assert hasattr(glm, "_OCC3_STRICT_STREAM")
    saved = glm._OCC3_STRICT_STREAM
    del glm._OCC3_STRICT_STREAM  # simulate the baseline detector module
    try:
        assert h.adapter._terminal_strict_flag() is None
        raw = terminal_call(h)
        response = await h.chat.handle_request(chat_request(), raw)
        assert response.status_code == 503
        # ZERO native/backend side effects before the refusal:
        assert h.tm.calls == []
        assert h.backend.reserves == []
        assert h.backend.retires == []
        assert h.tm.aborts == []
        assert h.adapter._active == 0
        assert h.adapter._terminal_active == 0
        assert h.adapter._terminal_attempts == set()
    finally:
        glm._OCC3_STRICT_STREAM = saved


def test_terminal_profile_incoherent_caps_refused():
    """Repair F6: attempts cap below the active cap fails closed at construction."""
    with pytest.raises(ValueError):
        TerminalProfile(max_frame_bytes=4096, max_native_bytes=1 << 20,
                         max_json_depth=16, max_json_nodes=4096,
                         max_terminal_active=4, max_attempts=1)


@pytest.mark.asyncio
async def test_terminal_attempts_refusal_never_steals_live_count(terminal_harness):
    """Review P5 / repair F6: a cap refusal cannot decrement a live stream.

    Post-repair, both charge domains are acquired and released together, and
    the attempts cap dominates the active cap (coherence check), so an
    attempts-full admission always coincides with a full active count: the
    refusal lands at the active cap with the live/retained counts intact.
    """
    h = terminal_harness
    h.adapter.profile = fast_profile(turn=8.0, cleanup=0.1)
    h.adapter.terminal_profile = terminal_profile(max_terminal_active=2, max_attempts=2)
    release = asyncio.Event()
    orig_retire = h.backend.retire

    async def resistant_retire(attempt_id, native_rid, dispatched):
        try:
            await release.wait()
        except asyncio.CancelledError:
            await release.wait()  # resistant: settles positively only when released
        return await orig_retire(attempt_id, native_rid, dispatched)

    h.backend.retire = resistant_retire
    raw0 = terminal_call(h)
    response0 = await h.chat.handle_request(chat_request(), raw0)
    assert response0.status_code == 200
    sent0 = []

    async def send0(event):
        sent0.append(event)
        if event["type"] == "http.response.body" and event.get("body"):
            await asyncio.Event().wait()  # abandon at the first body frame

    task0 = asyncio.create_task(response0(raw0.scope, raw0.receive, send0))
    assert await settle_wait(lambda: bool(sent0), 2)
    task0.cancel()  # its retire stays the only unresolved op (retained)
    with pytest.raises(asyncio.CancelledError):
        await task0
    await asyncio.sleep(0.3)
    assert len(h.adapter._terminal_attempts) == 1  # retained attempt fills the cap
    h.backend.retire = orig_retire
    gate = h.chat.buffer_gate = asyncio.Event()
    a1 = attachment(request_id="live-a", state_handle="PRIVATE_HANDLE_A")
    raw_a = terminal_call(h, a1)
    response_a = await h.chat.handle_request(chat_request(), raw_a)
    assert response_a.status_code == 200
    task_a = asyncio.create_task(collect(response_a, raw_a))
    await asyncio.sleep(0.05)
    assert h.adapter._terminal_active == 2  # retained + live
    assert len(h.adapter._terminal_attempts) == 2  # full
    # B passes the active cap (2 < 3) but trips the attempts cap:
    a_b = attachment(request_id="steal-b", state_handle="PRIVATE_HANDLE_B")
    raw_b = terminal_call(h, a_b)
    response_b = await h.chat.handle_request(chat_request(), raw_b)
    assert response_b.status_code == 429
    # The refusal did NOT decrement the live/retained counts:
    assert h.adapter._terminal_active == 2
    assert len(h.adapter._terminal_attempts) == 2
    gate.set()
    body_a, _ = await asyncio.wait_for(task_a, 5)
    assert b"event: optimus.occ3.receipt" in body_a  # the live stream still succeeds
    assert h.adapter._terminal_active == 1  # only the retained unknown is left
    release.set()
    assert await settle_wait(lambda: h.adapter._terminal_active == 0, 2)
    assert h.adapter._terminal_attempts == set()
    assert h.adapter._active == 0


@pytest.mark.asyncio
async def test_terminal_release_flags_prevent_underflow_and_steal(terminal_harness):
    """Repair F6: per-context flags make release exactly-once and steal-proof."""
    h = terminal_harness
    gate = h.chat.buffer_gate = asyncio.Event()
    raw = terminal_call(h)
    response = await h.chat.handle_request(chat_request(), raw)
    assert response.status_code == 200
    task = asyncio.create_task(collect(response, raw))
    await asyncio.sleep(0.05)
    ctx = getattr(raw.state, "_optimus_occ3_chat_context")
    assert h.adapter._terminal_active == 1
    # A foreign context that never charged cannot decrement anything:
    foreign = NS(terminal_mode=TERMINAL_MODE, terminal_attempt_charged=False,
                 terminal_active_charged=False, attempt_id="foreign",
                 terminal_released=False, charged=False)
    h.adapter._release_terminal(foreign)
    h.adapter._terminal_release_charges(foreign)
    assert h.adapter._terminal_active == 1
    assert len(h.adapter._terminal_attempts) == 1
    # Repeated/late release of the same context is exactly-once (no underflow):
    h.adapter._terminal_release_charges(ctx)
    assert h.adapter._terminal_active == 0
    h.adapter._terminal_release_charges(ctx)
    h.adapter._release_terminal(ctx)
    assert h.adapter._terminal_active == 0
    gate.set()
    await asyncio.wait_for(task, 5)
    assert h.adapter._terminal_active == 0
    assert h.adapter._terminal_attempts == set()


@pytest.mark.asyncio
async def test_terminal_cancel_at_consumer_send_settles_without_gc(terminal_harness):
    """Review P6 / repair F3: cancellation landing at send settles deterministically."""
    h = terminal_harness
    raw = terminal_call(h)
    response = await h.chat.handle_request(chat_request(), raw)
    assert response.status_code == 200
    consumer_block = asyncio.Event()

    async def send(event):
        if event["type"] == "http.response.body" and event.get("body"):
            await consumer_block.wait()  # slow consumer: passthrough suspends at yield

    task = asyncio.create_task(response(raw.scope, raw.receive, send))
    await asyncio.sleep(0.1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    # Deterministic settle with NO gc()/reference-drop reliance:
    assert await settle_wait(
        lambda: h.adapter._active == 0 and h.adapter._terminal_active == 0, 2)
    assert h.adapter._terminal_attempts == set()
    assert h.tm.aborts or h.backend.retires


@pytest.mark.asyncio
async def test_terminal_event_sent_then_abandon_no_done_no_retraction(terminal_harness):
    """Repair F5: bytes already emitted cannot be retracted; no DONE after abandon."""
    h = terminal_harness
    raw = terminal_call(h)
    response = await h.chat.handle_request(chat_request(), raw)
    assert response.status_code == 200
    sent = []
    consumer_block = asyncio.Event()

    async def send(event):
        sent.append(event)
        body = event.get("body") or b""
        if b"event: optimus.occ3.receipt" in body:
            await consumer_block.wait()  # block exactly after the private event

    task = asyncio.create_task(response(raw.scope, raw.receive, send))
    # Wait until the private event has actually been delivered:
    def event_delivered():
        return any(b"event: optimus.occ3.receipt" in (e.get("body") or b"")
                   for e in sent if e["type"] == "http.response.body")
    assert await settle_wait(event_delivered, 3)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    body = body_of(sent)
    assert b"event: optimus.occ3.receipt" in body  # already-sent prefix recorded
    assert DONE not in body  # no DONE, no clean EOF, no further success bytes
    assert await settle_wait(lambda: h.adapter._terminal_active == 0, 2)
    assert h.tm.aborts or h.backend.retires


@pytest.mark.asyncio
async def test_terminal_response_never_invoked_watchdog_settles(terminal_harness):
    """Repair F3: a response never ASGI-invoked is settled by the bounded watchdog."""
    h = terminal_harness
    h.adapter.profile = fast_profile(turn=0.4, cleanup=0.1)
    raw = terminal_call(h)
    response = await h.chat.handle_request(chat_request(), raw)
    assert response.status_code == 200
    assert len(h.tm.calls) == 1  # dispatched during the pinned prefetch
    # Never invoke response(...): the watchdog must settle at the deadline.
    assert await settle_wait(lambda: h.adapter._terminal_active == 0, 4)
    assert h.adapter._active == 0
    assert h.adapter._terminal_attempts == set()
    assert h.tm.aborts  # the live native request was aborted
    assert len(h.backend.retires) == 1  # positive retire -> released


@pytest.mark.asyncio
async def test_terminal_two_live_contexts_counts(terminal_harness):
    """Repair F7: two overlapping terminal contexts keep exact counts."""
    h = terminal_harness
    gate = h.chat.buffer_gate = asyncio.Event()
    a1 = attachment(request_id="one", state_handle="PRIVATE_HANDLE_1")
    a2 = attachment(request_id="two", state_handle="PRIVATE_HANDLE_2")
    raw1, raw2 = terminal_call(h, a1), terminal_call(h, a2)
    response1 = await h.chat.handle_request(chat_request(), raw1)
    response2 = await h.chat.handle_request(chat_request(), raw2)
    assert response1.status_code == 200 and response2.status_code == 200
    # FixtureBackend.finish builds evidence from the LAST reserve; with two
    # concurrent requests that is ambiguous — select by reservation instead.
    orig_finish = h.backend.finish

    async def finish_by_reservation(reservation, completion):
        entry = next(e for e in h.backend.reserves if e[2] == reservation.attempt_id)
        a = entry[0]
        h.backend.finishes.append(completion)
        return TerminalUseEvidence(
            reservation.attempt_id, a.binding_digest, reservation.native_rid,
            reservation.principal_digest, "fixture-source-commit", "fixture-injection-commit",
            a.runtime_id, a.runtime_generation, a.model_fingerprint, a.receiver_session_id,
            completion.output_sha256, 8, 8, 8, True, False, 0, True, True, reservation.reservation_id)

    h.backend.finish = finish_by_reservation
    task1 = asyncio.create_task(collect(response1, raw1))
    task2 = asyncio.create_task(collect(response2, raw2))
    await asyncio.sleep(0.05)
    assert h.adapter._terminal_active == 2
    assert len(h.adapter._terminal_attempts) == 2
    gate.set()
    body1, _ = await asyncio.wait_for(task1, 5)
    body2, _ = await asyncio.wait_for(task2, 5)
    assert b"event: optimus.occ3.receipt" in body1
    assert b"event: optimus.occ3.receipt" in body2
    assert h.adapter._terminal_active == 0
    assert h.adapter._terminal_attempts == set()
    assert h.adapter._active == 0
    assert len(h.backend.finishes) == 2
    assert h.backend.retires == []  # success keeps the settlement at finish
    assert h.tm.aborts == []


@pytest.mark.asyncio
async def test_terminal_success_no_double_cleanup_and_repeated_aclose(terminal_harness):
    """Repair F3/F7: known success releases charges once; no double cleanup."""
    h = terminal_harness
    raw = terminal_call(h)
    response, sent, body = await drive_terminal(h, raw)
    assert b"event: optimus.occ3.receipt" in body
    assert len(h.backend.finishes) == 1
    assert h.backend.retires == []
    assert h.tm.aborts == []
    assert h.adapter._terminal_active == 0
    # A repeated aclose after success changes nothing (idempotent settle):
    await response.body_iterator.aclose()
    await asyncio.sleep(0.05)
    assert len(h.backend.finishes) == 1
    assert h.backend.retires == []
    assert h.tm.aborts == []
    assert h.adapter._terminal_active == 0
    assert h.adapter._active == 0


@pytest.mark.asyncio
async def test_terminal_source_success_keeps_capture_lease(source_harness):
    """Repair F3: successful capture keeps the intended backend lease."""
    h = source_harness
    h.adapter.terminal_profile = terminal_profile()
    a = intent()
    raw, _ = source_raw(a, canonical({"occ3_capture": a.wire(), "occ3_receipt_mode": TERMINAL_MODE}))
    response = await h.chat.handle_request(chat_request(), raw)
    assert response.status_code == 200
    body, _ = await collect(response, raw)
    assert b"event: optimus.occ3.receipt" in body
    assert body.endswith(DONE)
    assert len(h.backend.source_finishes) == 1
    assert h.backend.retires == []  # lease kept: no routine retire on success
    assert h.tm.aborts == []
    assert h.adapter._terminal_active == 0
    assert h.adapter._active == 0


@pytest.mark.asyncio
async def test_terminal_prehandoff_refusal_settles_and_releases(terminal_harness):
    """Repair F7: a terminal context refused before hand-off settles tracked."""
    h = terminal_harness

    async def bad_authorize(raw):
        raise AttachmentError("identity_mismatch", 401)

    h.adapter.authorize = bad_authorize
    raw = terminal_call(h)
    response = await h.chat.handle_request(chat_request(), raw)
    assert response.status_code == 401
    assert h.tm.calls == []  # refused before dispatch
    assert h.backend.reserves == []
    assert h.backend.retires == []
    assert h.tm.aborts == []
    assert h.adapter._active == 0
    assert h.adapter._terminal_active == 0
    assert h.adapter._terminal_attempts == set()


# ---------------------------------------------------------------------------
# v2 lifetime repair regressions (replacement review R2/R3/R4 family).
# ALL-work barrier, ASGI emission fence, post-D clean-EOF prevention.
# All waits bounded; every spawned task and gate is settled at teardown.


def _state_attr():
    return "_optimus_occ3_chat_context"


async def _abandon_at_first_frame(h, raw):
    """Start the response and cancel it once the first body frame is sent."""
    sent = []

    async def send(event):
        sent.append(event)
        if event["type"] == "http.response.body" and event.get("body"):
            await asyncio.Event().wait()  # stall the consumer forever

    task = asyncio.create_task(h.response(raw.scope, raw.receive, send))
    assert await settle_wait(lambda: any(e.get("body") for e in sent), 3)
    return task, sent


@pytest.mark.asyncio
async def test_v2_resistant_abort_holds_charges_then_late_positive_releases(terminal_harness):
    """R2: a resistant ABORT is a required op; prompt-positive retire does NOT
    release while it survives; the second request is refused; a late positive
    abort completion releases exactly once."""
    h = terminal_harness
    h.adapter.profile = fast_profile(turn=8.0, cleanup=0.1)
    h.adapter.terminal_profile = terminal_profile(max_terminal_active=1)
    release_abort = asyncio.Event()

    async def resistant_abort(rid):
        try:
            await release_abort.wait()
        except asyncio.CancelledError:
            await release_abort.wait()  # resist the bounded cancel
        return None

    h.tm.abort_request = lambda rid: resistant_abort(rid)
    raw = terminal_call(h)
    response = await h.chat.handle_request(chat_request(), raw)
    assert response.status_code == 200
    h.response = response
    task, sent = await _abandon_at_first_frame(h, raw)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    # Retire returned promptly-positive, but the abort op is still pending:
    await asyncio.sleep(0.3)
    assert h.backend.retires  # retire positive (one required ack)
    assert h.adapter._active == 1 and h.adapter._terminal_active == 1
    assert len(h.adapter._terminal_attempts) == 1
    a2 = attachment(request_id="second", state_handle="PRIVATE_HANDLE_2")
    raw2 = terminal_call(h, a2)
    response2 = await h.chat.handle_request(chat_request(), raw2)
    assert response2.status_code == 429  # refused at the limits
    # Late positive abort completion: every required op positive -> release.
    release_abort.set()
    assert await settle_wait(lambda: h.adapter._terminal_active == 0, 2)
    assert h.adapter._active == 0 and h.adapter._terminal_attempts == set()


@pytest.mark.asyncio
async def test_v2_resistant_close_holds_charges_then_late_positive_releases(terminal_harness):
    """R2b: a resistant iterator CLOSE keeps the charges with a prompt retire."""
    h = terminal_harness
    h.adapter.profile = fast_profile(turn=8.0, cleanup=0.1)
    h.adapter.terminal_profile = terminal_profile(max_terminal_active=1)
    release_close = asyncio.Event()

    class ResistantClose:
        def __init__(self, release, inner):
            self._release, self._inner = release, inner

        async def _resistant(self):
            try:
                await self._release.wait()
            except asyncio.CancelledError:
                await self._release.wait()
            return None

        def aclose(self):
            return self._resistant()

        def __getattr__(self, name):
            return getattr(self._inner, name)

    raw = terminal_call(h)
    response = await h.chat.handle_request(chat_request(), raw)
    assert response.status_code == 200
    ctx = getattr(raw.state, _state_attr())
    ctx.response_iterator = ResistantClose(release_close, ctx.response_iterator)
    h.response = response
    task, sent = await _abandon_at_first_frame(h, raw)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    await asyncio.sleep(0.3)
    assert h.backend.retires  # retire prompt-positive
    assert h.adapter._active == 1 and h.adapter._terminal_active == 1
    a2 = attachment(request_id="second", state_handle="PRIVATE_HANDLE_2")
    raw2 = terminal_call(h, a2)
    response2 = await h.chat.handle_request(chat_request(), raw2)
    assert response2.status_code == 429
    release_close.set()
    assert await settle_wait(lambda: h.adapter._terminal_active == 0, 2)
    assert h.adapter._active == 0


@pytest.mark.asyncio
async def test_v2_resistant_native_next_holds_charges_then_late_positive(terminal_harness):
    """R2c: a resistant native NEXT survives watch's bounded cancel; a prompt
    retire does not release; its late normal completion (EOF shape) does."""
    h = terminal_harness
    h.adapter.profile = fast_profile(turn=0.5, cleanup=0.1)
    h.adapter.terminal_profile = terminal_profile(max_terminal_active=1)
    never = asyncio.Event()
    orig_stream = h.chat._generate_chat_stream

    async def resistant_stream(obj, request, raw):
        try:
            async for frame in orig_stream(obj, request, raw):
                yield frame
        except asyncio.CancelledError:
            await never.wait()  # resist watch's bounded cancel
            return  # complete normally afterwards (positive EOF shape)

    h.chat._generate_chat_stream = resistant_stream
    gate = h.chat.buffer_gate = asyncio.Event()  # stall the native stream
    raw = terminal_call(h)
    response = await h.chat.handle_request(chat_request(), raw)
    assert response.status_code == 200
    sent = []

    async def send(event):
        sent.append(event)

    task = asyncio.create_task(response(raw.scope, raw.receive, send))
    await asyncio.sleep(0.1)
    with pytest.raises(AttachmentError):
        await asyncio.wait_for(task, 3)  # deadline-bounded
    h.chat._generate_chat_stream = orig_stream
    h.chat.buffer_gate = None
    await asyncio.sleep(0.3)
    # Retire was prompt-positive but the resistant native op survives:
    assert h.backend.retires
    assert h.adapter._active == 1 and h.adapter._terminal_active == 1
    a2 = attachment(request_id="second", state_handle="PRIVATE_HANDLE_2")
    raw2 = terminal_call(h, a2)
    response2 = await h.chat.handle_request(chat_request(), raw2)
    assert response2.status_code == 429
    # Late normal completion of the resistant native op releases once:
    gate.set()
    never.set()
    assert await settle_wait(lambda: h.adapter._terminal_active == 0, 3)
    assert h.adapter._active == 0
    pending = [t for t in h.adapter._terminal_tasks if not t.done()]
    if pending:
        await asyncio.gather(*pending, return_exceptions=True)


@pytest.mark.asyncio
async def test_v2_resistant_proof_latches_fail_closed(terminal_harness):
    """R2d: a resistant PROOF that returns after the failure still ended with
    an exception (post-finish cancel check): latched, charges retained."""
    h = terminal_harness
    h.adapter.profile = fast_profile(turn=0.5, cleanup=0.1)
    h.adapter.terminal_profile = terminal_profile(max_terminal_active=1)
    release_proof = asyncio.Event()
    orig_finish = h.backend.finish

    async def resistant_finish(reservation, completion):
        h.backend.finishes.append(completion)
        try:
            await release_proof.wait()
        except asyncio.CancelledError:
            await release_proof.wait()
        return await orig_finish(reservation, completion)

    h.backend.finish = resistant_finish
    raw = terminal_call(h)
    response = await h.chat.handle_request(chat_request(), raw)
    assert response.status_code == 200
    sent = []

    async def send(event):
        sent.append(event)

    task = asyncio.create_task(response(raw.scope, raw.receive, send))
    await asyncio.sleep(0.15)
    with pytest.raises(AttachmentError):
        await asyncio.wait_for(task, 3)
    release_proof.set()
    await asyncio.sleep(0.2)
    assert h.backend.retires  # retire prompt-positive
    # The proof op latched (failed outcome): retention is permanent here.
    assert h.adapter._active == 1 and h.adapter._terminal_active == 1
    assert len(h.adapter._terminal_attempts) == 1
    # Second request refused at the limits while the latched op is retained:
    a2 = attachment(request_id="second", state_handle="PRIVATE_HANDLE_2")
    raw2 = terminal_call(h, a2)
    response2 = await h.chat.handle_request(chat_request(), raw2)
    assert response2.status_code == 429
    pending = [t for t in h.adapter._terminal_tasks if not t.done()]
    if pending:
        await asyncio.gather(*pending, return_exceptions=True)


@pytest.mark.asyncio
async def test_v2_one_late_positive_among_multiple_does_not_release(terminal_harness):
    """F2: with a resistant abort AND a resistant retire, releasing only the
    abort keeps the charges; the final retire completion releases once."""
    h = terminal_harness
    h.adapter.profile = fast_profile(turn=8.0, cleanup=0.1)
    h.adapter.terminal_profile = terminal_profile(max_terminal_active=1)
    release_abort = asyncio.Event()
    release_retire = asyncio.Event()
    orig_retire = h.backend.retire

    async def resistant_abort(rid):
        try:
            await release_abort.wait()
        except asyncio.CancelledError:
            await release_abort.wait()
        return None

    async def resistant_retire(attempt_id, native_rid, dispatched):
        try:
            await release_retire.wait()
        except asyncio.CancelledError:
            await release_retire.wait()
        return await orig_retire(attempt_id, native_rid, dispatched)

    h.tm.abort_request = lambda rid: resistant_abort(rid)
    h.backend.retire = resistant_retire
    raw = terminal_call(h)
    response = await h.chat.handle_request(chat_request(), raw)
    assert response.status_code == 200
    h.response = response
    task, sent = await _abandon_at_first_frame(h, raw)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    await asyncio.sleep(0.3)
    assert h.adapter._active == 1 and h.adapter._terminal_active == 1
    # One late positive among the two is NOT sufficient:
    release_abort.set()
    await asyncio.sleep(0.2)
    assert h.adapter._active == 1 and h.adapter._terminal_active == 1
    assert h.backend.retires == []  # the retire has not settled yet
    a2 = attachment(request_id="second", state_handle="PRIVATE_HANDLE_2")
    raw2 = terminal_call(h, a2)
    response2 = await h.chat.handle_request(chat_request(), raw2)
    assert response2.status_code == 429
    # All required ops positive -> release exactly once:
    release_retire.set()
    assert await settle_wait(lambda: h.adapter._terminal_active == 0, 2)
    assert h.adapter._active == 0
    assert len(h.backend.retires) == 1


@pytest.mark.asyncio
async def test_v2_d_send_stall_no_new_clean_eof_after_deadline(terminal_harness):
    """R3a: the consumer stalls exactly on the DONE send; the deadline crosses
    and the watchdog settles. After the consumer unblocks, the already-entered
    D send's outcome is uncertain, but NO NEW clean EOF and no success flag:
    the passthrough post-D recheck refuses to complete."""
    h = terminal_harness
    h.adapter.profile = fast_profile(turn=0.5, cleanup=0.1)
    raw = terminal_call(h)
    response = await h.chat.handle_request(chat_request(), raw)
    assert response.status_code == 200
    sent = []
    d_seen = asyncio.Event()
    d_blocked = asyncio.Event()

    async def send(event):
        sent.append(event)
        body = event.get("body") or b""
        if body.endswith(DONE):
            d_seen.set()
            await d_blocked.wait()  # stall exactly on the DONE send

    task = asyncio.create_task(response(raw.scope, raw.receive, send))
    assert await settle_wait(lambda: d_seen.is_set(), 3)
    # Deadline crosses during the stall; the watchdog settles the backend.
    assert await settle_wait(lambda: h.backend.retires, 3)
    # Charges are retained while the emission is still open (all-work barrier):
    assert h.adapter._active == 1 and h.adapter._terminal_active == 1
    assert not task.done()
    # Release the consumer: the D send completes (already entered, uncertain
    # wire outcome) and then the post-D recheck refuses success.
    d_blocked.set()
    with pytest.raises(Exception):
        await asyncio.wait_for(task, 3)
    body = body_of(sent)
    assert body.endswith(DONE) and b"event: optimus.occ3.receipt" in body  # already sent
    # NO NEW clean EOF after the failure settle:
    assert not any(e.get("more_body") is False for e in sent
                   if e["type"] == "http.response.body")
    ctx = getattr(raw.state, _state_attr())
    assert ctx.completed is False  # no success flag
    # With the emission ended and every settle op positive, release follows:
    assert await settle_wait(lambda: h.adapter._terminal_active == 0, 2)
    assert h.adapter._active == 0


@pytest.mark.asyncio
async def test_v2_first_frame_stall_charged_quarantine_then_unclean_end(terminal_harness):
    """R4: a stalled consumer send keeps the response unfenced-but-charged:
    the watchdog settle cannot authorize future frames; after the consumer
    unblocks, the stream ends unclean with no event/D and no clean EOF."""
    h = terminal_harness
    h.adapter.profile = fast_profile(turn=0.5, cleanup=0.1)
    raw = terminal_call(h)
    response = await h.chat.handle_request(chat_request(), raw)
    assert response.status_code == 200
    sent = []
    send_called = asyncio.Event()
    block = asyncio.Event()

    async def send(event):
        sent.append(event)
        if event["type"] == "http.response.body" and event.get("body"):
            send_called.set()
            await block.wait()  # stuck consumer send

    task = asyncio.create_task(response(raw.scope, raw.receive, send))
    assert await settle_wait(lambda: send_called.is_set(), 3)
    # Deadline crosses: the watchdog settles the backend; the emission op
    # keeps the charges quarantined while the stalled send outlives.
    assert await settle_wait(lambda: h.backend.retires, 3)
    assert h.adapter._active == 1 and h.adapter._terminal_active == 1
    await asyncio.sleep(0.1)
    assert not task.done()  # the stalled send is not forcibly terminated
    a2 = attachment(request_id="second", state_handle="PRIVATE_HANDLE_2")
    h.adapter.terminal_profile = terminal_profile(max_terminal_active=1)
    raw2 = terminal_call(h, a2)
    response2 = await h.chat.handle_request(chat_request(), raw2)
    assert response2.status_code == 429  # new work refused at the limits
    # Release: unclean end, no new frames, no clean EOF, prefix recorded.
    block.set()
    with pytest.raises(Exception):
        await asyncio.wait_for(task, 3)
    assert not any(e.get("more_body") is False for e in sent
                   if e["type"] == "http.response.body")
    body = body_of(sent)
    assert b"event: optimus.occ3.receipt" not in body and DONE not in body
    assert body  # the already-sent prefix is recorded, not retracted
    assert await settle_wait(lambda: h.adapter._terminal_active == 0, 2)
    assert h.adapter._active == 0


@pytest.mark.asyncio
async def test_v2_direct_iterator_post_d_deadline_cancel_refusal(terminal_harness):
    """R3b: driving the iterator directly, a post-D resume with the deadline
    long past and a requested cancel must refuse instead of completing."""
    h = terminal_harness
    h.adapter.profile = fast_profile(turn=5.0, cleanup=0.1)
    raw = terminal_call(h)
    response = await h.chat.handle_request(chat_request(), raw)
    assert response.status_code == 200
    it = response.body_iterator
    chunks = []
    while True:
        chunk = await it.__anext__()
        chunks.append(chunk)
        if chunk == DONE:
            break
    ctx = getattr(raw.state, _state_attr())
    assert ctx is not None
    # Isolate the post-D branch: stop the watchdog, then make the effective
    # deadline long past AND flag a requested cancel.
    wd = ctx.terminal_watchdog_task
    wd.cancel()
    await asyncio.gather(wd, return_exceptions=True)
    h.adapter.monotonic = lambda: ctx.deadline + 100.0
    ctx.cancel_requested = True
    with pytest.raises(AttachmentError):
        await it.__anext__()  # refused: NOT a clean StopAsyncIteration
    assert ctx.completed is False
    # The settle ran (abort/retire/closes all positive; no emission op was
    # ever registered because the ASGI lifecycle was never started):
    assert await settle_wait(lambda: h.adapter._terminal_active == 0, 2)
    assert h.adapter._active == 0
    assert h.adapter._terminal_attempts == set()
    assert h.tm.aborts


@pytest.mark.asyncio
async def test_v2_already_started_eof_send_outcome_is_uncertain_not_new(terminal_harness):
    """F4 distinction: an EOF send that already STARTED before the deadline
    crossing completes with an uncertain wire outcome; no NEW send is issued
    afterwards and the release follows the lifecycle end (success)."""
    h = terminal_harness
    h.adapter.profile = fast_profile(turn=5.0, cleanup=0.1)
    raw = terminal_call(h)
    response = await h.chat.handle_request(chat_request(), raw)
    assert response.status_code == 200
    sent = []
    eof_started = asyncio.Event()
    eof_block = asyncio.Event()

    async def send(event):
        sent.append(event)
        if event["type"] == "http.response.body" and event.get("more_body") is False:
            eof_started.set()
            await eof_block.wait()  # the final EOF send is entered, then stalls
        return None

    task = asyncio.create_task(response(raw.scope, raw.receive, send))
    assert await settle_wait(lambda: eof_started.is_set(), 3)
    # The final EOF send has STARTED (past the fence, uncertain outcome);
    # the producer already completed success before this send began.
    ctx = getattr(raw.state, _state_attr())
    assert ctx.completed is True
    clock = {"t": time.monotonic()}
    real_monotonic = h.adapter.monotonic
    h.adapter.monotonic = lambda: clock["t"] + 3600.0  # deadline now far past
    eof_block.set()  # the already-started EOF send completes
    await asyncio.wait_for(task, 3)
    h.adapter.monotonic = real_monotonic
    # The already-started EOF send is on the wire (uncertain delivery); no
    # NEW send was issued after it:
    assert any(e.get("more_body") is False for e in sent
              if e["type"] == "http.response.body")
    body = body_of(sent)
    assert body.endswith(DONE) and b"event: optimus.occ3.receipt" in body
    # The lifecycle ended; every required op is positive; release follows.
    assert await settle_wait(lambda: h.adapter._terminal_active == 0, 2)
    assert h.adapter._active == 0
    assert h.adapter._terminal_attempts == set()
    assert h.backend.retires == [] and h.tm.aborts == []  # success keeps the lease


@pytest.mark.asyncio
async def test_v2_direct_iteration_holds_charges_midstream(terminal_harness):
    """F2 ALL-work barrier: a directly driven body iterator (no ASGI
    lifecycle, so no emission op exists yet) must not release either
    charge domain while the stream is still live. A positively resolved
    native op is a PARTIAL op set — the settle obligations (abort/retire/
    close) are not yet registered — so release on it would admit a second
    request while the first is still streaming."""
    h = terminal_harness
    h.adapter.profile = fast_profile(turn=5.0, cleanup=0.1)
    raw = terminal_call(h)
    response = await h.chat.handle_request(chat_request(), raw)
    assert response.status_code == 200
    it = response.body_iterator
    chunk = await it.__anext__()  # first native op resolves positively
    assert chunk
    # Stream still live: BOTH charge domains stay held on the partial set.
    assert h.adapter._active == 1
    assert h.adapter._terminal_active == 1
    assert len(h.adapter._terminal_attempts) == 1
    # A second request is refused at the limits while the first is live:
    h.adapter.terminal_profile = terminal_profile(max_terminal_active=1)
    a2 = attachment(request_id="second", state_handle="PRIVATE_HANDLE_2")
    raw2 = terminal_call(h, a2)
    response2 = await h.chat.handle_request(chat_request(), raw2)
    assert response2.status_code == 429
    assert h.adapter._terminal_active == 1  # the refusal stole nothing
    # Teardown: consume to EOF; the success settle releases exactly once.
    while True:
        try:
            await it.__anext__()
        except StopAsyncIteration:
            break
    assert await settle_wait(lambda: h.adapter._terminal_active == 0, 2)
    assert h.adapter._active == 0
    assert h.adapter._terminal_attempts == set()


@pytest.mark.asyncio
async def test_v2_late_native_frame_after_deadline_no_partial_release(terminal_harness):
    """F2: a native op completing just after the deadline resolves its own
    op positively inside watch(), but that is still a PARTIAL op set: the
    required abort/retire/close obligations have not registered yet. With a
    resistant abort installed, the charges must stay held (and a second
    request be refused) until the abort's late positive completion."""
    h = terminal_harness
    h.adapter.profile = fast_profile(turn=5.0, cleanup=0.1)
    h.adapter.terminal_profile = terminal_profile(max_terminal_active=1)
    release_abort = asyncio.Event()

    async def resistant_abort(rid):
        try:
            await release_abort.wait()
        except asyncio.CancelledError:
            await release_abort.wait()  # resist the bounded cancel
        return None

    h.tm.abort_request = lambda rid: resistant_abort(rid)
    gate = h.chat.buffer_gate = asyncio.Event()
    raw = terminal_call(h)
    response = await h.chat.handle_request(chat_request(), raw)
    assert response.status_code == 200
    it = response.body_iterator
    first = await it.__anext__()  # the fixture gate blocks AFTER this frame
    assert first
    task = asyncio.create_task(it.__anext__())  # this one stalls at the gate
    await asyncio.sleep(0.05)
    assert not task.done()
    ctx = getattr(raw.state, _state_attr())
    real = h.adapter.monotonic
    h.adapter.monotonic = lambda: ctx.deadline + 10.0  # deadline far past
    gate.set()  # the native op completes now, after the deadline
    with pytest.raises(AttachmentError):
        await asyncio.wait_for(task, 3)
    # The late op resolved positive, but the resistant abort (required) is
    # still unresolved: no release on the partial set.
    assert h.adapter._active == 1 and h.adapter._terminal_active == 1
    assert len(h.adapter._terminal_attempts) == 1
    a2 = attachment(request_id="second", state_handle="PRIVATE_HANDLE_2")
    raw2 = terminal_call(h, a2)
    response2 = await h.chat.handle_request(chat_request(), raw2)
    assert response2.status_code == 429
    # Late positive abort completion: ALL required ops positive -> release.
    release_abort.set()
    assert await settle_wait(lambda: h.adapter._terminal_active == 0, 2)
    assert h.adapter._active == 0
    assert h.adapter._terminal_attempts == set()
    pending = [t for t in h.adapter._terminal_tasks if not t.done()]
    if pending:
        await asyncio.gather(*pending, return_exceptions=True)
