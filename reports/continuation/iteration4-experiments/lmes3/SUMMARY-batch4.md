# LME-S batch-4 (lmesq3b) — disjoint slice, official order idx 30-34 — 5/5 PASS

Worker: lmesq3b. Branch: improve/mem4b-lmes3 @ 234bbf7da (imp4 HEAD 4cd56bd67 merged:
W1 category-tagging + W2 battery-words write clauses; H2b + H3d premise-trust + H4
advice-construction read clauses). Binary: imp4b-target8k sha 4d2ba13f (verified at each
launch). Config: banked smoke5 base + ov-banked.json pulled fresh from HEAD (diff-verified).
TypeB ingest; captureProtocol forced; judge official-lme-v1 question-scoped; attempts root
C:/Users/openclawuser/lmesq3-att.

## Results (parallel per parent speed directive: 3 lanes, 5-min spawn stagger, fleet cap 11)

| qid | category | outcome | score | run |
|---|---|---|---|---|
| 0ea62687 | multi-session | PASS | 1.0 | lmesq3b-0ea62687 |
| 0edc2aef | single-session-preference | PASS | 1.0 | lmesq3b-0edc2aef |
| 0f05491a | knowledge-update | PASS | 1.0 | lmesq3b-0f05-r2 |
| 10d9b85a | multi-session | PASS | 1.0 | lmesq3b-10d9b85a |
| 10e09553 | knowledge-update | PASS | 1.0 | lmesq3b-10e09553 |

No failures -> no diagnosis, no fixes, no regression gates needed. Zero config/code changes.

Notable wins for the new clauses:
- 0edc2aef (Miami hotel advice): H4 advice-construction worked exactly as designed — no hotel
  recorded, so the answer built on recorded preferences and stated the basis instead of a bare
  'not recorded' abstention.
- 10e09553 (earlier fishing trip): H3d premise-trust resolved the before-the-7/22-trip
  presupposition to the 7/10 note.
- 0f05491a / 10d9b85a / 0ea62687: counts and deltas answered with per-fact date listing.

## Incidents (both recovered, no benchmark impact)

1. Parallelization kill race: the chain auto-started Q3 (0f05491a) seconds before the
   watcher's taskkill retired it; the controller+driver died mid-ingest (partial cache).
   The watcher's own Q3 launch then failed on makedirs (attempt dir existed). Recovery:
   verified no ledger attempt existed, removed the stale attempt dir, relaunched as
   lmesq3b-0f05-r2 — controller quarantined the partial cache and re-ingested; PASS.
2. Overnight gate-time parse: run_one.py gates parse HH:MM:SS as today, so Q5's 00:12 gate
   (set before midnight) had already passed and the lane spawned ~90s after Q2 instead of
   5 min. No collision (both preflights clean); fleet stayed under cap. Lesson: cross-midnight
   gates need a date component.

## Ops

- Ledger: 5 attempts auto-appended under controller lock (worker=lmesq3b). Batch-3 records
  re-appended earlier the same session under the lock (0ddfec37, 0ddfec37_abs, 0e4e4c46,
  0e5e2d1a) after the 22:57:30 rewrite stripped them.
- Wall-clock: 23:01-02:00Z (~3h with parallel lanes; sequential would have been ~4.5h).
- Per-question: 49.4 / 60.1 / ~65 (r2) / 53 / 62 min.
- Fleet peak with my 3 lanes: 9/11 drivers (other workers' shards included).
