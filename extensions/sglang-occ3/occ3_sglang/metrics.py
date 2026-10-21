"""Thread-safe metric counters for the OCC-3 plugin."""

from __future__ import annotations

import threading
import time
from typing import Dict

_KNOWN_COUNTERS = (
    "send_ok",
    "send_fail",
    "recv_ok",
    "recv_fail",
    "recv_hit",
    "recv_miss",
    "release_ok",
    "capture_ok",
    "capture_fail",
)


class Metrics:
    """Simple counter registry; safe to bump from any thread."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._counters: Dict[str, int] = {name: 0 for name in _KNOWN_COUNTERS}
        self._started = time.time()

    def inc(self, name: str, amount: int = 1) -> None:
        with self._lock:
            if name not in self._counters:
                # Unknown counters are still tracked (forward compatibility).
                self._counters[name] = 0
            self._counters[name] += amount

    def snapshot(self) -> Dict[str, object]:
        with self._lock:
            counters = dict(self._counters)
        return {
            "counters": counters,
            "uptime_seconds": round(time.time() - self._started, 3),
        }


_metrics = Metrics()


def inc(name: str, amount: int = 1) -> None:
    """Bump a plugin counter."""
    _metrics.inc(name, amount)


def snapshot() -> Dict[str, object]:
    """Return a copy of all counters plus uptime."""
    return _metrics.snapshot()
