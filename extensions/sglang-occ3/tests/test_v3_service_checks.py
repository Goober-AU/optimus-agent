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
