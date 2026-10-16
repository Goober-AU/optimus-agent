# OCC-2/OCC-3 Campaign Baseline: Real-Model Task Accuracy (OCC-0 vs OCC-1)

Worker: occ2-taskbench. Worktree: `improve/occ2-occ3-communication` (base v0.1.32).
All numbers in this file come from live runs through the local model gateway
(`http://127.0.0.1:43120/ollama/v1/chat/completions`, OpenAI format) on
2026-10-16. No simulator, no cached answers. Artifacts:
`research/taskbench/runner.py`, `research/taskbench/taskbench_runs.jsonl`
(raw per-task records), `research/taskbench/results_taskbench.json`
(aggregates).

These are the reference task-accuracy numbers for the campaign: every later
dialect (OCC-2) or latent mode (OCC-3) gets compared against these cells for
end-to-end task success on the same suite.

## 1. Protocol

Two logical agents per task, both real models behind the gateway:

- Agent A (speaker, `glm-5.3`) receives ONLY the task `sender_input` and
  produces a communication message for Agent B. A never sees `receiver_task`.
  - MODE=occ0: plain English, hard limit 120 words.
  - MODE=occ1: OCC-1 compact structured English (`OCC/1` header + KEY=VALUE
    lines), strictly parsed with the runtime codec
    (`prime-agent-runtime/src/rlm/occ1.py`); one counted retry on parse
    failure.
- Agent B (listener, `glm-5.3` or `glm-5.3-flash`) receives ONLY the message
  from A plus the `receiver_task`, and answers. B's system prompt is
  byte-identical in both modes (it says the message may be plain English or
  KEY=VALUE structured text), so B `prompt_tokens` differences between modes
  measure the message encoding cost on the real server-side tokenizer.
- Scoring: `research/tasks/checkers.py` against `gold_answer`, strict
  (B's stripped content as-is). A lenient diagnostic (last non-empty line;
  newline-to-comma for family D set answers) is recorded separately and never
  used as the headline.
- Retries: one format retry for A (occ0 word-limit violation, occ1 parse
  failure, empty output) and B (empty output). Transport errors: up to 3
  attempts, recorded separately. WRONG ANSWERS ARE NEVER RETRIED, so the
  gold answer cannot leak into the protocol.
- Determinism: temperature 0, seed 42, no other sampling.
- Because the speaker does not know the listener, one A call per
  (mode, task) serves both listener variants: 100 A calls + 200 B calls.

Run matrix: MODE {occ0, occ1} x LISTENER {glm-5.3, glm-5.3-flash} on the DEV
split, first 10 tasks per family (A-E), 50 tasks per cell, 200 task-units,
exactly 300 gateway calls. No cell was cut below 10 per family.

Deviations from the task instructions (both recorded, neither affects
comparability):

- B `max_tokens` = 1024 instead of the suggested 200: gateway
  `usage.completion_tokens` includes hidden reasoning, and easy-task B calls
  already spend 130-150 completion tokens. A 200 cap risks empty visible
  content. No call was truncated (all `finish_reason=stop`). A `max_tokens`
  = 2048.
- Gateway `usage.completion_tokens` includes hidden reasoning tokens
  (verified against the exposed `reasoning` field), so completion counts are
  a server-cost proxy only, never a wire-content claim. Prompt tokens are
  real server-side counts of what was sent.

## 2. Headline scorecard

Accuracy (strict) per mode x listener x family, n=10 tasks each cell:

| Mode | Listener | A | B | C | D | E | Total |
|---|---|---|---|---|---|---|---|
| occ0 | glm-5.3 | 9/10 | 10/10 | 9/10 | 10/10 | 10/10 | 48/50 (96%) |
| occ0 | glm-5.3-flash | 9/10 | 10/10 | 9/10 | 10/10 | 10/10 | 48/50 (96%) |
| occ1 | glm-5.3 | 10/10 | 10/10 | 10/10 | 10/10 | 10/10 | 50/50 (100%) |
| occ1 | glm-5.3-flash | 10/10 | 10/10 | 10/10 | 10/10 | 10/10 | 50/50 (100%) |

By difficulty (occ0, pooled listeners): easy 40/40, medium 40/42,
hard 16/18. occ1: 60/60 at every difficulty.

Both listeners gave byte-identical correctness patterns in every cell: the
two occ0 failures failed with both listeners, with the same answer text.
Lenient extraction changed nothing (lenient == strict on all 200 units), so
no failure is answer-wrapping; the two occ0 failures are content-form
mismatches (section 5).

## 3. Token cost (real server-side tokenizer)

Per-call means over 50 tasks per mode. `A prompt` = A system + briefing;
`B prompt` = B system + message + question (byte-identical template across
modes, so the B-prompt delta is the message encoding cost).

| Metric | occ0 | occ1 | occ1 - occ0 |
|---|---|---|---|
| A prompt tokens | 222.3 | 312.3 | +90.0 |
| B prompt tokens (glm-5.3) | 239.0 | 254.3 | +15.3 |
| B prompt tokens median | 239.0 | 228.0 | -1.0 |
| A completion tokens (incl. reasoning) | 591.4 | 727.3 | +135.9 |
| B completion tokens (incl. reasoning) | 88.8 / 77.6 | 80.4 / 73.0 | -8.4 / -4.6 |
| Message chars (mean) | 406.2 | 373.3 | -32.8 |
| Message words (mean) | 61.4 | 36.0 | -25.4 |

(glm-5.3 / glm-5.3-flash listener columns where they differ.)

B-prompt token delta (occ1 - occ0) per family, glm-5.3 listener:

| Family | mean delta | median delta | reading |
|---|---|---|---|
| A fact handoff | -15.1 | -15.0 | occ1 cheaper |
| B state update | +32.1 | +13.5 | occ1 more expensive |
| C procedural | -25.0 | -25.0 | occ1 cheaper |
| D multi-fact | +94.7 | +73.0 | occ1 much more expensive |
| E calculation | -10.0 | -12.5 | occ1 cheaper |

Overall: occ1 B-prompt cheaper on 25/50 tasks, mean +15.3 tokens, median
-1.0 tokens.

### Is OCC-1 actually cheaper on the wire? Honest answer: it depends.

- Where facts are scalar key/value pairs (families A, C, E), the OCC-1
  speaker output is genuinely smaller: message chars 139 vs 236 (A), 251 vs
  393 (C), 227 vs 308 (E), and the listener-side prompt shrinks by 10-25
  tokens.
- Where the content is record-shaped, the glm-5.3 speaker often explodes one
  record into one field PER ATTRIBUTE (D-dev-001: 48 fields,
  `F1_SERVICE=... F1_TEAM=... F1_STATUS=...`), which is MORE expensive than
  prose: family D message chars 650 vs 510, B prompt +94.7 tokens. Packing
  style is inconsistent across tasks: D-family occ1 field counts range 6-48
  for structurally identical inventories (compact one-field-per-service
  packing costs ~570 chars, per-attribute packing ~840).
- The occ1 mode also pays a fixed protocol overhead: the format
  specification (with example) makes the A system prompt +90 tokens longer
  than occ0, and the speaker burns +136 more completion tokens (incl.
  reasoning) producing the structured form.
- Bottom line for OCC-2 dialect design: compactness is won or lost in field
  packing discipline, not by the KEY=VALUE grammar itself. A dialect rule
  like "one field per record, attributes inline in the value" would remove
  the family D penalty; per-attribute field naming multiplies key tokens.

## 4. Latency (wall clock, seconds)

| Call | p50 | p95 | max |
|---|---|---|---|
| A occ0 (glm-5.3) | 4.42 | 8.76 | 12.18 |
| A occ1 (glm-5.3) | 5.38 | 13.48 | 16.69 |
| B occ0, glm-5.3 | 1.69 | 5.13 | 7.06 |
| B occ0, glm-5.3-flash | 0.81 | 3.62 | 149.85 |
| B occ1, glm-5.3 | 1.68 | 3.30 | 5.53 |
| B occ1, glm-5.3-flash | 0.82 | 1.58 | 4.30 |

glm-5.3-flash is ~2x faster at the p50 listener call (0.81s vs 1.69s) and
shows no accuracy loss on this suite. One flash outlier: a single
B call on occ0/A-dev-007 took 149.85s with only 37 completion tokens and
`finish_reason=stop` (correct answer) - a gateway/queueing hiccup, not model
behavior; excluding it, flash p95 is 3.6s.

## 5. Failures and baseline limitations (not hidden, by design)

Exactly 2 of 50 occ0 task-units failed (both listeners, same answers); occ1
failed none. Both occ0 failures transferred the needed fact correctly and
failed at the answer-normalization boundary:

1. `A-dev-010` (occ0, both listeners): gold `release engineer okafor`, B
   answered `Okafor`. The occ0 sender capitalized the prose form
   ("release engineer Okafor"), and both listeners returned just the name.
   In occ1 the raw value `SIGNATORY=release engineer okafor` was copied
   verbatim and both listeners answered the full gold string. The fact was
   never lost; the exact-match checker punishes the shorter paraphrase.
2. `C-dev-010` (occ0, both listeners): gold `stage the new public key`,
   B answered `Stage the new public key.` - capital S from the sender's
   sentence-style prose plus a trailing period. The checker casefolds and
   collapses whitespace but does not strip punctuation, so this fails by
   one character. With a trailing-punctuation strip, occ0 would be 49/50;
   A-dev-010 would still fail.

Reading: on exact-form answers, the OCC-1 field value acts as a verbatim
carrier and beat the prose paraphrase twice. This is a real, repeatable
effect, not noise: both failures are the two families whose gold answers
are verbatim strings (A values, C step texts), and both flipped to correct
in occ1 with byte-identical answers across listeners.

Prompt-side observations worth keeping for later dialect work:

- The 120-word occ0 budget never bound: max observed message 103 words
  (p95 = 99). The constraint was not the limiting factor for accuracy.
- occ1 parse failures: 0/50 first-attempt (glm-5.3 followed the OCC-1
  grammar exactly, no code fences, no retries needed). The strict parser +
  one retry mechanism was never exercised on real data in this run.
- occ1 field-count bound (128) never approached: max 48 fields.
- Difficulty labels do not predict failure here: both occ0 misses are
  easy-to-transfer content (one medium, one hard label); every "hard" occ1
  unit passed.
- Single run per cell: temperature 0 with fixed seed, but no repeat-run
  variance estimate was measured within the 300-call budget. Later
  comparisons on the same suite should re-run a drifted cell before
  claiming a regression smaller than one task.

## 6. Reproduction

```
python research/taskbench/runner.py smoke            # 5 tasks x 2 modes, first-per-family
python research/taskbench/runner.py run --tasks-per-family 10
python research/taskbench/runner.py aggregate        # rebuild results_taskbench.json
```

Requirements: Python 3.11, httpx, the gateway token file at the path
hard-coded in runner.py (override with OCC_GATEWAY_TOKEN_FILE), and the
local model gateway serving glm-5.3 / glm-5.3-flash. The runner is resumable:
completed (mode, task) units are skipped based on taskbench_runs.jsonl, and
aggregation deduplicates to the latest record per unit. Raw records
(messages, answers, per-call usage and latency, retry flags) are in
taskbench_runs.jsonl for re-analysis without new model calls.

Holdout discipline: only the DEV split was run. tasks_holdout.json was not
touched by any call in this baseline.
