"""Route behavior with a stubbed tokenizer manager and http_server."""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from occ3_sglang import api, plugin
from occ3_sglang.registry import registry


@pytest.fixture
def client(stub_http_server, stub_tm):
    app = FastAPI()
    app.include_router(api.router)
    stub_http_server.app = app
    stub_http_server._global_state = type("S", (), {"tokenizer_manager": stub_tm})()
    with TestClient(app) as c:
        yield c


@pytest.fixture(autouse=True)
def clean_registry():
    # The registry is a module singleton; reset between tests.
    with registry()._lock:
        registry()._channels.clear()
    yield
    with registry()._lock:
        registry()._channels.clear()


def test_status_reports_plugin_and_identity(client):
    r = client.get("/occ3/v1/status")
    assert r.status_code == 200
    body = r.json()
    assert body["plugin"] == "occ3-bridge"
    assert body["enabled"] is True
    assert body["model_fingerprint"]
    assert "channels" in body and "metrics" in body


def test_identity_route_exposes_contract_fields(client):
    r = client.get("/occ3/v1/identity")
    assert r.status_code == 200
    body = r.json()
    assert body["fields"]["architecture"] == "GlmMoeDsaForCausalLM"
    assert len(body["field_order"]) == 11
    assert body["fingerprint"].count("|") == 10


def test_capabilities_advertisement_shape(client):
    r = client.get("/occ3/v1/capabilities")
    assert r.status_code == 200
    body = r.json()
    assert body["supported"] is True
    assert body["connector_id"] == "sglang-occ3"
    assert body["methods"] == ["kv-handoff"]
    assert body["same_model_only"] is True
    assert body["verification"] == "cached_tokens"
    assert body["model_fingerprint"]


def test_send_with_text_then_recv_then_release(client):
    send = client.post("/occ3/v1/send", json={"text": "hello latent world"})
    assert send.status_code == 200, send.text
    body = send.json()
    channel = body["channel"]
    assert body["carrier_tokens"] == 5  # stub tokenizer mapping
    assert body["salt"] == f"occ3-{channel}"

    recv = client.post(
        "/occ3/v1/recv",
        json={"channel": channel, "suffix": "answer briefly", "max_new_tokens": 16},
    )
    assert recv.status_code == 200, recv.text
    rb = recv.json()
    assert rb["hit"] is True
    assert rb["cached_tokens"] == 5
    assert rb["output_text"] == "received"

    rel = client.post("/occ3/v1/release", json={"channel": channel})
    assert rel.status_code == 200
    assert rel.json()["released"] is True

    # After release, recv fails with 404 (consumed channels are not receivable).
    again = client.post(
        "/occ3/v1/recv", json={"channel": channel, "suffix": "again"}
    )
    assert again.status_code == 404


def test_recv_unknown_channel_404(client):
    r = client.post("/occ3/v1/recv", json={"channel": "ghost", "suffix": "x"})
    assert r.status_code == 404


def test_send_requires_exactly_one_input(client):
    r = client.post("/occ3/v1/send", json={})
    assert r.status_code == 400
    r = client.post(
        "/occ3/v1/send", json={"text": "a", "input_ids": [1]}
    )
    assert r.status_code == 400


def test_send_rejects_bad_channel_id(client):
    r = client.post(
        "/occ3/v1/send", json={"text": "a", "channel": "bad id!!"}
    )
    assert r.status_code == 400


def test_send_with_embeds_and_positions(client):
    r = client.post(
        "/occ3/v1/send",
        json={
            "input_ids": [1, 2, 3, 4],
            "embeds": [[0.5, 0.5], [0.25, 0.25]],
            "positions": [1, 3],
        },
    )
    assert r.status_code == 200, r.text
    assert r.json()["carrier_tokens"] == 4


def test_channels_listing_hides_ids(client):
    client.post("/occ3/v1/send", json={"input_ids": [7, 8, 9]})
    r = client.get("/occ3/v1/channels")
    assert r.status_code == 200
    listing = r.json()["channels"]
    assert len(listing) == 1
    assert "carrier_token_ids" not in listing[0]


def test_recv_miss_reports_hit_false(client, stub_tm):
    client.post("/occ3/v1/send", json={"input_ids": [1, 2, 3, 4, 5]})
    stub_tm.default_cached_tokens = 0
    r = client.post("/occ3/v1/recv", json={"channel": next(iter(registry()._channels)), "suffix": "x"})
    assert r.status_code == 200
    assert r.json()["hit"] is False


def test_plugin_load_installs_router_once(stub_http_server, stub_tm):
    app = FastAPI()
    stub_http_server.app = app
    stub_http_server._global_state = type("S", (), {"tokenizer_manager": stub_tm})()
    plugin._LOADED = False
    plugin.load()
    plugin.load()  # idempotent
    assert getattr(app, "_occ3_router_installed") is True
    with TestClient(app) as c:
        r = c.get("/occ3/v1/status")
        assert r.status_code == 200
        assert r.json()["plugin"] == "occ3-bridge"
        r = c.get("/occ3/v1/capabilities")
        assert r.status_code == 200
