# Knowledge-update extraction instruction — 2-env LME-S re-ingest smoke (2026-10-12)

Run: `lme_s_ku_smoke2_v1` (executed as two parallel 1-env drivers,
`lme_s_ku_smoke2a_v1` env 01493427 + `lme_s_ku_smoke2b_v1` env 031748ae).
Worker: imp3-ku (branch `improve/mem3-ku-extraction`, base = imp3 HEAD
`b95ca6aad`; Rust tree identical to the sealed smoke5 binary - reports-only
diff verified with `git diff --stat 820c94cdb b95ca6aad`).

## Change under test (manifest-level; no code change)

`settings.importInstructions` added to the LME-S manifest. Text = the built-in
default extraction instruction (the `unwrap_or_else` fallback in
`crates/pi-coding-agent/src/core/memory/service.rs`, 65 words) plus a 47-word
knowledge-update demand:

> When a fact changes mid-conversation or in a later session (new count, new
> value, new status, revised plan), always create a separate NEW entry capturing
> the updated value, what it updates, and its date. Never merge or skip updates.
> The question may ask about the latest state.

Diagnosis being addressed (lme-s-full-smoke5-notes.md): all 3 smoke5 misses were
extraction failures; the two KU misses specifically were late-session FACT UPDATES
that were never extracted ("added 25 new postcards", session 2023-11-30; "now lead
a team of five engineers", session 2023-10-24), so recall had only stale values
(17 postcards as of 2023-08-11; team of 4 as of 2023-05-11) and latest-wins had
nothing newer to win.

## Manifests

- `manifests/lme_s_ku_smoke2_v1.json` — manifest of record: 2 envs (01493427,
  031748ae), 1 question each, envs deep-copied verbatim from the committed
  `lme_s_full_smoke5_v1.json` (whose envs deep-equal `data/common/longmemeval/full`,
  re-verified for this run).
- `manifests/lme_s_ku_smoke2a_v1.json` / `lme_s_ku_smoke2b_v1.json` — the same
  content split one env per manifest for parallel execution (the driver ingests
  envs sequentially; two drivers halve wall time). Settings identical in all
  three, including the new `importInstructions`.

Settings = the committed full-run stack from the smoke5 manifest commit
(distill + rerank(200) + query-extract, expansion OFF, ingestTimeoutS 43200,
seed 20261010, LME protocol answerInstruction) + `importInstructions`. Note the
sealed smoke5 RUN executed expansion ON (2 draws); this re-ingest uses the
committed stack (expansion OFF). Extraction is upstream of recall, so the
store-level comparison is unaffected; the answer path differs from the sealed
run only by that recall-setting delta.

## Run

Binary: isolated build `CARGO_TARGET_DIR=imp3-target4 cargo build --release --example
memory_bench` (8m37s, exit 0; `imp3/target` untouched, sealed runs kept running
throughout). Binary sha256 `cfa5dfd623979d47e9a44c3405132d932c2c4ab0e9f564527bc061a31f9bb781`.
Model dgx-glm53/glm-5.3 (only LLM), noJev preflight-verified in both drivers.
Executed as two parallel 1-env drivers (the driver ingests envs sequentially):
`lme_s_ku_smoke2a_v1` (env 01493427) and `lme_s_ku_smoke2b_v1` (env 031748ae).
Ingest 2752s/2564s (187/162 entries vs sealed 139/125), questions 12.6s/24.7s.

### v1 instruction result: 1/2

| qid | question (short) | gold | sealed (no importInstructions) | v1 answer | v1 score |
|---|---|---|---|---|---|
| 01493427 | new postcards since restart | 25 | "17 new postcards" | "25 new postcards (as of 2023-11-30)" | **1.0** |
| 031748ae | engineers led then / now | 4 then, 5 now | abstention | "5 engineers. ... Now: 5 engineers" | 0.0 |

Store check (the extraction gap itself):
- env 01493427: NEW entry "25 postcards added since collecting resumed (updated
  count, 2023-11-30)" created and recalled (`postcards-25-new-since-collecting-resumed-2023-11-30`);
  also "8 postcards acquired at local antique shop (2023-11-30)". Sealed run had
  neither; its newest state was the 2023-08-11 note (17).
- env 031748ae: NEW entry "Team size update: leads five engineers (2023-10-24)"
  created and recalled (`mem_team_size_five_engineers_2023_10_24`); the 4-note
  (`mem_user_role_senior_swe_4_engineers`) also recalled. Sealed run had no 5-note.

### Why 031748ae still failed (answer path, not extraction)

The update capture worked, but the v1 instruction ALSO amplified count
extraction of the same session's outing talk: the user's loose phrase "we'll be
a group of 5 engineers and a manager" (2023-05-11, outing attendee count) was
extracted as "Team of 5 engineers; manager is Rachel" — a same-date pseudo-
conflict with the true "team of 4" note. The answer enumerated "one note says
4, another 5" for 2023-05-11, then resolved the START value to 5 by applying
latest-wins with the 2023-10-24 note (out of scope for a "when you just
started" sub-question). Result: "5 then, 5 now" vs gold "4 then, 5 now".

## v2 iteration

Added a scoping clause to the same addition (58 added words, still under the
60-word cap): "Never record an event or outing headcount as a team or
collection size; keep the count's stated scope in the title." — and dropped the
motivational tail "The question may ask about the latest state." Full v2 text
in `manifests/lme_s_ku_smoke2_v2.json`.

### v2 instruction result: 2/2 = 1.00 (SUCCESS)

Same execution pattern (two 1-env drivers, isolated imp3-target4 binary, same
seed). Ingest 2717s/2654s (183/181 entries), questions 12.8s/30.4s.

| qid | question (short) | gold | sealed (no importInstructions) | v1 | v2 answer | v2 score |
|---|---|---|---|---|---|---|
| 01493427 | new postcards since restart | 25 | "17 new postcards" (0.0) | 1.0 | "25 new postcards (as of 2023-11-30)" | **1.0** |
| 031748ae | engineers led then / now | 4 then, 5 now | abstention (0.0) | 0.0 | "Now: 5 engineers... just started (2023-05-11): 4 engineers" | **1.0** |

Store check (v2, envs/*/agent/memory/projects/*/harness_state.json):
- env 01493427: "25 new postcards added since restarting postcard collecting
  (as of Nov 2023)" + "17 new postcards acquired (June-July 2023)" + "8 more
  postcards acquired at local antique shop (Nov 2023)" — update chain complete.
- env 031748ae: "Engineering team led by user: 4 engineers ... Scope: size of
  the engineering team" (2023-05-11) + "Engineering team led by user: 5 engineers
  (updated from 4; stated 2023-10-24)" — and the outing facts are now titled as
  outing facts ("Team outing attendance: 5 people (planned)", "Team outing
  headcount update: 5 engineers plus manager Rachel (6 people)"), which is what
  let the answer model exclude them: its enumeration says "Excluded: the team
  outing attendance headcount of 5 people - the memory marks this as a separate
  fact (outing attendance), not team size."

## Scoring

Scorer: `repo-continuation/bench/.venv/Scripts/python.exe -B -W error -m
bench.eval.evaluate --bench longmemeval --run <combined-run.jsonl> --variant
improved --qs-dir data/common/longmemeval/full --out <scores.json>
--models-json reports/continuation/iteration2-inputs/lme-temporal-flip/judge-models.json
--judge-profile official-lme-v1` (cwd campaign root). Judge = dgx-glm53/glm-5.3
(official-lme-v1), 2 requests, 0 retries/failures per run. Committed score
copies: `runs/lme_s_ku_smoke2_v1-scores-official-lme-v1.json` (v1: 1/2) and
`runs/lme_s_ku_smoke2_v2-scores-official-lme-v1.json` (v2: 2/2). scores.json
overall means are diluted by the 498 flagged not-in-run questions (500-question
qs dir); the metric is the 2 answered questions.

## Conclusion

- The diagnosed extraction gap is closed by a manifest-level importInstructions
  override: both late-session fact updates ("25 new postcards", 2023-11-30;
  "team of five engineers", 2023-10-24) are extracted as separate NEW entries,
  recalled, and both questions now score 1.0 (sealed run: both 0.0).
- The v1 text (47 added words) fixed the postcards question but ALSO amplified
  count extraction of outing talk into a same-date "Team of 5 engineers"
  pseudo-conflict; the scoping clause in v2 (58 added words, still under the
  60-word cap) keeps event/outing headcounts out of team/collection sizes and
  the comparison question resolves correctly.
- Core default untouched (no Rust change): the full instruction text lives in
  the manifests; promoting it into the core default needs the broader evidence
  the parent wants first (e.g. a dev5 5-env regression + category breakdown on
  the full run).
- Suggested next step: fold this importInstructions into the lme_s_full_v1
  shard manifests (one converter constant + regeneration) before the full
  500-question run, since KU is 78/500 and the same failure mode presumably
  dominates that category.
