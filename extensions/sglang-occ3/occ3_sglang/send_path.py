"""Carrier send path: write one message's KV into a salted namespace.

The carrier request is an ordinary generation request whose prompt is
the sender's message content, executed under the channel's
``cache_salt``. Because the request carries no embed overrides, prefix
lookup stays enabled (it will simply miss in the fresh salted
namespace), and on completion the scheduler inserts the full KV row
set into the radix tree under that salt. The latent now lives on the
GPU; nothing crosses a process boundary.

With ``embeds`` the request additionally injects latent rows at chosen
positions via ``positional_embed_overrides`` (the hybrid StateBridge
arm): lookup is disabled for that request by the scheduler, so it
always full-prefills before inserting.
"""

from __future__ import annotations

import logging
from typing import Dict, List, Optional

from .channel import Channel

logger = logging.getLogger(__name__)


async def _first_output(tokenizer_manager: object, obj: object) -> Dict:
    """Await the first (final, for non-stream) output of a request."""
    gen = tokenizer_manager.generate_request(obj, None)
    try:
        out = await gen.__anext__()
    finally:
        await gen.aclose()
    return out


def _meta(out: Dict) -> Dict:
    meta = out.get("meta_info") if isinstance(out, dict) else None
    return meta if isinstance(meta, dict) else {}


async def send_carrier(
    tokenizer_manager: object,
    channel: Channel,
    *,
    input_ids: Optional[List[int]] = None,
    text: Optional[str] = None,
    embeds: Optional[List[List[float]]] = None,
    positions: Optional[List[int]] = None,
    max_new_tokens: int = 1,
) -> Dict[str, object]:
    """Execute the carrier request for a channel.

    Returns a summary dict with the carrier token ids (exact ids the
    server used when the caller passed ``text``), cache-hit accounting
    and the response metadata.
    """
    from sglang.srt.managers.io_struct import GenerateReqInput

    if (input_ids is None) == (text is None):
        raise ValueError("exactly one of input_ids or text is required")

    rid = f"occ3-send-{channel.channel_id}"
    kwargs: Dict[str, object] = {
        "cache_salt": channel.salt,
        "sampling_params": {
            "max_new_tokens": max_new_tokens,
            "temperature": 0.0,
        },
        "return_prompt_token_ids": True,
        "rid": rid,
    }
    if input_ids is not None:
        kwargs["input_ids"] = list(input_ids)
    else:
        kwargs["text"] = text

    if embeds is not None:
        import torch
        from sglang.srt.managers.embed_types import PositionalEmbeds

        kwargs["positional_embed_overrides"] = PositionalEmbeds(
            torch.tensor(embeds, dtype=torch.float32),
            list(positions or []),
        )

    obj = GenerateReqInput(**kwargs)
    out = await _first_output(tokenizer_manager, obj)

    meta = _meta(out)
    carrier_ids: List[int]
    if input_ids is not None:
        carrier_ids = list(input_ids)
    else:
        returned = out.get("prompt_token_ids")
        if not returned:
            raise RuntimeError(
                "server did not return prompt_token_ids for a text carrier"
            )
        carrier_ids = [int(t) for t in returned]

    summary: Dict[str, object] = {
        "channel": channel.channel_id,
        "salt": channel.salt,
        "carrier_token_ids": carrier_ids,
        "carrier_tokens": len(carrier_ids),
        "cached_tokens": int(meta.get("cached_tokens", 0) or 0),
        "prompt_tokens": int(meta.get("prompt_tokens", len(carrier_ids)) or 0),
        "completion_tokens": int(meta.get("completion_tokens", 0) or 0),
        "finish_reason": meta.get("finish_reason"),
        "rid": rid,
    }
    logger.info(
        "occ3 send channel=%s carrier_tokens=%d cached=%d",
        channel.channel_id,
        len(carrier_ids),
        summary["cached_tokens"],
    )
    return summary
