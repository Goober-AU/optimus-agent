"""EXPERIMENT A (protocol v2): KV-cache handoff between logical agents.

Chat-template probe (primary). Logical Agent A processes a completed chat turn
pair (user notes + fixed assistant acknowledgment), so its DynamicCache ends at
a turn boundary. Logical Agent B receives a deepcopy of that cache (the
transfer artifact) and continues generation from its own user turn, attending
to A's context only through the cache.

The receiver question never contains the secret. The full-text condition
(positive control) feeds the identical token stream as one sequence with no
cache; it must pass before any handoff claim is meaningful.

Auxiliary induction format (raw text, no chat template) is kept because the
135M model cannot do sentence-style QA even with full text, but completes
induction-style prompts reliably; it demonstrates the mechanism at the model's
actual capability.

Fixes applied after the first broken run (see RESEARCH-NOTES.md section 4.0):
- prompts rendered with the model chat template, sender turn completed
- probe forwards isolated (use_cache=False on fresh deepcopies)
- no probe reuses a live cache
"""

from __future__ import annotations

import copy
import random
import re
import time
from typing import Any

import numpy as np
import torch

from model_utils import chat_ids, inference_mode

WORD_POOL = [
    "lantern", "violin", "compass", "meadow", "harbor", "cactus", "tundra",
    "pelican", "granite", "willow", "cobalt", "falcon", "glacier", "juniper",
    "magnolia", "obsidian", "basil", "cedar", "dahlia", "ember",
    "flint", "garnet", "hazel", "indigo", "jasper", "kelp", "lagoon",
    "marble", "nectar", "opal", "parsley", "quartz", "raven", "saffron",
    "thistle", "umber", "violet", "walnut", "yarrow", "zephyr",
    "acorn", "bramble", "clover", "dune", "estuary", "fern", "gorse",
]

ACK = "Notes stored."

UNRELATED_USER = (
    "Recipe: warm the oven to 180 degrees. Mix flour, sugar, and butter in a "
    "bowl. Fold in two eggs and a cup of milk. Pour the batter into a greased "
    "pan and bake for thirty minutes until golden."
)


def make_trial(rng: random.Random) -> dict:
    """Build one random private-fact trial (5 word=number pairs)."""
    words = rng.sample(WORD_POOL, 5)
    numbers = rng.sample(range(10, 100), 5)
    pairs = list(zip(words, numbers))
    target_idx = rng.randrange(5)
    return {
        "words": words,
        "numbers": numbers,
        "pairs": pairs,
        "target_word": words[target_idx],
        "target_number": numbers[target_idx],
        "tab": "\n".join(f"{w}: {n}" for w, n in pairs),
        "pairs_text": ", ".join(f"{w}={n}" for w, n in pairs),
    }


def chat_ids_probe(tok, messages, add_generation_prompt: bool = False) -> list:
    """Chat-template render usable without a LoadedModel (tokenizer only)."""
    return chat_ids(tok, messages, add_generation_prompt=add_generation_prompt)


def build_ids_chat(lm, tr: dict) -> dict:
    """Token streams for the chat protocol.

    ids_A: system + sender user turn + sender assistant ack (turn boundary).
    ids_full: ids_A + receiver user turn + assistant header (generation prompt).
    ids_b_alone: receiver question with its own system prompt, no cache.
    """
    tok = lm.tokenizer
    u1 = ("Agent A private notes:\n" + tr["tab"] + "\nEach line is word: number.")
    q = (f"What number is paired with '{tr['target_word']}'? "
         "Reply with the number only.")
    conv_a = [{"role": "user", "content": u1}, {"role": "assistant", "content": ACK}]
    conv_full = conv_a + [{"role": "user", "content": q}]
    ids_a = chat_ids(tok, conv_a, add_generation_prompt=False)
    ids_full = chat_ids(tok, conv_full, add_generation_prompt=True)
    assert ids_full[:len(ids_a)] == ids_a, "chat template prefix mismatch"
    ids_b = chat_ids(tok, [{"role": "user", "content": q}], add_generation_prompt=True)
    ids_b_cont = ids_full[len(ids_a):]  # question turn as cache continuation
    return {
        "ids_a": ids_a,
        "ids_full": ids_full,
        "ids_b": ids_b,
        "ids_b_cont": ids_b_cont,
        "sender_user_ids_len": len(chat_ids(tok, [{"role": "user", "content": u1}],
                                           add_generation_prompt=False)),
        "question": q,
    }


def build_ids_induction(lm, tr: dict) -> dict:
    """Token streams for the raw induction format (auxiliary).

    The receiver prompt is a bare pair-completion prefix; the sender text
    states the pairs twice (induction-friendly), as validated on SmolLM2-135M.
    """
    tok = lm.tokenizer
    s_text = f"Notes: {tr['pairs_text']}. Repeat: {tr['pairs_text']}.\n"
    r_text = f"Complete the pair: {tr['target_word']}="
    ids_a = tok(s_text, return_tensors="pt").input_ids[0].tolist()
    ids_b = tok(r_text, add_special_tokens=False, return_tensors="pt").input_ids[0].tolist()
    ids_full = ids_a + ids_b
    return {"ids_a": ids_a, "ids_full": ids_full, "ids_b": ids_b,
            "ids_b_cont": ids_b, "sender_user_ids_len": len(ids_a),
            "question": r_text}


def validate_cache(cache, n_layers: int, expected_seq: int) -> dict:
    """Validate DynamicCache structure; raise on any mismatch."""
    assert cache is not None, "past_key_values is None"
    layers = cache.layers
    assert len(layers) == n_layers, f"layer count {len(layers)} != {n_layers}"
    seq_lens = set()
    per_layer = []
    for idx, layer in enumerate(layers):
        k, v = layer.keys, layer.values
        assert k is not None and v is not None, f"layer {idx} has empty k/v"
        assert k.shape[0] == 1 and v.shape[0] == 1, f"layer {idx} batch != 1"
        assert k.shape[2] == v.shape[2] == expected_seq, (
            f"layer {idx} seq {k.shape[2]}/{v.shape[2]} != {expected_seq}")
        assert k.dtype == v.dtype == torch.float32, f"layer {idx} dtype {k.dtype}"
        seq_lens.add(int(k.shape[2]))
        per_layer.append({"layer": idx, "k_shape": list(k.shape), "v_shape": list(v.shape)})
    assert len(seq_lens) == 1, f"ragged cache seq lengths: {seq_lens}"
    assert cache.get_seq_length() == expected_seq, "get_seq_length disagrees with shapes"
    return {
        "n_layers": len(layers),
        "seq_len": expected_seq,
        "batch": 1,
        "kv_heads": int(layers[0].keys.shape[1]),
        "head_dim": int(layers[0].keys.shape[3]),
        "per_layer_first3": per_layer[:3],
    }


def cache_bytes(cache) -> int:
    """Total bytes of all k/v tensors in the cache (fp32 storage)."""
    total = 0
    for layer in cache.layers:
        for t in (layer.keys, layer.values):
            total += t.numel() * t.element_size()
    return total


def sender_forward(lm, ids_a):
    """Agent A's forward pass over the completed turn; captures the cache."""
    model = lm.model
    a_ids = torch.tensor([ids_a])
    with inference_mode():
        t0 = time.perf_counter()
        out = model(input_ids=a_ids, use_cache=True)
        dt = time.perf_counter() - t0
    return out.past_key_values, dt


def receiver_generate(lm, ids_b, cache, max_new_tokens: int = 8):
    """Agent B continues generation on top of a transferred cache copy."""
    model = lm.model
    b_ids = torch.tensor([ids_b])
    cache_len = cache.get_seq_length()
    am = torch.ones(1, cache_len + b_ids.shape[1], dtype=torch.long)
    with inference_mode():
        t0 = time.perf_counter()
        out = model.generate(
            input_ids=b_ids,
            attention_mask=am,
            past_key_values=cache,
            max_new_tokens=max_new_tokens,
            do_sample=False,
            num_beams=1,
            min_new_tokens=1,
            pad_token_id=model.config.eos_token_id,
        )
        dt = time.perf_counter() - t0
    return out[0, b_ids.shape[1]:].tolist(), dt


def receiver_generate_text(lm, ids_full, max_new_tokens: int = 8):
    """Full-text control: one sequence, no cache."""
    model = lm.model
    f_ids = torch.tensor([ids_full])
    with inference_mode():
        t0 = time.perf_counter()
        out = model.generate(
            input_ids=f_ids,
            max_new_tokens=max_new_tokens,
            do_sample=False,
            num_beams=1,
            min_new_tokens=1,
            pad_token_id=model.config.eos_token_id,
        )
        dt = time.perf_counter() - t0
    return out[0, f_ids.shape[1]:].tolist(), dt


def receiver_generate_alone(lm, ids_b, max_new_tokens: int = 8):
    """No-cache control: receiver question only, fresh context."""
    model = lm.model
    b_ids = torch.tensor([ids_b])
    with inference_mode():
        t0 = time.perf_counter()
        out = model.generate(
            input_ids=b_ids,
            max_new_tokens=max_new_tokens,
            do_sample=False,
            num_beams=1,
            min_new_tokens=1,
            pad_token_id=model.config.eos_token_id,
        )
        dt = time.perf_counter() - t0
    return out[0, b_ids.shape[1]:].tolist(), dt


def score_answer(text: str, target_number: int) -> bool:
    """Correct iff the target number appears as a standalone integer."""
    ints = re.findall(r"(?<!\d)-?\d+(?!\d)", text)
    return str(target_number) in [i.lstrip("-") for i in ints]


def paired_bootstrap(with_ok, without_ok, n_boot: int = 1000, seed: int = 0):
    """Paired bootstrap CI for the accuracy difference (with - without)."""
    rng = np.random.default_rng(seed)
    with_ok = np.asarray(with_ok, dtype=float)
    without_ok = np.asarray(without_ok, dtype=float)
    n = len(with_ok)
    diffs = with_ok - without_ok
    if n == 0:
        return {"diff": None, "ci_low": None, "ci_high": None, "p_gt_0": None}
    idx = rng.integers(0, n, size=(n_boot, n))
    boot = diffs[idx].mean(axis=1)
    return {
        "diff": round(float(diffs.mean()), 4),
        "ci_low": round(float(np.percentile(boot, 2.5)), 4),
        "ci_high": round(float(np.percentile(boot, 97.5)), 4),
        "p_gt_0": round(float((boot > 0).mean()), 4),
    }


def run_experiment_a(lm, n_trials: int = 10, seed: int = 1234, max_new_tokens: int = 8,
                     probe: str = "chat", rss_probe=None) -> dict:
    """Run the private-fact probe across seeded trials.

    Conditions per trial: correct cache, no cache, full text (positive control).
    The handoff gate: full-text accuracy >= 80%, with-cache within 5 points of
    full text, with-cache > no-cache, and per-trial proof (with correct AND
    without wrong).
    """
    tok = lm.tokenizer
    build = build_ids_chat if probe == "chat" else build_ids_induction
    records = []
    with inference_mode():
        for i in range(n_trials):
            tr = make_trial(random.Random(seed + i))
            streams = build(lm, tr)
            rec: dict[str, Any] = {
                "trial": i,
                "target_word": tr["target_word"],
                "target_number": tr["target_number"],
                "sender_tokens": len(streams["ids_a"]),
                "receiver_tokens": len(streams["ids_b"]) if probe != "chat"
                else len(streams["ids_b_cont"]),
                "full_tokens": len(streams["ids_full"]),
            }

            cache, t_sender = sender_forward(lm, streams["ids_a"])
            rec["sender_forward_s"] = round(t_sender, 4)
            if i == 0:
                rec["cache_validation"] = validate_cache(
                    cache, lm.n_layers, len(streams["ids_a"]))
            else:
                validate_cache(cache, lm.n_layers, len(streams["ids_a"]))
            rec["cache_bytes_fp32"] = cache_bytes(cache)
            rec["cache_bytes_fp16"] = rec["cache_bytes_fp32"] // 2
            rec["text_artifact_bytes"] = len(" ".join(
                f"{w}={n}" for w, n in tr["pairs"]).encode("utf-8"))

            t0 = time.perf_counter()
            cache_copy = copy.deepcopy(cache)
            rec["transfer_s"] = round(time.perf_counter() - t0, 5)

            ids_b_rx = (streams["ids_b_cont"] if probe == "chat"
                        else streams["ids_b"])
            gen_ids, t_gen = receiver_generate(lm, ids_b_rx, cache_copy,
                                               max_new_tokens=max_new_tokens)
            rec["with_cache_answer"] = tok.decode(gen_ids, skip_special_tokens=True).strip()
            rec["with_cache_correct"] = score_answer(rec["with_cache_answer"],
                                                     tr["target_number"])
            rec["receiver_gen_s"] = round(t_gen, 4)

            assert cache.get_seq_length() == len(streams["ids_a"]), "A's cache mutated"
            rec["ownership_ok"] = True

            gen_ids2, t_fresh = receiver_generate_alone(lm, streams["ids_b"],
                                                        max_new_tokens=max_new_tokens)
            rec["without_cache_answer"] = tok.decode(gen_ids2, skip_special_tokens=True).strip()
            rec["without_cache_correct"] = score_answer(rec["without_cache_answer"],
                                                        tr["target_number"])
            rec["fresh_gen_s"] = round(t_fresh, 4)

            gen_ids3, t_full = receiver_generate_text(lm, streams["ids_full"],
                                                       max_new_tokens=max_new_tokens)
            rec["full_text_answer"] = tok.decode(gen_ids3, skip_special_tokens=True).strip()
            rec["full_text_correct"] = score_answer(rec["full_text_answer"],
                                                    tr["target_number"])
            rec["full_text_gen_s"] = round(t_full, 4)

            rec["proven_trial"] = bool(rec["with_cache_correct"]
                                       and not rec["without_cache_correct"])
            if rss_probe is not None:
                rec["rss_mb"] = rss_probe()
            records.append(rec)

    n = len(records)
    with_c = sum(r["with_cache_correct"] for r in records)
    without_c = sum(r["without_cache_correct"] for r in records)
    full_c = sum(r["full_text_correct"] for r in records)
    boot = paired_bootstrap([r["with_cache_correct"] for r in records],
                            [r["without_cache_correct"] for r in records])
    summary = {
        "probe": probe,
        "n_trials": n,
        "with_cache_correct": with_c,
        "without_cache_correct": without_c,
        "full_text_correct": full_c,
        "proven_trials": sum(r["proven_trial"] for r in records),
        "positive_control_pass": full_c >= 0.8 * n,
        "with_within_5pts_of_full": with_c >= full_c - 0.05 * n,
        "with_beats_without": with_c > without_c,
        "bootstrap_with_vs_without": boot,
        "gate_pass": bool(full_c >= 0.8 * n and with_c >= full_c - 0.05 * n
                          and with_c > without_c),
        "mean_sender_forward_s": round(sum(r["sender_forward_s"] for r in records) / n, 4),
        "mean_transfer_s": round(sum(r["transfer_s"] for r in records) / n, 5),
        "mean_receiver_gen_s": round(sum(r["receiver_gen_s"] for r in records) / n, 4),
        "mean_fresh_gen_s": round(sum(r["fresh_gen_s"] for r in records) / n, 4),
        "mean_full_text_gen_s": round(sum(r["full_text_gen_s"] for r in records) / n, 4),
        "mean_cache_bytes_fp32": sum(r["cache_bytes_fp32"] for r in records) // n,
        "mean_cache_bytes_fp16": sum(r["cache_bytes_fp16"] for r in records) // n,
        "mean_sender_tokens": sum(r["sender_tokens"] for r in records) // n,
    }
    return {"summary": summary, "trials": records}
