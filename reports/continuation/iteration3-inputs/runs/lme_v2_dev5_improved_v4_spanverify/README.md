# LME-v2 dev5 v4 span-verify run

Run: `lme_v2_dev5_improved_v4_spanverify` (variant improved, env web, 100 sessions, 5 questions).
Manifest: `manifests/lme_v2_dev5_improved_v4_spanverify.json` — identical to the v3 dev5 manifest
except `run_id`, `output_dir`, and `settings.lmev2SpanVerify: true` (AgentRunbook-C scoped-search
tool loop: read_summary/search_trace/get_state, 6-turn budget, fail-open).

Driver log: `../../../../../../iteration3-inputs/runs/lme_v2_dev5_improved_v4_spanverify.log`
(shared reports tree, not committed). Ingest: 100/100 sessions, 550 entries, 19107704 ms.
Model: dgx-glm53/glm-5.3 only (no Jev; preflight-verified).

## Scores (bench.eval.evaluate, qs_web.jsonl)

overall mean 0.20 (v2 baseline 0.40); procedure 0.5/2, static-environment 0.0/3.

| qid | category | gold | v2 baseline | v4 span-verify |
|---|---|---|---|---|
| 36fe48ef | static-environment | six | 0.0 ("unknown", forced zero) | 0.0 (preamble text, no answer) |
| 499488a6 | static-environment | none | 0.0 (blank) | 0.0 (preamble text, no answer) |
| f928c075 | static-environment | moderator | 0.0 ("Account") | 0.0 (preamble text, no answer) |
| 99b6e22f | procedure | B | 1.0 | 1.0 ("B") |
| a59d50f7 | procedure | three, Proceed to Checkout, Next, Place Order | 1.0 | 0.0 (em-dash separator instead of comma) |

## Finding

All 5 questions ended with span-verify outcome `turn_budget_exhausted` (6 turns, 6 tool calls each,
no tool errors). The loop works mechanically (searches returned results, session-scoped
search + get_state were used) but the 6-turn budget is spent before the model stops
investigating; the captured final text is a tool-call preamble for the three static-environment
questions. No question reached a `verified` outcome. a59d50f7 regressed vs v2 on formatting only
(em-dash vs comma separator under `norm_phrase_set_match` with separators ",;").

Conclusion: AgentRunbook-C scoped search with a 6-turn budget did NOT convert the
static-environment failures (still 0/3) and the run scored 0.20 vs the 0.40 baseline.
