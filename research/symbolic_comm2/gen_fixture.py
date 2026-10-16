
"""Generates research/occ2opt_analysis/dialect2_corpus_wire.json:
DIALECT/2 winner wires for every corpus1 + corpus2 message, produced by
the Python research encoder (dialect2.py). The Rust promotion test
asserts byte-identical re-encoding and exact typed decode."""
import json, pathlib, sys
HERE = pathlib.Path(r"C:/Users/openclawuser/optimus-occ2opt-20261016/research/symbolic_comm2")
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "symbolic_comm"))
import contract as C
import dialect2 as D2

card = D2.Dialect2Card.load(str(HERE / "dialect2-card.json"))
entries = []
corpora = []

def add_corpus(path, split_tag):
    raw = json.loads(path.read_text(encoding="utf-8"))
    msgs = raw["messages"] if isinstance(raw, dict) else raw
    for e in msgs:
        m = C.AgentCommMessage.from_payload(e["payload"])
        wire = card.encode(m)
        back = card.decode(wire)
        assert C.equal(m, back), e["id"]
        assert card.encode(back) == wire, e["id"]
        entries.append({"id": e["id"], "kind": e["payload"]["kind"], "wire": wire})
    corpora.append({"path": "research/%s" % "/".join(path.parts[-2:]), "count": len(msgs)})

add_corpus(HERE.parent / "tasks" / "msgs_corpus.json", "corpus1")
add_corpus(HERE.parent / "tasks2" / "msgs_corpus2_evolve.json", "evolve")
add_corpus(HERE.parent / "tasks2" / "msgs_corpus2_dev.json", "dev")
add_corpus(HERE.parent / "tasks2" / "msgs_corpus2_holdout.json", "holdout")

out = {
    "card_path": "research/symbolic_comm2/dialect2-card.json",
    "card_checksum": card.checksum(),
    "format": "DIALECT/1",
    "dialect": "DIALECT/2 extensions (wire_header, value_encodings, scalar_pack)",
    "corpora": corpora,
    "messages": entries,
}
dst = HERE.parent / "occ2opt_analysis" / "dialect2_corpus_wire.json"
dst.write_text(json.dumps(out, indent=1), encoding="utf-8")
print("wrote", dst, len(entries), "messages; checksum", card.checksum()[:12])
