# ITERATION-3 UPSTREAM PR DRAFT (do-not-push packet)

Prepared 2026-10-12 15:28 from worktree `imp3` @ `improve/memory-iteration-3` (HEAD `3021d82c7`); **updated 2026-10-12 evening at HEAD `366e01d14`** with the day's validated results (gate2 PASS, cue chain 99%, KU extraction 2/2, LME-S smoke5 2/5, LMEv2 v3/v4 readouts, v5 retry). Base `origin/main` `8da6b60b9`.
Flow: fork **Goober-AU/optimus-agent** -> upstream **telemusai/optimus-agent** `main`, same as PR #131 (`feat/memory-iteration-2`).
Scope: 43 commits `origin/main..HEAD` (25 non-merge), 65 files, +140,530/-68. PR-relevant subset (§4 allowlist): 11 files, +4,979/-68 — everything else is tracked reports/docs that stay local (§3(d)). Largest committed blob in the range: 494 KB (a verification copy of one run manifest) — no generated run data in git (see §6).
Invariants honored throughout: all LLM work (driver, judge, helpers) on **dgx-glm53/glm-5.3 only**, **no Jev**; third-party repos static-inspected only, never executed.
**Status: DRAFT ONLY — do not push, do not open the PR. Only full-suite results open the corresponding claims (§7); the dev5/smoke results banked today do NOT open the PR by themselves.**

---

## 1. Title

```
feat(memory): iteration-3 recall pipeline — temporal discipline, prospective-cue reliability, verbatim source spans, span-verify + LongMemEval-M benchmark support
```

## 2. Branch

- Upstream PR branch: **`feat/memory-iteration-3`** on fork `Goober-AU`, targeting `telemusai:main`.
- Built from `improve/memory-iteration-3` (`366e01d14` at this update; use the then-HEAD at build time) by rebase/squash onto `origin/main` (`8da6b60b9`) with the §3 file filter and §4 commit grouping. Rebase in a dedicated worktree (the packet's `imp3-prdraft`); never rebuild or touch `imp3`'s sealed `target/` while live runs execute. This docs update itself was committed directly on `improve/memory-iteration-3` in `imp3` (docs-only, parent-authorized) because the branch keeps moving under live merges.
- Draft/coordination: this document, `reports/continuation/iteration3-research/PR-ITERATION3-DRAFT.md` on `improve/memory-iteration-3` (original: branch `improve/mem3-pr-draft`, worktree `imp3-prdraft`).

## 3. Inventory — every change classified

`git log --oneline origin/main..HEAD` = 43 commits; `git diff --stat origin/main...HEAD` = 65 files, +140,530/-68 (the insertions are dominated by tracked verification records — gate manifests, smoke manifests, logs, score copies — all excluded from the PR per §3(d)/§4).

### (a) Core memory — `crates/pi-coding-agent/src/core/memory/*`, `src/core/extensions/builtin/memory.rs` (+786/-59 net)

| File | Delta | Carries |
|---|---|---|
| `core/memory/service.rs` | +487/-29 | temporal discipline (recall "at" date labels, header guidance), source-span attach after import apply, prospective-cue parser fix (prose-preamble tolerance) + stricter JSON-only prompt, cue-call budget 512->2048 on the driver path (`complete_prospective_cues`), cue-generation loop hardening |
| `core/memory/search.rs` | +184/-10 | latest-wins tie-break (newest `created_at` first), `sourceSpan` words searchable at 0.5 weight (title 3.0 / body 1.0), span render cap 200c |
| `core/memory/store.rs` | +56 | new `recallSourceSpans` setting (validate/apply/merge/round-trip + tests), default false |
| `extensions/builtin/memory.rs` | +59/-20 | activation/fallback test updates for the new recall contract; extension-path cue budget 512->2048 (`recall_llm_text`); rerank + query-expansion helper budgets 512->2048 (both call sites) |

### (b) Bench driver — `crates/pi-coding-agent/examples/memory_bench.rs` (+1552/-9)

- `recallProspectiveIndex` / `recallSourceSpans` / `lmev2SpanVerify` manifest-settings plumbing (requested-vs-effective settings audit keeps the driver honest).
- `import_apply` timeout scales to the ingest budget when `recallProspectiveIndex` is on (cue generation is apply-phase work; the original 120 s window killed LP v3 run #1 on a 687-entry env).
- LME-v2 span-verify tool loop (AgentRunbook-C transplant): bounded tool loop with three deterministic tools (`read_summary` / `search_trace` / `get_state`) over the session JSONL the driver wrote at ingest; verifies exactly ONE interaction state, appends "Verified interaction evidence:" to the question; fail-open on loop error, fails closed at manifest parse on mixed/no-ingest manifests; per-turn 16,384-token budget. **v5 shape (after the v4 negative readout): 12-turn budget + 300 s wall clock (v4's slowest question averaged 19.1 s/turn — the old 240 s cap would starve 12-turn searches), forced final answer** (when the budget ends in a tool call, one more tool-less interaction with a fixed "reply NOW with the final answer or UNVERIFIED" prompt; the loop now always terminates in a verified citation or UNVERIFIED; `forced_final` recorded) **and a list-separator note in the loop prompt** (v4's one pure-formatting loss was an em-dash instead of a comma).
- Driver example tests: 59 `#[test]` + 10 `#[tokio::test]` = **69 green** (68 pre-existing + 1 new forced-final test at `38475f41f`).

### (c) Converters / launchers / scripts — `bench/` + `scripts/` (+2,641)

| File | Delta | What |
|---|---|---|
| `bench/converters/longmemeval_m.py` | +395 | LongMemEval-M adapter (500-session haystack variant, same question/scoring family as S); deterministic dev5 + 25-env full shards + per-env manifests; ANSWER_INSTRUCTION = validated LME protocol variant (a) |
| `bench/converters/longmemeval_s_full.py` | +576 | full 500-q LongMemEval-S generator: 20 shards x 25 envs + combined + smoke5; dev5 improved_v2 settings + protocol (a) + `recallQueryExtract` + `answerMaxTokens` 8192; expansion OFF (E3-06 A/B parity), `ingestTimeoutS` 43200; cross-validated vs canonical common set + historical reference manifest |
| `scripts/lmem_parallel_shard_launcher.py` | +539 | N concurrent `memory_bench` shard drivers: staggering, per-shard logs with live tailing, progress parsing, retries, kill/cleanup (validated c2 dry-run, 37 jobs / 15 min, 0 fatals) |
| `scripts/lp_full_shard_launcher.py` | +775 | LP 401 launcher: shard glob discovery, `--validate-only` settings-uniformity check, dry-run mode, cost model; carries only a 4-word marker string of the Cognitive instruction for validation (license-clean, see §6.8) |
| `scripts/lmem_env_shard_splitter.py` | +355 | re-shards 20 shard manifests into 500 per-env manifests (~6 MB each) for ~500-worker parallelism; `--check-only` validation (counts, qid multiset, path budget) |
| `bench/README.md` | +1 | documents the LME-M input file (`longmemeval_m.json`, HF `longmemeval_m_cleaned.json`, ~2.7 GB) |

Not in this PR (unmerged branches, parent decides): the LP full 401-pair generator (`improve/mem3-lp-full` @ `d374fb1de` — generator reads the official Cognitive instruction from data-side reference manifests, embeds no prompt text) and the LME-v2 axtree capture-cap fix (`improve/mem3-lmev2-ingest` — converter `--lme-v2-axtree-cap` + driver `captureAxtreeMaxChars` opt-in, fail-closed). If merged before the PR build, add them to §4 group "Bench tooling" / "Driver" respectively.

### (d) Docs / reports — TRACKED on the dev branch, **EXCLUDED from the PR** (stay local)

Verified tracked in `improve/memory-iteration-3` but dropped when building `feat/memory-iteration-3` (52 of the 65 files):

- `docs/LME-M-ENV-SHARDS.md`, `docs/LMEM-SHARD-LAUNCHER.md`, `docs/LP-FULL-LAUNCHER.md` (research docs)
- `reports/continuation/iteration3-research/` — `EXPERIMENT-LEDGER.md`, `FULL-RUN-DECISION.md`, plus this PR draft
- `reports/lmesfull/` — `FULL-SET-VERIFICATION.md`, `driver-parse-validation.txt`, `manifests-receipt.json`
- `reports/continuation/iteration3-inputs/` — the LoCoMo gate2 packet (`locomo/`: GATE-REPORT.md, GATE-RESULT.json, LAUNCHES.json, declarations jsonl, s0-s2 manifests, scores, scripts); the smoke/validation manifests (`lme_s_full_smoke5_v1`, `lme_s_ku_smoke2*` x7, `loco_plus_cuefix/cuebudget`); the notes files (lme-s-full-smoke5, lme-s-ku-smoke2, loco-plus-cuefix, loco-plus-cuebudget); run logs + committed score copies (LME-S smoke5, KU v1/v2, cuefix, cuebudget + pre-fix abort evidence); the LMEv2 v3 run record (COMPARISON-NOTE.md + scores.json) and v4 span-verify record (README.md, manifest, scores.json)

**License note:** the two LP smoke manifests and `FULL-RUN-DECISION.md` quote the official LoCoMo-Plus Cognitive instruction verbatim — they are dev-branch coordination records and MUST stay out of the PR (see §6.8).

Untracked research docs already stay local (bench-root `reports/continuation/iteration3-research/`: AGENTOS/AGENTRUNBOOK/HINDSIGHT/KUMIHO reports, ANSWER-PROTOCOLS.md — never in git).

## 4. Commit grouping proposal (squash/rebase plan)

Three groups, nine commits (merge commits all squashed away; docs/reports dropped):

| # | Group | Proposed commit message | Source commits (imp3) | Files |
|---|---|---|---|---|
| 1 | Core features | `feat(memory): temporal discipline — recall date labels, latest-wins ordering, header guidance` | `955aafde2` + `7cc251a26` (+`7ff1b2fb2` merge) | `search.rs`, `service.rs`, `extensions/builtin/memory.rs` |
| 2 | Core features | `feat(memory): recallSourceSpans — verbatim source spans per imported memory, searchable at half body weight` | `98725da08` (+`00d6a6a9a` merge) | `store.rs`, `search.rs`, `service.rs` (+ driver flag plumbing hunk) |
| 3 | Core features | `fix(memory): prospective-cue parser tolerates prose preamble + stricter JSON-only prompt` | `b1df68a2d` | `service.rs` |
| 4 | Core features | `fix(memory): raise prospective-cue + recall-helper LLM budgets to 2048 (hidden reasoning starves 512)` | `c3736613d` + `b08efe9f7` + `91445b6ab` | `service.rs`, `extensions/builtin/memory.rs` |
| 5 | Driver | `feat(bench): expose recallProspectiveIndex/recallSourceSpans settings + import_apply timeout scaling in memory_bench` | `f79d5ad1c` + driver hunks of `955aafde2`/`98725da08` | `examples/memory_bench.rs` |
| 6 | Driver | `feat(bench): LME-v2 span-verify bounded tool-loop recall — 12-turn budget, forced final answer, fail-open` | `6ce9f869c` + `38475f41f` (+`1e0dbfd6e`, `d83d31335` merges) | `examples/memory_bench.rs` |
| 7 | Bench tooling | `feat(bench): LongMemEval-M adapter + validated LME answer protocol in run manifests` | `5225b5de1` + `0395ccb68` (+`bdffcad50`, `3021d82c7` merges) | `bench/converters/longmemeval_m.py`, `bench/README.md` |
| 8 | Bench tooling | `feat(bench): LongMemEval-S full 500-q run-manifest generator (A/B-parity settings)` | `98265c980` + `7e022d860` (+`4de53f688` merge) | `bench/converters/longmemeval_s_full.py` |
| 9 | Bench tooling | `feat(bench): full-run shard launchers — LME-M parallel, LP 401, per-env splitter` | `0447c4795` + `1b09425f3` + `21640c4e5` (+`08b568ad6`, `4c87c9e78`, `ec917a26a` merges) | `scripts/lmem_parallel_shard_launcher.py`, `scripts/lp_full_shard_launcher.py`, `scripts/lmem_env_shard_splitter.py` |

Condensed 3-commit variant if the maintainer prefers the PR #131 shape: (1) `feat(memory)` = #1-4, (2) `feat(bench)` driver = #5-6, (3) `feat(bench)` tooling = #7-9.

Excluded entirely: `eaf84452a` (ledger docs), all `reports/**` payloads (incl. the gate/smoke/run records committed after the original draft), all three `docs/*.md` launcher docs, all merge commits.

Delta vs the original 8-commit plan: commit #3 (parser fix) is unchanged; **new commit #4** folds the three budget commits (`c3736613d` extension-path cue budget, `b08efe9f7` driver-path cue budget — the c3736613d/cuebudget-smoke finding was that the driver path needed its own raise — and `91445b6ab` rerank+expansion helper budgets); commit #6 (span-verify) now includes the v5 driver rework `38475f41f`.

## 5. PR body (paste-ready)

### Summary

Third batch of memory-system benchmark findings turned into product fixes, built on the harness from #124 and the recall pipeline from #131. All new behavior is opt-in where it adds cost or output volume; defaults are preserved (`recallSourceSpans` defaults to false; the prospective index and query pipeline behave exactly as #131 shipped them).

- **Temporal discipline in recall** *(validated — regression gate PASS)*: recall entries now carry an `at <created_at>` date label, lexical score ties break newest-first, and the recall header teaches the supersession rule ("when notes conflict about the same fact, the latest timestamp wins"). On the 300-question LoCoMo dev gate (defaults arm, only the binary changed) this is a clean no-regression: cats 1-4 mean F1 0.2766 vs 0.2626 anchor (+0.0141, n.s.), the temporal category +0.0206, overall 0.2148 vs 0.2039 — every gating metric moved up.
- **Prospective-cue reliability** *(validated — 14-16% -> 98.9% coverage)*: the #131 write-time cue generator (`recallProspectiveIndex`) had two silent failure modes on reasoning-tuned models. First, GLM prefixes a prose preamble before the JSON object; the strict parser rejected those responses (measured coverage 11-16%). The parser now locates the first structural brace and parses from there. Second, GLM's hidden reasoning consumes the output budget before any text surfaces; cue and recall-helper calls (`complete_prospective_cues`, rerank, query expansion, both call sites) moved from a 512- to a 2048-token cap. Combined, cue coverage on the LoCoMo-Plus lp_18 validation env went 14-16% -> 26.0% (parser alone) -> **98.9%** (655/662 entries), cost-neutral (~700 output tokens/entry either way — failed 512-truncated calls had already burned their budget on hidden reasoning). The cue mechanism is load-bearing on LoCoMo-Plus-style benchmarks (raw-lexical top-6 gold hit rate ~1%), so this is a correctness fix, not a tuning change.
- **`recallSourceSpans` setting** (default false): on session import, each created entry retains the verbatim turn text of its first cited source id in `metadata.sourceSpan` (400-char cap, no LLM involved). Span words join the searchable corpus at half body weight (title 3.0 / body 1.0), and recall renders a capped 200-char `Source:` line after the entry content. Distilled entries paraphrase away exact names, dates and quantities; the span keeps the original wording retrievable.
- **`lmev2SpanVerify` bench setting** (driver-side, LME-v2 family only, **default off**): before the question prompt is assembled, a bounded tool loop (`read_summary` / `search_trace` / `get_state`) runs over the ingested session JSONL to verify exactly one interaction state and append it as "Verified interaction evidence:". Pure functions over the trace store — no process spawn, no file writes; any loop error leaves the question prompt unchanged (fail-open). After the first readout (6-turn budget exhausted on 5/5 questions with zero tool errors), the loop was reworked: 12-turn budget, 300 s cap, and a forced final answer interaction so the loop always terminates in a verified citation or an explicit UNVERIFIED instead of a dangling search. First scored readout was negative (0.20 vs 0.40 baseline) and is reported as such; the feature stays opt-in while the retry is pending.
- **LongMemEval-M benchmark support**: new converter for the 500-session haystack variant of LongMemEval (same question/scoring family as S; ~1.2M estimated tokens/env vs ~115k for S), emitting deterministic dev5, 25-env full-shard and per-env run manifests. The answer instruction is the validated LME protocol variant (a) (supersession, date scratchpad, counting keep/drop, entity guard, premise-check abstention).
- **LongMemEval-S full-set generator**: 500-question manifests (20 independent 25-env shards + combined + smoke5) at the dev5 improved_v2 settings plus the validated answer protocol, `recallQueryExtract`, `answerMaxTokens` 8192, `ingestTimeoutS` 43200, expansion OFF to match the validated A/B exactly.
- **Full-run launchers**: `lmem_parallel_shard_launcher.py` (N concurrent drivers, per-shard logs, progress tables, retries), `lp_full_shard_launcher.py` (LoCoMo-Plus 401, settings-uniformity validation, cost model) and `lmem_env_shard_splitter.py` (re-shard to one manifest per environment for ~500-worker parallelism).

### Why (technique + sources)

Techniques were extracted from read-only static source studies of four published memory systems; all algorithms are paraphrased, no third-party code copied (licenses re-verified from the pinned reference clones, see §6.8):

- **Hindsight** (vectorize-io, MIT): their 94.6% LongMemEval-S result rests on dual timestamps with latest-mentioned-wins supersession, answer-side counting/entity guards and a visible date scratchpad. That drove the temporal discipline (at-labels + newest-first ties + header rule) and the answer protocol adopted in the LME/LME-M/LME-S manifests.
- **AgentOS** (Apache-2.0): their 70.2% LongMemEval-M report shows K=5 reader evidence beating K=50, rerank pool 25 beating 50, and — decisive for us — that verbatim turn-pair chunks outperform LLM-written memory text while LLM-written stores regressed. That drove `recallSourceSpans` (verbatim first-cited-turn retention at write time, half-weight search arm) and the M-family manifest budgets (6 entries / 8000 chars, rerank pool 25).
- **Kumiho** (kumiho-memory, MIT): their 93.3% LoCoMo-Plus result rests on write-time prospective cues — with raw lexical/bge retrieval the top-6 gold hit rate on this family is ~1.0%, so retrieval is impossible without cues. Our #131 mechanism existed but only 14-16% of entries ever got cues (parser + budget failures above); the two fixes restore near-full coverage, and the first LoCoMo-Plus cognitive question now answers correctly end-to-end (see Evidence).
- **AgentRunbook-C** (longmemeval-v2-official, Apache-2.0): the current LME-V2 leaderboard #1 (74.9% small) uses a per-question sandboxed agent over a normalized trajectory store with scoped regex search and one-state verification. `lmev2SpanVerify` transplants that discipline driver-side as a bounded tool loop with fail-open semantics.

### Evidence

All numbers: model `dgx-glm53/glm-5.3` for driver, judge and helpers; official `longmemeval` judges; no Jev. Dev sets = 5 questions unless noted. Full-suite runs are in flight; only their results will open the corresponding claims.

**Validated (dev-level):**

- **LoCoMo dev300 gate v2 — temporal discipline (PASS)**: 300 questions, 10 envs, 3 shards, improved-defaults arm (all recall helpers off), only the binary changed (temporal-discipline build `ab1e1474` @ `bdffcad50` vs gate-1 anchor `4ece283e`; anchor re-score with the same bench code reproduces the published numbers exactly). cats 1-4 mean F1 **0.2766 vs 0.2626** (+0.0141, p~0.50, n.s.), acc@0.5 23.61% vs 21.46%, overall 0.2148 vs 0.2039. Temporal category +0.0206 (direction as designed); multi-hop -0.0303 (n.s., watchpoint: date-label tokens may crowd short recall windows); open-domain +0.119 (n=15, noise). 300/300 answered; 6 flagged transient rows, worst-case cats 1-4 distortion <0.001. Verdict: merge-safe; new anchor 0.2766 / 0.2148.
- **Prospective-cue chain (lp_18, 1-env smokes, isolated builds)**: coverage 14-16% (pre-fix; 122/758 on the sealed run) -> 26.0% (parser fix, 182/701) -> **98.9%** (parser + 2048 cue budget, 655/662; 4-5 cues per cued entry; 7 residual uncued = 4 fact/plan + 3 photo descriptions). The single Cognitive question — trigger "trusting the little slip of paper in my wallet" (zero lexical overlap with the stored fact) — answered **correctly (official-lp-v1 1.0)** in both smokes via the cued entry `evan_email_lockout_recovery_card`: the first LoCoMo-Plus cognitive point ever scored by this stack (iteration-2 baseline on this family: 0.00, 5/5 abstain). Cost-neutral: 700.7 vs 690.2 output tokens/entry, 99.9 vs 102.7 min ingest. Note the budget fix has two call sites (extension path and the bench driver's service path) — both are in this PR; raising only one leaves the other dark.
- **Knowledge-update extraction instruction (manifest-level, 2/2)**: the LME-S smoke5 misses traced to late-session fact updates never extracted ("added 25 new postcards", 2023-11-30; "now lead a team of five engineers", 2023-10-24). A 58-word `importInstructions` addition (update-capture + scoping clauses, on the existing driver setting, no code change) converted both: postcards 17 -> 25 (1.0) and engineers 4-then-5 (1.0); sealed run had both 0.0. Store checks confirm the update entries are created, titled and recalled. The v1 text without the scoping clause scored 1/2 — it amplified an outing headcount ("group of 5 engineers") into a same-date pseudo-conflict with the true team-of-4 note; v2's scoping clause ("never record an event or outing headcount as a team or collection size") fixes it. Applied to the full LME-S (20 shards) and LME-M (20 shards + 500 per-env) run manifests; core default untouched (promotion deferred to full-run evidence).
- **LME-S answer-protocol A/B (E3-06, frozen stores)**: same stores, same recall settings, only the answer instruction (and `recallQueryExtract`) differ. Old instruction 1.00 (5/5) -> protocol variant (a) **1.00 (5/5)**, non-regressive; +951 output tokens/answer for the visible scratchpad (1293 vs 342 mean), input slightly lower.
- **LME temporal-flip probe (E3-01, frozen replay)**: protocol answers correct with visible date scratchpad, 1.0 vs 1.0 baseline; recall top-entries unchanged; +526 output tokens.
- **LME-S full-stack smoke5 (first full-pipeline S data point, honest 2/5)**: 5 questions off the real 500-q full set (category mix 2 KU + 2 MS + 1 SSU), full stack (distill + rerank(200) + query-extract + expansion(2) + protocol (a)). Scored **2/5 = 0.40**: both KU misses and one MS count miss were **extraction failures, not recall failures** — the model answered exactly what the store contained (17 postcards when the 25-update was never extracted; 1 March appointment when the Mar-3 visit was never extracted; abstention when the 5-engineers update was never extracted). The two KU misses are now fixed by the extraction instruction above (re-ingest 2/2); the remaining miss (first-user-message fact) is open. The protocol behaved as designed (premise-based abstention scored 1.0; plans-vs-attended distinctions; one blank 8192-cap answer rescued by the driver's follow-up). Wall time 3.22 h for 5 envs / 240 sessions (~47.9 s/session) — the basis for the ~33 h full-S estimate.

**Honest negatives (root-caused, follow-ups owned):**

- **LME-V2 dev5 v3 (premise-channel instruction): 0.40 — parity, no gain.** Procedure questions 2/2 (one now exact including the separator), but the three static-environment questions did not convert; the instruction's investigative wording triggers GLM tool-intent narration ("I'll check the workspace for trajectory evidence...") instead of values. Deeper root cause measured on the same run: the LME-v2 converter artifacts cap every accessibility tree at **2000 chars — the static gold facts are dropped before the driver even runs** (1720/1737 states truncated; 65.8M raw chars -> 3.5M stored = 5.3%; "toolbox" / "hide this forum" / "moderator" appear 0 times in stored session text, import chunks and all 201 memory store files; the gold strings live at raw offsets 2309-33927, beyond the cap). Decision: revert this family's answerInstruction to direct-answer framing (keep \boxed{} + flaw-naming for abstention); the capture-cap fix (converter `--lme-v2-axtree-cap` + driver `captureAxtreeMaxChars` opt-in, fail-closed) is in progress and NOT part of this PR. Iteration-3 LMEv2 ledger: expansion ON = +0.20 (banked at 0.40), premise instruction = 0 (revert), span-verify = -0.20 (retry queued).
- **LME-V2 dev5 v4 span-verify: 0.20 vs 0.40 — honest negative at the 6-turn budget.** All 5 questions ended `turn_budget_exhausted` (6 turns, 6 tool calls, ZERO tool errors — the loop is mechanically sound, the searches were on-track, the budget was simply spent before the model stopped investigating) and the captured final text was a tool-call preamble. One additional loss was pure formatting (em-dash instead of comma separator; the boxed parser also keeps raw LaTeX `\text{}` wrappers, which the phrase matcher does not normalize). Decision: keep the feature opt-in/default-off (as shipped); the v5 rework in this PR (12 turns + forced final answer + separator note) plus a data-side instruction fix (plain text inside \boxed{}, never \text{}) is the queued retry.

**In flight (results land before the PR opens or are declared in-flight in the final body):**

- LoCoMo-Plus full 401 (smoke5 leg live on the rebuilt full-chain binary: parser + budgets + span capability), LP dev5 v3 (env 4/5, sealed binary = low-coverage cue stack).
- LongMemEval-M dev5 (env 2/5, first-ever M run) and the LME-M 500 / LME-S 500 full runs (manifests regenerated with protocol (a) + the KU extraction instruction).
- LoCoMo dev300 stack arm (helper stack ON + answer protocol (d), 3 shards, sealed binary).
- LME-V2 v5 span-verify retry (driver committed here; run queued).

### Test plan

- `cargo test -p pi-coding-agent --lib memory`: **120 passed, 0 failed** recorded at the cue-parser fix `b1df68a2d`. The later Rust commits in the range are budget-constant changes (`c3736613d`, `b08efe9f7`, `91445b6ab`) plus the driver v5 rework; re-run the lib suite when building the PR branch and record the fresh count.
- `memory_bench` example tests: **69 passed, 0 failed** (59 sync + 10 tokio) at the v5 commit `38475f41f` (68 pre-existing + 1 new forced-final test).
- Bench converter suite (`python -m unittest discover -s bench/tests` in the converter venv): 74 passed at the LME-M merge.
- `cargo check --locked --workspace --all-targets`: clean at the same gates.
- Compatibility: old settings JSON without the new keys loads unchanged (`recallSourceSpans` serde default false; validate/apply/merge round-trip unit-tested); public tool contracts, ids/scopes and provenance untouched.

### Benchmark deltas

| Benchmark (dev5/smoke unless noted) | Iteration-2 baseline | Iteration-3 candidate | Status |
|---|---|---|---|
| LoCoMo dev300 gate, cats 1-4 mean F1 | 0.2626 (gate1; overall 0.2039) | **0.2766** (temporal cat +0.0206; overall 0.2148; n.s.) | **Validated — gate2 PASS** (E3-02) |
| LoCoMo-Plus cue coverage (lp_18 env) | 14-16% (parser + budget failures) | **98.9%** (655/662; parser fix + 2048 budgets) | **Validated** (E3-03 chain) |
| LoCoMo-Plus lp_18 Cognitive question (official-lp-v1) | 0.0 (abstain; family baseline 0.00, 5/5 abstain) | **1.0** — correct, twice (cuefix + cuebudget smokes) | **Validated — first LP cognitive point** |
| LME-S knowledge-update re-ingest (2 q) | 0/2 (sealed smoke5) | **2/2** with KU extraction instruction (v1 without scoping: 1/2) | **Validated** (manifest-level) |
| LME-S full-stack smoke5 (5 q, real full-set questions) | n/a (dev5 1.00 on a different, easier set) | **2/5 = 0.40**; all 3 misses extraction-side; 2 now fixed by the KU instruction | **Validated — honest first data point** |
| LongMemEval-S, answer-stage A/B on frozen stores (5 q) | 1.00 (5/5), 342 out-tok/ans | **1.00 (5/5)** protocol (a), 1293 out-tok/ans | Validated (E3-06) |
| LongMemEval temporal-flip probe, frozen replay (1 q) | 1.0 | **1.0** (+ scratchpad, +526 tok) | Validated (E3-01) |
| LongMemEval-V2 dev5 (5 q) | 0.40 (improved_v2; 0.20 before #131) | v3 premise-channel **0.40** (parity; narration regression; axtree-cap root cause; revert decided) | **Validated — honest negative** |
| LongMemEval-V2 dev5 span-verify (5 q) | 0.40 | v4 **0.20** (5/5 turn_budget_exhausted, 0 tool errors; 1 formatting loss) | **Validated — honest negative**; v5 retry queued |
| LongMemEval-V2 dev5 span-verify v5 | — | 12 turns + forced final answer + \text{} ban (data-side instruction) | **Pending (run queued)** |
| LoCoMo dev300 stack arm (helper stack ON + protocol (d)) | 0.2766 (new defaults anchor) | stack arm | **In flight** (3 shards) |
| LoCoMo-Plus dev5 v3 (5 envs, sealed binary = low-coverage cues) | 0.00 (5/5 abstain) | prospective + Cognitive instruction | **In flight** (env 4/5) |
| LoCoMo-Plus full 401 (full-chain binary) | — | full run | **In flight** (smoke5 leg live) |
| LongMemEval-M dev5 (5 q) | never run | first M run (converter + protocol) | **In flight** (env 2/5) |
| LongMemEval-M full 500 / LongMemEval-S full 500 | — | full runs (manifests regenerated: protocol (a) + KU instruction) | **In flight / queued** |
| recallSourceSpans effect (any family) | n/a | unmeasured — binary-gated for the live runs; setting off in current stacks | Merged, first measurement pending (E3-05) |

## 6. Risk flags (pre-push checklist)

1. **~6.2 GB of generated run manifests** live at bench-root `reports/continuation/iteration3-inputs/manifests/` (LME-M shards 20 x ~140 MB, per-env 500 x ~6 MB, LP 43, LME-S 22) — **outside the repo**, never staged (verified: `git status` clean in `imp3`; largest blob in `origin/main..HEAD` is 494 KB). The bench-root set now also carries the regenerated LME-M/S manifests (protocol (a) + KU extraction instruction). When building the PR branch, `git add` ONLY the §4 file lists; never `git add reports/` or `git add -A` from a bench-root-adjacent context.
2. `.gitignore` does NOT exclude `reports/` (verification docs are tracked there on the dev branch by design) — the exclusion is procedural, enforced by the §4 path allowlist.
3. **Live runs are executing**: 5 drivers on the sealed binary `ab1e1474` from `imp3/target` (LP dev5 v3, LME-M dev5, LoCoMo stack arm x3) plus the LP full smoke5 on the rebuilt `imp3-target5` binary (full cue chain) and the axtree-cap mini validation on `imp3-lmev2ing-target`. Never rebuild `imp3` (or relink its `target/`) while these run — the linker replacing a locked exe killed a run pair on 2026-10-12. The PR branch build happens in a fresh worktree.
4. **LME-M manifest regeneration — DONE data-side (was: pending at first draft).** All 521 LME-M manifest files (dev5 + 20 shards + 500 per-env) were regenerated and verified carrying protocol (a) + the KU `importInstructions` (spot-checked shards + per-env, 2026-10-12 evening); the LME-S 20 shards were regenerated to A/B parity (expansion OFF, `ingestTimeoutS` 43200) + KU instruction. Regeneration is data-side and NOT part of the PR; the converter constant in this PR emits the same protocol.
5. `lmev2SpanVerify` is fail-open by design AND default-off; its first scored readout was negative (v4 0.20). Document both in the PR body (done above) — the feature ships as experimental opt-in with the v5 rework included.
6. Temporal discipline changes default recall rendering (at-labels, newest-first ties) — behavior change, not opt-in. Covered by updated unit tests; **gate2 PASSED** (no regression; temporal +0.0206). Watchpoint: multi-hop -0.0303 (n.s.) — re-read on the next gate.
7. PR body numbers cite imp3 hashes for traceability; after the squash/rebase the hashes change — re-verify the evidence links reference the campaign reports, not commit hashes, before pasting.
8. **License hygiene (re-verified 2026-10-12 evening)**: the four study licenses confirmed from the pinned reference clones — Hindsight (Vectorize AI) MIT, AgentOS Apache-2.0, kumiho-memory MIT, longmemeval-v2-official Apache-2.0; study-only, no code copied. LoCoMo-Plus upstream prompts stay OUT of the repo: the verbatim INSTRUCTION_COGNITIVE text exists only in (a) data-side manifests at bench-root (untracked) and (b) dev-branch reports/smoke manifests — both excluded from the PR by the §4 allowlist; `scripts/lp_full_shard_launcher.py` carries only a 4-word marker string for settings validation; `bench/THIRD_PARTY_NOTICES.md` still declares the CC-BY-NC LoCoMo evaluator / LoCoMo-Plus prompt exclusion. If the LP full generator branch (`improve/mem3-lp-full`) is merged for the PR, re-check: its converter reads the instruction from data-side reference manifests and embeds no prompt text (verified).
9. **Answer-format fragility (LME-v2 scoring)**: the boxed parser keeps raw LaTeX wrappers and the phrase-set matcher splits only on `,;` — GLM's `\text{}`-wrapped / em-dash-separated boxed answers score 0 despite correct content. The v5 mitigations are instruction-side (data-side \text{} ban) and prompt-side (driver separator note, in this PR). If this family matters upstream, consider scorer-side normalization in a follow-up (not this PR).
10. The cue-budget raise has TWO call sites (extension path + driver service path). Both are raised in this PR; keep them in sync in any future edits (the driver path alone decides bench-run coverage — measured: extension-only raise left the driver at 26-32%).

## 7. Pending — do not open the PR before these land (or mark them in-flight in the final body)

**Banked today (validated dev-level results — necessary, not sufficient; they do NOT open the PR by themselves):** gate2 PASS (0.2766 vs 0.2626), cue-chain smokes (26.0% -> 98.9%, lp_18 correct twice), KU extraction 2/2, LME-S smoke5 2/5 (honest), LMEv2 v3 0.40 parity + axtree root cause, LMEv2 v4 0.20 honest negative.

**Standing rule: only full-suite results open the corresponding claims.**

| Item | Gate for | State at this update |
|---|---|---|
| LoCoMo-Plus full 401 (full-chain binary) | LP efficacy claim | smoke5 leg live; full launch per the LP launch plan |
| LME-M full 500 (per-env) | first M-family claim | dev5 leg live (env 2/5); manifests regenerated (protocol + KU) |
| LME-S full 500 | full-S claim | queued; manifests regenerated (protocol + KU, A/B parity) |
| LP dev5 v3 scores (R1) | low-coverage-stack readout + LP full gate | env 4/5 on the sealed binary |
| LME-M dev5 (R4) | gates LME-M full | env 2/5 |
| LoCoMo dev300 stack arm | helper-stack + protocol (d) readout | 3 shards live |
| LME-V2 v5 span-verify retry | span-verify claim (else ship default-off with the documented negative) | driver committed; run queued |
| axtree capture-cap fix (`improve/mem3-lmev2-ingest`, unmerged) | static-env conversion follow-up | mini validation run live; parent merges |
| Type router (worker D) | NOT in this PR | still in flight; follow-up PR if it lands |

After those: the full runs are campaign work, not PR blockers, but the PR cites them as planned/in-flight per the FULL-RUN-DECISION packet.

---

*Prepared by the iteration-3 PR-draft task (original: worktree `imp3-prdraft`, branch `improve/mem3-pr-draft`; evening update: docs-only, committed directly on `improve/memory-iteration-3` in `imp3` under parent authorization). Sources: `git log/diff origin/main..HEAD` on `imp3` @ `366e01d14`, `reports/continuation/PLAN.md` (iteration-3 sections), `EXPERIMENT-LEDGER.md`, `FULL-RUN-DECISION.md`, the committed gate/smoke/run reports under `reports/continuation/iteration3-inputs/` (GATE-REPORT.md, lme-s-full-smoke5-notes.md, lme-s-ku-smoke2-notes.md, loco-plus-cuefix/cuebudget-smoke-notes.md, lme_v2_dev5_improved_v3/COMPARISON-NOTE.md, lme_v2_dev5_improved_v4_spanverify/README.md), `reports/lmesfull/manifests-receipt.json`, and the pinned reference-clone LICENSE files.*
