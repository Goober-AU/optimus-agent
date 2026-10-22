"""HTTP control routes for OCC-3 (mounted under /occ3/v1).

Security: the routes are served by the same FastAPI app as /generate,
so the server-level api-key middleware covers them (paths under
/occ3/ are auth level NORMAL). The plugin deliberately avoids any
/health* path prefix so nothing here bypasses the key.

All routes are control-plane only: no tensors, no GPU state and no
model weights ever appear in requests or responses.
"""

from __future__ import annotations

from typing import List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from . import __version__, metrics
from .identity import IDENTITY_FIELDS, compute_identity, fingerprint
from .registry import registry
from .security import DEFAULT_TTL_S

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


@router.get("/status")
async def status() -> StatusView:
    identity = compute_identity(_tm())
    return StatusView(
        plugin="occ3-bridge",
        version=__version__,
        enabled=False,
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
        "supported": False,
        "reason": "native_latent_receiver_not_implemented",
        "experimental_prompt_kv_path": "/occ3/v1/latent/capabilities",
        "model_fingerprint": fingerprint(identity),
        "connector_id": "sglang-occ3",
        "methods": [],
        "experimental_baselines": ["text-radix", "source-prompt-kv-handle-v1"],
        "protocol": "occ3-v1",
        "api_version": "v1",
        "same_model_only": True,
        "verification": "cached_tokens",
        "cache": {"salt_prefix": "occ3-", "eviction": "lru"},
    }


@router.get("/channels")
async def channels() -> dict:
    return {"channels": [], "available": False, "reason": "legacy_unbound_api_disabled"}


@router.post("/send")
async def send() -> dict:
    raise HTTPException(410, "legacy_unbound_api_disabled_use_opt_in_handles")


@router.post("/recv")
async def recv() -> dict:
    raise HTTPException(410, "legacy_unbound_api_disabled_use_opt_in_handles")


@router.post("/release")
async def release() -> dict:
    raise HTTPException(410, "legacy_unbound_api_disabled_use_opt_in_handles")
