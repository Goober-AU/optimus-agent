#!/usr/bin/env python3
"""Standalone CPU-only artifact readability probe. Run with python3 -I -S.

No third-party imports, model loaders, pickle deserialization, GPU initialization,
network requests, writes, directory walks, or model-content output. JSON files
are parsed completely under size limits. Safetensors headers/offsets are checked,
then real tensor bytes at the beginning and end of EVERY indexed shard are read.
This is bounded readability evidence, not full-weight integrity or serving proof.
Symlink targets must stay within the original data mounts. Linux opens traverse
canonical components with O_NOFOLLOW and reject non-regular files before reads.
"""
from __future__ import annotations

import json
import os
from pathlib import Path, PurePosixPath
import stat
import sys

SCHEMA = "occ3-artifact-preflight/v1"
LOADER_PROFILE = "sglang-0.5.21-glm-dsa-native-fp8"
# Committed e00930c5489053f26d86b179cee0d087f846acbb: DefaultModelLoader
# root safetensors glob + index filtering (loader.py 470-617); GLM-DSA is
# excluded from the separate Glm4Moe MTP-file exception (weight_utils.py 740-848).
# A live caller must attest candidate loader/model sources independently.
MAX_JSON_BYTES = 64 * 1024 * 1024
MAX_HEADER_BYTES = 16 * 1024 * 1024
MAX_TOTAL_BYTES = 256 * 1024 * 1024
MAX_SHARDS = 4096
MAX_INDEX_ENTRIES = 500_000
SAMPLE_BYTES = 4096


class PreflightError(ValueError):
    """Only fixed diagnostic codes may be emitted."""


def _fail(code):
    raise PreflightError(code)


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            _fail("duplicate_json_key")
        result[key] = value
    return result


def _json_bytes(raw):
    try:
        return json.loads(raw, object_pairs_hook=_unique_object,
                          parse_constant=lambda _: _fail("nonfinite_json_number"))
    except (ValueError, UnicodeError, RecursionError):
        _fail("invalid_json")


def _reference(root: Path, value, suffix: str | None = None) -> Path:
    if (not isinstance(value, str) or not value or "\\" in value or ":" in value
            or any(ord(c) < 32 for c in value)):
        _fail("unsafe_artifact_reference")
    path = PurePosixPath(value)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in value.split("/")):
        _fail("unsafe_artifact_reference")
    if suffix is not None and not value.endswith(suffix):
        _fail("unexpected_weight_reference_format")
    return root.joinpath(*path.parts)


def _exists(path: Path) -> bool:
    # lexists sees dangling symlinks; they must fail instead of selecting fallback.
    return os.path.lexists(path)


class _Reader:
    def __init__(self, roots):
        if not isinstance(roots, list) or not roots or len(roots) > 128:
            _fail("invalid_allowed_roots")
        self.roots = []
        for value in roots:
            if not isinstance(value, str) or not value or "\0" in value:
                _fail("invalid_allowed_root")
            path = Path(value)
            try:
                resolved = path.resolve(strict=True)
            except (OSError, RuntimeError, ValueError):
                _fail("allowed_root_unavailable")
            if not path.is_absolute() or path != resolved or not resolved.is_dir() or resolved == Path(resolved.anchor):
                _fail("unsafe_allowed_root")
            self.roots.append(resolved)
        self.bytes_read = 0
        self.files_read = 0
        self.symlink_files = 0

    def _resolve(self, source):
        if not isinstance(source, (str, Path)):
            _fail("invalid_artifact_path")
        source = Path(source)
        if not source.is_absolute():
            _fail("artifact_path_not_absolute")
        try:
            resolved = source.resolve(strict=True)
        except (OSError, RuntimeError, ValueError):
            _fail("artifact_path_unavailable")
        if not any(resolved.is_relative_to(root) for root in self.roots):
            _fail("artifact_symlink_escapes_mounts")
        return source, resolved

    def directory(self, source):
        _, resolved = self._resolve(source)
        if not resolved.is_dir():
            _fail("artifact_directory_required")
        return Path(source)

    def open(self, source):
        original, resolved = self._resolve(source)
        flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_BINARY", 0)
        fd = None
        directory_fd = None
        try:
            if os.name == "posix":
                if not hasattr(os, "O_NOFOLLOW") or not hasattr(os, "O_DIRECTORY"):
                    _fail("safe_open_unavailable")
                directory_flags = flags | os.O_DIRECTORY | os.O_NOFOLLOW
                directory_fd = os.open(resolved.anchor, directory_flags)
                for part in resolved.parts[1:-1]:
                    next_fd = os.open(part, directory_flags, dir_fd=directory_fd)
                    os.close(directory_fd)
                    directory_fd = next_fd
                fd = os.open(resolved.name, flags | os.O_NOFOLLOW, dir_fd=directory_fd)
            else:
                # Offline Windows tests only; production probe uses the POSIX path.
                fd = os.open(resolved, flags)
                if original.resolve(strict=True) != resolved:
                    _fail("artifact_changed_during_open")
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode):
                _fail("artifact_not_regular_file")
            if info.st_size <= 0:
                _fail("artifact_empty")
            handle = os.fdopen(fd, "rb")
            fd = None
            self.files_read += 1
            self.symlink_files += int(original != resolved)
            return handle, info
        except (OSError, RuntimeError, ValueError) as error:
            if isinstance(error, PreflightError):
                raise
            _fail("artifact_unreadable")
        finally:
            if fd is not None:
                os.close(fd)
            if directory_fd is not None:
                os.close(directory_fd)

    def read(self, handle, count):
        if count < 0 or self.bytes_read + count > MAX_TOTAL_BYTES:
            _fail("probe_read_budget_exceeded")
        raw = handle.read(count)
        if len(raw) != count:
            _fail("artifact_short_read")
        self.bytes_read += len(raw)
        return raw

    @staticmethod
    def unchanged(handle, before):
        after = os.fstat(handle.fileno())
        if ((before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
                != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)):
            _fail("artifact_changed_during_read")

    def json(self, path, limit=MAX_JSON_BYTES):
        handle, info = self.open(path)
        with handle:
            if info.st_size > limit:
                _fail("json_size_limit_exceeded")
            result = _json_bytes(self.read(handle, info.st_size))
            self.unchanged(handle, info)
        if not isinstance(result, dict) or not result:
            _fail("nonempty_json_object_required")
        return result

    def sample(self, path):
        handle, info = self.open(path)
        with handle:
            head = min(SAMPLE_BYTES, info.st_size)
            self.read(handle, head)
            tail = min(SAMPLE_BYTES, info.st_size - head)
            if tail:
                handle.seek(info.st_size - tail)
                self.read(handle, tail)
            self.unchanged(handle, info)
        return head + tail

    def safetensors(self, path):
        handle, info = self.open(path)
        with handle:
            if info.st_size < 10:
                _fail("invalid_safetensors_file")
            header_size = int.from_bytes(self.read(handle, 8), "little")
            if not 2 <= header_size <= MAX_HEADER_BYTES or header_size + 8 >= info.st_size:
                _fail("invalid_safetensors_header_size")
            header = _json_bytes(self.read(handle, header_size))
            if not isinstance(header, dict) or not header:
                _fail("invalid_safetensors_header")
            data_start = 8 + header_size
            data_size = info.st_size - data_start
            intervals = []
            for name, tensor in header.items():
                if name == "__metadata__":
                    if not isinstance(tensor, dict):
                        _fail("invalid_safetensors_metadata")
                    continue
                if not isinstance(tensor, dict) or not isinstance(tensor.get("dtype"), str):
                    _fail("invalid_safetensors_tensor")
                shape, offsets = tensor.get("shape"), tensor.get("data_offsets")
                if (not isinstance(shape, list) or any(type(x) is not int or x < 0 for x in shape)
                        or not isinstance(offsets, list) or len(offsets) != 2
                        or any(type(x) is not int for x in offsets)
                        or not 0 <= offsets[0] <= offsets[1] <= data_size):
                    _fail("invalid_safetensors_offsets")
                if offsets[1] > offsets[0]:
                    intervals.append(tuple(offsets))
            if not intervals or len(intervals) > MAX_INDEX_ENTRIES:
                _fail("invalid_safetensors_tensor_count")
            end = 0
            for start, stop in sorted(intervals):
                if start != end:
                    _fail("safetensors_data_gap_or_overlap")
                end = stop
            if end != data_size:
                _fail("safetensors_unindexed_data")
            count = min(SAMPLE_BYTES, data_size)
            handle.seek(data_start)
            self.read(handle, count)
            tail = min(SAMPLE_BYTES, data_size - count)
            if tail:
                handle.seek(info.st_size - tail)
                self.read(handle, tail)
            self.unchanged(handle, info)
        return header_size + 8, count + tail


def _tokenizer(reader, root):
    config = reader.json(root / "tokenizer_config.json")
    before = reader.bytes_read
    checked = set()
    for name in ("special_tokens_map.json", "added_tokens.json"):
        if _exists(root / name):
            reader.json(root / name)
            checked.add(root / name)
    for key in ("tokenizer_file", "vocab_file", "merges_file", "added_tokens_file", "special_tokens_map_file", "sentencepiece_model_file"):
        value = config.get(key)
        if value is not None:
            referenced = _reference(root, value)
            if referenced not in checked:
                if referenced.suffix == ".json":
                    reader.json(referenced)
                else:
                    reader.sample(referenced)
                checked.add(referenced)
    explicit = config.get("tokenizer_file")
    fast = _reference(root, explicit) if explicit is not None else root / "tokenizer.json"
    if _exists(fast):
        document = reader.json(fast)
        if not isinstance(document.get("model"), dict) or not document["model"]:
            _fail("invalid_tokenizer_model")
        checked.add(fast)
    else:
        # The audited GLM-DSA profile uses tokenizer JSON. Do not accept a
        # different tokenizer backend merely because some binary file exists.
        _fail("tokenizer_artifact_missing")
    return len(checked), reader.bytes_read - before


def _probe_artifacts(spec: dict) -> dict:
    """Implementation runs behind the public safe-error boundary."""
    if (not isinstance(spec, dict) or spec.get("schema") != SCHEMA
            or set(spec) - {"schema", "model_path", "tokenizer_path", "allowed_roots", "required_json", "load_format", "loader_profile"}
            or spec.get("loader_profile") != LOADER_PROFILE
            or spec.get("load_format", "auto") not in {"auto", "safetensors"}):
        _fail("invalid_probe_spec")
    reader = _Reader(spec.get("allowed_roots"))
    model = reader.directory(spec.get("model_path"))
    tokenizer = reader.directory(spec.get("tokenizer_path"))
    config = reader.json(model / "config.json")
    if (config.get("model_type") != "glm_moe_dsa"
            or config.get("architectures") != ["GlmMoeDsaForCausalLM"]
            or not isinstance(config.get("quantization_config"), dict)
            or config["quantization_config"].get("quant_method") != "fp8"):
        _fail("unsupported_model_loader_profile")
    if _exists(model / "generation_config.json"):
        reader.json(model / "generation_config.json")
    required = spec.get("required_json", [])
    if not isinstance(required, list) or len(required) > 16:
        _fail("invalid_required_json_list")
    for path in required:
        reader.json(path)
    tokenizer_files, tokenizer_bytes = _tokenizer(reader, tokenizer)
    index = model / "model.safetensors.index.json"
    if _exists(index):
        document = reader.json(index)
        mapping = document.get("weight_map")
        if (not isinstance(mapping, dict) or not 1 <= len(mapping) <= MAX_INDEX_ENTRIES
                or any(not isinstance(key, str) or not key for key in mapping)):
            _fail("invalid_weight_index")
        # The audited loader globs only the model root before index filtering.
        # A nested reference cannot be accepted as coverage of that loader.
        if any(not isinstance(value, str) or "/" in value for value in mapping.values()):
            _fail("weight_reference_not_root_glob_compatible")
        shards = sorted({_reference(model, value, ".safetensors") for value in mapping.values()})
        if not 1 <= len(shards) <= MAX_SHARDS:
            _fail("weight_shard_limit_exceeded")
        indexed = True
    else:
        _fail("audited_loader_profile_requires_index")
    header_bytes = data_bytes = 0
    for path in shards:
        header, data = reader.safetensors(path)
        header_bytes += header
        data_bytes += data
    return {
        "schema": SCHEMA, "result": "PASS", "cpu_only": True,
        "loader_profile": LOADER_PROFILE, "source_attestation_required": True,
        "model_config_json_valid": True, "tokenizer_config_json_valid": True,
        "required_json_files": len(required), "tokenizer_artifacts": tokenizer_files,
        "tokenizer_bytes_read": tokenizer_bytes, "indexed_weights": indexed,
        "weight_shards": len(shards), "weight_header_bytes_read": header_bytes,
        "weight_data_bytes_read": data_bytes, "total_bytes_read": reader.bytes_read,
        "files_read": reader.files_read, "symlink_files": reader.symlink_files,
        "scope": "bounded-readability-not-full-integrity-or-serving",
    }


def probe_artifacts(spec: dict) -> dict:
    """Return bounded path/content-free proof, or a fixed-code PreflightError."""
    try:
        return _probe_artifacts(spec)
    except PreflightError:
        raise
    except Exception:
        raise PreflightError("artifact_probe_failed") from None


def main(argv=None):
    args = list(sys.argv[1:] if argv is None else argv)
    try:
        if len(args) != 2 or args[0] != "--spec" or len(args[1]) > 65536:
            _fail("invalid_probe_arguments")
        # This check is defense in depth. Loader hooks run before Python;
        # the image and actual stopped-container gates must reject them first.
        hook_names = {"LD_PRELOAD", "LD_AUDIT", "LD_LIBRARY_PATH", "GCONV_PATH", "LOCPATH",
                      "PYTHONPATH", "PYTHONHOME", "PYTHONSTARTUP", "PYTHONUSERBASE", "ENV", "BASH_ENV"}
        if any(os.environ.get(name) for name in hook_names):
            _fail("executable_environment_hook_exposed")
        # Isolation prevents sitecustomize/plugins; runc plus explicit empty
        # device requests is enforced by the create request, not a GPU import.
        if any(os.path.exists(path) for path in ("/dev/nvidiactl", "/dev/nvidia0", "/dev/kfd")):
            _fail("gpu_device_exposed")
        proof = probe_artifacts(_json_bytes(args[1]))
        code = 0
    except PreflightError as error:
        proof = {"schema": SCHEMA, "result": "FAIL", "error": str(error)}
        code = 1
    except Exception:
        proof = {"schema": SCHEMA, "result": "FAIL", "error": "probe_failed"}
        code = 1
    print(json.dumps(proof, separators=(",", ":"), sort_keys=True))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
