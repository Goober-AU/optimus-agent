
import json, pathlib, sys
HERE = pathlib.Path(r"C:/Users/openclawuser/optimus-occ2opt-20261016/research/symbolic_comm2")
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "symbolic_comm"))
import contract as C
import dialect as D
import dialect2 as D2
v1 = D.DialectCard.load(str(HERE.parent / "symbolic_comm" / "dialect-card.json"))
v2 = D2.Dialect2Card.promote(v1)
v2.wire_header = "d"
v2.value_encodings = {"TS": "epoch36", "EXP": "epoch36", "ED": "b36", "ER": "b36", "FC": "b36"}
v2.scalar_pack = True
v2.validate()
ok = 0
for split in ("evolve", "dev", "holdout"):
    raw = json.loads((HERE.parent / "tasks2" / ("msgs_corpus2_%s.json" % split)).read_text(encoding="utf-8"))
    for e in raw["messages"]:
        m = C.AgentCommMessage.from_payload(e["payload"])
        w = v2.encode(m)
        back = v2.decode(w)
        assert C.equal(m, back), split + " " + str(e["id"])
        assert v2.encode(back) == w, "fp " + str(e["id"])
        ok += 1
print("all-encodings strict round-trips:", ok, "/ 204")
w = v2.encode(C.AgentCommMessage.from_payload(json.loads((HERE.parent / "tasks2" / "msgs_corpus2_evolve.json").read_text(encoding="utf-8"))["messages"][1]["payload"]))
print(w)
