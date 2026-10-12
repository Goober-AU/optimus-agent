# ITERATION-3 EXPERIMENT LEDGER

Consolidated view of every iteration-3 experiment: what was tried, on which
benchmark/sample, against which baseline, the current decision, and where the
artifacts live. Companion doc: `FULL-RUN-DECISION.md` (launch plan for the
three full runs). Sources: `PLAN.md` (chronological log), the four reference
reports + `ANSWER-PROTOCOLS.md` in this directory, `iteration3-inputs/manifests/`,
`iteration2-inputs/` (baselines + gate), the imp3 worktree git history, and the
live run directories (read-only).

Environment invariants for every row: model `dgx-glm53/glm-5.3` (DGX GLM-5.3)
for all LLM work, judge included; no Jev (noJev preflight verified in every
run log); driver `memory_bench.exe` (Rust, capture protocol
`optimus-memory-capture/temporal-authority/1.0.0`); reference clones static
(inspected, never executed).

## Ledger

| ID | Benchmark | Technique | Sample | Baseline -> Candidate | Decision | Artifacts |
|----|-----------|-----------|--------|----------------------|----------|-----------|
| E3-01 | LongMemEval (temporal-flip probe) | Answer protocols: latest-mentioned-wins supersession, counting keep/drop, entity-mismatch guard, both-answers, premise-check abstention, visible date scratchpad (Hindsight + AgentOS TR) as 4 family-tuned `answerInstruction` variants (<150 words) | 1 frozen-replay question (lme_temporal_flip, env `gpt4_78cf46a3`, fixture 89d5cf95, 77 entries, skipIngest) | flip baseline 1.0 (old instruction, iteration-2 replay) -> protocol 1.0 (official-lme-v1 judge, `judge_raw` yes; scratchpad visible; recall top-entries identical; +526 output tokens) | **KEEP** (validated, non-regressive) | `iteration3-research/ANSWER-PROTOCOLS.md`; `iteration3-research/answer-protocols-proto/` (manifest/log/run/scores, run `lme_temporal_flip_protocols_v1`); commit `0f13fa7e0` (improve/mem3-protocols, `bench/ANSWER-PROTOCOLS.md`) |
| E3-02 | LoCoMo dev300 (gate2) | Temporal discipline: recall "at" date labels, latest-mentioned-wins tie-break, header guidance; import_apply timeout scaling when prospective ON | 300 questions / 10 envs / 3 shards, defaults arm, binary `ab1e1474` (built from `bdffcad50`) | gate1 (iteration-2, binary `799760177`): overall F1 0.2039, cats1-4 0.2626 -> gate2 **PENDING** (4/10 envs ingested at ledger time) | **PENDING** (gate2 in flight) | imp3-gate worktree `reports/continuation/iteration3-inputs/locomo/` (manifests s0-s2, runs, `score_gate.py`, anchor re-score reproduces 0.2039/0.2626 exactly); commits `955aafde2`, `7cc251a26`, `7ff1b2fb2` on improve/memory-iteration-3 |
| E3-03 | LoCoMo-Plus (dev5 v3) | Write-time prospective cues (`recallProspectiveIndex`): 3-5 future-situation paraphrases per entry, indexed at body weight (Kumiho's decisive LP mechanism) | 5 envs (lp_18, lp_159, lp_69, lp_118, lp_232); lp_18 in flight: 25 sessions, 482 entries, 75 carry cues | cue coverage pre-fix: 11% (parent commit note) to 15.6% measured on lp_18 (75/482; launcher doc observed ~15%) -> parser fix committed (prose-preamble tolerance + JSON-only prompt); **smoke pending** | **KEEP mechanism / FIX committed / PENDING smoke** | fix commit `b1df68a2d` (improve/memory-iteration-3); live evidence `iteration3-inputs/runs/loco_plus_dev5_improved_v3/envs/lp_18/.../harness_state.json`; smoke manifest `imp3-cuefix/reports/continuation/iteration3-inputs/manifests/loco_plus_cuefix_smoke.json` (lp_18 replica, NOT yet run) |
| E3-04 | LME-V2 (dev5 v4) | `lmev2SpanVerify`: bounded tool-loop recall — pre-rendered trajectory index, scoped per-trajectory regex, one-state verify, span-pointer output, premise-contradiction channel (AgentRunbook-C transplant) | 5 questions / 1 env (`web`, 100 sessions), rerank pool 200, 12 entries / 12000 chars | iteration-2 dev5 v2: 0.40 mean (1 flagged) -> v4 span-verify **PENDING** (24/25 import jobs applied at ledger time; ingest near done) | **PENDING** (v4 run in flight) | code commits `6ce9f869c` + merge `1e0dbfd6e` (improve/mem3-lmev2-span); run `iteration3-inputs/runs/lme_v2_dev5_improved_v4_spanverify/`; manifest `iteration3-inputs/manifests/lme_v2_dev5_improved_v4_spanverify.json` (only delta vs v3: `lmev2SpanVerify: true`) |
| E3-05 | (all families, future runs) | `recallSourceSpans`: verbatim first-cited-turn source span per entry (400c cap), searchable at 0.5 weight (title 3.0 / body 1.0), rendered as "Source: <span>" (200c cap) in recall | none yet — merged after the live-run wave sealed their binary | unmeasured (no run has executed with it) | **KEEP (merged, unmeasured)** — applies to future runs only; 119 lib + 68 driver tests green at merge | commits `98725da08` + merge `00d6a6a9a` (improve/mem3-turnpair -> improve/memory-iteration-3) |
| E3-06 | LongMemEval-S (dev5, frozen stores) | LME answer protocol variant (a) + `recallQueryExtract: true` (question-only recall query) | 5 questions / 5 envs, frozen-store replay (skipIngest, fixtures from iteration-2 stores), expansion OFF | old instruction (iteration-2 dev5 v2: 1.00, mean 342 output tok/answer) -> protocol **1.00 (5/5)**, official-lme-v1 judge, mean 1293 output tok/answer (**+951/answer** scratchpad) | **KEEP** (non-regressive on score; +951 output tokens/answer is the accepted cost — LME's judge tolerates intermediate steps) | `iteration3-inputs/runs/lme_protocol_ab/` (5 manifests, `combined-run.jsonl`, `scores/scores-combined.json`) |

## Per-experiment notes

### E3-01 — answer protocols (validated)
- Four family variants exist: LME (144 words), LMEv2 (149), LP/LoCoMo-Plus (135),
  LoCoMo (141). The protocol element map (supersession / counting / entity guard /
  both-answers / abstention style / premise-check / scratchpad visibility) is in
  `ANSWER-PROTOCOLS.md`.
- Plumbing requirement: pair every protocol manifest with `recallQueryExtract: true`
  so instruction terms never pollute the lexical recall query. The recall header
  (temporal discipline, E3-02) already teaches latest-at-wins; the protocols extend it.
- The LoCoMo variant keeps the scratchpad internal (token-F1 protection); LME shows it
  (judge tolerates intermediate steps); LP has no abstention hatch (cue is always in
  corpus); LMEv2 forbids bare `UNKNOWN` (scores 0 on the abstention judge).

### E3-02 — temporal discipline (gate pending)
- Same 300-q dev set + era-A shard partition + settings as the iteration-2 gate
  anchor; only the binary differs (temporal-discipline build `ab1e1474` @ `bdffcad50`
  vs gate1's `799760177`). Anchor re-score with the current bench code reproduces
  the published gate1 numbers exactly (0.2039 / 0.2626), so any gate2 delta is
  attributable to the binary change.
- Manifests carry `evidenceTimestamps: true`; 10 envs total (s0: conv-26/42/47/50,
  s1: conv-30/43/48, s2: conv-41/44/49), 129+90+81 questions.
- Gate criteria: cats1-4 mean F1 vs 0.2626 (cat5 is scorer-mismatched by design;
  both arms scored 0 on all 67 cat-5 rows at gate1 — use cats 1-4).

### E3-03 — prospective cues (fix committed, smoke pending)
- Root cause of low coverage: GLM-5.3 prefixes a prose preamble before the JSON
  object; the strict parser rejected those responses (non-fatal). Fix `b1df68a2d`
  locates the first structural brace/bracket and parses from there, plus a stricter
  JSON-only prompt.
- Coverage evidence range: ~11% (parent commit note, live LP run) / ~15% (launcher
  doc, 5 sessions in) / 15.6% (this ledger's count on lp_18: 75/482 entries, 5 cues
  each where present). Treat 11-17% as the pre-fix band.
- Smoke (pending): `loco_plus_cuefix_smoke.json` is a full lp_18 replica with the
  fix stack; PASS criterion = essentially all newly created entries carry
  `prospectiveCues` (the Kumiho mechanism requires near-full coverage; raw-lexical
  top-6 gold hit rate on LP is 1.0%, so cues are load-bearing).
- The live LP v3 dev5 run (relaunched 13:22, binary `ab1e1474`) does NOT contain
  the fix — its scores measure the low-coverage stack.

### E3-04 — LME-V2 span-verify (run pending)
- v3 (same run without span-verify) also in flight; v3 and v4 differ ONLY in
  `lmev2SpanVerify`. v3 carries the interim premise-channel instruction; neither v3
  nor v4 uses the ANSWER-PROTOCOLS LMEv2 variant (b) — candidate follow-up after
  v4 reads out.
- Known v3 incident (fixed): `lme2ProcedureNotes` needed task-metadata headers the
  v2 manifest lacks — disabled for v3/v4 (`lme2ProcedureNotes: false`).

### E3-05 — source spans (merged, unmeasured)
- Merged after the current run wave sealed their binaries. The live runs (LP v3,
  LMEv2 v3/v4, LME-M dev5, gate2) all use the pre-B binary `ab1e1474`; source spans
  apply to runs launched after the next rebuild only. First measurement opportunity:
  the full runs (if rebuilt from HEAD) or a dedicated dev A/B.

### E3-06 — LME protocol A/B (keep)
- Frozen-store replay isolates the answer stage: same stores, same recall settings,
  only the instruction (and `recallQueryExtract`) differ. Score 1.00 = 5/5 with
  per-category coverage SSU x2, KU, MS, TR.
- Cost basis: 8840 input / 6467 output tokens over 5 answers (baseline 10962 / 1709):
  +951.6 output tokens/answer mean, input slightly lower (question-only recall query).
- The A/B ran with expansion OFF; the LME-S full set (M's manifests) carries
  expansion(2) ON — see FULL-RUN-DECISION.md flag.

## Run queue (state at ledger time)

| # | Run | Benchmark | State | Gate |
|---|-----|-----------|-------|------|
| R1 | `loco_plus_dev5_improved_v3` (5 envs, prospective+expansion+Cognitive instruction) | LoCoMo-Plus | LIVE (env 1/5) | feeds LP full decision |
| R2 | `lme_v2_dev5_improved_v3` (expansion + premise instruction) | LME-V2 | LIVE | E3-04 baseline arm |
| R3 | `lme_v2_dev5_improved_v4_spanverify` | LME-V2 | LIVE (env web 24/25 jobs) | E3-04 readout |
| R4 | `lme_m_dev5_improved_v1` (first-ever M run) | LongMemEval-M | LIVE (env 1/5, 90/91 jobs) | gates LME-M full |
| R5 | gate2 shards s0-s2 | LoCoMo | LIVE (4/10 envs) | E3-02 readout |
| R6 | `loco_plus_cuefix_smoke` (isolated build) | LoCoMo-Plus | PENDING | E3-03 validation; LP full prerequisite |
| R7 | LP full 401 | LoCoMo-Plus | PENDING | dev5 v3 scores + cue-fix smoke |
| R8 | LME-M full 500 (per-env) | LongMemEval-M | PENDING | dev5 M signal + protocol regeneration (see FULL-RUN-DECISION.md §4) |
| R9 | LME-S full 500 | LongMemEval-S | PENDING | E3-06 confirmed (1.00); needs run-queue clearance |

## Binary provenance (which build drives what)

- `ab1e1474` (sha256 of `imp3/target/release/examples/memory_bench.exe`, built
  2026-10-12 13:22 from `bdffcad50`): temporal discipline (E3-02), prospective
  index plumbing, query expansion/extract/distill/rerank, LMEv2 span-verify code
  (E3-04), LME-M converter support. Used by ALL live runs (R1-R5).
- NOT in `ab1e1474`: source spans (E3-05, merged `00d6a6a9a`), the cue parser fix
  (E3-03, `b1df68a2d`), the per-env re-sharding launcher patch (launcher-only).
- improve/memory-iteration-3 HEAD `ec917a26a` = `bdffcad50` + source spans + cue
  fix + launchers + LME-M re-sharding. Any rebuild after the run wave produces a
  binary with all of the above.
- Incident rule (2026-10-12): never rebuild while runs execute — the linker replaced
  a locked exe and killed a run pair. Rebuild only between waves.
