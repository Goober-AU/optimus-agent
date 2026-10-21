"""Channel registry: the OCC-3 mailbox bookkeeping.

Thread-safe (a plain lock; every operation is O(1) and non-blocking).
Expiry is lazy plus swept on read: a channel past its TTL reports
EXPIRED and stops being returned, which lets the HTTP layer respond
with a precise status while the radix tree reclaims its rows through
the normal LRU path.
"""

from __future__ import annotations

import threading
import time
from typing import Dict, List, Optional

from .channel import Channel, ChannelState, salt_for
from . import metrics

MAX_CHANNELS = 256


class ChannelFullError(RuntimeError):
    """Registry capacity reached; refuse new channels."""


class ChannelRegistry:
    """In-memory channel table (one per server process)."""

    def __init__(self, max_channels: int = MAX_CHANNELS) -> None:
        self._lock = threading.Lock()
        self._channels: Dict[str, Channel] = {}
        self._max_channels = max_channels

    def create(
        self,
        channel_id: str,
        carrier_token_ids: List[int],
        ttl_s: float,
    ) -> Channel:
        with self._lock:
            self._sweep_locked()
            if channel_id in self._channels:
                existing = self._channels[channel_id]
                if existing.state == ChannelState.CONSUMED:
                    # A released channel id may be reused after consumption.
                    del self._channels[channel_id]
                else:
                    raise KeyError(f"channel already exists: {channel_id}")
            if len(self._channels) >= self._max_channels:
                raise ChannelFullError(
                    f"registry full ({self._max_channels} channels)"
                )
            channel = Channel(
                channel_id=channel_id,
                salt=salt_for(channel_id),
                carrier_token_ids=list(carrier_token_ids),
                created_at=time.time(),
                ttl_s=ttl_s,
                state=ChannelState.ACTIVE,
                last_used=time.time(),
            )
            self._channels[channel_id] = channel
            return channel

    def get(self, channel_id: str) -> Optional[Channel]:
        """Return the channel if usable; lazily expire stale entries."""
        with self._lock:
            channel = self._channels.get(channel_id)
            if channel is None:
                return None
            if channel.state == ChannelState.ACTIVE and channel.expired():
                channel.state = ChannelState.EXPIRED
                metrics.inc("recv_miss")
                return None
            if channel.state != ChannelState.ACTIVE:
                return None
            return channel

    def record_send(self, channel_id: str, meta: dict) -> None:
        with self._lock:
            channel = self._channels.get(channel_id)
            if channel is not None:
                channel.send_meta = dict(meta)
                channel.last_used = time.time()

    def record_recv(self, channel_id: str, hit: bool) -> None:
        with self._lock:
            channel = self._channels.get(channel_id)
            if channel is not None:
                channel.recv_attempts += 1
                if hit:
                    channel.recv_hits += 1
                channel.last_used = time.time()
        metrics.inc("recv_hit" if hit else "recv_miss")

    def release(self, channel_id: str) -> Optional[ChannelState]:
        """Mark consumed; returns the prior state (None if unknown)."""
        with self._lock:
            channel = self._channels.get(channel_id)
            if channel is None:
                return None
            prior = channel.state
            channel.state = ChannelState.CONSUMED
            channel.last_used = time.time()
            metrics.inc("release_ok")
            return prior

    def drop(self, channel_id: str) -> bool:
        with self._lock:
            return self._channels.pop(channel_id, None) is not None

    def list_channels(self) -> List[dict]:
        with self._lock:
            self._sweep_locked()
            return [
                ch.public_view()
                for ch in sorted(
                    self._channels.values(), key=lambda c: c.created_at
                )
            ]

    def counts(self) -> Dict[str, int]:
        with self._lock:
            self._sweep_locked()
            out: Dict[str, int] = {"total": len(self._channels)}
            for state in ChannelState:
                out[state.value.lower()] = sum(
                    1 for ch in self._channels.values() if ch.state == state
                )
            return out

    def _sweep_locked(self) -> int:
        swept = 0
        for channel in list(self._channels.values()):
            if channel.state == ChannelState.ACTIVE and channel.expired():
                channel.state = ChannelState.EXPIRED
                swept += 1
        return swept


_registry = ChannelRegistry()


def registry() -> ChannelRegistry:
    """Process-wide registry singleton."""
    return _registry
