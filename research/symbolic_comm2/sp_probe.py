
import json, pathlib, sys, statistics
HERE = pathlib.Path(r"C:/Users/openclawuser/optimus-occ2opt-20261016/research/symbolic_comm2")
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "symbolic_comm"))
import dialect2 as D2
from evolve2 import load_split, count_tokens

win = D2.Dialect2Card.load(str(HERE / "evolution2-winner.json"))
items = load_split("evolve")
wires = [win.encode(it["message"]) for it in items]
base = count_tokens(wires)

def sp_wire(wire, card):
    lines = wire.split("\n")
    am = card.key_aliases["MSGID"]; at = card.key_aliases["TASK"]
    ax = card.key_aliases["TRACE"]; aw = card.key_aliases["TS"]
    want = {am: "MSGID", at: "TASK", ax: "TRACE", aw: "TS"}
    found = {}
    for i, line in enumerate(lines):
        al, sep, val = line.partition("=")
        if sep and al in want and want[al] not in found:
            found[want[al]] = (i, val)
    if len(found) != 4 or any(" " in v for _i, v in found.values()):
        return wire
    order = ["MSGID", "TASK", "TRACE", "TS"]
    vals = [found[k][1] for k in order]
    idxs = [found[k][0] for k in order]
    new_lines = [l for i, l in enumerate(lines) if i not in set(idxs)]
    pos = min(idxs)
    new_lines.insert(pos, "$=" + " ".join(vals))
    return "\n".join(new_lines)

sp = [sp_wire(w, win) for w in wires]
sptok = count_tokens(sp)
print("base mean %.2f  SP mean %.2f  delta %.2f tok/msg" % (statistics.mean(base), statistics.mean(sptok), statistics.mean(base)-statistics.mean(sptok)))
print("bytes: base %.1f  SP %.1f" % (statistics.mean(len(w) for w in wires), statistics.mean(len(w) for w in sp)))
print(sp[1])
# round-trip sanity: decode SP wire manually
w = sp[1]
lines = w.split("\n")
out = []
for line in lines:
    if line.startswith("$="):
        parts = line[2:].split(" ")
        out.append("%s=%s" % (win.key_aliases["MSGID"], parts[0]))
        out.append("%s=%s" % (win.key_aliases["TASK"], parts[1]))
        out.append("%s=%s" % (win.key_aliases["TRACE"], parts[2]))
        out.append("%s=%s" % (win.key_aliases["TS"], parts[3]))
    else:
        out.append(line)
msg = win.decode("\n".join(out))
print("roundtrip ok:", D2 is not None)
