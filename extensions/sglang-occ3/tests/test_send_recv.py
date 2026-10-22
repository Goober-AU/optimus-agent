"""Send/receive path request construction (no GPU, stubbed sglang)."""

from __future__ import annotations

import pytest

from occ3_sglang.channel import Channel, salt_for
from occ3_sglang.recv_path import recv_from_channel
from occ3_sglang.security import (
    ValidationError,
    validate_carrier,
    validate_embeds,
    validate_ttl,
)
from occ3_sglang.send_path import send_carrier


def make_channel(channel_id="ch1", ids=None, ttl=60.0):
    return Channel(
        channel_id=channel_id,
        salt=salt_for(channel_id),
        carrier_token_ids=ids or [11, 22, 33, 44, 55],
        created_at=0.0,
        ttl_s=ttl,
    )


async def test_send_with_input_ids_builds_salted_request(stub_tm):
    channel = make_channel()
    summary = await send_carrier(stub_tm, channel, input_ids=[11, 22, 33])
    assert len(stub_tm.requests) == 1
    req = stub_tm.requests[0]
    assert req.input_ids == [11, 22, 33]
    assert req.cache_salt == "occ3-ch1"
    assert req.sampling_params["max_new_tokens"] == 1
    assert req.rid == "occ3-send-ch1"
    assert summary["carrier_token_ids"] == [11, 22, 33]
    assert summary["cached_tokens"] == 0  # fresh namespace


async def test_send_with_text_uses_server_tokenization(stub_tm):
    channel = make_channel()
    summary = await send_carrier(stub_tm, channel, text="hello latent world")
    req = stub_tm.requests[0]
    assert req.text == "hello latent world"
    assert req.input_ids is None
    assert req.return_prompt_token_ids is True
    # The stub tokenizer maps the text to [11,22,33,44,55].
    assert summary["carrier_token_ids"] == [11, 22, 33, 44, 55]


async def test_send_rejects_both_or_neither(stub_tm):
    channel = make_channel()
    with pytest.raises(ValueError):
        await send_carrier(stub_tm, channel)
    with pytest.raises(ValueError):
        await send_carrier(
            stub_tm, channel, input_ids=[1], text="both"
        )


async def test_send_with_embeds_builds_positional_embeds(stub_tm):
    channel = make_channel(ids=[1, 2, 3, 4])
    summary = await send_carrier(
        stub_tm,
        channel,
        input_ids=[1, 2, 3, 4],
        embeds=[[0.5] * 4, [0.25] * 4],
        positions=[1, 3],
    )
    req = stub_tm.requests[0]
    overrides = req.positional_embed_overrides
    assert overrides is not None
    assert overrides.positions == [1, 3]
    assert len(overrides.embeds) == 2


async def test_recv_builds_carrier_plus_suffix_under_salt(stub_tm):
    channel = make_channel(ids=[11, 22, 33, 44, 55])
    summary = await recv_from_channel(
        stub_tm, channel, suffix_ids=[66, 77], max_new_tokens=32
    )
    req = stub_tm.requests[0]
    assert req.input_ids == [11, 22, 33, 44, 55, 66, 77]
    assert req.cache_salt == "occ3-ch1"
    assert req.rid == "occ3-recv-ch1"
    assert req.sampling_params["max_new_tokens"] == 32
    # Stub reports cached_tokens=5 -> hit covering the 5-token carrier.
    assert summary["hit"] is True
    assert summary["cached_tokens"] == 5


async def test_recv_miss_detected(stub_tm):
    channel = make_channel(ids=[11, 22, 33, 44, 55])
    stub_tm.default_cached_tokens = 2  # evicted / racing
    summary = await recv_from_channel(stub_tm, channel, suffix_ids=[1])
    assert summary["hit"] is False
    assert summary["cached_tokens"] == 2


async def test_recv_output_shape(stub_tm):
    channel = make_channel(ids=[11, 22])
    summary = await recv_from_channel(stub_tm, channel, suffix_ids=[5], max_new_tokens=8, temperature=0.7)
    assert summary["output_text"] == "received"
    assert summary["output_ids"] == [1, 2, 3]
    assert summary["carrier_tokens"] == 2


def test_validate_send_bounds():
    # embeds without positions
    with pytest.raises(ValidationError):
        validate_embeds([[0.1]], None, 2)
    # positions without embeds
    with pytest.raises(ValidationError):
        validate_embeds(None, [0], 2)
    # mismatched lengths
    with pytest.raises(ValidationError):
        validate_embeds([[0.1]], [0, 1], 2)
    # non-increasing positions
    with pytest.raises(ValidationError):
        validate_embeds([[0.1], [0.2]], [2, 1], 3)
    # out-of-range position
    with pytest.raises(ValidationError):
        validate_embeds([[0.1]], [5], 2)
    # non-finite embed value
    with pytest.raises(ValidationError):
        validate_embeds([[float("nan")]], [0], 2)
    # ragged rows
    with pytest.raises(ValidationError):
        validate_embeds([[0.1, 0.2], [0.3]], [0, 1], 2)
    # missing carrier length
    with pytest.raises(ValidationError):
        validate_embeds([[0.1]], [0], None)
    # ttl bounds
    with pytest.raises(ValidationError):
        validate_ttl(0.0)
    with pytest.raises(ValidationError):
        validate_ttl(9999.0)
    # empty carrier
    with pytest.raises(ValidationError):
        validate_carrier([])
    # valid
    assert validate_carrier([1, 2, 3]) == 3
    validate_embeds([[0.1], [0.2]], [0, 2], 3)
