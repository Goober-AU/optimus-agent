# Prospective-cue parser fix — lp_18 smoke validation (2026-10-12)

Run: `loco_plus_cuefix_smoke` — 1-env smoke (lp_18, 25 sessions, 511 events, 1 question),
settings identical to `loco_plus_dev5_improved_v3` (recall + distill + rerank(200) + extract
+ expansion(2) + **recallProspectiveIndex: true**), model dgx-glm53/glm-5.3, noJev verified.

## What was validated

- Isolated binary: `cargo build --release --example memory_bench` with
  `CARGO_TARGET_DIR=imp3-target2` (13m07s, exit 0). `imp3/target` untouched; the sealed
  dev5 v3 run kept running from it throughout.
- Driver commit: `4c87c9e78` = improve/memory-iteration-3 HEAD, includes fix `b1df68a2d`
  (prose-preamble-tolerant cue parser + stricter JSON-only prompt).

## Results

| Run (lp_18) | Binary | Cue coverage | Question |
| --- | --- | --- | --- |
| dev5 v2 (feature OFF) | old | 0/687 = **0%** | "not mentioned" |
| dev5 v3 sealed (pre-fix parser) | old | 122/758 = **16.1%** | not reached yet |
| **cuefix smoke (this run)** | isolated fix build | **182/701 = 26.0%** | **official-lp-v1: 1.0 (correct)** |

- Ingest: 6159.8 s (~103 min), 701 entries, 28 LLM import calls, 210k in / 484k out tokens.
- Question (Cognitive cue: security app vs paper recovery codes): recall retrieved
  `evan_email_lockout_recovery_card` — an entry that **has cues** — and the answer engaged
  the evidence directly. Official judge label `correct` (score 1.0), fresh network call
  (judge_cache 26492c53, 2.6 s).
- All 182 cued entries carry exactly 5 cues; cue quality on the gold entry was on-target
  (oblique wallet/slip-of-paper situations matching the question's vocabulary).

## Remaining failure mode (NOT the parser): cue-call token budget

Direct endpoint probes with the exact cue-request shape:

- `max_tokens: 512` (current `complete_prospective_cues` budget): GLM-5.3 spends the whole
  512 on hidden reasoning (`reasoning_tokens: 512`), returns `finish_reason: "length"`
  with **empty content** -> `prospective_cues_from_text("")` -> None -> failed, skipped.
- `max_tokens: 2048`: `finish_reason: stop`, clean bare JSON with 5 cues (525 reasoning +
  ~104 visible tokens).

So the parser fix is real but only part of the gap: pre-fix failures were mostly
empty/truncated text + preamble-rejected JSON; post-fix, the dominant remaining cause is
the 512-token budget being consumed by reasoning before any visible text. The judge path
already documented this exact GLM-5.3 behavior and raised its budget (LP_JUDGE_MAX_TOKENS).

## Verdict on gating the LP full launch

- The parser fix works as intended (0% -> 16.1% came from feature enablement; the fix
  lifted an 11-16% ceiling to 26.0% on the same env/settings, 1.6x) and removed a real
  failure class (prose-prefixed JSON now parses; 5/5 cues per cued entry).
- But 74% of entries still get no cues for the budget reason above. Launching the full LP
  run now would leave most of the prospective index dark.
- Recommendation: **one more small change before the LP full launch** — raise the cue-call
  `max_tokens` in `complete_prospective_cues` (512 -> 2048; probe shows 2048 is sufficient
  and clean) and optionally add a single retry. Then re-smoke (cheap: 1 env) — expected
  coverage should jump from 26% toward near-full. The parser fix should NOT gate alone;
  the budget fix should ship with it.
- Cost note: cue calls at 2048 tokens add ~1k output tokens/entry (701 entries -> ~0.7M
  output tokens/env) — within the campaign's measured ingest scale (484k output for this
  env already).

## Artifacts

- Manifest: `reports/continuation/iteration3-inputs/manifests/loco_plus_cuefix_smoke.json`
- Run outputs (external, untracked): `reports/continuation/iteration3-inputs/runs/loco_plus_cuefix_smoke/`
  (`run.jsonl`, `env.jsonl`, log at `...runs/loco_plus_cuefix_smoke.log`, scores at
  `.../scores-official-lp.json`)
