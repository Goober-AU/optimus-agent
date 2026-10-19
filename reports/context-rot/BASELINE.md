# BASELINE — code baseline, measurement setup, and what every number rests on

Scope: the baseline and the measurement conditions behind every number quoted in the other
reports. Worktree `C:/Users/openclawuser/optimus-rot-20261018`. No new measurement.

## 1. Code baseline

| field | value | source |
| --- | --- | --- |
| HEAD commit | `8ae54da78d94920b543e22567403b7e939cb5bd2` | `git rev-parse HEAD` |
| HEAD subject | Merge pull request #178: fix compaction persistence deadlock [skip ci] | `git log -1` |
| HEAD date | 2026-10-19 10:23:24 +1100 | `git log -1` |
| dirty files, snapshot | 8 modified, 4 untracked (all PR content) | `git status --porcelain`, captured before these reports were written |

The brief named `3b2583f15` as the baseline. That commit exists here
(`3b2583f15e178051fb43d0adf6fa8b608a89d569`, "Merge pull request #172 from
Goober-AU/improve/full-harness-validation", 2026-10-18 09:05:49 +1100) and is an ancestor of
HEAD, but it is not the base this campaign used: the worktree is at `8ae54da`. Measured
distance: `git rev-list --count 3b2583f15..HEAD` = 20 commits, 6 of them first-parent merges
(PRs #173 to #178). The brief calls the gap 14 commits; that figure is not reproducible from
this worktree, so the counted distance is used and no number depends on the older commit.

PR content under test (uncommitted at report time):

```
 crates/pi-coding-agent/src/core/agent_session.rs              |   1 +
 crates/pi-coding-agent/src/core/compaction/compaction.rs      | 132 +++++-
 crates/pi-coding-agent/src/core/compaction/mod.rs             |   1 +
 crates/pi-coding-agent/src/core/compaction/utils.rs           | 168 +++++-
 crates/pi-coding-agent/src/core/mod.rs                        |   1 +
 crates/pi-coding-agent/src/core/settings_manager.rs           |  21 ++
 crates/pi-coding-agent/src/modes/interactive/interactive_mode.rs | 80 ++-
 crates/pi-coding-agent/src/modes/interactive/native_host.rs   |  17 +-
 8 files changed, 395 insertions(+), 26 deletions(-)
```

Plus untracked `compaction/task_state.rs` (924 lines), `core/context_health.rs` (409
lines), `.changes/compaction-task-state-and-context-health.md`, and `research/context_rot/`.

The worktree is being edited while these reports are written. A later `git status` also shows
`native_host_status_tests.rs` modified (5 insertions, 2 deletions), after the snapshot above.
The diffstat and the five shipped items in section 4 of `FINAL-REPORT.md` are that snapshot;
any file added afterwards is outside this report.

## 2. Model under test

- Endpoint: one DGX endpoint, model id `dgx-deepseek41/deepseek-v4.1-flash-1017`
  (the lab drivers default `--model` to `deepseek-v4.1-flash-1017`).
- Sampling: the request sends `model`, `messages`, `max_tokens`, `stream:false` only. No
  `temperature`, no `seed` field, so temperature is the endpoint default. One seed per item
  (`reps=1`), derived by `hash((kind, length, position, distractor, rep)) & 0xffff`.
- Output caps: 200 tokens for NIAH items, 300 for probes, `floor(0.8 * 16384) = 13107` for
  compaction summaries (legacy summary budget, CC:732-747).
- ONE model on ONE endpoint: no second model, provider or quantization variant.

## 3. Measurement caveat that applies to every score

The endpoint leaks reasoning text into `content` before a `</think>` delimiter. Every
driver therefore scores the text AFTER the last delimiter (`strip_think`); each NIAH
record names the rule that fired (`extraction_rule`), and the compaction drivers do not.
Effect sizes:

| run | `after_think_delimiter` | `last_line` fallback | rule not recorded |
| --- | --- | --- | --- |
| `c1/niah-main.jsonl` | 104 / 105 | 1 / 105 | 0 |
| `c1/niah-distract.jsonl` | 21 / 42 | 21 / 42 | 0 |
| `e1`, `e2`, `e4` probes | - | - | all |

The fallback fires when the model never emits the delimiter, usually because the 200-token
cap ended the turn inside its deliberation. Such a record's "answer" is deliberation text.
See `CONTEXT-ROT-CURVES.md` for the one main-arm record this affects and `BUG-LEDGER.md` M1.

The E1/E2/E4 drivers strip the delimiter but record no `extraction_rule`. Inspection finds 3
probe answers that are unsplit deliberation text ("We need answer one short line ..."):
`e1` ledger cycle 1 and cycle 3 on `q10_remaining`, and `e2` ledger cycle 1 on `o1`. The
first and the third were scored correct only because the gold token appears inside the
deliberation; the second is the single ledger miss in E1, scored wrong because the cap cut
the line before the gold token. Treat any single-item probe difference as unresolved until
the answer text is read.

## 4. Corpus and protocol provenance

- Corpus is synthetic: deterministic seeded filler in four registers (prose, log, code,
  table), from `probe/niah.py`. Chroma's corpora were not redistributed, so the protocol
  shape (length x position x distractor difficulty) is reproduced, not their text.
- NIAH grid: 7 nominal lengths (2000 to 128000) x 5 positions (0.0 to 1.0) x 3 needlet kinds
  (exact lexical, semantic paraphrase, causal), 1 repetition.
- The harness inside the tree is `research/context_rot/` (see its `README.md`): offline,
  seeded, gold-isolated, with `correct: null` for invalid cells. It produced the
  in-tree selftest run only (`runs/20261019T001229Z-selftest`). All real-model artifacts
  live outside the tree in `C:/Users/openclawuser/optimus-rot-lab/runs/`, which is why
  this report cites lab JSONL, not in-tree run directories.
- The lab arms are Python mirrors of the production path, not the production path: E1/E2
  rebuild the request shape, the prompt selection and the ledger (`exp/compact_exp.py`,
  `exp/ledger.py`, "mirror of the Rust implementation"), and E4 reimplements both excerpt
  policies (`exp/e4_tool.py`). No measurement ran through the compiled Rust binary, so the
  Rust implementation is unit-tested and not end-to-end measured.
- Item seeds are not reproducible across processes: `probe/niah.py` derives them with
  `hash((kind, length, position, distractor, rep)) & 0xffff`, and Python salts string
  hashing per process (two runs of the same expression returned 2568 and 37643 during this
  review). Each item is only regenerable from the `seed` value recorded in its JSONL row.
  See `BUG-LEDGER.md` M5.

## 5. Two baselines, never to be confused

1. Code baseline `8ae54da`: what the PR diff is taken against (`BUG-LEDGER.md`).
2. Measurement baseline: the same fixture without the shipped mechanism, namely the
   `baseline` arm of E1/E2 (production prompts, `summary_update_policy = "off"`) and the
   `old` excerpt policy of E4.

## 6. Not measured

Official scorers were not run: Chroma context-rot, NoLiMa, RULER, LongBench, CompactBench,
RepoQA, SWE-bench, Terminal-Bench. No score here is comparable to a published number from any
of them.

## 7. Decision

Baseline: reference only. No baseline arm is kept or reverted.
