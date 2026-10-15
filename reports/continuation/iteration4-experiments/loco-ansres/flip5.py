#!/usr/bin/env python3
"""loco-ansres round-5 ledger flips: RESOLVED for passing cells, FINAL9-confirmed notes for failures.

Reads the ansres ar5-* attempts from the ledger, applies the round-5 policy:
- any cell (R or F group) with a passing ar5 draw (score >= 0.5) -> RESOLVED
  with a resolution block citing the run-id + commit;
- R-group cells with no passing ar5 draw -> stay FAILED, note gains a
  FINAL9-confirmed clause;
- F-group cells with no passing ar5 draw (a1 + a2 both < 0.5) -> stay FAILED,
  note documents the fresh-store re-run failure vs prior stability.

Usage: python flip5.py <ansres-commit-sha> [--apply]
Default is dry-run (prints the plan); --apply writes under the ledger lock.
"""
import importlib.util, json, os, sys

WT = "C:/Users/openclawuser/optimus-memory-bench-20261007-040424/imp4b-ansres"
ROOT = "C:/Users/openclawuser/optimus-memory-bench-20261007-040424"
LEDGER = ROOT + "/reports/continuation/iteration4/QUESTION-LEDGER.json"
EXP = WT + "/reports/continuation/iteration4-experiments/loco-ansres"

spec = importlib.util.spec_from_file_location("mql", WT + "/scripts/memory_question_loop.py")
mql = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mql)

COMMIT = sys.argv[1]
APPLY = "--apply" in sys.argv

# group map from the jobs files
groups = {}
for c in ["26", "41", "42", "43", "44", "47", "48", "50"]:
    for j in json.load(open(EXP + "/jobs-r5-%s.json" % c, encoding="utf-8")):
        groups[j["qid"]] = j["group"]
groups["conv-42_q11"] = "R"  # preflight cell

def ar5_attempts(entry):
    out = []
    for a in (entry.get("attempts") or []):
        if a.get("worker") == "ansres" and (a.get("run_id") or "").startswith("ar5-"):
            out.append(a)
    out.sort(key=lambda a: a.get("ts") or "")
    return out

with mql.LedgerLock(LEDGER):
    ledger = mql.load_ledger(LEDGER)
    plan = []
    for e in ledger.get("entries", []):
        qid = e.get("qid")
        if qid not in groups or e.get("benchmark") != "locomo":
            continue
        atts = ar5_attempts(e)
        if not atts:
            plan.append({"qid": qid, "action": "NO-ATTEMPT", "group": groups[qid]})
            continue
        best = max((a.get("score") or 0) for a in atts)
        passing = [a for a in atts if (a.get("score") or 0) >= 0.5]
        run_ids = [a.get("run_id") for a in atts]
        if passing:
            plan.append({"qid": qid, "action": "RESOLVE", "group": groups[qid],
                         "run_ids": run_ids, "score": round(best, 3)})
        else:
            plan.append({"qid": qid, "action": "KEEP-FAILED", "group": groups[qid],
                         "run_ids": run_ids, "score": round(best, 3)})
    if APPLY:
        today = "2026-10-15"
        for row in plan:
            e = mql.find_entry(ledger, row["qid"])
            if row["action"] == "RESOLVE":
                e["status"] = "RESOLVED"
                e["resolution"] = {
                    "worker": "ansres",
                    "ts": today,
                    "note": ("round-5 FINAL9 fresh-store re-run pass (score %.3f, runs %s; "
                             "ansres campaign commit %s; local-F1 question-scoped, typeB "
                             "cache-reuse on the warm clause-set store)" % (
                                 row["score"], ",".join(row["run_ids"]), COMMIT)),
                }
            elif row["action"] == "KEEP-FAILED":
                if row["group"] == "R":
                    add = ("round-5 FINAL9 fresh-store re-run FAILED (%.3f, runs %s) - "
                           "FINAL9-confirmed residual (ansres, commit %s)" % (
                               row["score"], ",".join(row["run_ids"]), COMMIT))
                else:
                    add = ("round-5 FINAL9 fresh-store re-run failed x2 (%.3f, runs %s) "
                           "despite prior stability-STABLE FINAL7 evidence - left FAILED "
                           "(ansres, commit %s)" % (row["score"], ",".join(row["run_ids"]), COMMIT))
                note = e.get("note") or ""
                if "round-5 FINAL9 fresh-store re-run" not in note:
                    e["note"] = (note + "; " + add).strip("; ") if note else add
        # refresh summary counts
        from collections import Counter
        by_status = Counter(e2.get("status") for e2 in ledger["entries"])
        by_bm = {}
        for e2 in ledger["entries"]:
            d = by_bm.setdefault(e2.get("benchmark"), {"total": 0})
            d["total"] += 1
            d[e2.get("status")] = d.get(e2.get("status"), 0) + 1
        ledger["summary"]["by_status"] = dict(by_status)
        ledger["summary"]["by_benchmark"] = by_bm
        ledger["summary"]["total_entries"] = len(ledger["entries"])
        mql.atomic_write_json(LEDGER, ledger)

print(json.dumps({"apply": APPLY, "commit": COMMIT,
                  "resolve": sum(1 for r in plan if r["action"] == "RESOLVE"),
                  "keep_failed": sum(1 for r in plan if r["action"] == "KEEP-FAILED"),
                  "no_attempt": sum(1 for r in plan if r["action"] == "NO-ATTEMPT")}, indent=1))
for r in plan:
    if r["action"] != "RESOLVE":
        print(r["action"], r["qid"], r.get("group"), r.get("score"))
