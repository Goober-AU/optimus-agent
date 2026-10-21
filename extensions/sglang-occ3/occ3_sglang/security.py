"""Input validation for the OCC-3 plugin routes.

All limits are defensive: they bound what a single request may put into
the scheduler, the radix tree or the response body. Nothing here talks
to the network or the GPU.
"""

from __future__ import annotations

import math
import re
from typing import List, Optional, Sequence

CHANNEL_ID_RE = re.compile(r"^[A-Za-z0-9._-]{1,64}$")

MAX_CHANNELS = 256
DEFAULT_TTL_S = 900.0
MIN_TTL_S = 1.0
MAX_TTL_S = 3600.0

# Carrier (prompt) bounds. The deployed context is 1M tokens; the plugin
# cap stays far below to keep one channel from dominating the tree.
MAX_CARRIER_TOKENS = 65536
MIN_CARRIER_TOKENS = 1

# Latent-row injection bounds (hybrid StateBridge arm).
MAX_EMBED_ROWS = 512
MAX_EMBED_DIM = 32768

# Receiver decode bounds.
MAX_RECV_NEW_TOKENS = 1024
MAX_TEMPERATURE = 2.0


class ValidationError(ValueError):
    """Raised with a client-safe message (no internals, no secrets)."""


def validate_channel_id(channel_id: str) -> str:
    if not CHANNEL_ID_RE.match(channel_id):
        raise ValidationError(
            "channel id must match [A-Za-z0-9._-]{1,64}"
        )
    return channel_id


def _check_finite(rows: Sequence[Sequence[float]]) -> None:
    for row in rows:
        for value in row:
            if not math.isfinite(value):
                raise ValidationError("embeds must contain only finite values")


def validate_carrier(input_ids: List[int]) -> int:
    """Validate resolved carrier token ids; returns the length."""
    if not input_ids:
        raise ValidationError("carrier must tokenize to at least one token")
    if not isinstance(input_ids[0], int):
        raise ValidationError("carrier input_ids must be integers")
    if not (MIN_CARRIER_TOKENS <= len(input_ids) <= MAX_CARRIER_TOKENS):
        raise ValidationError(
            f"carrier length must be in [{MIN_CARRIER_TOKENS}, {MAX_CARRIER_TOKENS}]"
        )
    return len(input_ids)


def validate_ttl(ttl_s: float) -> None:
    if not (MIN_TTL_S <= ttl_s <= MAX_TTL_S):
        raise ValidationError(f"ttl_s must be in [{MIN_TTL_S}, {MAX_TTL_S}]")


def validate_embeds(
    embeds: Optional[List[List[float]]],
    positions: Optional[List[int]],
    carrier_len: Optional[int] = None,
) -> None:
    """Validate latent-row injection arguments.

    ``carrier_len`` is required to bound the positions; pass the
    resolved (already tokenized) carrier length.
    """
    if embeds is None:
        if positions is not None:
            raise ValidationError("positions provided without embeds")
        return
    if positions is None:
        raise ValidationError("positions required with embeds")
    if len(embeds) != len(positions):
        raise ValidationError("embeds and positions lengths must match")
    if not (1 <= len(embeds) <= MAX_EMBED_ROWS):
        raise ValidationError(f"embeds rows must be in [1, {MAX_EMBED_ROWS}]")
    dim = len(embeds[0])
    if not (1 <= dim <= MAX_EMBED_DIM):
        raise ValidationError(f"embed dim must be in [1, {MAX_EMBED_DIM}]")
    for row in embeds:
        if len(row) != dim:
            raise ValidationError("embed rows must share one dimension")
    _check_finite(embeds)
    if carrier_len is None:
        raise ValidationError("embeds require a resolved carrier length")
    prev = -1
    for pos in positions:
        if not isinstance(pos, int):
            raise ValidationError("positions must be integers")
        if pos <= prev:
            raise ValidationError("positions must be strictly increasing")
        if pos < 0 or pos >= carrier_len:
            raise ValidationError("positions must index into the carrier tokens")
        prev = pos


def validate_send(
    *,
    text: Optional[str],
    input_ids: Optional[List[int]],
    embeds: Optional[List[List[float]]],
    positions: Optional[List[int]],
    ttl_s: float,
) -> int:
    """Validate a fully-resolved /occ3/v1/send body (carrier as ids)."""
    if text is not None:
        raise ValidationError("text must be tokenized by the caller")
    carrier_len = validate_carrier(input_ids or [])
    validate_ttl(ttl_s)
    validate_embeds(embeds, positions, carrier_len)
    return carrier_len


def validate_recv(
    *,
    suffix_ids: List[int],
    max_new_tokens: int,
    temperature: float,
) -> None:
    if len(suffix_ids) == 0:
        raise ValidationError("suffix must tokenize to at least one token")
    if not (1 <= max_new_tokens <= MAX_RECV_NEW_TOKENS):
        raise ValidationError(
            f"max_new_tokens must be in [1, {MAX_RECV_NEW_TOKENS}]"
        )
    if not (0.0 <= temperature <= MAX_TEMPERATURE):
        raise ValidationError(f"temperature must be in [0, {MAX_TEMPERATURE}]")
