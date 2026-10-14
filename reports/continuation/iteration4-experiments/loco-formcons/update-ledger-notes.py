#!/usr/bin/env python3
"""Append formcons (FINAL9) classification notes to QUESTION-LEDGER entries.

Uses the controller's LedgerLock + atomic write (same pattern as
round4-ledger-summary-update.py). Attempts untouched.
"""
import sys, json, importlib.util

ROOT = r"C:/Users/openclawuser/optimus-memory-bench-20261007-040424"
LEDGER = ROOT + r"/reports/continuation/iteration4/QUESTION-LEDGER.json"
GATE_LEDGER = ROOT + r"/reports/continuation/iteration4/QUESTION-LEDGER-locosweep-gate.json"

NOTES = json.loads(open(sys.argv[1], encoding="utf-8").read())

spec = importlib.util.spec_from_file_location("mql", ROOT + r"/imp4/scripts/memory_question_loop.py")
mql = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mql)

for path in (LEDGER, GATE_LEDGER):
    with mql.LedgerLock(path):
        ledger = json.load(open(path, encoding="utf-8"))
        changed = 0
        for e in ledger["entries"]:
            note = NOTES.get(e["qid"])
            if note is None:
                continue
            cur = e.get("note") or ""
            if "formcons (FINAL9)" in cur:
                continue
            e["note"] = (cur + "; " if cur else "") + note
            changed += 1
        with open(path, "w", encoding="utf-8", newline="\n") as h:
            json.dump(ledger, h, indent=1, ensure_ascii=False)
            h.write("\n")
    print(path, "updated:", changed)
