# IMPL-DIALECT-PROMOTION — Evolved-dialect promotion path (DIALECT/1 -> production OCC-2)

**Worktree:** `optimus-occ2opt-20261016` · **Date:** 2026-10-16 · **Mode:** offline, deterministic; no model calls, no network, no git commits.
**Mission item 7.** Translates the frozen research dialect card `sym-v1-pt-pt` (DIALECT/1, checksum `17836322dced3bb56e10bb0466abb5cb8d463ac6e4aba471f906ad5f24a4d49b`) into the production Rust runtime through a **validated loader**, not a hand-edit. The research artifact stays read-only (git status: clean under `research/symbolic_comm/`).

## 1. Files touched

| File | Change |
|---|---|
| `crates/pi-coding-agent/src/core/occ/dialect_load.rs` | NEW (2,466 lines): loader, validator, translator, DIALECT/1 wire codec, 17 unit tests |
| `crates/pi-coding-agent/src/core/occ/mod.rs` | `pub mod dialect_load;` + one doc bullet |
| `crates/pi-coding-agent/tests/occ2_dialect_promotion.rs` | NEW: 8 integration tests (frozen-card identity, 69-message round-trip, tamper rejection, OCC-2 interop) |
| `research/occ2opt_analysis/promotion_check.py` | NEW: Python translation check (checksum two ways, research strict round-trip, wire fixture) |
| `research/occ2opt_analysis/dialect1_corpus_wire.json` | NEW: fixture, research-encoded wire for all 69 corpus messages |

**Not touched:** `occ2.rs`, `agent_messages.rs`, `subagent_runs.rs`, `contract.rs`, `dialect.rs`, `occ2_comm_fidelity.rs`, `research/symbolic_comm/*` (read-only). Zero codec extensions were needed: every DIALECT/1 feature the frozen card uses is representable.

## 2. Format diff (DIALECT/1 research vs production DialectCard)

| DIALECT/1 construct | In the frozen card | Classification |
|---|---|---|
| Wire header `D/1` | yes | Codec-level; mirrored by the DIALECT/1 codec here (production uses `OCC/2 D=<id>@<v> C=<hash12>`) |
| 16 fixed key aliases (`MSGID`->`m`, ...) | yes | **Directly representable**: all 26 OCC-1 keys map 1:1 onto the 17 canonical slots + 9 subfields |
| 10 family aliases (`F`->`i`, `E`->`e`, ...) | yes | **Directly representable** (indexed slots/subfields) |
| lowercase alias grammar `[a-z][a-z0-9]?` | yes | **Needs translation**: production symbols are uppercase A-Z, prefix-free |
| 2-char subfield aliases (`vs`, `vm`, `vr`) | yes | **Needs translation**: production markers are single lowercase chars, unique per base slot |
| Value symbol tables (closed enums) | 5 domains, total | **DIALECT/1-wire only**: applied by `encode_dialect1`; not part of the production symbol-table codec |
| Microtemplates (16 prefix + 4 regex) | yes | **DIALECT/1-wire only**: declarative; compiled with the `regex` crate; backslash-N group refs translated to `${N}` |
| Field defaults (`RECIPIENT`,`SENDER`,`SEQ`) | yes | **DIALECT/1-wire only** (elision on encode, restoration on decode) |
| Packings (`E`,`FE`,`NEXT`,`FC`; `DEP`/`ART` toggled off by lineage) | yes | **DIALECT/1-wire only**: structural line merging |
| Never-compress classes (4 kinds + verification fail) | yes | **DIALECT/1-wire only** |
| `usage_rules` prose | yes | **Inert metadata**: checksum-verified, never rendered into any prompt |
| `id`/`generation`/`lineage` | yes | Inert metadata, carried through with bounds |

## 3. Translation rules

1. **Checksum first.** sha256 over the canonical JSON body (ten body keys, recursively key-sorted, compact separators) must equal the card's `checksum` field. Byte-identical to the research harness's `DialectCard.checksum()` (verified two independent ways in `promotion_check.py`).
2. **Base symbols = uppercased aliases.** `m`->`M`, ..., `vc`->`VC`. Prefix-freedom violations are repaired deterministically to the smallest uppercase letter no assigned symbol starts with, and **recorded** in `SymbolSubstitution`.
3. **Subfield markers.** Single-char aliases pass through; multi-char aliases repair to the alias's distinguishing suffix (`vs`->`s`, `vm`->`m`, `vr`->`r`) when free within the base slot, else the smallest free letter. All repairs recorded.
4. **Production identity.** Promoted as `sym-v1-pt-pt-r1` v1; codebook sha256 `e31f2630fb90d4a3f5fd9e49a4866346b35d48fe54853d538e76dc5f89567ddf`.
5. **Registration is explicit-only.** `promoted_registry()` = `with_sym_v1()` + promoted card. The default registry, `select_occ2_dialect`, negotiation and all send/prompt seams are untouched; `select_occ2_dialect` still refuses the promoted card without the dev override because its metrics are honestly unevaluated.

Actual repairs for the frozen card (complete list, asserted by test):
- `expiry`: `xp` -> `A` (uppercase `XP` would collide with `X` = trace_id under prefix-freedom)
- `verification.status`: `vs` -> `s`; `verification.method`: `vm` -> `m`; `verification.receipt`: `vr` -> `r`

## 4. Explicitly rejected constructs (never silently dropped)

Rejected at load time with `DialectLoadError::Unsupported`/`Invalid`, each naming the construct: unknown top-level JSON key; unknown JSON field inside any serde model (`deny_unknown_fields`); wrong `format`; unknown packing; duplicate packing; alias targeting a non-OCC-1 key; alias grammar violations; alias collisions (fixed-vs-fixed, family-vs-fixed, fixed-shadowing-family-index); unknown value-symbol domain; non-total enum domain; symbol equal to a raw value; duplicate symbols; never-compress kind not a known kind; field default on a non-fixed key; non-u64 `SEQ` default; duplicate microtemplate id; microtemplate targeting unknown field; **wildcard `*` microtemplate fields** (research supports them; the loader refuses them as un-auditable); unknown microtemplate type; regex without decode rule; invalid regex; `\g<name>` backreferences and lone backslashes in emit templates (only `\N` supported); plus resource bounds (card > 256 KiB; > 64 microtemplates; > 256 B template strings; > 4 KiB usage_rules; > 64 lineage entries; generation > 10,000).

Safety: no code execution from card content; cards are declarative data (tables + `regex`-crate patterns, no `eval`, no FS, no network); `usage_rules` prose is inert and never enters any prompt or wire.

## 5. Round-trip evidence

- **Checksum:** `17836322...` verified by (a) the research class itself, (b) an independent canonical-JSON sha256 in Python, (c) the Rust loader on the embedded copy, (d) the Rust loader on the on-disk copy; embedded and on-disk cards are byte-identical.
- **69/69 corpus messages:** research-encode (Python) -> promoted-card decode (Rust) == exact typed `PartialEq` equality; and promoted-card encode (Rust) == research wire **byte-for-byte**. Mean wire size 252.2 B/msg (vs OCC-1 353.1 B, sym-v1 337.9 B on the same corpus).
- **69/69** Rust-only encode -> strict decode round-trip; all 9 message kinds covered (TASK/PROGRESS/RESULT/EVIDENCE/REQUEST_DETAIL/ACK/CORRECTION/BLOCKED/CANCELLED), including never-compress kinds (values verbatim), verification-fail gating, packed evidence records with `@`/`#`/`~`/`!` tails, permille FC packing, defaults elision, TS/EXP regex templates and EXP expiry.
- **Strict decode rejects:** unknown kind/status/evidence symbols, unknown aliases, blank lines, missing `=`, wrong header, line reorders (fixed-point violation).
- **OCC-2 interop:** the translated symbol table drives the existing `occ2::encode`/`decode` (12 corpus spot checks round-trip through `promoted_registry()`).

## 6. Gate counts

| Gate | Result |
|---|---|
| `cargo test -p pi-coding-agent --test occ2_dialect_promotion` | **8 passed, 0 failed** |
| `cargo test -p pi-coding-agent --lib occ::` | **222 passed, 1 failed** — the failure is `core::occ::session_settings::tests::occ_session_resolution_reports_provenance_and_round_trips`, a sibling-owned file (107-line diff in `session_settings.rs`, outside my ownership), reproducible in isolation and unrelated to this change. All 17 `dialect_load` tests pass |
| `cargo test -p pi-coding-agent --test occ2_comm_fidelity` (regression) | **19 passed, 0 failed** |
| `cargo test -p pi-coding-agent --test occ_comm_fidelity` (regression) | **7 passed, 0 failed** |

## 7. Verdict

The frozen evolved dialect promotes cleanly: one deterministic symbol repair (`xp`->`A`) and three marker normalizations (`vs`/`vm`/`vr`), zero codec extensions, zero edits to negotiation or prompt seams. The promoted card is reachable only through the explicit `promoted_registry()` constructor and stays refused by default negotiation. Wire parity with the research encoder is byte-exact on the full corpus.
