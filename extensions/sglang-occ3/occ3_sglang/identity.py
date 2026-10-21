"""Model identity for the strict same-exact-model rule.

The identity fields and their order MUST match the Rust bridge type
``Occ3ModelIdentity`` in
``crates/pi-coding-agent/src/core/occ/occ3/types.rs`` so both sides can
compare the same ``|``-joined fingerprint string. A unit test in this
package reads the Rust source and asserts the parity.

Everything here reads only public, non-secret configuration: server
args, HF config, tokenizer config and weight shard metadata. It never
touches request data or API keys.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Dict, Optional

IDENTITY_FIELDS = (
    "model_id",
    "architecture",
    "checkpoint_revision",
    "weight_fingerprint",
    "tokenizer_fingerprint",
    "chat_template_fingerprint",
    "quantisation",
    "tensor_geometry",
    "kv_layout",
    "latent_method",
    "runtime",
)

LATENT_METHOD = "kv-radix-channel-v1"

# Bounded directory scan for weight fingerprints: shard counts stay in
# the tens even for very large checkpoints.
_MAX_DIR_ENTRIES = 256
_MAX_FILE_BYTES = 4 * 1024 * 1024


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()[:16]


def _dir_fingerprint(path: str) -> str:
    """Digest of a model directory: sorted (name, size) pairs, bounded."""
    try:
        entries = []
        with os.scandir(path) as it:
            for entry in it:
                entries.append((entry.name, entry.stat().st_size))
                if len(entries) >= _MAX_DIR_ENTRIES:
                    break
        if not entries:
            return ""
        entries.sort()
        payload = json.dumps(entries, separators=(",", ":"))
        return _sha(payload.encode("utf-8"))
    except OSError:
        return ""


def _file_fingerprint(path: Path) -> str:
    try:
        stat = path.stat()
        return _sha(f"{stat.st_size}".encode("utf-8"))
    except OSError:
        return ""


def _read_bounded(path: Path) -> Optional[bytes]:
    try:
        with open(path, "rb") as f:
            return f.read(_MAX_FILE_BYTES)
    except OSError:
        return None


def _first_str(value: object) -> str:
    if isinstance(value, (list, tuple)):
        return str(value[0]) if value else ""
    return str(value) if value is not None else ""


def _quantisation(server_args: object, hf_config: object) -> str:
    quant = getattr(hf_config, "quantization_config", None)
    if isinstance(quant, dict):
        method = quant.get("quant_method", "")
        fmt = quant.get("format", "")
        parts = [str(p) for p in (method, fmt) if p]
        if parts:
            return "-".join(parts).lower()
    value = getattr(server_args, "quantization", None)
    if value:
        return str(value)
    dtype = getattr(hf_config, "torch_dtype", "")
    return str(dtype or "none")


def _tensor_geometry(hf_config: object) -> str:
    text_config = getattr(hf_config, "text_config", None)
    cfg = text_config if text_config is not None else hf_config
    return ";".join(
        f"{key}={_first_str(getattr(cfg, key, ''))}"
        for key in (
            "hidden_size",
            "num_hidden_layers",
            "num_attention_heads",
            "vocab_size",
        )
    )


def _kv_layout(server_args: object) -> str:
    return ";".join(
        f"{key}={_first_str(getattr(server_args, key, ''))}"
        for key in (
            "page_size",
            "kv_cache_dtype",
            "tp_size",
            "speculative_algorithm",
        )
    )


def _runtime() -> str:
    try:
        import sglang  # noqa: PLC0415 (runtime-detected, late on purpose)

        return f"sglang/{getattr(sglang, '__version__', 'unknown')}"
    except Exception:
        return "sglang/unknown"


def compute_identity(tokenizer_manager: object) -> Dict[str, str]:
    """Build the 11-field identity from a live TokenizerManager.

    Every accessor is defensive: identity computation must never crash
    a route. Unknown components degrade to empty strings (which still
    compare equal only with equally-unknown peers).
    """
    server_args = getattr(tokenizer_manager, "server_args", None)
    model_config = getattr(tokenizer_manager, "model_config", None)
    hf_config = getattr(model_config, "hf_config", None)

    model_path = str(getattr(server_args, "model_path", "") or "")
    revision = _first_str(
        getattr(server_args, "revision", None)
        or getattr(hf_config, "_commit_hash", None)
    )
    architectures = getattr(hf_config, "architectures", None) or []
    architecture = str(architectures[0]) if architectures else ""

    tokenizer_path = str(
        getattr(server_args, "tokenizer_path", None) or model_path
    )
    tokenizer_dir = Path(tokenizer_path) if tokenizer_path else None

    tokenizer_fingerprint = ""
    chat_template_fingerprint = ""
    if tokenizer_dir is not None:
        config_bytes = _read_bounded(tokenizer_dir / "tokenizer_config.json")
        if config_bytes is not None:
            tokenizer_fingerprint = _sha(config_bytes)
            try:
                config = json.loads(config_bytes)
                template = config.get("chat_template")
                if isinstance(template, str):
                    chat_template_fingerprint = _sha(template.encode("utf-8"))
            except (ValueError, TypeError):
                pass
        if not tokenizer_fingerprint:
            tokenizer_fingerprint = _file_fingerprint(
                tokenizer_dir / "tokenizer_config.json"
            )

    return {
        "model_id": _first_str(
            getattr(tokenizer_manager, "served_model_name", None)
        ),
        "architecture": architecture,
        "checkpoint_revision": revision,
        "weight_fingerprint": _dir_fingerprint(model_path),
        "tokenizer_fingerprint": tokenizer_fingerprint,
        "chat_template_fingerprint": chat_template_fingerprint,
        "quantisation": _quantisation(server_args, hf_config),
        "tensor_geometry": _tensor_geometry(hf_config),
        "kv_layout": _kv_layout(server_args),
        "latent_method": LATENT_METHOD,
        "runtime": _runtime(),
    }


def fingerprint(identity: Dict[str, str]) -> str:
    """The ``|``-joined fingerprint, byte-identical to the Rust bridge."""
    return "|".join(str(identity.get(field, "")) for field in IDENTITY_FIELDS)
