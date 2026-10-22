"""Actual temporary files, not import/help/stat-only artifact proofs."""
import json
from pathlib import Path
import os
import sys

import pytest

from deploy.v3 import artifact_preflight as ap


def write_json(path, value):
    path.write_text(json.dumps(value), encoding="utf-8")


def write_shard(path, size=20_000):
    header = json.dumps({"weight": {"dtype": "U8", "shape": [size], "data_offsets": [0, size]}}).encode()
    path.write_bytes(len(header).to_bytes(8, "little") + header + b"t" * size)


@pytest.fixture
def artifacts(tmp_path):
    model = tmp_path / "model"
    model.mkdir()
    write_json(model / "config.json", {
        "model_type": "glm_moe_dsa", "architectures": ["GlmMoeDsaForCausalLM"],
        "quantization_config": {"quant_method": "fp8"},
    })
    write_json(model / "tokenizer_config.json", {"tokenizer_class": "PreTrainedTokenizerFast"})
    write_json(model / "tokenizer.json", {"model": {"type": "BPE", "vocab": {"a": 0}}})
    write_json(model / "model.safetensors.index.json", {
        "metadata": {"total_size": 40000},
        "weight_map": {"weight.one": "one.safetensors", "weight.two": "two.safetensors"},
    })
    write_shard(model / "one.safetensors")
    write_shard(model / "two.safetensors")
    write_json(tmp_path / "adaptive.json", {"enabled": True})
    return {
        "schema": ap.SCHEMA, "model_path": str(model), "tokenizer_path": str(model),
        "allowed_roots": [str(tmp_path.resolve())], "required_json": [str(tmp_path / "adaptive.json")],
        "load_format": "auto", "loader_profile": ap.LOADER_PROFILE,
    }


def test_reads_real_bytes_from_every_index_shard(artifacts, monkeypatch):
    reads = []
    original_read = ap._Reader.read
    modules_before = set(sys.modules)
    def observed(reader, handle, count):
        reads.append((handle.tell(), count))
        return original_read(reader, handle, count)
    monkeypatch.setattr(ap._Reader, "read", observed)
    result = ap.probe_artifacts(artifacts)
    assert result["weight_shards"] == 2
    assert result["weight_data_bytes_read"] == 4 * ap.SAMPLE_BYTES
    assert result["weight_header_bytes_read"] > 16
    assert result["required_json_files"] == 1 and result["cpu_only"] is True
    assert result["result"] == "PASS" and any(offset > 1000 for offset, _ in reads)
    assert len(json.dumps(result)) < 1024
    assert artifacts["model_path"] not in json.dumps(result)
    assert "glm_moe_dsa" not in json.dumps(result)
    assert not {"torch", "safetensors", "transformers"} & (set(sys.modules) - modules_before)


@pytest.mark.parametrize("filename", ["config.json", "tokenizer_config.json", "tokenizer.json", "model.safetensors.index.json"])
def test_invalid_or_truncated_json_fails(artifacts, filename):
    (Path(artifacts["model_path"]) / filename).write_text('{"truncated":', encoding="utf-8")
    with pytest.raises(ap.PreflightError, match="json"):
        ap.probe_artifacts(artifacts)


def test_large_actual_layout_sized_json_is_fully_parsed(artifacts):
    model = Path(artifacts["model_path"])
    # Regression: both actual tokenizer (20MB) and index (11MB) exceed old 4MB hash prefix.
    write_json(model / "tokenizer.json", {"model": {"type": "BPE"}, "padding_fixture": "x" * 20_217_442})
    write_json(model / "model.safetensors.index.json", {
        "weight_map": {"one": "one.safetensors", "two": "two.safetensors"},
        "metadata": {"fixture_padding": "x" * 11_359_251},
    })
    proof = ap.probe_artifacts(artifacts)
    assert proof["tokenizer_bytes_read"] > 20_000_000
    assert proof["total_bytes_read"] > 31_000_000
    with (model / "tokenizer.json").open("ab") as handle:
        handle.write(b"not-json-after-old-prefix")
    with pytest.raises(ap.PreflightError, match="invalid_json"):
        ap.probe_artifacts(artifacts)


@pytest.mark.parametrize("ref", ["../outside.safetensors", "/etc/passwd", "dir/../../x.safetensors",
                                 "dir\\x.safetensors", "http://host/x.safetensors", "one.bin", "./one.safetensors"])
def test_untrusted_index_references_rejected_before_shards(artifacts, ref, monkeypatch):
    model = Path(artifacts["model_path"])
    write_json(model / "model.safetensors.index.json", {"weight_map": {"x": ref}})
    monkeypatch.setattr(ap._Reader, "safetensors", lambda *args: pytest.fail("unsafe shard opened"))
    with pytest.raises(ap.PreflightError):
        ap.probe_artifacts(artifacts)


def test_index_requires_all_referenced_shards(artifacts):
    (Path(artifacts["model_path"]) / "two.safetensors").unlink()
    with pytest.raises(ap.PreflightError, match="unavailable"):
        ap.probe_artifacts(artifacts)


def test_index_duplicate_keys_and_empty_weight_map_rejected(artifacts):
    index = Path(artifacts["model_path"]) / "model.safetensors.index.json"
    index.write_text('{"weight_map":{"x":"one.safetensors","x":"two.safetensors"}}', encoding="utf-8")
    with pytest.raises(ap.PreflightError):
        ap.probe_artifacts(artifacts)
    write_json(index, {"weight_map": {}})
    with pytest.raises(ap.PreflightError, match="weight_index"):
        ap.probe_artifacts(artifacts)


def test_tokenizer_config_alone_is_not_artifact_proof(artifacts):
    (Path(artifacts["model_path"]) / "tokenizer.json").unlink()
    with pytest.raises(ap.PreflightError, match="tokenizer_artifact_missing"):
        ap.probe_artifacts(artifacts)


def test_tokenizer_explicit_file_and_required_launch_json_are_read(artifacts):
    model = Path(artifacts["model_path"])
    write_json(model / "tokenizer_config.json", {"tokenizer_file": "missing.json"})
    with pytest.raises(ap.PreflightError):
        ap.probe_artifacts(artifacts)
    write_json(model / "tokenizer_config.json", {"tokenizer_class": "Fast"})
    Path(artifacts["required_json"][0]).write_text("broken", encoding="utf-8")
    with pytest.raises(ap.PreflightError, match="json"):
        ap.probe_artifacts(artifacts)


def symlink_or_skip(link, target):
    try:
        link.symlink_to(target)
    except OSError:
        pytest.skip("symlink privilege unavailable on this offline host")


def test_huggingface_blob_symlink_within_data_mount_is_read(artifacts):
    model = Path(artifacts["model_path"])
    blobs = model.parent / "blobs"
    blobs.mkdir()
    (model / "one.safetensors").rename(blobs / "hash")
    symlink_or_skip(model / "one.safetensors", Path("../blobs/hash"))
    result = ap.probe_artifacts(artifacts)
    assert result["symlink_files"] >= 1 and result["weight_shards"] == 2


def test_symlink_escaping_mount_is_rejected(artifacts, tmp_path):
    model = Path(artifacts["model_path"])
    (model / "one.safetensors").unlink()
    outside = tmp_path.parent / (tmp_path.name + "-outside.safetensors")
    write_shard(outside)
    try:
        symlink_or_skip(model / "one.safetensors", outside)
        with pytest.raises(ap.PreflightError, match="escapes"):
            ap.probe_artifacts(artifacts)
    finally:
        outside.unlink()


def test_dangling_index_symlink_never_silently_falls_back(artifacts):
    model = Path(artifacts["model_path"])
    (model / "model.safetensors.index.json").unlink()
    symlink_or_skip(model / "model.safetensors.index.json", model / "absent")
    write_shard(model / "model.safetensors")
    with pytest.raises(ap.PreflightError):
        ap.probe_artifacts(artifacts)


def test_regular_file_check_and_actual_read_failure(artifacts, monkeypatch):
    model = Path(artifacts["model_path"])
    (model / "one.safetensors").unlink()
    (model / "one.safetensors").mkdir()
    with pytest.raises(ap.PreflightError):
        ap.probe_artifacts(artifacts)
    (model / "one.safetensors").rmdir()
    write_shard(model / "one.safetensors")
    def unreadable(*args):
        raise OSError("private path or model data must not print")
    monkeypatch.setattr(ap._Reader, "read", unreadable)
    with pytest.raises(ap.PreflightError, match="artifact_probe_failed") as failure:
        ap.probe_artifacts(artifacts)
    assert "private path" not in str(failure.value)


def test_shard_header_must_point_to_real_data(artifacts):
    model = Path(artifacts["model_path"])
    header = json.dumps({"weight": {"dtype": "U8", "shape": [20], "data_offsets": [0, 200]}}).encode()
    (model / "one.safetensors").write_bytes(len(header).to_bytes(8, "little") + header + b"x" * 20)
    with pytest.raises(ap.PreflightError, match="offsets"):
        ap.probe_artifacts(artifacts)


def test_json_budget_is_enforced_without_truncated_pass(artifacts, monkeypatch):
    monkeypatch.setattr(ap, "MAX_TOTAL_BYTES", 16)
    with pytest.raises(ap.PreflightError, match="budget"):
        ap.probe_artifacts(artifacts)


def test_probe_does_not_walk_directories(artifacts, monkeypatch):
    monkeypatch.setattr(os, "walk", lambda *args, **kwargs: pytest.fail("walk forbidden"))
    monkeypatch.setattr(Path, "iterdir", lambda *args, **kwargs: pytest.fail("directory scan forbidden"))
    assert ap.probe_artifacts(artifacts)["result"] == "PASS"


def test_cli_emits_only_bounded_safe_json(artifacts, capsys, monkeypatch):
    for key in ("LD_PRELOAD", "LD_AUDIT", "LD_LIBRARY_PATH", "GCONV_PATH", "LOCPATH",
                "PYTHONPATH", "PYTHONHOME", "PYTHONSTARTUP", "PYTHONUSERBASE", "ENV", "BASH_ENV"):
        monkeypatch.delenv(key, raising=False)
    assert ap.main(["--spec", json.dumps(artifacts)]) == 0
    success = json.loads(capsys.readouterr().out)
    assert success["result"] == "PASS"
    private = "a-private-error-secret"
    artifacts["required_json"] = [private]
    assert ap.main(["--spec", json.dumps(artifacts)]) == 1
    output = capsys.readouterr().out
    assert private not in output and artifacts["model_path"] not in output
    assert json.loads(output)["result"] == "FAIL"


@pytest.mark.parametrize("change", [
    {"model_type": "different"}, {"architectures": ["Glm4MoeForCausalLM"]},
    {"quantization_config": {"quant_method": "bitsandbytes"}},
])
def test_model_loader_profile_is_not_assumed(artifacts, change):
    config_path = Path(artifacts["model_path"]) / "config.json"
    config = json.loads(config_path.read_text(encoding="utf-8"))
    config.update(change)
    write_json(config_path, config)
    with pytest.raises(ap.PreflightError, match="loader_profile"):
        ap.probe_artifacts(artifacts)


def test_profile_requires_index_and_root_glob_compatible_references(artifacts):
    index = Path(artifacts["model_path"]) / "model.safetensors.index.json"
    write_json(index, {"weight_map": {"x": "subdir/one.safetensors"}})
    with pytest.raises(ap.PreflightError, match="root_glob"):
        ap.probe_artifacts(artifacts)
    index.unlink()
    with pytest.raises(ap.PreflightError, match="requires_index"):
        ap.probe_artifacts(artifacts)


def test_unreviewed_binary_tokenizer_is_not_substitute_for_json(artifacts):
    model = Path(artifacts["model_path"])
    (model / "tokenizer.json").unlink()
    (model / "tokenizer.model").write_bytes(b"nonempty-unreviewed-tokenizer")
    with pytest.raises(ap.PreflightError, match="tokenizer_artifact_missing"):
        ap.probe_artifacts(artifacts)


@pytest.mark.parametrize("key", ["LD_PRELOAD", "LD_AUDIT", "PYTHONPATH", "PYTHONSTARTUP"])
def test_standalone_refuses_hook_environment_as_defense_in_depth(artifacts, monkeypatch, capsys, key):
    for variable in ("LD_PRELOAD", "LD_AUDIT", "LD_LIBRARY_PATH", "GCONV_PATH", "LOCPATH",
                     "PYTHONPATH", "PYTHONHOME", "PYTHONSTARTUP", "PYTHONUSERBASE", "ENV", "BASH_ENV"):
        monkeypatch.delenv(variable, raising=False)
    monkeypatch.setenv(key, "/private/hook")
    assert ap.main(["--spec", json.dumps(artifacts)]) == 1
    output = capsys.readouterr().out
    assert json.loads(output)["error"] == "executable_environment_hook_exposed"
    assert "/private" not in output
