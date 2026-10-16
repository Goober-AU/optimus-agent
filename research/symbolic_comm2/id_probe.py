
import json, pathlib, sys, re, collections
HERE = pathlib.Path(r"C:/Users/openclawuser/optimus-occ2opt-20261016/research/symbolic_comm2")
sys.path.insert(0, str(HERE.parent / "symbolic_comm"))
from contract import AgentCommMessage
for split in ("evolve", "dev", "holdout"):
    raw = json.loads((HERE.parent / "tasks2" / ("msgs_corpus2_%s.json" % split)).read_text(encoding="utf-8"))
    msgs = [AgentCommMessage.from_payload(e["payload"]) for e in raw["messages"]]
    seq = collections.Counter(); task = collections.Counter(); msgid = collections.Counter(); tr = collections.Counter()
    for m in msgs:
        for k, v in m.occ1_fields():
            if k == "SEQ": seq[v] += 1
            if k == "TASK": task[v.split("-")[0] + "-"] += 1
            if k == "MSGID": msgid[v.split("-")[0] + "-"] += 1
            if k == "TRACE": tr[v.split("-")[0] + "-"] += 1
    print(split, "n=%d" % len(msgs))
    print("  SEQ:", dict(seq))
    print("  TASK prefixes:", dict(task))
    print("  MSGID prefixes:", dict(msgid))
    print("  TRACE prefixes:", dict(tr))
