"""DIALECT/2 — token-friendlier evolution of the frozen DIALECT/1 codec.

The v1 winner (research/symbolic_comm/dialect-card.json, promoted as
sym-v1-pt-pt-r1) already beats OCC-1 on wire tokens (0.815 ratio on the
v1 corpus, local Qwen2.5-0.5B tokenizer). This module extends — never
modifies — the frozen v1 implementation with three optional, exactly
reversible card features, discovered by the v2 deterministic evolution:

  wire_header       1-char wire header line (default keeps v1 "D/1").
  value_encodings  per-field value recodings:
      "TS"/"EXP": "epoch36" — the v1 MT6/MT7 digit stamp "YYYYMMDDHHMMSS"
                  is recoded to base36 epoch seconds (6-7 chars).
      "ED":       "b36"     — sha256 hex digests (64 chars) are recoded to
                  base36 (49-50 chars).

Policy: the value encodings apply only to non-never_compress messages,
mirroring the v1 microtemplate policy; the wire header applies always.
Decode is the exact inverse and strict mode proves the fixed point
encode(decode(wire)) == wire, so any ambiguity fails loudly rather than
corrupting silently.

Corpus assumptions (validated by the strict gate on every message):
  - evidence digests are lowercase sha256 hex (64 chars);
  - timestamps are ISO-8601 Z or the MT6 14-digit form;
  - values are ASCII (non-ASCII is reserved for microtemplate emits).

The v1 winner loaded with no v2 features keeps the identical body and
checksum, so promotion of a feature-less card is a no-op.
"""

import calendar
import json
import pathlib
import re
import sys
import time

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "symbolic_comm"))

import dialect as D
from contract import AgentCommMessage, EvidenceRef

_B36_DIGITS = "0123456789abcdefghijklmnopqrstuvwxyz"
_HEX64 = re.compile(r"[0-9a-f]{64}")
_B36_DIGEST = re.compile(r"[0-9a-z]{49,50}")
_TS14 = re.compile(r"[0-9]{14}")
_B36_STAMP = re.compile(r"[0-9a-z]{1,10}")

EPOCH36 = "epoch36"
B36 = "b36"
_ENCODING_FIELDS = ("TS", "EXP", "ED", "ER", "FC")
_ENCODINGS = {"TS": EPOCH36, "EXP": EPOCH36, "ED": B36, "ER": B36, "FC": B36}
_FC_LINE = None  # compiled per-card in _fc_alias()


def to_b36(n):
    """Non-negative int -> lowercase base36 (no leading zeros)."""
    if n < 0:
        raise D.DialectError("negative value for base36: %r" % n)
    out = ""
    while n:
        out = _B36_DIGITS[n % 36] + out
        n //= 36
    return out or "0"


def from_b36(text):
    return int(text, 36)


def _stamp14_to_epoch(digits):
    return calendar.timegm(time.strptime(digits, "%Y%m%d%H%M%S"))


def _epoch_to_stamp14(epoch):
    return time.strftime("%Y%m%d%H%M%S", time.gmtime(epoch))


_ISO8601 = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z")


def _iso_to_epoch(text):
    return calendar.timegm(time.strptime(text, "%Y-%m-%dT%H:%M:%SZ"))


def _epoch_to_iso(epoch):
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(epoch))


def _digest_to_b36(digest):
    if _HEX64.fullmatch(digest):
        return to_b36(int(digest, 16))
    return digest


def _digest_from_b36(text):
    if _B36_DIGEST.fullmatch(text):
        try:
            number = from_b36(text)
            recovered = "%064x" % number
            if to_b36(number) == text:
                return recovered
        except ValueError:
            pass
    return text


_ER_HEX9 = re.compile(r"[0-9a-f]{9}")
_ER_B36 = re.compile(r"[0-9a-z]{7}")


def _revision_to_b36(revision):
    if _ER_HEX9.fullmatch(revision):
        return to_b36(int(revision, 16))
    return revision


def _revision_from_b36(text):
    if _ER_B36.fullmatch(text):
        try:
            number = from_b36(text)
            recovered = "%09x" % number
            if to_b36(number) == text:
                return recovered
        except ValueError:
            pass
    return text


def _message_with_evidence_encodings(message, digest_fn, revision_fn):
    """Copy of message with recodings applied to evidence digests/revisions."""
    items = []
    changed = False
    for ev in message.evidence:
        digest = ev.digest
        revision = ev.revision
        new_digest = digest_fn(digest) if digest is not None else digest
        new_revision = revision_fn(revision) if revision is not None else revision
        if new_digest != digest or new_revision != revision:
            changed = True
            ev = EvidenceRef(id=ev.id, kind=ev.kind, locator=ev.locator,
                             line_range=ev.line_range, digest=new_digest,
                             revision=new_revision, verified=ev.verified)
        items.append(ev)
    if not changed:
        return message
    return AgentCommMessage(
        task_id=message.task_id, kind=message.kind, status=message.status,
        message_id=message.message_id, parent_task_id=message.parent_task_id,
        trace_id=message.trace_id, sender_identity=message.sender_identity,
        recipient_identity=message.recipient_identity, findings=message.findings,
        evidence=items, artifacts=message.artifacts,
        verification=message.verification, dependencies=message.dependencies,
        next_actions=message.next_actions, sequence=message.sequence,
        timestamp=message.timestamp, expiry=message.expiry)


def _message_with_digests(message, transform):
    """Copy of message with transform applied to every evidence digest."""
    items = []
    changed = False
    for ev in message.evidence:
        digest = ev.digest
        new_digest = transform(digest) if digest is not None else digest
        if new_digest != digest:
            changed = True
            ev = EvidenceRef(id=ev.id, kind=ev.kind, locator=ev.locator,
                              line_range=ev.line_range, digest=new_digest,
                              revision=ev.revision, verified=ev.verified)
        items.append(ev)
    if not changed:
        return message
    return AgentCommMessage(
        task_id=message.task_id, kind=message.kind, status=message.status,
        message_id=message.message_id, parent_task_id=message.parent_task_id,
        trace_id=message.trace_id, sender_identity=message.sender_identity,
        recipient_identity=message.recipient_identity, findings=message.findings,
        evidence=items, artifacts=message.artifacts,
        verification=message.verification, dependencies=message.dependencies,
        next_actions=message.next_actions, sequence=message.sequence,
        timestamp=message.timestamp, expiry=message.expiry)


class Dialect2Card(D.DialectCard):
    """A DIALECT/1 card with the optional v2 wire extensions."""

    SCALAR_PACK_FIELDS = ("MSGID", "TASK", "TRACE", "TS")
    SCALAR_PACK_ALIAS = "$"

    def __init__(self, key_aliases, family_aliases, value_symbols,
                 microtemplates, field_defaults, usage_rules="",
                 never_compress_kinds=None, never_compress_verification_fail=True,
                 packings=None, card_id="dialect2", generation=0, lineage=None,
                 wire_header=None, value_encodings=None, scalar_pack=False):
        # set before super().__init__ because it calls self.validate()
        self.wire_header = wire_header or D.DIALECT_HEADER
        self.value_encodings = dict(value_encodings or {})
        self.scalar_pack = bool(scalar_pack)
        D.DialectCard.__init__(
            self, key_aliases=key_aliases, family_aliases=family_aliases,
            value_symbols=value_symbols, microtemplates=microtemplates,
            field_defaults=field_defaults, usage_rules=usage_rules,
            never_compress_kinds=never_compress_kinds,
            never_compress_verification_fail=never_compress_verification_fail,
            packings=packings, card_id=card_id, generation=generation,
            lineage=lineage)

    # -- validation ------------------------------------------------------

    def validate(self):
        D.DialectCard.validate(self)
        if not isinstance(self.wire_header, str) or not self.wire_header:
            raise D.DialectError("wire_header must be a non-empty string")
        if "\n" in self.wire_header or "=" in self.wire_header:
            raise D.DialectError("wire_header %r must not contain newline or =" % self.wire_header)
        for field, encoding in self.value_encodings.items():
            if field not in _ENCODING_FIELDS:
                raise D.DialectError("unsupported value encoding field %r" % field)
            if _ENCODINGS[field] != encoding:
                raise D.DialectError("unsupported value encoding %r for %s" % (encoding, field))
        # A 1-char header must not equal a fixed-key alias: decode dispatches
        # on the whole first line, so equality would be ambiguous only if
        # the header could itself parse as a field line; forbid that.
        if self.wire_header in self._reverse_alias:
            raise D.DialectError("wire_header %r collides with a key alias" % self.wire_header)
        if self.scalar_pack:
            for field in self.SCALAR_PACK_FIELDS:
                if field not in self.key_aliases:
                    raise D.DialectError("scalar_pack needs an alias for %s" % field)
        # Prefix microtemplates decode by "value starts with emit"; two
        # templates of the same family sharing an emit (or with one emit a
        # prefix of the other) can silently misroute a decode for values
        # outside the observed corpus. Forbid both cases outright.
        by_family = {}
        for template in self.microtemplates:
            if template.type != "prefix":
                continue
            by_family.setdefault(template.field, []).append(template.emit)
        for family, emits in by_family.items():
            seen = set()
            for emit in emits:
                if emit in seen:
                    raise D.DialectError(
                        "family %s has two prefix templates with emit %r"
                        % (family, emit))
                seen.add(emit)
            for a in emits:
                for b in emits:
                    if a != b and b.startswith(a):
                        raise D.DialectError(
                            "family %s emit %r is a prefix of %r"
                            % (family, a, b))

    # -- wire helpers ----------------------------------------------------

    def _epoch_aliases(self):
        return {self.key_aliases[field] for field in ("TS", "EXP")
                if self.value_encodings.get(field) == EPOCH36}

    def _fc_alias(self):
        if self.value_encodings.get("FC") != B36:
            return None
        return self.family_aliases.get("FC")

    def _line_encode(self, line, never=False):
        alias, sep, value = line.partition("=")
        if not sep:
            return line
        if alias in self._epoch_aliases():
            # Both the MT6/7 14-digit stamp and the raw ISO form (never-
            # compress messages skip the microtemplates) recode to base36;
            # the inverse always restores ISO, on which the v1 MT6/7 decode
            # rule no-ops, so never-compress messages round-trip too.
            if _TS14.fullmatch(value):
                return "%s=%s" % (alias, to_b36(_stamp14_to_epoch(value)))
            if _ISO8601.fullmatch(value):
                return "%s=%s" % (alias, to_b36(_iso_to_epoch(value)))
        if not never:
            fc = self._fc_alias()
            if fc is not None:
                base, idx = alias, ""
                while base and base[-1].isdigit():
                    idx = base[-1] + idx
                    base = base[:-1]
                if base == fc and idx and value.isdigit() and len(value) == 3:
                    return "%s=%s" % (alias, to_b36(int(value)))
        return line

    def _line_decode(self, line):
        alias, sep, value = line.partition("=")
        if not sep:
            return line
        if alias in self._epoch_aliases():
            if _B36_STAMP.fullmatch(value) and not _TS14.fullmatch(value):
                return "%s=%s" % (alias, _epoch_to_iso(from_b36(value)))
            return line
        fc = self._fc_alias()
        if fc is not None:
            base, idx = alias, ""
            while base and base[-1].isdigit():
                idx = base[-1] + idx
                base = base[:-1]
            if base == fc and idx and _B36_STAMP.fullmatch(value):
                try:
                    number = from_b36(value)
                    recovered = "%03d" % number
                    if to_b36(number) == value and 100 <= number <= 999:
                        return "%s=%s" % (alias, recovered)
                except ValueError:
                    pass
        return line

    # -- encode / decode -------------------------------------------------

    def encode(self, message):
        never = self.is_never_compress(message)
        payload_message = message
        if not never and (self.value_encodings.get("ED") == B36
                          or self.value_encodings.get("ER") == B36):
            payload_message = _message_with_evidence_encodings(
                message,
                _digest_to_b36 if self.value_encodings.get("ED") == B36 else (lambda d: d),
                _revision_to_b36 if self.value_encodings.get("ER") == B36 else (lambda r: r))
        wire = D.DialectCard.encode(self, payload_message)
        lines = wire.split("\n")
        lines[0] = self.wire_header
        lines[1:] = [self._line_encode(line, never) for line in lines[1:]]
        if self.scalar_pack:
            lines = self._scalar_pack_lines(lines)
        return "\n".join(lines)

    def _scalar_pack_lines(self, lines):
        """Merge the MSGID/TASK/TRACE/TS lines into one space-joined line.

        Applied only when all four lines exist and none of the values
        contains a space; otherwise the message keeps separate lines.
        The merged line takes the position of the earliest merged line.
        """
        wanted = {self.key_aliases[field]: field for field in self.SCALAR_PACK_FIELDS}
        found = {}
        for index, line in enumerate(lines):
            alias, sep, value = line.partition("=")
            if sep and alias in wanted and wanted[alias] not in found:
                found[wanted[alias]] = (index, value)
        if len(found) != len(self.SCALAR_PACK_FIELDS):
            return lines
        values = [found[field][1] for field in self.SCALAR_PACK_FIELDS]
        if any(" " in value for value in values):
            return lines
        indexes = [found[field][0] for field in self.SCALAR_PACK_FIELDS]
        merged = self.SCALAR_PACK_ALIAS + "=" + " ".join(values)
        out = [line for i, line in enumerate(lines) if i not in set(indexes)]
        out.insert(min(indexes), merged)
        return out

    def _scalar_unpack_line(self, line):
        """Inverse of _scalar_pack_lines for a single line."""
        parts = line[len(self.SCALAR_PACK_ALIAS) + 1:].split(" ")
        if len(parts) != len(self.SCALAR_PACK_FIELDS):
            raise D.DialectError("scalar pack line %r has %d parts" % (line, len(parts)))
        return ["%s=%s" % (self.key_aliases[field], value)
                for field, value in zip(self.SCALAR_PACK_FIELDS, parts)]

    def decode(self, wire, strict=True):
        if not wire:
            raise D.DialectError("empty dialect input")
        text = wire[:-1] if wire.endswith("\n") else wire
        lines = text.split("\n")
        if lines[0] != self.wire_header:
            raise D.DialectError("first line %r is not the header %r"
                                 % (lines[0], self.wire_header))
        restored = [D.DIALECT_HEADER]
        for raw in lines[1:]:
            if raw.endswith("\r"):
                raw = raw[:-1]
            if raw == "":
                raise D.DialectError("blank line in dialect message")
            if self.scalar_pack and raw.startswith(self.SCALAR_PACK_ALIAS + "="):
                restored.extend(self._line_decode(part)
                                for part in self._scalar_unpack_line(raw))
                continue
            restored.append(self._line_decode(raw))
        message = D.DialectCard.decode(self, "\n".join(restored), strict=False)
        if (self.value_encodings.get("ED") == B36
                or self.value_encodings.get("ER") == B36):
            message = _message_with_evidence_encodings(
                message,
                _digest_from_b36 if self.value_encodings.get("ED") == B36 else (lambda d: d),
                _revision_from_b36 if self.value_encodings.get("ER") == B36 else (lambda r: r))
        if strict:
            if self.encode(message) != text:
                raise D.DialectError(
                    "wire is not a fixed point of encode; ambiguous transform")
        return message

    # -- card serialization ----------------------------------------------

    def body(self):
        out = D.DialectCard.body(self)
        if self.wire_header != D.DIALECT_HEADER:
            out["wire_header"] = self.wire_header
        if self.value_encodings:
            out["value_encodings"] = dict(self.value_encodings)
        if self.scalar_pack:
            out["scalar_pack"] = True
        return out

    @classmethod
    def from_json(cls, payload):
        return cls(
            key_aliases=payload["key_aliases"],
            family_aliases=payload["family_aliases"],
            value_symbols=payload["value_symbols"],
            microtemplates=payload.get("microtemplates", []),
            field_defaults=payload.get("field_defaults", {}),
            usage_rules=payload.get("usage_rules", ""),
            never_compress_kinds=payload.get("never_compress_kinds"),
            never_compress_verification_fail=payload.get(
                "never_compress_verification_fail", True),
            packings=payload.get("packings", []),
            card_id=payload.get("id", "dialect2"),
            generation=payload.get("generation", 0),
            lineage=payload.get("lineage", []),
            wire_header=payload.get("wire_header"),
            value_encodings=payload.get("value_encodings"),
            scalar_pack=payload.get("scalar_pack", False))

    @classmethod
    def load(cls, path):
        return cls.from_json(
            json.loads(pathlib.Path(path).read_text(encoding="utf-8")))

    @classmethod
    def promote(cls, card):
        """Lift a v1 DialectCard into a Dialect2Card (features default off)."""
        return cls.from_json(card.to_json())


if __name__ == "__main__":
    # Smoke: v1 winner round-trips through the v2 class unchanged, and the
    # full-feature variant round-trips with strict fidelity on corpus2.
    import collections
    import json as _json
    v1 = D.DialectCard.load(str(HERE.parent / "symbolic_comm" / "dialect-card.json"))
    v2 = Dialect2Card.promote(v1)
    assert v2.checksum() == v1.checksum(), "feature-less v2 must keep the v1 checksum"
    v2.value_encodings = {"TS": EPOCH36, "EXP": EPOCH36, "ED": B36}
    v2.wire_header = "d"
    v2.validate()
    raw = _json.loads((HERE.parent / "tasks2" / "msgs_corpus2_evolve.json")
                      .read_text(encoding="utf-8"))
    from contract import AgentCommMessage as M
    msgs = [M.from_payload(e["payload"]) for e in raw["messages"]]
    import contract as C
    ok = 0
    for m in msgs:
        wire = v2.encode(m)
        back = v2.decode(wire)
        assert C.equal(m, back), "fidelity failure on %s" % m.message_id
        assert v2.encode(back) == wire, "fixed point failure on %s" % m.message_id
        ok += 1
    print("dialect2 smoke: %d/%d strict round-trips OK; checksum %s"
          % (ok, len(msgs), v2.checksum()[:12]))
