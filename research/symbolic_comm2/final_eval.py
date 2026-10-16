
"""Final DIALECT/2 evaluation: winner vs baselines on all profiles."""
import json, pathlib, sys, statistics
HERE = pathlib.Path(r"C:/Users/openclawuser/optimus-occ2opt-20261016/research/symbolic_comm2")
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "symbolic_comm"))
# requests stub (offline venv; gateway.py imports it at module level only)
try:
    import requests  # noqa: F401
except ImportError:
    import types as _types, sys as _sys
    _stub = _types.ModuleType("requests")
    def _offline(*_a, **_k):
        raise RuntimeError("offline: gateway stub")
    _stub.post = _offline
    _stub.get = _offline
    _sys.modules["requests"] = _stub

import contract as C
import dialect as D
import dialect2 as D2
import evolve as E
from evolve2 import load_split, count_tokens, key_dense_subset, structural_density
from baselines import occ1_text

def load_v1_corpus():
    raw = json.loads((HERE.parent / "tasks" / "msgs_corpus.json").read_text(encoding="utf-8"))
    return [{"id": e["id"], "message": C.AgentCommMessage.from_payload(e["payload"])} for e in raw]

CODECS = {}
CODECS["OCC-1"] = lambda m: occ1_text(m)
sym_v1 = E.seed_cards()[0]
CODECS["sym-v1"] = lambda m: sym_v1.encode(m)
v1w = D.DialectCard.load(str(HERE.parent / "symbolic_comm" / "dialect-card.json"))
CODECS["sym-v1-pt-pt-r1"] = lambda m: v1w.encode(m)
d2 = D2.Dialect2Card.load(str(HERE / "evolution2-winner.json"))
CODECS["dialect2-winner"] = lambda m: d2.encode(m)

profiles = {
    "corpus1 (69)": load_v1_corpus(),
    "corpus2-evolve (90)": load_split("evolve"),
    "corpus2-dev (60)": load_split("dev"),
    "corpus2-holdout (54)": load_split("holdout"),
}

# fidelity of d2 everywhere (incl. holdout, never used during evolution)
print("== fidelity (d2 winner, strict round-trip) ==")
for name, items in profiles.items():
    bad = []
    for it in items:
        try:
            w = d2.encode(it["message"])
            back = d2.decode(w)
            if not C.equal(it["message"], back) or d2.encode(back) != w:
                bad.append(it["id"])
        except Exception as err:
            bad.append("%s:%s" % (it["id"], str(err)[:40]))
    print("  %-20s %d/%d ok %s" % (name, len(items) - len(bad), len(items), bad[:3]))

print("\n== mean wire tokens / bytes ==")
hdr = "%-22s | %8s %8s %8s | %8s %8s %8s" % ("profile", "OCC-1", "sym-v1", "r1", "d2", "d2/r1", "d2/OCC1")
print(hdr)
rows = {}
for name, items in profiles.items():
    row = {}
    for cname, fn in CODECS.items():
        wires = [fn(it["message"]) for it in items]
        toks = count_tokens(wires)
        row[cname] = (statistics.mean(toks), statistics.mean(len(w) for w in wires))
    rows[name] = row
    print("%-22s | %8.2f %8.2f %8.2f | %8.2f %8.2f %8.4f   (B: %.1f/%.1f/%.1f/%.1f)"
          % (name, row["OCC-1"][0], row["sym-v1"][0], row["sym-v1-pt-pt-r1"][0],
             row["dialect2-winner"][0], row["dialect2-winner"][0] / row["sym-v1-pt-pt-r1"][0],
             row["dialect2-winner"][0] / row["OCC-1"][0],
             row["OCC-1"][1], row["sym-v1"][1], row["sym-v1-pt-pt-r1"][1], row["dialect2-winner"][1]))

print("\n== key-dense subprofile (top third by structural density) ==")
for name, items in profiles.items():
    dense = key_dense_subset(items, size=max(1, len(items) // 3))
    row = {}
    for cname, fn in CODECS.items():
        toks = count_tokens([fn(it["message"]) for it in dense])
        row[cname] = statistics.mean(toks)
    print("  %-20s n=%2d OCC-1 %7.2f  sym-v1 %7.2f  r1 %7.2f  d2 %7.2f  d2/OCC1 %.4f"
          % (name, len(dense), row["OCC-1"], row["sym-v1"], row["sym-v1-pt-pt-r1"],
             row["dialect2-winner"], row["dialect2-winner"] / row["OCC-1"]))

print("\nwinner card:")
print(json.dumps({k: v for k, v in d2.to_json().items() if k != "microtemplates"}, indent=1, sort_keys=True))
print("microtemplates:", json.dumps(d2.to_json()["microtemplates"]))
