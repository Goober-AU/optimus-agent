# DGX Environment (verified 2026-10-21, read-only)

**Worktree:** `optimus-occ3-20261021` · **Date:** 2026-10-21 · **Mode:** offline documentation from existing evidence; no live claims.

**Citation convention.** `occ3-campaign/<path>` refers to the campaign evidence root `C:/Users/openclawuser/Optimus-Assistant/profile/workspace/occ3-campaign/`; worktree files are repository-relative. Full hashes in the evidence register.

**Staleness note.** All facts below were verified by read-only inspection on 2026-10-21 and have not been re-verified since. The deployment history section records the two failed OCC-3 deployment attempts and their verified recoveries on the same day; after the second recovery the original service was verified healthy and no further deployment has been performed under the current approval [E-STATE, E-REC2].

## 1. Host, hardware, driver

- Host: `TRY-72998-gpu01` (user nvidia). Access through a Pomerium SSH gateway [E-ENV].
- 8x NVIDIA B300 SXM6 AC, 275040 MiB each; live serving used ~272/275 GiB on sampled GPUs at inspection time [E-ENV].
- NVIDIA driver 595.71.05, CUDA 13.0 (torch reports 2.13.0+cu130) [E-ENV].
- `/data` on `/dev/md127`: 23 TB total, 19 TB free [E-ENV].
- Docker 29.6.2 (version recorded during deployment failure analysis) [E-FA1].

## 2. Live serving container: `neo-glm53`

- Image: `lmsysorg/sglang:latest`, digest `sha256:b1259f3ea3275f66237c498ea388919729018bc9f01c3d638391e06e2cf3f469`, 52.4 GB; SGLang version 0.5.21 (verified via docker exec import) [E-ENV].
- Container id (full, from sanitized inspect): `c4a26b345ec6a5348ad3cab4554e5f58399b83bd23cf581bb5858a979e3c1927` [E-ORIGSAN].
- Port: `127.0.0.1:30001 -> 30001` (localhost only; external via launchpad-traefik + Pomerium) [E-ENV, E-ORIGSAN].
- Mount: `/data/neo-glm53 -> /data/neo-glm53` (single bind, recorded in `HostConfig.Mounts`, one entry; `HostConfig.Binds` is null) [E-ORIGSAN].
- Network: `neo-k3-ingress-net`; IPC host; runtime nvidia; restart policy `no`; device request all GPUs [E-ORIGSAN].
- Launch command (api-key redacted; full arg vector in the sanitized inspect): `python -m sglang.launch_server --model-path /data/neo-glm53/model --tokenizer-path /data/neo-glm53/model --trust-remote-code --served-model-name glm-5.3 --tp-size 8 --context-length 1048576 --reasoning-parser glm45 --tool-call-parser glm47 --host 0.0.0.0 --port 30001 --default-chat-template-kwargs {"reasoning_effort":"max","clear_thinking":true} --speculative-algorithm EAGLE --speculative-num-steps 5 --speculative-eagle-topk 1 --speculative-num-draft-tokens 6 --speculative-adaptive --speculative-adaptive-config /data/neo-glm53/updates/adaptive-20261004T060732559176Z/adaptive-mtp.json --chunked-prefill-size 32768 --max-running-requests 32 --cuda-graph-max-bs-decode 32 --mem-fraction-static 0.9 --kv-cache-dtype fp8_e4m3 --api-key <REDACTED>` [E-ENV, E-ORIGSAN].

## 3. Model checkpoint

- Path `/data/neo-glm53/model`: 152 files; 141 safetensors shards plus config.json, chat_template.jinja, generation_config.json, tokenizer.json (20.2 MB), tokenizer_config.json, model.safetensors.index.json [E-ARTMETA, E-ENV].
- Revision `aca966e4e02791568aa6a4ced368624b3d897f42` (model-revision.txt; download metadata) [E-ENV, E-MIMV3].
- Architecture `GlmMoeDsaForCausalLM`; model type `glm_moe_dsa`; hidden size 6144; 78 hidden layers; head_dim 192; first_k_dense_replace 3; GLM MTP (`index_share_for_mtp_iteration` true); dtype bfloat16 checkpoint served as FP8 (`quant_method: fp8`) [E-ENV, E-MIMV3].
- `model_config_sha256`: `3ac72612095574542f7fff847ada8e59d9199dd8af44bdf625d7e02615572e69` [E-MIMV3].
- Checkpoint evidence scope: existing download metadata and file sizes only; no recomputed shard content checksum [E-MIMV3].

## 4. Other containers and directories

- `neo-k3-ingress` (nginx:alpine, port 80) — route ingress; `launchpad-traefik` (traefik:v3.7.1) — Pomerium-fronted routing [E-ENV].
- Exited at inspection time: `neo-glm53-bf16`, `neo-dsv41-1017` (+downloads), `neo-k3`, several timestamped snapshots [E-ENV].
- `/data` directories: `/data/neo-k3`, `/data/neo-glm53`, `/data/neo-glm53-bf16`, `/data/neo-dsv41-1017`; `/data/neo-glm53` contains api-key, launch-args.json, model-revision.txt, serving-image.txt, select-dgx-model.py, updates/, runs/, huggingface/ [E-ENV].

## 5. Safety posture for OCC-3 deployment (from the inspection)

- Do not touch the `neo-glm53` container/image/weights; GPU memory is ~fully committed (mem-fraction 0.9) [E-ENV].
- Extension deployment must be additive (sidecar or in-process plugin), never a replacement; a second full model instance is not possible [E-ENV].
- Rollback principle: stop/remove only OCC-3-added containers/files; the original deployment stays untouched [E-ENV].

## 6. Deployment history state (2026-10-21)

Two OCC-3 deployment attempts were made and both failed; both were recovered with the original service verified:

1. First attempt (~03:34-04:19 UTC): derived image built and plugin preflight passed, but the new container exited within ~1 s. Root cause: docker 29.6.2 range templates emit a trailing blank line even for non-empty ranges; the deploy script's line-based capture turned it into an empty-string element in the replicated launch command, so the container ran `python3 "" -m sglang.launch_server ...` and Python could not find `__main__` in the workdir. Total downtime ~41 minutes; recovery via the manual user runbook; failed container preserved as `neo-glm53-occ3-failed-20261021T041217Z` [E-FA1].
2. Second attempt (~05:04 UTC): candidate started and exited code 1. Root cause: the controller normalised mounts from `NetworkSettings.Mounts` instead of docker's top-level `Mounts`, so both normalised mount lists were empty, the bind mount was omitted, `diff_configs()` falsely reported parity, and the server failed resolving `/data/neo-glm53/model` as an invalid Hugging Face repository id. Rollback was slow because `wait_health()` only polled HTTP and never checked Docker state, treating the dead candidate as a still-loading server for twenty minutes. Recovery completed 05:31 UTC; failed candidate preserved as `neo-glm53-occ3-failed-second-20261021T050435Z` [E-REC2].

Post-recovery verification (2026-10-21 ~04:4xZ and 05:30-05:31 UTC): original container restored as `neo-glm53` (config, HostConfig, Mounts, Image and Id matched the preserved original exactly); `/health` 200; `/v1/models` 200 (glm-5.3); authenticated chat 200 with nonempty expected response; ingress `neo-k3-ingress -> neo-glm53:30001/health` reachable; OCC-3 routes absent (`/occ3/v1/status` and `/occ3/v1/capabilities` return 404 with valid auth); nothing auto-redeploys (restart policies `no`, no cron/systemd/scheduled tasks referencing the occ3 deployment); native Optimus proof completed (child selected `dgx-glm53/glm-5.3`, answered a challenge, one agent_message tool call, receipt `agentmsg_1add7b64-e7cb-4c52-b758-840514f5e1a4`) [E-FA1, E-REC2].

Deployment control processes were stopped and scripts disarmed during the recovery-only phase; the current campaign authorization records zero live attempts under the latest approval and `production_swap` NOT_STARTED [E-STATE].

## 7. PENDING LIVE EVIDENCE

1. A fresh read-only re-inspection to confirm the environment facts above still hold (all facts are as of 2026-10-21).
2. Any successful OCC-3 deployment state on this host (none exists; GATE 5 not started under current approval).
3. Live measurements of GPU memory/throughput with the extension installed (GATE 11 inputs).

## Evidence register

| Id | Path | sha256 |
|---|---|---|
| E-ENV | occ3-campaign/dgx-inspect/ENVIRONMENT.md | e0667968a38430e9359084242fe0553f0571fad2cdca4a4a4f4a08a345e0afb7 |
| E-FA1 | occ3-campaign/dgx-inspect/OCC3-DEPLOY-FAILURE-ANALYSIS.md | 084be196931164e17293ab3f60241cad8105c4c57a47b8d3894b278d945173d9 |
| E-REC2 | occ3-campaign/recovery-only/SECOND-DEPLOYMENT-RECOVERY.md | 5e0a9c06f4c9a5f8e91322fcb20258964a753e333d5d00351f0aef68390b80a7 |
| E-ORIGSAN | occ3-campaign/v3-controlled-deploy/original-sanitized.json | b1e15312a042f39bb2620139b7de5251517f242feb46bc571cf81fbb1cae89f4 |
| E-ARTMETA | occ3-campaign/v3-controlled-deploy/artifact-metadata.json | 08287cb9acd2a6852acaef154f1d26ce39e886c1995542c0822e468ace0bc141 |
| E-MIMV3 | occ3-campaign/v3-controlled-deploy/model-identity-metadata-v3.json | 11bc9ce88cd24a6cb8dd8473be7fa31f7975658257cf90e3ade9be2273265467 |
| E-STATE | occ3-campaign/v3-controlled-deploy/STATE.json | 19a497e2c5682ccd2f03cd6dc364b02ca8e93f8b29dd2cc29939853cd0e5223d |
