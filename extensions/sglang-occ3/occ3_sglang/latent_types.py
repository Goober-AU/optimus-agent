"""Tensor-free backend records. GPU objects live only in latent_worker/registry."""
from __future__ import annotations

import math
import os
import re
from dataclasses import asdict, dataclass
from typing import Any

from .attachment_contract import (CaptureWorkerDescriptor, ConsumeWorkerDescriptor,
                                  canonical, digest, identifier, uint)

METHOD = "statebridge.glm53.final-prenorm-predictor.continuous-prefix.v1"
ALIGNMENT = "union-subspace-procrustes.v1"
HEX = re.compile(r"[0-9a-f]{64}\Z")


class LatentError(RuntimeError):
    def __init__(self, code="latent_failed", members=None):
        allowed = {"latent_failed", "unavailable", "profile", "identity", "expired",
                   "quota", "replay", "state", "lineage", "ranks", "invalid",
                   "not_ready", "retired", "numerical", "cancelled"}
        self.code = code if code in allowed else "latent_failed"
        # Bounded sanitised names of the profile members that tripped; a live
        # decline names every failing check in one attempt instead of one.
        if (isinstance(members, (list, tuple)) and members
                and all(isinstance(name, str) and name.isascii()
                        and len(name) <= 32 for name in members)):
            self.members = ",".join(members)[:128]
        else:
            self.members = None
        super().__init__(self.code)


class UniformRequestCancellation(LatentError):
    """Only emitted after same-request, same-error all-rank preflight."""
    def __init__(self, code, native_rid, attempt_id):
        if code not in ("expired", "retired"):
            raise LatentError("invalid")
        identifier(native_rid)
        identifier(attempt_id)
        self.native_rid, self.attempt_id = native_rid, attempt_id
        super().__init__(code)


def require(condition, code="invalid"):
    if not condition:
        raise LatentError(code)


def require_members(checks, code="profile"):
    """Evaluate every (name, holds) member, then decline naming all failures.

    No short-circuit: a decline carries the bounded member names so one live
    window identifies every tripping profile check at that stage. Member
    conditions are positive, matching require(condition).
    """
    failed = [name for name, holds in checks if not holds]
    if failed:
        raise LatentError(code, members=failed)


def advertised_method(value):
    """True when value is a method id the native registry advertises.

    native_profile.py owns the one list of methods this build may pin, capture
    or negotiate, so a profile names either the per-message method (the default
    below) or the independent-calibration method, and never a copy of either.
    The registry is imported lazily: latent_types must stay importable while
    native_profile imports it.
    """
    if type(value) is not str:
        return False
    try:
        from .native_profile import ProfileError, advertised_methods
    except ImportError:
        # No usable registry in this host (an embedded or stubbed runtime): the
        # per-message method is the only id latent_types itself defines, so no
        # other id can be named and nothing else is accepted here.
        return value == METHOD
    try:
        return value in advertised_methods()
    except ProfileError:
        # A registry that cannot describe its own entries is broken, not open.
        return False


def sha(value):
    require(isinstance(value, str) and HEX.fullmatch(value) is not None)
    return value


def bounded_payload(value, *, maximum=16384):
    """Reject arbitrary objects/tensors before any serializer can call enc_hook."""
    def walk(v, depth=0):
        require(depth <= 12)
        if v is None or type(v) in (bool, str, int):
            if type(v) is int:
                require(-(2**63) <= v < 2**64)
            if isinstance(v, str):
                require(len(v) <= 1024)
            return
        if type(v) is float:
            require(math.isfinite(v))
            return
        if type(v) is dict:
            require(len(v) <= 64 and all(type(k) is str for k in v))
            for child in v.values():
                walk(child, depth + 1)
            return
        if type(v) in (list, tuple):
            require(len(v) <= 128)
            for child in v:
                walk(child, depth + 1)
            return
        raise LatentError("invalid")
    walk(value)
    require(len(canonical(value)) <= maximum, "quota")
    return value


def decode_descriptor(value):
    if value is None:
        return None
    from .attachment_contract import normalize_worker_descriptor
    return normalize_worker_descriptor(value)


@dataclass(frozen=True)
class RuntimeProfile:
    runtime_epoch: str
    runtime_generation: int
    model_fingerprint: str
    model_id: str
    expected_ranks: int = 8
    hidden_dim: int = 6144
    layer_index: int = 77
    max_prefix_tokens: int = 128
    max_live_bytes: int = 256 * 1024 * 1024
    workspace_bytes: int = 128 * 1024 * 1024
    max_records: int = 256
    max_active: int = 1
    max_ttl_seconds: int = 3600
    anchor_alpha: float = 0.0
    tile_rows: int = 256
    # An advertised native method id. The default is the per-message method, so
    # an unpinned caller is unchanged; the independent-calibration method is a
    # different id with its own artifact, never a reinterpretation of this one.
    method: str = METHOD
    # The frozen calibration artifact this profile is bound to: an absolute path
    # and the artifact_digest that the file at that path must contain. Both are
    # empty for the per-message method, which consumes no artifact. The worker
    # resolves the pair through statebridge_calibrated.deserialize_artifact
    # before any aligner exists, so a mismatched file is never loaded.
    calibration_path: str = ""
    calibration_digest: str = ""

    def __post_init__(self):
        for key in ("runtime_epoch", "model_fingerprint", "model_id"):
            identifier(getattr(self, key))
        uint(self.runtime_generation)
        for key in ("expected_ranks", "hidden_dim", "layer_index", "max_prefix_tokens",
                    "max_live_bytes", "workspace_bytes", "max_records", "max_active",
                    "max_ttl_seconds", "tile_rows"):
            uint(getattr(self, key))
        require(type(self.anchor_alpha) in (int, float) and math.isfinite(self.anchor_alpha), "profile")
        require(self.expected_ranks == 8 and self.hidden_dim == 6144 and self.layer_index == 77, "profile")
        require(self.max_prefix_tokens in (8, 16, 32, 64, 128)
                and advertised_method(self.method), "profile")
        for name in ("calibration_path", "calibration_digest"):
            require(type(getattr(self, name)) is str, "profile")
        # A reference is all-or-nothing, absolute and digest-pinned; the
        # per-message method consumes no artifact, so it may not carry one.
        require(bool(self.calibration_path) == bool(self.calibration_digest), "profile")
        if self.calibration_path:
            require(0 < len(self.calibration_path) <= 512
                    and "\0" not in self.calibration_path
                    and os.path.isabs(self.calibration_path), "profile")
            require(HEX.fullmatch(self.calibration_digest) is not None, "identity")
            require(self.method != METHOD, "profile")
        require(self.max_active == 1 and 1 <= self.max_records <= 4096, "profile")
        require(0 < self.max_live_bytes <= 2**30 and 0 < self.workspace_bytes <= 2**30, "profile")
        require(self.anchor_alpha in (0.0, 0.3) and 1 <= self.tile_rows <= 4096, "profile")
        require(0 < self.max_ttl_seconds <= 3600, "profile")

    @property
    def binding_digest(self):
        return digest(asdict(self))


@dataclass(frozen=True, repr=False)
class RequestSlice:
    descriptor: CaptureWorkerDescriptor | ConsumeWorkerDescriptor
    extend_start: int
    extend_len: int
    prefix_len: int
    output_start: int
    final_chunk: bool

    def __post_init__(self):
        for x in (self.extend_start, self.extend_len, self.prefix_len, self.output_start):
            require(type(x) is int and x >= 0)
        require(type(self.final_chunk) is bool)


@dataclass(frozen=True, repr=False)
class SessionEnrollment:
    principal_digest: str
    session_id: str
    model_fingerprint: str
    runtime_epoch: str
    runtime_generation: int
    access_domain: str
    expires_at_epoch: int

    def __post_init__(self):
        sha(self.principal_digest)
        for v in (self.session_id, self.model_fingerprint, self.runtime_epoch, self.access_domain):
            identifier(v)
        uint(self.runtime_generation)
        uint(self.expires_at_epoch)


@dataclass(frozen=True, repr=False)
class Publication:
    publication_id: str
    source_handle: str
    principal_digest: str
    source_session_id: str
    receiver_session_id: str
    message_id: str
    source_turn_nonce: str
    tool_call_id: str
    expires_at_epoch: int

    def __post_init__(self):
        sha(self.principal_digest)
        for k, v in asdict(self).items():
            if k not in ("principal_digest", "expires_at_epoch"):
                identifier(v)
        uint(self.expires_at_epoch)

    @property
    def binding_digest(self):
        return digest(asdict(self))


def control_request(command_id, operation, payload):
    identifier(command_id)
    require(operation in {"health", "enroll_session", "reserve_capture", "reserve_consume",
                          "publish", "publication_status", "status", "retire", "bind_output"})
    bounded_payload(payload)
    return {"schema": "occ3.control.v1", "command_id": command_id,
            "operation": operation, "payload": payload}


def parse_control(value):
    bounded_payload(value)
    require(type(value) is dict and set(value) == {"schema", "command_id", "operation", "payload"})
    require(value["schema"] == "occ3.control.v1" and type(value["payload"]) is dict)
    return control_request(value["command_id"], value["operation"], value["payload"])


def validate_control_payload(value, *, result=False):
    if not result:
        return parse_control(value)
    bounded_payload(value)
    require(type(value) is dict and set(value) == {"schema", "command_id", "ok", "result", "error"})
    require(value["schema"] == "occ3.control.result.v1" and type(value["ok"]) is bool)
    identifier(value["command_id"])
    require(type(value["result"]) is dict and type(value["error"]) is str)
    require((value["ok"] and not value["error"]) or (not value["ok"] and not value["result"]))
    return value
