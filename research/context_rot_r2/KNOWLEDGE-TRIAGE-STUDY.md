# KNOWLEDGE TRIAGE STUDY — reference research for Optimus context-rot iteration 2

Worker W5. Date: 2026-10-19. Status: complete, offline only.

## 0. Provenance

| item | value |
| --- | --- |
| Upstream repo | https://github.com/searchsim-org/cikm26-knowledge-triage (cloned to `refs/cikm26-knowledge-triage`) |
| Pinned commit | `a6ceb01a3368cee25ef7ebcf05ebdab8c9be24a4` (2026-08-26, default branch) |
| License | Apache-2.0 (`LICENSE`, "Copyright 2026 The Knowledge Triage Authors") |
| Paper | "The Compaction Cliff in Long-Running AI Agent Memory", CIKM '26, DOI 10.1145/3799682.3840567 |
| Paper claim scope | 20 stratified configs from AgentArtifactCorpus; LLMs: gpt-5.4-mini, claude-sonnet-4-6 (via Claude Code SDK), claude-opus-4-7; embedder octen-embedding-8b |
| Dataset | AgentArtifactCorpus (AAC): 396,934 artifacts from 54,628 public GitHub repos; gated HF release; 327-file stratified sample shipped in-repo (`data/sample/`) |
| Python | 3.11+; package deps numpy/scipy/sklearn/networkx/tenacity/dotenv/openai |

Everything in sections 1-5 describes THEIR code and numbers. Section 6 maps it to
Optimus. Section 7 lists what must not be copied naively. Section 8 summarizes our
deterministic reproduction (see `repro/typecompact_repro.py`).

## 1. The type system (kb.py, classifier.py)

Five operational types, decided per knowledge item (a non-empty line of a config):

| type | meaning | examples from their code/prototypes |
| --- | --- | --- |
| `CONSTRAINT` | rule bounding agent behavior; violation causes safety/correctness failure | "Never commit secrets", "Patient is allergic to penicillin" |
| `PROCEDURAL` | step-by-step instruction | "Run `pytest -x --tb=short`", "Deploy via `make deploy`" |
| `BELIEF` | factual assertion about the world | "The API uses REST, not GraphQL" |
| `PREFERENCE` | soft guideline | "Prefer 2-space indentation" |
| `EPISODIC` | record of a past event | "Yesterday we refactored the auth module" |

Data model: `Item(text, type, topic="root", scope=(), source="")` with
`token_estimate = max(1, len(text)//4)`. `KnowledgeBase.b_min` is Theorem 1's
minimum safe budget: total tokens of CONSTRAINT+PROCEDURAL items.

A second, orthogonal axis exists: `Authority` (SYSTEM 1.50 > DEVELOPER 1.25 >
USER 1.00 > TOOL 0.75 > RETRIEVED 0.60, `classifier_cascade.AUTHORITY_WEIGHTS`),
used by `risk_score = P(CONSTRAINT) x authority_weight`. This is the only place
where provenance is modeled, and it is only used for risk weighting, not for
retention policy.

## 2. Classifier design (classifier.py, classifier_cascade.py, cascade_singleton.py, safety_margin.py)

### 2.1 Regex reference classifier (`classifier.classify_instruction`)

Order of precedence (first match wins): CONSTRAINT patterns (negation/modals,
"CRITICAL|MANDATORY|REQUIRED|IMPORTANT", always+must/shall, uppercase
imperatives) then PROCEDURAL (command starts, fenced bash blocks, `$ ` prompts,
npm/yarn/pip/cargo/go), then EPISODIC (yesterday/today/last week...), then
PREFERENCE (prefer/recommend/...), else BELIEF. README reports 0.89 F1 on
constraints against the AAC annotation set. This is the offline baseline and the
one our reproduction uses.

### 2.2 Cascade (their production classifier, LLM-backed)

1. Regex, accepted at confidence >= 0.85.
2. Encoder: cosine to per-type prototype centroids (10 hand-written exemplars per
   type, embedded once, softmax temperature 0.1), accepted at posterior >= 0.55.
3. LLM adjudicator (gpt-5.4-mini, one-word answer) for the rest.
4. Abstain-as-hard: posterior < 0.40 routes to CONSTRAINT with a 0.5 confidence
   and `abstained=True` (fail-safe to the pinned lane).

`cascade_singleton` memoizes every classification to
`experiments/results/classifier_cache.jsonl`, keyed `sha1(version::text)[:16]`;
the shipped cache holds ~50k adjudicated labels, which is how their experiments
re-run cheaply. `reclassify_kb` overwrites regex labels with cascade labels.

### 2.3 SafetyMargin (safety_margin.py)

Types an item by counterfactual safety instead of grammatical form: prompt an
LLM for `margin(i) = max_a [P(unsafe | a, K \ {i}) - P(unsafe | a, K)]` on a
0.0-1.0 scale (parse with regex, threshold 0.5); margin >= 0.5 -> CONSTRAINT,
else BELIEF. Reported to lift declarative-fact constraint recall 0.62 -> 0.90 on
a 50-rule x 4-form adversarial set, at 880 ms/item. This is a model-dependent
claim; we did not and cannot verify it offline.

## 3. Preservation scoring (preservation.py)

Per-type distortion checks, all deterministic string tests:

| type | check | threshold |
| --- | --- | --- |
| CONSTRAINT | `constraint_preserved`: all key tokens survive as substrings | d_C = 0 (exact key tokens) |
| PROCEDURAL | `procedure_preserved`: first 2 `verb + argument` commands survive | d_P <= eps |
| BELIEF | `content_recall` >= 0.5 | |
| PREFERENCE | `content_recall` >= 0.4 | |
| EPISODIC | `content_recall` >= 0.3 | |

`constraint_key_tokens` extracts at most 5 distinctive tokens: the first
negation/modal match plus tokens containing `._/-`, starting uppercase, or
length >= 6, minus a stopword list. `content_recall` is the fraction of
distinctive content words (length >= 3, not stopword) of the original that appear
in the output.

## 4. The three operators (operators.py)

### 4.1 TypeCompact (Algorithm 1)

`type_compact(kb, budget) -> (KnowledgeBase, status)`:

1. Pin all CONSTRAINT items; deduplicate PROCEDURAL items
   (whitespace/lowercase-normalized text equality).
2. If pinned tokens > budget: return the pinned set with status
   `COMPACTION_UNSAFE` (caller must check; the items are still returned).
3. Spend the remainder 50% BELIEF / 20% PREFERENCE / 30% EPISODIC via
   `_take_until` (in-order greedy, first item that would overflow the sub-budget
   stops that lane).
4. Status `ok`.

No LLM, no rewriting: "compaction" is selection. Dropped items are gone, not
summarized.

### 4.2 TypeDecompose (Algorithm 2)

Group by `topic`, chunk each topic to `partition_budget`, then replicate every
CONSTRAINT into each partition whose items' topics intersect the constraint's
`scope` (default scope = its own topic). This is what makes locality violations
structurally impossible (their 0% vs 93% under uniform chunking).

### 4.3 TypeRetrieve (Algorithm 3)

`type_retrieve(corpus, query, k, relevance_fn, in_scope_fn)`: pin every in-scope
CONSTRAINT into the result, then fill the remaining slots by relevance over all
other items.

## 5. What their numbers measure vs what transfers to Optimus

### 5.1 Their committed numbers (results/*.json, mean over their 20-config runs)

Multi-round stability (50% per round, 5 rounds, recall vs ORIGINAL constraint
set; `results/multiturn_stability.json`):

| round | 1 | 2 | 3 | 4 | 5 |
| --- | --- | --- | --- | --- | --- |
| TypeCompact | 1.000 | 0.9603 | 0.9579 | 0.9579 | 0.9579 |
| vanilla gpt-5.4-mini | 0.4766 | 0.2756 | 0.1792 | 0.1229 | 0.1021 |
| vanilla Sonnet 4.6 (/compact) | 0.5283 | 0.2123 | 0.1556 | 0.1267 | 0.1011 |

TypeRetrieve (`results/typeretrieve_vs_vanilla.json`): recall@50 in scope
1.000 for TypeRetrieve_dense vs 0.7045 (gpt-5.4-mini) / 0.7255 (Sonnet 4.6);
recall@5 is 0.3973 for TypeRetrieve (it pins constraints ahead of relevance, so at
tiny k relevance suffers).

Single-shot tradeoff at 50% (`results/vanilla_vs_typecompact.json`, 30 configs):
TypeCompact_cascade keeps constraint 1.000 / procedural 0.9998 but belief 0.5205
/ preference 0.554 / episodic 0.9162 (macro 0.7981); gpt-5.4-mini keeps
constraint 0.9569 with macro 0.9833. The type-aware policy buys constraint
safety by discarding beliefs wholesale; the vanilla LLM loses constraints slowly
in one round and quickly across rounds.

### 5.2 What these numbers are, precisely

- Input artifacts are static Markdown agent *configurations* (CLAUDE.md-style
  rules files), segmented line-by-line. No tool calls, no code, no subagent
  traffic, no interleaved user conversation.
- "Constraint recall" is their key-token substring check (section 3), not a
  semantic judgment, and not enforceability.
- The cliff arm is LLM paraphrase drift under a compress-to-N-tokens prompt; the
  round-to-round budget halves (50% of current size, floor 64 tokens), then
  `truncate_to_tokens` hard-cuts to 4 chars/token.
- Their classifier is in the loop for both arms (labels decide what counts as a
  constraint), cached from the cascade.

### 5.3 What transfers to Optimus, and what does not

Transfers (structure, not parameters):

- The core invariant: identify a small set of must-not-lose items, pin them
  outside the summarization path, and check the pinned budget explicitly
  (an `UNSAFE` style status rather than silent loss).
- Per-type retention policy instead of one uniform policy; their Theorem 1
  (B_min = constraint + procedural tokens) is the same shape as our ledger caps.
- Multi-round recall measured against the round-0 set, at fixed ratio, is the
  right measurement frame for our E2-style multicycle drift work (already
  implemented in iteration 1, `research/context_rot/multicycle.py`).
- Abstain-as-hard (unknown -> treat as constraint) is a defensible default for
  a classifier we do not fully trust.

Does not transfer directly:

- Live histories are not flat rule files. Optimus compacts conversation
  transcripts with tool results, file excerpts, subagent reports. There is no
  ground-truth type per line; classification must run on messages and content
  blocks, with authority from the message source (system/user/tool), which their
  model only touches via the unused-in-operators Authority axis.
- User corrections and supersession: their types have no ordering, no
  invalidation. A later "actually, use port 8081" must retire an earlier belief.
  Iteration-1 `task_state.rs` already models this (superseded decisions,
  retractions); Knowledge Triage has nothing comparable.
- Their operators never rewrite: dropped items are simply gone. Our compaction
  summarizes the leaving window. A pin-plus-summarize hybrid needs the pinned
  items to survive summarization *and* remain consistent with the summary body.
- Their scope/topic model (strings on items) is trivial; Optimus scopes are
  files, sessions, tools, subagents.

## 6. Mapping table: their types -> Optimus types

Optimus iteration-2 types named in the W5 brief: USER_CONSTRAINT,
USER_CORRECTION, SAFETY_OR_PERMISSION_BOUNDARY, plus the iteration-1 ledger
categories from `reports/context-rot/FINAL-REPORT.md` (constraints, decisions,
superseded, evidence, anchors, open, retractions; caps 12/10/8/8/12/6/4, 320
chars/item). Mapping:

| Knowledge Triage | Optimus iteration-2 type | Notes |
| --- | --- | --- |
| CONSTRAINT (user-stated rule) | USER_CONSTRAINT | pin-for-survival lane; their `type_compact` step 1 |
| CONSTRAINT (system/policy/permission) | SAFETY_OR_PERMISSION_BOUNDARY | their Authority SYSTEM/DEVELOPER weight 1.5/1.25; never enters the summarizable window |
| CONSTRAINT as invalidation of earlier state | USER_CORRECTION | not modeled upstream; maps to supersession pass in `task_state.rs` |
| PROCEDURAL | ledger `decisions` + `anchors` (commands, exact tool invocations) | their `procedure_preserved` = command survives verbatim |
| BELIEF | session facts; ledger `evidence` when tool-verified, else summary-only | their content_recall >= 0.5 lane |
| PREFERENCE | soft guidance; summary lane with the lowest pin priority | content_recall >= 0.4 lane |
| EPISODIC | leaving-window conversation history; `open` items when a task is unfinished | content_recall >= 0.3 lane; their 30% episodic budget split is arbitrary |
| (no counterpart) | subagent state, tool-result identity | out of their model entirely |
| Authority axis | message source in the session vector (system / developer / user / tool / retrieved) | their weights 1.5/1.25/1.0/0.75/0.6 are an unvalidated prior |

## 7. What is deliberately simple and must NOT be copied naively

1. The regex classifier (0.89 F1 on their annotation set): grammar-based,
   English-only, mislabels "IMPORTANT" prose as CONSTRAINT, and its precedence
   order is an arbitrary safety-first choice. Copying it wholesale into Optimus
   would pin noise and inflate B_min.
2. Arbitrary constants everywhere: cascade thresholds 0.85/0.55/0.40, softmax
   temperature 0.1, split ratios 50/20/30, per-type recall thresholds
   0.3/0.4/0.5, 5 key tokens, chars-per-token=4. None are tuned for our corpus.
3. `token_estimate = len(text)//4` is a crude proxy; Optimus has a real
   estimator (`estimate_context_tokens`). Mixing the two inside one budget
   computation would silently double-count.
4. `type_compact` returns items even when status is `COMPACTION_UNSAFE`; a
   caller that forgets the status check believes it got a safe result.
5. `_take_until` is FIFO greedy: no recency, no importance, no relevance. For a
   conversation transcript the tail (most recent) matters most; their episodic
   lane would keep the OLDEST episodes.
6. `_dedupe` is exact normalized-text equality; near-duplicate procedural
   variants survive.
7. `type_decompose` replicates constraints into partitions *after* chunking
   without re-checking the per-partition budget, so a partition can exceed
   `partition_budget` after replication (the docstring says "we re-balance
   below" but no re-balancing code exists at `a6ceb01`).
8. `constraint_preserved` is a substring test over <= 5 heuristic tokens: a
   summary can "preserve" a constraint it merely quotes out of context, and
   fail one that was correctly enforced but reworded. It is a cheap proxy, not a
   safety oracle.
9. Prototype exemplars in `classifier_cascade.PROTOTYPES` are
   medical/legal/retail flavored; centroids inherit that bias.
10. The operators drop content rather than compress it; on Optimus histories
    (code, tool output) that would discard exactly the evidence our E4 result
    showed must be recovered (see `reports/context-rot/FINAL-REPORT.md` 3.5).

## 8. Our deterministic reproduction (summary)

`repro/typecompact_repro.py` re-implements their `multiturn_stability` loop with
their own operators, on their shipped 327-file AAC sample, regex classifier, 10
cycles at ratio 0.50, seed 11. Their LLM vanilla arm is replaced by three
deterministic baselines (naive_head = their `hierarchical_summarize` proxy,
naive_tail = their `temporal_window`, naive_truncate = their
`truncate_to_tokens` alone); their LLM numbers are carried in the JSON as a
reference block only. Full protocol and deviations are recorded inside the JSON.

n=20 (their protocol size) and n=197 (all qualifying sample files) agree:

| arm | c1 | c3 | c5 | c8 | c10 |
| --- | --- | --- | --- | --- | --- |
| typecompact (n=20) | 1.000 | 0.8872 | 0.8408 | 0.8408 | 0.8408 |
| naive_head (n=20) | 0.3663 | 0.0607 | 0.0071 | 0.0071 | 0.0071 |
| naive_tail (n=20) | 0.6334 | 0.1126 | 0.0538 | 0.0538 | 0.0538 |
| naive_truncate (n=20) | 0.3663 | 0.0607 | 0.0071 | 0.0071 | 0.0071 |
| typecompact (n=197) | 1.000 | 0.8863 | 0.8482 | 0.8482 | 0.8482 |
| naive_truncate (n=197) | 0.3992 | 0.0859 | 0.0284 | 0.0277 | 0.0277 |

Reading: the qualitative claim (flat vs cliff) reproduces deterministically. Our
TypeCompact numbers sit below their 0.96 because the regex classifier labels a
different (larger) constraint set, and because cycles 6-10 push the budget to the
64-token floor where `truncate_to_tokens` starts cutting pinned constraints:
repeated 50% compaction with a hard floor eventually erodes even pinned content
when the pinned set exceeds the floor. That interaction (floor vs B_min) is
worth an explicit guard in our implementation: when `budget < B_min`, stop
compacting rather than truncating the pinned set.

Command (from the worktree root, offline, stdlib + cloned refs):

```
python research/context_rot_r2/repro/typecompact_repro.py
python research/context_rot_r2/repro/typecompact_repro.py --n all --rounds 10 \
  --out research/context_rot_r2/repro/typecompact_repro_full_suite.json
```
