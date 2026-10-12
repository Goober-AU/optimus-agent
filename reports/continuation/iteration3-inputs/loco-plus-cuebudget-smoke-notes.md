# Prospective-cue budget raise — lp_18 re-smoke (2026-10-12)

Run: `loco_plus_cuebudget_smoke` — 1-env smoke (lp_18, 25 sessions, 511 events, 1 question),
settings identical to `loco_plus_dev5_improved_v3` / `loco_plus_cuefix_smoke` (recall +
distill + rerank(200) + extract + expansion(2) + `recallProspectiveIndex: true`),
model dgx-glm53/glm-5.3, noJev verified.

## Finding 0: the imp3 budget commit (c3736613d) fixed the wrong call site

- `c3736613d` raised the cue budget only in the EXTENSION path
  (`extensions/builtin/memory.rs` -> `recall_llm_text`, 512 -> 2048): the path a real
  agent session exercises via the `/memory` command.
- The bench driver wires `import_apply` through `create_memory_host_handlers`
  (`core/memory/service.rs`), whose `generate_cues` closure calls
  `complete_prospective_cues` — still capped at `max_tokens: 512`.
- Empirical confirmation: the first launch (isolated build of imp3 HEAD `820c94cdb`)
  ran ~40 min and coverage stayed flat at 26-32% (28/88 at abort), the same band as the
  cuefix smoke. Aborted, output dir deleted (fresh-output rule); evidence kept at
  `runs/loco_plus_cuebudget_smoke-prefix.log` + `-prefix-evidence.json`.

## The fix under test (this branch)

- `service.rs` `complete_prospective_cues`: `max_tokens` 512 -> 2048 — the same
  rationale as `c3736613d`, applied at the call site the bench driver actually uses.
- Isolated rebuild: `CARGO_TARGET_DIR=imp3-target3` (5m57s, exit 0). `imp3/target`
  untouched; the `imp3` worktree stayed clean throughout.

## Results

| Run (lp_18) | Cue budget (driver path) | Cue coverage | Question (official-lp-v1) | Ingest tokens |
| --- | --- | --- | --- | --- |
| dev5 v3 sealed (pre-fix parser) | 512 | 122/758 = 16.1% | not reached | — |
| cuefix smoke (`1fd5fa9eb` build) | 512 | 182/701 = 26.0% | 1.0 (correct) | 210,378 in / 483,840 out |
| **cuebudget smoke (this run)** | **2048** | **655/662 = 98.9%** | **1.0 (correct)** | 219,997 in / 463,894 out |

- Coverage gate (>60%): **PASS at 98.9%** (3.8x vs the 26.0% cuefix baseline).
- 655 cued entries carry 4-5 cues each (647x5, 8x4); the 7 uncued are 4 fact/plan
  entries and 3 photo-description entries (1.1% residual failures).
- Ingest: 5,993,798 ms (99.9 min; cuefix: 102.7 min) — the budget raise is
  latency-neutral.
- Token cost: **cost-neutral, not +0.7M**. Output per entry 700.7 vs 690.2 tokens
  (463,894/662 vs 483,840/701): at 512 every failed cue call still burned its full
  512-token budget on hidden reasoning, so raising to 2048 (which stops early at
  ~630 tokens with finish_reason=stop) did not add net output tokens. The cuefix
  notes' "+1k output tokens/entry" estimate did not materialize.
- Question (Cognitive cue: security app vs paper recovery codes): recall retrieved
  `evan-recovery-codes-physical-card` + `evan-work-email-lockout`; the answer engaged
  the evidence directly; judge label `correct` (score 1.0), fresh judge call 4.6 s.
  A `scores.json` (fresh judge call, identical result) appeared in the output dir at
  18:55 — this re-run independently re-scored via the official command (cache hit).

## Verdict for the LP full launch

- The 2048-token cue budget DOES lift coverage from 26.0% to 98.9% on the driver path —
  the mechanism is validated and the prospective index would be fully lit.
- BUT `c3736613d` as merged into `improve/memory-iteration-3` does NOT take effect for
  the bench driver: a rebuild of imp3 HEAD alone would still cue only ~26%.
  Cherry-pick this branch's `service.rs` change (or amend imp3) into the launch build.
- Cost of the raise: ~zero (same output tokens/entry, same ingest wall-clock). No LP
  full-run cost adjustment is needed for the cue budget.

## Artifacts

- Manifest: `reports/continuation/iteration3-inputs/manifests/loco_plus_cuebudget_smoke.json`
- Run outputs (external, untracked): `reports/continuation/iteration3-inputs/runs/loco_plus_cuebudget_smoke/`
- Driver log: `reports/continuation/iteration3-inputs/runs/loco_plus_cuebudget_smoke.log`
- Scores: `reports/continuation/iteration3-inputs/runs/loco_plus_cuebudget_smoke-scores-official-lp.json`
- Pre-fix abort evidence: `runs/loco_plus_cuebudget_smoke-prefix.log`, `runs/loco_plus_cuebudget_smoke-prefix-evidence.json`
