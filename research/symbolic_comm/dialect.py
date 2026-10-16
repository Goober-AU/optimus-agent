"""DIALECT/1: learned symbolic dialect over the optimus.agent-comm.v1 contract.

Implements the candidate grammar from RESEARCH-NOTES.md section 4.1 plus the
packing layer suggested by the occ2-taskbench baseline findings (record
attributes inline, repeated-prefix merging, reference enumeration packing):

- Key aliases: fixed OCC-1 keys and indexed key families map to 1-2 char
  lowercase aliases; the wire keeps the OCC/1-style KEY=VALUE line grammar
  under a `D/1` header so decoders can fall back per field.
- Value symbol tables over the contract's closed enums (kinds, statuses,
  verification, evidence kinds, evidence-verified flags).
- Microtemplates: deterministic per-field value transforms (prefix replace
  or regex fullmatch rewrite), reversible on decode.
- Packings: lossless structural merges of related OCC-1 fields into one line:
    E     evidence record inline: e<n>=<kind>:<locator>@<lines>#<digest>~<revision>!<verified>
    FE    evidence refs "E1,E2" -> "1,2"
    NEXT  repeated NEXT lines -> one n=a|b|c line
    DEP   DEP1..DEPn -> one u=a|b|c line
    ART   ART1..ARTn -> one h=a|b|c line
    FC    confidence "0.900" -> permille "900"
- Field defaults: elide a fixed field when its value equals the default.
- Never-compress classes: kinds in never_compress_kinds and any message with
  verification status `fail` skip microtemplates and defaults (values stay
  verbatim); symbols and packings still apply because they are lossless
  structural transforms. Mirrors the occ::prune never-suppress lesson.
- Deterministic invariants checked before any model call: alias uniqueness,
  closed-enum totality, symbol/alias grammar, no symbol equal to a raw
  domain value, no fixed alias shadowing an indexed family alias.

Decode is strict: unknown alias, unknown symbol, or a non-fixed-point
round-trip raises DialectError instead of guessing.

Wire grammar (envelope): first line exactly "D/1", then KEY=VALUE lines
where KEY is a dialect alias (fixed alias, or family alias + index digits
for un-packed indexed families). Values contain no newlines or C0/C1
control chars, same as OCC-1.

The card serializes to JSON; the functional body carries a sha256 checksum
so deployment can pin a dialect exactly.
"""

import hashlib
import json
import re

from contract import (
    KINDS, STATUSES, VERIFICATION_STATUSES, EVIDENCE_KINDS,
    AgentCommMessage,
)

DIALECT_HEADER = "D/1"

# Fixed OCC-1 keys (non-indexed) that may carry a key alias.
FIXED_KEYS = [
    "MSGID", "TASK", "PARENT", "TRACE", "SENDER", "RECIPIENT",
    "KIND", "STATUS", "SEQ", "V", "VS", "VM", "VR", "NEXT", "TS", "EXP",
]
# Indexed key families (alias + digits on the wire when not packed).
FAMILY_KEYS = ["F", "FC", "FE", "E", "EL", "ED", "ER", "EV", "ART", "DEP"]

# Structural packings: OCC-1 field group -> single wire line.
PACKINGS = ["E", "FE", "NEXT", "DEP", "ART", "FC"]

# Closed enum domains that must be total in every card.
ENUM_DOMAINS = {
    "STATUS": STATUSES,
    "KIND": KINDS,
    "VERIFY": VERIFICATION_STATUSES,
    "EVIDENCE_VERIFIED": ["pass", "fail"],
    "EVIDENCE_KIND": EVIDENCE_KINDS,
}

# Which wire field each enum domain applies to.
_DOMAIN_FIELD = {
    "STATUS": ("fixed", "STATUS"),
    "KIND": ("fixed", "KIND"),
    "VERIFY": ("fixed", "VS"),
    "EVIDENCE_VERIFIED": ("family", "EV"),
    "EVIDENCE_KIND": ("family", "E"),
}

_MT_FIELDS = set(FIXED_KEYS) | set(FAMILY_KEYS) | {"*"}

# Sigils used by the E packing. A locator containing any sigil falls back
# to unpacked fields (deterministic, never ambiguous).
_E_SIGILS = "@#~!"

_ALIAS_RE = re.compile(r"^[a-z][a-z0-9]?$")
_SYMBOL_RE = re.compile(r"^[^\s:=\n]{1,2}$")

DEFAULT_NEVER_COMPRESS_KINDS = ["BLOCKED", "CANCELLED", "CORRECTION", "ACK"]


class DialectError(Exception):
    """Card validation or strict encode/decode failure."""


class Microtemplate:
    """One deterministic value transform.

    type "prefix": encode replaces a leading `match` with `emit`;
    decode replaces a leading `emit` with `match`.
    type "regex": encode fullmatches `match` and rewrites via `emit`
    (backrefs); decode fullmatches `decode_match` and rewrites via
    `decode_emit`. Both directions are explicit for regex rules.
    """

    __slots__ = ("id", "field", "type", "match", "emit", "decode_match", "decode_emit")

    def __init__(self, id, field, type, match, emit, decode_match=None, decode_emit=None):
        self.id = id
        self.field = field
        self.type = type
        self.match = match
        self.emit = emit
        self.decode_match = decode_match
        self.decode_emit = decode_emit

    def to_dict(self):
        out = {"id": self.id, "field": self.field, "type": self.type,
               "match": self.match, "emit": self.emit}
        if self.decode_match is not None:
            out["decode_match"] = self.decode_match
        if self.decode_emit is not None:
            out["decode_emit"] = self.decode_emit
        return out

    @classmethod
    def from_dict(cls, payload):
        return cls(
            id=payload["id"], field=payload["field"], type=payload["type"],
            match=payload["match"], emit=payload["emit"],
            decode_match=payload.get("decode_match"),
            decode_emit=payload.get("decode_emit"),
        )

    def encode_value(self, value):
        if self.type == "prefix":
            if value.startswith(self.match):
                return self.emit + value[len(self.match):], True
            return value, False
        if self.type == "regex":
            if re.fullmatch(self.match, value) is None:
                return value, False
            return re.sub(self.match, self.emit, value), True
        raise DialectError("unknown microtemplate type %r" % self.type)

    def decode_value(self, value):
        if self.type == "prefix":
            if value.startswith(self.emit):
                return self.match + value[len(self.emit):], True
            return value, False
        if self.type == "regex":
            if self.decode_match is None or self.decode_emit is None:
                raise DialectError("regex microtemplate %s lacks decode rule" % self.id)
            if re.fullmatch(self.decode_match, value) is None:
                return value, False
            return re.sub(self.decode_match, self.decode_emit, value), True
        raise DialectError("unknown microtemplate type %r" % self.type)


def _pack_fc(value):
    """'0.900' -> '900'; '1.000' -> '1000'. Injective on the 3-decimal grid."""
    if not re.fullmatch(r"\d\.\d{3}", value):
        return value, False
    permille = int(round(float(value) * 1000))
    return str(permille), True


def _unpack_fc(value):
    if not value.isascii() or not value.isdigit():
        return value, False
    if not (0 <= int(value) <= 1000):
        return value, False
    return "%.3f" % (int(value) / 1000.0), True


def _split_packed_e(value):
    """Splits an E-packed value into its parts. Returns None when the value
    does not follow the packed shape (forces unpacked fallback)."""
    head, sep, rest = value.partition("@")
    parts = {"lines": None, "digest": None, "revision": None, "verified": None}
    if not sep:
        return head, parts
    segments = re.split(r"(?=[#~!])", rest)
    for segment in segments:
        if not segment:
            continue
        if segment[0] == "@":
            return None
        if segment[0] == "#":
            if parts["digest"] is not None:
                return None
            parts["digest"] = segment[1:]
        elif segment[0] == "~":
            if parts["revision"] is not None:
                return None
            parts["revision"] = segment[1:]
        elif segment[0] == "!":
            if parts["verified"] is not None:
                return None
            parts["verified"] = segment[1:]
        else:
            return None
    if not re.fullmatch(r"\d+-\d+", head if "@" not in value else head):
        pass
    return head, parts


class DialectCard:
    """A DIALECT/1 card: deterministic codec over the typed contract."""

    def __init__(self, key_aliases, family_aliases, value_symbols,
                 microtemplates, field_defaults, usage_rules="",
                 never_compress_kinds=None, never_compress_verification_fail=True,
                 packings=None, card_id="dialect", generation=0, lineage=None):
        self.key_aliases = dict(key_aliases)
        self.family_aliases = dict(family_aliases)
        self.value_symbols = {k: dict(v) for k, v in value_symbols.items()}
        self.microtemplates = [m if isinstance(m, Microtemplate) else Microtemplate.from_dict(m)
                               for m in microtemplates]
        self.field_defaults = dict(field_defaults)
        self.usage_rules = usage_rules
        self.never_compress_kinds = list(never_compress_kinds or DEFAULT_NEVER_COMPRESS_KINDS)
        self.never_compress_verification_fail = never_compress_verification_fail
        self.packings = list(packings or [])
        self.card_id = card_id
        self.generation = generation
        self.lineage = list(lineage or [])
        self.validate()

    # -- validation ------------------------------------------------------

    def validate(self):
        for packing in self.packings:
            if packing not in PACKINGS:
                raise DialectError("unknown packing %r" % packing)
        if len(set(self.packings)) != len(self.packings):
            raise DialectError("duplicate packing entries")
        seen_alias = {}
        for key, alias in self.key_aliases.items():
            if key not in FIXED_KEYS:
                raise DialectError("alias target %r is not a fixed OCC-1 key" % key)
            if _ALIAS_RE.fullmatch(alias) is None:
                raise DialectError("alias %r for %s violates [a-z][a-z0-9]?" % (alias, key))
            if alias in seen_alias:
                raise DialectError("alias %r used by both %s and %s"
                                   % (alias, seen_alias[alias], key))
            seen_alias[alias] = key
        for family, alias in self.family_aliases.items():
            if family not in FAMILY_KEYS:
                raise DialectError("family %r is not an indexed OCC-1 family" % family)
            if _ALIAS_RE.fullmatch(alias) is None:
                raise DialectError("family alias %r for %s violates [a-z][a-z0-9]?"
                                   % (alias, family))
            if alias in seen_alias:
                raise DialectError("family alias %r collides with fixed alias of %s"
                                   % (alias, seen_alias[alias]))
            seen_alias[alias] = family
        for family, family_alias in self.family_aliases.items():
            for alias in self.key_aliases:
                if alias.startswith(family_alias) and alias[len(family_alias):].isdigit():
                    raise DialectError(
                        "fixed alias %r shadows family alias %r + index" % (alias, family_alias))
        for domain, values in ENUM_DOMAINS.items():
            table = self.value_symbols.get(domain, {})
            missing = [value for value in values if value not in table]
            if missing:
                raise DialectError("value symbols for %s not total; missing %r"
                                   % (domain, missing))
            symbols = list(table.values())
            if len(set(symbols)) != len(symbols):
                raise DialectError("duplicate symbols in domain %s" % domain)
            for value, symbol in table.items():
                if symbol in values:
                    raise DialectError("symbol %r equals raw value in %s" % (symbol, domain))
                if _SYMBOL_RE.fullmatch(symbol) is None:
                    raise DialectError("symbol %r for %s/%s violates grammar"
                                       % (symbol, domain, value))
        for kind in self.never_compress_kinds:
            if kind not in KINDS:
                raise DialectError("never-compress kind %r is not a known kind" % kind)
        for key in self.field_defaults:
            if key not in FIXED_KEYS:
                raise DialectError("field default on non-fixed key %r" % key)
        ids = set()
        for template in self.microtemplates:
            if template.id in ids:
                raise DialectError("duplicate microtemplate id %r" % template.id)
            ids.add(template.id)
            if template.field not in _MT_FIELDS:
                raise DialectError("microtemplate %s targets unknown field %r"
                                   % (template.id, template.field))
            try:
                if template.type == "regex":
                    re.compile(template.match)
                    if template.decode_match is None or template.decode_emit is None:
                        raise DialectError(
                            "regex microtemplate %s needs decode_match/decode_emit"
                            % template.id)
                    re.compile(template.decode_match)
                elif template.type != "prefix":
                    raise DialectError("unknown microtemplate type %r" % template.type)
            except re.error as error:
                raise DialectError("microtemplate %s regex invalid: %s"
                                   % (template.id, error))
        self._build_lookups()

    def _build_lookups(self):
        self._reverse_alias = {alias: key for key, alias in self.key_aliases.items()}
        self._reverse_family = {alias: family for family, alias in self.family_aliases.items()}
        self._reverse_symbols = {
            domain: {symbol: value for value, symbol in table.items()}
            for domain, table in self.value_symbols.items()
        }

    # -- checksum / serialization ----------------------------------------

    def body(self):
        return {
            "format": "DIALECT/1",
            "key_aliases": self.key_aliases,
            "family_aliases": self.family_aliases,
            "value_symbols": self.value_symbols,
            "microtemplates": [m.to_dict() for m in self.microtemplates],
            "field_defaults": self.field_defaults,
            "packings": self.packings,
            "never_compress_kinds": self.never_compress_kinds,
            "never_compress_verification_fail": self.never_compress_verification_fail,
            "usage_rules": self.usage_rules,
        }

    def checksum(self):
        blob = json.dumps(self.body(), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(blob.encode("utf-8")).hexdigest()

    def to_json(self):
        out = {
            "id": self.card_id,
            "generation": self.generation,
            "lineage": self.lineage,
            "checksum": self.checksum(),
        }
        out.update(self.body())
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
            card_id=payload.get("id", "dialect"),
            generation=payload.get("generation", 0),
            lineage=payload.get("lineage", []),
        )

    def save(self, path):
        import pathlib
        pathlib.Path(path).write_text(json.dumps(self.to_json(), indent=2),
                                      encoding="utf-8")

    @classmethod
    def load(cls, path):
        import pathlib
        return cls.from_json(
            json.loads(pathlib.Path(path).read_text(encoding="utf-8")))

    # -- never-compress test ----------------------------------------------

    def is_never_compress(self, message):
        if message.kind in self.never_compress_kinds:
            return True
        if (self.never_compress_verification_fail and message.verification is not None
                and message.verification.status == "fail"):
            return True
        return False

    # -- value transforms -------------------------------------------------

    def _apply_mts_encode(self, field, value):
        for template in self.microtemplates:
            if template.field not in (field, "*"):
                continue
            new_value, matched = template.encode_value(value)
            if matched:
                return new_value
        return value

    def _apply_mts_decode(self, field, value):
        for template in self.microtemplates:
            if template.field not in (field, "*"):
                continue
            new_value, matched = template.decode_value(value)
            if matched:
                return new_value
        return value

    def _mt_field(self, key):
        """Normalizes an indexed key (ART2, E1, FE3) to its family for
        microtemplate field matching (ART, E, FE)."""
        for family in FAMILY_KEYS:
            if key.startswith(family) and key[len(family):].isdigit():
                return family
        return key

    def _symbolize(self, domain, value):
        return self.value_symbols.get(domain, {}).get(value, value)

    def _unsymbolize(self, domain, symbol):
        table = self._reverse_symbols.get(domain, {})
        if symbol not in table:
            raise DialectError("unknown %s symbol %r" % (domain, symbol))
        return table[symbol]

    # -- packing -----------------------------------------------------------

    def _pack_fields(self, fields):
        """Groups OCC-1 (key, value) pairs into packed wire lines."""
        out = []
        index = {}
        for key, value in fields:
            index.setdefault(key, []).append(value)
        used = set()

        def family_items(prefix):
            items = []
            n = 1
            while "%s%d" % (prefix, n) in index:
                items.append(index["%s%d" % (prefix, n)][0])
                used.add("%s%d" % (prefix, n))
                n += 1
            return items

        if "E" in self.packings:
            for n, base in enumerate(family_items("E"), start=1):
                # base is "<kind>:<locator>"
                if ":" not in base:
                    out.append(("E%d" % n, base))
                    continue
                kind_text, locator = base.split(":", 1)
                if any(sigil in locator for sigil in _E_SIGILS):
                    out.append(("E%d" % n, base))
                    continue
                parts = []
                line = index.get("EL%d" % n, [None])[0]
                if line is not None:
                    used.add("EL%d" % n)
                    parts.append("@" + line)
                digest = index.get("ED%d" % n, [None])[0]
                if digest is not None:
                    if any(sigil in digest for sigil in _E_SIGILS):
                        out.append(("E%d" % n, base))
                        continue
                    used.add("ED%d" % n)
                    parts.append("#" + digest)
                revision = index.get("ER%d" % n, [None])[0]
                if revision is not None:
                    if any(sigil in revision for sigil in _E_SIGILS):
                        out.append(("E%d" % n, base))
                        continue
                    used.add("ER%d" % n)
                    parts.append("~" + revision)
                verified = index.get("EV%d" % n, [None])[0]
                if verified is not None:
                    used.add("EV%d" % n)
                    parts.append("!" + self._symbolize("EVIDENCE_VERIFIED", verified))
                out.append(("E%d" % n, kind_text + ":" + locator + "".join(parts)))
        if "FE" in self.packings:
            n = 1
            while "FE%d" % n in index:
                refs = index["FE%d" % n][0]
                used.add("FE%d" % n)
                stripped = ",".join(
                    ref[1:] if ref.startswith("E") and ref[1:].isdigit() else ref
                    for ref in refs.split(","))
                out.append(("FE%d" % n, stripped))
                n += 1
        if "NEXT" in self.packings and "NEXT" in index:
            used.add("NEXT")
            if any("|" in value for value in index["NEXT"]):
                for value in index["NEXT"]:
                    out.append(("NEXT", value))
            else:
                out.append(("NEXT", "|".join(index["NEXT"])))
        if "DEP" in self.packings:
            items = family_items("DEP")
            if items:
                if any("|" in item for item in items):
                    for n, item in enumerate(items, start=1):
                        out.append(("DEP%d" % n, item))
                else:
                    out.append(("DEP", "|".join(items)))
        if "ART" in self.packings:
            items = family_items("ART")
            if items:
                if any("|" in item for item in items):
                    for n, item in enumerate(items, start=1):
                        out.append(("ART%d" % n, item))
                else:
                    out.append(("ART", "|".join(items)))
        if "FC" in self.packings:
            n = 1
            while "FC%d" % n in index:
                value = index["FC%d" % n][0]
                used.add("FC%d" % n)
                packed, ok = _pack_fc(value)
                out.append(("FC%d" % n, packed if ok else value))
                n += 1
        for key, values in index.items():
            if key in used:
                continue
            for value in values:
                out.append((key, value))
        return out

    def _unpack_fields(self, lines):
        """Expands packed wire lines back into OCC-1 (key, value) pairs."""
        out = []
        for key, value in lines:
            if key.startswith("E") and key[1:].isdigit() and "E" in self.packings:
                if ":" not in value:
                    out.append((key, value))
                    continue
                kind_text, rest = value.split(":", 1)
                if not any(sigil in rest for sigil in _E_SIGILS):
                    out.append((key, value))
                    continue
                n = key[1:]
                match = re.fullmatch(r"(?P<loc>[^@#~!]*)(?P<tail>.*)", rest)
                if match is None:
                    raise DialectError("malformed packed evidence %r" % value)
                out.append((key, kind_text + ":" + match.group("loc")))
                seen_sigils = set()
                for segment in re.split(r"(?=[@#~!])", match.group("tail")):
                    if not segment:
                        continue
                    sigil = segment[0]
                    if sigil in seen_sigils:
                        raise DialectError("duplicate %r segment in %r" % (sigil, value))
                    seen_sigils.add(sigil)
                    if sigil == "@":
                        if not re.fullmatch(r"\d+-\d+", segment[1:]):
                            raise DialectError("bad line range %r" % segment)
                        out.append(("EL" + n, segment[1:]))
                    elif sigil == "#":
                        out.append(("ED" + n, segment[1:]))
                    elif sigil == "~":
                        out.append(("ER" + n, segment[1:]))
                    elif sigil == "!":
                        flag = self._unsymbolize("EVIDENCE_VERIFIED", segment[1:])
                        out.append(("EV" + n, flag))
                continue
            if key.startswith("FE") and key[2:].isdigit() and "FE" in self.packings:
                refs = ",".join(
                    ("E" + ref if ref.isdigit() else ref)
                    for ref in value.split(","))
                out.append((key, refs))
                continue
            if key == "NEXT" and "NEXT" in self.packings:
                if "|" in value:
                    for part in value.split("|"):
                        out.append(("NEXT", part))
                else:
                    out.append((key, value))
                continue
            if key == "DEP" and "DEP" in self.packings:
                for n, part in enumerate(value.split("|"), start=1):
                    out.append(("DEP%d" % n, part))
                continue
            if key == "ART" and "ART" in self.packings:
                for n, part in enumerate(value.split("|"), start=1):
                    out.append(("ART%d" % n, part))
                continue
            if key.startswith("FC") and key[2:].isdigit() and "FC" in self.packings:
                unpacked, ok = _unpack_fc(value)
                out.append((key, unpacked if ok else value))
                continue
            out.append((key, value))
        return out

    # -- encode -----------------------------------------------------------

    def encode(self, message):
        """Contract -> dialect wire text. Deterministic and strict.

        Order: default elision -> evidence-locator microtemplates ->
        packing -> enum symbolization -> key aliasing. Decode runs the
        exact inverse, and strict mode re-encodes to prove the fixed point.
        """
        fields = message.occ1_fields()
        never = self.is_never_compress(message)
        if not never:
            fields = [(key, value) for key, value in fields
                      if self.field_defaults.get(key) != value]
        # Apply evidence-locator microtemplates to the pure locator before
        # packing so the packed tail (@/#/~/!) never passes through an MT.
        # Artifact/dependency/next-action parts get their MTs applied before
        # the multi-value packing joins them.
        prepared = []
        for key, value in fields:
            if key.startswith("E") and key[1:].isdigit() and ":" in value:
                kind_text, locator = value.split(":", 1)
                if not never:
                    locator = self._apply_mts_encode("E", locator)
                    if any(s in locator for s in _E_SIGILS):
                        raise DialectError(
                            "microtemplate produced sigil-bearing locator %r" % locator)
                value = kind_text + ":" + locator
            elif (not never) and (
                (key.startswith("ART") and key[3:].isdigit())
                or (key.startswith("DEP") and key[3:].isdigit())
                or key == "NEXT"):
                family = "ART" if key.startswith("ART") else (
                    "DEP" if key.startswith("DEP") else "NEXT")
                value = self._apply_mts_encode(family, value)
                if "|" in value:
                    raise DialectError(
                        "microtemplate produced '|' in multi-value %r" % value)
            prepared.append((key, value))
        if self.packings:
            prepared = self._pack_fields(prepared)
        lines = [DIALECT_HEADER]
        for key, value in prepared:
            if key.startswith("E") and key[1:].isdigit() and ":" in value:
                kind_text, rest = value.split(":", 1)
                value = self._symbolize("EVIDENCE_KIND", kind_text) + ":" + rest
            elif not never and key not in ("NEXT", "DEP", "ART") and not (
                    key.startswith("FC") and key[2:].isdigit() and "FC" in self.packings):
                value = self._apply_mts_encode(self._mt_field(key), value)
            if key == "STATUS":
                value = self._symbolize("STATUS", value)
            elif key == "KIND":
                value = self._symbolize("KIND", value)
            elif key == "VS":
                value = self._symbolize("VERIFY", value)
            elif key.startswith("EV") and key[2:].isdigit():
                value = self._symbolize("EVIDENCE_VERIFIED", value)
            alias = self._alias_for(key)
            lines.append("%s=%s" % (alias, value))
        return "\n".join(lines)

    def _alias_for(self, key):
        if key in self.key_aliases:
            return self.key_aliases[key]
        # Packed multi-value lines (DEP, ART) reuse the bare family alias.
        if key in self.family_aliases and key in ("DEP", "ART") and key in self.packings:
            return self.family_aliases[key]
        for family in sorted(self.family_aliases, key=len, reverse=True):
            if key.startswith(family) and key[len(family):].isdigit():
                return self.family_aliases[family] + key[len(family):]
        raise DialectError("no alias for OCC-1 key %r" % key)

    # -- decode -----------------------------------------------------------

    def decode(self, wire, strict=True):
        """Dialect wire text -> contract. Strict mode verifies the wire is a
        fixed point of encode (any ambiguity raises DialectError)."""
        if not wire:
            raise DialectError("empty dialect input")
        text = wire[:-1] if wire.endswith("\n") else wire
        lines = text.split("\n")
        if lines[0] != DIALECT_HEADER:
            raise DialectError("first line %r is not the header %r"
                               % (lines[0], DIALECT_HEADER))
        expanded = []
        for raw in lines[1:]:
            if raw.endswith("\r"):
                raw = raw[:-1]
            if raw == "":
                raise DialectError("blank line in dialect message")
            alias, sep, value = raw.partition("=")
            if not sep:
                raise DialectError("line %r lacks KEY=VALUE" % raw)
            key = self._expand_alias(alias)
            if key is None:
                raise DialectError("unknown dialect alias %r" % alias)
            expanded.append((key, value))
        message = self._rebuild(expanded)
        if strict:
            re_encoded = self.encode(message)
            if re_encoded != text:
                raise DialectError(
                    "wire is not a fixed point of encode; ambiguous transform")
        return message

    def _expand_alias(self, alias):
        if alias in self._reverse_alias:
            return self._reverse_alias[alias]
        for family_alias in sorted(self._reverse_family, key=len, reverse=True):
            if alias.startswith(family_alias) and alias[len(family_alias):].isdigit():
                return self._reverse_family[family_alias] + alias[len(family_alias):]
        # Bare family alias: a packed multi-value line (DEP/ART).
        for family, family_alias in self.family_aliases.items():
            if alias == family_alias and family in self.packings:
                return family
        return None

    def _rebuild(self, expanded):
        desym = []
        for key, value in expanded:
            if key == "STATUS":
                value = self._unsymbolize("STATUS", value)
            elif key == "KIND":
                value = self._unsymbolize("KIND", value)
            elif key == "VS":
                value = self._unsymbolize("VERIFY", value)
            elif key.startswith("EV") and key[2:].isdigit():
                value = self._unsymbolize("EVIDENCE_VERIFIED", value)
            desym.append((key, value))
        lookup = {}
        for key, value in desym:
            lookup.setdefault(key, []).append(value)
        kind = (lookup.get("KIND") or [None])[0]
        verification_fail = any(v == "fail" for v in lookup.get("VS", []))
        never = kind in self.never_compress_kinds or (
            self.never_compress_verification_fail and verification_fail)
        if self.packings:
            desym = self._unpack_fields(desym)
        rebuilt = []
        present = set()
        for key, value in desym:
            present.add(key)
            if key.startswith("E") and key[1:].isdigit() and ":" in value:
                symbol, locator = value.split(":", 1)
                kind_text = self._unsymbolize("EVIDENCE_KIND", symbol)
                if not never:
                    locator = self._apply_mts_decode("E", locator)
                value = kind_text + ":" + locator
            else:
                if not never:
                    value = self._apply_mts_decode(self._mt_field(key), value)
            rebuilt.append((key, value))
        if not never:
            for key, default in self.field_defaults.items():
                if key not in present:
                    rebuilt.append((key, default))
        return AgentCommMessage.from_occ1_fields(rebuilt)

    # -- model-facing card text --------------------------------------------

    def card_text(self):
        """Compact readable spec used in speaker/listener prompts."""
        rows = []
        rows.append("DIALECT/1 %s" % self.card_id)
        rows.append("Wire: first line 'D/1', then KEY=VALUE lines.")
        rows.append("KEYS (wire key = contract field):")
        fixed = " ".join("%s=%s" % (self.key_aliases[k], k) for k in FIXED_KEYS
                         if k in self.key_aliases)
        rows.append("  " + fixed)
        fams = " ".join("%s<n>=%s<n>" % (self.family_aliases[f], f)
                       for f in FAMILY_KEYS if f in self.family_aliases)
        rows.append("  " + fams)
        rows.append("SYMBOLS:")
        for domain in ("STATUS", "KIND", "VERIFY", "EVIDENCE_KIND", "EVIDENCE_VERIFIED"):
            table = self.value_symbols.get(domain, {})
            rows.append("  %s: %s" % (domain, " ".join(
                "%s=%s" % (symbol, value) for value, symbol in table.items())))
        if self.packings:
            rows.append("PACKINGS:")
            if "E" in self.packings:
                rows.append("  E<n> carries the full evidence record inline:")
                rows.append("    e<n>=<kind symbol>:<locator>[@start-end][#digest][~revision][!verified]")
                rows.append("    verified symbol after ! (1=pass 0=fail); omit absent parts")
            if "FE" in self.packings:
                rows.append("  FE<n> lists evidence refs as bare numbers: v<n>=1,2")
            if "NEXT" in self.packings:
                rows.append("  all NEXT actions on one line: n=<action>|<action>|...")
            if "DEP" in self.packings:
                rows.append("  all dependencies on one line: u=<id>|<id>|...")
            if "ART" in self.packings:
                rows.append("  all artifacts on one line: h=<path>|<path>|...")
            if "FC" in self.packings:
                rows.append("  FC<n> confidence in permille: c<n>=900 means 0.900")
        if self.microtemplates:
            rows.append("MICROTEMPLATES (value rewrites, reversed on decode):")
            for template in self.microtemplates:
                if template.type == "prefix":
                    rows.append("  %s %s: %s... -> %s..." % (
                        template.id, template.field, template.match, template.emit))
                else:
                    rows.append("  %s %s: /%s/ -> %s (reverse /%s/ -> %s)" % (
                        template.id, template.field, template.match, template.emit,
                        template.decode_match, template.decode_emit))
        if self.field_defaults:
            rows.append("DEFAULTS (omitted line means this value):")
            for key, default in self.field_defaults.items():
                rows.append("  %s=%s" % (self.key_aliases.get(key, key), default))
        rows.append("NEVER-COMPRESS: kinds %s%s: no microtemplates or defaults; "
                    "values verbatim (symbols and packings still apply)."
                    % (self.never_compress_kinds,
                       "; verification=fail" if self.never_compress_verification_fail else ""))
        if self.usage_rules:
            rows.append("RULES: " + self.usage_rules)
        return "\n".join(rows)
