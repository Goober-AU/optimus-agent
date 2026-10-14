#!/usr/bin/env python3
"""Spot-diagnosis dump for selected qids: question, gold, draw answers, recall window note titles."""
import json, os, re, sys

ROOT = "C:/Users/openclawuser/optimus-memory-bench-20261007-040424"
LEDGER = ROOT + "/reports/continuation/iteration4/QUESTION-LEDGER.json"
ATT = ROOT + "/reports/continuation/iteration4/ql-attempts-locostab"

def load(p):
    with open(p, encoding="utf-8") as f:
        return json.load(f)

L = load(LEDGER)
ent = {e["qid"]: e for e in L["entries"] if e.get("benchmark") == "locomo"}

def store_notes(env_id):
    sp = ent["conv-%s_q0" % env_id.split("-")[1]] if False else None
    # find any entry with this env
    for e in L["entries"]:
        if e.get("benchmark") == "locomo" and e.get("env_id") == env_id:
            sp = e["store_path"]; break
    pj = os.path.join(sp, "agent", "memory", "projects")
    proj = [d for d in os.listdir(pj) if d.startswith("project_")][0]
    return json.load(open(os.path.join(pj, proj, "harness_state.json")))["entries"]["memory"]

for qid in sys.argv[1:]:
    e = ent[qid]
    conv = e["env_id"]
    qs = None
    for line in open(ROOT + "/reports/continuation/iteration2-inputs/locomo/common/locomo/qs_%s.jsonl" % conv, encoding="utf-8"):
        r = json.loads(line)
        if r["qid"] == qid:
            qs = r; break
    notes = store_notes(conv)
    print("=" * 100)
    print("Q:", qs["question"])
    print("GOLD:", repr(qs["answer"]))
    for a in e["attempts"]:
        if a.get("worker") not in ("locosweep", "locostab"):
            continue
        print("-" * 80)
        print("%-18s %-9s score=%-8s flag=%s" % (a["run_id"], a["worker"], a.get("score"), a.get("flag")))
        print("  answer:", repr(a.get("answer_excerpt")))
        rec = a.get("recall_ids") or []
        print("  recall_n=%d chars=%s" % (len(rec), a.get("recall_chars")))
        print("  window titles:")
        for rid in rec[:24]:
            k = rid.split("memory:")[-1]
            t = (notes.get(k) or {}).get("title") or "(imported, no title)"
            print("    ", t[:110])
