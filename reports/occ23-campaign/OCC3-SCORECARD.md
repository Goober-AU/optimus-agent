# OCC-3 Scorecard: KV-Cache Handoff and Latent Continuation

Campaign deliverable for the OCC-2 + OCC-3 campaign, branch `improve/occ2-occ3-communication`
(base telemusai/optimus-agent v0.1.32). Date: 2026-10-16. Owner: occ3-report worker; verdict
recorded by the parent per the pre-registered framework.

**VERDICT: NO-GO for production integration in this campaign.** V1-V3 PASS, G1 PASS, G2 not
measured at the pre-registered 4096-token scale, G3 FAIL for Qwen2.5-0.5B (harness peak RSS
2,443.1 MiB, about 2.4x the 1 GiB sidecar budget), G4 untested. GO requires G1 + G3 + (G2 or
G4) per `research/integration/OCC3-INTEGRATION-DESIGN.md` section 1.2; the decision rule is
quoted in section 8 below. No production code was written or is claimed; the `occ/` module doc
keeps saying "not implemented"; the LAT telemetry indicator stays reserved. The production
design (`research/integration/OCC3-INTEGRATION-DESIGN.md`) is retained on disk for a future
revisit.

Every number below comes from the on-disk harness results
(`research/latent_handoff/results/qwen05-results.json`,
`research/latent_handoff/results/smollm2-results.json`) and the generated
`RESULTS-EXP-A.md` / `RESULTS-EXP-B.md`. Verdict source: the 2026-10-16 ~12:5xZ entry in
`reports/occ23-campaign/EXPERIMENT-LEDGER.md`.

## 1. What was measured

A standalone CPU-first harness in `research/latent_handoff/` (protocol v2) measures two
mechanisms between a logical sender (Agent A) and receiver (Agent B):

- **KV-cache handoff (Experiment A):** A processes a completed chat-template turn (private
  notes plus a fixed assistant acknowledgment) with `use_cache=True`; the captured
  `DynamicCache` is deep-copied (the transfer artifact) and handed to B, which continues from
  its own user turn, seeing A's context only through the cache. The receiver question never
  contains the secret.
- **Latent continuation (Experiment B):** LatentMAS-style latent loop. The sender's final
  hidden state (post final-norm) is re-aligned (rescale to the mean input-embedding row
  norm; optional ridge map) and fed back through `inputs_embeds` for a bounded number of
  latent steps, extending the KV cache one position per step; the extended cache is then
  transferred. No text is decoded and re-encoded anywhere in the loop.

Probe: 5 word=number pairs in the sender turn, one target question per trial; scoring is
exact-integer match on 8 greedy new tokens. Harness integrity: greedy decoding, fixed seed
1234, fp32 CPU, and an A-vs-A double-probe sanity check whose KL must be exactly 0 (it is,
on every trial, both models). The first run (archived under `results/archive-broken-run/`,
finished 2026-10-16T03:49:25) scored 0/10 everywhere including the full-text control; three
probe defects were diagnosed (`RESEARCH-NOTES.md` section 4.0: raw prompts without chat
template, a logits probe mutating the shared cache, missing realignment in Experiment B),
fixed in protocol v2, and the suite re-run. One further self-found bug (rescale applied under
transform=none) was fixed mid-run; the Qwen Experiment B grid was re-run with the corrected
identity-none (`results/run-qwen05-expb2.log`), and the unchanged conditions reproduce their
earlier accuracies exactly.

## 2. Environment and models

| Field | Value |
|---|---|
| Python / torch / transformers | 3.11.15 / 2.14.1+cpu / 5.20.0 (tokenizers 0.23.3, safetensors 0.8.0, numpy 2.4.6) |
| OS / CPU | Windows 10, AMD EPYC 74F3, 6 physical / 12 logical cores, torch threads 6, 109.95 GiB RAM |
| Qwen2.5-0.5B-Instruct | revision `7ae557604adf67be50417f59c2c2f167def9a775`, 494,032,768 params, 24 layers, hidden 896, 2 KV heads x 64 head dim, vocab 151,936, fp32 eager CPU. Load 29.85 s; RSS after load 2,292.3 MiB |
| SmolLM2-135M-Instruct | revision `12fd25f77366fa6b3b4b768ec3050bf629380bac`, 134,515,008 params, 30 layers, hidden 576, 3 KV heads x 64 head dim, vocab 49,152, fp32 eager CPU. Load 6.76 s; RSS after load 868.6 MiB |
| KV bytes per cached token (fp32) | Qwen 24 KiB; SmolLM2 45 KiB |

## 3. Probe validity: chat vs induction, per model

The full-text positive control gates every claim (V1: receiver must answer with the answer in
plain text, >= 80%). The two models need different probe formats:

- **Qwen2.5-0.5B: chat-template probe valid.** Full-text control 50/50 (100%). All Qwen
  numbers in this scorecard are chat-probe numbers.
- **SmolLM2-135M: chat-template probe INVALID as an instrument.** Its own full-text control
  scores 15/50 (30%): the model cannot do sentence-style QA at this size even with the answer
  in plain text. This is a capability ceiling, not a harness defect. The auxiliary raw
  induction-format probe (bare pair-completion prefix, sender text states the pairs twice) is
  valid: full-text control 49/50 (98%). All SmolLM2 decision numbers are induction-probe
  numbers.
- The intervention battery (section 5) runs on the chat probe for both models. On SmolLM2
  that makes its accuracy columns ceiling-limited (the correct-cache reference itself scores
  5/20); the KL/L1 divergence columns remain valid measurements against that reference.

## 4. Scorecard: mode x model

The campaign-required table. Metrics per the campaign prompt: task count, task accuracy,
latent steps, cache bytes, peak RAM, sender time, receiver time, total latency, intervention
result. "Trials" are private-fact probe trials (5 pairs, one target question each).
Peak RSS is per-trial sampled process RSS (psutil), dominated by fp32 model residency; the
whole-run process peaks are Qwen 2,443.1 MiB (Exp B re-run; battery run samples to
2,462.5 MiB) and SmolLM2 967.8 MiB (Exp B induction re-run; full-suite run samples to
991.8 MiB). Total latency = sender forward + transfer (deepcopy) + receiver generation for
cache modes; for text modes the receiver re-prefills the text in one generate call (no
separate sender timing exists: the sender turn is fixed text).

| Mode | Model / probe | Trials | Task accuracy | Latent steps | Cache bytes | Peak RAM (MiB) | Sender (s) | Receiver (s) | Total (s) | Intervention result |
|---|---|---|---|---|---|---|---|---|---|---|
| No communication | Qwen2.5-0.5B, chat | 50 (probe) + 20 (battery) | 0/50 (0%); battery 0/20 | 0 | 0 | up to 2,428.2 | n/a (no artifact) | 0.65 (fresh question-only) | 0.65 | B_no_cache 0/20, mean KL vs correct cache 2.417: receiver cannot answer |
| No communication | SmolLM2-135M, induction | 50 (probe) | 0/50 (0%) | 0 | 0 | up to 967.1 | n/a | 0.80 | 0.80 | chat battery B_no_cache 0/20, KL 1.209 |
| Full text (positive control) | Qwen2.5-0.5B, chat | 50 + 20 | 50/50 (100%); battery 20/20 | 0 | 0 (text artifact: sender turn 81 tokens = 104 B; minimal pairs payload 49 B) | up to 2,428.2 | not timed (fixed sender turn) | 1.19 (re-prefill 104 tokens + 8 new) | 1.19 | F_full_text 20/20, KL 0.0: full recomputation equals cache continuation bit-for-bit |
| Full text (positive control) | SmolLM2-135M, induction | 50 | 49/50 (98%) | 0 | 0 (sender text 59 tokens = 125 B; pairs payload 49 B) | up to 967.1 | not timed | 1.00 (re-prefill 66 tokens + 8 new) | 1.00 | chat battery F_full_text 5/20 (ceiling), KL 0.0 |
| OCC-1 compact text | Qwen2.5-0.5B, chat | 20 (battery only) | 18/20 (90%) | 0 | 0 (compact single user turn, inline notes: 79.8 tokens mean, 131 B) | up to 2,462.5 (battery run) | not timed | 1.10 | G_compact_text 18/20, KL 0.259 vs correct cache |
| OCC-1 compact text | SmolLM2-135M, chat battery | 20 (battery only) | 1/20 (ceiling-limited, invalid instrument) | 0 | 0 (compact turn 80 tokens, ~131 B) | up to 854.0 | not timed | 1.85 | G_compact_text 1/20, KL 0.864 |
| KV-cache handoff | Qwen2.5-0.5B, chat | 50 + 20 | 50/50 (100%), 50/50 proven; battery A 20/20 | 0 | 1,991,147 B fp32 (1,944.5 KiB) / 995,573 B fp16 for an 81-token sender turn | up to 2,428.2 (run peaks to 2,462.5) | 0.83 (forward that builds the cache) | 0.85 (+0.018 transfer) | 1.70 | A 20/20; every content-destroying perturbation 0/20 with KL 2.4-15.3; see section 5 |
| KV-cache handoff | SmolLM2-135M, induction | 50 + 20 (chat battery) | 49/50 (98%), 49/50 proven | 0 | 2,729,779 B fp32 (2,665.8 KiB) / 1,364,889 B fp16 for a 59-token sender turn | up to 967.1 (process peak 967.8) | 0.31 | 0.80 (+0.019 transfer) | 1.13 | chat battery A 5/20 (ceiling), KL separation intact |
| Latent continuation | Qwen2.5-0.5B, chat | 10 per config, 19 configs (190 trials) | NEGATIVE: none 10/10 at 0 steps, 8/10 at 1, 8/10 at 2, 2/10 at 4, 0/10 at 8; rescale 9/3/0/0; rescale_ridge 9/3/0/0; random control 10/10/10/9 | grid 0/1/2/4/8 | 1,993,113 B fp32 at 0 steps growing to 2,189,721 B at 8 (one position per step) | 2,443.1 process peak | 0.83 (then 0.14-0.21 s per latent step) | 0.60-1.37 per config | e.g. rescale 8 steps: 3.39 | random-vector control hurts less than real latents: 10/10 at 1-4 steps vs 8/2 and 3/0 for real transforms |
| Latent continuation | SmolLM2-135M, induction | 10 per config, 15 configs (150 trials) | No measurable effect: 9-10/10 at every step count for every transform and the random control | grid 0/1/2/4 | 2,709,504 B fp32 at 0 steps growing to 2,893,824 B at 4 | up to 967.8 | 0.31 (then 0.07-0.09 s per step) | 0.60-0.77 per config | ~1.0 | neither helps nor hurts at this model scale (chat-probe grid is 1-2/10 everywhere: ceiling) |

Reading of the table: the KV-cache handoff reproduces the full-text control exactly on both
models under the probe each model can do (Qwen chat 50/50 vs 50/50; SmolLM2 induction 49/50
vs 49/50), with per-trial proof (with-cache correct AND no-cache wrong) on 50/50 and 49/50
trials. Latent continuation degrades accuracy monotonically with step count on Qwen and does
nothing on SmolLM2; section 6 has the grid.

## 5. Intervention battery (cache-quality controls)

Receiver next-token full-vocab KL/L1 divergence vs condition A (correct cache) at the first
decoding position, plus 8-token greedy generation scored for the target number. Every probe
runs on a fresh deepcopy with `use_cache=False`; the A-vs-A double-probe sanity KL is exactly
0.0 (max over 20 trials) on both models.

### 5.1 Qwen2.5-0.5B (chat probe, 20 trials)

| Condition | Correct | Mean KL vs A | Mean L1 vs A | Mean gen (s) |
|---|---|---|---|---|
| A_correct_cache | 20/20 | 0.0 | 0.0 | 0.8228 |
| B_no_cache | 0/20 | 2.416766 | 1.734372 | 0.631 |
| C_zeroed_cache | 0/20 | 15.257282 | 1.998105 | 1.4684 |
| D_random_cache | 0/20 | 12.003527 | 1.998214 | 1.4263 |
| E_unrelated_cache | 0/20 | 2.543691 | 1.764373 | 0.4995 |
| F_full_text | 20/20 | 0.0 | 2e-06 | 1.1285 |
| G_compact_text | 18/20 | 0.259071 | 0.4731 | 1.0973 |
| H_shuffled_cache | 20/20 | 0.0 | 1e-06 | 0.6336 |
| H2_unpaired_shuffle | 1/20 | 7.561553 | 1.940283 | 1.3127 |
| I_truncated_cache | 19/20 | 0.024065 | 0.08909 | 0.6519 |
| J_covgauss_cache | 0/20 | 8.542137 | 1.93358 | 1.2536 |
| K_haar_cache | 0/20 | 9.783115 | 1.991705 | 1.8903 |

Conditions: A correct cache; B no cache; C zeroed; D per-tensor Gaussian (mean/std matched);
E unrelated-task cache; F full text (positive control); G compact single-turn text; H
consistent shuffle (see 5.3); H2 unpaired shuffle (independent k/v permutations, breaks
pairing); I truncated (system + sender user turn only, assistant ack dropped); J CovGauss-mu
(per layer/head mean+cov matched, the Interlat control); K Haar rotation (per layer/tensor
head-dim rotation).

### 5.2 SmolLM2-135M (chat probe, 20 trials; accuracy ceiling-limited, KL valid)

| Condition | Correct | Mean KL vs A | Mean L1 vs A | Mean gen (s) |
|---|---|---|---|---|
| A_correct_cache | 5/20 | 0.0 | 0.0 | 1.6224 |
| B_no_cache | 0/20 | 1.209449 | 1.0216 | 2.4189 |
| C_zeroed_cache | 0/20 | 2.118687 | 1.32607 | 1.7824 |
| D_random_cache | 0/20 | 8.100362 | 1.880028 | 1.1875 |
| E_unrelated_cache | 0/20 | 0.778079 | 0.834048 | 1.4186 |
| F_full_text | 5/20 | 0.0 | 0.0 | 1.229 |
| G_compact_text | 1/20 | 0.864005 | 0.884471 | 1.8452 |
| H_shuffled_cache | 5/20 | 0.0 | 5e-06 | 1.3734 |
| H2_unpaired_shuffle | 0/20 | 3.128096 | 1.500315 | 1.3858 |
| I_truncated_cache | 1/20 | 0.742832 | 0.840975 | 1.2485 |
| J_covgauss_cache | 0/20 | 4.005751 | 1.650704 | 1.2717 |
| K_haar_cache | 0/20 | 7.830962 | 1.873005 | 1.3846 |

The condition ordering is identical to Qwen's (every content-destroying perturbation at 0/20
with large KL; H null; H2 separated), so the mechanism-level conclusions transfer; only the
absolute accuracy is ceiling-limited on this model.

### 5.3 New finding: the consistent shuffle is a NULL intervention

The pre-registered V3 condition list included "shuffled order" as a cache-destruction
control. As implemented (H: the same position permutation applied to keys and values
together on every layer), it is provably null on this probe class: 20/20 correct, KL exactly
0.0 (Qwen), 5/20 with KL 0.0 (SmolLM2, its ceiling). Attention over a fully attended prefix
is permutation-invariant when (k_i, v_i) pairs move together: RoPE is already baked into the
cached key tensors, each softmax weight stays attached to its own value, and the receiver's
position ids depend only on the cache length, so the receiver's next-token logits are
bit-identical. The shuffle therefore does not test cache content at all on a fully attended
prefix; it is reclassified as the order-invariance control. The informative pairing control
is H2 (independent k and v permutations): 1/20 correct, mean KL 7.56 (Qwen) - the multiset
of cached vectors is preserved but attention scores retrieve the wrong values. V3's intent
(cache content must matter) is carried by the remaining perturbations, which all separate
cleanly.

One nuance from I_truncated_cache (19/20, KL 0.024): the assistant acknowledgment carries
almost no task information; the sender's user-turn tokens hold the facts.

## 6. Latent continuation grid (Experiment B, the negative result)

Qwen2.5-0.5B, chat receiver, 10 trials per config. Transforms: none (raw post-final-norm
hidden fed back), rescale (row-wise rescale to the mean input-embedding row norm, the
LatentMAS default), rescale_ridge (ridge map then rescale), random (fixed random vector
rescaled to the target norm, the control).

| Config | Correct | Mean step (s) | Mean drift | Mean cos-to-first | Mean next-entropy | Mean top1 mass | Recv gen (s) |
|---|---|---|---|---|---|---|---|
| none_steps0 | 10/10 | - | - | - | - | - | 0.7928 |
| none_steps1 | 8/10 | 0.2135 | - | - | 0.0023 | 0.9998 | 0.6376 |
| none_steps2 | 8/10 | 0.1535 | 112.4703 | 0.9061 | 0.5302 | 0.8012 | 0.9946 |
| none_steps4 | 2/10 | 0.153 | 95.5192 | 0.7597 | 1.204 | 0.7802 | 1.0342 |
| none_steps8 | 0/10 | 0.1581 | 81.9376 | 0.5556 | 4.0846 | 0.4374 | 1.371 |
| rescale_steps0 | 10/10 | - | - | - | - | - | 0.7237 |
| rescale_steps1 | 9/10 | 0.1597 | - | - | 5.4019 | 0.126 | 0.6025 |
| rescale_steps2 | 3/10 | 0.1468 | 0.5778 | 0.1852 | 4.5704 | 0.233 | 1.1123 |
| rescale_steps4 | 0/10 | 0.1475 | 0.3992 | 0.2158 | 5.5212 | 0.1733 | 1.2237 |
| rescale_steps8 | 0/10 | 0.1499 | 0.2724 | 0.2202 | 6.1731 | 0.133 | 1.3595 |
| rescale_ridge_steps0 | 10/10 | - | - | - | - | - | 0.5955 |
| rescale_ridge_steps1 | 9/10 | 0.1373 | - | - | 5.402 | 0.126 | 0.8237 |
| rescale_ridge_steps2 | 3/10 | 0.1445 | 0.5778 | 0.1852 | 4.5704 | 0.233 | 1.1297 |
| rescale_ridge_steps4 | 0/10 | 0.1547 | 0.3992 | 0.2158 | 5.5212 | 0.1733 | 1.2526 |
| rescale_ridge_steps8 | 0/10 | 0.1403 | 0.2724 | 0.2202 | 6.1729 | 0.133 | 1.3291 |
| random_steps1 | 10/10 | 0.1482 | - | - | 5.1677 | 0.2236 | 0.7239 |
| random_steps2 | 10/10 | 0.1446 | - | - | 5.5267 | 0.2026 | 0.6733 |
| random_steps4 | 10/10 | 0.1468 | - | - | 5.3454 | 0.2346 | 0.7619 |
| random_steps8 | 9/10 | 0.1579 | - | - | 5.1578 | 0.2558 | 0.7891 |

Findings, stated plainly:

1. **Accuracy degrades monotonically with latent steps on the untrained models tested.**
   none: 10/10 -> 8/10 -> 8/10 -> 2/10 -> 0/10 at 0/1/2/4/8 steps; rescale and
   rescale_ridge degrade at least as fast (9/10 at 1 step, 3/10 at 2, 0/10 from 4). The
   LatentMAS realignment fixes the drift magnitude (0.58/step vs 112/step for raw none) but
   not the accuracy; ridge is indistinguishable from rescale.
2. **Real latent continuations corrupt the cache more than injected noise.** The random
   control keeps 10/10 at 1-4 steps and 9/10 at 8. A real hidden state fed back is worse
   than a random vector at the same norm.
3. **Mechanism.** The raw post-final-norm hidden (norm 240.2 on Qwen) acts as a
   near-deterministic pseudo-token (next-entropy 0.0023, top-1 mass 0.9998 at step 1),
   while the rescaled latent (norm 0.45) lands in high-entropy territory (entropy 5.4).
   Counterintuitively, raw-none survives better at 2 steps (8/10 vs 3/10 for rescale).
4. **SmolLM2 shows no effect in either direction.** Induction grid: 9-10/10 at every step
   count for every transform and the random control (9/10 baseline; none_steps4 even 10/10).
   Chat grid is 1-2/10 everywhere (ceiling). At this model scale the latent loop neither
   helps nor hurts.
5. **Scope of the negative.** This is a result about untrained small models (135M and 0.5B,
   no receiver training). It does not rule out trained or future variants - the Interlat
   pattern trains a receiver head precisely because raw latent reuse does not transfer. It
   rules out shipping THIS mechanism (untrained latent continuation on the pinned harness
   models) now.

## 7. Cost honesty: cache bytes vs text bytes

| Artifact | Qwen2.5-0.5B (chat) | SmolLM2-135M (induction) |
|---|---|---|
| KV cache, fp32, per handoff | 1,991,147 B (1,944.5 KiB) for 81 sender tokens | 2,729,779 B (2,665.8 KiB) for 59 sender tokens |
| KV cache, fp16 | 995,573 B | 1,364,889 B |
| Minimal text payload (word=number pairs) | 49 B mean (44-53) | 49 B mean |
| Full sender-turn text | 104 B mean (81 tokens) | 125 B mean (59 tokens) |
| Compact single-turn text (G condition) | 131 B mean (79.8 tokens) | ~131 B |
| Ratio, fp32 cache vs pairs payload | ~40,500x | ~55,500x |
| Per cached token | 24 KiB fp32 | 45 KiB fp32 |

- The KV artifact loses on bytes at every measured scale, by roughly four orders of
  magnitude against the equivalent text payload. fp16 halves it and does not close the gap.
- The cache wins two things only: receiver wall-clock at this scale (Qwen chat: 0.85 s with
  cache vs 1.19 s re-prefilling full text, 0.72x; SmolLM2 induction 0.80 s vs 1.00 s,
  0.80x) and continuation without re-encoding. Note the no-cache receiver is faster still
  (0.65 s) because it attends over fewer tokens - and wrong (0/50).
- Projections at the G2 scale (4096 tokens): Qwen ~96 MiB fp32 per handoff (inside the
  128 MiB per-handoff cap); SmolLM2 ~180 MiB fp32 (over the cap; ~90 MiB fp16 inside).
  These are projections from the per-token anchors, not measurements.
- The pre-registered latency gate (G2) is defined at >= 4096 transferred tokens and was not
  measured: the probe contexts are 59-108 tokens. The 0.72x receiver ratio above is a
  small-context observation, not a G2 claim.
- The resource failure is model residency, not the artifact: the per-handoff artifact
  (1.99 MB) is far under the 128 MiB cap; the fp32 Qwen process peaks at 2,443.1 MiB
  against the 1 GiB sidecar budget.

## 8. Go/no-go verdict (pre-registered framework)

Framework: `research/integration/OCC3-INTEGRATION-DESIGN.md` section 1, pre-registered
before the harness re-runs. Decision rule (section 1.3): "GO: V1-V3 plus G1 plus G3 plus
(G2 or G4) on at least one pinned pair." "NO-GO: V1-V3 hold but G1 fails, or neither G2 nor
G4 holds." "INVALID: any V gate fails" (a harness defect, not a decision). Verdict entry:
`reports/occ23-campaign/EXPERIMENT-LEDGER.md`, 2026-10-16 ~12:5xZ.

| Gate | Pre-registered criterion | Outcome | Evidence |
|---|---|---|---|
| V1 positive control | full-text condition >= 80% | **PASS** | Qwen chat 50/50 (100%); SmolLM2 induction 49/50 (98%). SmolLM2 chat 15/50 (30%) is a model-capability ceiling; that probe is invalid as an instrument for that model and is not a decision input |
| V2 determinism | repeat runs agree trial-for-trial (greedy, fixed seeds) | **PASS** | A-vs-A double-probe KL exactly 0.0 (max over 20 trials, both models); unchanged Exp B conditions reproduce exactly across two independent Qwen runs (rescale 9/3/0/0, rescale_ridge 9/3/0/0, random 10/10/10/9 in both `run-qwen05.log` and `run-qwen05-expb2.log`) |
| V3 specificity | correct cache beats perturbed conditions, non-overlapping paired bootstrap CIs | **PASS** | Qwen: with-cache 50/50 vs no-cache 0/50, bootstrap diff 1.0, 95% CI [1.0, 1.0]; battery 20/20 vs 0/20 with mean KL 2.42-15.26 for zeroed / random / unrelated / covgauss / haar / unpaired shuffle. Consistent shuffle is null by construction (section 5.3) and is reclassified as the order-invariance control; truncated degrades mildly (19/20, KL 0.024) |
| G1 accuracy (required) | with-cache >= text accuracy - 5 points AND with-cache > no-cache with 95% paired bootstrap CI | **PASS** | Qwen chat: with-cache 50/50 equals full-text 50/50, and exceeds the compact-text battery (18/20, 90%) by 10 points; vs no-cache 0/50, CI [1.0, 1.0]. SmolLM2 induction: 49/50 equals 49/50; CI [0.94, 1.0] |
| G2 latency (optional path) | at >= 4096 transferred tokens, receiver wall-clock <= 0.8x full-text on >= 90% of trials | **NOT MEASURED** | Probe contexts are 59-108 tokens. Receiver ratio at that scale: 0.72x (Qwen chat), 0.80x (SmolLM2 induction). The gate is defined at >= 4096 tokens; no claim is made |
| G3 resources (required) | sidecar peak RSS <= 1 GiB and per-handoff cache <= 128 MiB | **FAIL (Qwen)** | Qwen fp32 harness process peak 2,443.1 MiB (about 2.4x the 1,024 MiB budget; battery-run samples to 2,462.5 MiB). Per-handoff artifact 1.99 MB is far under the 128 MiB cap - residency is the breach. SmolLM2 peaks at 991.8 MiB sampled (under), but it has no valid chat probe |
| G4 unique capability (optional path) | over-bound (> 16,384-char) contexts beat truncated text by >= 10 points | **UNTESTED** | No over-bound context trials exist in protocol v2 (design open question OQ5) |
| **Decision** | GO requires V1-V3 + G1 + G3 + (G2 or G4) on at least one pinned pair | **NO-GO** | G3 fails on the only pair with a valid chat probe (Qwen2.5-0.5B); neither G2 nor G4 is measured, so no optional path is satisfied either. The run is valid (no V failure), so the branch is NO-GO, not INVALID |

Consequences, per the pre-registered NO-GO branch:

- No production integration. No OCC-3 code path exists in the product; `core/occ/mod.rs`
  keeps documenting latent communication as "not implemented". Nothing in this campaign
  claims otherwise.
- The LAT telemetry indicator stays reserved: there is no producer, so no LAT activity can
  be reported (the OCC-3 design requires confirmed transfer AND receiver consumption
  before any LAT).
- The production design (`research/integration/OCC3-INTEGRATION-DESIGN.md`, 367 lines,
  89 file:line citations) is retained for a future revisit. Its own pre-registered revisit
  triggers apply: a stable cross-instance cache API in transformers, a trained receiver
  head (the Interlat pattern), or a concrete production need for over-bound context
  transfer; the verdict entry adds a GPU / larger-context rerun as the natural next
  experiment for G2/G3.
- What survives the NO-GO as research findings: the KV-cache handoff itself is real and
  causally proven on both harness models (section 4 and the V/G1 gates); the intervention
  battery (section 5) including the consistent-shuffle null; the latent-continuation
  negative (section 6) with the random-control contrast; and the cost table (section 7).

## 9. Provenance and reproduction

Raw results (re-analyzable without new model calls):

- `research/latent_handoff/results/qwen05-results.json` - Qwen Exp A (chat), intervention
  battery, Exp B grid; exp_b from the corrected identity-none re-run
- `research/latent_handoff/results/smollm2-results.json` - SmolLM2 Exp A (chat + induction),
  battery, Exp B (chat + induction)
- `research/latent_handoff/results/archive-broken-run/smollm2-results.json` - the v1
  protocol run (0/10 everywhere including full text), kept as the bugfix-history record
- Run logs: `results/run-qwen05.log`, `results/run-qwen05-expb2.log`,
  `results/run-smollm2.log`, `results/run-smollm2-expb-ind2.log`
- Generated markdown: `research/latent_handoff/RESULTS-EXP-A.md`, `RESULTS-EXP-B.md`
- Harness: `model_utils.py`, `kv_handoff.py`, `latent_continuation.py`,
  `interventions.py`, `run_experiments.py`, `results_writer.py`

Reproduction (fp32 CPU, greedy, seed 1234; torch 2.14.1+cpu, transformers 5.20.0):

```
cd research/latent_handoff
.venv/Scripts/python.exe run_experiments.py --model qwen05
.venv/Scripts/python.exe run_experiments.py --model qwen05 --only exp_b
.venv/Scripts/python.exe run_experiments.py --model smollm2
.venv/Scripts/python.exe run_experiments.py --model smollm2 --only exp_b_induction
```

The `--only exp_b` invocation is the corrected identity-none re-run; the JSON writer
preserves sections not re-run. Model revisions and the environment block are recorded
inside each results JSON.
