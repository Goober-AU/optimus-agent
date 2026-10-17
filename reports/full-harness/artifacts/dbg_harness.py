
import sys, json, tempfile
sys.path.insert(0, "prime-agent-runtime/src")
from pathlib import Path
from rlm.harness import HarnessState

with tempfile.TemporaryDirectory() as td:
    sp = Path(td) / "harness_state.json"
    good = {"id":"good","kind":"memory","title":"Good","content":"fine","created_at":"2026-01-01T00:00:00+00:00","updated_at":"2026-01-01T00:00:00+00:00"}
    bad = {"id":"bad","kind":"memory","title":"Bad","content":123,"created_at":"2026-01-01T00:00:00+00:00","updated_at":"2026-01-01T00:00:00+00:00"}
    sp.write_text(json.dumps({"schema":1,"entries":{"memory":{"good":good,"bad":bad}},"refinements":[]}), encoding="utf-8")
    st = HarnessState(sp)
    print("get bad:", st.get("memory","bad"))
    st.create_memory("New","Written while a malformed sibling exists.", id="new")
    dropped = sorted(Path(td).glob("harness_state.dropped-*.json"))
    print("dropped files:", [p.name for p in dropped])
    reloaded = HarnessState(sp)
    print("reloaded keys:", sorted(reloaded.entries["memory"]))
    if st._dropped if hasattr(st,"_dropped") else None:
        print("_dropped:", st._dropped)
