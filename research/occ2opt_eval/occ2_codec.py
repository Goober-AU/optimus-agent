"""OCC-2 wire codecs for the evaluation driver (offline, deterministic).

Two dialect sides, both faithful mirrors of production paths:

- `SymV1Codec`: the production OCC-2 codec (`crates/pi-coding-agent/src/
  core/occ/occ2.rs`, card `DialectCard::sym_v1()` in `dialect.rs`) and the
  runtime codebook legend (`agent_messages.rs::occ2_codebook_legend`).
  The codebook sha256 is recomputed at import and asserted against the
  pin in occ2.rs's tests, so drift is impossible.

- `Dialect2EvalCodec`: the promoted DIALECT/2 winner
  (`research/symbolic_comm2/dialect2-card.json`, production id
  `sym-v2-d2-r1`) through the Python research codec
  (`research/symbolic_comm2/dialect2.py`), plus a codebook text rendered
  programmatically from the card (never hand-written prose).

Both expose the same interface the driver needs:
  dialect_id, version, wire_header_hint, codebook_text(), codebook_sha_prefix(),
  encode(message), decode(wire), parse_speaker_output(text),
  decode_wire_payload(wire).

No model calls, no network. Pure functions over the frozen artifacts.
"""

import hashlib
import json
import os
import re
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(os.path.dirname(_HERE))
for _p in ("research/symbolic_comm", "research/symbolic_comm2"):
    pass  # path inserted below with forward-slash joining per platform


def _insert_path(*parts):
    full = os.path.join(_ROOT, *parts)
    if full not in sys.path:
        sys.path.insert(0, full)


_insert_path("research", "symbolic_comm")
_insert_path("research", "symbolic_comm2")

import contract as SYM  # noqa: E402  (frozen typed contract)


# ---------------------------------------------------------------------------
# sym-v1: the production OCC-2 codec mirror
# ---------------------------------------------------------------------------

class Occ2FormatError(Exception):
    """Raised when a sym-v1 wire violates the production codec rules."""


SYM_V1_DIALECT_ID = "sym-v1"
SYM_V1_VERSION = 1
SYM_V1_CODEBOOK_SHA256 = (
    "1efce9992ee24ff779a0430d998b37704d484f9cbb90c2fb78feec063add1547"
)
CODEBOOK_PREFIX_LEN = 12

SYM_V1_SLOTS = {
    "message_id": "M",
    "task_id": "T",
    "parent_task_id": "P",
    "trace_id": "R",
    "sender": "A",
    "recipient": "B",
    "kind": "K",
    "status": "S",
    "sequence": "Q",
    "timestamp": "C",
    "expiry": "X",
    "finding.statement": "F",
    "evidence.ref": "E",
    "artifact": "G",
    "verification.claim": "V",
    "dependency": "D",
    "next_action": "N",
}
SYM_V1_SUBFIELDS = {
    "finding.confidence": "c",
    "finding.evidence": "e",
    "evidence.line_range": "l",
    "evidence.digest": "d",
    "evidence.revision": "r",
    "evidence.verified": "v",
    "verification.status": "s",
    "verification.method": "m",
    "verification.receipt": "r",
}


def _sym_v1_codebook_sha256():
    """sha256 over the canonical codebook JSON, byte-identical to Rust
    `codebook_sha256(&DialectCard::sym_v1())` (struct field order, sorted
    maps)."""
    slots = json.dumps(SYM_V1_SLOTS, separators=(",", ":"), sort_keys=True)
    subfields = json.dumps(SYM_V1_SUBFIELDS, separators=(",", ":"), sort_keys=True)
    canonical = (
        '{"grammar":{"operator":"=","list_separator":","},'
        '"symbol_table":{"slots":' + slots + ',"subfields":' + subfields + "}}"
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


assert _sym_v1_codebook_sha256() == SYM_V1_CODEBOOK_SHA256, (
    "sym-v1 canonical codebook hash drifted from the Rust pin"
)

OCC2_MAX_FIELDS = 128
OCC2_MAX_VALUE_BYTES = 4096
OCC2_MAX_SYMBOLIC_BYTES = 64 * 1024

_RFC3339_RE = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})$"
)
_KINDS = {
    "TASK", "PROGRESS", "RESULT", "EVIDENCE", "REQUEST_DETAIL",
    "ACK", "CORRECTION", "BLOCKED", "CANCELLED",
}
_STATUSES = {
    "pending", "in_progress", "done", "partial", "blocked", "failed", "cancelled",
}
_EVIDENCE_KINDS = {
    "src", "commit", "log", "build", "tool", "artifact", "lines", "finding",
}


class SymV1Codec:
    """Production OCC-2 sym-v1 wire codec (Python mirror of occ2.rs)."""

    dialect_id = SYM_V1_DIALECT_ID
    version = SYM_V1_VERSION
    operator = "="
    list_separator = ","

    def __init__(self):
        self._slots = dict(SYM_V1_SLOTS)
        self._subfields = dict(SYM_V1_SUBFIELDS)
        self._symbol_to_slot = {symbol: slot for slot, symbol in self._slots.items()}
        # prefix-free symbols: at most one base symbol matches any key
        self._sha = SYM_V1_CODEBOOK_SHA256
        self._prefix = self._sha[:CODEBOOK_PREFIX_LEN]
        self._legend = self._render_legend()

    # -- identity ---------------------------------------------------------

    def codebook_sha_prefix(self):
        return self._prefix

    def _render_legend(self):
        """Byte-identical mirror of agent_messages.rs::occ2_codebook_legend."""
        slots = ", ".join(
            "%s:%s" % (symbol, name)
            for name, symbol in sorted(self._slots.items())
        )
        subfields = ", ".join(
            "%s:%s" % (name, marker)
            for name, marker in sorted(self._subfields.items())
        )
        return (
            "OCC-2 %s@%d codebook (sha256:%s): %s. Indexed fields use 1-based "
            "indices; suffixes: %s. Values follow '='; evidence references "
            "are comma-separated. Reply using OCC-1; transport handles "
            "encoding." % (self.dialect_id, self.version, self._prefix, slots, subfields)
        )

    def codebook_text(self):
        return self._legend

    def wire_header_hint(self):
        return "OCC/2 D=%s@%d C=%s" % (self.dialect_id, self.version, self._prefix)

    # -- encode -----------------------------------------------------------

    def encode(self, message):
        """The symbolic section exactly as occ2.rs::encode_symbolic emits."""
        fields = []

        def emit(key, value):
            fields.append("%s=%s" % (key, value))

        if message.message_id is not None:
            emit("M", message.message_id)
        emit("T", message.task_id)
        if message.parent_task_id is not None:
            emit("P", message.parent_task_id)
        if message.trace_id is not None:
            emit("R", message.trace_id)
        if message.sender_identity is not None:
            emit("A", message.sender_identity)
        if message.recipient_identity is not None:
            emit("B", message.recipient_identity)
        emit("K", message.kind)
        emit("S", message.status)
        if message.sequence is not None:
            emit("Q", str(message.sequence))
        for index, finding in enumerate(message.findings, start=1):
            emit("F%d" % index, finding.statement)
            if finding.confidence is not None:
                emit("F%dc" % index, "%.3f" % finding.confidence)
            if finding.evidence:
                references = []
                for reference in finding.evidence:
                    number = reference[1:] if reference[:1] == "E" else None
                    if number is None or not number.isdigit() or int(number) < 1:
                        raise Occ2FormatError(
                            "finding %d references non-positional evidence %r"
                            % (index, reference)
                        )
                    references.append(str(int(number)))
                emit("F%de" % index, self.list_separator.join(references))
        for index, evidence in enumerate(message.evidence, start=1):
            emit("E%d" % index, "%s:%s" % (evidence.kind, evidence.locator))
            if evidence.line_range is not None:
                emit(
                    "E%dl" % index,
                    "%d-%d" % (evidence.line_range[0], evidence.line_range[1]),
                )
            if evidence.digest is not None:
                emit("E%dd" % index, evidence.digest)
            if evidence.revision is not None:
                emit("E%dr" % index, evidence.revision)
            if evidence.verified is not None:
                emit("E%dv" % index, "1" if evidence.verified else "0")
        for index, artifact in enumerate(message.artifacts, start=1):
            emit("G%d" % index, artifact)
        if message.verification is not None:
            emit("V", message.verification.claim)
            emit("Vs", message.verification.status)
            emit("Vm", message.verification.method)
            if message.verification.receipt is not None:
                emit("Vr", message.verification.receipt)
        for index, dependency in enumerate(message.dependencies, start=1):
            emit("D%d" % index, dependency)
        for action in message.next_actions:
            emit("N", action)
        if message.timestamp is not None:
            emit("C", message.timestamp)
        if message.expiry is not None:
            emit("X", message.expiry)

        if len(fields) + 1 > OCC2_MAX_FIELDS:
            raise Occ2FormatError("message exceeds the %d field bound" % OCC2_MAX_FIELDS)
        header = "OCC/2 D=%s@%d C=%s" % (self.dialect_id, self.version, self._prefix)
        return "\n".join([header] + fields)

    # -- decode -----------------------------------------------------------

    def decode(self, wire):
        """Strict mirror of occ2.rs::decode on a symbolic (non-dual) wire."""
        if not wire:
            raise Occ2FormatError("OCC-2 input is empty")
        if len(wire.encode("utf-8")) > OCC2_MAX_SYMBOLIC_BYTES:
            raise Occ2FormatError("OCC-2 input too large")
        text = wire[:-1] if wire.endswith("\n") else wire
        lines = text.split("\n")
        header = lines[0][:-1] if lines[0].endswith("\r") else lines[0]
        self._parse_header(header)
        parsed = []
        for offset, raw in enumerate(lines[1:]):
            line_no = offset + 2
            line = raw[:-1] if raw.endswith("\r") else raw
            if not line:
                raise Occ2FormatError("line %d: blank lines are not allowed" % line_no)
            if len(parsed) >= OCC2_MAX_FIELDS:
                raise Occ2FormatError("message exceeds the %d field bound" % OCC2_MAX_FIELDS)
            key, sep, value = line.partition(self.operator)
            if not sep:
                raise Occ2FormatError(
                    "line %d: expected SYMBOL%sVALUE" % (line_no, self.operator)
                )
            if len(value.encode("utf-8")) > OCC2_MAX_VALUE_BYTES:
                raise Occ2FormatError("value of %r exceeds %d bytes" % (key, OCC2_MAX_VALUE_BYTES))
            if any(ord(ch) < 0x20 or ord(ch) == 0x7F for ch in value):
                raise Occ2FormatError(
                    "line %d: value of %r contains a control character" % (line_no, key)
                )
            slot, index, marker = self._parse_key(key)
            parsed.append((slot, index, marker, value, key, line_no))
        return self._finish(parsed)

    def _parse_header(self, header):
        tokens = header.split(" ")
        if len(tokens) != 3 or tokens[0] != "OCC/2":
            raise Occ2FormatError("OCC-2 header %r is malformed" % header)
        dialect_token = tokens[1]
        if not dialect_token.startswith("D="):
            raise Occ2FormatError("OCC-2 header %r is malformed" % header)
        if "@" not in dialect_token[2:]:
            raise Occ2FormatError("OCC-2 header %r is malformed" % header)
        dialect, version_text = dialect_token[2:].split("@", 1)
        if (
            not dialect
            or not dialect[0].islower()
            or not all(ch.islower() or ch.isdigit() or ch == "-" for ch in dialect)
            or not version_text
            or not version_text.isdigit()
            or int(version_text) == 0
        ):
            raise Occ2FormatError("OCC-2 header %r is malformed" % header)
        if dialect != self.dialect_id or int(version_text) != self.version:
            raise Occ2FormatError(
                "unknown dialect %s@%s (this codec is %s@%d)"
                % (dialect, version_text, self.dialect_id, self.version)
            )
        hash_token = tokens[2]
        if not hash_token.startswith("C="):
            raise Occ2FormatError("OCC-2 header %r is malformed" % header)
        prefix = hash_token[2:]
        if len(prefix) != CODEBOOK_PREFIX_LEN or any(
            ch not in "0123456789abcdef" for ch in prefix
        ):
            raise Occ2FormatError("OCC-2 header %r is malformed" % header)
        if prefix != self._prefix:
            raise Occ2FormatError(
                "codebook hash mismatch: header says %s, registered card says %s"
                % (prefix, self._prefix)
            )

    def _parse_key(self, key):
        """(slot, index-or-None, marker-or-None); mirrors parse_key."""
        for symbol, slot in self._symbol_to_slot.items():
            if not key.startswith(symbol):
                continue
            rest = key[len(symbol):]
            invalid = Occ2FormatError("key %r is not valid for dialect %s" % (key, self.dialect_id))
            if slot in (
                "message_id", "task_id", "parent_task_id", "trace_id", "sender",
                "recipient", "kind", "status", "timestamp", "expiry",
            ):
                if rest:
                    raise invalid
                return slot, None, None
            if slot in ("sequence", "next_action"):
                if rest:
                    raise invalid
                return slot, None, None
            if slot == "verification.claim":
                if not rest:
                    return slot, None, None
                if len(rest) == 1 and rest.islower():
                    name = {
                        self._subfields["verification.status"]: "verification.status",
                        self._subfields["verification.method"]: "verification.method",
                        self._subfields["verification.receipt"]: "verification.receipt",
                    }.get(rest)
                    if name is not None:
                        return name, None, None
                raise invalid
            index, marker = self._split_index_marker(rest)
            if index is None:
                raise invalid
            if slot == "finding.statement":
                if marker is None:
                    return "finding.statement", index, None
                if marker == self._subfields["finding.confidence"]:
                    return "finding.confidence", index, None
                if marker == self._subfields["finding.evidence"]:
                    return "finding.evidence", index, None
                raise invalid
            if slot == "evidence.ref":
                if marker is None:
                    return "evidence.ref", index, None
                name = {
                    self._subfields["evidence.line_range"]: "evidence.line_range",
                    self._subfields["evidence.digest"]: "evidence.digest",
                    self._subfields["evidence.revision"]: "evidence.revision",
                    self._subfields["evidence.verified"]: "evidence.verified",
                }.get(marker)
                if name is None:
                    raise invalid
                return name, index, None
            if slot == "artifact":
                if marker is not None:
                    raise invalid
                return "artifact", index, None
            if slot == "dependency":
                if marker is not None:
                    raise invalid
                return "dependency", index, None
            raise invalid
        raise Occ2FormatError(
            "key %r does not match any symbol in dialect %s" % (key, self.dialect_id)
        )

    @staticmethod
    def _split_index_marker(rest):
        digit_count = 0
        for ch in rest:
            if ch.isdigit():
                digit_count += 1
            else:
                break
        digits, tail = rest[:digit_count], rest[digit_count:]
        if not digits:
            return None, None
        index = int(digits)
        if index == 0:
            return None, None
        if not tail:
            return index, None
        if len(tail) == 1 and tail.islower():
            return index, tail
        return None, None

    def _finish(self, parsed):
        scalars = {}
        findings = {}
        evidence = {}
        artifacts = {}
        dependencies = {}
        next_actions = []
        verification = {}

        for slot, index, _marker, value, key, line_no in parsed:
            if slot in (
                "message_id", "task_id", "parent_task_id", "trace_id", "sender",
                "recipient", "kind", "status", "timestamp", "expiry",
            ):
                if slot in scalars:
                    raise Occ2FormatError("field %r appears more than once" % key)
                scalars[slot] = value
            elif slot == "sequence":
                if "sequence" in scalars:
                    raise Occ2FormatError("field %r appears more than once" % key)
                if not value.isdigit():
                    raise Occ2FormatError("line %d: value of %r is not a u64" % (line_no, key))
                scalars["sequence"] = int(value)
            elif slot == "next_action":
                next_actions.append(value)
            elif slot == "finding.statement":
                entry = findings.setdefault(index, {})
                if "statement" in entry:
                    raise Occ2FormatError("field %r appears more than once" % key)
                entry["statement"] = value
            elif slot == "finding.confidence":
                entry = findings.setdefault(index, {})
                if "confidence" in entry:
                    raise Occ2FormatError("field %r appears more than once" % key)
                try:
                    entry["confidence"] = float(value)
                except ValueError:
                    raise Occ2FormatError("line %d: value of %r is not a number" % (line_no, key))
            elif slot == "finding.evidence":
                entry = findings.setdefault(index, {})
                if "evidence" in entry:
                    raise Occ2FormatError("field %r appears more than once" % key)
                references = []
                for part in value.split(self.list_separator):
                    if not part.isdigit() or int(part) == 0:
                        raise Occ2FormatError(
                            "line %d: evidence references must be 1-based decimal indices"
                            % line_no
                        )
                    references.append(int(part))
                entry["evidence"] = references
            elif slot == "evidence.ref":
                entry = evidence.setdefault(index, {})
                if "kind_locator" in entry:
                    raise Occ2FormatError("field %r appears more than once" % key)
                entry["kind_locator"] = value
            elif slot == "evidence.line_range":
                entry = evidence.setdefault(index, {})
                if "line_range" in entry:
                    raise Occ2FormatError("field %r appears more than once" % key)
                start, sep, end = value.partition("-")
                if not sep or not start.isdigit() or not end.isdigit():
                    raise Occ2FormatError(
                        "line %d: value of %r must be <start>-<end>" % (line_no, key)
                    )
                entry["line_range"] = (int(start), int(end))
            elif slot == "evidence.digest":
                entry = evidence.setdefault(index, {})
                if "digest" in entry:
                    raise Occ2FormatError("field %r appears more than once" % key)
                entry["digest"] = value
            elif slot == "evidence.revision":
                entry = evidence.setdefault(index, {})
                if "revision" in entry:
                    raise Occ2FormatError("field %r appears more than once" % key)
                entry["revision"] = value
            elif slot == "evidence.verified":
                entry = evidence.setdefault(index, {})
                if "verified" in entry:
                    raise Occ2FormatError("field %r appears more than once" % key)
                if value not in ("1", "0"):
                    raise Occ2FormatError(
                        "line %d: value of %r must be 1 (verified) or 0 (failed)"
                        % (line_no, key)
                    )
                entry["verified"] = value == "1"
            elif slot == "artifact":
                if index in artifacts:
                    raise Occ2FormatError("field %r appears more than once" % key)
                artifacts[index] = value
            elif slot == "dependency":
                if index in dependencies:
                    raise Occ2FormatError("field %r appears more than once" % key)
                dependencies[index] = value
            elif slot.startswith("verification."):
                name = slot.split(".", 1)[1]
                if name in verification:
                    raise Occ2FormatError("field %r appears more than once" % key)
                verification[name] = value
            else:
                raise Occ2FormatError("unhandled slot %r" % slot)

        for name, mapping, prefix in (
            ("findings", findings, "F"),
            ("evidence", evidence, "E"),
            ("artifacts", artifacts, "G"),
            ("dependencies", dependencies, "D"),
        ):
            if mapping:
                highest = max(mapping)
                if len(mapping) != highest:
                    raise Occ2FormatError(
                        "%s index gap below the highest index %d" % (prefix, highest)
                    )

        for required in ("task_id", "kind", "status"):
            if required not in scalars:
                raise Occ2FormatError("contract field %r is required" % required)

        out = SYM.AgentCommMessage(
            task_id=scalars["task_id"],
            kind=scalars["kind"],
            status=scalars["status"],
            message_id=scalars.get("message_id"),
            parent_task_id=scalars.get("parent_task_id"),
            trace_id=scalars.get("trace_id"),
            sender_identity=scalars.get("sender"),
            recipient_identity=scalars.get("recipient"),
            sequence=scalars.get("sequence"),
            timestamp=scalars.get("timestamp"),
            expiry=scalars.get("expiry"),
        )
        evidence_count = len(evidence)
        for index in sorted(findings):
            entry = findings[index]
            if "statement" not in entry:
                raise Occ2FormatError("F%d statement missing" % index)
            links = entry.get("evidence", [])
            for number in links:
                if number > evidence_count:
                    raise Occ2FormatError(
                        "F%d references E%d, outside 1..=%d" % (index, number, evidence_count)
                    )
            out.findings.append(
                SYM.Finding(
                    id="F%d" % index,
                    statement=entry["statement"],
                    confidence=entry.get("confidence"),
                    evidence=["E%d" % number for number in links],
                )
            )
        for index in sorted(evidence):
            entry = evidence[index]
            if "kind_locator" not in entry:
                raise Occ2FormatError("E%d kind:locator missing" % index)
            if ":" not in entry["kind_locator"]:
                raise Occ2FormatError("E%d must be <kind>:<locator>" % index)
            kind, locator = entry["kind_locator"].split(":", 1)
            out.evidence.append(
                SYM.EvidenceRef(
                    id="E%d" % index,
                    kind=kind,
                    locator=locator,
                    line_range=entry.get("line_range"),
                    digest=entry.get("digest"),
                    revision=entry.get("revision"),
                    verified=entry.get("verified"),
                )
            )
        for index in sorted(artifacts):
            out.artifacts.append(artifacts[index])
        if verification:
            for required in ("claim", "status", "method"):
                name = {"claim": "claim", "status": "status", "method": "method"}[required]
                if name not in verification:
                    raise Occ2FormatError(
                        "verification fields must appear as a complete group (%s missing)"
                        % name
                    )
            out.verification = SYM.Verification(
                claim=verification["claim"],
                status=verification["status"],
                method=verification["method"],
                receipt=verification.get("receipt"),
            )
        for index in sorted(dependencies):
            out.dependencies.append(dependencies[index])
        out.next_actions = list(next_actions)
        self._validate(out)
        return out

    @staticmethod
    def _validate(message):
        """The contract-level checks occ2.rs finish() enforces."""
        if message.kind not in _KINDS:
            raise Occ2FormatError("unknown message kind %r" % message.kind)
        if message.status not in _STATUSES:
            raise Occ2FormatError("unknown task status %r" % message.status)
        if message.kind == "EVIDENCE" and not message.evidence:
            raise Occ2FormatError("an EVIDENCE message must carry evidence references")
        for finding in message.findings:
            if finding.confidence is not None and not 0.0 <= finding.confidence <= 1.0:
                raise Occ2FormatError("confidence %r is outside [0, 1]" % finding.confidence)
        for item in message.evidence:
            if item.kind not in _EVIDENCE_KINDS:
                raise Occ2FormatError("unknown evidence kind %r" % item.kind)
        if message.verification is not None and message.verification.status not in (
            "pass", "fail", "unknown"
        ):
            raise Occ2FormatError("unknown verification status %r" % message.verification.status)
        for stamp in (message.timestamp, message.expiry):
            if stamp is not None and not _RFC3339_RE.match(stamp):
                raise Occ2FormatError("timestamp %r is not RFC 3339" % stamp)

    # -- driver helpers ----------------------------------------------------

    def parse_speaker_output(self, text):
        """(ok, wire, reason, codebook_ok). The reply is either the codebook
        followed by a blank line and the wire, or the bare wire; commentary
        or a paraphrased preamble is a protocol fault."""
        if text is None:
            return False, None, "no reply", None
        stripped = text.rstrip()
        if not stripped:
            return False, None, "empty reply", None
        if stripped.startswith("```"):
            return False, None, "fenced reply", None
        lines = stripped.split("\n")
        header = self.wire_header_hint()
        wire_start = None
        for index, line in enumerate(lines):
            if line == header:
                wire_start = index
                break
        if wire_start is None:
            return False, None, "no %s wire header line" % self.dialect_id, None
        prefix = [line for line in lines[:wire_start]]
        while prefix and not prefix[-1]:
            prefix.pop()
        wire = "\n".join(lines[wire_start:])
        if not prefix:
            return True, wire, None, False
        if "\n".join(prefix) == self.codebook_text():
            return True, wire, None, True
        return False, None, "unexpected preamble before the wire header", None

    def decode_wire_payload(self, wire):
        return self.decode(wire).to_payload()


# ---------------------------------------------------------------------------
# sym-v2-d2-r1: the promoted DIALECT/2 winner through the research codec
# ---------------------------------------------------------------------------

DIALECT2_CARD_PATH = os.path.join(
    _ROOT, "research", "symbolic_comm2", "dialect2-card.json"
)


class Dialect2EvalCodec:
    """The promoted DIALECT/2 winner (`sym-v2-d2-r1`) via dialect2.py."""

    dialect_id = "sym-v2-d2-r1"
    version = 1

    def __init__(self, card_path=DIALECT2_CARD_PATH):
        import dialect2 as D2  # research codec (offline)

        self._card = D2.Dialect2Card.load(card_path)
        self._D2 = D2
        self._wire_header = self._card.wire_header
        self._codebook = self._render_codebook()

    def codebook_sha_prefix(self):
        return self._card.checksum()[:CODEBOOK_PREFIX_LEN]

    def wire_header_hint(self):
        return self._wire_header

    def _render_codebook(self):
        """The wire spec, rendered programmatically from the card only."""
        card = self._card
        rows = []
        rows.append(
            "OCC-2 %s@%d codebook (sha256:%s): wire lines are KEY=VALUE under "
            "the one-line header %r; emit the lines in the canonical order "
            "stated below."
            % (self.dialect_id, self.version, self.codebook_sha_prefix(), card.wire_header)
        )
        rows.append(
            "keys: "
            + " ".join("%s=%s" % (alias, key) for key, alias in sorted(card.key_aliases.items(), key=lambda kv: kv[1]))
            + "; families: "
            + " ".join(
                "%s<n>=%s<n>" % (alias, family)
                for family, alias in sorted(card.family_aliases.items(), key=lambda kv: kv[1])
            )
            + "."
        )
        rows.append(
            "line order: after the header emit the family lines first, in "
            "packing order - evidence e<n> (packed or unpacked), finding "
            "links v<n>, next actions n, dependencies u, finding "
            "confidences c<n>; then the $= line (or, when the merge does not "
            "apply, the separate m/t/x/w lines in that position); then the "
            "remaining lines in contract order - p, f, r, k, s, q, i<n>, any "
            "unpacked d/g/l/y lines, artifacts h<n>, verification vc/vs/vm/"
            "vr, expiry xp. Emit exactly this order: decoding verifies it."
        )
        symbol_rows = []
        for domain in sorted(card.value_symbols):
            table = card.value_symbols[domain]
            symbol_rows.append(
                "%s: %s" % (
                    domain,
                    " ".join("%s=%s" % (symbol, value) for value, symbol in sorted(table.items(), key=lambda kv: kv[1])),
                )
            )
        rows.append("value symbols " + "; ".join(symbol_rows) + ".")
        if card.field_defaults:
            rows.append(
                "elided fields equal defaults: "
                + ", ".join(
                    "%s=%s" % (key, value)
                    for key, value in sorted(card.field_defaults.items())
                )
                + "."
            )
        rows.append(
            "findings i1..in and evidence e1..en are positional; evidence "
            "values are <kind>:<locator> with sigil-packed tails @<lines>, "
            "#<digest>, ~<revision>, !<verified>; evidence links on packed "
            "v<n>= lines are bare 1-based indices, comma-separated "
            "(v1=1,2 means E1 and E2 - never v1=e1,e2)."
        )
        if card.packings:
            rows.append("packings: " + ", ".join(card.packings) + ".")
        rows.append(
            "next actions join on one n= line and dependencies on one u= "
            "line, values separated by |; artifacts h<n> are one per line."
        )
        if card.value_encodings:
            enc = []
            for field in sorted(card.value_encodings):
                enc.append("%s=%s" % (field, card.value_encodings[field]))
            rows.append("value encodings: " + ", ".join(enc) + ".")
            if card.value_encodings.get("TS") == "epoch36" or card.value_encodings.get("EXP") == "epoch36":
                rows.append(
                    "epoch36 fields carry base36 Unix epoch seconds and decode "
                    "to RFC 3339 Z timestamps."
                )
            if any(v == "b36" for v in card.value_encodings.values()):
                rows.append(
                    "b36 fields carry base36 recodings: digests of 64 hex "
                    "chars, revisions of 9 hex chars, confidences of 3 digits."
                )
        if card.scalar_pack:
            keys = [card.key_aliases[k] for k in ("MSGID", "TASK", "TRACE", "TS")]
            rows.append(
                "the %s lines merge onto one $= line as $=<msgid> <task> "
                "<trace> <ts>, space-joined." % "/".join(keys)
            )
        if card.microtemplates:
            rules = []
            for template in card.microtemplates:
                if getattr(template, "type", None) == "prefix":
                    rules.append("%s %r->%r" % (template.field, template.match, template.emit))
            if rules:
                rows.append("prefix substitutions: " + "; ".join(rules) + ".")
        rows.append(
            "never-compress kinds (%s) and verification-fail messages keep "
            "every field: elision is off (defaults are emitted as-is), "
            "prefix substitutions are off (values stay fully literal), and "
            "the b36 recodings are off (digests, revisions, and confidences "
            "stay in their original form); the symbol alphabet still applies "
            "(k=A, s=1, evidence kind f:), packing and the canonical line "
            "order still apply, and TS/EXP still travel epoch36."
            % ", ".join(card.never_compress_kinds)
        )
        return "\n".join(rows)

    def codebook_text(self):
        return self._codebook

    def encode(self, message):
        return self._card.encode(message)

    def decode(self, wire):
        return self._card.decode(wire)

    def parse_speaker_output(self, text):
        if text is None:
            return False, None, "no reply", None
        stripped = text.rstrip()
        if not stripped:
            return False, None, "empty reply", None
        if stripped.startswith("```"):
            return False, None, "fenced reply", None
        lines = stripped.split("\n")
        header = self._wire_header
        wire_start = None
        for index, line in enumerate(lines):
            if line == header:
                wire_start = index
                break
        if wire_start is None:
            return False, None, "no %r wire header line" % header, None
        prefix = [line for line in lines[:wire_start]]
        while prefix and not prefix[-1]:
            prefix.pop()
        wire = "\n".join(lines[wire_start:])
        if not prefix:
            return True, wire, None, False
        if "\n".join(prefix) == self.codebook_text():
            return True, wire, None, True
        return False, None, "unexpected preamble before the wire header", None

    def decode_wire_payload(self, wire):
        return self.decode(wire).to_payload()


_CODECS = {}


def get_codec(dialect):
    """Factory: 'sym-v1' or 'sym-v2-d2-r1' -> codec instance (cached)."""
    if dialect not in _CODECS:
        if dialect == "sym-v1":
            _CODECS[dialect] = SymV1Codec()
        elif dialect == "sym-v2-d2-r1":
            _CODECS[dialect] = Dialect2EvalCodec()
        else:
            raise SystemExit("unknown dialect %r" % dialect)
    return _CODECS[dialect]
