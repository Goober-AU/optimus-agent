{
 "updated": "2026-10-14 (q3 round complete)",
 "config": "ov-banked.json with H2b clause (from q2 round)",
 "results": {
  "078150f1": "1.0 (lmesq3-078150f1, multi-session arithmetic)",
  "07b6f563": "1.0 (lmesq3-07b6f563, preference)",
  "0862e8bf": "1.0 (lmesq3-0862e8bf, single-session-user)",
  "0862e8bf_abs": "1.0 (lmesq3-0862e8bfa, ABSTENTION twin - H2b anti-abstention + H1 exact-name guard both held)",
  "08e075c7": "1.0 (lmesq3-08e075c7, KU duration)",
  "08f4fc43": "1.0 (lmesq3-08f4fc43, temporal-reasoning day arithmetic)"
 },
 "parallel": "last two (08e075c7 + 08f4fc43) ran 2-concurrent under the 12-cap; both passed; no latency regressions observed",
 "fleet": "ingests ran 33-48 min (heavy fleet per parent notice); no stalls",
 "ownership_rule": "next_batch() skips any question with attempts from ANY worker (covers sibling lmesq3b's idx-25-29 slice); sibling queue = glm-lmes3",
 "queue_next": "0977f2af, 099778bb, 09ba9854, 09ba9854_abs, 09d032c9 (official order, UNTESTED)",
 "streak": "11 consecutive passes this session (q2: 4 + fixed 07741c45; q3: 6)"
}