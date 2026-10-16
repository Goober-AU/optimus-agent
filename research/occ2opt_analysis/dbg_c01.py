
import json, pathlib, sys
WT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(WT / "research" / "symbolic_comm"))
import dialect, corpus as corpus_mod
card = dialect.DialectCard.load(str(WT / "research" / "symbolic_comm" / "dialect-card.json"))
items = corpus_mod.load_corpus()
item = items[0]
m = item["message"]; d = card.decode(card.encode(m), strict=True)
fo, fd = m.findings[0], d.findings[0]
print("finding slots:", [a for a in dir(fo) if not a.startswith("_")])
for a in ("id", "statement", "confidence", "evidence"):
    print(a, repr(getattr(fo, a, "<none>")), "|", repr(getattr(fd, a, "<none>")))
print("eq:", fo == fd, "conf types:", type(fo.confidence), type(fd.confidence))
