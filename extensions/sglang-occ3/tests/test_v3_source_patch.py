from __future__ import annotations

import copy
import hashlib
import json
import os
from pathlib import Path

import pytest

from deploy.v3 import source_patch as patch


def sha(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def entry(path: str, before: str, after: str) -> dict:
    return {"path": path, "before_sha256": sha(before.encode()), "after_sha256": sha(after.encode()),
            "replacements": [{"before": before, "after": after}]}


def manifest(files: list[dict]) -> dict:
    return {"schema": patch.SCHEMA, "source_revision": patch.SOURCE_REVISION,
            "source_version": patch.SOURCE_VERSION, "patch_set": "offline-fixture", "files": files}


@pytest.fixture
def tree(tmp_path):
    base = tmp_path / "srt"
    (base / "models").mkdir(parents=True)
    (base / "models/first.py").write_bytes(b"VALUE = 1\n")
    (base / "models/second.py").write_bytes(b"VALUE = 2\n")
    m = manifest([entry("models/first.py", "VALUE = 1\n", "VALUE = 3\n"),
                  entry("models/second.py", "VALUE = 2\n", "VALUE = 4\n")])
    return base, m


def test_verify_does_not_write_or_import(tree, monkeypatch):
    base, m = tree
    m["files"][0] = entry("models/first.py", "VALUE = 1\n", "raise RuntimeError('would_execute')\n")
    p = patch.prepare(base, m)
    receipt = patch.proof(p, applied=False)
    assert receipt["applied"] is False and receipt["runtime_imported"] is False
    assert receipt["gpu_proof"] is False and len(receipt["files"]) == 2
    assert (base / "models/first.py").read_bytes() == b"VALUE = 1\n"
    assert list(base.rglob(".occ3-source-*")) == []


def test_exact_apply_and_second_apply_refused(tree):
    base, m = tree
    result = patch.apply(patch.prepare(base, m))
    assert result["applied"] is True
    assert (base / "models/first.py").read_bytes() == b"VALUE = 3\n"
    assert (base / "models/second.py").read_bytes() == b"VALUE = 4\n"
    with pytest.raises(patch.PatchError, match="original_digest_mismatch"):
        patch.prepare(base, m)
    assert list(base.rglob(".occ3-source-*")) == []


@pytest.mark.parametrize("field,value,code", [
    ("schema", "other", "unsupported_manifest_schema"),
    ("source_revision", "0" * 40, "unsupported_source_revision"),
    ("source_version", "0.5.22", "unsupported_source_revision"),
    ("patch_set", "../escape", "invalid_patch_set"),
    ("files", [], "invalid_file_count"),
])
def test_invalid_top_level(tree, field, value, code):
    base, m = tree
    m[field] = value
    with pytest.raises(patch.PatchError, match=code):
        patch.prepare(base, m)


@pytest.mark.parametrize("path", ["../first.py", "/first.py", "C:/first.py", "models//first.py", "models/./first.py",
                                  "models/../first.py", "models\\first.py", "models/first.json"])
def test_path_rejections(tree, path):
    base, m = tree
    m["files"][0]["path"] = path
    with pytest.raises(patch.PatchError, match="invalid_source_path"):
        patch.prepare(base, m)


def test_duplicate_file_and_fields_rejected(tree, tmp_path):
    base, m = tree
    m["files"][1] = copy.deepcopy(m["files"][0])
    with pytest.raises(patch.PatchError, match="duplicate_source_path"):
        patch.prepare(base, m)
    source = tmp_path / "manifest.json"
    source.write_text('{"schema":"a","schema":"b"}', encoding="utf-8")
    with pytest.raises(patch.PatchError, match="duplicate_json_field"):
        patch.load_manifest(source)


def test_no_partial_apply_when_later_original_mismatches(tree):
    base, m = tree
    (base / "models/second.py").write_bytes(b"DIFFERENT = 2\n")
    with pytest.raises(patch.PatchError, match="original_digest_mismatch"):
        patch.prepare(base, m)
    assert (base / "models/first.py").read_bytes() == b"VALUE = 1\n"


def test_wrong_after_hash_and_invalid_syntax_refuse(tree):
    base, m = tree
    m["files"][0]["after_sha256"] = "f" * 64
    with pytest.raises(patch.PatchError, match="patched_digest_mismatch"):
        patch.prepare(base, m)
    m["files"][0] = entry("models/first.py", "VALUE = 1\n", "def invalid(\n")
    with pytest.raises(patch.PatchError, match="patch_validation_failed"):
        patch.prepare(base, m)


def test_exact_bytes_no_newline_normalization(tree):
    base, m = tree
    (base / "models/first.py").write_bytes(b"VALUE = 1\r\n")
    with pytest.raises(patch.PatchError, match="original_digest_mismatch"):
        patch.prepare(base, m)


@pytest.mark.parametrize("old,new", [("", "x"), ("missing", "x"), ("VALUE", "VALUE"), ("VALUE", "\x00")])
def test_invalid_replacements(tree, old, new):
    base, m = tree
    m["files"][0]["replacements"] = [{"before": old, "after": new}]
    with pytest.raises(patch.PatchError):
        patch.prepare(base, m)


def test_nonunique_anchor_rejected(tree):
    base, m = tree
    raw = "VALUE = 1\nVALUE = 1\n"
    (base / "models/first.py").write_bytes(raw.encode("utf-8"))
    m["files"][0] = entry("models/first.py", raw, "VALUE = 3\n")
    m["files"][0]["replacements"] = [{"before": "VALUE = 1", "after": "VALUE = 3"}]
    with pytest.raises(patch.PatchError, match="replacement_not_unique"):
        patch.prepare(base, m)


def test_symlink_file_refused(tree, tmp_path):
    base, m = tree
    outside = tmp_path / "outside.py"
    outside.write_bytes(b"VALUE = 1\n")
    target = base / "models/first.py"
    target.unlink()
    try:
        target.symlink_to(outside)
    except OSError:
        pytest.skip("symlink privilege unavailable on this host")
    with pytest.raises(patch.PatchError, match="symlink_source_path"):
        patch.prepare(base, m)
    assert outside.read_bytes() == b"VALUE = 1\n"


def test_hardlink_refused(tree, tmp_path):
    base, m = tree
    try:
        os.link(base / "models/first.py", tmp_path / "linked.py")
    except OSError:
        pytest.skip("hardlink unavailable")
    with pytest.raises(patch.PatchError, match="source_hardlink_unsupported"):
        patch.prepare(base, m)


def test_mutation_between_prepare_and_apply_refused(tree):
    base, m = tree
    p = patch.prepare(base, m)
    (base / "models/second.py").write_bytes(b"OTHER = 2\n")
    with pytest.raises(patch.PatchError, match="source_changed_before_write"):
        patch.apply(p)
    assert (base / "models/first.py").read_bytes() == b"VALUE = 1\n"
    assert (base / "models/second.py").read_bytes() == b"OTHER = 2\n"
    assert list(base.rglob(".occ3-source-*")) == []


def test_same_bytes_replaced_inode_refused(tree):
    base, m = tree
    p = patch.prepare(base, m)
    temp = base / "models/replace.py"
    temp.write_bytes(b"VALUE = 1\n")
    os.replace(temp, base / "models/first.py")
    with pytest.raises(patch.PatchError, match="source_identity_changed_before_write"):
        patch.apply(p)


def test_caught_midwrite_failure_restores_originals(tree, monkeypatch):
    base, m = tree
    p = patch.prepare(base, m)
    real_replace = patch.os.replace
    count = 0

    def fail_second(src, dst):
        nonlocal count
        count += 1
        if count == 2:
            raise OSError("SYNTHETIC_PRIVATE_EXCEPTION")
        return real_replace(src, dst)

    monkeypatch.setattr(patch.os, "replace", fail_second)
    with pytest.raises(patch.PatchError, match="apply_failed_originals_restored") as exc:
        patch.apply(p)
    assert "SYNTHETIC_PRIVATE_EXCEPTION" not in str(exc.value)
    assert (base / "models/first.py").read_bytes() == b"VALUE = 1\n"
    assert (base / "models/second.py").read_bytes() == b"VALUE = 2\n"
    assert list(base.rglob(".occ3-source-*")) == []


def test_does_not_overwrite_concurrent_edit_during_rollback(tree, monkeypatch):
    base, m = tree
    p = patch.prepare(base, m)
    real_replace = patch.os.replace
    count = 0

    def fail_after_external_change(src, dst):
        nonlocal count
        count += 1
        if count == 2:
            (base / "models/first.py").write_bytes(b"CONCURRENT = 5\n")
            raise OSError("private")
        return real_replace(src, dst)

    monkeypatch.setattr(patch.os, "replace", fail_after_external_change)
    with pytest.raises(patch.PatchError, match="partial_apply_restore_failed_discard_tree"):
        patch.apply(p)
    assert (base / "models/first.py").read_bytes() == b"CONCURRENT = 5\n"


def test_size_bounds(tree, monkeypatch):
    base, m = tree
    monkeypatch.setattr(patch, "MAX_FILE_BYTES", 5)
    with pytest.raises(patch.PatchError, match="source_too_large"):
        patch.prepare(base, m)


def test_unknown_fields_rejected(tree):
    base, m = tree
    m["allow_upgrade"] = True
    with pytest.raises(patch.PatchError, match="invalid_manifest_fields"):
        patch.prepare(base, m)


def test_cli_output_safe_and_verify_no_write(tree, tmp_path, capsys):
    base, m = tree
    file = tmp_path / "manifest.json"
    file.write_text(json.dumps(m), encoding="utf-8")
    assert patch.main(["verify", "--root", str(base), "--manifest", str(file)]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["ok"] is True and result["applied"] is False
    m["files"][0]["before_sha256"] = "0" * 64
    file.write_text(json.dumps(m), encoding="utf-8")
    assert patch.main(["apply", "--root", str(base), "--manifest", str(file)]) == 1
    result = json.loads(capsys.readouterr().out)
    assert result == {"ok": False, "code": "original_digest_mismatch"}
    assert (base / "models/first.py").read_bytes() == b"VALUE = 1\n"
