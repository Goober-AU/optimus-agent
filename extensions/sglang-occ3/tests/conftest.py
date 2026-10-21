"""Offline test fixtures: stub the SGLang API surface.

The tests import occ3_sglang without a SGLang install and without a
GPU. The stubs mirror the exact SGLang 0.5.21 contracts the plugin
relies on (GenerateReqInput kwargs, PositionalEmbeds validation,
the http_server module globals, the plugin hook registry).
"""

from __future__ import annotations

import dataclasses
import sys
import types
from typing import Any, Dict, List

import pytest


class _RecordedRequest:
    """One submitted generate request, as the plugin built it."""

    def __init__(self, obj: "StubGenerateReqInput") -> None:
        self.input_ids = list(obj.input_ids) if obj.input_ids is not None else None
        self.text = obj.text
        self.cache_salt = obj.cache_salt
        self.sampling_params = dict(obj.sampling_params or {})
        self.rid = obj.rid
        self.return_prompt_token_ids = obj.return_prompt_token_ids
        self.positional_embed_overrides = obj.positional_embed_overrides
        self.session_id = obj.session_id
        self.routed_dp_rank = obj.routed_dp_rank


@dataclasses.dataclass
class StubGenerateReqInput:
    input_ids: List[int] | None = None
    text: str | None = None
    cache_salt: str | None = None
    sampling_params: Dict[str, Any] | None = None
    rid: str | None = None
    return_prompt_token_ids: bool = False
    positional_embed_overrides: Any = None
    session_id: str | None = None
    routed_dp_rank: int | None = None


@dataclasses.dataclass
class StubAbortReq:
    rid: str = ""
    abort_all: bool = False


class StubPositionalEmbeds:
    """Mirror of sglang PositionalEmbeds (embeds stacked, lengths checked)."""

    def __init__(self, embeds, positions):
        if isinstance(embeds, list):
            if not embeds:
                raise ValueError("empty embeds")
            if not hasattr(embeds[0], "dim"):  # plain lists in tests
                embeds = [row for row in embeds]
        if len(embeds) != len(positions):
            raise ValueError(
                f"embeds length ({len(embeds)}) != positions length ({len(positions)})"
            )
        self.embeds = embeds
        self.positions = list(positions)


class StubTokenizer:
    def __init__(self, vocab=None):
        self._vocab = vocab or {}

    def encode(self, text: str, add_special_tokens=True) -> List[int]:
        ids = self._vocab.get(text)
        if ids is not None:
            return list(ids)
        return [1000 + (i % 97) for i, _ in enumerate(text)]


class StubState:
    def __init__(self, tokenizer_manager):
        self.tokenizer_manager = tokenizer_manager


class StubHttpServer(types.ModuleType):
    """Mirror of sglang.srt.entrypoints.http_server (module globals)."""

    def __init__(self):
        super().__init__("sglang.srt.entrypoints.http_server")
        self._global_state = None
        self.app = None  # set by a FastAPI app fixture


class StubHookRegistry:
    def __init__(self):
        self.registrations: List[Dict[str, Any]] = []

    def register(self, target, hook, hook_type=None, source=None):
        self.registrations.append(
            {"target": target, "hook": hook, "hook_type": hook_type}
        )


class HookType:
    BEFORE = "before"
    AFTER = "after"
    AROUND = "around"
    REPLACE = "replace"


class _StubTorch:
    """Minimal torch stand-in for offline tests (list-backed tensors)."""

    float32 = "float32"
    contiguous_format = "contiguous"

    @staticmethod
    def tensor(data, dtype=None):
        return data

    @staticmethod
    def save(obj, path):
        with open(path, "wb") as f:
            f.write(b"stub")

    class distributed:  # noqa: N801
        @staticmethod
        def is_available():
            return False


def _install_stubs(http_server_module: StubHttpServer, hook_registry: StubHookRegistry):
    torch_stub = types.ModuleType("torch")
    torch_stub.float32 = _StubTorch.float32
    torch_stub.contiguous_format = _StubTorch.contiguous_format
    torch_stub.tensor = _StubTorch.tensor
    torch_stub.save = _StubTorch.save
    torch_stub.distributed = _StubTorch.distributed
    sys.modules["torch"] = torch_stub

    sglang = types.ModuleType("sglang")
    sglang.__version__ = "0.5.21-test"
    sglang.__path__ = []  # namespace-ish

    srt = types.ModuleType("sglang.srt")
    srt.__path__ = []

    entrypoints = types.ModuleType("sglang.srt.entrypoints")
    entrypoints.__path__ = []

    managers = types.ModuleType("sglang.srt.managers")
    managers.__path__ = []

    io_struct = types.ModuleType("sglang.srt.managers.io_struct")
    io_struct.GenerateReqInput = StubGenerateReqInput
    io_struct.AbortReq = StubAbortReq

    embed_types = types.ModuleType("sglang.srt.managers.embed_types")
    embed_types.PositionalEmbeds = StubPositionalEmbeds

    plugins = types.ModuleType("sglang.srt.plugins")
    plugins.__path__ = []
    hook_registry_mod = types.ModuleType("sglang.srt.plugins.hook_registry")
    hook_registry_mod.HookRegistry = hook_registry
    hook_registry_mod.HookType = HookType

    sys.modules["sglang"] = sglang
    sys.modules["sglang.srt"] = srt
    sys.modules["sglang.srt.entrypoints"] = entrypoints
    sys.modules["sglang.srt.entrypoints.http_server"] = http_server_module
    sys.modules["sglang.srt.managers"] = managers
    sys.modules["sglang.srt.managers.io_struct"] = io_struct
    sys.modules["sglang.srt.managers.embed_types"] = embed_types
    sys.modules["sglang.srt.plugins"] = plugins
    sys.modules["sglang.srt.plugins.hook_registry"] = hook_registry_mod


@pytest.fixture
def stub_http_server():
    server = StubHttpServer()
    registry = StubHookRegistry()
    _install_stubs(server, registry)
    return server


@pytest.fixture
def stub_tm(stub_http_server):
    """A tokenizer manager that records requests and returns canned outputs."""

    class StubTokenizerManager:
        def __init__(self):
            self.requests: List[_RecordedRequest] = []
            self.tokenizer = StubTokenizer(
                {
                    "hello latent world": [11, 22, 33, 44, 55],
                    "answer briefly": [66, 77, 88],
                }
            )
            self.server_args = types.SimpleNamespace(
                model_path="/models/glm-5.3",
                revision="aca966e4e02791568aa6a4ced368624b3d897f42",
                tokenizer_path="/models/glm-5.3",
                page_size=64,
                kv_cache_dtype="fp8_e4m3",
                tp_size=8,
                speculative_algorithm="EAGLE",
                quantization=None,
            )
            self.served_model_name = "glm-5.3"
            self.model_config = types.SimpleNamespace(
                hf_config=types.SimpleNamespace(
                    architectures=["GlmMoeDsaForCausalLM"],
                    _commit_hash="aca966e4e02791568aa6a4ced368624b3d897f42",
                    torch_dtype="bfloat16",
                    quantization_config={"quant_method": "fp8", "format": "e4m3"},
                    hidden_size=6144,
                    num_hidden_layers=78,
                    num_attention_heads=48,
                    vocab_size=160000,
                )
            )
            # Per-request canned responses keyed by rid prefix.
            self.responses: Dict[str, Dict[str, Any]] = {}
            self.default_cached_tokens = 5

        async def generate_request(self, obj, request=None):
            self.requests.append(_RecordedRequest(obj))
            rid = obj.rid or ""
            if rid.startswith("occ3-send-"):
                out = {
                    "text": " ok",
                    "output_ids": [9],
                    "prompt_token_ids": list(obj.input_ids or [])
                    or self.tokenizer.encode(obj.text),
                    "meta_info": {
                        "cached_tokens": 0,
                        "prompt_tokens": len(obj.input_ids or [])
                        or len(self.tokenizer.encode(obj.text or "")),
                        "completion_tokens": 1,
                        "finish_reason": {"type": "length"},
                    },
                }
            else:
                cached = self.default_cached_tokens
                out = {
                    "text": "received",
                    "output_ids": [1, 2, 3],
                    "meta_info": {
                        "cached_tokens": cached,
                        "prompt_tokens": len(obj.input_ids or []),
                        "completion_tokens": 3,
                        "finish_reason": {"type": "stop"},
                    },
                }
            custom = self.responses.get(rid)
            if custom:
                out = custom
            yield out

    return StubTokenizerManager()


@pytest.fixture
def kv_client(stub_http_server, stub_tm, monkeypatch):
    """Offline cache-tier simulation, NOT a CUDA or SGLang implementation test."""
    import copy
    import json
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from occ3_sglang import api, kv_handles, metrics

    clock = [1000.0]
    monkeypatch.setattr(kv_handles, "store", kv_handles.CaptureStore(clock=lambda: clock[0]))
    monkeypatch.setattr(metrics, "_metrics", metrics.Metrics())
    sys.modules["sglang"].__version__ = "0.5.21"
    stub_tm.server_args.device = "cuda"
    stub_tm.server_args.dp_size = 1
    stub_tm.server_args.disable_radix_cache = False
    stub_tm.server_args.enable_lora = False
    stub_tm.server_args.disaggregation_mode = "null"

    class Tokenizer:
        def encode(self, text, add_special_tokens=True):
            return list(text.encode("utf-8"))
    stub_tm.tokenizer = Tokenizer()
    stub_tm.kv_requests = []
    stub_tm.kv_cache = {}
    stub_tm.mutate_output = None
    stub_tm.fail_generation = False
    stub_tm.abort_requests = []

    async def abort_dispatch(req):
        stub_tm.abort_requests.append(req)
    stub_tm._async_dispatch_to_scheduler = abort_dispatch

    async def fake_generate(obj, request=None):
        stub_tm.kv_requests.append(copy.deepcopy(obj))
        clock[0] += 0.4
        if stub_tm.fail_generation:
            raise RuntimeError("synthetic-private-server-error")
        ids = list(obj.input_ids)
        cached = 0
        text = "source complete"
        if obj.rid.startswith("occ3-capture-"):
            cached = len(stub_tm.kv_cache.get(obj.cache_salt, []))
            stub_tm.kv_cache[obj.cache_salt] = list(ids)
        else:
            source = stub_tm.kv_cache.get(obj.cache_salt, [])
            if ids[:len(source)] == source:
                page = stub_tm.server_args.page_size
                cached = max(0, ((len(source) - 1) // page) * page)
            decoded = bytes(ids).decode("utf-8", "replace")
            try:
                facts, _ = json.JSONDecoder().raw_decode(decoded[decoded.index("{"):])
                text = json.dumps(facts)
            except (ValueError, TypeError):
                text = "receiver fixture"
        out = {"text": text, "prompt_token_ids": ids, "output_ids": [7],
               "meta_info": {"cached_tokens": cached,
                             "cached_tokens_details": {"device": cached, "host": 0},
                             "prompt_tokens": len(ids), "completion_tokens": 1,
                             "num_retractions": 0, "dp_rank": 0,
                             "id": obj.rid, "weight_version": "fixture-weight-v1",
                             "finish_reason": {"type": "stop"}}}
        if stub_tm.mutate_output:
            stub_tm.mutate_output(obj, out)
        yield out
    stub_tm.generate_request = fake_generate
    app = FastAPI()
    app.include_router(api.router)
    app.include_router(kv_handles.router)
    stub_http_server.app = app
    stub_http_server._global_state = StubState(stub_tm)
    with TestClient(app) as client:
        yield client, stub_tm, clock
