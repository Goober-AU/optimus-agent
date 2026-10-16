"""Model loading and deterministic generation helpers.

CPU-only latent handoff harness. Loads each model once per process, pins
tokenizer and weights to the same revision, and provides greedy
(deterministic) generation helpers.

This module is deliberately free of experiment logic.
"""

from __future__ import annotations

import platform
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any

import torch

SMOLL2_135M = ("HuggingFaceTB/SmolLM2-135M-Instruct", "12fd25f77366fa6b3b4b768ec3050bf629380bac")
QWEN25_05B = ("Qwen/Qwen2.5-0.5B-Instruct", "7ae557604adf67be50417f59c2c2f167def9a775")


@dataclass
class LoadedModel:
    model_id: str
    revision: str
    tokenizer: Any
    model: Any
    meta: dict = field(default_factory=dict)

    @property
    def device(self) -> str:
        return "cpu"

    @property
    def n_layers(self) -> int:
        return self.model.config.num_hidden_layers

    @property
    def hidden_size(self) -> int:
        return self.model.config.hidden_size

    @property
    def vocab_size(self) -> int:
        return self.model.config.vocab_size

    def input_embeddings(self) -> torch.Tensor:
        return self.model.get_input_embeddings()

    def embed_tokens(self, input_ids: torch.Tensor) -> torch.Tensor:
        return self.input_embeddings()(input_ids)


def load_model(model_id: str, revision: str) -> LoadedModel:
    """Load tokenizer and causal LM on CPU, eval mode, same revision for both."""
    from transformers import AutoModelForCausalLM, AutoTokenizer

    tok = AutoTokenizer.from_pretrained(model_id, revision=revision)
    model = AutoModelForCausalLM.from_pretrained(
        model_id,
        revision=revision,
        dtype=torch.float32,
        attn_implementation="eager",
    )
    model.to("cpu")
    model.eval()
    meta = {
        "model_id": model_id,
        "revision": revision,
        "dtype": str(next(model.parameters()).dtype),
        "device": str(next(model.parameters()).device),
        "n_layers": model.config.num_hidden_layers,
        "hidden_size": model.config.hidden_size,
        "vocab_size": model.config.vocab_size,
        "n_params": sum(p.numel() for p in model.parameters()),
        "attn_implementation": getattr(model.config, "_attn_implementation", "unknown"),
        "torch_num_threads": torch.get_num_threads(),
    }
    return LoadedModel(model_id=model_id, revision=revision, tokenizer=tok, model=model, meta=meta)


@contextmanager
def inference_mode():
    """Wrap a block in torch.inference_mode()."""
    with torch.inference_mode():
        yield


def greedy_generate(model, input_ids, attention_mask=None, past_key_values=None, max_new_tokens=12):
    """Deterministic greedy generation. Returns generated token ids only."""
    gen_kwargs = dict(
        input_ids=input_ids,
        max_new_tokens=max_new_tokens,
        do_sample=False,
        num_beams=1,
        pad_token_id=getattr(model.config, "eos_token_id", None) or 0,
    )
    if attention_mask is not None:
        gen_kwargs["attention_mask"] = attention_mask
    if past_key_values is not None:
        gen_kwargs["past_key_values"] = past_key_values
    out = model.generate(**gen_kwargs)
    # With past_key_values, generate returns only the newly generated ids
    # plus the input; slice off the input length.
    if past_key_values is None:
        return out[0, input_ids.shape[1]:]
    return out[0, :]


def chat_ids(tok, messages, add_generation_prompt: bool = False) -> list:
    """Render messages with the model's chat template; return plain token ids.

    transformers 5.x returns a BatchEncoding by default; return_dict=False
    gives the plain id list we need for prefix splitting.
    """
    return tok.apply_chat_template(
        messages, add_generation_prompt=add_generation_prompt, return_dict=False)


def versions() -> dict:
    """Collect exact package versions and host CPU facts for the scorecard."""
    import psutil

    import transformers

    cpu_name = platform.processor() or platform.machine()
    try:
        w = __import__("wmi", fromlist=["WMI"])
        cpu_name = w.WMI().Win32_Processor()[0].Name.strip()
    except Exception:
        try:
            for line in _wmic_cpu():
                if line:
                    cpu_name = line.strip()
                    break
        except Exception:
            pass
    freq = psutil.cpu_freq()
    return {
        "python": platform.python_version(),
        "torch": torch.__version__,
        "transformers": transformers.__version__,
        "tokenizers": __import__("tokenizers").__version__,
        "safetensors": __import__("safetensors").__version__,
        "numpy": __import__("numpy").__version__,
        "psutil": psutil.__version__,
        "os": f"{platform.system()} {platform.release()}",
        "cpu": cpu_name,
        "cpu_count_physical": psutil.cpu_count(logical=False),
        "cpu_count_logical": psutil.cpu_count(logical=True),
        "cpu_freq_mhz": getattr(freq, "current", None),
        "torch_num_threads": torch.get_num_threads(),
        "ram_total_gb": round(psutil.virtual_memory().total / 1024**3, 2),
    }


def _wmic_cpu():
    import subprocess

    r = subprocess.run(
        ["powershell", "-NoProfile", "-Command",
         "(Get-CimInstance Win32_Processor).Name"],
        capture_output=True, text=True, timeout=30)
    return [r.stdout] if r.returncode == 0 else []


def rss_mb() -> float:
    """Current process RSS in MiB."""
    import psutil

    return round(psutil.Process().memory_info().rss / 1024**2, 1)
