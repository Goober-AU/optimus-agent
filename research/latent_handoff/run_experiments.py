"""Entry point for the CPU-first latent-communication harness (protocol v2).

Runs, in order, on real local models (no GPU, no accelerate, no device_map):
  Experiment A (kv_handoff)          - KV-cache handoff private-fact probe
                                        (chat template; plus an auxiliary raw
                                        induction probe on SmolLM2)
  Interventions (interventions)      - cache-quality battery (A-K conditions)
  Experiment B (latent_continuation) - bounded latent continuation grid with
                                        LatentMAS realignment (none / rescale /
                                        rescale+ridge) and a random-vector control

Usage:
  .venv/Scripts/python.exe run_experiments.py --model qwen05
  .venv/Scripts/python.exe run_experiments.py --model smollm2
  .venv/Scripts/python.exe run_experiments.py --model qwen05 --only exp_a
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time

os.environ.setdefault("HF_HOME", r"C:\Users\openclawuser\optimus-occ23-models")
os.environ.setdefault("HF_HUB_OFFLINE", "1")

import torch  # noqa: E402

from model_utils import QWEN25_05B, SMOLL2_135M, load_model, rss_mb, versions  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
RESULTS_DIR = os.path.join(HERE, "results")

MODELS = {
    "smollm2": {
        "spec": SMOLL2_135M,
        "tag": "SmolLM2-135M-Instruct",
        "suite": ["exp_a", "exp_a_induction", "interventions", "exp_b",
                  "exp_b_induction"],
        "latent_grid": [0, 1, 2, 4],
    },
    "qwen05": {
        "spec": QWEN25_05B,
        "tag": "Qwen2.5-0.5B-Instruct",
        "suite": ["exp_a", "interventions", "exp_b"],
        "latent_grid": [0, 1, 2, 4, 8],
    },
}


def warmup(lm, n: int = 2) -> float:
    t0 = time.perf_counter()
    with torch.inference_mode():
        ids = lm.tokenizer("warmup", return_tensors="pt").input_ids
        for _ in range(n):
            lm.model(input_ids=ids, use_cache=True)
    return time.perf_counter() - t0


def run_model(model_key: str, trials_a: int, trials_iv: int, trials_b: int,
              seed: int, only: list | None) -> dict:
    spec = MODELS[model_key]["spec"]
    print(f"[{model_key}] loading {spec[0]} @ {spec[1]}", flush=True)
    t0 = time.perf_counter()
    lm = load_model(*spec)
    load_s = time.perf_counter() - t0
    rss0 = rss_mb()
    print(f"[{model_key}] loaded in {load_s:.1f}s, RSS {rss0} MiB", flush=True)
    t_w = warmup(lm)
    print(f"[{model_key}] warmup {t_w:.2f}s", flush=True)

    result = {
        "model": lm.meta,
        "load_s": round(load_s, 2),
        "rss_after_load_mb": rss0,
        "warmup_s": round(t_w, 3),
        "seed": seed,
        "trials_a": trials_a,
        "trials_iv": trials_iv,
        "trials_b": trials_b,
    }
    peak = {"rss": rss0}

    def rss_probe():
        cur = rss_mb()
        peak["rss"] = max(peak["rss"], cur)
        return cur

    suite = [s for s in MODELS[model_key]["suite"] if not only or s in only]

    if "exp_a" in suite:
        from kv_handoff import run_experiment_a
        print(f"[{model_key}] Exp A (chat): {trials_a} trials", flush=True)
        t0 = time.perf_counter()
        result["exp_a"] = run_experiment_a(lm, n_trials=trials_a, seed=seed,
                                           probe="chat", rss_probe=rss_probe)
        s = result["exp_a"]["summary"]
        print(f"[{model_key}] Exp A done {time.perf_counter()-t0:.1f}s: "
              f"with={s['with_cache_correct']}/{trials_a} "
              f"full={s['full_text_correct']}/{trials_a} "
              f"no={s['without_cache_correct']}/{trials_a} "
              f"gate={s['gate_pass']}", flush=True)
    if "exp_a_induction" in suite:
        from kv_handoff import run_experiment_a
        print(f"[{model_key}] Exp A (induction aux): {trials_a} trials", flush=True)
        t0 = time.perf_counter()
        result["exp_a_induction"] = run_experiment_a(lm, n_trials=trials_a, seed=seed,
                                                    probe="induction",
                                                    rss_probe=rss_probe)
        s = result["exp_a_induction"]["summary"]
        print(f"[{model_key}] Exp A induction done {time.perf_counter()-t0:.1f}s: "
              f"with={s['with_cache_correct']}/{trials_a} "
              f"full={s['full_text_correct']}/{trials_a} "
              f"no={s['without_cache_correct']}/{trials_a} "
              f"gate={s['gate_pass']}", flush=True)
    if "interventions" in suite:
        from interventions import run_interventions
        print(f"[{model_key}] interventions: {trials_iv} trials", flush=True)
        t0 = time.perf_counter()
        result["interventions"] = run_interventions(lm, n_trials=trials_iv, seed=seed,
                                                    rss_probe=rss_probe)
        s = result["interventions"]["summary"]
        print(f"[{model_key}] interventions done {time.perf_counter()-t0:.1f}s "
              f"(A-vs-A KL max {s['a_vs_a_kl_max']})", flush=True)
        for k, v in s.items():
            if isinstance(v, dict):
                print(f"    {k:20s} correct={v['correct']}/{trials_iv} "
                      f"KL={v['mean_kl_vs_A']:.4f} L1={v['mean_l1_vs_A']:.4f}", flush=True)
    if "exp_b" in suite or "exp_b_induction" in suite:
        from latent_continuation import run_experiment_b
        grid = MODELS[model_key]["latent_grid"]
        for section, probe in (("exp_b", "chat"), ("exp_b_induction", "induction")):
            if section not in suite:
                continue
            print(f"[{model_key}] Exp B ({probe}): latent grid {grid} x 3 transforms "
                  f"+ random control, {trials_b} trials", flush=True)
            t0 = time.perf_counter()
            result[section] = run_experiment_b(lm, n_trials=trials_b, seed=seed,
                                               latent_grid=tuple(grid),
                                               rss_probe=rss_probe, probe=probe)
            print(f"[{model_key}] Exp B ({probe}) done {time.perf_counter()-t0:.1f}s",
                  flush=True)
            for k, v in result[section]["grid"].items():
                print(f"    {k:24s} correct={v['correct']}/{v['n_trials']}", flush=True)

    result["rss_peak_mb"] = peak["rss"]
    result["rss_end_mb"] = rss_mb()
    return result


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", choices=list(MODELS), default="qwen05")
    ap.add_argument("--trials-a", type=int, default=50)
    ap.add_argument("--trials-iv", type=int, default=20)
    ap.add_argument("--trials-b", type=int, default=10)
    ap.add_argument("--seed", type=int, default=1234)
    ap.add_argument("--only", nargs="*", default=None,
                    choices=["exp_a", "exp_a_induction", "interventions", "exp_b",
                             "exp_b_induction"])
    args = ap.parse_args()

    env = versions()
    out = {"env": env, "rss_before_load_mb": rss_mb(),
           "started": time.strftime("%Y-%m-%dT%H:%M:%S")}
    print(json.dumps({"env": env}, indent=2), flush=True)

    torch.manual_seed(args.seed)
    res = run_model(args.model, args.trials_a, args.trials_iv, args.trials_b,
                    args.seed, args.only)
    res["env"] = env
    res["finished"] = time.strftime("%Y-%m-%dT%H:%M:%S")

    os.makedirs(RESULTS_DIR, exist_ok=True)
    json_path = os.path.join(RESULTS_DIR, f"{args.model}-results.json")

    # Preserve sections not re-run this invocation
    if os.path.exists(json_path) and args.only:
        try:
            with open(json_path, "r", encoding="utf-8") as f:
                old = json.load(f)
            for k, v in old.items():
                if k not in res:
                    res[k] = v
        except Exception:
            pass

    with open(json_path, "w", encoding="utf-8", newline="\n") as f:
        json.dump(res, f, indent=2)
    print(f"[done] results -> {json_path}", flush=True)

    from results_writer import write_all_markdown
    write_all_markdown(RESULTS_DIR)
    print("[done] markdown updated", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
