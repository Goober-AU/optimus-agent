# OCC-2 Live-Eval Driver Extension (sym-v1, sym-v2-d2-r1)

Offline preparation for the OCC-2-side live evaluation: DGX GLM 5.3 real-model
validation of the promoted dialects. This driver stays fully offline; the
parent drives the live children. No model calls, no network, no commits.

## What was added

### 1. `research/occ2opt_eval/occ2_codec.py` (new, ~34 KB)

Deterministic codec mirrors used for prompts, parsing, and scoring:

- `SymV1Codec` - faithful Python mirror of the production `occ2.rs`
  encode/decode: wire header `OCC/2 D=sym-v1@1 C=<12hex>`, the 15 symbol
  slots, subfield suffixes, strict validation (duplicates, index gaps,
  contiguous indices, kind/status/evidence-kind enums, confidence range,
  RFC 3339 timestamps, codebook hash check) and a byte-exact mirror of the
  `agent_messages.rs` legend. An import-time assertion pins the sym-v1
  canonical codebook sha256 to the Rust constant
  `1efce9992ee24ff779a0430d998b37704d484f9cbb90c2fb78feec063add1547`.
- `Dialect2EvalCodec` - wraps the frozen promoted card
  `research/symbolic_comm2/dialect2-card.json` (id `sym-v2-d2-r1`,
  body checksum `d69eb25e...65876e`) via the frozen `dialect2.py` codec.
  Its `codebook_text()` is rendered programmatically from the card (aliases,
  families, value symbols, defaults, packings, value encodings with
  epoch36/b36 semantics, field-scoped prefix MT rules, never-compress kinds).
- `parse_speaker_output(text)` - expected reply shape: the codebook verbatim,
  a blank line, then the wire. A bare wire is accepted (scored, flagged
  `codebook_ok=False`); any other preamble is a protocol fault; a fenced
  reply is a format fault.

Verified: 60/60 round-trips on the dev split for BOTH dialects (encode ->
decode -> equal payload -> re-encode byte-identical).

### 2. Driver OCC-2 mode (`--dialect sym-v1|sym-v2-d2-r1`)

- **Speaker prompts**: typed payload + the dialect codebook + the exact reply
  shape (codebook verbatim, blank line, wire with the exact expected first
  line: `OCC/2 D=sym-v1@1 C=1efce9992ee2` for sym-v1, `d` for sym-v2-d2-r1).
  Works for scripted and free modes.
- **Listener prompts**: same QA + payload-reconstruction structure as the
  OCC-1 protocol mode, plus the dialect codebook in the prompt. The codebook
  rides the FIRST item of each listener batch; later items rely on the child
  remembering it. That mirrors a session-scoped legend at batch granularity
  and is exactly what the comprehension questions measure.
- **Child routing**: speakers eval-spk-1/eval-spk-2 by pipeline column;
  listeners eval-lst-1/eval-lst-2 by batch parity (`child_hint` on every
  row), same child for all rows of a batch, file order.
- **Scoring**: deterministic decode via the codec mirrors. Speaker wire is
  scored for fidelity (payload equality against ground truth, re-encode
  byte-equality, field-level diffs); protocol faults (preamble, fenced,
  malformed) are recorded per item. Listener QA (3 questions per item),
  payload reconstruction, and canary drift use the same frozen checkers and
  the same 5 acceptance gates as the OCC-1 protocol runs.
- **New metrics** (never gate-blocking):
  - per item: `wire_chars`, `wire_tokens`, `occ1_wire_chars`,
    `occ1_wire_tokens`, `wire_char_delta`, `wire_token_delta`,
    `codebook_ok`, `carries_codebook`;
  - summary `wire_tokens` block: mean wire tokens/chars, mean OCC-1
    baseline, mean delta and ratio, tokenizer source label;
  - summary `codebook_overhead` block: codebook chars/tokens, carriers,
  batches, speaker codebook emission rate, amortized chars/tokens per item.
- **Token counting**: local Qwen2.5-0.5B-Instruct tokenizer
  (`add_special_tokens=False`) from the offline model directory; lazy, and
  degrades to `unavailable` without affecting gates (tested).

## Numbers (dev split, batch 10, deterministic synthetic speakers)

Same designed fault plan as OCC-1 protocol runs: 2 speaker semantic + 1
speaker format + 1 listener QA + 1 listener comprehension + 1 listener
format; canaries untouched. 59/60 items scored (1 speaker format fault).

| dialect | wire tok/item | wire ch/item | OCC-1 tok/item | tok ratio | tok delta | codebook (ch/tok) | amortized tok/item | net vs OCC-1 |
|---|---|---|---|---|---|---|---|---|
| sym-v1 | 158.39 | 313.34 | 144.47 | 1.0963 | -13.92 | 626 / 178 | 17.8 | -31.7 |
| sym-v2-d2-r1 | 87.81 | 194.00 | 143.10 | 0.6136 | +55.29 | 3466 / 1187 | 118.7 | -63.4 |

- sym-v2-d2-r1 wires cost 0.61x the OCC-1 protocol tokens per message
  (consistent with the offline DIALECT-V2 eval: 88.57 tok/item over 60 items
  vs 87.81 over the 59 scored here).
- Codebook amortization is per-batch at batch 10 (6 injections over 60
  items). Break-even for sym-v2-d2-r1 is ~21.5 items per injection; at
  batch 30 the amortized cost is 39.6 tok/item and the net turns +15.7
  tok/item. The runtime's real amortization (legend once per session) is
  the number to compare against reports/occ2opt/CODEBOOK-COST.md.
- 2026-10-17: the sym-v2-d2-r1 codebook text was clarified after the first
  live speaker run (live-dev-sym-v2) exposed three spec gaps - canonical
  line order, the never-compress policy, and the bare-index evidence-link
  format. The codebook grew from 2517 ch/943 tok to 3466 ch/1187 tok; see
  reports/occ2opt/CARD-CLARIFY.md and re-run live speakers with the current
  driver only.
- sym-v1 wires are slightly cheaper in chars but MORE expensive in tokens
  than OCC-1 (1.10x): the symbolic tokens fragment under the Qwen tokenizer.

## Self-test

- 174/174 checks with the venv python (transformers available), 170/170
  degraded (4 conditional token checks skip when the tokenizer is absent).
- Existing OCC-1 protocol coverage unchanged: scripted + free dry-runs with
  exact fault detection, byte-identical determinism, live refusal guards,
  negative controls, no-network source scan (now also scans occ2_codec.py).
- New OCC-2 coverage: both dialect dry-runs green with identical fault
  numbers; dialect label + manifest provenance; codebook carriers ==
  batches; listener ordering + child hints; first-prompt-carries-codebook /
  later-prompts-do-not; codec round-trips over the whole dev split; sym-v1
  legend + sha pin checks; speaker-output parsing (codebook / bare /
  commentary / fenced); OCC-2 determinism re-run byte-identical;
  degraded-tokenizer path (metrics null, gates green, artifacts not
  clobbered - the degraded check runs on a throwaway copy).
- Full live-flow rehearsal executed offline end to end: speaker stage ->
  synthetic replies -> listener stage -> score stage, with PARENT-INSTRUCTIONS
  generated per stage (speaker and listener variants).

## Live invocation lines (parent-run; do NOT run as sub-agent)

Prerequisite: `OPTIMUS_OCC2OPT_EVAL_LIVE=1` plus `--confirm-live`. Run from
the worktree root `C:/Users/openclawuser/optimus-occ2opt-20261016`. Use the
venv python (below) at least for the score stage so token metrics are
recorded; the speaker/listener stages only write prompts.

(a) dev split, sym-v1:

```
cd C:/Users/openclawuser/optimus-occ2opt-20261016
V=C:/Users/openclawuser/optimus-occ2opt-20261016/research/latent_handoff/.venv/Scripts/python.exe
D=C:/Users/openclawuser/optimus-occ2opt-20261016/research/occ2opt_eval/driver.py
R=C:/Users/openclawuser/optimus-occ2opt-20261016/research/occ2opt_eval/runs/live-dev-sym-v1

OPTIMUS_OCC2OPT_EVAL_LIVE=1 "$V" "$D" --split dev --mode scripted --batch 10 --dialect sym-v1 --live --stage speaker --confirm-live --run-dir "$R"
# parent: spawn eval-spk-1/eval-spk-2, send prompts/speaker.jsonl rows by
# pipeline column, collect replies/speaker.jsonl (per prompts/PARENT-INSTRUCTIONS.md)

OPTIMUS_OCC2OPT_EVAL_LIVE=1 "$V" "$D" --split dev --mode scripted --batch 10 --dialect sym-v1 --live --stage listener --confirm-live --run-dir "$R"
# parent: spawn eval-lst-1/eval-lst-2, send prompts/listener.jsonl rows by
# child_hint, same child per batch in file order, collect replies/listener.jsonl

OPTIMUS_OCC2OPT_EVAL_LIVE=1 "$V" "$D" --split dev --mode scripted --batch 10 --dialect sym-v1 --live --stage score --confirm-live --run-dir "$R"
```

(b) dev split, sym-v2-d2-r1: identical, replace `--dialect sym-v1` with
`--dialect sym-v2-d2-r1` and use run dir `live-dev-sym-v2`:

```
cd C:/Users/openclawuser/optimus-occ2opt-20261016
V=C:/Users/openclawuser/optimus-occ2opt-20261016/research/latent_handoff/.venv/Scripts/python.exe
D=C:/Users/openclawuser/optimus-occ2opt-20261016/research/occ2opt_eval/driver.py
R=C:/Users/openclawuser/optimus-occ2opt-20261016/research/occ2opt_eval/runs/live-dev-sym-v2

OPTIMUS_OCC2OPT_EVAL_LIVE=1 "$V" "$D" --split dev --mode scripted --batch 10 --dialect sym-v2-d2-r1 --live --stage speaker --confirm-live --run-dir "$R"
# ... collect replies/speaker.jsonl, then:
OPTIMUS_OCC2OPT_EVAL_LIVE=1 "$V" "$D" --split dev --mode scripted --batch 10 --dialect sym-v2-d2-r1 --live --stage listener --confirm-live --run-dir "$R"
# ... collect replies/listener.jsonl, then:
OPTIMUS_OCC2OPT_EVAL_LIVE=1 "$V" "$D" --split dev --mode scripted --batch 10 --dialect sym-v2-d2-r1 --live --stage score --confirm-live --run-dir "$R"
```

Notes:
- Each stage prints the exact next-stage command line (including the
  dialect flag and run dir); prompts/PARENT-INSTRUCTIONS.md in the run dir
  restates the flow with the reply-file schema.
- A child that dies or never answers is left ABSENT from the reply file;
  the driver accounts it as child_lost (gate 2). Never invent replies.
- Fresh children assumed (eval-spk-*, eval-lst-*); reuse only if the
  parent's fresh-context rules allow.

## Honest limits

- Token counts use the local Qwen2.5-0.5B-Instruct tokenizer as a proxy for
  GLM 5.3; absolute numbers will shift, ratios should hold approximately.
- Codebook overhead is measured at batch 10 (once per batch). The eval does
  not model the runtime's true once-per-session amortization.
- The listener memory effect (codebook on first item only) is measured per
  batch, not per session lifetime.
- sym-v2-d2-r1 value encodings (epoch36, b36) are reversible by the codec
  but rely on the model reading base36; a live failure there is a finding,
  not a driver bug.
