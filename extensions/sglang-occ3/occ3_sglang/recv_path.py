"""Receiver path: read a channel's KV as a zero-copy prefix hit.

The receiver replays the channel's carrier token ids under the SAME
``cache_salt`` and appends its task suffix. The radix lookup matches
the carrier prefix and hands back the sender's KV slot indices with no
copy; only the suffix is prefilled. ``cached_tokens`` in the response
is the deterministic verification signal: it must cover the carrier.
"""

from __future__ import annotations

import logging
from typing import Dict, List, Optional

from .channel import Channel

logger = logging.getLogger(__name__)


async def recv_from_channel(
    tokenizer_manager: object,
    channel: Channel,
    *,
    suffix_ids: List[int],
    max_new_tokens: int = 256,
    temperature: float = 0.0,
) -> Dict[str, object]:
    """Execute the receiver request and verify the prefix hit."""
    from sglang.srt.managers.io_struct import GenerateReqInput

    input_ids = list(channel.carrier_token_ids) + list(suffix_ids)
    rid = f"occ3-recv-{channel.channel_id}"
    obj = GenerateReqInput(
        input_ids=input_ids,
        cache_salt=channel.salt,
        sampling_params={
            "max_new_tokens": max_new_tokens,
            "temperature": temperature,
        },
        rid=rid,
    )
    gen = tokenizer_manager.generate_request(obj, None)
    try:
        out = await gen.__anext__()
    finally:
        await gen.aclose()

    meta = out.get("meta_info") if isinstance(out, dict) else None
    meta = meta if isinstance(meta, dict) else {}
    cached = int(meta.get("cached_tokens", 0) or 0)
    hit = cached >= channel.carrier_len

    output_ids = out.get("output_ids") if isinstance(out, dict) else None
    summary: Dict[str, object] = {
        "channel": channel.channel_id,
        "hit": hit,
        "cached_tokens": cached,
        "carrier_tokens": channel.carrier_len,
        "prompt_tokens": int(meta.get("prompt_tokens", len(input_ids)) or 0),
        "completion_tokens": int(meta.get("completion_tokens", 0) or 0),
        "finish_reason": meta.get("finish_reason"),
        "output_ids": [int(t) for t in output_ids]
        if isinstance(output_ids, list)
        else [],
        "output_text": out.get("text") if isinstance(out, dict) else None,
        "rid": rid,
    }
    logger.info(
        "occ3 recv channel=%s cached=%d/%d hit=%s",
        channel.channel_id,
        cached,
        channel.carrier_len,
        hit,
    )
    return summary
