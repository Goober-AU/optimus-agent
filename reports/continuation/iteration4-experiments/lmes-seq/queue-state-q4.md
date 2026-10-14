{
 "updated": "2026-10-14 (q4 round complete)",
 "config": "ov-banked.json = W1 (importInstructions category words) + H3d (answerInstruction premise-trust) on top of H2b",
 "results": {
  "0977f2af": "0.0 banked -> 1.0 with W1+H3d (lmesq4-0977fh3d); RESOLVED - two-stage failure (retrieval vocab gap, then premise-abstention)",
  "099778bb": "1.0 (lmesq4-099778bb, was in flight when stop-rule hit)"
 },
 "stopped": "queue stopped at 0977f2af failure per protocol; 099778bb was already running and passed",
 "queue_next": "09ba9854, 09ba9854_abs, 09d032c9, 0a34ad58 (UNTESTED, official order)",
 "warm_caches": "existing banked-config caches remain valid for read-time; W1 importInstructions = NEW cache key (W1 re-ingest caches: 07741c45, 031748ae_abs, 0862e8bf, 0862e8bf_abs, 06db6396, 07741c44, 0977f2af)",
 "caveats": "H3b-style long clauses cause glm-5.3 length-stop empty answers; blank-rescue answers without notes (driver defect)"
}