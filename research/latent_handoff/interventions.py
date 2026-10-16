"""Intervention battery for the KV-cache handoff (protocol v2, chat probe).

All conditions share the same per-trial receiver question (chat template).
Conditions:
  A correct_cache    transferred cache from Agent A (the mechanism)
  B no_cache         receiver question alone, fresh context
  C zeroed_cache     A's cache with all k/v tensors zeroed (same shapes)
  D random_cache     A's cache refilled with per-tensor mean/std-matched Gaussian
  E unrelated_cache  cache built from an unrelated task (recipe), same turn shape
  F full_text        the identical full token stream, no cache (positive control)
  G compact_text     one user turn: inline notes payload + question, no cache
  H shuffled_cache   same permutation of cached positions on k and v (all layers);
                      provably null for a fully attended prefix (order-
                      invariance control)
  H2_unpaired_shuffle independent permutations for k and v (breaks pairing)
  I truncated_cache  system + sender user turn only (assistant ack dropped)
  J covgauss_cache   per (layer, head, k/v) Gaussian resample, mean/cov matched
  K haar_cache       per (layer, k/v) Haar random rotation of the head dim

Isolation (bugfix): every logits probe runs on a fresh deepcopy with
use_cache=False, so probes never extend or mutate a live cache. The battery
includes an A-vs-A double-probe sanity check; its KL must be exactly 0.
"""

from __future__ import annotations

import copy
import random
import time
from typing import Any

import torch

from kv_handoff import (ACK, UNRELATED_USER, build_ids_chat, chat_ids_probe,
                        make_trial, score_answer)
from model_utils import inference_mode

CONDITIONS = ["A_correct_cache", "B_no_cache", "C_zeroed_cache", "D_random_cache",
              "E_unrelated_cache", "F_full_text", "G_compact_text", "H_shuffled_cache",
              "H2_unpaired_shuffle", "I_truncated_cache", "J_covgauss_cache",
              "K_haar_cache"]


def zero_cache(cache):
    c = copy.deepcopy(cache)
    for layer in c.layers:
        layer.keys.zero_()
        layer.values.zero_()
    return c


def randomize_cache(cache, gen):
    """Per-tensor Gaussian noise matched to each tensor's mean/std."""
    c = copy.deepcopy(cache)
    for layer in c.layers:
        for t in (layer.keys, layer.values):
            mu, std = t.mean(), t.std()
            t.copy_(torch.randn(t.shape, generator=gen) * std + mu)
    return c


def shuffle_cache(cache, gen):
    """Same position permutation on every layer, k and v together.

    Empirically (and provably for a fully attended prefix) this is a NULL
    intervention: attention output is invariant to permuting (k_i, v_i) pairs
    because rope is baked into the tensors and each softmax weight stays
    attached to its pair. Kept as the order-invariance control.
    """
    c = copy.deepcopy(cache)
    seq = c.get_seq_length()
    perm = torch.randperm(seq, generator=gen)
    for layer in c.layers:
        layer.keys = layer.keys[:, :, perm, :].contiguous()
        layer.values = layer.values[:, :, perm, :].contiguous()
    return c


def unpaired_shuffle_cache(cache, gen):
    """Independent permutations for k and v on every layer.

    Breaks the key-value pairing while keeping the multiset of cached vectors;
    attention scores then point at the wrong values.
    """
    c = copy.deepcopy(cache)
    seq = c.get_seq_length()
    for layer in c.layers:
        perm_k = torch.randperm(seq, generator=gen)
        perm_v = torch.randperm(seq, generator=gen)
        layer.keys = layer.keys[:, :, perm_k, :].contiguous()
        layer.values = layer.values[:, :, perm_v, :].contiguous()
    return c


def truncate_cache(cache, keep_first: int):
    """Keep the first keep_first positions (system + sender user turn)."""
    c = copy.deepcopy(cache)
    total = c.get_seq_length()
    assert 0 < keep_first <= total, f"keep_first {keep_first} out of range {total}"
    c.crop(-(total - keep_first))
    assert c.get_seq_length() == keep_first
    return c


def covgauss_cache(cache, gen):
    """Interlat CovGauss-mu: per (layer, head, k/v) resample with matched
    mean and covariance over the cached positions."""
    c = copy.deepcopy(cache)
    for layer in c.layers:
        for t in (layer.keys, layer.values):
            heads = t.shape[1]
            for h in range(heads):
                block = t[0, h].float()  # [seq, head_dim]
                mu = block.mean(dim=0)
                centered = block - mu
                cov = (centered.T @ centered) / max(block.shape[0] - 1, 1)
                cov = cov + 1e-6 * torch.eye(cov.shape[0])
                try:
                    L = torch.linalg.cholesky(cov)
                except Exception:
                    continue  # leave the real tensor on degenerate covariance
                eps = torch.randn(block.shape, generator=gen)
                t[0, h] = mu + eps @ L.T
    return c


def haar_cache(cache, gen):
    """Per (layer, k/v) Haar-random orthogonal rotation of the head dim.

    Preserves norms and second moments; destroys value geometry.
    """
    c = copy.deepcopy(cache)
    for layer in c.layers:
        for t in (layer.keys, layer.values):
            d = t.shape[-1]
            gauss = torch.randn((d, d), generator=gen)
            q, _r = torch.linalg.qr(gauss)
            t.copy_(t @ q)
    return c


def probe_logits(lm, ids_b, cache):
    """Isolated next-token logits at the receiver's first decoding position.

    The cache is deep-copied and use_cache=False, so the probe cannot extend
    or mutate any live cache.
    """
    model = lm.model
    b_ids = torch.tensor([ids_b])
    c = copy.deepcopy(cache)
    am = torch.ones(1, c.get_seq_length() + b_ids.shape[1], dtype=torch.long)
    with inference_mode():
        out = model(input_ids=b_ids, attention_mask=am, past_key_values=c,
                    use_cache=False)
    return out.logits[0, -1].clone()


def probe_logits_text(lm, ids_full):
    """Same probe for a full-text condition (no cache)."""
    model = lm.model
    f_ids = torch.tensor([ids_full])
    with inference_mode():
        out = model(input_ids=f_ids, use_cache=False)
    return out.logits[0, -1].clone()


def kl_l1(logits_a: torch.Tensor, logits_x: torch.Tensor) -> dict:
    """KL(P_A || P_X) and L1 on the full-vocab next-token distributions."""
    la = torch.log_softmax(logits_a.double(), dim=-1)
    lx = torch.log_softmax(logits_x.double(), dim=-1)
    p_a = la.exp()
    kl = float((p_a * (la - lx)).sum())
    l1 = float((p_a - lx.exp()).abs().sum())
    return {"kl": round(kl, 6), "l1": round(l1, 6)}


def gen_with(lm, ids, cache=None, max_new_tokens=8):
    """Greedy generation either on a cache copy or on full text."""
    model = lm.model
    ids_t = torch.tensor([ids])
    with inference_mode():
        t0 = time.perf_counter()
        kwargs = dict(input_ids=ids_t, max_new_tokens=max_new_tokens, do_sample=False,
                      num_beams=1, min_new_tokens=1,
                      pad_token_id=model.config.eos_token_id)
        if cache is not None:
            kwargs["attention_mask"] = torch.ones(
                1, cache.get_seq_length() + ids_t.shape[1], dtype=torch.long)
            kwargs["past_key_values"] = copy.deepcopy(cache)
        out = model.generate(**kwargs)
        dt = time.perf_counter() - t0
    return out[0, ids_t.shape[1]:].tolist(), dt


def run_interventions(lm, n_trials: int = 10, seed: int = 1234, max_new_tokens: int = 8,
                      rss_probe=None) -> dict:
    tok = lm.tokenizer
    model = lm.model
    gen = torch.Generator().manual_seed(seed)
    records = []

    # Unrelated-task cache template (same turn shape as A: user + assistant ack)
    with inference_mode():
        unrel_ids = chat_ids_probe(tok, [{"role": "user", "content": UNRELATED_USER},
                                         {"role": "assistant", "content": ACK}],
                                   add_generation_prompt=False)

    with inference_mode():
        for i in range(n_trials):
            tr = make_trial(random.Random(seed + i))
            streams = build_ids_chat(lm, tr)
            ids_a, ids_b, ids_full = (streams["ids_a"], streams["ids_b"],
                                      streams["ids_full"])
            ids_b_cont = streams["ids_b_cont"]  # question turn for cache conditions
            a_ids = torch.tensor([ids_a])
            base = model(input_ids=a_ids, use_cache=True).past_key_values

            variants = {
                "A_correct_cache": base,
                "C_zeroed_cache": zero_cache(base),
                "D_random_cache": randomize_cache(base, gen),
                "E_unrelated_cache": model(
                    input_ids=torch.tensor([unrel_ids]), use_cache=True).past_key_values,
                "H_shuffled_cache": shuffle_cache(base, gen),
                "H2_unpaired_shuffle": unpaired_shuffle_cache(base, gen),
                "I_truncated_cache": truncate_cache(base, streams["sender_user_ids_len"]),
                "J_covgauss_cache": covgauss_cache(base, gen),
                "K_haar_cache": haar_cache(base, gen),
            }
            compact_text = ("Notes: " + tr["tab"].replace("\n", ", ") +
                            ". " + streams["question"])
            compact_ids = chat_ids_probe(
                tok, [{"role": "user", "content": compact_text}], add_generation_prompt=True)

            # Reference logits: correct cache, isolated probe
            ref_logits = probe_logits(lm, ids_b_cont, variants["A_correct_cache"])

            # A-vs-A isolation sanity: second probe on a fresh deepcopy must be identical
            ref_logits2 = probe_logits(lm, ids_b_cont, variants["A_correct_cache"])
            sanity = kl_l1(ref_logits, ref_logits2)

            rec: dict[str, Any] = {
                "trial": i,
                "target_word": tr["target_word"],
                "target_number": tr["target_number"],
                "sender_tokens": len(ids_a),
                "full_text_tokens": len(ids_full),
                "compact_text_tokens": len(compact_ids),
                "cache_bytes_fp32": sum(
                    layer.keys.numel() * layer.keys.element_size() * 2
                    for layer in base.layers),
                "a_vs_a_kl": sanity["kl"],
            }

            for cond in CONDITIONS:
                crec: dict[str, Any] = {}
                if cond in variants:
                    lg = probe_logits(lm, ids_b_cont, variants[cond])
                    ids, t_gen = gen_with(lm, ids_b_cont, cache=variants[cond],
                                          max_new_tokens=max_new_tokens)
                elif cond == "B_no_cache":
                    lg = probe_logits_text(lm, ids_b)
                    ids, t_gen = gen_with(lm, ids_b, max_new_tokens=max_new_tokens)
                elif cond == "F_full_text":
                    lg = probe_logits_text(lm, ids_full)
                    ids, t_gen = gen_with(lm, ids_full, max_new_tokens=max_new_tokens)
                elif cond == "G_compact_text":
                    lg = probe_logits_text(lm, compact_ids)
                    ids, t_gen = gen_with(lm, compact_ids, max_new_tokens=max_new_tokens)
                else:
                    raise AssertionError(cond)
                text = tok.decode(ids, skip_special_tokens=True)
                crec["divergence_vs_A"] = kl_l1(ref_logits, lg)
                crec["answer"] = text.strip()
                crec["correct"] = score_answer(text, tr["target_number"])
                crec["gen_s"] = round(t_gen, 4)
                crec["argmax_token"] = tok.decode([int(lg.argmax())])
                rec[cond] = crec

            if rss_probe is not None:
                rec["rss_mb"] = rss_probe()
            records.append(rec)
            del base

    summary: dict[str, Any] = {"a_vs_a_kl_max": max(r["a_vs_a_kl"] for r in records)}
    for cond in CONDITIONS:
        rs = [r[cond] for r in records]
        n = len(rs)
        summary[cond] = {
            "correct": sum(r["correct"] for r in rs),
            "mean_kl_vs_A": round(sum(r["divergence_vs_A"]["kl"] for r in rs) / n, 6),
            "mean_l1_vs_A": round(sum(r["divergence_vs_A"]["l1"] for r in rs) / n, 6),
            "mean_gen_s": round(sum(r["gen_s"] for r in rs) / n, 4),
        }
    return {"summary": summary, "trials": records}
