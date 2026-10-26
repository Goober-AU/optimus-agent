# OCC-3 Hello (Live) — Campaign Status

Goal: two real Optimus subagents (both DGX SGLang GLM-5.3) exchange a private
"hello" (vault code) through genuine OCC-3 latent state, with negative controls
proving no text leakage — `occ3_hello_live`.

## Baseline

- PR #200 (`260e996fe`, merged at `7d731313f`) fixed the live startup route
  registration and advertised the capture wire cap.
- Production runtime: DGX `TRY-72998-gpu01`, SGLang 0.5.21 (`e00930c5`),
  GLM-5.3 FP8 E4M3 KV, TP8, EAGLE/MTP adaptive, RadixAttention, container
  `neo-glm53` (image `b1259f3ea327`).

## Windows so far (controlled-swap V3 controller, auto-rollback, one attempt each)

| Window | Outcome | Root cause found |
|--------|---------|------------------|
| TS9/TS10 (Oct 22–23) | RESTORED | offline root causes fixed along the way; no window reached a completed initial native proof |
| TS11 (Oct 26, 09:56Z) | RESTORED | live-409 on the capture-armed source request — see `ts11-live-409-root-cause.md` |
| TS12 (Oct 26, 13:24Z) | RESTORED (proof-deadline miss) | **TS11 fix verified live**; two executor-toolchain findings — see `ts12-live-window.md` |

GATE-8 (one real GPU capture + receiver consumption) has not yet been completed
by any window. TS12 reached the furthest point so far: candidate swap +
`service_initial` gate + native runtime mounted + runtime identity + six session
enrolls + capture-armed admission + armed decode (832+ tokens) before the
executor-side stream watchdog aborted the client.

## Next: TS13

Prepared and staged (see `ts12-live-window.md` "TS13 requirements"). Needs a fresh
authorization for a new controlled-swap window; the candidate image
(`fe932112a2ef`) is unchanged and verified good.
