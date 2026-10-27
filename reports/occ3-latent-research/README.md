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
the corrected probe and existing fixtures pass 25 tests. Earlier diagnostic
results are not reclassified or pooled with the corrected-input runs below.

Window `occ3-live-20261027t105044z` retested the corrected probe (`e347be1cf`).
The fresh vault-code proof captured 128 states on all eight ranks, but its receiver
interrupted after about 340 seconds and returned no answer. Total harness time
was 414.8 seconds; the lease was `Uncertain`. No controller proof was published.
A separate matched category diagnostic returned UNKNOWN without state (2.2 seconds)
and the correct class with full text (2.5 seconds). Its native arm captured 126
states on all eight ranks, then interrupted (362.3 seconds total, `Uncertain`).
Positive and no-state ordinary payload hashes matched exactly, each with one user
turn and no private fact. All three arms confirmed logical cleanup; physical GPU
release is not established by those receipts. These interruptions are incomplete
trials, not completed semantic misses. The native adapter buffers the entire
response before headers. Client diagnostic repair `18e622f4f` now retains an
allowlisted failure cause and HTTP status without changing the public no-replay
error, deadlines, or serving behavior. A local HTTP regression failed before the
repair; 22 attachment/source integration tests and 20 provider unit tests passed
after it. Workspace checks also passed.

One fresh instrumented category diagnostic then completed native consumption
in 249.2 seconds but answered UNKNOWN. Its full-text control returned the correct
class (2.3 seconds), and no-state returned UNKNOWN (3.1 seconds). Capture retained
128 states on all eight ranks; the receiver lease was `Consumed`, with logical
cleanup confirmed and identical positive/no-state ordinary payload hashes. This
is a completed semantic miss, not a successful hidden-state communication proof.
The interruption did not recur, so its earlier cause remains unresolved. The
runtime image and all timeouts were unchanged. No controller proof was published.
See [sanitized results](live-window-20261027t105044z.json); raw evidence remains
in `window-20261027t105044z` under the same evidence root.

After the unused proof checkpoint expired, the existing controller restored the
original service automatically. At 12:21 UTC, read-only observations verified
`RESTORED`, service and cleanup success, separate health HTTP 200, and the original
container identity. The candidate remains stopped and preserved. No manual
restart or recovery signal was issued in this window.

## Focused offline debugging after the completed semantic miss

- Server diagnostics now distinguish allowlisted disconnect (499) and deadline
  (504) guard outcomes without logging prompts, handles, or raw exceptions.
  Commit `57c23a901` passed 338 affected tests and workspace checks. It was not
  deployed in the GPU window above; the earlier interruption cause is unresolved.
- The thin solver matched the executed, source-pinned dense StateBridge reference
  on controlled full-row-rank CPU fixtures. Rank-deficient carrier differences
  had essentially equal Procrustes objectives, so they did not reproduce a bug.
  These fixtures do not represent real GLM hidden-state distributions.
- Content-sensitive CPU tests verified target and rotated-draft prefix copy
  coordinates, and that selected accepted rows—not rejected or padded rows—enter
  the retained ring on the tested path. These are not live GPU or semantic proof.
- Reference/alignment and worker tests passed 52 together; the accepted-state
  source-attested file passed 11. A cleanup fixture file passed 10. The latter
  bounds cleanup on a responsive synthetic event loop only. The preserved
  candidate configuration used a 900-second turn window, not the default 300.

The source audit previously omitted accepted-span start/end even though the
validated capture receipt contained them. A new fixture failed on the missing
field before the two-field audit correction; all 11 source-handoff tests passed
afterward. All 12 attachment tests also passed with cause/status assertions for
transport, HTTP, incomplete-stream, and receipt failures. The audit correction
does not change which states are captured.

No numerical, accepted-row, or injection-coordinate production repair was justified
by these new tests. Actual source-reference-only and fixed-reference/shuffled-H
GPU controls remain unimplemented. They require an explicit method/profile and
compatible supervised admission; prompt changes cannot substitute for them.

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
No wider benchmark was started. Receiver-input parity is verified offline and in
the fresh GPU request audit; successful semantic recovery remains unproven.

Offline numerical research also identified a token-reference confound in thin
StateBridge alignment: fitting the same low-rank rows can reproduce reference token
embeddings with little dependence on hidden-state geometry. This is not a live
semantic result. Token-only and changed-hidden-state controls remain mandatory.

The [Trinity/C2C source review](TRINITY-C2C-ANALYSIS.md) is static evidence, not proof
of GLM-5.3 KV transfer. A native KV method has not been implemented.

The work remains surgical: reuse the native bridge, state machine, extension,
controller and original model. No spent descriptor is replayed. The previous
window's restoration was verified read-only before the user authorized the one
fresh supervised window above. The original container and rollback are preserved.
