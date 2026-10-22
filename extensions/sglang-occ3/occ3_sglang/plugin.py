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

    try:
        import sglang.srt.entrypoints.http_server as http_server

        from .api import router
        from .kv_handles import router as handle_router

        app = getattr(http_server, "app", None)
        if app is None:
            raise RuntimeError("http_server.app missing")
        if not getattr(app, "_occ3_router_installed", False):
            previous_routes = list(app.router.routes)
            try:
                app.include_router(router)
                app.include_router(handle_router)
            except Exception:
                app.router.routes[:] = previous_routes
                raise
            app._occ3_router_installed = True
            logger.info(
                "occ3-bridge: routes mounted under /occ3/v1 (version %s)",
                __import__("occ3_sglang", fromlist=["__version__"]).__version__,
            )
    except Exception:
        logger.error("occ3-bridge: route installation failed")
        return

    try:
        from .capture import register_capture_hook

        register_capture_hook()
    except Exception:
        logger.error("occ3-bridge: capture hook registration failed")
        return
    _LOADED = True


async def install_native(app, tokenizer_manager, *, experimental_opt_in=False, buffer_profile=None):
    """Trusted pre-serving bootstrap only; ordinary load() never invokes this."""
    from .native_install import install_native_runtime

    return await install_native_runtime(app, tokenizer_manager,
        experimental_opt_in=experimental_opt_in, buffer_profile=buffer_profile)
