# OCC-2 Codebook Cost — Deterministic Offline Analysis

**Worktree:** `optimus-occ2opt-20261016` · **Date:** 2026-10-16 · **Mode:** offline, deterministic, no model calls, no network, no git writes.

**Question.** The prior SILO run measured OCC-2 sym-v1 at 153.8 B/msg on the inter-agent wire (vs OCC-1 289.8 B/msg),
but **+32% total prompt tokens** (191,634 → 253,864) and +51% median latency. Hypothesis: the recipient prompt
re-includes the full dialect codebook on every message. This report quantifies that from the runtime source, offline.

**Method.** Every rendering path was read from source and mirrored byte-for-byte in Python
(`research/occ2opt_analysis/analyze_codebook_cost.py`):
- `crates/pi-coding-agent/src/core/agent_messages.rs` — `agent_session_message_prompt_body()` (the single codebook injection site),
  `create_agent_session_message_prompt()` (listener scaffolding), `wrap_outgoing_body()` (dual payload).
- `crates/pi-coding-agent/src/core/occ/dialect.rs` — `DialectCard::sym_v1()` symbol table (BTreeMap ⇒ alphabetical iteration), `codebook_sha256()`.
- `crates/pi-coding-agent/src/core/occ/occ2.rs` — `encode_symbolic()` / `encode_dual()` wire grammar.
- `crates/pi-coding-agent/src/core/agent_session/subagent_runs.rs` — S2 spawn seam (`[task from parent]` + wrapped prompt).
- Corpus: `research/tasks/msgs_corpus.json`, 69 typed messages, 9 kinds (all present in the sample below).

**Tokenizer.** Qwen2.5-0.5B-Instruct `AutoTokenizer` from the local HF cache
(`optimus-occ23-models`), CPU only, `add_special_tokens=False`. Cross-check on the codebook with SmolLM2-135M-Instruct.
Raw results: `research/occ2opt_analysis/results.json`.

---

## 1. The exact codebook the runtime injects

Injected by `agent_session_message_prompt_body()` (agent_messages.rs). For **every** incoming OCC-2 message that
strict-decodes, the recipient's prompt body becomes `codebook + "\n\n" + symbolic-section`:

```rust
return Cow::Owned(format!("OCC-2 sym-v1 codebook: {slots}. Indexed fields use 1-based indices;
    suffixes: {subfields}. Values follow '='; evidence references are comma-separated.
    Reply using OCC-1; transport handles encoding.\n\n{symbolic}"));
```

Rendered with the sym-v1 card (`slots` iterated alphabetically, `symbol:name`; `subfields` as `name:marker`):

```text
OCC-2 sym-v1 codebook: G:artifact, D:dependency, E:evidence.ref, X:expiry, F:finding.statement, K:kind, M:message_id, N:next_action, P:parent_task_id, B:recipient, A:sender, Q:sequence, S:status, T:task_id, C:timestamp, R:trace_id, V:verification.claim. Indexed fields use 1-based indices; suffixes: evidence.digest:d, evidence.line_range:l, evidence.revision:r, evidence.verified:v, finding.confidence:c, finding.evidence:e, verification.method:m, verification.receipt:r, verification.status:s. Values follow '='; evidence references are comma-separated. Reply using OCC-1; transport handles encoding.
```

| Metric | Value |
|---|---|
| Characters | **602** |
| Tokens (Qwen2.5-0.5B-Instruct) | **161** |
| Tokens (SmolLM2-135M-Instruct, cross-check) | 195 |
| Codebook sha256 (canonical JSON of grammar+symbol table) | `1efce9992ee24ff7...` (wire header carries the first 12 hex: `OCC/2 D=sym-v1@1 C=1efce9992ee2`) |

The codebook is a **fixed 602 B / 161 tok block**, independent of message size. For scale: the mean
symbolic payload on the 69-message corpus is 337.9 B / 167.1 tok — the codebook is
**1.78x its bytes and 0.96x its tokens — about the size of the average message it prefixes**.

---

## 2. Per-message variants (chars and tokens)

Variants per message: **(a)** symbolic payload only; **(b)** codebook + symbolic exactly as the runtime injects today;
**(c)** OCC-1 encoding; **(d)** plain concise English (corpus `plain_english`). Table: stratified sample of 26
messages (3 per kind; REQUEST_DETAIL has only 2), all 9 kinds. Aggregates over all 69 follow.

| id | kind | (a) sym B | (a) sym tok | (b) today B | (b) today tok | (c) OCC-1 B | (c) OCC-1 tok | (d) plain B | (d) plain tok |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|
| c01 | TASK | 344 | 147 | 948 | 308 | 364 | 136 | 329 | 73 |
| c02 | PROGRESS | 447 | 210 | 1051 | 371 | 463 | 194 | 447 | 126 |
| c03 | RESULT | 642 | 330 | 1246 | 491 | 661 | 311 | 821 | 257 |
| c04 | EVIDENCE | 328 | 204 | 932 | 365 | 342 | 190 | 314 | 122 |
| c05 | CORRECTION | 334 | 170 | 938 | 331 | 350 | 156 | 313 | 90 |
| c06 | TASK | 347 | 145 | 951 | 306 | 370 | 134 | 285 | 62 |
| c07 | PROGRESS | 362 | 175 | 966 | 336 | 378 | 160 | 378 | 100 |
| c08 | PROGRESS | 268 | 146 | 872 | 307 | 281 | 131 | 204 | 55 |
| c09 | RESULT | 471 | 208 | 1075 | 369 | 492 | 193 | 599 | 139 |
| c10 | ACK | 319 | 173 | 923 | 334 | 334 | 159 | 315 | 76 |
| c11 | TASK | 335 | 147 | 939 | 308 | 355 | 136 | 320 | 72 |
| c13 | RESULT | 533 | 245 | 1137 | 406 | 549 | 227 | 684 | 171 |
| c14 | EVIDENCE | 295 | 196 | 899 | 357 | 307 | 182 | 256 | 114 |
| c17 | REQUEST_DETAIL | 283 | 123 | 887 | 284 | 295 | 111 | 212 | 46 |
| c18 | ACK | 260 | 143 | 864 | 304 | 273 | 129 | 283 | 69 |
| c24 | BLOCKED | 419 | 179 | 1023 | 340 | 435 | 164 | 402 | 95 |
| c27 | CORRECTION | 413 | 178 | 1017 | 339 | 429 | 164 | 399 | 98 |
| c30 | BLOCKED | 381 | 173 | 985 | 334 | 397 | 158 | 364 | 89 |
| c31 | CANCELLED | 314 | 156 | 918 | 317 | 330 | 141 | 292 | 71 |
| c34 | REQUEST_DETAIL | 269 | 122 | 873 | 283 | 281 | 110 | 198 | 45 |
| c35 | CANCELLED | 307 | 157 | 911 | 318 | 323 | 142 | 285 | 72 |
| c40 | EVIDENCE | 240 | 145 | 844 | 306 | 255 | 131 | 215 | 62 |
| c44 | CORRECTION | 333 | 157 | 937 | 318 | 349 | 143 | 312 | 76 |
| c59 | BLOCKED | 331 | 162 | 935 | 323 | 347 | 147 | 307 | 75 |
| c60 | ACK | 251 | 139 | 855 | 300 | 261 | 125 | 238 | 60 |
| c61 | CANCELLED | 278 | 149 | 882 | 310 | 291 | 134 | 224 | 59 |
| **mean (26-sample)** | — | **350.2** | **172.3** | **954.2** | **333.3** | **365.8** | **158.0** | **346.0** | **91.3** |
| **mean (all 69)** | — | **337.9** | **167.1** | **941.9** | **328.1** | **353.1** | **153.1** | **340.7** | **87.9** |

All-69 ranges: sym 108-330 tok, OCC-1 96-311 tok, plain 27-257 tok.

Per-kind means (all 69, tokens, Qwen tokenizer):

| kind | n | sym tok | today tok (b) | OCC-1 tok | plain tok |
|---|---:|---:|---:|---:|---:|
| ACK | 4 | 149.8 | 312.8 | 135.8 | 68.2 |
| BLOCKED | 3 | 171.3 | 334.3 | 156.3 | 86.3 |
| CANCELLED | 4 | 155.0 | 318.0 | 140.0 | 68.8 |
| CORRECTION | 3 | 168.3 | 331.3 | 154.3 | 88.0 |
| EVIDENCE | 5 | 181.6 | 344.6 | 167.6 | 98.2 |
| PROGRESS | 13 | 165.7 | 328.7 | 150.6 | 81.9 |
| REQUEST_DETAIL | 2 | 122.5 | 285.5 | 110.5 | 45.5 |
| RESULT | 16 | 212.9 | 375.9 | 196.6 | 140.2 |
| TASK | 19 | 135.9 | 298.9 | 124.6 | 58.0 |

**Readings.**

- Adding the codebook turns a 167.1-tok symbolic payload into a 328.1-tok prompt body (**+96%**).
- Today's OCC-2 body costs **2.14x the OCC-1 body** in tokens (+175.0 tok/msg; codebook 161 + symbolic-vs-OCC-1 14.0).
- On this corpus the symbolic payload **saves bytes (-4.3%) but loses tokens (+9.2%)** vs OCC-1: values (natural-language findings, URLs, ids) dominate and are identical in both encodings; the 22-token OCC-2 wire header (`OCC/2 D=sym-v1@1 C=<12 hex>` = 22 tok vs `OCC/1` = 4 tok) outweighs the 1-3 tokens saved per shortened key. Not one of the 69 messages is cheaper in tokens than its OCC-1 form.
- Plain concise English is the cheapest rendering of the semantics (87.9 tok = 57% of OCC-1), because prose packs denser in BPE than `KEY=VALUE` lines with ids and URLs. The codebook alone (161 tok) is **1.83x a whole plain-English message**.

---

## 3. Amortization: per-message vs once-per-session codebook

Message-content stream totals (tokens, corpus means: symbolic 167.1, OCC-1 153.1, plain 87.9 per message; codebook 161 once or per message). The fixed listener scaffolding is identical in every variant and cancels.

| msgs n | OCC-2 today (codebook every msg) | OCC-2 once-per-session | saved tokens | saved % | OCC-1 baseline | plain English |
|---:|---:|---:|---:|---:|---:|---:|
| 1 | 328 | 328 | 0 | 0% | 153 | 88 |
| 2 | 656 | 495 | 161 | 25% | 306 | 176 |
| 3 | 984 | 662 | 322 | 33% | 459 | 264 |
| 4 | 1,313 | 830 | 483 | 37% | 612 | 351 |
| 5 | 1,641 | 997 | 644 | 39% | 766 | 439 |
| 6 | 1,969 | 1,164 | 805 | 41% | 919 | 527 |
| 8 | 2,625 | 1,498 | 1,127 | 43% | 1,225 | 703 |
| 10 | 3,281 | 1,832 | 1,449 | 44% | 1,531 | 879 |
| 15 | 4,922 | 2,668 | 2,254 | 46% | 2,297 | 1,318 |
| 20 | 6,563 | 3,504 | 3,059 | 47% | 3,062 | 1,757 |
| 25 | 8,204 | 4,340 | 3,864 | 47% | 3,828 | 2,197 |
| 30 | 9,844 | 5,175 | 4,669 | 47% | 4,593 | 2,636 |
| 40 | 13,126 | 6,847 | 6,279 | 48% | 6,124 | 3,515 |
| 50 | 16,407 | 8,518 | 7,889 | 48% | 7,655 | 4,393 |

**Curve / crossover.**

- Per-message codebook: `T(n) = n x (161 + 167.1) = 328.1 x n` tokens. Once-per-session: `T(n) = 161 + 167.1 x n`.
- Once-per-session is strictly better from **n = 2** on (saves 161 tok per additional message; -44% of the stream at n = 10, -48% at n = 50, approaching -49% as n grows).
- **OCC-2 (today) vs OCC-1:** OCC-2 is more expensive from the first message on this corpus (328.1 vs 153.1 tok/msg); the gap **grows +175.0 tok per message** — no crossover.
- **OCC-2 (once-per-session) vs OCC-1:** on this corpus, never in tokens (the symbolic payload itself is +14.0 tok vs OCC-1); in bytes break-even needs n >= 40 (symbolic saves only 15.1 B/msg vs the 602 B codebook).
- **Under the SILO traffic profile** (153.8 vs 289.8 B/msg => symbolic saves ~136 B = ~59-67 tok per message): once-per-session beats OCC-1 from **n = 3-5 messages**; per-message codebook **never** beats OCC-1 there either (the 161-tok codebook exceeds the ~60-tok per-message saving).

---

## 4. Decomposition of the runtime recipient prompt

One delivered inter-agent OCC-2 message turn (sibling delivery, corpus-mean payload, realistic daemon metadata — uuid ids as in this session):

| Component | Chars | Tokens | Notes |
|---|---:|---:|---|
| Listener scaffolding (label, Source/From/To/Message-id lines) | 350 | 211 | fixed per message; identical under OCC-1, cancels in deltas; ~170 tok of it is 5 uuid-family ids |
| **Codebook (injected every message today)** | **602** | **161** | 29.9% of the whole turn; 49% of the message body |
| Symbolic wire payload (mean) | 337.9 | 167.1 | what the codebook exists to make readable |
| **Turn total (OCC-2 today)** | **~1,290** | **~539** | vs ~364 tok for the same turn under OCC-1 (**+48%**) |

Wire/storage-only duplication (NOT in the model prompt — `split_occ2_dual_body` strips it, unit-tested):

| Component | Chars | Tokens |
|---|---:|---:|
| `#FALLBACK-OCC1` sentinel + full OCC-1 fallback section (mean) | 369.1 | 162.1 |

Speaker side and spawn seam:

- S2 spawn seam (`subagent_runs.rs`): a contract-shaped child task prompt is rendered as `[task from parent]` + blank line + codebook + symbolic — 5 tok prefix + a full 161 tok codebook **per spawned child**.
- The speaker's own composition instructions and any dialect explanations it sees are outside the read scope of this analysis; the speaker composes OCC-1 either way, so they are configuration-constant.

**MEASURE-ON-DGX placeholders (model-side costs — deliberately not estimated numerically here):**

- MEASURE-ON-DGX: speaker reasoning tokens to compose the OCC-1 body before encoding
- MEASURE-ON-DGX: listener reasoning tokens to decode symbolic lines
- MEASURE-ON-DGX: clarification round-trips caused by codebook misunderstanding
- MEASURE-ON-DGX: latency share of +161 tok/message extra prefill vs decode/reasoning changes (the +51% median latency is not explained by this report).

---

## 5. Dual-payload duplication factor

`encode_dual()` ships symbolic + `#FALLBACK-OCC1` + full OCC-1 on every wire message. On the same 69 messages:

- **2.09x chars** (mean 707.0 B vs 337.9 B symbolic) and **1.97x tokens** (mean 329.2 vs 167.1).
- Per-message ratio range: 2.06-2.13x chars, 1.96-1.99x tokens.
- This duplication costs wire bytes, session-file storage (`details.message` keeps the dual body verbatim) and any history replay that re-reads it — but the **model-visible prompt strips the fallback**, so it does not enter the +32% prompt-token figure.

---

## 6. Validation and caveats

- **Byte-exact from source:** codebook text and size, symbolic encoding, dual construction, prompt-body composition, listener scaffolding (unit-test-mirrored), spawn seam.
- **Derived, not read directly (occ1.rs was outside the allowed read list):** OCC-1 key names/order (from `dialect.py` FIXED_KEYS/FAMILY_KEYS + the occ2.rs comment that symbolic field order mirrors the OCC-1 encoder), `OCC/1` header, `EV{n}=pass|fail`. Bounded impact: keys differ from symbolic by 1-3 tok per line; even a different key convention shifts OCC-1 means by well under 10% and cannot change the verdict, which is driven by the fixed 161-tok codebook.
- **Corpus vs SILO traffic:** on msgs_corpus.json the wire means are 337.9 B (sym) / 353.1 B (OCC-1), not 153.8/289.8 — the SILO measured live benchmark traffic with shorter values and a more metadata-dense mix (where key shortening pays). The codebook conclusions are traffic-independent because the codebook is a fixed block; per-message conclusions for this corpus are labeled as such.
- Token counts are Qwen2.5-0.5B BPE, no special tokens; the codebook measures 195 tok under SmolLM2, so the headline is tokenizer-robust within about +/-25%.

---

## 7. Verdict (5 lines)

1. Confirmed: the runtime injects the full 602-char / 161-token sym-v1 codebook into the recipient prompt on **every** OCC-2 message (`agent_session_message_prompt_body`), plus once per spawned child at the S2 seam.
2. Arithmetic: today's OCC-2 message body = 328.1 tok vs 153.1 tok for OCC-1 (**+175.0 tok/msg = +114%**); the codebook contributes 161 of those, and the symbolic payload itself is token-neutral-to-negative vs OCC-1 on natural-language values.
3. The +32% total (+62,230 tok) is fully explained by repeated codebook injection at roughly **356 OCC-2 messages** at the corpus profile (+175 tok/msg), or **~560 messages** at the SILO byte profile (+111 tok/msg) — both plausible for the benchmark run; wire-side dual duplication (2.09x) does NOT enter prompts, so it cannot be the cause.
4. Fix impact: teaching the codebook once per session removes (n-1) x 161 tokens (-44% of the message stream at n=10, -48% at n=50, approaching -49%) and, on SILO-profile traffic, makes OCC-2 cheaper than OCC-1 from ~n=3 messages; on this corpus symbolic still never beats OCC-1 in tokens, so pairing the fix with a shorter codebook or plain-English fallback is advisable.
5. Residual risk: the +51% latency and any reasoning-token effects are model-side (MEASURE-ON-DGX) — prefill of +175.0 tok/msg is the only deterministic latency contributor this analysis can claim.

---

*Reproduce:* `C:/Users/openclawuser/optimus-occ23-20261016/research/latent_handoff/.venv/Scripts/python.exe research/occ2opt_analysis/analyze_codebook_cost.py` (offline; writes `research/occ2opt_analysis/results.json`).