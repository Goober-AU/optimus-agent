"""Channel registry lifecycle tests."""

from __future__ import annotations

import time

import pytest

from occ3_sglang.channel import ChannelState, salt_for
from occ3_sglang.registry import ChannelFullError, ChannelRegistry


def make_registry(max_channels=4):
    return ChannelRegistry(max_channels=max_channels)


def test_create_and_get_roundtrip():
    reg = make_registry()
    ch = reg.create("c1", [1, 2, 3], ttl_s=60.0)
    assert ch.salt == "occ3-c1"
    assert ch.state == ChannelState.ACTIVE
    got = reg.get("c1")
    assert got is not None
    assert got.carrier_token_ids == [1, 2, 3]


def test_duplicate_active_channel_rejected_but_consumed_id_reusable():
    reg = make_registry()
    reg.create("c1", [1], ttl_s=60.0)
    with pytest.raises(KeyError):
        reg.create("c1", [2], ttl_s=60.0)
    reg.release("c1")
    ch = reg.create("c1", [3], ttl_s=60.0)
    assert ch.carrier_token_ids == [3]


def test_expiry_is_lazy_and_terminal():
    reg = make_registry()
    reg.create("c1", [1], ttl_s=60.0)
    # Force expiry by rewinding created_at.
    with reg._lock:
        reg._channels["c1"].created_at = time.time() - 120.0
    assert reg.get("c1") is None
    assert reg.counts()["expired"] == 1
    # Expired channels never come back.
    assert reg.get("c1") is None


def test_release_marks_consumed():
    reg = make_registry()
    reg.create("c1", [1], ttl_s=60.0)
    prior = reg.release("c1")
    assert prior == ChannelState.ACTIVE
    assert reg.get("c1") is None  # consumed channels are not receivable
    assert reg.release("nope") is None


def test_capacity_enforced():
    reg = make_registry(max_channels=2)
    reg.create("a", [1], ttl_s=60.0)
    reg.create("b", [1], ttl_s=60.0)
    with pytest.raises(ChannelFullError):
        reg.create("c", [1], ttl_s=60.0)


def test_record_recv_counts_hits_and_misses():
    reg = make_registry()
    reg.create("c1", [1, 2, 3], ttl_s=60.0)
    reg.record_recv("c1", hit=True)
    reg.record_recv("c1", hit=False)
    ch = reg.get("c1")
    assert ch.recv_attempts == 2
    assert ch.recv_hits == 1


def test_list_channels_sanitizes_token_ids():
    reg = make_registry()
    reg.create("c1", [9, 8, 7], ttl_s=60.0)
    listing = reg.list_channels()
    assert len(listing) == 1
    assert "carrier_token_ids" not in listing[0]
    assert listing[0]["carrier_tokens"] == 3


def test_salt_for_is_deterministic_and_bounded():
    assert salt_for("abc") == "occ3-abc"
    assert salt_for("abc") == salt_for("abc")
