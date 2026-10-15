# CACHE-AUDIT — typeB cache inventory, integrity, and reuse map

Audit snapshot: **2026-10-15T00:25Z** (cache is LIVE: LME-S sequential queue was ingesting during the audit;
in-flight stores are marked). Branch `improve/mem4b-cachaudit`, worktree `imp4b-cachaudit`, from imp4 HEAD
`240e9fbc9`. READ-ONLY on the campaign tree: nothing outside this worktree was written.

Cache roots audited:
- `reports/continuation/iteration4/ql-typeb-cache` (main; 78 dirs = 66 live + 12 quarantined `.failed-*`/`.aborted-rsq`)
- `lpcamp-scratch/cache` (LP; 24 dirs = 5 live + 19 quarantined)
- `reports/continuation/iteration4/writeres-scratch/typeb-cache` (1 live experiment store)

## Method

- Creators were recovered from **1,950 attempt manifests** across 33 attempt roots
  (`it4/ql-attempts*`, scratch `ql-attempts`, `lmesq-att`, `lmesq3-att`, `lmesq4-att`, `lm-q4-att`,
  `lpcamp2-att`, `lpcamp-scratch/att`, …) by matching `manifest.output_dir` to cache dirs.
- Every classified store's dir name was **re-derived from first principles**: `sha256(source_rev(env json),
  env_id, importInstructions, extraction keys, model)` using the exact `typeb_cache_key` algorithm from
  `scripts/memory_question_loop.py`. Env source follows controller priority (`env-file` >
  `env-from-manifest` > qs-dir `env_<id>.json` > source-manifest). 71/71 creator-linked stores verify.
  4 early stores verify only via their true env source (manifest envs[] dropped `notes` before commit
  c8b84e401 — a manifest-only recomputation gives the wrong key; the qs-dir/env-file source gives the right one).
- Warm = `env.jsonl` has an error-free row for the env AND a store document
  (`envs/<env>/agent/memory/projects/*/harness_state.json`) exists.

## Classification table (72 live stores; key = dir suffix)

### CURRENT-CONFIG (33) — matches each family's banked write settings today

| Family / lineage | Stores (env--key) | Warm | Creator runs |
|---|---|---|---|
| LME-S `W1+W2` (1359-char importInstructions = ov-banked.json, commits 73f4dec18+629d5b2fb) | 031748ae_abs--ec432dc, 06db6396--cd87fa, 07741c44--f460b5, 07741c45--adc005, 09d032c9--69a59b, 0a995998--959d89, 0bb5a684--f32def, 0ea62687--3aad2a (warm); 0edc2aef--dbec26, 10d9b85a--2794c1, 10e09553--539974, 118b2229--4b1db3, 1192316e--328c35, 18dcd5a5--40a2c9, 1903aded--757561, 195a1a1b--a3c615 (in flight, no ingested.json yet) | 8/16 | lmesq5-gw/lmesq6/lmesq3b-b4/lpc2/lmes4-* |
| LoCoMo writecamp/writefull banked (1110-char attr-v1 text, sha 5e64c236b9c8) | conv-26--d4b542a8 (lf4-q149-ing), conv-30--62b3e5ec, conv-49--543cedbc (writefull), conv-41--f3e2ba0, conv-42--beda05a, conv-43--cfbee6d, conv-44--15430ae, conv-47--3f79028, conv-48--029159a, conv-50--a336692 (writecamp) | 10/10 | lf4/wf/wc4x-*-ing |
| LME-M banked (source-manifest base, NO importInstructions override) | bc8a6e93_abs--8abf2d78, e61a7584--3dd84f83 | 2/2 | lmemq1-bc8a-r6, lmemq1-e61a-r2 |
| LP banked (545-char lineage + `recallProspectiveIndex:true`, own key lineage in lpcamp-scratch/cache) | lp_18--9e2926e, lp_69--93b181e, lp_118--58328ea, lp_159--225ee5d, lp_232--dbbe2be | 5/5 | lpcamp-pin/rep |

### PRE-W1 (19, all warm) — H2b-era LME-S base (743-char importInstructions, no W1/W2 clauses)

031748ae_abs--76648b0 (lmesq1-031748aeabs), 06878be2--0418163, 06db6396--ed375c, 06f04340--31a6739,
07741c44--258f5f8, 07741c45--921ca2b, 078150f1--9be2568, 07b6f563--618b9b0, 0862e8bf--a24ad0f,
0862e8bf_abs--9d6ac03, 08e075c7--436bfa2, 08f4fc43--5b0d836, 0977f2af--aaafc7f, 099778bb--6b3fba3,
0db4c65d--c31ca69, 0ddfec37--9488a08, 0ddfec37_abs--fa0a6f8, 0e4e4c46--bb3be01, 0e5e2d1a--b57f903
(creators: lmesq1/lmesq2/lmesq3/lmesq3b ingest runs). **Not reusable** under the banked W1+W2 config —
a current-config run on these envs computes a different key and re-ingests.

### PRE-W2 / W1-only transitional (13, all warm) — W1 landed, W2 not yet

031748ae_abs--72d241f (lmesq4-gw-0317), 06878be2--d28ecbf, 06db6396--5d9d917, 07741c44--2d578f2,
07741c45--0fa7a59, 07b6f563--43346cf, 0862e8bf--90cdd63, 0862e8bf_abs--36d8a55, 0977f2af--9a929d2
(lmesq4-0977f2w1), 09ba9854--1d42a0d, 09ba9854_abs--8a8e7eb, 09d032c9--2956ffe, 0a34ad58--9cd4481.
Same verdict: superseded by the W2 bank; kept only as write-arm history.

### SPECIAL (7)

| Store | What it is | State |
|---|---|---|
| 0e5e2d1a--83c80b595356e84e | writecamp **clause-b** (study-facts clause B, 1110-char) — the clause-b experiment cache | warm (run wc5-0e5e2d1a-b, FAILED 0.0) |
| 0e5e2d1a--e4048c64c490b534 | writecamp **clause-a** (restrictive study clause, 1048-char) | cold/in-flight (wc5-0e5e2d1a-a) |
| conv-26--37ca5f1254f4e6c0, conv-26--603d5a512bf70394 | loco-q7 attr h4/h4b experiments on a **1-session** conv-26 env (`env_conv-26-session18.json`, tracked in repo) | warm; keys verify vs that env file; NOT the full conv-26 env |
| conv-47--3afe0f0a9f8cc2fa (writeres-scratch/typeb-cache) | attrclause-v2 experiment, 1-session conv-47 env (`writeres-scratch/env-conv47-session16.json`) | warm |
| conv-44--c6d3a1e3c47d1ed8 | **ORPHAN**: only `_control`/`_preflight` (preflight mtime 2026-10-14T09:51:51Z, the 09:45–09:51 extkill window); no env.jsonl, no ingested.json, no creator manifest anywhere, no ledger attempt | cold; controller will quarantine on next same-key ingest |
| web--46c8aec059fe9cec | lme_v2 full-env ingest (40M manifest, `captureAxtreeMaxChars:40000`, 1757-char ii) | **cold partial** — 100 sessions, project with 12 jobs, last write 11:49Z; 4 `.failed` siblings (04:51/08:43/09:24/09:46 extkills); needs fresh re-ingest |

## Headline-store verification (task 2) — 5/5 PASS

| Store | env.jsonl | q row | jobs | ingested.json | failed siblings | key re-derived |
|---|---|---|---|---|---|---|
| conv-26--d4b542a8e98deca6 | 1 row, no error | conv-26_q149 | 19/19 applied | ts 2026-10-13T09:41:03Z (lf4-q149-ing) | none | MATCH |
| conv-30--62b3e5ece0f952f0 | 1 row, no error | conv-30_q59 | 19/19 applied | ts 2026-10-14T12:49:40Z (wf30-q59-ing) | none | MATCH |
| conv-49--543cedbccf0f73d9 | 1 row, no error | conv-49_q128 | 25/25 applied | ts 2026-10-14T13:10:42Z (wf49-q128-ing) | none | MATCH |
| bc8a6e93_abs--8abf2d78200defe2 | 1 row, no error | bc8a6e93_abs | 475/475 applied | ts 2026-10-14T17:10:19Z (lmemq1-bc8a-r6) | 4 quarantined (04:39 / 08:14 extkill / 09:23 extkill2 / 09:45 r5pre) — live store is the r6 success | MATCH |
| e61a7584--3dd84f833a4fe535 | 1 row, no error | e61a7584 | 476/476 applied | ts 2026-10-14T22:50:52Z (lmemq1-e61a-r2) | none | MATCH |

All five: harness_state.json present, exactly one project, no parse errors, no in-flight writes.

## Ledger integrity sweep (READ-ONLY; ledger mtime 2026-10-14T23:50:54Z, 11.3 MB)

1. **1180 entries** confirmed; summary block matches recount (RESOLVED 26 / FAILED 162 / UNTESTED 992;
   lme_v2 3, locomo 175, locomo_plus 5, longmemeval_s 498, longmemeval_m 499).
2. **lmesq3b batch-3 records PRESENT**: 0db4c65d pass 1.0, 0ddfec37 pass 1.0, 0ddfec37_abs pass 1.0,
   0e4e4c46 pass 1.0 (all UNTESTED-status by convention — ledger tracks the failure queue), 0e5e2d1a FAILED
   with both attempts (lmesq3b-0e5e2d1a fail 0.0; wc5-0e5e2d1a-b clause-b retry fail 0.0).
3. **22:57 incident recovery PARTIALLY COMPLETE**:
   - 0977f2af: RESOLVED + resolution block present (parent re-flip 23:15Z), **but its attempts list is EMPTY** —
     the 8 stripped attempt records (lmesq4-0977f2af, -0977f2exp, -0977f2h3, -0977f2h3b, -0977f2w1, -0977fh3b2,
     -0977fh3c, -0977fh3d) exist in `C:/Users/openclawuser/lmesq-att` with scores but are not re-appended yet
     (the resolution block itself says "attempts to be re-appended by lmesseq").
   - 099778bb: its passing attempt (lmesq4-099778bb, 1.0, per queue-state-q4.md) is likewise **missing** — same
     strip, and this entry has no recovery note.
   - 09d032c9: fully recovered (RESOLVED + resolution block + 5 attempts incl. lmesq5-09dw2h4 pass 1.0).
4. **No twin-qid status leaks** (497 S/M twin qids checked): all longmemeval_m statuses UNTESTED; every attempt's
   `benchmark` field matches its entry; no S-run landed in an M entry. Nuance, not a leak: M entries 001be529 and
   0100672e hold typeA gate attempts (lmesq5-g-01be / lmesq5-g-1006) recorded by the lmes-seq worker against
   S-side frozen smoke5 stores — the twin question text is identical (verified), and the 2 LME-M headline attempts
   (lmemq1-*) are genuinely M. M twin questions whose S twin passed in smoke5 have no S ledger entry by design
   (listed under `summary.known_correct_not_ledgered`).
5. Minor: conv-41_q50 `attempts_count` summary field says 26 but the entry holds 27 attempts (off-by-one in a
   summary refresh; the attempts array itself is intact, no duplicates).

## Reuse map (task 4)

Store reuse is env-scoped and key-scoped: any future question on the same env with the same
(importInstructions, extraction, model) hits `cache-reuse` (copy-out, skipIngest) and skips the multi-hour ingest.

- **LoCoMo — full coverage**: the 10 warm writecamp/writefull stores cover **all 175 locomo ledger questions**
  (conv-26:19, conv-30:9, conv-41:18, conv-42:27, conv-43:20, conv-44:13, conv-47:15, conv-48:18, conv-49:15,
  conv-50:21). Any LoCoMo retry with the banked write config is instant-warm.
- **LME-S — 16/498 envs current-config**: 8 warm (06db6396, 07741c44, 07741c45, 09d032c9, 0a995998, 0bb5a684,
  0ea62687 + gates) + 8 in flight as of the snapshot (031748ae_abs, 0edc2aef, 10d9b85a, 10e09553, 118b2229,
  1192316e, 18dcd5a5, 1903aded, 195a1a1b — sequential queue is ingesting them now). The 19 PRE-W1 and 13 W1-only
  warm caches do NOT match the W1+W2 key: those 18 envs re-ingest if re-asked under the banked config
  (read-time-only experiments can still copy out of them, since read keys never enter the cache key).
- **LME-M — 2/499 envs warm** (bc8a6e93_abs, e61a7584). Only 5 M envs are even provisioned
  (`data/common/longmemeval_m/env_*.json`; the `full/` qs-dir has no env files), so M typeB runs need
  env-from-manifest or new env jsons; S stores do NOT serve M twins (different env content => different
  source_rev => different key).
- **LP — 5/5 ledger envs warm** in lpcamp-scratch/cache (own 545-char lineage; do not mix with the main root's
  key space).
- **lme_v2 — nothing warm**: web--46c8aec is a cold partial; the 3 lme_v2 ledger questions all need a fresh
  full-env ingest (the 40M v4 manifest remains the source).
- Headline cross-family note: writecamp's clause-b store (0e5e2d1a--83c80b) is keyed to the clause-b
  importInstructions only; the banked W1+W2 runs of 0e5e2d1a use the PRE-W1 key (b57f903) — three key lineages
  now exist for that one env.

## Integrity findings and recommended actions (no writes made)

1. `conv-44--c6d3a1e3c47d1ed8` — orphan cold dir from the 09:51Z extkill window; harmless (auto-quarantine on
   next same-key ingest) but it is the only live-looking cache entry with zero provenance. Delete or ignore.
2. `web--46c8aec059fe9cec` — cold partial with 4 failed siblings; the lme_v2 full ingest needs a clean
   restart-from-zero (the relaunch scripts in `lmev2full-relaunch/` already implement this).
3. Ledger: re-append the 8+1 stripped attempts (0977f2af family, 099778bb) — recovery note already assigns
   this to lmesseq; fix the conv-41_q50 attempts_count off-by-one in the next summary refresh.
4. Manifests created before c8b84e401 omit env `notes`, so cache keys cannot be re-derived from the attempt
   manifest alone — always resolve the env source file (qs-dir / env-file / source-manifest) as this audit did.
5. In-flight set at snapshot: 0edc2aef, 10d9b85a, 10e09553, 118b2229, 1192316e, 18dcd5a5, 1903aded, 195a1a1b,
   031748ae_abs(ec432dc), 0e5e2d1a clause-a — treat their warm/cold state as stale; re-check after the
   sequential queue drains.
