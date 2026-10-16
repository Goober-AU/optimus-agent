"""Typed contract for optimus.agent-comm.v1 (Python mirror of contract.rs).

Dataclasses, JSON (de)serialization in the corpus payload shape, the OCC-1
field mapping (mirroring AgentCommMessage::to_occ1 / from_occ1), validation,
and the equality oracle used for fidelity checks.

Conventions:
- The corpus payload shape omits None/empty optionals and the finding `id`
  (findings are positional F1..Fn, evidence E1..En, exactly like the OCC-1
  mapping in contract.rs).
- `protocol` is implicit ("optimus.agent-comm.v1") and not serialized.
- The equality oracle normalizes confidence to 3 decimals, mirroring the
  OCC-1 confidence formatting ("{:.3f}") that both codecs apply.
"""

import importlib.util
import json
import pathlib
import re
import unicodedata

_WORKTREE = pathlib.Path(__file__).resolve().parents[2]
_OCC1_PATH = _WORKTREE / "prime-agent-runtime" / "src" / "rlm" / "occ1.py"

_spec = importlib.util.spec_from_file_location("repo_occ1", _OCC1_PATH)
repo_occ1 = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(repo_occ1)
Occ1Message = repo_occ1.Occ1Message

AGENT_COMM_PROTOCOL = "optimus.agent-comm.v1"

KINDS = [
    "TASK", "PROGRESS", "RESULT", "EVIDENCE", "REQUEST_DETAIL",
    "ACK", "CORRECTION", "BLOCKED", "CANCELLED",
]
STATUSES = [
    "pending", "in_progress", "done", "partial",
    "blocked", "failed", "cancelled",
]
DELIVERY_STATES = [
    "accepted", "queued", "delivered", "processed",
    "result_reported", "verified",
]
EVIDENCE_KINDS = [
    "src", "commit", "log", "build", "tool", "artifact", "lines", "finding",
]
VERIFICATION_STATUSES = ["pass", "fail", "unknown"]

_CONF_RE = re.compile(r"^[+-]?\d+(\.\d+)?$")


class ContractError(Exception):
    """Contract validation or conversion failure."""


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------

class EvidenceRef:
    __slots__ = ("id", "kind", "locator", "line_range", "digest", "revision", "verified")

    def __init__(self, id, kind, locator, line_range=None, digest=None,
                 revision=None, verified=None):
        self.id = id
        self.kind = kind
        self.locator = locator
        self.line_range = tuple(line_range) if line_range else None
        self.digest = digest
        self.revision = revision
        self.verified = verified

    def to_payload(self):
        out = {"id": self.id, "kind": self.kind, "locator": self.locator}
        if self.line_range is not None:
            out["line_range"] = list(self.line_range)
        if self.digest is not None:
            out["digest"] = self.digest
        if self.revision is not None:
            out["revision"] = self.revision
        if self.verified is not None:
            out["verified"] = self.verified
        return out

    @classmethod
    def from_payload(cls, payload, index):
        return cls(
            id=payload.get("id", "E%d" % index),
            kind=payload["kind"],
            locator=payload["locator"],
            line_range=payload.get("line_range"),
            digest=payload.get("digest"),
            revision=payload.get("revision"),
            verified=payload.get("verified"),
        )


class Finding:
    __slots__ = ("id", "statement", "confidence", "evidence")

    def __init__(self, id, statement, confidence=None, evidence=None):
        self.id = id
        self.statement = statement
        self.confidence = confidence
        self.evidence = list(evidence or [])

    def to_payload(self):
        out = {"statement": self.statement}
        if self.confidence is not None:
            out["confidence"] = self.confidence
        if self.evidence:
            out["evidence"] = list(self.evidence)
        return out

    @classmethod
    def from_payload(cls, payload, index):
        return cls(
            id=payload.get("id", "F%d" % index),
            statement=payload["statement"],
            confidence=payload.get("confidence"),
            evidence=payload.get("evidence"),
        )


class Verification:
    __slots__ = ("claim", "status", "method", "receipt")

    def __init__(self, claim, status, method, receipt=None):
        self.claim = claim
        self.status = status
        self.method = method
        self.receipt = receipt

    def to_payload(self):
        out = {"claim": self.claim, "status": self.status, "method": self.method}
        if self.receipt is not None:
            out["receipt"] = self.receipt
        return out

    @classmethod
    def from_payload(cls, payload):
        return cls(
            claim=payload["claim"],
            status=payload["status"],
            method=payload["method"],
            receipt=payload.get("receipt"),
        )


class AgentCommMessage:
    __slots__ = (
        "message_id", "task_id", "parent_task_id", "trace_id", "sender_identity",
        "recipient_identity", "kind", "status", "findings", "evidence", "artifacts",
        "verification", "dependencies", "next_actions", "sequence", "protocol",
        "timestamp", "expiry",
    )

    def __init__(self, task_id, kind, status, message_id=None, parent_task_id=None,
                 trace_id=None, sender_identity=None, recipient_identity=None,
                 findings=None, evidence=None, artifacts=None, verification=None,
                 dependencies=None, next_actions=None, sequence=None,
                 timestamp=None, expiry=None):
        self.message_id = message_id
        self.task_id = task_id
        self.parent_task_id = parent_task_id
        self.trace_id = trace_id
        self.sender_identity = sender_identity
        self.recipient_identity = recipient_identity
        self.kind = kind
        self.status = status
        self.findings = list(findings or [])
        self.evidence = list(evidence or [])
        self.artifacts = list(artifacts or [])
        self.verification = verification
        self.dependencies = list(dependencies or [])
        self.next_actions = list(next_actions or [])
        self.sequence = sequence
        self.protocol = AGENT_COMM_PROTOCOL
        self.timestamp = timestamp
        self.expiry = expiry

    # -- payload (corpus JSON shape) ----------------------------------

    def to_payload(self):
        out = {
            "task_id": self.task_id,
            "kind": self.kind,
            "status": self.status,
        }
        optional = (
            ("message_id", self.message_id),
            ("parent_task_id", self.parent_task_id),
            ("trace_id", self.trace_id),
            ("sender_identity", self.sender_identity),
            ("recipient_identity", self.recipient_identity),
            ("sequence", self.sequence),
            ("timestamp", self.timestamp),
            ("expiry", self.expiry),
        )
        for key, value in optional:
            if value is not None:
                out[key] = value
        if self.findings:
            out["findings"] = [finding.to_payload() for finding in self.findings]
        if self.evidence:
            out["evidence"] = [item.to_payload() for item in self.evidence]
        if self.artifacts:
            out["artifacts"] = list(self.artifacts)
        if self.verification is not None:
            out["verification"] = self.verification.to_payload()
        if self.dependencies:
            out["dependencies"] = list(self.dependencies)
        if self.next_actions:
            out["next_actions"] = list(self.next_actions)
        return out

    @classmethod
    def from_payload(cls, payload):
        return cls(
            task_id=payload["task_id"],
            kind=payload["kind"],
            status=payload["status"],
            message_id=payload.get("message_id"),
            parent_task_id=payload.get("parent_task_id"),
            trace_id=payload.get("trace_id"),
            sender_identity=payload.get("sender_identity"),
            recipient_identity=payload.get("recipient_identity"),
            findings=[Finding.from_payload(p, i + 1)
                      for i, p in enumerate(payload.get("findings") or [])],
            evidence=[EvidenceRef.from_payload(p, i + 1)
                      for i, p in enumerate(payload.get("evidence") or [])],
            artifacts=payload.get("artifacts"),
            verification=(Verification.from_payload(payload["verification"])
                          if payload.get("verification") else None),
            dependencies=payload.get("dependencies"),
            next_actions=payload.get("next_actions"),
            sequence=payload.get("sequence"),
            timestamp=payload.get("timestamp"),
            expiry=payload.get("expiry"),
        )

    # -- validation (mirrors AgentCommMessage::validate) ---------------

    def validate(self):
        if not self.task_id or any(unicodedata.category(c) == "Cc" for c in self.task_id):
            raise ContractError("InvalidTaskId: %r" % self.task_id)
        if self.kind not in KINDS:
            raise ContractError("UnknownKind: %r" % self.kind)
        if self.status not in STATUSES:
            raise ContractError("UnknownStatus: %r" % self.status)
        if self.kind == "EVIDENCE" and not self.evidence:
            raise ContractError("an EVIDENCE message must carry at least one evidence reference")
        if self.protocol != AGENT_COMM_PROTOCOL:
            raise ContractError("ProtocolMismatch: %r" % self.protocol)
        evidence_ids = {item.id for item in self.evidence}
        for finding in self.findings:
            if finding.confidence is not None and not 0.0 <= finding.confidence <= 1.0:
                raise ContractError("InvalidConfidence: %r" % finding.confidence)
            for ref in finding.evidence:
                if ref not in evidence_ids:
                    raise ContractError("UnknownEvidence: %s -> %s" % (finding.id, ref))
        for item in self.evidence:
            if item.kind not in EVIDENCE_KINDS:
                raise ContractError("UnknownEvidenceKind: %r" % item.kind)
        if self.verification is not None:
            if self.verification.status not in VERIFICATION_STATUSES:
                raise ContractError("unknown verification status %r" % self.verification.status)
        for stamp in (self.timestamp, self.expiry):
            if stamp is not None and not _is_rfc3339(stamp):
                raise ContractError("InvalidTimestamp: %r" % stamp)

    # -- OCC-1 mapping (mirrors to_occ1 / from_occ1) -------------------

    def occ1_fields(self):
        """Ordered (key, value) pairs exactly as Rust to_occ1 emits."""
        self.validate()
        fields = []
        if self.message_id is not None:
            fields.append(("MSGID", self.message_id))
        fields.append(("TASK", self.task_id))
        if self.parent_task_id is not None:
            fields.append(("PARENT", self.parent_task_id))
        if self.trace_id is not None:
            fields.append(("TRACE", self.trace_id))
        if self.sender_identity is not None:
            fields.append(("SENDER", self.sender_identity))
        if self.recipient_identity is not None:
            fields.append(("RECIPIENT", self.recipient_identity))
        fields.append(("KIND", self.kind))
        fields.append(("STATUS", self.status))
        if self.sequence is not None:
            fields.append(("SEQ", str(self.sequence)))
        for index, finding in enumerate(self.findings, start=1):
            fields.append(("F%d" % index, finding.statement))
            if finding.confidence is not None:
                fields.append(("FC%d" % index, format(finding.confidence, ".3f")))
            if finding.evidence:
                fields.append(("FE%d" % index, ",".join(finding.evidence)))
        for index, item in enumerate(self.evidence, start=1):
            fields.append(("E%d" % index, "%s:%s" % (item.kind, item.locator)))
            if item.line_range is not None:
                fields.append(("EL%d" % index, "%d-%d" % item.line_range))
            if item.digest is not None:
                fields.append(("ED%d" % index, item.digest))
            if item.revision is not None:
                fields.append(("ER%d" % index, item.revision))
            if item.verified is not None:
                fields.append(("EV%d" % index, "pass" if item.verified else "fail"))
        for index, artifact in enumerate(self.artifacts, start=1):
            fields.append(("ART%d" % index, artifact))
        if self.verification is not None:
            fields.append(("V", self.verification.claim))
            fields.append(("VS", self.verification.status))
            fields.append(("VM", self.verification.method))
            if self.verification.receipt is not None:
                fields.append(("VR", self.verification.receipt))
        for index, dependency in enumerate(self.dependencies, start=1):
            fields.append(("DEP%d" % index, dependency))
        for action in self.next_actions:
            fields.append(("NEXT", action))
        if self.timestamp is not None:
            fields.append(("TS", self.timestamp))
        if self.expiry is not None:
            fields.append(("EXP", self.expiry))
        return fields

    def to_occ1(self):
        message = Occ1Message()
        for key, value in self.occ1_fields():
            message.push(key, value)
        return message

    @classmethod
    def from_occ1_fields(cls, fields):
        """Reconstructs the contract from ordered (key, value) OCC-1 pairs."""
        message = Occ1Message()
        for key, value in fields:
            message.push(key, value)
        return cls.from_occ1(message)

    @classmethod
    def from_occ1(cls, message):
        task_id = message.get("TASK")
        kind = message.get("KIND")
        status = message.get("STATUS")
        if task_id is None:
            raise ContractError("MissingRequired: TASK")
        if kind is None:
            raise ContractError("MissingRequired: KIND")
        if status is None:
            raise ContractError("MissingRequired: STATUS")
        out = cls(task_id=task_id, kind=kind, status=status)
        out.message_id = message.get("MSGID")
        out.parent_task_id = message.get("PARENT")
        out.trace_id = message.get("TRACE")
        out.sender_identity = message.get("SENDER")
        out.recipient_identity = message.get("RECIPIENT")
        seq = message.get("SEQ")
        if seq is not None:
            if not (seq.isascii() and seq.isdigit()):
                raise ContractError("OCC-1 conversion: SEQ %r is not a u64" % seq)
            out.sequence = int(seq)
        evidence_count = _indexed_count(message, "E")
        finding_count = _indexed_count(message, "F")
        for index in range(1, finding_count + 1):
            statement = message.get("F%d" % index)
            if statement is None:
                raise ContractError("finding index %d missing" % index)
            finding = Finding(id="F%d" % index, statement=statement)
            confidence = message.get("FC%d" % index)
            if confidence is not None:
                if _CONF_RE.fullmatch(confidence) is None:
                    raise ContractError("FC%d not a number" % index)
                finding.confidence = float(confidence)
            refs = message.get("FE%d" % index)
            if refs is not None:
                for reference in refs.split(","):
                    reference = reference.strip()
                    if not reference:
                        continue
                    if not reference.startswith("E"):
                        raise ContractError("FE%d references %r, expected E<n>" % (index, reference))
                    number = reference[1:]
                    if not (number.isascii() and number.isdigit()):
                        raise ContractError("FE%d references %r, expected E<n>" % (index, reference))
                    number = int(number)
                    if number == 0 or number > evidence_count:
                        raise ContractError("FE%d references E%d, outside 1..=%d"
                                             % (index, number, evidence_count))
                    finding.evidence.append("E%d" % number)
            out.findings.append(finding)
        for index in range(1, evidence_count + 1):
            encoded = message.get("E%d" % index)
            if encoded is None:
                raise ContractError("evidence index %d missing" % index)
            if ":" not in encoded:
                raise ContractError("E%d must be <kind>:<locator>" % index)
            kind_text, locator = encoded.split(":", 1)
            item = EvidenceRef(id="E%d" % index, kind=kind_text, locator=locator)
            value = message.get("EL%d" % index)
            if value is not None:
                if "-" not in value:
                    raise ContractError("EL%d must be <start>-<end>" % index)
                start, end = value.split("-", 1)
                if not (start.isascii() and start.isdigit() and end.isascii() and end.isdigit()):
                    raise ContractError("EL%d start/end invalid" % index)
                item.line_range = (int(start), int(end))
            item.digest = message.get("ED%d" % index)
            item.revision = message.get("ER%d" % index)
            verified = message.get("EV%d" % index)
            if verified is not None:
                if verified == "pass":
                    item.verified = True
                elif verified == "fail":
                    item.verified = False
                else:
                    raise ContractError("EV%d must be pass/fail/unknown, got %r"
                                        % (index, verified))
            out.evidence.append(item)
        artifact_count = _indexed_count(message, "ART")
        for index in range(1, artifact_count + 1):
            value = message.get("ART%d" % index)
            if value is None:
                raise ContractError("artifact index %d missing" % index)
            out.artifacts.append(value)
        claim = message.get("V")
        if claim is not None:
            vs = message.get("VS")
            vm = message.get("VM")
            if vs is None or vm is None:
                raise ContractError("V without VS/VM")
            out.verification = Verification(claim=claim, status=vs, method=vm,
                                            receipt=message.get("VR"))
        elif message.has("VS") or message.has("VM"):
            raise ContractError("VS/VM without V")
        dependency_count = _indexed_count(message, "DEP")
        for index in range(1, dependency_count + 1):
            value = message.get("DEP%d" % index)
            if value is None:
                raise ContractError("dependency index %d missing" % index)
            out.dependencies.append(value)
        out.next_actions = list(message.get_all("NEXT"))
        out.timestamp = message.get("TS")
        out.expiry = message.get("EXP")
        out.validate()
        return out


def _indexed_count(message, prefix):
    highest = 0
    for key, _value in message.iter_fields():
        if not key.startswith(prefix):
            continue
        rest = key[len(prefix):]
        if rest.isascii() and rest.isdigit():
            highest = max(highest, int(rest))
    for index in range(1, highest + 1):
        if not message.has("%s%d" % (prefix, index)):
            raise ContractError("%s index %d missing below the highest index %d"
                                 % (prefix, index, highest))
    return highest


_TIMESTAMP_RE = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?(Z|[+-]\d{2}:\d{2})$"
)


def _is_rfc3339(stamp):
    return _TIMESTAMP_RE.fullmatch(stamp) is not None


# ---------------------------------------------------------------------------
# Equality oracle
# ---------------------------------------------------------------------------

def _normalize(value):
    """Canonical form: confidence rounded to 3 decimals, keys sorted."""
    if isinstance(value, dict):
        return {key: _normalize(item) for key, item in sorted(value.items())}
    if isinstance(value, list):
        return [_normalize(item) for item in value]
    if isinstance(value, float):
        return round(value, 3)
    return value


def canonical_payload(message):
    return _normalize(message.to_payload())


def equal(a, b):
    """Fidelity oracle: two messages are equal iff canonical payloads match."""
    if isinstance(a, AgentCommMessage) and isinstance(b, AgentCommMessage):
        return canonical_payload(a) == canonical_payload(b)
    return _normalize(a) == _normalize(b)


def payload_from_listener_json(text):
    """Parse listener JSON output into a canonical payload dict.

    Accepts raw JSON or a fenced ```json block (fence use is recorded by the
    caller via the return flag). Raises ContractError when unparseable.
    """
    stripped = text.strip()
    try:
        parsed = json.loads(stripped)
        fenced = False
    except json.JSONDecodeError:
        match = re.search(r"```(?:json)?\s*(\{.*\})\s*```", stripped, re.DOTALL)
        if match is None:
            match = re.search(r"(\{.*\})", stripped, re.DOTALL)
        if match is None:
            raise ContractError("listener output is not JSON")
        parsed = json.loads(match.group(1))
        fenced = True
    if not isinstance(parsed, dict):
        raise ContractError("listener output is not a JSON object")
    return _normalize(parsed), fenced
