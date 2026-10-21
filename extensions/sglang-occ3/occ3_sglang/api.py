"""HTTP control routes for OCC-3 (mounted under /occ3/v1).

Security: the routes are served by the same FastAPI app as /generate,
so the server-level api-key middleware covers them (paths under
/occ3/ are auth level NORMAL). The plugin deliberately avoids any
/health* path prefix so nothing here bypasses the key.

All routes are control-plane only: no tensors, no GPU state and no
model weights ever appear in requests or responses.
"""

from __future__ import annotations

import logging
import uuid
from typing import List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from . import __version__, metrics
from .channel import Channel, ChannelState
from .identity import IDENTITY_FIELDS, compute_identity, fingerprint
from .registry import ChannelFullError, registry
from .security import (
    DEFAULT_TTL_S,
    ValidationError,
    validate_channel_id,
    validate_recv,
    validate_ttl,
    validate_embeds,
    validate_carrier,
)
from .send_path import send_carrier
from .recv_path import recv_from_channel

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/occ3/v1", tags=["occ3"])


def _state():
    """Live server state (module attribute read at request time)."""
    import sglang.srt.entrypoints.http_server as http_server

    state = getattr(http_server, "_global_state", None)
    if state is None or getattr(state, "tokenizer_manager", None) is None:
        raise HTTPException(status_code=503, detail="server not ready")
    return state


def _tm():
    return _state().tokenizer_manager


class SendBody(BaseModel):
    channel: Optional[str] = None
    text: Optional[str] = None
    input_ids: Optional[List[int]] = None
    embeds: Optional[List[List[float]]] = None
    positions: Optional[List[int]] = None
    ttl_s: float = DEFAULT_TTL_S


class RecvBody(BaseModel):
    channel: str
    suffix: Optional[str] = None
    suffix_ids: Optional[List[int]] = None
    max_new_tokens: int = 256
    temperature: float = 0.0


class ReleaseBody(BaseModel):
    channel: str


class StatusView(BaseModel):
    plugin: str
    version: str
    enabled: bool
    model_fingerprint: Optional[str]
    channels: dict
    metrics: dict


def _tokenize(text: str) -> List[int]:
    tokenizer = getattr(_tm(), "tokenizer", None)
    if tokenizer is None:
        raise HTTPException(status_code=503, detail="tokenizer unavailable")
    try:
        ids = tokenizer.encode(text)
    except AttributeError:
        ids = tokenizer(text)["input_ids"]
    if not ids:
        raise HTTPException(status_code=400, detail="text tokenized to 0 tokens")
    return [int(t) for t in ids]


@router.get("/status")
async def status() -> StatusView:
    identity = compute_identity(_tm())
    return StatusView(
        plugin="occ3-bridge",
        version=__version__,
        enabled=True,
        model_fingerprint=fingerprint(identity),
        channels=registry().counts(),
        metrics=metrics.snapshot(),
    )


@router.get("/identity")
async def identity_route() -> dict:
    identity = compute_identity(_tm())
    return {
        "schema": "optimus.occ3.identity.v1",
        "fields": identity,
        "fingerprint": fingerprint(identity),
        "field_order": list(IDENTITY_FIELDS),
    }


@router.get("/capabilities")
async def capabilities() -> dict:
    """Capability advertisement, shaped for the Rust Occ3CapabilityAdvertisement."""
    identity = compute_identity(_tm())
    return {
        "schema": "optimus.occ3.capability.v1",
        "supported": True,
        "model_fingerprint": fingerprint(identity),
        "connector_id": "sglang-occ3",
        "methods": ["kv-handoff"],
        "protocol": "occ3-v1",
        "api_version": "v1",
        "same_model_only": True,
        "verification": "cached_tokens",
        "cache": {"salt_prefix": "occ3-", "eviction": "lru"},
    }


@router.get("/channels")
async def channels() -> dict:
    return {"channels": registry().list_channels()}


@router.post("/send")
async def send(body: SendBody) -> dict:
    channel_id = body.channel or uuid.uuid4().hex[:12]
    try:
        validate_channel_id(channel_id)
    except ValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    if (body.text is None) == (body.input_ids is None):
        raise HTTPException(
            status_code=400, detail="exactly one of text or input_ids required"
        )

    # Resolve the carrier to token ids up front (text is tokenized with
    # the server's own tokenizer) so every bound is validated BEFORE
    # any request reaches the scheduler.
    carrier_ids: List[int]
    if body.input_ids is not None:
        carrier_ids = [int(t) for t in body.input_ids]
    else:
        carrier_ids = _tokenize(body.text or "")

    try:
        carrier_len = validate_carrier(carrier_ids)
        validate_ttl(body.ttl_s)
        validate_embeds(body.embeds, body.positions, carrier_len)
    except ValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    # The salt derives from the channel id, so the carrier can be sent
    # before the registry entry exists.
    from .channel import salt_for

    provisional = Channel(
        channel_id=channel_id,
        salt=salt_for(channel_id),
        carrier_token_ids=carrier_ids,
        created_at=0.0,
        ttl_s=body.ttl_s,
    )
    try:
        summary = await send_carrier(
            _tm(),
            provisional,
            input_ids=carrier_ids,
            embeds=body.embeds,
            positions=body.positions,
        )
    except HTTPException:
        raise
    except Exception as exc:
        metrics.inc("send_fail")
        logger.exception("occ3 send failed for channel %s", channel_id)
        raise HTTPException(status_code=502, detail=f"carrier failed: {exc}")

    resolved_ids = summary["carrier_token_ids"]
    try:
        channel = registry().create(channel_id, resolved_ids, body.ttl_s)
    except KeyError:
        raise HTTPException(status_code=409, detail=f"channel exists: {channel_id}")
    except ChannelFullError as exc:
        raise HTTPException(status_code=429, detail=str(exc))

    registry().record_send(channel_id, dict(summary))
    metrics.inc("send_ok")
    return {
        "channel": channel.channel_id,
        "salt": channel.salt,
        "carrier_tokens": channel.carrier_len,
        "ttl_s": channel.ttl_s,
        "meta": summary,
    }


@router.post("/recv")
async def recv(body: RecvBody) -> dict:
    try:
        validate_channel_id(body.channel)
    except ValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    channel = registry().get(body.channel)
    if channel is None:
        raise HTTPException(status_code=404, detail=f"unknown channel: {body.channel}")

    if (body.suffix is None) == (body.suffix_ids is None):
        raise HTTPException(
            status_code=400, detail="exactly one of suffix or suffix_ids required"
        )
    suffix_ids = (
        list(body.suffix_ids) if body.suffix_ids is not None else _tokenize(body.suffix)
    )
    try:
        validate_recv(
            suffix_ids=suffix_ids,
            max_new_tokens=body.max_new_tokens,
            temperature=body.temperature,
        )
    except ValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    try:
        summary = await recv_from_channel(
            _tm(),
            channel,
            suffix_ids=suffix_ids,
            max_new_tokens=body.max_new_tokens,
            temperature=body.temperature,
        )
    except Exception as exc:
        metrics.inc("recv_fail")
        logger.exception("occ3 recv failed for channel %s", body.channel)
        raise HTTPException(status_code=502, detail=f"receiver request failed: {exc}")

    registry().record_recv(body.channel, bool(summary["hit"]))
    metrics.inc("recv_ok")
    return summary


@router.post("/release")
async def release(body: ReleaseBody) -> dict:
    prior = registry().release(body.channel)
    if prior is None and registry().get(body.channel) is None:
        return {"channel": body.channel, "released": False, "state": "unknown"}
    return {
        "channel": body.channel,
        "released": True,
        "prior_state": prior.value if prior is not None else None,
        "state": ChannelState.CONSUMED.value,
    }
