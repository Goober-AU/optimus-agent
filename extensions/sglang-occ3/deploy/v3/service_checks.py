"""Single-shot service proofs. Bodies and credentials never enter safe evidence.

No function retries, changes settings, restarts a process, or executes a tool.
The controller owns deadlines and concurrent container-state supervision.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable

MAX_RESPONSE_BYTES = 2 * 1024 * 1024


@dataclass(repr=False)
class HttpResult:
    status: int | None
    body: Any = field(default=None, repr=False)
    error_code: str | None = None

    def __repr__(self):
        return f"HttpResult(status={self.status!r}, body=<private>)"


Requester = Callable[[str, str, str | None, dict | None, float], HttpResult]


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def request_json(method, url, key, payload=None, timeout=120) -> HttpResult:
    """Private transport. Never log exception strings, URLs, headers or bodies."""
    try:
        parts = urllib.parse.urlsplit(url)
        if (parts.scheme not in ("http", "https") or not parts.hostname
                or parts.username or parts.password or parts.fragment
                or not math.isfinite(timeout) or not 0 < timeout <= 600):
            return HttpResult(None, error_code="invalid_request")
        headers = {"Accept": "application/json", "Content-Type": "application/json"}
        if key is not None:
            headers["Authorization"] = "Bearer " + key
        data = json.dumps(payload).encode("utf-8") if payload is not None else None
        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        opener = urllib.request.build_opener(_NoRedirect())
        try:
            response = opener.open(req, timeout=timeout)
        except urllib.error.HTTPError as exc:
            response = exc
        with response:
            status = response.code
            raw = response.read(MAX_RESPONSE_BYTES + 1)
        if len(raw) > MAX_RESPONSE_BYTES:
            return HttpResult(status, error_code="response_too_large")
        if not raw.strip():
            return HttpResult(status)
        try:
            body = json.loads(raw)
        except (ValueError, UnicodeError):
            return HttpResult(status, error_code="invalid_json")
        return HttpResult(status, body)
    except Exception:
        return HttpResult(None, error_code="transport_error")


def _call(requester, method, base_url, path, key, payload, timeout):
    try:
        result = (requester or request_json)(
            method, base_url.rstrip("/") + path, key, payload, timeout
        )
        return result if isinstance(result, HttpResult) else HttpResult(
            None, error_code="invalid_transport_result"
        )
    except Exception:
        return HttpResult(None, error_code="transport_error")


def _evidence(check, result=None, error_code=None, **details):
    out = {"ok": error_code is None, "check": check,
           "http_status": result.status if result else None,
           "error_code": error_code}
    out.update(details)
    return out


def _http_failure(result):
    if result.status != 200:
        return "http_status"
    if result.error_code:
        return "invalid_response"
    return None


def _message(result, model):
    body = result.body
    if not isinstance(body, dict) or body.get("model") != model:
        return None
    choices = body.get("choices")
    usage = body.get("usage")
    if (not isinstance(choices, list) or len(choices) != 1
            or not isinstance(choices[0], dict)
            or not isinstance(usage, dict)
            or type(usage.get("completion_tokens")) is not int
            or usage["completion_tokens"] <= 0
            or type(usage.get("prompt_tokens")) is not int
            or usage["prompt_tokens"] <= 0
            or not isinstance(body.get("id"), str) or not body["id"]):
        return None
    message = choices[0].get("message")
    if not isinstance(message, dict) or message.get("role") != "assistant":
        return None
    return message, choices[0].get("finish_reason")


def check_health(base_url, key, timeout=5, *, requester=None) -> dict:
    result = _call(requester, "GET", base_url, "/health", key, None, timeout)
    # SGLang /health returns an empty body. This proves liveness only.
    error = None if result.status == 200 and result.error_code is None else "health_unready"
    return _evidence("health", result, error, scope="liveness_only")


OCC3_IDENTITY_PATH = "/occ3/v1/native/runtime/identity"
OCC3_STATUS_PATH = "/occ3/v1/status"
OCC3_STATUS_SCHEMA = "optimus.occ3.status.v1"
OCC3_DECLINE_STATE = "DECLINED"
_OCC3_DECLINE_CODE = re.compile(r"[a-z0-9_]{1,64}")
_OCC3_DECLINE_STAGE = ("prepare", "install")
_OCC3_PROGRESS_FIELDS = ("experimental_selected", "preflight_reached", "backend_constructed",
                         "worker_readiness_reached", "hooks_prepared", "route_mount_attempted",
                         "rollback_completed")


def _occ3_decline_details(body):
    """Fixed-vocabulary decline facts only; no free-form body text enters evidence."""
    details = {"declined_code": None, "stage": None}
    code = body.get("code")
    if isinstance(code, str) and _OCC3_DECLINE_CODE.fullmatch(code):
        details["declined_code"] = code
    if body.get("stage") in _OCC3_DECLINE_STAGE:
        details["stage"] = body.get("stage")
    for name in _OCC3_PROGRESS_FIELDS:
        details[name] = body.get(name) is True
    return details


def _occ3_identity_without_native_tuple(requester, base_url, key, timeout, result):
    """Ask the control plane why the identity route is not native-ready.

    A DECLINED server is terminal for its lifetime, so its fixed decline code
    fails the gate immediately instead of waiting out a proof deadline. Every
    other status answer keeps the generic unavailable evidence.
    """
    status = _call(requester, "GET", base_url, OCC3_STATUS_PATH, key, None, timeout)
    body = status.body if status.status == 200 and not status.error_code else None
    if (isinstance(body, dict) and body.get("schema") == OCC3_STATUS_SCHEMA
            and body.get("state") == OCC3_DECLINE_STATE):
        return _evidence("occ3_identity", result, "occ3_declined",
                         **_occ3_decline_details(body))
    if status.status == 404:
        # The control-plane router itself never registered on this app.
        return _evidence("occ3_identity", result, "occ3_routes_absent")
    return _evidence("occ3_identity", result, "occ3_identity_unavailable")


def check_occ3_identity(base_url, key, timeout=15, *, requester=None) -> dict:
    """Control-plane presence: the exact executor step-0 route; no new credential."""
    if not key:
        return _evidence("occ3_identity", error_code="missing_credentials")
    payload = {"schema": "occ3.runtime.identity.request.v1"}
    result = _call(requester, "POST", base_url, OCC3_IDENTITY_PATH, key, payload, timeout)
    if result.status == 404:
        # W4-A1 signature: api-key middleware active (401 keyless) but router absent.
        return _evidence("occ3_identity", result, "occ3_routes_absent")
    if result.status != 200 or result.error_code:
        return _occ3_identity_without_native_tuple(requester, base_url, key, timeout, result)
    body = result.body
    fields = ("schema", "runtime_id", "runtime_generation", "model_fingerprint", "expected_ranks",
              "ready_ranks", "source_capture", "continuous_prefix", "terminal_journal")
    if (not isinstance(body, dict) or body.get("schema") != "occ3.runtime.identity.v1"
            or any(name not in body for name in fields)
            or body.get("expected_ranks") != 8 or body.get("ready_ranks") != 8
            or body.get("source_capture") is not True
            or body.get("continuous_prefix") is not True
            or body.get("terminal_journal") is not True):
        return _evidence("occ3_identity", result, "occ3_identity_malformed")
    return _evidence("occ3_identity", result, None, route_present=True)


def check_chat(base_url, key, model, timeout=120, *, requester=None) -> dict:
    if not key or not model:
        return _evidence("chat", error_code="missing_credentials_or_model")
    nonce = "OCC3_READY_" + uuid.uuid4().hex
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": "Reply with exactly this string, "
                      "without quotes or any other text: " + nonce}],
        "max_tokens": 4096, "temperature": 0,
    }
    result = _call(requester, "POST", base_url, "/v1/chat/completions", key, payload, timeout)
    error = _http_failure(result)
    parsed = _message(result, model) if error is None else None
    if error is None and parsed is None:
        error = "malformed_chat"
    if error is None:
        message, finish = parsed
        if (finish != "stop" or not isinstance(message.get("content"), str)
                or message["content"].strip() != nonce or message.get("tool_calls")):
            error = "chat_semantics"
    details = {"semantic_match": error is None}
    if error is None:
        details["request_id_sha256"] = hashlib.sha256(result.body["id"].encode()).hexdigest()
    return _evidence("chat", result, error, **details)


def check_tools(base_url, key, model, timeout=120, *, requester=None) -> dict:
    if not key or not model:
        return _evidence("tools", error_code="missing_credentials_or_model")
    nonce = "OCC3_TOOL_" + uuid.uuid4().hex
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": "Call occ3_echo with nonce exactly "
                      + nonce + ". Do not answer in plain text."}],
        "tools": [{"type": "function", "function": {
            "name": "occ3_echo", "description": "Return the supplied verification nonce.",
            "parameters": {"type": "object", "properties": {"nonce": {"type": "string"}},
                           "required": ["nonce"], "additionalProperties": False},
        }}],
        "tool_choice": {"type": "function", "function": {"name": "occ3_echo"}},
        "max_tokens": 4096, "temperature": 0,
    }
    result = _call(requester, "POST", base_url, "/v1/chat/completions", key, payload, timeout)
    error = _http_failure(result)
    parsed = _message(result, model) if error is None else None
    if error is None and parsed is None:
        error = "malformed_tools"
    if error is None:
        message, finish = parsed
        calls = message.get("tool_calls")
        try:
            valid = finish == "tool_calls" and isinstance(calls, list) and len(calls) == 1
            call = calls[0] if valid else {}
            valid = (valid and call["type"] == "function"
                     and isinstance(call["id"], str) and bool(call["id"])
                     and call["function"]["name"] == "occ3_echo"
                     and isinstance(call["function"]["arguments"], str)
                     and json.loads(call["function"]["arguments"]) == {"nonce": nonce})
            if not valid:
                error = "tool_semantics"
        except (KeyError, IndexError, TypeError, ValueError):
            error = "tool_semantics"
    details = {"exact_function_and_arguments": error is None, "tool_executed": False}
    if error is None:
        details["request_id_sha256"] = hashlib.sha256(result.body["id"].encode()).hexdigest()
    return _evidence("tools", result, error, **details)


def check_auth(base_url, key, timeout=10, *, requester=None) -> dict:
    if not key:
        return _evidence("auth", error_code="missing_credentials")
    # Deliberately invalid fixture credential; never generate or rotate a serving key.
    invalid = "occ3-validation-deliberately-invalid"
    if invalid == key:
        return _evidence("auth", error_code="invalid_fixture_collision")
    statuses = []
    for supplied in (None, invalid):
        result = _call(requester, "GET", base_url, "/v1/models", supplied, None, timeout / 2)
        statuses.append(result.status)
    ok = all(status in (401, 403) for status in statuses)
    return _evidence("auth", error_code=None if ok else "auth_not_enforced",
                     absent_denied=statuses[0] in (401, 403),
                     wrong_denied=statuses[1] in (401, 403))


def check_ingress(base_url, key, model, timeout=120, *, requester=None, route=None) -> dict:
    """The trusted lifecycle adapter must execute *through* neo-k3-ingress.

    A URL or route label alone is not proof. No direct-HTTP fallback is allowed.
    The adapter must preserve its route/container identity in its own evidence.
    """
    if (requester is None or route != "neo-k3-ingress"
            or base_url.rstrip("/") != "http://neo-k3-ingress:8080"):
        return _evidence("ingress", error_code="ingress_route_unproven")
    deadline = time.monotonic() + timeout
    proofs = {}
    for name, check in (("chat", check_chat), ("tools", check_tools), ("auth", check_auth)):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return _evidence("ingress", error_code="ingress_deadline", proofs=proofs)
        args = (base_url, key) if name == "auth" else (base_url, key, model)
        proofs[name] = check(*args, timeout=remaining, requester=requester)
        if proofs[name]["ok"] is not True:
            return _evidence("ingress", error_code="ingress_" + name, proofs=proofs)
    return _evidence("ingress", route=route, proofs=proofs,
                     route_attestation_required=True)
