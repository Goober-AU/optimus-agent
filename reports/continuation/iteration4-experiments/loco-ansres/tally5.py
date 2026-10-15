#!/usr/bin/env python3
"""loco-ansres round-5 tally: before/after ledger state + per-cell outcomes."""
import json, sys
from collections import Counter

ROOT = "C:/Users/openclawuser/optimus-memory-bench-20261007-040424"
LEDGER = ROOT + "/reports/continuation/iteration4/QUESTION-LEDGER.json"
EXP = ROOT + "/imp4b-ansres/reports/continuation/iteration4-experiments/loco-ansres"
CONVS = ["26", "41", "42", "43", "44", "47", "48", "50"]

groups = {}
for c in CONVS:
    for j in json.load(open(EXP + "/jobs-r5-%s.json" % c, encoding="utf-8")):
        groups[j["qid"]] = j["group"]
groups["conv-42_q11"] = "R"

L = json.load(open(LEDGER, encoding="utf-8"))
by_qid = {e["qid"]: e for e in L["entries"]}

rows = []
for q, g in sorted(groups.items()):
    e = by_qid.get(q)
    if not e:
        rows.append({"qid": q, "group": g, "status": "MISSING"}); continue
    atts = [a for a in (e.get("attempts") or [])
            if a.get("worker") == "ansres" and (a.get("run_id") or "").startswith("ar5-")]
    atts.sort(key=lambda a: a.get("ts") or "")
    if not atts:
        rows.append({"qid": q, "group": g, "status": e.get("status"), "ar5": []}); continue
    best = max((a.get("score") or 0) for a in atts)
    rows.append({"qid": q, "group": g, "status": e.get("status"),
                 "ar5": [{"run": a.get("run_id"), "score": a.get("score")} for a in atts],
                 "best": round(best, 3), "pass": best >= 0.5,
                 "answer": (atts[-1].get("answer_excerpt") or "")[:90]})

out = {
    "before": {"loco_FAILED": sum(1 for e in L["entries"] if e.get("benchmark") == "locomo" and e.get("status") == "FAILED"),
               "by_benchmark": L["summary"]["by_benchmark"]},
    "cells": rows,
}
n_pass = sum(1 for r in rows if r.get("pass"))
n_fail = sum(1 for r in rows if r.get("ar5") and not r.get("pass"))
n_none = sum(1 for r in rows if not r.get("ar5"))
out["after_ar5"] = {"pass": n_pass, "fail_confirmed": n_fail, "no_attempt": n_none}
g = Counter((r["group"], "pass" if r.get("pass") else ("none" if not r.get("ar5") else "fail")) for r in rows)
out["by_group"] = {f"{k[0]}/{k[1]}": v for k, v in g.items()}
json.dump(out, open(EXP + "/tally5.json", "w", encoding="utf-8", newline="\n"), indent=1)
print(json.dumps({k: out[k] for k in ("after_ar5", "by_group")}, indent=1))
print("loco FAILED now:", sum(1 for e in L["entries"] if e.get("benchmark") == "locomo" and e.get("status") == "FAILED"))
