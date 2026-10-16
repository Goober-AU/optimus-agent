# DIALECT/2 — token-friendlier dialect evolution (occ2opt-dialect-v2)

**Task**: evolve a dialect v2 that pushes mean wire tokens well below the
v1 winner, fully offline and deterministically. Frozen v1 artifacts are
untouched; all v2 work lives in `research/symbolic_comm2/` (new) plus
owned extensions to the promotion path.

## Result in one line

The v2 winner `d2-full-mined-status-di-sp-pt-mine-mine`
(`research/symbolic_comm2/dialect2-card.json`, checksum
`d69eb25ebf7b4359aacd9994880e5c96fb58f993dd333bcb26a7621ffc65876e`) cuts
the evolve-split wire from the v1 winner's **127.88 to 87.42 tokens**
(-31.6%) and beats OCC-1 by **44.2%** (ratio 0.558), with exact typed
fidelity 1.0 on every split including holdout, and validates through the
`dialect_load.rs` promotion path (273/273 research-wire round-trips,
byte-identical Rust re-encoding).

## Headline table (local Qwen2.5-0.5B tokenizer, add_special_tokens=False)

Mean wire tokens per message:

| profile | OCC-1 | sym-v1 | sym-v1-pt-pt-r1 (v1 winner) | DIALECT/2 winner | d2 / r1 | d2 / OCC-1 |
|---|---|---|---|---|---|---|
| corpus1 (69, v1 corpus) | 153.10 | 124.49 | 124.78 | **98.28** | 0.788 | 0.642 |
| corpus2 evolve (90) | 156.93 | 127.82 | 127.88 | **87.42** | 0.684 | 0.558 |
| corpus2 dev (60) | 144.17 | 115.65 | 115.67 | **88.57** | 0.766 | 0.614 |
| corpus2 holdout (54, never used in evolution) | 144.30 | 114.98 | 115.00 | **87.52** | 0.761 | 0.607 |

Mean wire bytes per message:

| profile | OCC-1 | sym-v1 | r1 | DIALECT/2 | d2 / OCC-1 |
|---|---|---|---|---|---|
| corpus1 | 353.1 | 251.9 | 252.2 | **223.5** | 0.633 |
| corpus2 evolve | 361.4 | 256.4 | 256.4 | **206.9** | 0.572 |
| corpus2 dev | 327.4 | 223.7 | 223.7 | **195.6** | 0.597 |
| corpus2 holdout | 323.0 | 216.8 | 216.8 | **187.7** | 0.581 |

Key-dense subprofile (top third by structural density = occ1-field items
per value character; the SILO-style profile, same metric family as the
selection-policy SILO ratio):

| profile (n) | OCC-1 | sym-v1 | r1 | DIALECT/2 | d2 / OCC-1 |
|---|---|---|---|---|---|
| corpus1 (23) | 133.26 | 107.74 | 107.78 | **83.09** | 0.624 |
| corpus2 evolve (30) | 124.37 | 99.97 | 100.00 | **63.17** | 0.508 |
| corpus2 dev (20) | 124.25 | 97.15 | 97.20 | **72.00** | 0.579 |
| corpus2 holdout (18) | 130.00 | 101.28 | 101.28 | **75.39** | 0.580 |

The v2 target (beat OCC-1's 153.1 tok/msg while keeping byte wins) is met
with a wide margin on every profile; on the key-dense profile the win is
largest (0.51-0.62 of OCC-1).

## What v2 added (all optional card features, default-off = v1 behavior)

Implemented in `research/symbolic_comm2/dialect2.py`
(`Dialect2Card`, subclass of the frozen v1 `DialectCard`; reuse, not a
rewrite):

1. `wire_header` — 1-char header `d` instead of `D/1` (3 tokens -> 1).
2. `value_encodings`:
   - `TS`/`EXP` = `epoch36`: the 14-digit stamp (and the raw ISO form on
     never-compress messages) recodes to base36 epoch seconds (16 -> ~6
     tokens). Decode restores ISO, on which the v1 MT6/MT7 decode rules
     no-op, so never-compress messages round-trip too.
   - `ED` = `b36`: sha256 hex64 digests -> base36 (64 chars/63 tok ->
     49-50 chars/~34 tok). Decode is length-disambiguated and
     round-trip-guarded.
   - `ER` = `b36`: 9-hex revisions -> base36 (7 chars).
   - `FC` = `b36`: 3-digit confidences -> base36 (2 chars).
3. `scalar_pack` — the always-present MSGID/TASK/TRACE/TS lines merge
   onto one space-joined `$=` line (only when all four are present and no
   value contains a space; otherwise the message keeps separate lines).
   Measured -8.6 tok/msg on the evolve split.
4. Mined prefix microtemplates with 1-token non-ASCII emits (`V2M*`),
   inserted at the front of the template chain so they fire before the
   inherited path templates (`trace-occ2b-e*`, `agent://worker-`,
   `reports/occ2b-e*`, `m2-e*`, `crates/pi-coding-agent/src/core/occ/`,
   ...). The corpus is ASCII-only, so non-ASCII emits are collision-free.
5. Default promotions found by evolution: `KIND=PROGRESS`,
   `STATUS=in_progress` on top of the v1 `RECIPIENT`/`SENDER`/`SEQ`.

Two hard safety rules added to card validation (Python and Rust):
- per-family prefix-template emits must be pairwise distinct and
  prefix-free (a shared emit can silently misroute a decode for values
  outside the observed corpus — the first evolved winner had this
  latently and was rejected; the guard forced a clean re-run);
- `wire_header` must not collide with a key alias.

## Evolution (deterministic, no model calls)

`research/symbolic_comm2/evolve2.py`, log `evolution2-log.jsonl`
(population 10, elite 4, stagnation 3, seed 20261017). The v1 loop is
reused via imports (operators, fidelity oracle, fresh groups); the
model-assisted parts are replaced per mandate: seeds are a deterministic
feature ladder over the frozen v1 winner plus a greedy exact-savings
miner, and wire tokens are measured with the local tokenizer.

Fitness: exact typed fidelity 1.0 (hard gate) -> mean tokens -> mean
bytes. Deviation from v1 (recorded): every candidate is scored on the
FULL evolve split each generation — v1's fresh groups existed to keep the
model listener honest; with deterministic fitness they only added +-4
tokens of group noise that masked real improvements. Mining still draws
a fresh group per generation so induced templates must repeat across
subsets to survive selection.

Fitness curve (evolve split, mean tokens):

| gen | best card | tok | B | key-dense tok |
|---|---|---|---|---|
| 0 | d2-full-mined-status | 98.56 | 215.6 | 73.78 |
| 1 | d2-full-mined-sp | 91.53 | 211.4 | 66.96 |
| 2 | d2-full-mined-status-di-sp | 88.49 | 207.9 | 63.81 |
| 4 | d2-full-mined-status-di-sp-pt | 88.44 | 207.9 | 63.78 |
| 6 | d2-full-mined-status-di-sp-pt-mi-mine | 87.61 | 207.1 | 63.00 |
| 8 | d2-full-mined-status-di-sp-pt-mine-mine | 87.42 | 206.9 | 62.81 |
| 9-11 | (stagnation) | 87.42 | 206.9 | 62.81 |

Winner lineage: `packing_toggle:DEP=off`, `packing_toggle:ART=off`,
`mt_mine:12`, `default_induction:KIND=PROGRESS`, `scalar_pack:True`,
`packing_toggle:DEP=on`, `mt_mine:2`, `mt_mine:1`. Seed robustness:
alternate seeds 20261018/19/20 all converge to 87.47 tokens (logs
`evolution2-log-seed*.jsonl`).

## Fidelity

Strict round-trips (encode -> decode -> typed equality AND
re-encode byte-identity), Python codec: 273/273 across corpus1 +
corpus2 evolve/dev/holdout. Rust promotion path: 273/273 in
`tests/occ2_dialect_promotion.rs::v2::v2_corpus_research_encode_to_promoted_decode_round_trip`
(research-encoded wire -> promoted decode -> exact typed match; Rust
encode reproduces the research wire byte-for-byte).

## Promotion path (owned-file extension, as mandated)

`crates/pi-coding-agent/src/core/occ/dialect_load.rs` (the v2 mandate
allows extending this owned file; noted here):

- `CardJson`/`ResearchDialectCard` gain optional `wire_header`,
  `value_encodings`, `scalar_pack`; unknown keys are still rejected
  (deny_unknown_fields + top-level scan), and the canonical checksum
  now includes the v2 keys exactly when the research `body()` emits
  them — a feature-less v2 card keeps the v1 checksum (tested).
- `canonical_json` strings now escape exactly like Python
  `json.dumps(ensure_ascii=True)`; the v2 card's non-ASCII emits made
  the previous serde_json string form diverge from the Python checksum.
- `encode_dialect2`/`decode_dialect2` mirror the Python wire extensions
  (message-level ED/ER recoding, header, line-level epoch/FC recoding,
  scalar packing, strict fixed-point proof).
- `translate_to_production_as` parameterizes the production identity;
  `PromotedDialect::load_embedded_v2()` promotes the DIALECT/2 winner as
  `sym-v2-d2-r1` v1; `promoted_registry_v2()` registers both promoted
  cards explicitly (never default; selection still refuses them without
  the development override, since evaluation samples are honest zeroes).
- New fixture `research/occ2opt_analysis/dialect2_corpus_wire.json`
  (273 research-encoded wires, generated by
  `research/symbolic_comm2/gen_fixture.py`).

Tests added: 8 unit tests in `dialect_load.rs` (frozen v2 card,
emit-collision/unknown-field/bad-encoding/header-collision rejection,
feature-less checksum identity, promoted-2 translation, base36 helpers)
and 7 integration tests in `tests/occ2_dialect_promotion.rs` (`v2::`
module; includes mutual v1/v2 wire rejection and structural tampering:
wrong header, truncated scalar pack, non-canonical base36 epoch).

## Gates

| suite | result |
|---|---|
| `--test occ2_dialect_promotion` | 15 passed / 0 failed (8 v1 + 7 v2) |
| `--lib occ::dialect_load` | 24 passed / 0 failed |
| `--lib occ::` | 250 passed / 1 failed — the pre-existing, sibling-owned `session_settings::occ_session_resolution_reports_provenance_and_round_trips` (documented in earlier gate tables, not touched here) |
| `--test occ_comm_fidelity` | 7 passed / 0 failed |
| `--test occ2_comm_fidelity` | 30 passed / 0 failed |
| `--test occ2_selection_policy` | 9 passed / 0 failed |

## What did NOT improve (honest limits)

- **Prose**: findings/next-action statements are unique per message (95/96
  unique findings on the evolve split), and the tokenizer already fuses
  common words into single tokens; a phrase dictionary has no lossless
  win left. Roughly 40% of the remaining wire tokens are prose and are
  effectively at the tokenizer floor.
- **IDs**: `m2-e01-02`-style ids tokenize digit-by-digit; a general
  digit-run recoding is reversible only with markers that cost what they
  save (leading zeros). Only corpus-specific prefixes were mined.
- **Deterministic-only search**: the miner is greedy; the space of
  regex microtemplates (e.g. mid-string patterns) was not searched.
- **Tokenizer proxy**: all token counts use the local Qwen2.5-0.5B
  tokenizer; other models may tokenize the non-ASCII sigils or base36
  strings differently (sigils were chosen as single-token there).
- **Overfitting surface**: the mined `occ2b-e*`/`m2-e*` templates fire
  only on matching story prefixes; the holdout split (different stories,
  same id scheme) still shows a 0.761 ratio vs the v1 winner, and on the
  v1 corpus (different id scheme entirely) the structural features alone
  deliver 0.788.
- Never-compress messages (ACK/CORRECTION/BLOCKED/CANCELLED and
  verification-fail) skip MTs and value recodings per v1 policy; only the
  always-on epoch recoding covers their timestamps.

## Artifacts

- `research/symbolic_comm2/` — `dialect2.py` (codec),
  `evolve2.py` (evolution), `dialect2-card.json` /
  `evolution2-winner.json` (frozen winner, checksum
  `d69eb25e...65876e`), `evolution2-log.jsonl` +
  `evolution2-log-seed*.jsonl`, `final_eval.py`, `gen_fixture.py`,
  probe scripts, `check_all_enc.py`.
- `research/occ2opt_analysis/dialect2_corpus_wire.json` — 273-message
  promotion fixture.
- `crates/pi-coding-agent/src/core/occ/dialect_load.rs` — v2 extensions
  (owned file; format extension noted above).
- `crates/pi-coding-agent/tests/occ2_dialect_promotion.rs` — v2 tests.
