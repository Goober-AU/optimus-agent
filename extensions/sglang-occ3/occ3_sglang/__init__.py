"""OCC-3 latent communication plugin for SGLang.

Implements the connector side of the Optimus OCC-3 bridge: salted
KV-radix channels that let agents exchange latent state through the
GPU-resident radix cache of one SGLang server.

The package is a plain SGLang plugin (entry point group
``sglang.srt.plugins``) and adds:

- HTTP control routes under ``/occ3/v1/*`` (main process), and
- an optional, dormant hidden-state capture hook (scheduler process).

The generic Rust bridge in ``crates/pi-coding-agent/src/core/occ/occ3``
stays engine-agnostic: everything SGLang-specific lives here.
"""

__version__ = "0.1.0"

__all__ = ["__version__"]
