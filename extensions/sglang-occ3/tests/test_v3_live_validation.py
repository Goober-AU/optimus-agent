"""Offline fixture proofs. No server restart, GPU or live model is used."""
import copy
import json
from urllib.parse import urlsplit

import pytest

from deploy.v3.live_validation import LiveValidator, SourceKvBaselineValidator
from deploy.v3.service_checks import HttpResult
from occ3_sglang import kv_handles


KEY = "synthetic-private-key"


def fixture_requester(client, recorded=None, mutate=None):
    def request(method, url, key, payload, timeout):
        if recorded is not None:
            recorded.append((method, urlsplit(url).path, copy.deepcopy(payload)))
        response = client.request(method, urlsplit(url).path, json=payload)
        result = HttpResult(response.status_code, response.json())
        if mutate:
            mutate(urlsplit(url).path, payload, result)
        return result
    return request


def validator(client, clock, **kwargs):
    return SourceKvBaselineValidator("http://fixture", KEY, "fixture-model",
        attempt_id="attempt-offline", candidate_id="candidate-offline",
        requester=kwargs.pop("requester", fixture_requester(client)),
        clock=lambda: clock[0], **kwargs)


def test_full_gate_refuses_baseline_and_advertised_boolean_pass(kv_client):
    client, tm, clock = kv_client
    full = LiveValidator("http://fixture", KEY, "fixture", requester=fixture_requester(client))
    result = full.before_restart()
    assert result["ok"] is False and result["gate8_pass"] is False
    assert "true_latent_method_missing_text_radix_is_only_baseline" in result["blockers"]
    assert not tm.kv_requests
    def fabricated(*args):
        return HttpResult(200, {"true_latent_method": True, "native_receiver_integrated": True})
    full = LiveValidator("http://fixture", KEY, "fixture", requester=fabricated)
    result = full.before_restart()
    assert not result["ok"]
    assert result["blockers"] == ["validated_native_latent_proof_adapter_not_implemented"]
    assert not full.after_restart()["ok"]


def test_baseline_all_phases_are_not_true_latent_gate8(kv_client, monkeypatch):
    client, tm, clock = kv_client
    recorded = []
    check = validator(client, clock, requester=fixture_requester(client, recorded))
    before = check.before_restart()
    assert before["baseline_ok"], before
    assert not before["ok"] and not before["gate8_pass"]
    assert not before["restart_authorized"]
    assert before["checks"]["first_transfer"]["exact_fact_match"]
    assert before["checks"]["metrics_delta"]["handle_recv_hit"] == 2
    assert before["checks"]["expiry"] is True
    assert before["checks"]["release"] == "handle_retired_not_physical_gpu_erasure"
    assert "native_fallback_unproven" in before["blockers"]
    for method, path, payload in recorded:
        if path.endswith("/send"):
            assert "text" not in payload and "input_ids" not in payload
        if path.endswith("/recv"):
            assert "code-" not in payload["suffix"] and "owner-" not in payload["suffix"]
    prior_epoch = kv_handles.store.epoch
    monkeypatch.setattr(kv_handles, "store", kv_handles.CaptureStore(clock=lambda: clock[0]))
    after = check.after_restart()
    assert after["baseline_ok"], after
    assert after["process_epoch"] != prior_epoch
    assert after["checks"]["old_handle_stale"] is True
    assert after["checks"]["fresh_transfer"]["exact_fact_match"] is True
    assert not after["ok"] and not after["gate8_pass"]
    assert not kv_handles.store.captures
    # Only the in-memory fixture epoch changed. No restart API exists on validator.
    assert not any("restart" in path or "/containers/" in path for _, path, _ in recorded)


@pytest.mark.parametrize("fault", ["cached_only", "host", "facts", "lineage", "fallback", "retractions"])
def test_baseline_rejects_false_positive_evidence(kv_client, fault):
    client, tm, clock = kv_client
    def mutate(path, payload, result):
        if path.endswith("/recv") and result.status == 200:
            body = result.body
            if fault == "cached_only":
                body.pop("cache_tiers")
            elif fault == "host":
                body["cache_tiers"]["host"] = 4
            elif fault == "facts":
                body["output_text"] = '{"code":"incorrect"}'
            elif fault == "lineage":
                body["source_request_id"] = "unrelated-source"
            elif fault == "fallback":
                body["fallback_used"] = True
            else:
                body["num_retractions"] = 1
    check = validator(client, clock, requester=fixture_requester(client, mutate=mutate))
    result = check.before_restart()
    assert not result["baseline_ok"] and not result["gate8_pass"]
    assert result["error_code"]
    assert not kv_handles.store.captures


def test_send_and_capture_failures_cannot_turn_missing_handle_into_pass(kv_client):
    client, tm, clock = kv_client
    called = []
    base = fixture_requester(client, called)
    def request(method, url, key, payload, timeout):
        if url.endswith("/capture"):
            return HttpResult(502, {"private_error": KEY})
        return base(method, url, key, payload, timeout)
    result = validator(client, clock, requester=request).before_restart()
    assert not result["baseline_ok"]
    assert KEY not in json.dumps(result)
    assert not any(path.endswith("/recv") for _, path, _ in called)


def test_no_automatic_repeat_can_warm_a_missed_source(kv_client):
    client, tm, clock = kv_client
    check = validator(client, clock)
    first = check.before_restart()
    assert first["baseline_ok"], first
    n = len(tm.kv_requests)
    again = check.before_restart()
    assert again["error_code"] == "no_automatic_repeat"
    assert len(tm.kv_requests) == n


def test_staleness_requires_new_process_epoch(kv_client):
    client, tm, clock = kv_client
    check = validator(client, clock)
    assert check.before_restart()["baseline_ok"]
    after = check.after_restart()
    assert not after["baseline_ok"] and after["error_code"] == "process_epoch_unchanged"


def test_after_restart_cannot_run_without_before(kv_client):
    client, tm, clock = kv_client
    result = validator(client, clock).after_restart()
    assert result["error_code"] == "invalid_phase_order"
    assert not tm.kv_requests


def test_unavailable_connector_has_no_implicit_text_fallback():
    calls = []
    def missing(method, url, key, payload, timeout):
        calls.append(url)
        raise RuntimeError(KEY)
    result = LiveValidator("http://fixture", KEY, "fixture", requester=missing).before_restart()
    assert not result["ok"] and result["blockers"] == ["connector_unavailable"]
    assert len(calls) == 1 and calls[0].endswith("/latent/capabilities")
    assert KEY not in json.dumps(result)


def test_source_derived_baseline_never_says_gpu_erased(kv_client):
    client, tm, clock = kv_client
    result = validator(client, clock).before_restart()
    assert result["baseline_ok"] and result["gpu_erasure_proven"] is False
    assert result["scope"] == "text_radix_baseline"


@pytest.mark.parametrize("release_fails", [False, True])
def test_malformed_capture_is_tracked_and_cleanup_outcome_preserved(kv_client, release_fails):
    client, tm, clock = kv_client
    base = fixture_requester(client)
    calls = []
    def request(method, url, key, payload, timeout):
        calls.append((urlsplit(url).path, copy.deepcopy(payload)))
        if release_fails and url.endswith("/release"):
            return HttpResult(503, {"detail": "SYNTHETIC-CLEANUP-SECRET"})
        result = base(method, url, key, payload, timeout)
        if url.endswith("/capture") and result.status == 200:
            result.body["source_cached_tokens"] = 999
        return result
    check = validator(client, clock, requester=request)
    result = check.before_restart()
    assert not result["baseline_ok"]
    assert result["error_code"] == "source_capture_unproven"
    releases = [payload for path, payload in calls if path.endswith("/release")]
    assert len(releases) == 1 and releases[0]["capture_handle"]
    evidence = result["cleanup_evidence"]
    assert len(evidence) == 1
    assert evidence[0]["cpu_retirement_confirmed"] is (not release_fails)
    assert evidence[0]["gpu_erasure_proven"] is False
    assert result["cleanup_pending_handles"] == int(release_fails)
    assert len(kv_handles.store.captures) == int(release_fails)
    assert "SYNTHETIC-CLEANUP-SECRET" not in json.dumps(result)
    assert releases[0]["capture_handle"] not in json.dumps(result)


def test_unavailable_partial_capture_handle_is_reported_not_invented(kv_client):
    client, tm, clock = kv_client
    base = fixture_requester(client)
    def request(method, url, key, payload, timeout):
        if url.endswith("/capture"):
            return HttpResult(502, {"private": KEY})
        return base(method, url, key, payload, timeout)
    result = validator(client, clock, requester=request).before_restart()
    assert result["capture_handle_unavailable"] is True
    assert result["cleanup_evidence"] == []
    assert result["cleanup_pending_handles"] == 0
    assert not result["baseline_ok"] and KEY not in json.dumps(result)


@pytest.mark.parametrize("release_fails", [False, True])
def test_after_restart_result_includes_completed_cleanup_attempt(kv_client, monkeypatch, release_fails):
    client, tm, clock = kv_client
    base = fixture_requester(client)
    fail_phase = [False]
    def request(method, url, key, payload, timeout):
        if fail_phase[0] and release_fails and url.endswith("/release"):
            return HttpResult(503, {"detail": KEY})
        result = base(method, url, key, payload, timeout)
        if fail_phase[0] and url.endswith("/capture") and result.status == 200:
            result.body["source_cached_tokens"] = 1
        return result
    check = validator(client, clock, requester=request)
    assert check.before_restart()["baseline_ok"]
    monkeypatch.setattr(kv_handles, "store", kv_handles.CaptureStore(clock=lambda: clock[0]))
    fail_phase[0] = True
    result = check.after_restart()
    assert not result["baseline_ok"] and result["error_code"] == "source_capture_unproven"
    assert len(result["cleanup_evidence"]) == 1
    assert result["cleanup_evidence"][0]["cpu_retirement_confirmed"] is (not release_fails)
    assert result["cleanup_pending_handles"] == int(release_fails)
    assert KEY not in json.dumps(result)


@pytest.mark.parametrize("validator_class", [LiveValidator, SourceKvBaselineValidator])
def test_pre_mutation_admission_refuses_locally_without_spending_any_request(validator_class):
    calls = []
    def forbidden_request(*args):
        calls.append(args)
        raise AssertionError("local admission must not query the old serving process")
    check = validator_class("http://fixture", KEY, "fixture", requester=forbidden_request)
    result = check.pre_mutation_admission()
    assert result["ok"] is False and result["gate8_pass"] is False
    assert result["status"] == "blocked"
    assert result["code"] == "validated_native_latent_proof_adapter_not_implemented"
    assert result["phase"] == "pre_mutation_admission" and calls == []
