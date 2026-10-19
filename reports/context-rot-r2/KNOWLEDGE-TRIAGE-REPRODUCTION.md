# KNOWLEDGE-TRIAGE REPRODUCTION — the TypeCompact multiturn-stability experiment, reproduced offline

Scope: the deterministic offline reproduction of the CIKM 26 Knowledge
Triage multiturn-stability experiment, committed by W5. Report writer W21,
2026-10-20; no new measurement was produced for this file. Category:
SYNTHETIC-DIAGNOSTIC - their Python operators on static rule files, not the
Optimus pipeline. Sources:
`research/context_rot_r2/repro/typecompact_repro.json` (n = 20),
`research/context_rot_r2/repro/typecompact_repro_full_suite.json`
(n = 197), `research/context_rot_r2/repro/typecompact_repro.py` (the
committed driver), and
`research/context_rot_r2/KNOWLEDGE-TRIAGE-STUDY.md` (the study, sections
4-8).

## 1. Upstream provenance

| item | value |
| --- | --- |
| upstream repo | https://github.com/searchsim-org/cikm26-knowledge-triage |
| pinned commit | `a6ceb01a3368cee25ef7ebcf05ebdab8c9be24a4` |
| upstream experiment | `experiments/multiturn_stability.py` |
| upstream paper | The Compaction Cliff in Long-Running AI Agent Memory (CIKM 26) |
| their committed numbers | `results/multiturn_stability.json` (carried inside our repro JSON as a labelled reference-only block) |

## 2. Reproduction setup

- Corpus: their shipped stratified AAC sample, 327 files (the
  full-corpus E2 stratum is not shipped; their committed numbers ran 20
  configs of it).
- Segmentation: their `build_kb_e2` rules (800-30 000 chars, at least 10
  items per file).
- Classifier: their regex reference classifier
  (`classifier.py classify_instruction`), not the LLM-adjudicated cascade
  their paper numbers used.
- Operators: their three - TypeCompact (Algorithm 1, pin constraints
  outside the summarizable window), TypeDecompose (Algorithm 2),
  TypeRetrieve (Algorithm 3); study section 4 analyses each.
- Loop: `n_configs` configurations, compaction ratio 0.5 per round, seed 11,
  metric = mean constraint recall versus the ORIGINAL constraint set per
  cycle (their `constraint_preserved` key-token check).
- Rounds: 10 (their protocol is 5; cycles 6-10 are our extension, which is
  where the floor interaction in section 6 shows).

## 3. What was substituted, and why

Their vanilla arm calls live LLMs (`openai:gpt-5.4-mini`,
`claude_code:claude-sonnet-4-6`); the campaign's offline rule forbids model
calls, so the reproduction replaces them with three deterministic
baselines: `naive_head` (their `hierarchical_summarize` proxy), `naive_tail`
(their `temporal_window` proxy), and `naive_truncate` (their
`truncate_to_tokens` alone - deterministic truncation). Their LLM numbers
are carried in the JSON as a reference-only block and are NOT claimed as
ours. The other deviations, verbatim from the committed JSON:

- classification uses the regex baseline, not the LLM-adjudicated cascade,
  so the identified constraint sets differ from the paper's runs;
- their experiment ran 5 rounds on 20 configs of the full-corpus E2
  stratum; this reproduction runs 10 rounds on the shipped 327-file sample
  subset (n configurable);
- their loop reclassifies post-compaction text with the cached cascade each
  round for LLM arms; the naive baselines rebuild the KB with the regex
  classifier (`from_lines`), which is the offline equivalent.

## 4. Numbers (deterministic, ours)

n = 20 configs (their protocol size), 10 rounds, ratio 0.5, seed 11 -
`research/context_rot_r2/repro/typecompact_repro.json`:

| arm | c1 | c2 | c3 | c4 | c5 | c10 |
| --- | --- | --- | --- | --- | --- | --- |
| typecompact | 1.000 | 1.000 | 0.8872 | 0.8408 | 0.8408 | 0.8408 |
| naive_head | 0.3663 | 0.1388 | 0.0607 | 0.0071 | 0.0071 | 0.0071 |
| naive_tail | 0.6334 | 0.2800 | 0.1126 | 0.0574 | 0.0538 | 0.0538 |
| naive_truncate | 0.3663 | 0.1388 | 0.0607 | 0.0071 | 0.0071 | 0.0071 |

(at n = 20, cycles 6-9 equal cycle 5 in every arm; the full per-cycle
table is in the JSON. `n_unsafe_status_rounds_typecompact` = 73 rounds
returned `COMPACTION_UNSAFE`.)

n = 197 rerun (all qualifying sample files) -
`research/context_rot_r2/repro/typecompact_repro_full_suite.json`:

| arm | c1 | c2 | c3 | c4 | c5 | c10 |
| --- | --- | --- | --- | --- | --- | --- |
| typecompact | 1.000 | 0.9824 | 0.8863 | 0.8482 | 0.8482 | 0.8482 |
| naive_head | 0.3908 | 0.1873 | 0.0822 | 0.0290 | 0.0252 | 0.0252 |
| naive_tail | 0.5782 | 0.3165 | 0.1511 | 0.1102 | 0.0955 | 0.0944 |
| naive_truncate | 0.3992 | 0.1936 | 0.0859 | 0.0372 | 0.0284 | 0.0277 |

(`n_unsafe_status_rounds_typecompact` = 824; here the baselines settle one
cycle later - naive_tail 0.0944 from cycle 6, naive_truncate 0.0277 from
cycle 7.)

Headline: the qualitative claim reproduces deterministically at both
sample sizes - TypeCompact holds 1.00 -> 0.8872 (c3) -> 0.8408 (c5, flat to
c10) while deterministic truncation cliffs to 0.0071 (n = 20) / 0.0277
(n = 197). A pin-outside-the-summarizer policy survives repeated
compaction that destroys naive summarization.

Their committed LLM numbers, reference only (20 configs, 5 rounds, their
full-corpus stratum, LLM-adjudicated cascade - from the same JSONs):

| strategy | r1 | r2 | r3 | r4 | r5 |
| --- | --- | --- | --- | --- | --- |
| TypeCompact (their run) | 1.000 | 0.9603 | 0.9579 | 0.9579 | 0.9579 |
| vanilla gpt-5.4-mini | 0.4766 | 0.2756 | 0.1792 | 0.1229 | 0.1021 |
| vanilla Sonnet 4.6 (/compact) | 0.5283 | 0.2123 | 0.1556 | 0.1267 | 0.1011 |

Our TypeCompact sits below their 0.96 for two reasons (study section 8):
the regex classifier labels a different, larger constraint set, and cycles
6-10 push the budget to the 64-token floor where `truncate_to_tokens`
starts cutting pinned constraints.

## 5. Mapping their types to our typed_state v2 kinds

The study's mapping table (section 6), with the committed typed_state v2
counterparts added (`crates/pi-coding-agent/src/core/compaction/typed_state.rs`,
commit `c948905a3`; 14 `TypedInformationKind` values):

| Knowledge Triage | study's iteration-2 mapping | committed typed_state v2 counterpart |
| --- | --- | --- |
| CONSTRAINT (user-stated rule) | USER_CONSTRAINT - pin-for-survival lane; their `type_compact` step 1 | `UserConstraint` (UC) - highest allocator lane with safety and criteria |
| CONSTRAINT (system / policy / permission) | SAFETY_OR_PERMISSION_BOUNDARY - their Authority SYSTEM/DEVELOPER weight 1.5/1.25; never enters the summarizable window | `SafetyOrPermissionBoundary` (SB) - same never-summarized lane |
| CONSTRAINT as invalidation of earlier state | USER_CORRECTION - not modeled upstream; maps to the supersession pass in `task_state.rs` | `UserCorrection` (CO) - explicit supersession, both kept, revival on restatement |
| PROCEDURAL | ledger `decisions` + `anchors` (commands, exact tool invocations); their `procedure_preserved` = command survives verbatim | allocator `decisions` lane (`VerifiedDecision` VD, `DecisionRationale` DR) and `task/pending` lane (`CurrentTask` TK, `PendingAction` PA) |
| BELIEF | session facts; ledger `evidence` when tool-verified, else summary-only; their content_recall >= 0.5 lane | `SourceEvidence` (SE) / `VerifiedToolResult` (VT) when tool-verified; `HistoricalFact` (HF) otherwise |
| PREFERENCE | soft guidance; summary lane with the lowest pin priority; content_recall >= 0.4 lane | no dedicated kind; soft guidance rides the unprotected summarizer text (the allocator's lowest lane, episodic, is `LowPriorityEpisode` LP) |
| EPISODIC | leaving-window conversation history; `open` items when a task is unfinished; content_recall >= 0.3 lane; their 30% episodic budget split is arbitrary | `LowPriorityEpisode` (LP); unfinished work rides `CurrentTask` / `PendingAction` |
| (no counterpart) | subagent state, tool-result identity - out of their model entirely | `SubagentState` (SS), `VerifiedToolResult` (VT); `RepositoryState` (RS) and `AcceptanceCriterion` (AC) are likewise beyond their model |
| Authority axis | message source in the session vector (system / developer / user / tool / retrieved); their weights 1.5 / 1.25 / 1.0 / 0.75 / 0.6 are an unvalidated prior | role-gated classification: only user-role text yields USER_CONSTRAINT / USER_CORRECTION / SAFETY_OR_PERMISSION_BOUNDARY / ACCEPTANCE_CRITERION; tool output never does (commit `c948905a3`) |

The allocator's priority order (constraints / safety / criteria >
corrections > task / pending > decisions > evidence > repo / subagent >
historical > episodic, commit `c948905a3`) is the committed analogue of
their per-type recall thresholds (0.3 / 0.4 / 0.5) turned into lanes.

## 6. What the reproduction teaches our implementation

- The flat-versus-cliff shape is real and deterministic: pinning
  must-keep material outside the summarizer survives repeated 50 %
  compaction that destroys naive summarization (also the R2-E1 result on
  the production path - `reports/context-rot-r2/BASELINE.md` sections 3
  and 5).
- The floor-versus-B_min interaction is the transferable failure mode:
  repeated 50 % compaction with a hard floor eventually erodes even pinned
  content when the pinned set exceeds the floor (study section 8, cycles
  6-10). The guard adopted for typed_state v2: when `budget < B_min`, stop
  compacting rather than truncating the pinned set - the allocator raises
  `UnsafeCompaction` when mandatory material cannot fit (commit
  `c948905a3`), instead of their behavior of returning items even when
  status is `COMPACTION_UNSAFE` (study section 7, item 4).
- What must NOT be copied naively (study section 7): the regex classifier
  (0.89 F1, English-only, mislabels "IMPORTANT" prose), the arbitrary
  constants, `len(text)//4` token estimates (Optimus has a real estimator),
  FIFO `_take_until` (keeps the OLDEST episodes; a transcript's tail
  matters most), exact-text `_dedupe`, and the drop-not-compress
  operators, which would discard exactly the evidence E4 showed must be
  recovered (`reports/context-rot/FINAL-REPORT.md` 3.5).

## 7. Commands (offline, stdlib + cloned refs)

```
python research/context_rot_r2/repro/typecompact_repro.py
python research/context_rot_r2/repro/typecompact_repro.py --n all --rounds 10 \
  --out research/context_rot_r2/repro/typecompact_repro_full_suite.json
```

Status: SYNTHETIC-DIAGNOSTIC. Their operators, their sample, their
classifier, deterministic baselines; no Optimus pipeline and no model calls.
