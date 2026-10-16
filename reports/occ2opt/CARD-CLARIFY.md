# DIALECT/2 card-text clarification (post live-dev-sym-v2)

The first live speaker run (DGX GLM 5.3, run dir `research/occ2opt_eval/runs/live-dev-sym-v2`)
returned 60/60 codebook-verbatim replies but 0/60 strict decodes. The child was fully
consistent; the failures were three systematic card-text gaps, plus one factual error found
while verifying the Rust side. This note records the clarified card text, the Rust verdicts,
and the re-validation counts.

## What the child did vs what the encoder emits

| class | child form | canonical form | items |
|---|---|---|---|
| line order | `$=` first, then scalars, family lines last | family lines (e/v/n/u/c) first, then `$=`, then contract-order scalars | all |
| never-compress | `k=ACK`, `s=in_progress`, `e1=finding:...` | `k=A`, `s=1`, `e1=f:...` | ACK/BLOCKED/CORRECTION/CANCELLED |
| evidence links | `v1=e1` | `v1=1` (bare index) | FE-packed items |
| never-compress TS | ISO `2026-11-05T19:58:00Z` in `$=` | epoch36 `to1oso` in `$=` | never-compress items |

The fourth row is the factual error: the OLD card text said never-compress messages travel
with "ISO timestamps", but both encoders (Python `dialect2.py` and Rust `encode_dialect2`)
recode TS/EXP to epoch36 UNCONDITIONALLY - `encode_line_v2` is not gated on `never` (the
Python comment says "never-compress messages are covered too"). A child following the old
text literally (ISO ts) fails the fixed-point check. The clarified text states epoch36.

## Rust verdicts (promoted `dialect_load.rs`, empirically verified by new tests)

1. LINE ORDER: the parse stage is order-insensitive (map-based rebuild), but strict decode
   is a fixed-point check, so ANY non-canonical order fails with `NotFixedPoint`. The card
   text stating the canonical order matches Rust behavior. No Rust change needed.
2. NEVER-COMPRESS: spelled-out enums fail at `unsymbolize` (`UnknownSymbol`); a raw ISO
   timestamp fails the fixed point. TS/EXP epoch36 is unconditional in Rust exactly as in
   Python (frozen fixtures confirm: ACK/CORRECTION entries carry epoch36 `$=` and `xp=`).
   No Rust change needed; the card text was wrong, not the codec.
3. EVIDENCE LINKS: `v1=e1` is REJECTED by the typed contract with
   `Contract(UnknownEvidence { finding_id, evidence_id: "e1" })`, matching the Python
   contract error `FE1 references 'e1', expected E<n>`. No Python/Rust divergence. The
   card text was silent on the link format; it now states bare indices.

New regression tests in `crates/pi-coding-agent/tests/occ2_dialect_promotion.rs` (18/18
pass, was 15): `v2_reordered_lines_fail_the_fixed_point`,
`v2_never_compress_requires_symbols_and_epoch36`,
`v2_non_index_evidence_links_are_rejected`.

## The exact new codebook text (rendered from the card by `occ2_codec.py`)

```text
OCC-2 sym-v2-d2-r1@1 codebook (sha256:d69eb25ebf7b): wire lines are KEY=VALUE under the one-line header 'd'; emit the lines in the canonical order stated below.
keys: f=SENDER k=KIND m=MSGID n=NEXT p=PARENT q=SEQ r=RECIPIENT s=STATUS t=TASK vc=V vm=VM vr=VR vs=VS w=TS x=TRACE xp=EXP; families: c<n>=FC<n> d<n>=ED<n> e<n>=E<n> g<n>=ER<n> h<n>=ART<n> i<n>=F<n> l<n>=EL<n> u<n>=DEP<n> v<n>=FE<n> y<n>=EV<n>.
line order: after the header emit the family lines first, in packing order - evidence e<n> (packed or unpacked), finding links v<n>, next actions n, dependencies u, finding confidences c<n>; then the $= line (or, when the merge does not apply, the separate m/t/x/w lines in that position); then the remaining lines in contract order - p, f, r, k, s, q, i<n>, any unpacked d/g/l/y lines, artifacts h<n>, verification vc/vs/vm/vr, expiry xp. Emit exactly this order: decoding verifies it.
value symbols EVIDENCE_KIND: a=artifact b=build c=commit f=finding l=log n=lines s=src t=tool; EVIDENCE_VERIFIED: 0=fail 1=pass; KIND: A=ACK B=BLOCKED C=CORRECTION E=EVIDENCE P=PROGRESS Q=REQUEST_DETAIL R=RESULT T=TASK X=CANCELLED; STATUS: 0=pending 1=in_progress 2=done 3=partial 4=blocked 5=failed 6=cancelled; VERIFY: +=pass -=fail ?=unknown.
elided fields equal defaults: KIND=PROGRESS, RECIPIENT=daemon://coordinator, SENDER=daemon://coordinator, SEQ=1, STATUS=in_progress.
findings i1..in and evidence e1..en are positional; evidence values are <kind>:<locator> with sigil-packed tails @<lines>, #<digest>, ~<revision>, !<verified>; evidence links on packed v<n>= lines are bare 1-based indices, comma-separated (v1=1,2 means E1 and E2 - never v1=e1,e2).
packings: E, FE, NEXT, FC, DEP.
next actions join on one n= line and dependencies on one u= line, values separated by |; artifacts h<n> are one per line.
value encodings: ED=b36, ER=b36, EXP=epoch36, FC=b36, TS=epoch36.
epoch36 fields carry base36 Unix epoch seconds and decode to RFC 3339 Z timestamps.
b36 fields carry base36 recodings: digests of 64 hex chars, revisions of 9 hex chars, confidences of 3 digits.
the m/t/x/w lines merge onto one $= line as $=<msgid> <task> <trace> <ts>, space-joined.
prefix substitutions: E 'reports/occ2b-e0'->'¡'; TRACE 'trace-occ2b-e1'->'µ'; TRACE 'trace-occ2b-e0'->'¶'; NEXT 'run the'->'§'; RECIPIENT 'agent://worker-'->'§'; E 'research/tasks2/'->'¿'; E 'crates/pi-coding-agent/src/core/occ/'->'µ'; E 'receipts/occ2b-e'->'¶'; MSGID 'm2-e1'->'¶'; TASK 'occ2b-e1'->'¶'; SENDER 'agent://worker-'->'§'; E 'reports/occ2b-e'->'§'; MSGID 'm2-e0'->'§'; TASK 'occ2b-e0'->'§'; TRACE 'trace-occ2b-e'->'§'; TRACE 'trace-'->'%'; SENDER 'agent://'->'a:'; SENDER 'daemon://'->'d:'; RECIPIENT 'agent://'->'a:'; RECIPIENT 'daemon://'->'d:'; E 'crates/pi-coding-agent/'->'^'; E 'logs/'->'g:'; E 'receipts/'->'j:'; E 'reports/'->'q:'; E 'target/'->'y:'; E 'research/'->'z:'; E 'docs/'->'o:'; ART 'crates/pi-coding-agent/'->'^'; ART 'logs/'->'g:'; ART 'receipts/'->'j:'; ART 'reports/'->'q:'; ART 'target/'->'y:'; ART 'research/'->'z:'.
never-compress kinds (BLOCKED, CANCELLED, CORRECTION, ACK) and verification-fail messages keep every field: elision is off (defaults are emitted as-is), prefix substitutions are off (values stay fully literal), and the b36 recodings are off (digests, revisions, and confidences stay in their original form); the symbol alphabet still applies (k=A, s=1, evidence kind f:), packing and the canonical line order still apply, and TS/EXP still travel epoch36.
```

## Diff (5 rows changed/added in `_render_codebook`)

1. Row 0 (header): appended "; emit the lines in the canonical order stated below."
2. Row 2 (NEW, after keys/families): the line-order statement (see listing above).
3. Links row: "evidence links on packed v<n>= lines are comma-separated." ->
   "evidence links on packed v<n>= lines are bare 1-based indices, comma-separated
   (v1=1,2 means E1 and E2 - never v1=e1,e2)."
4. Next/dep row: "next actions repeat on n= lines joined by |; dependencies u1..un are
   one per line." -> "next actions join on one n= line and dependencies on one u= line,
   values separated by |; artifacts h<n> are one per line." (The old text was also wrong:
   multi-value dependencies JOIN on one u= line; the encoder refuses values containing |.)
5. Never-compress row: replaced the ambiguous "travel verbatim: no elision, no recoding,
   ISO timestamps" with the precise policy (see listing above).

The frozen card JSON (`research/symbolic_comm2/dialect2-card.json`, checksum
`d69eb25ebf7b...65876e`) is UNTOUCHED - the card semantics never changed; only the
rendered prose that describes them to a model was clarified. Wire bytes are identical
(273/273 fixture fixed-point round-trips re-verified after the change).

## Re-validation

- Driver self-test: 178/178 with the venv tokenizer (was 174; +4 new checks pin the
  clarified clauses), 174/174 degraded (no transformers).
- Both dialect dry-runs: gates GREEN (23 checks, 0 failures), identical designed fault
  numbers (speaker 59 scored / 57 fidelity / 1 proto fault; listener 172/174 QA;
  canaries 12/12).
- Codec round-trips: dev split 60/60 both dialects; full 273-message fixture fixed-point
  273/273.
- Rust: `occ2_dialect_promotion` 18/18.

## Codebook size impact (Qwen2.5-0.5B tokenizer)

| | chars | tokens | break-even (items/injection) | net @ batch 30 |
|---|---|---|---|---|
| old text | 2517 | 943 | ~17 | +23.9 tok/item |
| clarified text | 3466 | 1187 | ~21.5 | +15.7 tok/item |

Wire tokens are unchanged (87.81 tok/item, 0.6136x OCC-1). The clarification costs +244
codebook tokens per injection; the runtime's once-per-session amortization makes this
negligible at real session lengths.

## Next live run

Re-run the speaker stage with a FRESH child and run dir `live-dev-sym-v2b` (previous run
dirs are evidence and must not be touched):

```
cd C:/Users/openclawuser/optimus-occ2opt-20261016
V=research/latent_handoff/.venv/Scripts/python.exe
R=research/occ2opt_eval/runs/live-dev-sym-v2b
OPTIMUS_OCC2OPT_EVAL_LIVE=1 "$V" research/occ2opt_eval/driver.py --split dev --mode scripted \
    --batch 10 --dialect sym-v2-d2-r1 --live --stage speaker --confirm-live --run-dir "$R"
```
Listener/score stages follow the printed next-step lines, unchanged from
reports/occ2opt/OCC2-EVAL-DRIVER.md.
