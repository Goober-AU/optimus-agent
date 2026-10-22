"""Offline HTTP fixtures only. These tests do not prove a live service."""
import io
import json
import re
import urllib.error

import pytest

from deploy.v3 import service_checks as checks


KEY = "synthetic-test-secret-do-not-log"
MODEL = "fixture-model"


def valid_response(payload):
    nonce = re.search(r"OCC3_(?:READY|TOOL)_[0-9a-f]+", payload["messages"][0]["content"])[0]
    message = {"role": "assistant", "content": nonce}
    finish = "stop"
    if "tools" in payload:
        message = {"role": "assistant", "content": None, "tool_calls": [{
            "id": "call-fixture", "type": "function", "function": {
                "name": "occ3_echo", "arguments": json.dumps({"nonce": nonce})}}]}
        finish = "tool_calls"
    return {"id": "chatcmpl-fixture", "model": MODEL,
            "choices": [{"message": message, "finish_reason": finish}],
            "usage": {"completion_tokens": 4, "prompt_tokens": 12}}


def responding(method, url, key, payload, timeout):
    if url.endswith("/health"):
        return checks.HttpResult(200)
    if url.endswith("/v1/models"):
        return checks.HttpResult(401, {"private": KEY})
    return checks.HttpResult(200, valid_response(payload))


def test_exact_chat_and_tool_semantics_without_execution():
    assert checks.check_chat("http://fixture", KEY, MODEL, requester=responding)["ok"]
    tool = checks.check_tools("http://fixture", KEY, MODEL, requester=responding)
    assert tool["ok"] and tool["exact_function_and_arguments"]
    assert tool["tool_executed"] is False


@pytest.mark.parametrize("body", [None, {}, [], {"choices": []}, {"model": MODEL},
                                   {"model": MODEL, "choices": ["not-an-object"]}])
def test_http_200_is_not_chat_proof(body):
    out = checks.check_chat("http://fixture", KEY, MODEL,
                            requester=lambda *args: checks.HttpResult(200, body))
    assert not out["ok"]


@pytest.mark.parametrize("mutation", ["reasoning_only", "wrong_content", "length", "wrong_model", "zero_tokens"])
def test_chat_rejects_incomplete_or_wrong_answers(mutation):
    def requester(method, url, key, payload, timeout):
        body = valid_response(payload)
        message = body["choices"][0]["message"]
        if mutation == "reasoning_only":
            message["reasoning_content"] = message.pop("content")
        elif mutation == "wrong_content":
            message["content"] = "ready"
        elif mutation == "length":
            body["choices"][0]["finish_reason"] = "length"
        elif mutation == "wrong_model":
            body["model"] = "other-checkpoint"
        else:
            body["usage"]["completion_tokens"] = 0
        return checks.HttpResult(200, body)
    assert not checks.check_chat("http://fixture", KEY, MODEL, requester=requester)["ok"]


@pytest.mark.parametrize("mutation", ["name", "args", "extra", "missing", "invalid_json", "type", "finish"])
def test_tool_call_must_match_exact_name_and_arguments(mutation):
    def requester(method, url, key, payload, timeout):
        body = valid_response(payload)
        choice = body["choices"][0]
        calls = choice["message"]["tool_calls"]
        fn = calls[0]["function"]
        if mutation == "name":
            fn["name"] = "shell"
        elif mutation == "args":
            fn["arguments"] = '{"nonce":"wrong"}'
        elif mutation == "extra":
            args = json.loads(fn["arguments"])
            args["unrequested"] = True
            fn["arguments"] = json.dumps(args)
        elif mutation == "missing":
            choice["message"]["tool_calls"] = []
        elif mutation == "invalid_json":
            fn["arguments"] = "not-json"
        elif mutation == "type":
            calls[0]["type"] = "unknown"
        else:
            choice["finish_reason"] = "stop"
        return checks.HttpResult(200, body)
    assert not checks.check_tools("http://fixture", KEY, MODEL, requester=requester)["ok"]


def test_no_request_disables_thinking_or_reduces_output_budget():
    captured = []
    def requester(*args):
        captured.append(args)
        return responding(*args)
    checks.check_chat("http://fixture", KEY, MODEL, requester=requester)
    checks.check_tools("http://fixture", KEY, MODEL, requester=requester)
    for method, url, key, payload, timeout in captured:
        assert key == KEY and timeout == 120
        assert payload["max_tokens"] >= 4096
        assert not {"reasoning_effort", "thinking", "enable_thinking", "chat_template_kwargs"} & payload.keys()


def test_ingress_requires_exact_proxy_and_trusted_requester():
    assert not checks.check_ingress("http://neo-k3-ingress:8080", KEY, MODEL)["ok"]
    assert not checks.check_ingress("http://127.0.0.1:30001", KEY, MODEL,
                                   route="neo-k3-ingress", requester=responding)["ok"]
    assert not checks.check_ingress("http://neo-glm53:30001", KEY, MODEL,
                                   route="neo-k3-ingress", requester=responding)["ok"]
    used = []
    def requester(*args):
        used.append(args[1])
        return responding(*args)
    result = checks.check_ingress("http://neo-k3-ingress:8080", KEY, MODEL,
                                 route="neo-k3-ingress", requester=requester)
    assert result["ok"] and result["route_attestation_required"]
    assert all(url.startswith("http://neo-k3-ingress:8080/") for url in used)
    assert len(used) == 4


@pytest.mark.parametrize("status", [200, 302, 404, 500, None])
def test_auth_denial_is_not_any_error(status):
    result = checks.check_auth("http://fixture", KEY,
                               requester=lambda *args: checks.HttpResult(status))
    assert not result["ok"]


def test_transport_error_and_response_body_are_private():
    def fail(*args):
        raise RuntimeError(KEY)
    result = checks.check_chat("http://fixture", KEY, MODEL, requester=fail)
    assert not result["ok"] and KEY not in json.dumps(result)
    private = checks.HttpResult(502, {"credential": KEY}, "private_" + KEY)
    assert KEY not in repr(private)
    result = checks.check_tools("http://fixture", KEY, MODEL, requester=lambda *args: private)
    assert KEY not in json.dumps(result)


def test_private_http_rejects_redirect_and_does_not_follow(monkeypatch):
    seen = []
    class Opener:
        def open(self, req, timeout):
            seen.append((req, timeout))
            raise urllib.error.HTTPError(req.full_url, 302, "redirect " + KEY,
                                         {"Location": "http://other.invalid"}, io.BytesIO(KEY.encode()))
    monkeypatch.setattr(checks.urllib.request, "build_opener", lambda *args: Opener())
    out = checks.request_json("GET", "http://fixture/private", KEY)
    assert out.status == 302 and len(seen) == 1
    assert checks._NoRedirect().redirect_request(None, None, 302, "", {}, "http://other") is None


def test_private_http_bounds_body(monkeypatch):
    class Response(io.BytesIO):
        code = 200
    class Opener:
        def open(self, req, timeout):
            return Response(b"x" * (checks.MAX_RESPONSE_BYTES + 1))
    monkeypatch.setattr(checks.urllib.request, "build_opener", lambda *args: Opener())
    out = checks.request_json("GET", "http://fixture/private", KEY)
    assert out.body is None and out.error_code == "response_too_large"


def test_health_is_only_liveness():
    assert checks.check_health("http://fixture", KEY, requester=responding) == {
        "ok": True, "check": "health", "http_status": 200,
        "error_code": None, "scope": "liveness_only"}


IDENTITY_BODY = {"schema": "occ3.runtime.identity.v1", "runtime_id": "fixture-runtime",
                 "runtime_generation": 1, "model_fingerprint": "sha256:fixture",
                 "expected_ranks": 8, "ready_ranks": 8, "source_capture": True,
                 "continuous_prefix": True, "terminal_journal": True}


def test_occ3_identity_route_absent_404_is_the_w4a1_signature():
    result = checks.check_occ3_identity("http://fixture", KEY,
                                        requester=lambda *args: checks.HttpResult(404))
    assert result == {"ok": False, "check": "occ3_identity", "http_status": 404,
                      "error_code": "occ3_routes_absent"}


def test_occ3_identity_exact_step0_route_payload_and_valid_body():
    seen = []
    def requester(method, url, key, payload, timeout):
        seen.append((method, url, key, payload, timeout))
        return checks.HttpResult(200, dict(IDENTITY_BODY))
    result = checks.check_occ3_identity("http://fixture", KEY, requester=requester)
    assert result == {"ok": True, "check": "occ3_identity", "http_status": 200,
                      "error_code": None, "route_present": True}
    assert seen == [("POST", "http://fixture/occ3/v1/native/runtime/identity", KEY,
                     {"schema": "occ3.runtime.identity.request.v1"}, 15)]


@pytest.mark.parametrize("mutation", ["schema", "ready_ranks", "expected_ranks", "missing_field",
                                      "false_boolean", "non_dict"])
def test_occ3_identity_200_with_wrong_shape_is_malformed(mutation):
    body = dict(IDENTITY_BODY)
    if mutation == "schema":
        body["schema"] = "occ3.runtime.identity.v2"
    elif mutation == "ready_ranks":
        body["ready_ranks"] = 7
    elif mutation == "expected_ranks":
        body["expected_ranks"] = 4
    elif mutation == "missing_field":
        del body["terminal_journal"]
    elif mutation == "false_boolean":
        body["source_capture"] = False
    else:
        body = []
    result = checks.check_occ3_identity("http://fixture", KEY,
                                        requester=lambda *args: checks.HttpResult(200, body))
    assert result == {"ok": False, "check": "occ3_identity", "http_status": 200,
                      "error_code": "occ3_identity_malformed"}


@pytest.mark.parametrize("status", [401, 403, 500, 503])
def test_occ3_identity_other_http_failures_are_unavailable(status):
    result = checks.check_occ3_identity("http://fixture", KEY,
                                        requester=lambda *args: checks.HttpResult(status))
    assert result == {"ok": False, "check": "occ3_identity", "http_status": status,
                      "error_code": "occ3_identity_unavailable"}


def test_occ3_identity_transport_error_invalid_json_and_missing_key():
    def broken(*args):
        raise RuntimeError(KEY)
    result = checks.check_occ3_identity("http://fixture", KEY, requester=broken)
    assert result == {"ok": False, "check": "occ3_identity", "http_status": None,
                      "error_code": "occ3_identity_unavailable"}
    invalid = checks.check_occ3_identity("http://fixture", KEY,
        requester=lambda *args: checks.HttpResult(200, None, "invalid_json"))
    assert invalid["error_code"] == "occ3_identity_unavailable"
    missing = checks.check_occ3_identity("http://fixture", "", requester=broken)
    assert missing == {"ok": False, "check": "occ3_identity", "http_status": None,
                       "error_code": "missing_credentials"}
    assert KEY not in json.dumps(result) + json.dumps(missing)


DECLINED_STATUS = {"schema": "optimus.occ3.status.v1", "state": "DECLINED",
                   "code": "unsupported_profile", "stage": "install", "attempt": "deadbeef",
                   "experimental_selected": True, "preflight_reached": True,
                   "backend_constructed": True, "worker_readiness_reached": False,
                   "hooks_prepared": True, "route_mount_attempted": False,
                   "rollback_completed": True, "plugin": "occ3-bridge", "version": "0.1.0",
                   "enabled": False, "model_fingerprint": None, "channels": {}, "metrics": {}}


def declining(status_body=None, identity_status=503, requests=None):
    def requester(method, url, key, payload, timeout):
        if requests is not None:
            requests.append((method, url, key, payload, timeout))
        if url.endswith(checks.OCC3_IDENTITY_PATH):
            return checks.HttpResult(identity_status, {"error": {"code": "not_ready"}})
        return checks.HttpResult(200, dict(status_body or DECLINED_STATUS))
    return requester


def test_occ3_identity_declined_503_probes_status_and_fails_fast_with_the_code():
    seen = []
    result = checks.check_occ3_identity("http://fixture", KEY, requester=declining(requests=seen))
    assert result == {"ok": False, "check": "occ3_identity", "http_status": 503,
                      "error_code": "occ3_declined", "declined_code": "unsupported_profile",
                      "stage": "install", "experimental_selected": True, "preflight_reached": True,
                      "backend_constructed": True, "worker_readiness_reached": False,
                      "hooks_prepared": True, "route_mount_attempted": False,
                      "rollback_completed": True}
    # The status probe reuses the same credential, route and single-shot timeout.
    assert seen == [("POST", "http://fixture/occ3/v1/native/runtime/identity", KEY,
                     {"schema": "occ3.runtime.identity.request.v1"}, 15),
                    ("GET", "http://fixture/occ3/v1/status", KEY, None, 15)]


@pytest.mark.parametrize("state", ["INSTALLING", "CONTROL_REGISTERED", "NATIVE_READY", "DISABLED"])
def test_occ3_identity_transient_states_keep_exact_unavailable_evidence(state):
    result = checks.check_occ3_identity("http://fixture", KEY,
                                        requester=declining(dict(DECLINED_STATUS, state=state)))
    assert result == {"ok": False, "check": "occ3_identity", "http_status": 503,
                      "error_code": "occ3_identity_unavailable"}


def test_occ3_identity_status_404_means_control_plane_registration_failure():
    def requester(method, url, key, payload, timeout):
        if url.endswith(checks.OCC3_IDENTITY_PATH):
            return checks.HttpResult(503, {"error": {"code": "not_ready"}})
        return checks.HttpResult(404)
    result = checks.check_occ3_identity("http://fixture", KEY, requester=requester)
    assert result == {"ok": False, "check": "occ3_identity", "http_status": 503,
                      "error_code": "occ3_routes_absent"}


@pytest.mark.parametrize("mutation", ["wrong_schema", "missing_state", "lowercase_state",
                                      "not_dict", "invalid_json", "status_transport_error"])
def test_occ3_identity_unparseable_status_keeps_generic_unavailable(mutation):
    def requester(method, url, key, payload, timeout):
        if url.endswith(checks.OCC3_IDENTITY_PATH):
            return checks.HttpResult(503, {"error": {"code": "not_ready"}})
        if mutation == "wrong_schema":
            return checks.HttpResult(200, dict(DECLINED_STATUS, schema="optimus.occ3.status.v2"))
        if mutation == "missing_state":
            body = dict(DECLINED_STATUS)
            del body["state"]
            return checks.HttpResult(200, body)
        if mutation == "lowercase_state":
            return checks.HttpResult(200, dict(DECLINED_STATUS, state="declined"))
        if mutation == "not_dict":
            return checks.HttpResult(200, [])
        if mutation == "invalid_json":
            return checks.HttpResult(200, None, "invalid_json")
        raise RuntimeError(KEY)
    result = checks.check_occ3_identity("http://fixture", KEY, requester=requester)
    assert result == {"ok": False, "check": "occ3_identity", "http_status": 503,
                      "error_code": "occ3_identity_unavailable"}


@pytest.mark.parametrize("code", [None, 7, "not a code!", "UPPER_CODE", "x" * 65, "code\npath"])
def test_occ3_declined_evidence_sanitizes_non_taxonomy_detail(code):
    result = checks.check_occ3_identity("http://fixture", KEY,
                                        requester=declining(dict(DECLINED_STATUS, code=code, stage="cleanup")))
    assert result["error_code"] == "occ3_declined"
    assert result["declined_code"] is None and result["stage"] is None
    assert result["rollback_completed"] is True


def test_occ3_declined_booleans_require_literal_true():
    result = checks.check_occ3_identity("http://fixture", KEY,
        requester=declining(dict(DECLINED_STATUS, experimental_selected="true", rollback_completed=1)))
    assert result["error_code"] == "occ3_declined"
    assert result["experimental_selected"] is False and result["rollback_completed"] is False
    assert result["preflight_reached"] is True


@pytest.mark.parametrize("identity_status", [401, 403, 500, 502, None])
def test_occ3_identity_any_non_200_non_404_answer_still_probes_the_decline(identity_status):
    result = checks.check_occ3_identity("http://fixture", KEY,
                                        requester=declining(identity_status=identity_status))
    assert result["error_code"] == "occ3_declined" and result["http_status"] == identity_status
    assert result["declined_code"] == "unsupported_profile" and result["stage"] == "install"


def test_occ3_identity_404_never_probes_the_status_route():
    seen = []
    def requester(method, url, key, payload, timeout):
        seen.append(url)
        return checks.HttpResult(404)
    result = checks.check_occ3_identity("http://fixture", KEY, requester=requester)
    assert result["error_code"] == "occ3_routes_absent"
    assert seen == ["http://fixture/occ3/v1/native/runtime/identity"]


def test_occ3_declined_evidence_never_carries_foreign_body_fields():
    body = dict(DECLINED_STATUS, secret=KEY, path="/etc/passwd", exception="boom " + KEY)
    result = checks.check_occ3_identity("http://fixture", KEY, requester=declining(body))
    assert result["error_code"] == "occ3_declined" and result["declined_code"] == "unsupported_profile"
    assert KEY not in json.dumps(result) and "secret" not in result and "exception" not in result
