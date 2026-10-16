"""Render RESULTS-EXP-A.md and RESULTS-EXP-B.md from results/*.json.

Both markdown files are regenerated from every results/<model>-results.json
present in the results directory; the JSON files are the single source of truth.
"""

from __future__ import annotations

import json
import os

MODELS = ["qwen05", "smollm2"]
TAGS = {"smollm2": "SmolLM2-135M-Instruct", "qwen05": "Qwen2.5-0.5B-Instruct"}
BUGFIX_NOTE = """## Bugfix history

The first run (2026-10-16, before the protocol-v2 fixes) reported 0/10 on every
condition including the full-text positive control. Root causes, diagnosed in
RESEARCH-NOTES.md section 4.0 and fixed before the re-run:

1. Prompts were raw text without the model chat template, so even full-text
   transfer failed (broken probe ceiling). Fixed: all prompts render through the
   model chat template; the sender turn is completed by a fixed assistant
   acknowledgment so the cache ends at a turn boundary.
2. interventions.py probed logits on a live shared cache without use_cache=False,
   so every probe extended the cache in place (A-vs-A KL 1.99 instead of 0).
   Fixed: every probe runs on a fresh deepcopy with use_cache=False; the battery
   now includes an A-vs-A double-probe sanity check (KL must be exactly 0).
3. Experiment B fed raw post-final-norm hidden states (std 2.4) back through
   inputs_embeds without the LatentMAS norm rescale (target: mean input-embedding
   row norm), producing 48-85 L2 drift per step. Fixed: the realignment
   (rescale, optional ridge map) from RESEARCH-NOTES section 1.3 is applied per
   latent step; the unmodified feed remains as the "none" transform for
   comparison.
"""


def load_all(results_dir: str) -> dict:
    out = {}
    for key in MODELS:
        p = os.path.join(results_dir, f"{key}-results.json")
        if os.path.exists(p):
            with open(p, "r", encoding="utf-8") as f:
                out[key] = json.load(f)
    return out


def env_table(env: dict) -> str:
    rows = [
        ("Python", env.get("python")),
        ("torch", env.get("torch")),
        ("transformers", env.get("transformers")),
        ("tokenizers", env.get("tokenizers")),
        ("safetensors", env.get("safetensors")),
        ("numpy", env.get("numpy")),
        ("OS", env.get("os")),
        ("CPU", env.get("cpu")),
        ("Physical cores", env.get("cpu_count_physical")),
        ("Logical cores", env.get("cpu_count_logical")),
        ("CPU freq (MHz)", env.get("cpu_freq_mhz")),
        ("torch threads", env.get("torch_num_threads")),
        ("Total RAM (GiB)", env.get("ram_total_gb")),
    ]
    return "| Field | Value |\n|---|---|\n" + "\n".join(f"| {k} | {v} |" for k, v in rows)


def model_header(res: dict) -> str:
    m = res["model"]
    return (
        f"- Model: `{m['model_id']}` @ revision `{m['revision']}`\n"
        f"- Parameters: {m['n_params']:,} | layers: {m['n_layers']} | "
        f"hidden: {m['hidden_size']} | vocab: {m['vocab_size']} | "
        f"dtype: {m['dtype']} | attn: {m['attn_implementation']} | device: CPU\n"
        f"- Load time: {res['load_s']}s | RSS after load: {res['rss_after_load_mb']} MiB | "
        f"RSS peak: {res.get('rss_peak_mb')} MiB | RSS end: {res.get('rss_end_mb')} MiB"
    )


def exp_a_section(res: dict, key: str, title: str, exp_key: str) -> list:
    s = res[exp_key]["summary"]
    trials = res[exp_key]["trials"]
    n = s["n_trials"]
    boot = s.get("bootstrap_with_vs_without",
                 {"diff": None, "ci_low": None, "ci_high": None, "p_gt_0": None})
    lines = [f"### {title}", ""]
    lines += [
        f"- With-cache correct: **{s['with_cache_correct']}/{n}**",
        f"- Full-text correct (positive control): **{s['full_text_correct']}/{n}**",
        f"- No-cache correct: **{s['without_cache_correct']}/{n}**",
        f"- Proven trials (with correct AND without wrong): "
        f"**{s['proven_trials']}/{n}**",
        f"- Positive control pass (full >= 80%): **{s['positive_control_pass']}**",
        f"- With-cache within 5 points of full text: **{s['with_within_5pts_of_full']}**",
        f"- With-cache beats no-cache: **{s['with_beats_without']}**",
        f"- Paired bootstrap with-vs-without: diff={boot['diff']}, "
        f"95% CI [{boot['ci_low']}, {boot['ci_high']}], P(diff>0)={boot['p_gt_0']}",
        f"- **GATE PASS: {s['gate_pass']}**",
        f"- Mean sender forward: {s['mean_sender_forward_s']}s | "
        f"mean transfer (deepcopy): {s['mean_transfer_s']}s | "
        f"mean receiver generation: {s['mean_receiver_gen_s']}s | "
        f"mean fresh generation: {s['mean_fresh_gen_s']}s | "
        f"mean full-text generation: {s['mean_full_text_gen_s']}s",
        f"- Mean cache artifact: {s['mean_cache_bytes_fp32']:,} bytes fp32 "
        f"({s['mean_cache_bytes_fp32']/1024:.1f} KiB) | "
        f"{s['mean_cache_bytes_fp16']:,} bytes fp16 | "
        f"vs text payload ~{trials[0].get('text_artifact_bytes', 0)} bytes",
        f"- Mean sender tokens: {s['mean_sender_tokens']}",
        "",
        "#### Per-trial records",
        "",
        "| Trial | Target | With-cache | OK | Full-text | OK | No-cache | OK | "
        "Sender fwd (s) | Transfer (s) | Recv gen (s) | Cache fp32 (bytes) |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for r in trials:
        def cut(x, n_=26):
            x = (x[:n_] + "...") if len(x) > n_ + 3 else x
            return x.replace("|", "/")
        lines.append(
            f"| {r['trial']} | {r['target_word']}={r['target_number']} "
            f"| {cut(r['with_cache_answer'])} | {r['with_cache_correct']} "
            f"| {cut(r['full_text_answer'])} | {r['full_text_correct']} "
            f"| {cut(r['without_cache_answer'])} | {r['without_cache_correct']} "
            f"| {r['sender_forward_s']} | {r['transfer_s']} | {r['receiver_gen_s']} "
            f"| {r['cache_bytes_fp32']:,} |")
    lines.append("")
    return lines


def interventions_section(res: dict) -> list:
    iv = res["interventions"]
    s = iv["summary"]
    n = res["trials_iv"]
    t0 = iv["trials"][0]
    lines = [
        "## Intervention battery (chat probe)",
        "",
        "Receiver next-token full-vocab KL/L1 divergence vs condition A (correct "
        "cache) at the first decoding position. Every probe runs on a fresh "
        "deepcopy with use_cache=False; the A-vs-A double-probe sanity KL is "
        f"{s['a_vs_a_kl_max']} (must be 0).",
        "",
        "| Condition | Correct | Mean KL vs A | Mean L1 vs A | Mean gen (s) |",
        "|---|---|---|---|---|",
    ]
    for cond, v in s.items():
        if not isinstance(v, dict):
            continue
        lines.append(f"| {cond} | {v['correct']}/{n} | {v['mean_kl_vs_A']} "
                     f"| {v['mean_l1_vs_A']} | {v['mean_gen_s']} |")
    lines += [
        "",
        f"- Sender turn: {t0['sender_tokens']} tokens | full text: "
        f"{t0['full_text_tokens']} tokens | compact text: {t0['compact_text_tokens']} "
        f"tokens | cache fp32: {t0['cache_bytes_fp32']:,} bytes",
        "- Conditions: A correct cache; B no cache; C zeroed; D per-tensor "
        "Gaussian (mean/std matched); E unrelated-task cache; F full text "
        "(positive control); G compact text; H consistent shuffle (order-"
        "invariance control, null by construction); H2 unpaired shuffle "
        "(independent k/v permutations, breaks pairing); I truncated "
        "(sender user turn only); J CovGauss-mu (per layer/head mean+cov matched); "
        "K Haar rotation (per layer/tensor head-dim rotation).",
        "",
    ]
    return lines


def write_exp_a(results_dir: str, data: dict) -> str:
    first = next(iter(data.values()), None)
    lines = [
        "# RESULTS - Experiment A: KV-Cache Handoff (OCC-3 probe, protocol v2)",
        "",
        "Standalone CPU-first harness in `research/latent_handoff/`. Logical Agent A",
        "processes a completed chat-template turn (user notes + fixed assistant",
        "acknowledgment) with `use_cache=True`; the captured `DynamicCache` is",
        "deep-copied (the transfer artifact) and handed to logical Agent B, which",
        "continues generation from its own user turn, attending to A's context only",
        "through the cache. The receiver question never contains the secret.",
        "",
        "Gate (per RESEARCH-NOTES 4.1): full-text positive control >= 80%, with-cache",
        "within 5 points of full text, with-cache > no-cache, and per-trial proof",
        "(with-cache correct AND no-cache wrong).",
        "",
    ]
    if first:
        lines += ["## Environment", "", env_table(first["env"]), ""]
    for key in MODELS:
        if key not in data:
            continue
        res = data[key]
        lines += [f"## {TAGS[key]}", "", model_header(res), ""]
        if "exp_a" in res:
            lines += exp_a_section(res, key, "Chat-template probe (primary)", "exp_a")
        if "exp_a_induction" in res:
            lines += exp_a_section(res, key, "Raw induction-format probe (auxiliary)",
                                   "exp_a_induction")
        if "interventions" in res:
            lines += interventions_section(res)
    lines += [BUGFIX_NOTE]
    return "\n".join(lines)


def write_exp_b(results_dir: str, data: dict) -> str:
    first = next(iter(data.values()), None)
    lines = [
        "# RESULTS - Experiment B: Bounded Latent Continuation (protocol v2)",
        "",
        "LatentMAS-style latent loop ported to transformers 5.20.0 (RESEARCH-NOTES",
        "sections 1.2-1.3). The sender's final hidden state (post final-norm) is",
        "re-aligned and fed back through `inputs_embeds` for a bounded number of",
        "latent steps, extending the KV cache one position per step; the extended",
        "cache is then transferred to Agent B. No text is decoded and re-encoded",
        "anywhere in the loop.",
        "",
        "Transforms: `none` (raw hidden, the broken v1 baseline kept for comparison),",
        "`rescale` (row-wise rescale to the mean input-embedding row norm, the",
        "LatentMAS default), `rescale_ridge` (ridge map then rescale). Control:",
        "`random` (fixed random vector rescaled to the target norm).",
        "",
    ]
    if first:
        lines += ["## Environment", "", env_table(first["env"]), ""]
    for key in MODELS:
        if key not in data or "exp_b" not in data[key]:
            continue
        res = data[key]
        b = res["exp_b"]
        proj = b["projection_check"]
        probe_note = ("chat probe" if b.get("probe", "chat") == "chat"
                      else "raw induction probe")
        lines += [f"## {TAGS[key]} ({probe_note})", "", model_header(res), ""]
        lines += [
            "## Projection check (hidden state -> input embedding space)",
            "",
            f"- hidden_size: {proj['hidden_size']} | embedding dim: {proj['embedding_dim']} "
            f"| dims match: **{proj['dims_match']}** | needs projection: "
            f"{proj['needs_projection']}",
            f"- Token embedding stats: {proj['token_embedding_stats']}",
            f"- Last hidden state stats: {proj['last_hidden_stats']}",
            f"- {proj['note']}",
            "",
            "## Realigner metadata",
            "",
        ]
        for t, m in b["realigner_meta"].items():
            lines.append(f"- {t}: target_norm={m['target_norm']}, "
                         f"embedding_std={m['embedding_std']}, "
                         f"ridge max diagonal deviation from 1={m['ridge_trace_dev']}")
        lines += [
            "",
            "## Latent-step grid (private-fact probe, chat receiver)",
            "",
            "| Config | Correct | Mean step (s) | Mean drift | Mean cos-to-first | "
            "Mean next-entropy | Mean top1 mass | Recv gen (s) |",
            "|---|---|---|---|---|---|---|---|",
        ]
        for cfg, v in b["grid"].items():
            lines.append(
                f"| {cfg} | {v['correct']}/{v['n_trials']} | {v.get('mean_step_s', '-')} "
                f"| {v.get('mean_drift', '-')} | {v.get('mean_cosine_to_first', '-')} "
                f"| {v.get('mean_next_entropy', '-')} | {v.get('mean_top1_mass', '-')} "
                f"| {v['mean_receiver_gen_s']} |")
        lines += ["", "## Per-step telemetry (trial 0)", ""]
        seen = set()
        for r in b["trials"]:
            if r["trial"] != 0 or "latent_telemetry" not in r:
                continue
            cfg = f"{r['transform']}_steps{r['latent_steps']}"
            if cfg in seen:
                continue
            seen.add(cfg)
            lines.append(f"### {cfg}")
            lines.append("")
            for s in r["latent_telemetry"]:
                lines.append(
                    f"- step {s['step']}: latent_norm={s.get('latent_norm')} "
                    f"latent_std={s.get('latent_std')} hidden_std={s.get('hidden_std')} "
                    f"drift={s.get('drift_from_prev')} cos_first={s.get('cosine_to_first')} "
                    f"entropy={s['next_token']['entropy']} "
                    f"top1={s['next_token']['top1_mass']}")
            lines.append("")
        lines.append("")

        if "exp_b_induction" in res:
            bi = res["exp_b_induction"]
            lines += [f"## {TAGS[key]} (raw induction probe)", ""]
            lines += [
                "The chat-template probe is above this model's capability "
                "(positive control fails), so the latent grid is re-run in the "
                "induction format whose positive control passes.",
                "",
                "| Config | Correct | Mean step (s) | Mean drift | Mean cos-to-first | "
                "Mean next-entropy | Mean top1 mass | Recv gen (s) |",
                "|---|---|---|---|---|---|---|---|",
            ]
            for cfg, v in bi["grid"].items():
                lines.append(
                    f"| {cfg} | {v['correct']}/{v['n_trials']} | {v.get('mean_step_s', '-')} "
                    f"| {v.get('mean_drift', '-')} | {v.get('mean_cosine_to_first', '-')} "
                    f"| {v.get('mean_next_entropy', '-')} | {v.get('mean_top1_mass', '-')} "
                    f"| {v['mean_receiver_gen_s']} |")
            lines.append("")
    lines += [BUGFIX_NOTE]
    return "\n".join(lines)


def write_all_markdown(results_dir: str) -> None:
    data = load_all(results_dir)
    base = os.path.dirname(results_dir)
    with open(os.path.join(base, "RESULTS-EXP-A.md"), "w", encoding="utf-8",
              newline="\n") as f:
        f.write(write_exp_a(results_dir, data))
    with open(os.path.join(base, "RESULTS-EXP-B.md"), "w", encoding="utf-8",
              newline="\n") as f:
        f.write(write_exp_b(results_dir, data))
