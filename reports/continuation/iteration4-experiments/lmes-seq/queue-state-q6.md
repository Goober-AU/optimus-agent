{
 "updated": "2026-10-15 (q6 round complete)",
 "config": "ov-banked.json = W1+W2 (write) + H2b+H3d+H4+H5 (read)",
 "results": {
  "0a995998": "0.0 -> 1.0 with H5 (lmesq6-0a9959h5); RESOLVED (count clause)",
  "0bb5a684": "1.0 (lmesq6-0bb5a684, 7-day arithmetic)",
  "0bc8ad92": "0.0 FAILED - needs driver fix (question_date visibility); documented",
  "0bc8ad93": "0.0 (blank-rescue defect) -> 1.0 on clean retry (lmesq6-0bc8a93b); no config change"
 },
 "ledger_recovery": "0977f2af (8 attempts) + 099778bb (1 attempt) re-appended under lock per audit",
 "queue_next": "0c... onward",
 "driver_gaps": "question_date visibility (0bc8ad92), blank-rescue note loss (3 incidents), expansion nondeterminism, judge empty-content"
}