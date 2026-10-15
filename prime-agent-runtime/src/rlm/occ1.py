"""OCC-1 compact structured English codec (Python runtime side).

This module implements the OCC-1 line grammar defined by the Rust codec in
crates/pi-coding-agent/src/core/occ/occ1.rs (see also the occ1 section of
crates/pi-coding-agent/src/core/occ/mod.rs). It is the kernel-side codec for
Optimus Compact Communication level 1: every agent can parse and encode
OCC-1 task/result messages without reimplementing the grammar.

On by default: `agent_message.send` payloads may be OCC-1 encoded text (the
daemon transport carries text unchanged); the agent-message skill documents
the format, and the grammar is pinned by golden tests on both sides
(prime-agent-runtime/test/test_occ1_codec.py mirrors the Rust unit tests).

Grammar (mirrored exactly from occ1.rs):
- Line 1 is exactly "OCC/1".
- Every following line is KEY=VALUE with KEY = [A-Z][A-Z0-9_]*.
- Repeated keys are allowed and order-preserving (e.g. NEXT=...).
- Values are UTF-8 and contain neither newlines nor C0/C1 control chars.
- Decode tolerates CRLF line endings and at most one trailing newline;
  interior blank lines are errors; whitespace-only lines fail the KEY=VALUE
  grammar (MissingSeparator, not BlankLine -- documented mismatch, pinned
  by tests to match the Rust implementation).
- Bounds: 128 fields, 32-byte keys, 4096-byte values, 64 KiB messages.
  All length checks count UTF-8 bytes, not characters.

Also mirrors the contract-layer index-gap rule (contract.rs indexed_count,
BUG-001 regression): indexed field families E<n> and F<n> must be present
contiguously from 1 up to the highest seen index. F1+F3 without F2 is
corruption, not truncation, and must raise.

Out of scope (kept minimal on purpose): kind/status tables, per-kind
dialect required-field rules, and the full optimus.agent-comm.v1 contract
mapping. See the Rust contract module for those.
"""

import re
import unittest
import unicodedata

# ---------------------------------------------------------------------------
# Reference implementation (test-local; see module docstring)
# ---------------------------------------------------------------------------

#: Exact required first line of every OCC-1 message.
OCC1_HEADER = "OCC/1"
#: Maximum number of KEY=VALUE fields in one message.
OCC1_MAX_FIELDS = 128
#: Maximum length of a field key in bytes.
OCC1_MAX_KEY_BYTES = 32
#: Maximum length of a field value in bytes.
OCC1_MAX_VALUE_BYTES = 4096
#: Maximum encoded size of one message in bytes.
OCC1_MAX_MESSAGE_BYTES = 64 * 1024

_KEY_RE = re.compile(r"[A-Z][A-Z0-9_]*")
_USIZE_MAX = 2**64 - 1


class Occ1Error(Exception):
    """Codec-level parse/encode failure (mirrors occ1::Occ1Error)."""


class Empty(Occ1Error):
    """The input was empty."""

    def __init__(self):
        super().__init__("OCC-1 input is empty")


class BadHeader(Occ1Error):
    """The first line was not the exact OCC/1 header."""

    def __init__(self, got, expected=OCC1_HEADER):
        self.got = got
        self.expected = expected
        super().__init__(
            "first line {got!r} is not the exact header {expected!r}".format(
                got=got, expected=expected
            )
        )


class MissingSeparator(Occ1Error):
    """A non-header line lacked the = separator."""

    def __init__(self, line):
        self.line = line
        super().__init__("line {0}: expected KEY=VALUE, no '=' found".format(line))


class InvalidKey(Occ1Error):
    """A key failed the [A-Z][A-Z0-9_]* grammar (or was empty)."""

    def __init__(self, line, key):
        self.line = line
        self.key = key
        super().__init__(
            "line {0}: key {1!r} is not [A-Z][A-Z0-9_]*".format(line, key)
        )


class ControlChar(Occ1Error):
    """A value contained a forbidden control character."""

    def __init__(self, line, key):
        self.line = line
        self.key = key
        super().__init__(
            "line {0}: value of {1!r} contains a control character".format(line, key)
        )


class BlankLine(Occ1Error):
    """An interior line was empty."""

    def __init__(self, line):
        self.line = line
        super().__init__("line {0}: blank lines are not allowed".format(line))


class TooLarge(Occ1Error):
    """The message exceeded the byte bound."""

    def __init__(self, limit):
        self.limit = limit
        super().__init__("message exceeds the {0} byte bound".format(limit))


class TooManyFields(Occ1Error):
    """The message exceeded the field-count bound."""

    def __init__(self, limit):
        self.limit = limit
        super().__init__("message exceeds the {0} field bound".format(limit))


class ValueTooLarge(Occ1Error):
    """A value exceeded its byte bound."""

    def __init__(self, key, limit):
        self.key = key
        self.limit = limit
        super().__init__("value of {0!r} exceeds {1} bytes".format(key, limit))


class KeyTooLarge(Occ1Error):
    """A key exceeded its byte bound."""

    def __init__(self, key, limit):
        self.key = key
        self.limit = limit
        super().__init__("key {0!r} exceeds {1} bytes".format(key, limit))


class Occ1ContractError(Exception):
    """Contract-level conversion failure (mirrors ContractError::Occ1Conversion)."""


def _is_control(char):
    # Rust char::is_control() is Unicode category Cc (C0 and C1 controls).
    return unicodedata.category(char) == "Cc"


def _byte_len(text):
    return len(text.encode("utf-8"))


def _validate_key(key):
    # Mirrors occ1.rs validate_key(): empty -> InvalidKey, then the byte
    # bound -> KeyTooLarge, then the pattern -> InvalidKey. encode() has no
    # line context and reports line 0, exactly like the Rust mapping.
    if key == "":
        raise InvalidKey(0, key)
    if _byte_len(key) > OCC1_MAX_KEY_BYTES:
        raise KeyTooLarge(key, OCC1_MAX_KEY_BYTES)
    if _KEY_RE.fullmatch(key) is None:
        raise InvalidKey(0, key)


def _parse_usize(text):
    # Mirrors Rust text.parse::<usize>(): ASCII digits only, u64-bounded.
    if not text or not (text.isascii() and text.isdigit()):
        return None
    value = int(text)
    if value > _USIZE_MAX:
        return None
    return value


class Occ1Message:
    """An OCC-1 message: an ordered multiset of KEY=VALUE fields.

    Mirrors occ1::Occ1Message. Ordering is preserved exactly as inserted;
    repeated keys are legal and get()/get_all() return the first/all
    occurrences respectively.
    """

    __slots__ = ("fields",)

    def __init__(self):
        self.fields = []

    @classmethod
    def new(cls):
        return cls()

    def with_field(self, key, value):
        """Builder-style append (Rust names this `with`; that is a keyword here)."""
        self.push(key, value)
        return self

    def push(self, key, value):
        """Appends a field. Duplicate keys are allowed; order is preserved."""
        self.fields.append((key, value))

    def set(self, key, value):
        """Replaces every existing occurrence of key with a single field."""
        self.fields = [pair for pair in self.fields if pair[0] != key]
        self.fields.append((key, value))

    def get(self, key):
        """Returns the first value stored under key, or None."""
        for candidate, value in self.fields:
            if candidate == key:
                return value
        return None

    def get_all(self, key):
        """Returns every value stored under key, in insertion order."""
        return [value for candidate, value in self.fields if candidate == key]

    def has(self, key):
        """True when at least one field with key exists."""
        return any(candidate == key for candidate, _ in self.fields)

    def remove(self, key):
        """Removes every field with key and returns how many were removed."""
        kept = [pair for pair in self.fields if pair[0] != key]
        removed = len(self.fields) - len(kept)
        self.fields = kept
        return removed

    def iter_fields(self):
        """Iterates (key, value) pairs in insertion order."""
        return iter(list(self.fields))

    def __iter__(self):
        return self.iter_fields()

    def is_empty(self):
        """True when the message has no fields (header only)."""
        return not self.fields

    def required_missing(self, required):
        """Returns the subset of required keys that are absent."""
        return [key for key in required if not self.has(key)]

    def render_readable(self):
        """Readable English fallback for consumers that do not understand OCC-1."""
        kind = self.get("KIND")
        task = self.get("TASK")
        status = self.get("STATUS")
        out = "OCC-1 message kind={0} task={1} status={2}".format(
            "UNKNOWN" if kind is None else kind,
            "-" if task is None else task,
            "-" if status is None else status,
        )
        for key, value in self.fields:
            if key in ("KIND", "TASK", "STATUS"):
                continue
            out += "\n  {0}: {1}".format(key, value)
        return out

    def encode(self):
        """Encodes to the wire grammar; raises Occ1Error on any violation."""
        if len(self.fields) > OCC1_MAX_FIELDS:
            raise TooManyFields(OCC1_MAX_FIELDS)
        parts = [OCC1_HEADER]
        for key, value in self.fields:
            _validate_key(key)
            if _byte_len(value) > OCC1_MAX_VALUE_BYTES:
                raise ValueTooLarge(key, OCC1_MAX_VALUE_BYTES)
            if any(_is_control(char) for char in value):
                raise ControlChar(0, key)
            parts.append(key + "=" + value)
        wire = "\n".join(parts)
        if _byte_len(wire) > OCC1_MAX_MESSAGE_BYTES:
            raise TooLarge(OCC1_MAX_MESSAGE_BYTES)
        return wire

    @classmethod
    def decode(cls, text):
        """Parses the wire grammar. Malformed input is rejected, never guessed."""
        if text == "":
            raise Empty()
        if _byte_len(text) > OCC1_MAX_MESSAGE_BYTES:
            raise TooLarge(OCC1_MAX_MESSAGE_BYTES)
        # At most one trailing newline is stripped before splitting.
        trimmed = text[:-1] if text.endswith("\n") else text
        lines = trimmed.split("\n")
        header = lines[0]
        if header.endswith("\r"):
            header = header[:-1]
        if header != OCC1_HEADER:
            raise BadHeader(header, OCC1_HEADER)
        message = cls()
        for offset, raw_line in enumerate(lines[1:]):
            line_no = offset + 2  # header is line 1
            line = raw_line[:-1] if raw_line.endswith("\r") else raw_line
            if line == "":
                raise BlankLine(line_no)
            key, sep, value = line.partition("=")
            if not sep:
                raise MissingSeparator(line_no)
            if key == "":
                raise InvalidKey(line_no, key)
            try:
                _validate_key(key)
            except InvalidKey as error:
                # decode remaps the line-less key error onto the wire line
                # (Rust: validate_key(..).map_err(.. InvalidKey { line_no, .. })).
                raise InvalidKey(line_no, error.key) from None
            if any(_is_control(char) for char in value):
                raise ControlChar(line_no, key)
            if _byte_len(value) > OCC1_MAX_VALUE_BYTES:
                raise ValueTooLarge(key, OCC1_MAX_VALUE_BYTES)
            if len(message.fields) >= OCC1_MAX_FIELDS:
                raise TooManyFields(OCC1_MAX_FIELDS)
            message.fields.append((key, value))
        return message

    def __len__(self):
        return len(self.fields)

    def __eq__(self, other):
        if not isinstance(other, Occ1Message):
            return NotImplemented
        return self.fields == other.fields

    def __repr__(self):
        return "Occ1Message({0!r})".format(self.fields)


def indexed_count(message, prefix):
    """Counts keys PREFIX1..PREFIXn and rejects gaps (mirrors contract.rs).

    When the highest index is N, every index 1..=N must be present. A hole
    (e.g. F1 then F3) is corruption, not truncation, so it raises
    Occ1ContractError instead of silently decoding a prefix.
    """
    highest = 0
    for key, _value in message.iter_fields():
        if not key.startswith(prefix):
            continue
        index = _parse_usize(key[len(prefix):])
        if index is not None and index > highest:
            highest = index
    for index in range(1, highest + 1):
        if not message.has(prefix + str(index)):
            raise Occ1ContractError(
                "{0} index {1} missing below the highest index {2}".format(
                    prefix, index, highest
                )
            )
    return highest


def validate_index_gaps(message):
    """Applies the contract gap rule in from_occ1 order: E family, then F."""
    evidence_count = indexed_count(message, "E")
    finding_count = indexed_count(message, "F")
    return evidence_count, finding_count
