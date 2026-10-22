"""Experimental, explicit-opt-in source-prompt KV handles for SGLang 0.5.21.

This is NOT arbitrary hidden-state transfer or native Optimus receiver injection.
Source prompt IDs stay inside the server as radix lookup keys. Only opaque handles
cross the send transport. KV remains LRU-evictable; a miss is an error, never success.
Retirement removes access/CPU references, not the GPU cache's normal LRU rows.
"""
from __future__ import annotations

import asyncio
import hashlib
import threading
import time
import uuid
from dataclasses import dataclass, field

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field, StrictBool

from . import metrics
from .identity import compute_identity, fingerprint
from .security import ValidationError, validate_channel_id
from .send_path import _first_output

PROTOCOL = "source-prompt-kv-handle-v1"
MAX_CAPTURES = 16
MAX_CAPTURES_PER_SESSION = 4
MAX_FACT_TOKENS = 4096
MAX_PAGE_SIZE = 256
OPERATION_TIMEOUT_S = 120.0
CANCEL_GRACE_S = 1.0
router = APIRouter(prefix="/occ3/v1/latent", tags=["occ3-experimental"])


class ProtocolError(HTTPException):
    """Only fixed, locally audited codes may cross the HTTP boundary."""


class BoundBody(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    opt_in: StrictBool = False
    session_id: str = Field(min_length=1, max_length=64)
    model_fingerprint: str = Field(min_length=1, max_length=4096)


class CaptureBody(BoundBody):
    text: str = Field(min_length=1, max_length=32768)
    ttl_s: float = Field(default=600.0, ge=1.0, le=600.0)


class SendHandleBody(BoundBody):
    capture_handle: str = Field(min_length=1, max_length=128)
    receiver_session_id: str = Field(min_length=1, max_length=64)


class ReceiveHandleBody(BoundBody):
    channel_handle: str = Field(min_length=1, max_length=128)
    suffix: str = Field(min_length=1, max_length=8192)
    max_new_tokens: int = Field(default=1024, ge=1, le=4096)


class ReleaseHandleBody(BoundBody):
    capture_handle: str = Field(min_length=1, max_length=128)


@dataclass(repr=False)
class SourceCapture:
    handle: str
    owner: str
    model_fingerprint: str
    epoch: str
    salt: str
    source_rid: str
    expires: float
    created: float
    input_ids: list[int] = field(default_factory=list, repr=False)
    required_gpu_tokens: int = 0
    meta: dict = field(default_factory=dict, repr=False)
    state: str = "PREPARING"
    channel_handle: str | None = None
    receiver: str | None = None
    in_flight: bool = False
    operation_rid: str | None = None
    operation_deadline: float = float("inf")
    operation_task: object = field(default=None, repr=False)
    abort_task: object = field(default=None, repr=False)
    operation_loop: object = field(default=None, repr=False)
    local_task_closed: bool = True
    abort_admitted: bool = False
    uncertain_reason: str | None = None


class CaptureStore:
    def __init__(self, clock=time.monotonic):
        self.epoch = uuid.uuid4().hex
        self.clock = clock
        self.lock = threading.Lock()
        self.captures: dict[str, SourceCapture] = {}
        self.channels: dict[str, str] = {}

    def _sweep(self):
        for capture in list(self.captures.values()):
            if capture.state == "UNCERTAIN":
                continue  # Native abort has no scheduler cleanup acknowledgement.
            due = min(capture.expires, capture.operation_deadline)
            if capture.in_flight and due <= self.clock():
                self._uncertain(capture, "operation_deadline")
                task, loop = capture.operation_task, capture.operation_loop
                if task is not None and loop is not None and not task.done():
                    loop.call_soon_threadsafe(task.cancel)
            elif capture.expires <= self.clock() and not capture.in_flight:
                self._retire(capture)

    def _uncertain(self, capture, reason):
        if self.captures.get(capture.handle) is not capture:
            return
        capture.state = "UNCERTAIN"
        capture.uncertain_reason = reason
        capture.in_flight = not capture.local_task_closed
        self.channels.pop(capture.channel_handle, None)
        capture.input_ids.clear()

    def _retire(self, capture):
        if (capture.state == "UNCERTAIN"
                or (capture.operation_task is not None and not capture.local_task_closed)):
            self._uncertain(capture, "cleanup_unconfirmed")
            return False
        if self.captures.get(capture.handle) is not capture:
            return False
        self.captures.pop(capture.handle, None)
        self.channels.pop(capture.channel_handle, None)
        capture.input_ids.clear()
        capture.state = "RETIRED"
        return True

    def reserve(self, owner, model_fp, ttl):
        with self.lock:
            self._sweep()
            if (len(self.captures) >= MAX_CAPTURES
                    or sum(item.owner == owner for item in self.captures.values())
                    >= MAX_CAPTURES_PER_SESSION):
                raise ProtocolError(429, "capture_capacity")
            handle = self.epoch + "." + uuid.uuid4().hex
            capture = SourceCapture(
                handle, owner, model_fp, self.epoch,
                "occ3-kv-" + uuid.uuid4().hex,
                "occ3-capture-" + uuid.uuid4().hex,
                float("inf"), self.clock(), in_flight=True,
            )
            self.captures[handle] = capture
            return capture

    def get(self, handle, *, channel=False):
        self._sweep()
        if channel:
            handle = self.channels.get(handle)
        capture = self.captures.get(handle)
        if capture is not None and capture.state == "UNCERTAIN":
            raise ProtocolError(409, "cleanup_unconfirmed")
        if capture is None or capture.expires <= self.clock():
            raise ProtocolError(410, "stale_or_unavailable")
        return capture

    def retire(self, capture):
        with self.lock:
            self._retire(capture)


store = CaptureStore()


def _settled(owner_store, capture, rid, task, *, abort=False):
    """Observe task errors privately; late results can never restore a lease."""
    try:
        result = task.result()
        succeeded = True
    except BaseException:
        result, succeeded = None, False
    with owner_store.lock:
        if (owner_store.captures.get(capture.handle) is not capture
                or capture.operation_rid != rid):
            return
        if abort:
            capture.abort_admitted = succeeded and result is True
        else:
            capture.local_task_closed = True
            if capture.state == "UNCERTAIN":
                capture.in_flight = False


async def _abort_exact(tm, rid):
    # Full generated fixed-length ID, never caller input, blank ID or abort_all.
    # SGLang matches prefixes; all IDs owned by this API have equal-length UUID tails.
    from sglang.srt.managers.io_struct import AbortReq
    await tm._async_dispatch_to_scheduler(AbortReq(rid=rid, abort_all=False))
    return True  # Admission only; not GPU/scheduler cleanup acknowledgement.


async def _quarantine(owner_store, capture, tm, rid, reason):
    with owner_store.lock:
        owner_store._uncertain(capture, reason)
        task = capture.operation_task
        if task is not None and not task.done():
            task.cancel()
        if capture.abort_task is None:
            capture.abort_task = asyncio.create_task(_abort_exact(tm, rid))
            capture.abort_task.add_done_callback(
                lambda done: _settled(owner_store, capture, rid, done, abort=True))
        pending = {item for item in (task, capture.abort_task) if item is not None}
    # Unlike wait_for, wait does not wait forever for cancellation-resistant code.
    if pending:
        await asyncio.wait(pending, timeout=CANCEL_GRACE_S)
    # Even closed local tasks and admitted aborts retain quota: native cleanup is unacknowledged.


async def _run_operation(owner_store, capture, tm, obj, *, consume=False):
    rid = obj.rid
    budget = OPERATION_TIMEOUT_S
    if consume:
        budget = min(budget, max(0.0, capture.expires - owner_store.clock()))
    if budget <= 0:
        raise ProtocolError(410, "operation_expired_before_dispatch")
    task = asyncio.create_task(_first_output(tm, obj))
    with owner_store.lock:
        capture.operation_rid = rid
        capture.operation_deadline = owner_store.clock() + budget
        capture.operation_task = task
        capture.abort_task = None
        capture.operation_loop = asyncio.get_running_loop()
        capture.local_task_closed = False
        capture.abort_admitted = False
    task.add_done_callback(lambda done: _settled(owner_store, capture, rid, done))
    try:
        done, _ = await asyncio.wait({task}, timeout=budget)
        if not done:
            await _quarantine(owner_store, capture, tm, rid, "operation_deadline")
            raise ProtocolError(504, "operation_deadline_cleanup_unconfirmed")
        result = task.result()
        with owner_store.lock:
            uncertain = capture.state == "UNCERTAIN"
            capture.local_task_closed = True
            capture.operation_deadline = float("inf")
        if uncertain:
            # A concurrent expiry sweep can race with the final local result.
            # It still needs the same one-shot exact request abort admission.
            await _quarantine(owner_store, capture, tm, rid, "expired_during_completion")
            raise ProtocolError(409, "cleanup_unconfirmed")
        return result
    except ProtocolError:
        raise
    except asyncio.CancelledError:
        await _quarantine(owner_store, capture, tm, rid, "operation_cancelled")
        if asyncio.current_task().cancelling():
            raise
        raise ProtocolError(504, "operation_cancelled_cleanup_unconfirmed") from None
    except Exception:
        await _quarantine(owner_store, capture, tm, rid, "backend_failed")
        raise ProtocolError(502, "backend_failed_cleanup_unconfirmed") from None


def _tm():
    from .api import _tm as main_tm
    return main_tm()


def _check(body, tm):
    if body.opt_in is not True:
        raise ProtocolError(403, "explicit_opt_in_required")
    try:
        validate_channel_id(body.session_id)
    except ValidationError:
        raise ProtocolError(400, "invalid_session")
    current = fingerprint(compute_identity(tm))
    if body.model_fingerprint != current:
        raise ProtocolError(409, "model_fingerprint_mismatch")
    return current


def _runtime(tm):
    import sglang
    args = tm.server_args
    if sglang.__version__ != "0.5.21":
        raise ProtocolError(503, "unsupported_runtime")
    if str(getattr(args, "device", "")) != "cuda":
        raise ProtocolError(503, "cuda_backend_unproven")
    page_size = getattr(args, "page_size", None)
    if type(page_size) is not int or not 1 <= page_size <= MAX_PAGE_SIZE:
        raise ProtocolError(503, "cache_geometry_unproven")
    if getattr(args, "disable_radix_cache", False):
        raise ProtocolError(503, "radix_cache_unavailable")
    if getattr(args, "enable_lora", False):
        raise ProtocolError(503, "adapter_identity_unproven")
    if getattr(args, "tokenizer_worker_num", 1) != 1:
        raise ProtocolError(503, "process_local_handle_routing_unproven")
    if getattr(args, "disaggregation_mode", "null") not in (None, "null"):
        raise ProtocolError(503, "disaggregated_backend_unproven")
    return page_size


def _encode(tm, text):
    try:
        ids = tm.tokenizer.encode(text, add_special_tokens=False)
    except Exception:
        raise ProtocolError(502, "tokenizer_failed") from None
    if not ids or any(type(token) is not int or token < 0 for token in ids):
        raise ProtocolError(400, "invalid_tokenization")
    return list(ids)


def _finished(meta):
    reason = meta.get("finish_reason")
    return (isinstance(reason, dict) and reason.get("type") in ("stop", "length")
            and type(meta.get("completion_tokens")) is int
            and meta["completion_tokens"] > 0
            and type(meta.get("num_retractions")) is int
            and meta["num_retractions"] == 0)


def gpu_cache_evidence(meta, required):
    """No aggregate-to-device guess. Missing tier details cannot prove reuse."""
    details = meta.get("cached_tokens_details")
    cached = meta.get("cached_tokens")
    if not isinstance(details, dict) or type(cached) is not int:
        return None
    device, host, storage = (details.get("device"), details.get("host"),
                             details.get("storage", 0))
    if any(type(value) is not int or value < 0 for value in (device, host, storage)):
        return None
    prompt = meta.get("prompt_tokens")
    if (device < required or host != 0 or storage != 0 or cached != device
            or type(prompt) is not int or device > prompt):
        return None
    return {"device": device, "host": host, "storage": storage}


def _view(capture):
    return {
        "protocol": PROTOCOL, "capture_handle": capture.handle,
        "process_epoch": capture.epoch, "source_request_id": capture.source_rid,
        "model_fingerprint": capture.model_fingerprint,
        "required_gpu_tokens": capture.required_gpu_tokens,
        "source_prompt_tokens": len(capture.input_ids),
        "guard_tokens": len(capture.input_ids) - capture.required_gpu_tokens,
        "salt_sha256": hashlib.sha256(capture.salt.encode()).hexdigest(),
        "state": capture.state, "residency": "lru_evictable",
        "source_cached_tokens": capture.meta.get("cached_tokens"),
        "source_completion_tokens": capture.meta.get("completion_tokens"),
        "remaining_ttl_s": max(0.0, capture.expires - store.clock()),
        "capture_kind": "source_prompt_kv", "hidden_state_capture": False,
        "method_scope": "text_radix_baseline", "num_retractions": 0,
        "identity_scope": "process_local_metadata_not_weight_attestation",
    }


@router.get("/capabilities")
async def capabilities():
    tm = _tm()
    try:
        _runtime(tm)
        available, reason = True, None
    except ProtocolError as exc:
        available, reason = False, exc.detail
    return {
        "protocol": PROTOCOL, "available": available, "reason": reason,
        "process_epoch": store.epoch, "explicit_opt_in_required": True,
        "native_receiver_integrated": False,
        "capture_kind": "source_prompt_kv", "handle_only_send": True,
        "method_scope": "text_radix_baseline", "true_latent_method": False,
        "identity_scope": "process_local_metadata_not_weight_attestation",
        "gpu_evidence": "sglang_cached_tokens_details.device",
        "gpu_pin_supported": False, "release_kind": "handle_retirement_lru",
        "operation_timeout_s": OPERATION_TIMEOUT_S,
        "scheduler_cleanup_ack_supported": False,
        "uncertain_cleanup": "quota_retained_until_process_restart",
        "session_authorization": "caller_labels_under_shared_authenticated_broker",
        "model_fingerprint": fingerprint(compute_identity(tm)),
    }


@router.post("/capture")
async def capture_source(body: CaptureBody):
    tm = _tm()
    model_fp = _check(body, tm)
    page = _runtime(tm)
    facts = _encode(tm, body.text)
    if len(facts) > MAX_FACT_TOKENS:
        raise ProtocolError(400, "source_prompt_too_large")
    guards = _encode(tm, "\n")
    # Keep the entire meaningful prefix on committed cache pages in EAGLE.
    guard_ids = (guards * (2 * page + len(guards)))[:2 * page]
    capture = store.reserve(body.session_id, model_fp, body.ttl_s)
    capture.required_gpu_tokens = len(facts)
    capture.input_ids = facts + guard_ids
    try:
        from sglang.srt.managers.io_struct import GenerateReqInput
        obj = GenerateReqInput(
            input_ids=list(capture.input_ids), cache_salt=capture.salt,
            rid=capture.source_rid, session_id=body.session_id,
            return_prompt_token_ids=True,
            sampling_params={"max_new_tokens": 1, "temperature": 0.0},
        )
        out = await _run_operation(store, capture, tm, obj)
        meta = out.get("meta_info", {}) if isinstance(out, dict) else {}
        if (not _finished(meta) or meta.get("cached_tokens") != 0
                or meta.get("id") != capture.source_rid
                or "weight_version" not in meta or meta.get("weight_versions")
                or meta.get("prompt_tokens") != len(capture.input_ids)
                or out.get("prompt_token_ids") != capture.input_ids):
            raise ProtocolError(502, "source_capture_unproven")
        dp_size = getattr(tm.server_args, "dp_size", 1)
        if dp_size > 1 and type(meta.get("dp_rank")) is not int:
            raise ProtocolError(503, "source_replica_unproven")
        with store.lock:
            capture.meta = {key: meta.get(key) for key in (
                "cached_tokens", "completion_tokens", "dp_rank", "weight_version"
            )}
            capture.state = "CAPTURED"
            capture.expires = store.clock() + body.ttl_s
            capture.in_flight = False
        metrics.inc("source_capture_ok")
        return _view(capture)
    except BaseException as exc:
        store.retire(capture)
        metrics.inc("source_capture_fail")
        if isinstance(exc, ProtocolError):
            raise
        if isinstance(exc, Exception):
            raise ProtocolError(502, "source_capture_failed") from None
        raise


@router.post("/send")
async def send_handle(body: SendHandleBody):
    current = _check(body, _tm())
    try:
        validate_channel_id(body.receiver_session_id)
    except ValidationError:
        raise ProtocolError(400, "invalid_receiver_session")
    with store.lock:
        capture = store.get(body.capture_handle)
        if capture.owner != body.session_id or capture.model_fingerprint != current:
            raise ProtocolError(403, "source_binding_mismatch")
        if capture.state != "CAPTURED" or capture.in_flight:
            raise ProtocolError(409, "capture_not_sendable")
        capture.channel_handle = store.epoch + "." + uuid.uuid4().hex
        capture.receiver = body.receiver_session_id
        capture.state = "BOUND"
        store.channels[capture.channel_handle] = capture.handle
        view = _view(capture)
        view.update(channel_handle=capture.channel_handle,
                    receiver_session_id=capture.receiver, send_inference_requests=0,
                    transport="opaque_handle_only")
    metrics.inc("handle_send_ok")
    return view


@router.post("/recv")
async def receive_handle(body: ReceiveHandleBody):
    tm = _tm()
    current = _check(body, tm)
    _runtime(tm)
    suffix_ids = _encode(tm, body.suffix)
    if len(suffix_ids) > 2048:
        raise ProtocolError(400, "suffix_too_large")
    with store.lock:
        capture = store.get(body.channel_handle, channel=True)
        if (capture.receiver != body.session_id or capture.model_fingerprint != current
                or capture.state != "BOUND"):
            raise ProtocolError(403, "receiver_binding_mismatch")
        if capture.in_flight:
            raise ProtocolError(409, "capture_busy")
        capture.in_flight = True
        capture.state = "CONSUMING"
        input_ids = list(capture.input_ids) + suffix_ids
    rid = "occ3-receive-" + uuid.uuid4().hex
    try:
        from sglang.srt.managers.io_struct import GenerateReqInput
        obj = GenerateReqInput(
            input_ids=input_ids, cache_salt=capture.salt, rid=rid,
            session_id=body.session_id, routed_dp_rank=capture.meta.get("dp_rank"),
            sampling_params={"max_new_tokens": body.max_new_tokens, "temperature": 0.0},
        )
        out = await _run_operation(store, capture, tm, obj, consume=True)
        meta = out.get("meta_info", {}) if isinstance(out, dict) else {}
        evidence = gpu_cache_evidence(meta, capture.required_gpu_tokens)
        if (evidence is None or not _finished(meta)
                or meta.get("id") != rid or "weight_version" not in meta
                or meta["weight_version"] != capture.meta["weight_version"]
                or meta.get("weight_versions")
                or meta.get("prompt_tokens") != len(input_ids)
                or not isinstance(out.get("text"), str) or not out["text"].strip()
                or (capture.meta.get("dp_rank") is not None
                    and meta.get("dp_rank") != capture.meta["dp_rank"])):
            metrics.inc("handle_recv_miss")
            # Inference may have recomputed a missed prefix. Never expose that answer.
            store.retire(capture)
            raise ProtocolError(409, "gpu_reuse_unproven_no_fallback")
        with store.lock:
            if capture.expires <= store.clock() or capture.state != "CONSUMING":
                raise ProtocolError(410, "capture_expired_during_receive")
            capture.state = "CONSUMED"
            view = _view(capture)
        metrics.inc("handle_recv_hit")
        view.update(
            channel_handle=body.channel_handle, request_id=rid, hit=True,
            cache_tiers=evidence, cached_tokens=meta["cached_tokens"],
            prompt_tokens=meta["prompt_tokens"], completion_tokens=meta["completion_tokens"],
            output_text=out["text"], fallback_used=False,
            transport="opaque_handle_only", source_prefix_reused=True,
            recomputed_meaningful_tokens=0,
        )
        return view
    except BaseException as exc:
        store.retire(capture)
        metrics.inc("handle_recv_fail")
        if isinstance(exc, ProtocolError):
            raise
        if isinstance(exc, Exception):
            raise ProtocolError(502, "receiver_failed_no_fallback") from None
        raise
    finally:
        with store.lock:
            capture.in_flight = not capture.local_task_closed
            store._sweep()


@router.post("/release")
async def release_handle(body: ReleaseHandleBody):
    current = _check(body, _tm())
    with store.lock:
        capture = store.get(body.capture_handle)
        if capture.owner != body.session_id or capture.model_fingerprint != current:
            raise ProtocolError(403, "source_binding_mismatch")
        if capture.in_flight:
            raise ProtocolError(409, "capture_busy")
        store._retire(capture)
    metrics.inc("handle_release_ok")
    return {"released": True, "state": "RETIRED", "process_epoch": store.epoch,
            "cpu_references_removed": True, "gpu_erased": False,
            "release_kind": "handle_retirement_lru"}
