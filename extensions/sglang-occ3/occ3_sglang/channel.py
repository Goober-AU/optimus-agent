"""OCC-3 channel state.

A channel is the server-side mailbox for one latent transfer: a
``cache_salt`` namespace in the radix tree plus the carrier token ids
a receiver must replay to hit the sender's KV rows prefix.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional


class ChannelState(str, Enum):
    """Lifecycle of a channel (mirrors the bridge delivery states)."""

    ACTIVE = "ACTIVE"
    CONSUMED = "CONSUMED"
    EXPIRED = "EXPIRED"


@dataclass
class Channel:
    """One latent mailbox in the radix cache."""

    channel_id: str
    salt: str
    carrier_token_ids: List[int]
    created_at: float
    ttl_s: float
    state: ChannelState = ChannelState.ACTIVE
    send_meta: Dict[str, object] = field(default_factory=dict)
    recv_attempts: int = 0
    recv_hits: int = 0
    last_used: float = 0.0

    @property
    def carrier_len(self) -> int:
        return len(self.carrier_token_ids)

    def expires_at(self) -> float:
        return self.created_at + self.ttl_s

    def expired(self, now: Optional[float] = None) -> bool:
        now = time.time() if now is None else now
        return now >= self.expires_at()

    def public_view(self, include_ids: bool = False) -> Dict[str, object]:
        view: Dict[str, object] = {
            "channel_id": self.channel_id,
            "salt": self.salt,
            "state": self.state.value,
            "carrier_tokens": self.carrier_len,
            "created_at": round(self.created_at, 3),
            "ttl_s": self.ttl_s,
            "recv_attempts": self.recv_attempts,
            "recv_hits": self.recv_hits,
        }
        if include_ids:
            view["carrier_token_ids"] = list(self.carrier_token_ids)
        return view


def salt_for(channel_id: str) -> str:
    """Deterministic radix-cache salt for a channel id."""
    return f"occ3-{channel_id}"
