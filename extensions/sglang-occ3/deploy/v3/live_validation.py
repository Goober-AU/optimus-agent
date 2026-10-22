"""Fail-closed Gate 8 and a separately labelled source-prompt KV baseline.

No restart, deployment, settings change, native-message fallback or external proof
is performed here. The lifecycle supervisor alone may authorize a restart.
The baseline is ARM C, not StateBridge/LatentMAS and never passes true Gate 8.

The method-specific native phase-suite adapter (NativeHandoffValidator) is
selected only by an explicit reviewed plan key through lifecycle
Services.validator; the default here remains the blocked LiveValidator and the
SourceKvBaselineValidator experiment is unchanged. The adapter performs zero
HTTP and zero credential resolution: it admits offline pinned inputs, writes
per-phase challenges and verifies strictly bound harness reports.
"""
from __future__ import annotations

import hashlib
import json
import re
import time
import uuid

from .native_validator import (NativeHandoffValidator, UnselectedValidator,  # noqa: F401
                               VALIDATOR_KEY)
from .service_checks import HttpResult, request_json

PREFIX = "/occ3/v1/latent"
PROTOCOL = "source-prompt-kv-handle-v1"


class ProofFailure(Exception):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


def _digest(value):
    return hashlib.sha256(str(value).encode()).hexdigest()


class LiveValidator:
    """Production gate. No implemented true-latent/native adapter exists yet."""
    def __init__(self, base_url, key, model, timeout=120, attempt_id="", candidate_id="",
                 *, requester=None):
        self.base_url, self._key, self.model = base_url, key, model
        self.timeout = timeout
        self.attempt_id, self.candidate_id = attempt_id, candidate_id
        self._requester = requester or request_json

    def _call(self, method, path, payload=None):
        try:
            result = self._requester(method, self.base_url.rstrip("/") + path,
                                     self._key, payload, self.timeout)
            if not isinstance(result, HttpResult):
                return HttpResult(None, error_code="invalid_transport")
            return result
        except Exception:
            return HttpResult(None, error_code="transport_error")

    def _blocked(self, phase, blockers):
        return {"ok": False, "gate8_pass": False, "status": "blocked", "phase": phase,
                "scope": "true_latent_native_gate8", "blockers": blockers,
                "attempt_id": self.attempt_id, "candidate_id": self.candidate_id,
                "live_success_claimed": False}

    def pre_mutation_admission(self):
        """Local readiness only. Never spend swap authorization on a known gap."""
        code = "validated_native_latent_proof_adapter_not_implemented"
        result = self._blocked("pre_mutation_admission", [code])
        result["code"] = code
        return result

    def before_restart(self):
        result = self._call("GET", PREFIX + "/capabilities")
        if result.status != 200 or not isinstance(result.body, dict) or result.error_code:
            return self._blocked("before_restart", ["connector_unavailable"])
        caps = result.body
        blockers = []
        if caps.get("true_latent_method") is not True:
            blockers.append("true_latent_method_missing_text_radix_is_only_baseline")
        if caps.get("native_receiver_integrated") is not True:
            blockers.append("native_receiver_attachment_missing")
        # A self-reported capability cannot install a native semantic proof adapter.
        blockers.append("validated_native_latent_proof_adapter_not_implemented")
        return self._blocked("before_restart", blockers)

    def after_restart(self):
        return self._blocked("after_restart", ["no_successful_true_latent_prephase"])


class SourceKvBaselineValidator(LiveValidator):
    """Explicit experiment only. Even all baseline checks cannot set ok=True."""
    def __init__(self, *args, clock=time.monotonic, **kwargs):
        super().__init__(*args, **kwargs)
        self._clock = clock
        self._sender = "g8-s-" + uuid.uuid4().hex
        self._receiver = "g8-r-" + uuid.uuid4().hex
        self._epoch = None
        self._fingerprint = None
        self._stale = None
        self._before_passed = False
        self._before_attempted = False
        self._after_attempted = False
        self._owned = []
        self._cleanup_evidence = []
        self._capture_handle_unavailable = False

    def _json(self, method, path, payload=None, status=200):
        result = self._call(method, path, payload)
        if result.status != status or result.error_code or not isinstance(result.body, dict):
            raise ProofFailure("unexpected_http_or_body")
        return result.body

    def _bound(self, *, receiver=False, **extra):
        return {"opt_in": True, "session_id": self._receiver if receiver else self._sender,
                "model_fingerprint": self._fingerprint, **extra}

    def _capabilities(self):
        caps = self._json("GET", PREFIX + "/capabilities")
        if (caps.get("protocol") != PROTOCOL or caps.get("available") is not True
                or caps.get("capture_kind") != "source_prompt_kv"
                or caps.get("method_scope") != "text_radix_baseline"
                or not isinstance(caps.get("process_epoch"), str)
                or not caps["process_epoch"]
                or not isinstance(caps.get("model_fingerprint"), str)
                or caps["model_fingerprint"].count("|") != 10):
            raise ProofFailure("baseline_capability_unproven")
        return caps

    def _metrics(self):
        body = self._json("GET", "/occ3/v1/status")
        counters = body.get("metrics", {}).get("counters")
        if not isinstance(counters, dict):
            raise ProofFailure("metrics_missing")
        return {key: value for key, value in counters.items() if type(value) is int and value >= 0}

    def _capture(self, facts, ttl=600.0):
        text = "Private source facts. Return these exact values when asked: " + json.dumps(facts)
        result = self._call("POST", PREFIX + "/capture", self._bound(text=text, ttl_s=ttl))
        cap = result.body if isinstance(result.body, dict) else {}
        handle = cap.get("capture_handle")
        if (isinstance(handle, str) and re.fullmatch(r"[0-9a-f]{32}\.[0-9a-f]{32}", handle)
                and handle.startswith(self._epoch + ".")):
            if handle not in self._owned:
                self._owned.append(handle)
        else:
            self._capture_handle_unavailable = True
        if result.status != 200 or result.error_code:
            raise ProofFailure("capture_http_failed_cleanup_tracked")
        if (cap.get("process_epoch") != self._epoch
                or cap.get("model_fingerprint") != self._fingerprint
                or cap.get("protocol") != PROTOCOL
                or cap.get("capture_kind") != "source_prompt_kv"
                or cap.get("source_cached_tokens") != 0
                or cap.get("num_retractions") != 0
                or type(cap.get("required_gpu_tokens")) is not int
                or cap["required_gpu_tokens"] < 8
                or type(cap.get("source_prompt_tokens")) is not int
                or cap["source_prompt_tokens"] <= cap["required_gpu_tokens"]
                or not isinstance(cap.get("source_request_id"), str)
                or not cap["source_request_id"].startswith("occ3-capture-")
                or not isinstance(cap.get("capture_handle"), str)
                or not cap["capture_handle"].startswith(self._epoch + ".")
                or not isinstance(cap.get("salt_sha256"), str)
                or len(cap["salt_sha256"]) != 64
                or cap.get("state") != "CAPTURED"):
            raise ProofFailure("source_capture_unproven")
        sent = self._json("POST", PREFIX + "/send", self._bound(
            capture_handle=cap["capture_handle"], receiver_session_id=self._receiver))
        for field in ("source_request_id", "capture_handle", "model_fingerprint",
                      "process_epoch", "salt_sha256", "required_gpu_tokens"):
            if sent.get(field) != cap[field]:
                raise ProofFailure("send_source_lineage_mismatch")
        if (sent.get("send_inference_requests") != 0
                or sent.get("transport") != "opaque_handle_only"
                or sent.get("receiver_session_id") != self._receiver
                or sent.get("state") != "BOUND"
                or not isinstance(sent.get("channel_handle"), str)
                or not sent["channel_handle"].startswith(self._epoch + ".")):
            raise ProofFailure("handle_only_send_unproven")
        return sent

    @staticmethod
    def _facts():
        return {"code": "code-" + uuid.uuid4().hex, "owner": "owner-" + uuid.uuid4().hex,
                "slot": uuid.uuid4().hex[:12]}

    def _recv_payload(self, sent):
        # Never include the expected source values in the receiver request.
        return self._bound(receiver=True, channel_handle=sent["channel_handle"],
                           suffix="\nReturn only a JSON object with code, owner and slot "
                                  "copied exactly from the private source facts above.",
                           max_new_tokens=4096)

    def _receive(self, sent, facts):
        rec = self._json("POST", PREFIX + "/recv", self._recv_payload(sent))
        for field in ("source_request_id", "capture_handle", "channel_handle",
                      "model_fingerprint", "process_epoch", "salt_sha256",
                      "required_gpu_tokens"):
            if rec.get(field) != sent[field]:
                raise ProofFailure("receiver_source_lineage_mismatch")
        tiers = rec.get("cache_tiers")
        if (rec.get("hit") is not True or rec.get("fallback_used") is not False
                or rec.get("source_prefix_reused") is not True
                or rec.get("recomputed_meaningful_tokens") != 0
                or rec.get("num_retractions") != 0
                or rec.get("transport") != "opaque_handle_only"
                or not isinstance(tiers, dict)
                or type(tiers.get("device")) is not int
                or tiers["device"] < sent["required_gpu_tokens"]
                or tiers.get("host") != 0 or tiers.get("storage") != 0
                or rec.get("cached_tokens") != tiers["device"]
                or type(rec.get("completion_tokens")) is not int
                or rec["completion_tokens"] <= 0
                or not isinstance(rec.get("request_id"), str)
                or not rec["request_id"].startswith("occ3-receive-")):
            raise ProofFailure("gpu_first_use_unproven")
        try:
            actual = json.loads(rec.get("output_text", ""))
        except (TypeError, ValueError):
            raise ProofFailure("semantic_output_not_exact_json") from None
        if actual != facts:
            raise ProofFailure("semantic_transfer_incorrect")
        return {"source_request_id": sent["source_request_id"],
                "receiver_request_id": rec["request_id"],
                "required_gpu_tokens": sent["required_gpu_tokens"],
                "device_tokens_reused": tiers["device"], "host_tokens": 0,
                "storage_tokens": 0, "num_retractions": 0, "exact_fact_match": True,
                "source_facts_sha256": _digest(json.dumps(facts, sort_keys=True)),
                "source_prefix_reused": True, "fallback_used": False}

    def _release(self, sent):
        released = self._json("POST", PREFIX + "/release",
                              self._bound(capture_handle=sent["capture_handle"]))
        if (released.get("released") is not True or released.get("state") != "RETIRED"
                or released.get("cpu_references_removed") is not True
                or released.get("gpu_erased") is not False
                or released.get("release_kind") != "handle_retirement_lru"):
            raise ProofFailure("release_unproven")
        self._json("POST", PREFIX + "/recv", self._recv_payload(sent), status=410)
        self._owned.remove(sent["capture_handle"])

    def _result(self, phase, checks, error=None):
        return {"ok": False, "gate8_pass": False, "baseline_ok": error is None,
                "scope": "text_radix_baseline", "phase": phase,
                "status": "blocked" if error is None else "failed",
                "error_code": error, "checks": checks,
                "attempt_id": self.attempt_id, "candidate_id": self.candidate_id,
                "process_epoch": self._epoch,
                "blockers": ["true_latent_method_missing", "native_receiver_use_unproven",
                             "native_fallback_unproven"],
                "restart_authorized": False, "gpu_erasure_proven": False,
                "cleanup_evidence": list(self._cleanup_evidence),
                "cleanup_pending_handles": len(self._owned),
                "capture_handle_unavailable": self._capture_handle_unavailable}

    def _cleanup(self):
        for handle in list(self._owned):
            result = self._call("POST", PREFIX + "/release", self._bound(capture_handle=handle))
            body = result.body if isinstance(result.body, dict) else {}
            confirmed = (result.status == 200 and not result.error_code
                         and body.get("released") is True and body.get("state") == "RETIRED"
                         and body.get("cpu_references_removed") is True
                         and body.get("gpu_erased") is False)
            self._cleanup_evidence.append({"handle_sha256": _digest(handle),
                "http_status": result.status, "cpu_retirement_confirmed": confirmed,
                "gpu_erasure_proven": False,
                "error_code": None if confirmed else "cleanup_unconfirmed"})
            if confirmed:
                self._owned.remove(handle)

    def before_restart(self):
        checks = {}
        if self._before_attempted:
            return self._result("before_restart", checks, "no_automatic_repeat")
        self._before_attempted = True
        try:
            caps = self._capabilities()
            self._epoch, self._fingerprint = caps["process_epoch"], caps["model_fingerprint"]
            baseline = self._metrics()
            expiry = self._capture(self._facts(), ttl=1.0)
            expiry_not_before = self._clock() + max(0, expiry.get("remaining_ttl_s", 1.0))
            first_facts, second_facts = self._facts(), self._facts()
            first = self._capture(first_facts)
            second = self._capture(second_facts)
            if first["salt_sha256"] == second["salt_sha256"]:
                raise ProofFailure("salt_isolation_failed")
            wrong_receiver = self._recv_payload(first)
            wrong_receiver["session_id"] = self._sender
            self._json("POST", PREFIX + "/recv", wrong_receiver, status=403)
            wrong_model = self._recv_payload(first)
            wrong_model["model_fingerprint"] += "-different-checkpoint"
            self._json("POST", PREFIX + "/recv", wrong_model, status=409)
            checks["first_transfer"] = self._receive(first, first_facts)
            checks["second_transfer"] = self._receive(second, second_facts)
            checks["cross_channel_and_model_isolation"] = True
            self._release(first)
            self._release(second)
            checks["release"] = "handle_retired_not_physical_gpu_erasure"
            if self._clock() < expiry_not_before:
                raise ProofFailure("expiry_window_not_elapsed_no_sleep_or_poll")
            self._json("POST", PREFIX + "/recv", self._recv_payload(expiry), status=410)
            checks["expiry"] = True
            missing = dict(self._recv_payload(expiry), channel_handle=self._epoch + "." + uuid.uuid4().hex)
            self._json("POST", PREFIX + "/recv", missing, status=410)
            checks["unavailable"] = "explicit_refusal_no_fallback_or_retry"
            final = self._metrics()
            delta = {key: final.get(key, 0) - baseline.get(key, 0) for key in (
                "source_capture_ok", "handle_send_ok", "handle_recv_hit", "handle_release_ok")}
            if any(delta[key] < expected for key, expected in (
                    ("source_capture_ok", 3), ("handle_send_ok", 3),
                    ("handle_recv_hit", 2), ("handle_release_ok", 2))):
                raise ProofFailure("per_attempt_metrics_missing")
            checks["metrics_delta"] = delta
            self._stale = self._capture(self._facts())
            self._before_passed = True
            return self._result("before_restart", checks)
        except ProofFailure as exc:
            self._cleanup()
            return self._result("before_restart", checks, exc.code)
        except Exception:
            self._cleanup()
            return self._result("before_restart", checks, "invalid_proof_response")

    def after_restart(self):
        checks = {}
        if not self._before_passed or self._after_attempted:
            return self._result("after_restart", checks, "invalid_phase_order")
        self._after_attempted = True
        error = None
        try:
            caps = self._capabilities()
            if caps["process_epoch"] == self._epoch:
                raise ProofFailure("process_epoch_unchanged")
            if caps["model_fingerprint"] != self._fingerprint:
                raise ProofFailure("model_changed_across_restart")
            self._json("POST", PREFIX + "/recv", self._recv_payload(self._stale), status=410)
            checks["old_handle_stale"] = True
            self._owned.clear()
            self._epoch = caps["process_epoch"]
            facts = self._facts()
            fresh = self._capture(facts)
            checks["fresh_transfer"] = self._receive(fresh, facts)
            self._release(fresh)
        except ProofFailure as exc:
            error = exc.code
        except Exception:
            error = "invalid_proof_response"
        finally:
            self._cleanup()
        # Snapshot cleanup evidence only after bounded retirement attempts finish.
        return self._result("after_restart", checks, error)
