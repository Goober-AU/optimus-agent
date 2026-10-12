# LME-v2 static-environment ingestion wall — capture-fidelity fix

Branch: `improve/mem3-lmev2-ingest` (worktree `imp3-lmev2ing`, based on
`improve/memory-iteration-3` @ 5e8bcf28b).

## Root cause (measured)

The three static-environment questions of the dev5 set never had their gold
facts in memory because the LME-v2 converter artifacts cap every accessibility
tree at 2000 chars. The driver and the memory import are lossless from there
on — the text is already gone before the driver runs.

Measured on the dev5 v3 run (`lme_v2_dev5_improved_v3`, env `web`):

| quantity | value |
|---|---|
| sessions / observation events | 100 / 1737 |
| states truncated at 2000 chars | 1720 / 1737 |
| raw axtree chars | 65,792,253 |
| stored axtree chars | 3,458,283 (5.3%) |
| `toolbox` occurrences in stored session text | 0 |
| `hide this forum` occurrences | 0 |
| `moderator` occurrences | 0 |
| same strings in import source chunks (`jobs/*.source.jsonl`) | 0 |
| same strings in the 201 memory store files | 0 |

Where the gold facts live in the RAW trajectories (`data/trajectories.jsonl`,
web haystack): `toolbox` first appears at tree offset 2309, `hide this forum`
at 2362, and the only own-profile `Moderator on:` section in the haystack
(traj `b1c719fb`, state 2) sits at offset 33927 of a 34491-char tree. All
three are beyond the 2000-char cap. The converter records the cap per env in
`notes.axtree_cap_chars` (2000 in every shipped artifact; the converter's
code default of 8000 never governed these files).

## Fix (opt-in raise, fail closed, default preserved)

1. `bench/converters/run_converters.py`: new `--lme-v2-axtree-cap N` flag,
   applied before the family modules are imported (the converter resolves
   `LME_V2_AXTREE_CAP` at import time). The chosen cap is recorded in each
   env's `notes.axtree_cap_chars` and the family manifest stats.
2. `crates/pi-coding-agent/examples/memory_bench.rs`: new manifest setting
   `captureAxtreeMaxChars` (alias `capture_axtree_max_chars`), default `None`
   = unchanged behavior. When set, the driver requires every `lme_v2` env to
   record `notes.axtree_cap_chars >= captureAxtreeMaxChars` and fails closed
   at parse time with a regeneration instruction (source text truncated at
   conversion time cannot be restored at run time). The request is recorded
   in the env telemetry as `capture_input_policy.capture_axtree_max_chars_requested`.
3. `scripts/lmev2_static_mini_manifest.py`: mini-manifest generator that
   re-captures only the trajectories containing the static gold facts at a
   chosen cap and embeds the three static questions.

Cap guidance from the raw tree-length distribution (web haystack, n=1737
states): p50 = 16,201 chars, p90 = 133,854, max = 322,509. Cap 8000 retains
20.1% of chars (covers the `Toolbox` and `Hide this forum` facts at offsets
2309-2728); the own-profile `Moderator on:` fact needs >= 33928. Full
retention would ingest 65.8M chars (~16.5M tokens) for the web env alone —
~19x the cap-2000 artifacts — so the raise stays an explicit per-run decision.

## Validation

- Unit tests: `capture_axtree_max_chars_defaults_aliases_bounds_and_fail_closed_validation`
  (driver) and `test_lme_v2_axtree_cap_flag_*` (converter CLI, fixtures).
- Negative control (real 2000-capped dev5 manifest + `captureAxtreeMaxChars:
  40000`): driver refuses at parse time — see below.
- Mini run `lmev2_static_mini_cap40k` (3 sessions: `1ad1af4b`, `b1c719fb`,
  `2e66df34`; 24 states; 0 truncated at cap 40000; 275,529 raw axtree chars;
  questions 36fe48ef/499488a6/f928c075): one driver ingest, then the gold
  strings are checked in the session files, import chunks and memory store —
  results below.

## Validation results (2026-10-12)

Negative control (real dev5 v3 manifest, cap 2000, `captureAxtreeMaxChars:
40000`): driver refuses at parse time —

```
error: invalid manifest JSON: env web recorded axtree cap 2000 < captureAxtreeMaxChars 40000; source text truncated at conversion time cannot be restored at run time — regenerate inputs with LME_V2_AXTREE_CAP >= 40000
```

Mini run `lmev2_static_mini_cap40k` (3 sessions, 24 states, 0 truncated,
275,529 raw axtree chars; 25 ingest LLM calls, 23 memory entries, 805 s;
DGX GLM-5.3 only, no Jev):

| checkpoint | `toolbox` | `hide this forum` | `Moderator on:` |
|---|---|---|---|
| dev5 v3 run (cap 2000): session files / import chunks / store | 0 / 0 / 0 | 0 / 0 / 0 | 0 / 0 / 0 |
| mini run (cap 40000): session files | 14 | 13 | 1 |
| mini run: import source chunks | 14 | 13 | 1 |
| mini run: memory store notes | 1 (user-page Toolbox links) | 0 | 1 (own-profile moderator section, five forums listed) |

Scores (deterministic lme_v2 evals): 0/3 — but the failure mode changed from
"unknown"/blank/"Account" with zero relevant recall to near-misses grounded in
the newly captured facts: 36fe48ef answered "two" (user-page Toolbox; the
forum-page Toolbox with six links was captured but not distilled into a note),
f928c075 answered "Toolbox" (right page, adjacent section — the gold
`Moderator on:` note exists in the store and was recalled), 499488a6 hit the
output-length cap (blank). The ingestion wall itself is closed; the residual
misses are extraction granularity and answer-side selection.

Full details: `runs/lmev2_static_mini_cap40k/README.md`.
