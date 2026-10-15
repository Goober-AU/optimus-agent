"""OCC-1 codec reference tests (Python-side golden parity with the Rust codec).

TEST-LOCAL REFERENCE IMPLEMENTATION -- NOT WIRED INTO THE RUNTIME YET.

This module contains a minimal, pure-stdlib Python re-implementation of the
OCC-1 line grammar defined by the Rust codec in
crates/pi-coding-agent/src/core/occ/occ1.rs (see also the occ1 section of
crates/pi-coding-agent/src/core/occ/mod.rs). It exists so Python-side
agents (the REPL kernel) can later parse OCC-1 text without reimplementing
the grammar, and so the grammar stays pinned by tests on both sides. When
kernel integration lands, lift the reference classes below into a runtime
module and import them here; until then nothing in prime-agent-runtime
imports this parser, and no runtime file references it.

Grammar mirrored exactly from occ1.rs:
- Line 1 is exactly "OCC/1".
- Every following line is KEY=VALUE with KEY = [A-Z][A-Z0-9_]*.
- Repeated keys are allowed and order-preserving (e.g. NEXT=...).
- Values are UTF-8 and contain neither newlines nor C0/C1 control chars.
- Decode tolerates CRLF line endings and at most one trailing newline;
  interior blank lines are errors; whitespace-only lines fail the KEY=VALUE
  grammar (MissingSeparator, not BlankLine -- documented mismatch, pinned
  here to match the implementation).
- Bounds: 128 fields, 32-byte keys, 4096-byte values, 64 KiB messages.
  All length checks count UTF-8 bytes, not characters.

Additionally mirrored from the contract layer (contract.rs indexed_count,
BUG-001 regression): indexed field families E<n> and F<n> must be present
contiguously from 1 up to the highest seen index. F1+F3 without F2 is
corruption, not truncation, and must raise. from_occ1 checks the E family
before the F family; the reference preserves that order.

Out of scope (kept minimal on purpose): kind/status tables, per-kind
dialect required-field rules, and the full optimus.agent-comm.v1 contract
mapping. See contract.rs / codec_tests.rs for those.

Golden cases below include the exact strings from the Rust codec unit
tests in occ1.rs and codec_tests.rs.
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


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class GoldenDecodeTests(unittest.TestCase):
    """Exact strings from the Rust codec unit tests (occ1.rs, codec_tests.rs)."""

    def test_occ1_alone_is_valid_and_empty(self):
        decoded = Occ1Message.decode("OCC/1")
        self.assertTrue(decoded.is_empty())
        self.assertEqual(len(decoded), 0)
        self.assertEqual(decoded, Occ1Message.new())

    def test_header_only_variants_with_trailing_newlines(self):
        for text in ("OCC/1\n", "OCC/1\r\n"):
            decoded = Occ1Message.decode(text)
            self.assertTrue(decoded.is_empty(), repr(text))
            self.assertEqual(decoded, Occ1Message.new(), repr(text))

    def test_single_field_task_t42_round_trip(self):
        message = Occ1Message.new().with_field("TASK", "t42")
        encoded = message.encode()
        self.assertEqual(encoded, "OCC/1\nTASK=t42")
        self.assertEqual(Occ1Message.decode(encoded), message)
        decoded = Occ1Message.decode("OCC/1\nTASK=t42")
        self.assertEqual(decoded.get("TASK"), "t42")
        self.assertEqual(len(decoded), 1)
        self.assertFalse(decoded.is_empty())

    def test_lowercase_occ1_header_rejected(self):
        with self.assertRaises(BadHeader) as caught:
            Occ1Message.decode("occ/1")
        self.assertEqual(caught.exception.got, "occ/1")
        self.assertEqual(caught.exception.expected, "OCC/1")
        with self.assertRaises(BadHeader) as caught:
            Occ1Message.decode("occ/1\nA=1")
        self.assertEqual(caught.exception.got, "occ/1")

    def test_lowercase_key_task_rejected(self):
        with self.assertRaises(InvalidKey) as caught:
            Occ1Message.decode("OCC/1\ntask=t42")
        self.assertEqual(caught.exception.line, 2)
        self.assertEqual(caught.exception.key, "task")

    def test_missing_separator_task(self):
        with self.assertRaises(MissingSeparator) as caught:
            Occ1Message.decode("OCC/1\nTASK")
        self.assertEqual(caught.exception.line, 2)
        # The Rust golden variant with a space payload.
        with self.assertRaises(MissingSeparator) as caught:
            Occ1Message.decode("OCC/1\nTASK t42")
        self.assertEqual(caught.exception.line, 2)
        with self.assertRaises(MissingSeparator) as caught:
            Occ1Message.decode("OCC/1\nA=1\nJUSTKEY")
        self.assertEqual(caught.exception.line, 3)

    def test_blank_interior_line_rejected(self):
        with self.assertRaises(BlankLine) as caught:
            Occ1Message.decode("OCC/1\nTASK=t42\n\nKIND=RESULT")
        self.assertEqual(caught.exception.line, 3)

    def test_crlf_endings_tolerated_and_normalized(self):
        text = "OCC/1\r\nTASK=t42\r\nKIND=RESULT\r\n"
        decoded = Occ1Message.decode(text)
        self.assertEqual(decoded.get("TASK"), "t42")
        self.assertEqual(decoded.get("KIND"), "RESULT")
        # Re-encoding normalizes to LF, like the Rust codec.
        self.assertEqual(decoded.encode(), "OCC/1\nTASK=t42\nKIND=RESULT")

    def test_trailing_lone_cr_stripped_as_line_ending(self):
        decoded = Occ1Message.decode("OCC/1\r\nA=1\r\n")
        self.assertEqual(decoded.get("A"), "1")
        decoded = Occ1Message.decode("OCC/1\nA=1\r")
        self.assertEqual(decoded.get("A"), "1")

    def test_repeated_next_keys_preserve_order(self):
        message = (
            Occ1Message.new()
            .with_field("NEXT", "first")
            .with_field("TASK", "t1")
            .with_field("NEXT", "second")
            .with_field("NEXT", "third")
        )
        encoded = message.encode()
        decoded = Occ1Message.decode(encoded)
        self.assertEqual(decoded, message)
        self.assertEqual(decoded.get_all("NEXT"), ["first", "second", "third"])
        self.assertEqual(decoded.get("NEXT"), "first")
        self.assertEqual(len(decoded), 4)


class HeaderRuleTests(unittest.TestCase):
    def test_empty_input_rejected(self):
        with self.assertRaises(Empty):
            Occ1Message.decode("")

    def test_lone_newline_is_bad_header(self):
        # A lone newline is not "empty": its only stripped line is a bad header.
        with self.assertRaises(BadHeader) as caught:
            Occ1Message.decode("\n")
        self.assertEqual(caught.exception.got, "")
        self.assertEqual(caught.exception.expected, "OCC/1")

    def test_missing_header_rejected(self):
        with self.assertRaises(BadHeader) as caught:
            Occ1Message.decode("TASK=t42\nKIND=RESULT")
        self.assertEqual(caught.exception.got, "TASK=t42")

    def test_wrong_header_variants_rejected(self):
        for wrong in ("OCC/2", "occ/1", "OCC/1 ", " OCC/1", "OCC/1.0", "OCC1", "XOCC/1"):
            text = wrong + "\nA=1"
            with self.assertRaises(BadHeader) as caught:
                Occ1Message.decode(text)
            self.assertEqual(caught.exception.got, wrong, repr(text))

    def test_lone_cr_is_not_a_line_ending(self):
        # CR without LF is not a line ending: the whole text is one line and
        # the header comparison fails with the whole text as the offender.
        for text in ("OCC/1\rTASK=t42", "OCC/1\rKIND=RESULT\rSTATUS=done"):
            with self.assertRaises(BadHeader) as caught:
                Occ1Message.decode(text)
            self.assertEqual(caught.exception.got, text)


class KeyGrammarTests(unittest.TestCase):
    def test_key_grammar_violations_rejected(self):
        for bad_key in ("1AB", "A-B", "A B", "\u00c4", "A.B", "_x"):
            text = "OCC/1\n" + bad_key + "=x"
            with self.assertRaises(InvalidKey) as caught:
                Occ1Message.decode(text)
            self.assertEqual(caught.exception.line, 2, repr(text))
            self.assertEqual(caught.exception.key, bad_key, repr(text))

    def test_empty_key_rejected(self):
        with self.assertRaises(InvalidKey) as caught:
            Occ1Message.decode("OCC/1\n=orphan")
        self.assertEqual(caught.exception.line, 2)
        self.assertEqual(caught.exception.key, "")

    def test_max_length_key_round_trips(self):
        max_key = "K" * OCC1_MAX_KEY_BYTES
        message = Occ1Message.new().with_field(max_key, "v")
        decoded = Occ1Message.decode(message.encode())
        self.assertEqual(decoded, message)
        self.assertEqual(decoded.get(max_key), "v")

    def test_oversized_key_rejected_on_encode_and_decode(self):
        oversized_key = "K" * (OCC1_MAX_KEY_BYTES + 1)
        message = Occ1Message.new().with_field(oversized_key, "v")
        with self.assertRaises(KeyTooLarge) as caught:
            message.encode()
        self.assertEqual(caught.exception.key, oversized_key)
        self.assertEqual(caught.exception.limit, OCC1_MAX_KEY_BYTES)
        with self.assertRaises(KeyTooLarge) as caught:
            Occ1Message.decode("OCC/1\n" + oversized_key + "=v")
        self.assertEqual(caught.exception.key, oversized_key)
        self.assertEqual(caught.exception.limit, OCC1_MAX_KEY_BYTES)


class ValueRuleTests(unittest.TestCase):
    def test_special_characters_round_trip(self):
        message = (
            Occ1Message.new()
            .with_field("EQ", "=x")
            .with_field("COLON", "a=b:c")
            .with_field("QUOTED", "\"double\" 'single'")
            .with_field("SPACED", "  padded  value  ")
        )
        decoded = Occ1Message.decode(message.encode())
        self.assertEqual(decoded, message)
        self.assertEqual(decoded.get("EQ"), "=x")
        self.assertEqual(decoded.get("COLON"), "a=b:c")
        self.assertEqual(decoded.get("QUOTED"), "\"double\" 'single'")
        self.assertEqual(decoded.get("SPACED"), "  padded  value  ")

    def test_unicode_values_round_trip(self):
        for value in (
            "\u914d\u8ee2\u8ff0 \u9806\u5e8f\u3092\u4fdd\u3064",
            "emoji \U0001f600\U0001f680 in values is legal",
            "mixed \u914d \U0001f600 ascii",
            "\U0001f512\U0001f512\U0001f512",
        ):
            message = Occ1Message.new().with_field("U", value)
            decoded = Occ1Message.decode(message.encode())
            self.assertEqual(decoded, message, repr(value))
            self.assertEqual(decoded.get("U"), value)

    def test_empty_and_whitespace_values_round_trip(self):
        message = (
            Occ1Message.new()
            .with_field("EMPTY", "")
            .with_field("SPACES", "   ")
            .with_field("TRAILING", "x  ")
        )
        self.assertEqual(message.encode(), "OCC/1\nEMPTY=\nSPACES=   \nTRAILING=x  ")
        decoded = Occ1Message.decode(message.encode())
        self.assertEqual(decoded, message)
        self.assertEqual(decoded.get("EMPTY"), "")
        self.assertEqual(decoded.get("SPACES"), "   ")
        self.assertEqual(decoded.get("TRAILING"), "x  ")

    def test_control_characters_rejected(self):
        cases = [
            ("OCC/1\nA=x\ty", 2),
            ("OCC/1\nA=x\ry", 2),
            ("OCC/1\nA=x\u0001y", 2),
            ("OCC/1\nA=x\u001fy", 2),
            ("OCC/1\nA=x\u007fy", 2),
            ("OCC/1\nA=x\u0085y", 2),
            ("OCC/1\nA=x\u009by", 2),
            ("OCC/1\nB=1\nA=x\ty", 3),
        ]
        for text, line in cases:
            with self.assertRaises(ControlChar) as caught:
                Occ1Message.decode(text)
            self.assertEqual(caught.exception.line, line, repr(text))
            self.assertEqual(caught.exception.key, "A", repr(text))
        # encode() has no line context, so it reports line 0.
        with self.assertRaises(ControlChar) as caught:
            Occ1Message.new().with_field("A", "x\ty").encode()
        self.assertEqual(caught.exception.line, 0)
        self.assertEqual(caught.exception.key, "A")

    def test_max_length_value_round_trips(self):
        ascii_value = "x" * OCC1_MAX_VALUE_BYTES
        cjk_value = "\u914d" * 1365 + "x"  # 1365 * 3 + 1 = 4096 bytes
        self.assertEqual(_byte_len(cjk_value), OCC1_MAX_VALUE_BYTES)
        message = Occ1Message.new().with_field("A", ascii_value)
        message.push("B", cjk_value)
        decoded = Occ1Message.decode(message.encode())
        self.assertEqual(decoded, message)
        self.assertEqual(decoded.get("A"), ascii_value)
        self.assertEqual(decoded.get("B"), cjk_value)

    def test_oversized_value_rejected_on_encode_and_decode(self):
        oversized = "x" * (OCC1_MAX_VALUE_BYTES + 1)
        message = Occ1Message.new().with_field("A", oversized)
        with self.assertRaises(ValueTooLarge) as caught:
            message.encode()
        self.assertEqual(caught.exception.key, "A")
        self.assertEqual(caught.exception.limit, OCC1_MAX_VALUE_BYTES)
        with self.assertRaises(ValueTooLarge) as caught:
            Occ1Message.decode("OCC/1\nA=" + oversized)
        self.assertEqual(caught.exception.key, "A")
        self.assertEqual(caught.exception.limit, OCC1_MAX_VALUE_BYTES)


class BoundsTests(unittest.TestCase):
    def test_max_field_count_round_trips(self):
        message = Occ1Message.new()
        for i in range(OCC1_MAX_FIELDS):
            message.push("A", "v{0}".format(i))
        self.assertEqual(len(message), OCC1_MAX_FIELDS)
        self.assertEqual(Occ1Message.decode(message.encode()), message)

    def test_too_many_fields_rejected_on_encode(self):
        message = Occ1Message.new()
        for _ in range(OCC1_MAX_FIELDS + 1):
            message.push("A", "v")
        with self.assertRaises(TooManyFields) as caught:
            message.encode()
        self.assertEqual(caught.exception.limit, OCC1_MAX_FIELDS)

    def test_too_many_fields_rejected_on_decode(self):
        # The 129th field line (wire line 130) trips the bound.
        text = "OCC/1" + "\nA=1" * (OCC1_MAX_FIELDS + 1)
        with self.assertRaises(TooManyFields) as caught:
            Occ1Message.decode(text)
        self.assertEqual(caught.exception.limit, OCC1_MAX_FIELDS)

    def test_max_message_size_round_trips(self):
        # 5 header bytes + 15 fields of 4100 bytes + one field of 4031
        # bytes = 65536 bytes exactly.
        message = Occ1Message.new()
        for _ in range(15):
            message.push("AA", "x" * OCC1_MAX_VALUE_BYTES)
        message.push("AA", "x" * 4027)
        encoded = message.encode()
        self.assertEqual(_byte_len(encoded), OCC1_MAX_MESSAGE_BYTES)
        self.assertEqual(Occ1Message.decode(encoded), message)

    def test_oversized_message_rejected_on_encode_and_decode(self):
        message = Occ1Message.new()
        for _ in range(15):
            message.push("AA", "x" * OCC1_MAX_VALUE_BYTES)
        message.push("AA", "x" * 4028)  # 65537 bytes when encoded
        with self.assertRaises(TooLarge) as caught:
            message.encode()
        self.assertEqual(caught.exception.limit, OCC1_MAX_MESSAGE_BYTES)
        text = "OCC/1"
        for _ in range(15):
            text += "\nAA=" + "x" * OCC1_MAX_VALUE_BYTES
        text += "\nAA=" + "x" * 4028
        self.assertEqual(_byte_len(text), OCC1_MAX_MESSAGE_BYTES + 1)
        with self.assertRaises(TooLarge) as caught:
            Occ1Message.decode(text)
        self.assertEqual(caught.exception.limit, OCC1_MAX_MESSAGE_BYTES)

    def test_whitespace_only_interior_line_is_missing_separator(self):
        # The BlankLine doc mentions whitespace-only lines, but the
        # implementation only flags truly empty lines; whitespace-only
        # lines fail the KEY=VALUE grammar instead. Pinned to match.
        with self.assertRaises(MissingSeparator) as caught:
            Occ1Message.decode("OCC/1\nA=1\n   \nB=2")
        self.assertEqual(caught.exception.line, 3)

    def test_two_trailing_newlines_rejected(self):
        # At most one trailing newline is tolerated; a second becomes an
        # interior blank line.
        with self.assertRaises(BlankLine) as caught:
            Occ1Message.decode("OCC/1\nTASK=t42\n\n")
        self.assertEqual(caught.exception.line, 3)
        with self.assertRaises(BlankLine) as caught:
            Occ1Message.decode("OCC/1\n\n")
        self.assertEqual(caught.exception.line, 2)

    def test_cr_only_interior_line_is_blank(self):
        with self.assertRaises(BlankLine) as caught:
            Occ1Message.decode("OCC/1\nA=1\r\n\r\nB=2")
        self.assertEqual(caught.exception.line, 3)


class IndexGapTests(unittest.TestCase):
    """Contract-level indexed-family gap rule (BUG-001 regression)."""

    @staticmethod
    def _result_message():
        return Occ1Message.new().with_field("TASK", "t42").with_field(
            "KIND", "RESULT"
        ).with_field("STATUS", "done")

    def test_contiguous_indices_pass(self):
        message = self._result_message()
        message.push("F1", "first finding")
        message.push("F2", "second finding")
        message.push("F3", "third finding")
        message.push("E1", "src:src/a.rs")
        message.push("E2", "src:src/b.rs")
        self.assertEqual(validate_index_gaps(message), (2, 3))

    def test_f_index_gap_rejected(self):
        # F1 + F3 without F2 is corruption, not truncation.
        message = self._result_message()
        message.push("F1", "first finding")
        message.push("F3", "third finding")
        with self.assertRaises(Occ1ContractError) as caught:
            validate_index_gaps(message)
        self.assertEqual(
            str(caught.exception),
            "F index 2 missing below the highest index 3",
        )

    def test_lone_f3_is_a_gap_at_index_1(self):
        message = self._result_message()
        message.push("F3", "third finding")
        with self.assertRaises(Occ1ContractError) as caught:
            validate_index_gaps(message)
        self.assertEqual(
            str(caught.exception),
            "F index 1 missing below the highest index 3",
        )

    def test_e_index_gap_rejected(self):
        message = self._result_message()
        message.push("E1", "src:src/a.rs")
        message.push("E3", "src:src/c.rs")
        with self.assertRaises(Occ1ContractError) as caught:
            validate_index_gaps(message)
        self.assertEqual(
            str(caught.exception),
            "E index 2 missing below the highest index 3",
        )

    def test_e_family_checked_before_f_family(self):
        # from_occ1 validates E before F; both broken reports the E error.
        message = self._result_message()
        message.push("E1", "src:src/a.rs")
        message.push("E3", "src:src/c.rs")
        message.push("F1", "first finding")
        message.push("F3", "third finding")
        with self.assertRaises(Occ1ContractError) as caught:
            validate_index_gaps(message)
        self.assertTrue(str(caught.exception).startswith("E index"), str(caught.exception))

    def test_non_indexed_suffixed_keys_are_ignored(self):
        # Keys like FC1 (confidence) or FE1 (evidence links) share the F
        # prefix but do not parse as plain F<n>; they must not count as
        # findings and must not create phantom gaps.
        message = self._result_message()
        message.push("F1", "first finding")
        message.push("FC1", "0.9")
        message.push("FE1", "E1")
        message.push("E1", "src:src/a.rs")
        self.assertEqual(validate_index_gaps(message), (1, 1))


class MessageApiTests(unittest.TestCase):
    def test_set_replaces_all_occurrences(self):
        message = Occ1Message.new()
        message.push("NEXT", "a")
        message.push("OTHER", "x")
        message.push("NEXT", "b")
        message.set("NEXT", "c")
        self.assertEqual(message.get_all("NEXT"), ["c"])
        self.assertEqual(message.get("OTHER"), "x")
        self.assertEqual(len(message), 2)

    def test_remove_counts_removals(self):
        message = Occ1Message.new()
        message.push("NEXT", "a")
        message.push("NEXT", "b")
        message.push("TASK", "t1")
        self.assertEqual(message.remove("NEXT"), 2)
        self.assertFalse(message.has("NEXT"))
        self.assertEqual(message.get("TASK"), "t1")
        self.assertEqual(message.remove("NEXT"), 0)

    def test_required_missing(self):
        message = Occ1Message.new().with_field("TASK", "t42").with_field("KIND", "RESULT")
        self.assertEqual(message.required_missing(["TASK", "KIND", "STATUS"]), ["STATUS"])
        self.assertEqual(message.required_missing(["TASK"]), [])

    def test_render_readable(self):
        message = (
            Occ1Message.new()
            .with_field("TASK", "t42")
            .with_field("KIND", "RESULT")
            .with_field("STATUS", "done")
            .with_field("NEXT", "inspect_lock")
        )
        self.assertEqual(
            message.render_readable(),
            "OCC-1 message kind=RESULT task=t42 status=done\n  NEXT: inspect_lock",
        )
        empty = Occ1Message.new()
        self.assertEqual(
            empty.render_readable(),
            "OCC-1 message kind=UNKNOWN task=- status=-",
        )

    def test_equality_and_len(self):
        first = Occ1Message.new().with_field("A", "1")
        second = Occ1Message.new().with_field("A", "1")
        third = Occ1Message.new().with_field("A", "1").with_field("B", "2")
        self.assertEqual(first, second)
        self.assertNotEqual(first, third)
        self.assertEqual(len(third), 2)


if __name__ == "__main__":
    unittest.main()
