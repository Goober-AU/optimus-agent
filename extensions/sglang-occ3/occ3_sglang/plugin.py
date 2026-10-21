"""Plugin entry point for the SGLang unified plugin framework.

Loaded via the setuptools entry-point group ``sglang.srt.plugins``
in every server process (main + schedulers). The main process mounts
the control routes onto the FastAPI app before uvicorn serves it;
scheduler processes only register the (dormant) capture hook.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

_LOADED = False


def load() -> None:
    """Install routes and hooks; idempotent per process."""
    global _LOADED
    if _LOADED:
        return
    _LOADED = True

    try:
        import sglang.srt.entrypoints.http_server as http_server

        from .api import router

        app = getattr(http_server, "app", None)
        if app is None:
            raise RuntimeError("http_server.app missing")
        if not getattr(app, "_occ3_router_installed", False):
            app.include_router(router)
            app._occ3_router_installed = True
            logger.info(
                "occ3-bridge: routes mounted under /occ3/v1 (version %s)",
                __import__("occ3_sglang", fromlist=["__version__"]).__version__,
            )
    except Exception:
        logger.exception("occ3-bridge: route installation failed")

    try:
        from .capture import register_capture_hook

        register_capture_hook()
    except Exception:
        logger.exception("occ3-bridge: capture hook registration failed")
