"""Dormant hidden-state capture hook (OCC-3 v1.1 substrate).

Registers an AROUND hook on
``sglang.srt.speculative.eagle_worker_common.run_eagle_verify`` that
clones the accepted-token hidden states on the forward stream (CUDA
graph buffers are reused by the next replay) and keeps the most
recent capture per request in process-local memory.

OFF by default: set ``OCC3_CAPTURE_ENABLED=1`` in the server
environment to arm it. The v1 primary arm (KV-radix channel) does not
need this; it exists for the portable StateBridge arm, latent export
and observability.
"""

from __future__ import annotations

import logging
import os
import time
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

TARGET = "sglang.srt.speculative.eagle_worker_common.run_eagle_verify"

_MAX_CAPTURES = 8
_captures: Dict[str, Dict[str, Any]] = {}
_registered = False


def _enabled() -> bool:
    return os.environ.get("OCC3_CAPTURE_ENABLED", "").lower() in ("1", "true", "yes")


def register_capture_hook() -> None:
    """Arm the capture hook when the env flag is set."""
    global _registered
    if _registered:
        return
    if not _enabled():
        logger.info("occ3-bridge: capture hook dormant (OCC3_CAPTURE_ENABLED unset)")
        return
    from sglang.srt.plugins.hook_registry import HookRegistry, HookType

    HookRegistry.register(TARGET, _capture_around, HookType.AROUND)
    _registered = True
    logger.info("occ3-bridge: capture hook armed on %s", TARGET)


def _capture_around(original_fn, *args, **kwargs):
    result = original_fn(*args, **kwargs)
    try:
        _store(result, args, kwargs)
    except Exception:
        logger.exception("occ3-bridge: capture store failed")
    return result


def _rank_ok() -> bool:
    """Store once per replica: rank 0 when distributed, else always."""
    try:
        import torch.distributed as dist

        if dist.is_available() and dist.is_initialized():
            return dist.get_rank() == 0
    except Exception:
        pass
    return True


def _store(result: Any, args: tuple, kwargs: Dict[str, Any]) -> None:
    if not _rank_ok():
        return
    logits_output = getattr(result, "logits_output", None)
    hidden_states = getattr(logits_output, "hidden_states", None)
    accept_index = getattr(result, "accept_index", None)
    if hidden_states is None or accept_index is None:
        return

    # Clone immediately: decode CUDA graph replay overwrites the
    # static output buffers (eagle_worker_v2.py comment on clone()).
    import torch

    rows = hidden_states[accept_index]
    captured_cpu = rows.detach().clone().to("cpu")

    batch = args[0] if args else kwargs.get("batch")
    rids = list(getattr(batch, "rids", []) or [])
    entry = {
        "hidden_states": captured_cpu,
        "accept_index": accept_index.detach().to("cpu").tolist()
        if hasattr(accept_index, "detach")
        else accept_index,
        "rids": rids,
        "shape": list(captured_cpu.shape),
        "captured_at": time.time(),
    }
    for rid in rids or ["<anonymous>"]:
        _captures[str(rid)] = entry
    while len(_captures) > _MAX_CAPTURES:
        _captures.pop(next(iter(_captures)))

    dump_dir = os.environ.get("OCC3_CAPTURE_DIR", "")
    if dump_dir:
        _dump(dump_dir, rids, entry)


def _dump(dump_dir: str, rids, entry: Dict[str, Any]) -> None:
    try:
        import os as _os
        from pathlib import Path

        stamp = int(entry["captured_at"] * 1000)
        for rid in rids or ["anonymous"]:
            path = Path(dump_dir) / f"occ3-capture-{rid}-{stamp}.pt"
            path.parent.mkdir(parents=True, exist_ok=True)
            import torch

            torch.save(entry["hidden_states"], path)
    except Exception:
        logger.exception("occ3-bridge: capture dump failed")


def get_capture(rid: str) -> Optional[Dict[str, Any]]:
    """Latest capture for a request id (observability only)."""
    entry = _captures.get(str(rid))
    if entry is None:
        return None
    out = dict(entry)
    out.pop("hidden_states", None)
    return out


def capture_count() -> int:
    return len(_captures)
