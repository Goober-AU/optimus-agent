"""Promotion translation check: frozen DIALECT/1 card -> fixture for Rust tests.

Offline, deterministic, read-only for the research tree. Verifies the frozen
card checksum (two independent computations), encodes the full 69-message
corpus with the research encoder (dialect.py), strict-decodes each wire back
and confirms typed equality with the corpus payload, then writes the wire
fixture consumed by crates/pi-coding-agent/tests/occ2_dialect_promotion.rs.
"""
import hashlib, json, pathlib, sys

WT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(WT / "research" / "symbolic_comm"))

import dialect  # noqa: E402  (research module; read-only use)
import corpus as corpus_mod  # noqa: E402

CARD_PATH = WT / "research" / "symbolic_comm" / "dialect-card.json"
FIXTURE = WT / "research" / "occ2opt_analysis" / "dialect1_corpus_wire.json"
EXPECTED_CHECKSUM = "17836322dced3bb56e10bb0466abb5cb8d463ac6e4aba471f906ad5f24a4d49b"

BODY_KEYS = ["format", "key_aliases", "family_aliases", "value_symbols",
             "microtemplates", "field_defaults", "packings",
             "never_compress_kinds", "never_compress_verification_fail",
             "usage_rules"]

raw = json.loads(CARD_PATH.read_text(encoding="utf-8"))

# Checksum way 1: the research class's own computation.
card = dialect.DialectCard.load(str(CARD_PATH))
c1 = card.checksum()

# Checksum way 2: independent canonical body sha256 (mirrors the Rust loader).
body = {k: raw[k] for k in BODY_KEYS}
blob = json.dumps(body, sort_keys=True, separators=(",", ":"))
c2 = hashlib.sha256(blob.encode("utf-8")).hexdigest()

print("checksum(card.checksum()) :", c1)
print("checksum(independent)    :", c2)
print("checksum(file field)     :", raw["checksum"])
print("expected (task brief)    :", EXPECTED_CHECKSUM)
assert c1 == c2 == raw["checksum"] == EXPECTED_CHECKSUM, "checksum mismatch"
print("checksum verification: PASS")

# Round-trip the corpus through the research codec.
items = corpus_mod.load_corpus()
print("corpus messages:", len(items))
wires = []
total_bytes = 0
for item in items:
    wire = card.encode(item["message"])
    decoded = card.decode(wire, strict=True)
    # NOTE: research Finding has no value __eq__; compare canonical payloads.
    assert decoded.to_payload() == item["message"].to_payload(), f"typed mismatch for {item['id']}"
    wires.append({"id": item["id"], "kind": item["message"].kind,
                  "wire": wire})
    total_bytes += len(wire)
print("research strict round-trip: PASS for all", len(wires), "messages")
print("mean wire bytes: %.1f" % (total_bytes / len(wires)))

FIXTURE.parent.mkdir(parents=True, exist_ok=True)
FIXTURE.write_text(json.dumps({
    "card_path": "research/symbolic_comm/dialect-card.json",
    "card_checksum": c1,
    "format": "DIALECT/1",
    "corpus_path": "research/tasks/msgs_corpus.json",
    "messages": wires,
}, indent=1), encoding="utf-8")
print("fixture written:", FIXTURE)

print("\n--- sample wire (first message, %s) ---" % wires[0]["kind"])
print(wires[0]["wire"])
print("\n--- sample wire (a RESULT with evidence+verification) ---")
for w in wires:
    if w["kind"] == "RESULT" and "#" in w["wire"]:
        print(w["wire"]); break
