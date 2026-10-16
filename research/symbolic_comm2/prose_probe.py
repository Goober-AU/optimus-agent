
import json, pathlib, sys, re, collections, statistics
HERE = pathlib.Path(r"C:/Users/openclawuser/optimus-occ2opt-20261016/research/symbolic_comm2")
sys.path.insert(0, str(HERE.parent / "symbolic_comm"))
import dialect as D
from contract import AgentCommMessage
from transformers import AutoTokenizer
MODELS = pathlib.Path(r"C:/Users/openclawuser/optimus-occ23-models/hub")
QWEN = str(next((MODELS / "models--Qwen--Qwen2.5-0.5B-Instruct" / "snapshots").glob("*")))
tok = AutoTokenizer.from_pretrained(QWEN, local_files_only=True)
def ntok(t): return len(tok(t, add_special_tokens=False)["input_ids"])
card = D.DialectCard.load(str(HERE.parent / "symbolic_comm" / "dialect-card.json"))
raw = json.loads((HERE.parent / "tasks2" / "msgs_corpus2_evolve.json").read_text(encoding="utf-8"))
msgs = [AgentCommMessage.from_payload(e["payload"]) for e in raw["messages"]]

# collect prose-ish values by field family
vals = collections.defaultdict(list)
for m in msgs:
    for k, v in m.occ1_fields():
        fam = None
        for fam_key in ("F", "FC", "FE", "E", "NEXT", "ART", "DEP"):
            if k.startswith(fam_key) and k[len(fam_key):].isdigit():
                fam = fam_key; break
        if fam is None and k in ("NEXT",): fam = "NEXT"
        if fam: vals[fam].append(v)
for fam, vs in vals.items():
    c = collections.Counter(vs)
    rep = sum(n for n in c.values() if n >= 2)
    print(f"{fam:5s} n={len(vs):3d} unique={len(c):3d} repeat-instances={rep:3d} top={c.most_common(3)}")

# token cost of full-value symbols: how many tokens would repeated values cost as-is vs as a sigil
rep_vals = collections.Counter()
for fam, vs in vals.items():
    if fam in ("F", "NEXT", "E"):
        for v in vs: rep_vals[(fam, v)] += 1
tok_now = sum(n * ntok(v) for (f, v), n in rep_vals.items())
tok_sym = sum(n * 2 for (f, v), n in rep_vals.items())  # sigil + something
print("\nprose-ish value tokens now:", tok_now, " if each full value were a 2-tok symbol:", tok_sym)

# prefix mining potential
pref = collections.Counter()
for fam in ("F", "NEXT", "E", "ART", "DEP"):
    for v in vals[fam]:
        for size in (6, 10, 14, 20):
            if len(v) > size + 4: pref[(fam, v[:size])] += 1
common = [(k, c) for k, c in pref.most_common(40) if c >= 4]
print("\ncommon prefixes >=4:", len(common))
for (f, p), c in common[:20]:
    print(f"  {f} {p!r} x{c}  tok {ntok(p)}")
# unique prefix tokens saved if replaced by 1 tok
