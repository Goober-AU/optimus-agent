
import json, pathlib, sys, re, collections, statistics
HERE = pathlib.Path(r"C:/Users/openclawuser/optimus-occ2opt-20261016/research/symbolic_comm2")
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "symbolic_comm"))
import contract as C
import dialect2 as D2
from evolve2 import load_split, count_tokens, key_dense_subset

win = D2.Dialect2Card.load(str(HERE / "evolution2-winner.json"))
print("lineage:", win.lineage)
print("defaults:", win.field_defaults)
print("encodings:", win.value_encodings, "header:", win.wire_header)
print("V2M MTs:", [(m.id, m.field, m.match, m.emit) for m in win.microtemplates if m.id.startswith("V2M")])

items = load_split("evolve")
wires = [win.encode(it["message"]) for it in items]
toks = count_tokens(wires)
print("\nfull evolve split: n=%d mean=%.2f tok  %.1f B" % (len(items), statistics.mean(toks), statistics.mean(len(w) for w in wires)))
print("sample wire:")
print(wires[1])

line_tok = collections.defaultdict(list)
for w in wires:
    for line in w.split("\n"):
        key = line.split("=", 1)[0]
        line_tok[key].append(count_tokens([line])[0])
tot = 0
rows = []
for key, vals in line_tok.items():
    rows.append((sum(vals), key, len(vals), statistics.mean(vals)))
    tot += sum(vals)
rows.sort(reverse=True)
print("\nper-line tokens (key, n, mean, TOTAL):")
for s, key, n, mean in rows:
    print("  %-4s n=%3d mean=%5.2f total=%5d" % (key, n, mean, s))
print("  line-token total:", tot, " vs wire total:", sum(toks))
nl = sum(w.count("\n") for w in wires)
print("  newline tokens:", nl)
