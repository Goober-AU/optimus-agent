# LME-v2 static-environment mini run (capture cap 40000)

Run: `lmev2_static_mini_cap40k` (variant improved, env web, 3 sessions, 3 questions).
Purpose: cheap validation of the static-environment ingestion-wall fix
(`improve/mem3-lmev2-ingest`). The historical 2000-char axtree cap removed every
static-env gold fact before the driver ran; this run re-captures only the three
haystack trajectories that contain them at cap 40000 (effectively full for these
trees: 24 states, 0 truncated, 275,529 raw axtree chars).

Sessions: `1ad1af4b` (forum-page Toolbox at tree offset 2728 + `Hide this forum`),
`b1c719fb` (own-profile `Moderator on:` section at offset 33927 of a 34491-char
tree — impossible at cap 2000 or 8000), `2e66df34` (redundant toolbox/hide hits).
Questions: 36fe48ef (gold six), 499488a6 (gold none), f928c075 (gold moderator) —
gold-free declarations byte-identical to the dev5 v3 run manifest.

Driver: isolated build (`CARGO_TARGET_DIR=imp3-lmev2ing-target`, commit on this
branch). Model: dgx-glm53/glm-5.3 only (no Jev; preflight-verified).
Ingest: 25 LLM calls, 23 memory entries, 805 s — minutes, not the ~5.3 h of a
full 100-session dev5 env.

## Retention results (the wall under test)

| checkpoint | `toolbox` | `hide this forum` | `Moderator on:` |
|---|---|---|---|
| dev5 v3 run, 100 sessions (cap 2000) | 0 | 0 | 0 |
| this run, session files (cap 40000) | 14 | 13 | 1 |
| this run, import source chunks | present | present | present |
| this run, memory store notes | 1 entry (user-page Toolbox links) | 0 | 1 entry ("MarvelsGrantMan136 is a moderator on: sci_fi, cmu_lti, Cyberpunk, PlantsForCatParents, karaoke") |

The f928c075 gold fact (own-profile `Moderator on:` section) is now a memory
note with the five moderated forums — structurally impossible before the fix.
Telemetry: `capture_input_policy.axtree_cap_chars=40000`,
`capture_input_policy.capture_axtree_max_chars_requested=40000`.

## Scores (deterministic lme_v2 evals; `bench.eval.evaluate`)

overall mean 0.0, static-environment 0/3 — but the failure mode changed
completely vs the dev5 v3 run (unknown/blank/Account with zero relevant
recall):

| qid | gold | v3 (cap 2000) | this run (cap 40000) |
|---|---|---|---|
| 36fe48ef | six | "unknown" (forced zero) | "two" (recalled the user-page Toolbox; the forum-page Toolbox note was not distilled) |
| 499488a6 | none | blank | blank at output cap (length stop) |
| f928c075 | moderator | "Account" | "Toolbox" (right page, adjacent section) |

Conclusion: the ingestion wall is fixed — the gold facts now survive capture
into sessions, import chunks and (for the moderator/toolbox sections) memory
notes, and recall surfaces them. The remaining misses are extraction
granularity (no note for the forum-page Toolbox's six links) and answer-side
selection/formatting, which are separate from this fix.

## Negative control (fail closed)

The real dev5 v3 manifest (100 sessions, cap 2000) plus
`settings.captureAxtreeMaxChars: 40000` is refused by the driver at parse time:

```
error: invalid manifest JSON: env web recorded axtree cap 2000 < captureAxtreeMaxChars 40000; source text truncated at conversion time cannot be restored at run time — regenerate inputs with LME_V2_AXTREE_CAP >= 40000
```

(constructed as: dev5 v3 manifest + captureAxtreeMaxChars 40000, new run_id;
reproduce with `scripts/lmev2_static_mini_manifest.py` + jq or see
`../lmev2-static-capture-fix.md`).

## Reproduce

```
python scripts/lmev2_static_mini_manifest.py --cap 40000 \
    --run-id lmev2_static_mini_cap40k \
    --out-dir reports/continuation/iteration3-inputs
MEMORY_BENCH_MODELS_JSON=<dgx models.json> <memory_bench binary> \
    reports/continuation/iteration3-inputs/manifests/lmev2_static_mini_cap40k.json
python -m bench.eval.evaluate --run <out>/runs/lmev2_static_mini_cap40k/run.jsonl \
    --bench lme_v2 --qs-dir <qs dir with gold> --variant improved
```
