"""Pinned C-source extraction tests, NOT a SGLang/GPU runtime simulation.

Only exact patched methods are executed. External scheduler/allocator objects in
these three suites are bounded stubs. Optional image tests use real installed
Torch/msgspec and require an explicit root-only environment opt-in.
"""
from __future__ import annotations

import ast
import builtins
import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import patch
from dataclasses import dataclass, field, asdict
from collections import Counter
import uuid

EXT = Path(__file__).resolve().parents[1]
MANIFEST = EXT / "patches/scheduler-cache-ipc.v1.json"
ATTESTED = Path(os.environ.get("OCC3_ATTESTED_SRT", r"C:\Users\openclawuser\Optimus-Assistant\profile\workspace\occ3-campaign\v3-controlled-deploy\original-sglang-source-attestation-v2"))
if str(EXT) not in sys.path:
    sys.path.insert(0, str(EXT))
from occ3_sglang.attachment_contract import CaptureWorkerDescriptor, ConsumeWorkerDescriptor, normalize_worker_descriptor


def descriptor(operation="capture", **changes):
    common = dict(attempt_id="attempt", reservation_id="reservation", native_rid="native-rid",
                  principal_digest="a" * 64, runtime_epoch="epoch", runtime_generation=7,
                  model_fingerprint="model", method_id="statebridge.glm53.final-prenorm-predictor.continuous-prefix.v1",
                  deadline=1000.0)
    if operation == "capture":
        common.update(capture_id="capture", source_session_id="source", source_turn_nonce="turn",
                      span_policy="whole_completed_response_tail_v1", max_prefix_tokens=8,
                      max_capture_bytes=64 * 1024 * 1024)
        cls = CaptureWorkerDescriptor
    else:
        common.update(attachment_digest="b" * 64, receiver_session_id="receiver", message_id="message",
                      consume_nonce="consume", opaque_handle="handle", state_generation=11,
                      source_capture_commit_id="commit", tp_layout_digest="c" * 64,
                      expected_rank_count=8, expected_prefix_tokens=8, hidden_dim=6144, dtype="bfloat16")
        cls = ConsumeWorkerDescriptor
    common.update(changes)
    return cls(**common)


class AppliedSource:
    """Always applies all nine manifest entries through root's real applier."""
    def __init__(self):
        self.temp = tempfile.TemporaryDirectory(prefix="occ3-c-pinned-")
        self.root = Path(self.temp.name)
        self.manifest = json.loads(MANIFEST.read_bytes())
        for f in self.manifest["files"]:
            src = ATTESTED / f["path"]
            if not src.exists():
                self.temp.cleanup()
                raise unittest.SkipTest("exact attested SGLang source snapshot unavailable; set OCC3_ATTESTED_SRT")
            dst = self.root / f["path"]
            dst.parent.mkdir(parents=True, exist_ok=True)
            dst.write_bytes(src.read_bytes())
        spec = importlib.util.spec_from_file_location("occ3_c_source_patch", EXT / "deploy/v3/source_patch.py")
        self.applier = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = self.applier
        spec.loader.exec_module(self.applier)
        self.plan = self.applier.prepare(self.root, self.manifest)
        self.proof = self.applier.apply(self.plan)
        self.text = {f["path"]: (self.root / f["path"]).read_text(encoding="utf-8") for f in self.manifest["files"]}

    def close(self):
        self.temp.cleanup()

    def node(self, path, name, scope=None):
        tree = ast.parse(self.text[path])
        body = tree.body
        if scope:
            body = next(n for n in body if isinstance(n, ast.ClassDef) and n.name == scope).body
        return copy.deepcopy(next(n for n in body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and n.name == name))

    def function(self, path, name, scope=None, **namespace):
        node = self.node(path, name, scope)
        node.decorator_list = []
        code = ast.Module(body=[ast.ImportFrom(module="__future__", names=[ast.alias(name="annotations")], level=0), node], type_ignores=[])
        ns = dict(namespace)
        exec(compile(ast.fix_missing_locations(code), f"{path}:{node.lineno}", "exec"), ns)
        return ns[name]

    def generate_class(self):
        node = self.node("managers/io_struct.py", "GenerateReqInput")
        module = types.ModuleType("occ3_c_generate_fixture")
        sys.modules[module.__name__] = module
        module.__dict__.update(dataclass=dataclass, field=field, uuid=uuid, Counter=Counter,
                               copy=copy, decode_kv_hints_envelope=lambda value: value,
                               has_valid_data=bool)
        body = [ast.ImportFrom(module="__future__", names=[ast.alias(name="annotations")], level=0), node]
        exec(compile(ast.fix_missing_locations(ast.Module(body=body, type_ignores=[])), "pinned-GenerateReqInput", "exec"), module.__dict__)
        return module.GenerateReqInput


class PinnedCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.sources = AppliedSource()

    @classmethod
    def tearDownClass(cls):
        cls.sources.close()


class TestPinnedIpc(PinnedCase):
    def test_manifest_exact_raw_hashes_and_compilation(self):
        self.assertTrue(self.sources.proof["applied"])
        self.assertFalse(self.sources.proof["gpu_proof"])
        self.assertEqual(len(self.sources.manifest["files"]), 9)
        for f in self.sources.manifest["files"]:
            self.assertEqual(hashlib.sha256((ATTESTED / f["path"]).read_bytes()).hexdigest(), f["before_sha256"])
            data = (self.sources.root / f["path"]).read_bytes()
            self.assertEqual(hashlib.sha256(data).hexdigest(), f["after_sha256"])
            compile(data, f["path"], "exec")
        with self.assertRaises(self.sources.applier.PatchError):
            self.sources.applier.prepare(self.sources.root, self.sources.manifest)

    def test_crlf_drift_is_not_normalized(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for f in self.sources.manifest["files"]:
                dest = root / f["path"]
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_bytes((ATTESTED / f["path"]).read_bytes().replace(b"\n", b"\r\n"))
            with self.assertRaises(self.sources.applier.PatchError) as failure:
                self.sources.applier.prepare(root, self.sources.manifest)
            self.assertEqual(failure.exception.code, "original_digest_mismatch")

    def test_exact_generate_normalization_scalar_and_identity(self):
        cls = self.sources.generate_class()
        for op in ("capture", "consume"):
            d = descriptor(op)
            obj = cls(rid=d.native_rid, text="receiver text", sampling_params={"max_new_tokens": 9999}, occ3_worker_descriptor=asdict(d))
            obj.normalize_batch_and_arguments()
            self.assertEqual(obj.occ3_worker_descriptor, d)
            self.assertEqual(obj.text, "receiver text")
            self.assertEqual(obj.sampling_params["max_new_tokens"], 9999)
            with self.assertRaisesRegex(ValueError, "replay_forbidden"):
                obj.regenerate_rid()
            with self.assertRaisesRegex(ValueError, "batch_unsupported"):
                obj[0]
        obj = cls(rid="different", text="x", occ3_worker_descriptor=asdict(d))
        with self.assertRaisesRegex(ValueError, "invalid_native_request"):
            obj.normalize_batch_and_arguments()

    def test_parallel_and_batch_optin_reject_before_expansion(self):
        cls = self.sources.generate_class()
        for text, params in [(["a", "b"], {}), ("a", {"n": 2}), ("a", {"beam_width": 2})]:
            obj = cls(rid="native-rid", text=text, sampling_params=params, occ3_worker_descriptor=descriptor())
            before = copy.deepcopy(obj.text)
            with self.assertRaisesRegex(ValueError, "unsupported_profile"):
                obj.normalize_batch_and_arguments()
            self.assertEqual(before, obj.text)

    def test_none_does_not_import_extension(self):
        cls = self.sources.generate_class()
        original = builtins.__import__
        def guarded(name, *args, **kwargs):
            if name.startswith("occ3_sglang"):
                raise AssertionError("ordinary request imported backend")
            return original(name, *args, **kwargs)
        with patch("builtins.__import__", side_effect=guarded):
            obj = cls(text="ordinary", sampling_params={"max_new_tokens": 42})
            obj.normalize_batch_and_arguments()
            self.assertIsNone(obj.occ3_worker_descriptor)
            self.assertEqual(obj.sampling_params["max_new_tokens"], 42)

    def ipc_validator(self):
        class Tokenized:
            def __init__(self, value=None):
                self.occ3_worker_descriptor, self.rid = value, "native-rid"
        class Batch:
            def __init__(self, batch): self.batch = batch
        class Control:
            def __init__(self, payload): self.payload, self.rid = payload, "command"
        class Output(Control): pass
        fn = self.sources.function("managers/io_struct.py", "_validate_occ3_ipc",
                                   TokenizedGenerateReqInput=Tokenized, BatchTokenizedGenerateReqInput=Batch,
                                   Occ3ControlReq=Control, Occ3ControlOutput=Output)
        fn.__globals__["_validate_occ3_ipc"] = fn
        return fn, Tokenized, Batch, Control, Output

    def test_exact_wire_validator_zero_tensor_and_distinct_generations(self):
        fn, Tokenized, Batch, _, _ = self.ipc_validator()
        for operation in ("capture", "consume"):
            req = Tokenized(descriptor(operation))
            fn(Batch([req]))
            self.assertIs(type(req.occ3_worker_descriptor), dict)
            self.assertTrue(all(type(v) in (str, int, float) for v in req.occ3_worker_descriptor.values()))
            self.assertEqual(normalize_worker_descriptor(req.occ3_worker_descriptor), descriptor(operation))
        self.assertNotEqual(req.occ3_worker_descriptor["runtime_generation"], req.occ3_worker_descriptor["state_generation"])
        class TensorTrap:
            def cpu(self): raise AssertionError("tensor reached D2H")
            def __reduce__(self): raise AssertionError("tensor reached pickle")
        for value in [TensorTrap(), {**asdict(descriptor()), "capture_id": TensorTrap()},
                      {**asdict(descriptor()), "hidden": [1.0]}, {**asdict(descriptor()), "operation": "other"}]:
            with self.assertRaises((ValueError, TypeError)):
                fn(Tokenized(value))

    def test_exact_control_validation_before_serialization(self):
        fn, _, _, Control, Output = self.ipc_validator()
        from occ3_sglang.latent_types import control_request, LatentError
        obj = Control(control_request("command", "health", {}))
        fn(obj)
        fn(Output({"schema": "occ3.control.result.v1", "command_id": "command", "ok": True, "result": {}, "error": ""}))
        for payload in [dict(obj.payload, extra=1), dict(obj.payload, payload={"tensor": object()}), dict(obj.payload, command_id="wrong")]:
            with self.assertRaises((LatentError, ValueError)):
                fn(Control(payload))

    def test_exact_tokenizer_wire_preserves_real_rid(self):
        fn = self.sources.function("managers/tokenizer_manager.py", "_occ3_descriptor_wire", "TokenizerManager")
        self.assertIsNone(fn(types.SimpleNamespace(occ3_worker_descriptor=None)))
        obj = types.SimpleNamespace(occ3_worker_descriptor=descriptor(), rid="native-rid")
        self.assertEqual(fn(obj), asdict(descriptor()))
        obj.rid = "not-native"
        with self.assertRaises(ValueError): fn(obj)

    def test_actual_shared_helper_signatures_are_bound_not_invented(self):
        # Import the installed extension through the native test interpreter.
        # This is only API coherence; it does not initialize Torch or SGLang.
        import inspect
        from occ3_sglang import latent_worker as worker
        expected={
            "bootstrap_worker":["scheduler"],
            "admit_request":["scheduler","req","value"],
            "prepare_batch":["scheduler","batch"],
            "commit_result":["scheduler","req","result","request_index","output_start","output_end","terminal","forward_iter"],
            "abort_request":["scheduler","req","reason"],
            "fence_private_kv_release":["scheduler","req"],
            "handle_control":["scheduler","payload"],
            "deliver_control":["tokenizer_manager","payload"],
        }
        for name,parameters in expected.items():
            self.assertEqual(list(inspect.signature(getattr(worker,name)).parameters),parameters)

    @unittest.skipUnless(os.environ.get("OCC3_ROOT_IMAGE_CPU_TESTS") == "1", "root-only exact-image test; full SGLang/msgspec/Torch unavailable in default offline environment")
    def test_root_only_actual_installed_msgpack_round_trip(self):
        from array import array
        from sglang.srt.managers import io_struct as io
        from sglang.srt.sampling.sampling_params import SamplingParams
        manifest_file = next(f for f in self.sources.manifest["files"] if f["path"] == "managers/io_struct.py")
        self.assertEqual(hashlib.sha256(Path(io.__file__).read_bytes()).hexdigest(), manifest_file["after_sha256"])
        req = io.TokenizedGenerateReqInput(rid="native-rid", input_text="x", input_ids=array("q", [1]),
            input_embeds=None, mm_inputs=None, token_type_ids=None, sampling_params=SamplingParams(max_new_tokens=42),
            return_logprob=False, logprob_start_len=-1, top_logprobs_num=0, token_ids_logprob=None, stream=False,
            occ3_worker_descriptor=asdict(descriptor()))
        decoded = io.msgpack_decode(io.msgpack_encode(req))
        self.assertEqual(normalize_worker_descriptor(decoded.occ3_worker_descriptor), descriptor())


if __name__ == "__main__":
    unittest.main()
