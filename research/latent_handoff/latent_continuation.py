"""EXPERIMENT B (protocol v2): bounded latent continuation before handoff.

Port of the LatentMAS latent step loop (RESEARCH-NOTES section 1) to
transformers 5.20.0, bounded and instrumented:

1. Sender forward over the completed chat turn with output_hidden_states=True;
   last_hidden = hidden_states[-1][:, -1, :] (post final-norm, the LM-head
   input).
2. Realignment (RESEARCH-NOTES 1.3) per latent step:
   - none: feed the hidden state back unmodified (the broken v1 baseline;
     out-of-distribution for the embedding path, kept for comparison)
   - rescale: row-wise rescale to target_norm = mean ||W_in row||_2
   - rescale_ridge: first apply the ridge map
     M = solve(W_out^T W_out + 1e-5 I, W_out^T W_in), then rescale
3. Latent step: model(inputs_embeds=latent_vec[1,1,D], attention_mask=
   ones(past+1), past_key_values=cache, use_cache=True,
   output_hidden_states=True); the cache grows one position per step; the
   next latent input is the fresh final hidden state (feedback recurrence).
4. Transfer the extended cache (deepcopy) to Agent B; append the receiver
   question; generate; score.

No text is decoded and re-encoded anywhere in the loop. Telemetry per step:
L2 drift between consecutive latent inputs, cosine to the first latent,
next-token entropy and top-1 mass, latent norm vs the embedding target norm.
A fixed random-vector control (rescaled to target norm) runs the same loop.
"""

from __future__ import annotations

import copy
import math
import random
import time
from typing import Any

import torch

from kv_handoff import (build_ids_chat, build_ids_induction, make_trial,
                        receiver_generate, score_answer, validate_cache)
from model_utils import inference_mode

TRANSFORMS = ["none", "rescale", "rescale_ridge"]


def tensor_stats(t: torch.Tensor) -> dict:
    tf = t.detach().float()
    return {
        "mean": round(float(tf.mean()), 5),
        "std": round(float(tf.std()), 5),
        "min": round(float(tf.min()), 5),
        "max": round(float(tf.max()), 5),
        "fro_norm": round(float(tf.norm()), 4),
    }


class Realigner:
    """LatentMAS hidden-to-embedding realignment (RESEARCH-NOTES 1.3)."""

    def __init__(self, lm, transform: str):
        self.transform = transform
        emb = lm.model.get_input_embeddings().weight.detach()  # [V, D]
        self.target_norm = float(emb.norm(dim=1).mean())
        self.emb_std = float(emb.std())
        self.ridge = None
        if transform == "rescale_ridge":
            w_out = lm.model.get_output_embeddings().weight.detach()  # [V, D]
            d = emb.shape[1]
            eye = torch.eye(d, dtype=emb.dtype)
            a = w_out.T @ w_out + 1e-5 * eye
            b = w_out.T @ emb
            self.ridge = torch.linalg.solve(a, b)  # [D, D]

    def __call__(self, h: torch.Tensor) -> torch.Tensor:
        v = h.detach().float().reshape(-1)
        if self.transform == "none":
            return v
        if self.ridge is not None:
            v = v @ self.ridge
        norm = float(v.norm())
        if norm > 0:
            v = v * (self.target_norm / norm)
        return v

    def meta(self) -> dict:
        return {
            "transform": self.transform,
            "target_norm": round(self.target_norm, 5),
            "embedding_std": round(self.emb_std, 5),
            "ridge_trace_dev": None if self.ridge is None else round(
                float((self.ridge.diagonal() - 1.0).abs().max()), 6),
        }


def projection_check(lm, ids_a) -> dict:
    """Validate hidden_size == embedding dim and compare numerical ranges."""
    a_ids = torch.tensor([ids_a])
    emb = lm.model.get_input_embeddings()(a_ids)
    with inference_mode():
        out = lm.model(input_ids=a_ids, use_cache=True, output_hidden_states=True)
    h_last = out.hidden_states[-1][:, -1, :]
    return {
        "hidden_size": lm.hidden_size,
        "embedding_dim": int(emb.shape[-1]),
        "dims_match": int(emb.shape[-1]) == lm.hidden_size,
        "needs_projection": int(emb.shape[-1]) != lm.hidden_size,
        "token_embedding_stats": tensor_stats(emb),
        "last_hidden_stats": tensor_stats(h_last),
        "note": ("hidden_states[-1] is post final-norm (LM-head input); "
                 "fed back via inputs_embeds after the configured realignment."),
    }


def entropy_top1(logits: torch.Tensor) -> dict:
    lp = torch.log_softmax(logits.detach().float(), dim=-1)
    p = lp.exp()
    ent = float(-(p * lp).sum())
    return {"entropy": round(ent, 4), "top1_mass": round(float(p.max()), 4)}


def latent_loop(lm, cache, h, realigner, n_steps: int):
    """Run n_steps latent continuation steps, extending the cache in place.

    Returns (final_hidden, per-step telemetry list).
    """
    model = lm.model
    telemetry = []
    first_latent = None
    prev = None
    cur_h = h
    for step in range(n_steps):
        latent_vec = realigner(cur_h)
        if first_latent is None:
            first_latent = latent_vec.clone()
        cache_len = cache.get_seq_length()
        am = torch.ones(1, cache_len + 1, dtype=torch.long)
        with inference_mode():
            t0 = time.perf_counter()
            out = model(
                inputs_embeds=latent_vec.reshape(1, 1, -1),
                attention_mask=am,
                past_key_values=cache,
                use_cache=True,
                output_hidden_states=True,
            )
            dt = time.perf_counter() - t0
        new_h = out.hidden_states[-1][:, -1, :]
        tel = {
            "step": step + 1,
            "step_s": round(dt, 4),
            "latent_norm": round(float(latent_vec.norm()), 4),
            "latent_std": round(float(latent_vec.std()), 5),
            "hidden_std": round(float(new_h.std()), 5),
            "drift_from_prev": None if prev is None else round(
                float((latent_vec - prev).norm()), 4),
            "cosine_to_first": None if first_latent is None or step == 0 else round(
                float(torch.nn.functional.cosine_similarity(
                    latent_vec.reshape(-1), first_latent.reshape(-1), dim=0)), 4),
            "next_token": entropy_top1(out.logits[0, -1]),
        }
        telemetry.append(tel)
        prev = latent_vec
        cur_h = new_h
    return cur_h, telemetry


def run_experiment_b(lm, n_trials: int = 10, seed: int = 1234, max_new_tokens: int = 8,
                     latent_grid=(0, 1, 2, 4, 8), transforms=TRANSFORMS,
                     random_control: bool = True, rss_probe=None,
                     probe: str = "chat") -> dict:
    """Latent-step grid over transforms with the private-fact probe.

    probe="chat" uses the chat-template streams; probe="induction" uses the
    raw induction-format streams (needed where the chat probe is above the
    model's capability, e.g. SmolLM2-135M).
    """
    tok = lm.tokenizer
    realigners = {t: Realigner(lm, t) for t in transforms}
    build = build_ids_chat if probe == "chat" else build_ids_induction

    trials = [make_trial(random.Random(seed + i)) for i in range(n_trials)]
    streams0 = build(lm, trials[0])
    proj = projection_check(lm, streams0["ids_a"])

    records = []
    with inference_mode():
        for i, tr in enumerate(trials):
            streams = build(lm, tr)
            a_ids = torch.tensor([streams["ids_a"]])
            out = lm.model(input_ids=a_ids, use_cache=True, output_hidden_states=True)
            base_cache = out.past_key_values
            base_h = out.hidden_states[-1][:, -1, :]
            if i == 0:
                validate_cache(base_cache, lm.n_layers, len(streams["ids_a"]))
            base_bytes = sum(layer.keys.numel() * layer.keys.element_size() * 2
                             for layer in base_cache.layers)

            configs = [(t, k, False) for t in transforms for k in latent_grid]
            if random_control:
                configs += [("random", k, True) for k in latent_grid if k > 0]

            for transform, k, is_random in configs:
                rec: dict[str, Any] = {
                    "trial": i,
                    "transform": transform,
                    "latent_steps": k,
                    "sender_tokens": len(streams["ids_a"]),
                    "receiver_tokens": len(streams["ids_b_cont"]),
                    "target_word": tr["target_word"],
                    "target_number": tr["target_number"],
                }
                work_cache = copy.deepcopy(base_cache)
                realigner = realigners.get(transform)
                if is_random:
                    gen = torch.Generator().manual_seed(seed * 100003 + i)
                    rand_h = torch.randn(base_h.shape[-1], generator=gen)
                    rand_h = rand_h * (realigners["rescale"].target_norm
                                       / rand_h.norm())
                    h, telemetry = None, None
                    if k > 0:
                        fixed_vec = rand_h
                        telem = []
                        cur = fixed_vec
                        for step in range(k):
                            cache_len = work_cache.get_seq_length()
                            am = torch.ones(1, cache_len + 1, dtype=torch.long)
                            t0 = time.perf_counter()
                            o = model_step(lm, cur.reshape(1, 1, -1), am, work_cache)
                            dt = time.perf_counter() - t0
                            telem.append({
                                "step": step + 1, "step_s": round(dt, 4),
                                "latent_norm": round(float(cur.norm()), 4),
                                "next_token": entropy_top1(o.logits[0, -1])})
                            cur = fixed_vec  # fixed vector, not a recurrence
                        rec["latent_telemetry"] = telem
                elif k > 0:
                    h, telemetry = latent_loop(lm, work_cache, base_h, realigner, k)
                    rec["latent_telemetry"] = telemetry
                rec["cache_bytes_fp32"] = sum(
                    layer.keys.numel() * layer.keys.element_size() * 2
                    for layer in work_cache.layers)
                rec["cache_bytes_before"] = base_bytes

                t0 = time.perf_counter()
                cache_copy = copy.deepcopy(work_cache)
                rec["transfer_s"] = round(time.perf_counter() - t0, 5)

                gen_ids, t_gen = receiver_generate(lm, streams["ids_b_cont"],
                                                   cache_copy, max_new_tokens=max_new_tokens)
                text = tok.decode(gen_ids, skip_special_tokens=True)
                rec["answer"] = text.strip()
                rec["correct"] = score_answer(text, tr["target_number"])
                rec["receiver_gen_s"] = round(t_gen, 4)
                if rss_probe is not None:
                    rec["rss_mb"] = rss_probe()
                records.append(rec)
            del base_cache, base_h, out

    by_config: dict[str, dict] = {}
    for transform in list(transforms) + (["random"] if random_control else []):
        for k in latent_grid:
            if transform == "random" and k == 0:
                continue
            rs = [r for r in records if r["transform"] == transform
                  and r["latent_steps"] == k]
            if not rs:
                continue
            n = len(rs)
            entry = {
                "n_trials": n,
                "correct": sum(r["correct"] for r in rs),
                "mean_receiver_gen_s": round(
                    sum(r["receiver_gen_s"] for r in rs) / n, 4),
                "mean_transfer_s": round(
                    sum(r["transfer_s"] for r in rs) / n, 5),
                "mean_cache_bytes_fp32": sum(
                    r["cache_bytes_fp32"] for r in rs) // n,
            }
            steps_rs = [r for r in rs if r.get("latent_telemetry")]
            if steps_rs:
                all_steps = [s for r in steps_rs for s in r["latent_telemetry"]]
                entry["mean_step_s"] = round(
                    sum(s["step_s"] for s in all_steps) / len(all_steps), 4)
                drifts = [s["drift_from_prev"] for s in all_steps
                          if s.get("drift_from_prev") is not None]
                if drifts:
                    entry["mean_drift"] = round(sum(drifts) / len(drifts), 4)
                coss = [s["cosine_to_first"] for s in all_steps
                        if s.get("cosine_to_first") is not None]
                if coss:
                    entry["mean_cosine_to_first"] = round(sum(coss) / len(coss), 4)
                entry["mean_next_entropy"] = round(
                    sum(s["next_token"]["entropy"] for s in all_steps) / len(all_steps), 4)
                entry["mean_top1_mass"] = round(
                    sum(s["next_token"]["top1_mass"] for s in all_steps) / len(all_steps), 4)
            by_config[f"{transform}_steps{k}"] = entry

    return {
        "probe": probe,
        "projection_check": proj,
        "realigner_meta": {t: r.meta() for t, r in realigners.items()},
        "grid": by_config,
        "trials": records,
    }


def model_step(lm, embed, am, cache):
    return lm.model(inputs_embeds=embed, attention_mask=am, past_key_values=cache,
                    use_cache=True, output_hidden_states=True)
