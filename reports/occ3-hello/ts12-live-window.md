# TS12 live window (2026-10-26T13:24Z) — outcome and findings

Window `occ3-live-20261026t132423z`, supervised V3 controller, candidate image
`sha256:fe932112a2ef…` (patch set unchanged from TS11; the delta is the
plan-declared env addition `SGLANG_USE_PICKLE_IPC=0`). One controlled attempt,
auto-rollback verify-then-stop. Outcome: **RESTORED** at the
`checkpoint-initial` proof deadline (`supervisor-result.json`,
`reason: controller_exit`); production verified back on `b1259f3ea327`,
ingress health 200, plain completion 200.

## What passed live

- R: rehearsal 10/10 scenarios with the new controller code.
- P1/P1.6: offline build (`--pull=false --network=none`), artifact preflight,
  per-file source digests (96 attested entries; all 24 `occ3_sglang` entries
  match the branch manifest).
- P2.1/P2.2: plan `PLAN_OK`; preflight `PREPARED_NOT_DEPLOYED`; live inspect:
  candidate env = production 53 + `SGLANG_USE_PICKLE_IPC=0` exactly.
- L (disposable TP1 GPU7 boot of the candidate image, tiny model):
  `_USE_PICKLE_IPC = False` verified **inside the running process**
  (`docker exec … io_struct`), health 200, plain generation 200 SSE.
- Swap: `service_initial` gate passed on the production GPUs.
- Native runtime: mounted (`installed_experimental_not_live_validated`),
  identity 200 with `ready_ranks == expected_ranks == 8` and all flags true,
  profile digest re-derived and matching, **six session enrolls 200**.
- **The capture-armed source request was admitted and decoded** (F1 below).

## Findings

### F1 — the TS11 live-409 root cause fix is verified live

With the env addition, the armed request produced no 409 and no refusal code:
prefill 298 tokens, then 832+ tokens decoded (scheduler log, rid
`77cfd51a…`, `cuda graph: False`, capture journal active). The pre-prefill
`occ3_unsupported_profile` guard passed. The TS11 failure mode is gone.

### F2 — PR #200 readiness payload breaks the pinned handoff binary

PR #200 added `max_capture_bytes` to the native readiness/health payload
(`latent_worker` digest `0cc66ba0…` vs TS11 `88aeab9e…`). The pinned TS11-era
`occ3_native_handoff` binary (`0aa8a4ca…`, built from a pre-PR#200 tree) parses
`NativeReadiness` with `serde(deny_unknown_fields)` and refuses the unknown
field: enroll returns HTTP 200 and the client fails the parse, exiting rc 1
("Native validation failed or refused") with zero further server contact.
The branch (main + PR #200) already carries the client-side fix
(`max_capture_bytes: Option<u64>`); the binary was stale. Fixed by building the
harness from `fix/occ3-native-hello-handoff` (`de469bac…`).

### F3 — armed streams are held to completion by design; the client watchdog is not

The capture-armed source path buffers the entire response
(`chat_adapter._generate` consumes the full `body_iterator` into `chunks`,
settles the `whole_completed_response_tail_v1` span, then delivers). SSE events
reach the client only at turn completion. The harness stream watchdog
(`PRIME_AGENT_STREAM_EVENT_GAP_MS`, default 120 s) aborts any armed turn longer
than that. TS12's armed decode ran ~5–7 tok/s (eager mode, CUDA graphs disabled
on the armed path, EAGLE adaptive overhead) → the watchdog aborted the client at
120 s with `stream_stall event_gap` while the server was still decoding. The
ingress is not the cause: plain streaming through the same ingress delivers
events per-token (first event 0.6 s).

### History correction

No prior window has completed the initial native proof (no
`native-proof-initial.json` in any `v3-live-*` attempt dir). TS12 is the
furthest-reaching window so far.

## TS13 requirements (prepared, awaiting authorization)

- Harness binaries built from the branch: handoff `de469bac…`, suite (release)
  recorded in the workspace closure receipt; watchdog env
  `PRIME_AGENT_STREAM_EVENT_GAP_MS=600000` (the harness maximum) and
  `PRIME_AGENT_STREAM_DEADLINE_MS=7200000`.
- Plan timeouts: `proof_timeout_s` 1200→3600, `validation_timeout_s` 900→2400,
  `deployment_timeout_s` 3600→10800, `startup_timeout_s` 1200→1800.
- Candidate image `fe932112a2ef` unchanged (server verified good).
- Executor subprocess timeouts: handoff 2400 s, phase-suite 4200 s.
- Open risk: if an armed thinking-max source turn needs more than 600 s at the
  observed ~5–7 tok/s, the watchdog (at its maximum) still aborts; the next
  lever would be server-side armed-path throughput, which is out of scope for
  this window.
