# live-dev-sym-v2 speaker findings (strict decode 0/60)

60/60 replies, codebook verbatim on all. Strict fixed-point decode fails on every item via three
systematic classes (child fully consistent; no random errors):

| class | child form | canonical form | items |
|---|---|---|---|
| line order | `$=` header first, then k/n/r/s/i | family lines (e/v/c/u/n) before `$=`, then f/r/k/s/q/i | ~all |
| never-compress | `k=ACK`, `s=in_progress`, `e1=finding:m2-d01-03`, ISO TS | `k=A`, `s=1`, `e1=f:m2-d01-03`, ISO TS | ACK/BLOCKED/CORRECTION/CANCELLED items |
| evidence links | `v1=e1` | `v1=1` (bare index) | items with FE packing |

Interpretation: card prose ambiguity, not model error. The card's 'never-compress kinds travel verbatim:
no elision, no recoding' admits the child's literal reading; the encoder means 'value encodings stay literal
(b36/epoch36 off), symbol alphabet still applies'. Link format is unspecified in the card text.

Parent-side note: an earlier logging artifact (parent's one-time codebook transcription dropped one
prefix rule) was corrected to the canonical codec codebook before this analysis; wire bodies untouched.

Next: clarify card text (order, never-compress policy, link format), verify Rust decode order handling,
re-run with fresh child.
