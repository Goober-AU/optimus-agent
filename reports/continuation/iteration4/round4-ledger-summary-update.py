#!/usr/bin/env python3
"""Update the QUESTION-LEDGER summary counts block (statuses only; attempts untouched).

Uses the controller's own LedgerLock + atomic write so live workers never race us.
Adds a round4_tally sub-block with the evidence-worker classification counts.
"""
import sys, json, importlib.util
from collections import Counter

ROOT = r"C:/Users/openclawuser/optimus-memory-bench-20261007-040424"
LEDGER = ROOT + r"/reports/continuation/iteration4/QUESTION-LEDGER.json"
SCORECARD_CLASSES = json.loads(sys.argv[1]) if len(sys.argv) > 1 else None

spec = importlib.util.spec_from_file_location("mql", ROOT + r"/imp4/scripts/memory_question_loop.py")
mql = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mql)

with mql.LedgerLock(LEDGER):
    ledger = json.load(open(LEDGER, encoding="utf-8"))
    by_status = Counter(e["status"] for e in ledger["entries"])
    by_bm = {}
    for e in ledger["entries"]:
        b = by_bm.setdefault(e["benchmark"], {"total": 0})
        b["total"] += 1
        b[e["status"]] = b.get(e["status"], 0) + 1
    ledger["summary"]["total_entries"] = len(ledger["entries"])
    ledger["summary"]["by_status"] = {k: by_status[k] for k in ("FAILED", "EVAL_ERROR", "RESOLVED", "UNTESTED") if by_status.get(k)}
    ledger["summary"]["by_benchmark"] = by_bm
    ledger["summary"]["round4_tally"] = {
        "snapshot_ts": max((a["ts"] for e in ledger["entries"] for a in (e.get("attempts") or [])), default=None),
        "source": "ROUND4-SCORECARD.md (imp4b-evidence, improve/mem4b-evidence); classification of the FAILED queue after the FINAL8 merge",
        "classes": SCORECARD_CLASSES,
        "note": "counts only; per-question classes live in the scorecard, not in entries",
    }
    mql.atomic_write_json(LEDGER, ledger)
print("summary updated:", dict(by_status))
