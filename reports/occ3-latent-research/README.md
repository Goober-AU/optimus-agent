# OCC-3 latent transfer validation

## Verified baseline

Checked at `2026-10-27T07:25:48.769644+00:00`.

- Repository: https://github.com/telemusai/optimus-agent
- Latest fetched `main`: `4357e0275db7aa80d1a489cf08586e745537ea5f`.
- PR #202 merged at `2026-10-27T07:07:08Z`; its merge is still main HEAD.
- The public GitHub PR list contains no PR newer than #202 at this check.
- Working branch: `fix/occ3-glm53-latent-transfer-validation`. A new worktree preserves previous campaign trees and artifacts.
- PRs #189, #190, #191, #193, #200, #201 and #202 are merged. Their historical results are prior evidence, not new validation results.

## Controlled GPU window

Window `occ3-live-20261027t074003z` ran the existing v3 controller on the official
GLM-5.3 FP8/TP8 runtime. See [sanitized results](live-window-20261027t074003z.json).

- Native sender capture returned 128 states on all eight ranks (1,572,864 logical bytes).
- The initial handoff failed before publication. Fresh diagnostics identified invalid
  receiver arguments and missing tool calls. The isolated harness now specifies and
  constrains the one authorized target using native SGLang tool-choice support.
- The final diagnostic published the captured state and dispatched a receiver request.
  The receiver's ordinary payload did not contain the private code.
- That request crashed before inference: `occ3_private_match` accessed
  `UnifiedRadixCache.root_node`, which the active Rust tree backend does not implement.
  The receiver lease remained `Uncertain`; no completed-use receipt or semantic success.
- The existing controller restored the original container. At 08:55 UTC, health,
  chat, tools and authentication checks passed. The failed candidate is preserved.

The focused correction uses `root_node_handle(extra_key=req.extra_key)`, the API
used by pinned SGLang's own zero-match path. A source-executed regression reproduced
and then eliminated the exact exception, including namespace and legacy-cache cases.
The combined focused Python suite passed 37 tests, skipped four, and passed 18 subtests.
The follow-up GPU test below passed this cache operation.

## Receiver verification retest

Window `occ3-live-20261027t090824z` again captured 128 states on all eight ranks,
published them, and reached receiver prefill. See [sanitized results](live-window-20261027t090824z.json).
The next speculative verification step failed because native `TARGET_VERIFY.is_extend()`
is true, while its OCC-3 context has decode geometry (`extend_len=0`). The prefix
injection hook incorrectly tried to apply its prefill shape check again.

A pinned-source regression reproduced the exact `LatentError(profile)`. The hook
now excludes target verification, preserving the existing prefix KV and injection
receipt. The full extension suite and workspace checks passed. This window
restored the original server with health, chat, tools and authentication checks
passing. The next window below retested the fix on the GPU.

## Latest GPU diagnostics and receiver-input correction

Window `occ3-live-20261027t093614z` reached native receiver consumption after
capture on all eight ranks. After correcting the GLM instruction role, four
category diagnostics produced one correct native answer, two UNKNOWN answers,
and one interrupted request with an `Uncertain` lease. That is 1/4 attempted
and 1/3 completed, versus full-text 4/4 and no-state UNKNOWN 4/4. These are
saved diagnostics, not a successful controller checkpoint or reliable semantic proof.
Evidence is under `C:/Users/openclawuser/occ3-latent-validation/evidence/`
`window-20261027t093614z/system-category-*`.

The probe had a further confirmed comparability defect: its default converter
removed the native Custom delivery record, leaving no ordinary receiver user
turn, while controls had one. The probe now seeds the same neutral user task
for positive and no-state; only full-text adds the fact. Native authorization
and Custom bookkeeping are unchanged. Controls continue from the seeded user
turn without extra inference. The receipt gate requires exactly one ordinary
user message. Two regressions reproduced the mismatch before the correction;
the corrected probe and existing fixtures pass 25 tests. This correction has
not been tested on the GPU, and earlier diagnostic results are not reclassified.

## Offline regression results

- `bash scripts/check.sh`: passed, including locked workspace/all-target Cargo check,
  293 packaging tests (11 skipped), and native resource layout.
- Full extension suite: 1,925 passed, 24 skipped, 77 subtests passed.
- Native handoff driver and source/handoff integration fixtures: 25 passed after
  receiver-input parity correction (two new regressions failed before correction).
- Test web dependencies match the DGX: FastAPI 0.142.2, Starlette 1.7.0,
  Pydantic 2.13.5, httpx 0.28.1. Existing Rust warnings and the dependency's
  httpx deprecation remain; no new implementation warning was introduced.

## Proof boundary

GPU capture, publication and completed receiver consumption have been observed.
They do not prove semantic hidden-state communication. Reliable fresh-secret recovery,
token-only controls, likelihood measures and comparative benchmarks remain unproven.
No wider benchmark was started. The receiver-input correction has offline proof only.

Offline numerical research also identified a token-reference confound in thin
StateBridge alignment: fitting the same low-rank rows can reproduce reference token
embeddings with little dependence on hidden-state geometry. This is not a live
semantic result. Token-only and changed-hidden-state controls remain mandatory.

The [Trinity/C2C source review](TRINITY-C2C-ANALYSIS.md) is static evidence, not proof
of GLM-5.3 KV transfer. A native KV method has not been implemented.

The work remains surgical: reuse the native bridge, state machine, extension,
controller and original model. No spent descriptor is replayed. The continuation
issued no server-control commands; the previous session's recovery is observed
read-only. Any later live validation needs a fresh supervised window.
