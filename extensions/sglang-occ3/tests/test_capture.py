"""Capture hook: dormant by default, armed by env, clones accepted rows."""

from __future__ import annotations

import os

import pytest

from occ3_sglang import capture


@pytest.fixture(autouse=True)
def reset_capture():
    capture._captures.clear()
    capture._registered = False
    yield
    capture._captures.clear()
    capture._registered = False


def test_dormant_without_env(stub_http_server, monkeypatch):
    monkeypatch.delenv("OCC3_CAPTURE_ENABLED", raising=False)
    capture.register_capture_hook()
    assert capture._registered is False


def test_armed_with_env(stub_http_server, monkeypatch):
    monkeypatch.setenv("OCC3_CAPTURE_ENABLED", "1")
    capture.register_capture_hook()
    assert capture._registered is True
    # Registry from the conftest stub received the AROUND registration.
    import sys

    hook_mod = sys.modules["sglang.srt.plugins.hook_registry"]
    regs = hook_mod.HookRegistry.registrations
    assert any(
        r["target"] == capture.TARGET and r["hook_type"] == "around" for r in regs
    )


def test_around_hook_passes_result_through_and_stores(stub_http_server):
    called = {"n": 0}

    class FakeTensor:
        def __init__(self, data, shape=(1, 4)):
            self.data = data
            self.shape = shape

        def detach(self):
            return self

        def clone(self):
            return self

        def to(self, *a, **k):
            return self

    class Rows:
        def __init__(self, data):
            self.data = data

        def __getitem__(self, idx):
            if isinstance(idx, list):
                return FakeTensor(
                    [self.data[i].data for i in idx], shape=(len(idx), 4)
                )
            return self.data[idx]

    class Result:
        logits_output = type(
            "LO",
            (),
            {"hidden_states": Rows([FakeTensor([1.0]), FakeTensor([2.0])])},
        )()
        accept_index = [0]  # accepts the first row

    class Batch:
        rids = ["req-1"]

    def original(batch, **kwargs):
        called["n"] += 1
        return Result()

    out = capture._capture_around(original, Batch())
    assert called["n"] == 1
    assert out is not None
    assert "req-1" in capture._captures
    entry = capture.get_capture("req-1")
    assert entry is not None
    assert entry["rids"] == ["req-1"]
    assert capture.capture_count() == 1


def test_capture_store_is_bounded(stub_http_server):
    class Batch:
        rids = None

    class Result:
        logits_output = None
        accept_index = None

    def original(batch, **kwargs):
        return Result()

    for i in range(capture._MAX_CAPTURES + 4):
        capture._capture_around(original, Batch())
        # entries with no rids go to the anonymous bucket
    assert capture.capture_count() <= capture._MAX_CAPTURES
