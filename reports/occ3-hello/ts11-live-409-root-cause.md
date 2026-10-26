# TS11 live-409 root cause (and the fix)

## Symptom

TS11's GATE-8 armed completion failed with a public 409 `native_completion_failed`
on the capture-armed source request, before any GPU capture work. The public
body carries no diagnostic (PR #200 policy: bounded, identifier-free
diagnostics go to the container log only).

## Fold chain (all steps traced in live evidence)

1. The scheduler aborts the request pre-prefill; the abort output's message is a
   fixed occ3 refusal code.
2. `_generate` raises on the abort finish_reason, discarding the occ3 message.
3. The pinned first-chunk handler in `serving_chat.py` (lines 1892–1894,
   SGLang 0.5.21 `e00930c5`) catches the `ValueError` and returns a flat 400.
4. The post-check in `_run` folds that into the exact observed 409.

## Root cause (verified with real code from the live candidate image)

`Scheduler._occ3_validate_received` refuses pre-prefill admission with
`occ3_unsupported_profile`. Of the 31 guard terms, exactly one trips:
`envs.SGLANG_USE_PICKLE_IPC.get() == True`. Deployed sglang v0.5.21 defaults
`SGLANG_USE_PICKLE_IPC = EnvBool(True)` (upstream flip 2026-09-29), and the live
containers carry no override (docker inspect: 53 env entries). The OCC-3 native
contract requires msgpack IPC; with pickle IPC the received-side objects cannot
be validated, so the guard refuses admission. All other admission checks pass
with real code (budget arithmetic, registry resolve-equality, msgpack round-trip,
beam width 1, `init_req_max_new_tokens`).

## Fix (two layers, both red-first)

1. **Plan-declared candidate environment additions** (`2176de8ee`): the V3
   controller accepts `environment_additions` in the plan; the candidate env is
   the original env plus pure additions, fail-closed (`environment_exact` parity,
   no override/removal/collision). The TS12 candidate launched with
   `SGLANG_USE_PICKLE_IPC=0`; live docker inspect confirmed 54 entries = the
   original 53 + the addition.
2. **Refusal-code diagnostics at the seam** (`62011dd23`): the `_generate`
   finish_reason seam now logs `occ3-bridge: native turn aborted (refusal=%s
   status=%s)` before raising, and the broad except logs the bounded exception
   class. The public 409 stays generic. Red-first: 9 tests failed on the
   pre-repair adapter at the missing diagnostic line, 9 pass after.

Also in the chain: `_inner_error_detail` now parses the pinned flat
`ErrorResponse` `{"message":...}` body in addition to the nested error shape
(`8ac6f790b`) — repro-fixture F9 showed real `serving_base` returns the flat
shape.

## Live verification (TS12)

With `SGLANG_USE_PICKLE_IPC=0` the capture-armed source request was admitted and
ran on the GLM candidate: prefill (298 tokens) and 832+ tokens decoded with the
capture journal active. No 409, no refusal code, no scheduler abort. See
`ts12-live-window.md` for the remaining executor-toolchain issues.
