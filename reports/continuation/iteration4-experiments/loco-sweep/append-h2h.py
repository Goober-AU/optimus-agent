
#!/usr/bin/env python3
"""Append the locosweep summary to the main ledger h2h_log (locked, atomic)."""
import json, os, sys
sys.path.insert(0, "C:/Users/openclawuser/optimus-memory-bench-20261007-040424/imp4b-locosweep/scripts")
import memory_question_loop as mq

LEDGER = "C:/Users/openclawuser/optimus-memory-bench-20261007-040424/reports/continuation/iteration4/QUESTION-LEDGER.json"
entry = json.load(open(sys.argv[1], encoding="utf-8"))
with mq.LedgerLock(LEDGER):
    L = mq.load_ledger(LEDGER)
    L.setdefault("h2h_log", []).append(entry)
    mq.atomic_write_json(LEDGER, L)
print("appended h2h_log entry:", entry.get("worker"), entry.get("purpose", "")[:80])
