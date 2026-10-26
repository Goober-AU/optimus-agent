"""Offline tests for plan-declared candidate environment additions.

The deployed sglang build (e00930c) defaults SGLANG_USE_PICKLE_IPC to True,
while the occ3 patch contract requires deterministic msgpack IPC; the 2026-10-26
TS11 live window proved the scheduler guard refuses every armed request
pre-prefill (occ3_unsupported_profile folding to the generic 409) when the
container environment leaves that default in place. The only reliable lever is
the candidate container environment, so the create body must carry a declared,
bounded, pure addition while every other field stays exact and parity still
fails closed on any undeclared environment change.
"""
from copy import deepcopy

import pytest

from deploy.v3 import docker_config as v3_dc
from deploy.v3.lifecycle import validate_plan, Failure

from test_controller import V3_CANDIDATE_IMAGE, docker29_create, v3_original


ADDITION = {"name": "SGLANG_USE_PICKLE_IPC", "value": "0"}


def build(original=None, additions=(ADDITION,)):
    original = original if original is not None else v3_original()
    body = v3_dc.build_create_request(original, V3_CANDIDATE_IMAGE, "candidate", list(additions))
    return original, body


def test_builder_appends_declared_additions_in_order():
    original, body = build(additions=(ADDITION, {"name": "SGLANG_MAX_NEW_TOKENS_LIMIT", "value": "200000"}))
    assert body["Env"] == ["A=1", "SGLANG_USE_PICKLE_IPC=0", "SGLANG_MAX_NEW_TOKENS_LIMIT=200000"]


def test_builder_without_additions_is_unchanged():
    original, body = build(additions=())
    assert body["Env"] == ["A=1"]


def test_builder_refuses_duplicate_additions():
    with pytest.raises(v3_dc.ConfigError):
        build(additions=({"name": "SGLANG_X", "value": "1"}, {"name": "SGLANG_X", "value": "2"}))


@pytest.mark.parametrize("bad", [
    {"name": "A", "value": "2"},                    # collides with the original env
    {"name": "lower_case", "value": "1"},           # bad name charset
    {"name": "", "value": "1"},                     # empty name
    {"name": "SGLANG_X", "value": ""},              # empty value
    {"name": "SGLANG_X", "value": " padded "},      # surrounding whitespace
    {"name": "SGLANG_X", "value": "nul\0x"},       # NUL in value
    {"name": "SGLANG_X"},                            # missing value key
    ["SGLANG_X=1"],                                  # not a mapping
])
def test_builder_refuses_malformed_additions(bad):
    with pytest.raises(v3_dc.ConfigError):
        build(additions=(bad,))


def test_roundtrip_parity_accepts_declared_additions_only():
    original, body = build()
    candidate = docker29_create(original, body)
    evidence = v3_dc.assert_candidate_parity(original, candidate, V3_CANDIDATE_IMAGE, [ADDITION])
    assert evidence["environment_exact"] is True
    assert evidence["declared_environment_additions"] == ["SGLANG_USE_PICKLE_IPC"]


def test_parity_fails_when_addition_is_missing_from_candidate():
    original, _ = build()
    undeclared = deepcopy(original)
    undeclared.update(Id="c" * 64, Name="/candidate", Image=V3_CANDIDATE_IMAGE)
    undeclared["Config"] = deepcopy(original["Config"])
    undeclared["Config"]["Image"] = V3_CANDIDATE_IMAGE
    undeclared["State"] = {"Status": "created", "Running": False}
    with pytest.raises(v3_dc.ConfigError):
        v3_dc.assert_candidate_parity(original, undeclared, V3_CANDIDATE_IMAGE, [ADDITION])


def test_parity_fails_on_undeclared_extra_environment():
    original, body = build(additions=())
    body["Env"] = original["Config"]["Env"] + ["INJECTED=1"]
    candidate = docker29_create(original, body)
    with pytest.raises(v3_dc.ConfigError):
        v3_dc.assert_candidate_parity(original, candidate, V3_CANDIDATE_IMAGE)


def test_parity_fails_when_original_env_entry_is_dropped():
    original, body = build()
    body["Env"] = ["SGLANG_USE_PICKLE_IPC=0"]
    candidate = docker29_create(original, body)
    with pytest.raises(v3_dc.ConfigError):
        v3_dc.assert_candidate_parity(original, candidate, V3_CANDIDATE_IMAGE, [ADDITION])


def test_validate_plan_accepts_and_normalizes_additions():
    plan = {"attempt_id": "occ3-live-20261026t095633z", "original_id": "a" * 64,
            "original_image_id": "sha256:" + "a" * 64, "candidate_image_id": "sha256:" + "b" * 64,
            "base_url": "http://127.0.0.1:30001", "ingress_base_url": "http://neo-k3-ingress:8080",
            "model": "glm-5.3"}
    plan = validate_plan(plan)
    assert plan["environment_additions"] == []
    plan["environment_additions"] = [ADDITION]
    assert validate_plan(plan)["environment_additions"] == [ADDITION]
    plan["environment_additions"] = [{"name": "lower", "value": "1"}]
    with pytest.raises(Failure):
        validate_plan(plan)
