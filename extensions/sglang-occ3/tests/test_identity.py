"""Identity computation and Rust field-order parity."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from occ3_sglang.identity import IDENTITY_FIELDS, compute_identity, fingerprint

REPO_ROOT = Path(__file__).resolve().parents[3]
RUST_TYPES = (
    REPO_ROOT
    / "crates/pi-coding-agent/src/core/occ/occ3/types.rs"
)


def test_identity_has_eleven_fields_in_contract_order():
    assert len(IDENTITY_FIELDS) == 11


def test_identity_field_order_matches_rust_bridge():
    """The fingerprint string must be comparable across languages."""
    source = RUST_TYPES.read_text(encoding="utf-8")
    start = source.find("pub struct Occ3ModelIdentity")
    end = source.find("impl Occ3ModelIdentity", start)
    struct = source[start:end]
    fields = [
        line.split(":")[0].replace("pub ", "").strip()
        for line in struct.splitlines()
        if line.strip().startswith("pub ") and ":" in line
    ]
    assert fields, "failed to parse Occ3ModelIdentity fields"
    assert tuple(fields) == IDENTITY_FIELDS


def test_identity_computed_from_stub_tm(stub_tm):
    identity = compute_identity(stub_tm)
    assert set(identity) == set(IDENTITY_FIELDS)
    assert identity["architecture"] == "GlmMoeDsaForCausalLM"
    assert identity["model_id"] == "glm-5.3"
    assert identity["checkpoint_revision"] == "aca966e4e02791568aa6a4ced368624b3d897f42"
    assert identity["quantisation"] == "fp8-e4m3"
    assert "hidden_size=6144" in identity["tensor_geometry"]
    assert "tp_size=8" in identity["kv_layout"]
    assert "kv_cache_dtype=fp8_e4m3" in identity["kv_layout"]
    assert identity["latent_method"] == "kv-radix-channel-v1"
    assert identity["runtime"] == "sglang/0.5.21-test"


def test_fingerprint_is_stable_and_pipe_joined(stub_tm):
    identity = compute_identity(stub_tm)
    fp = fingerprint(identity)
    assert fp == "|".join(identity[f] for f in IDENTITY_FIELDS)
    assert fingerprint(compute_identity(stub_tm)) == fp


def test_fingerprint_changes_when_topology_changes(stub_tm):
    before = fingerprint(compute_identity(stub_tm))
    stub_tm.server_args.tp_size = 4
    after = fingerprint(compute_identity(stub_tm))
    assert before != after


def test_identity_never_crashes_on_minimal_tm():
    class Bare:
        pass

    identity = compute_identity(Bare())
    assert set(identity) == set(IDENTITY_FIELDS)
    assert all(isinstance(v, str) for v in identity.values())
    # Unknown runtime yields the fallback marker, not an exception.
    assert identity["runtime"] in ("sglang/unknown",) or identity["runtime"].startswith("sglang/")
