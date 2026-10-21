"""Verify and apply a bounded source overlay to a disposable derived-image tree.

Never import SGLang here. A manifest matches the exact attested source bytes;
normal source checkouts and running containers are not installation targets.
Multi-file application is not crash-atomic. An interrupted build must be
abandoned: a partly patched tree cannot pass verification as an original tree.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import tempfile
from dataclasses import dataclass
from typing import Any

SCHEMA = "optimus.sglang-occ3.source-patch.v1"
SOURCE_REVISION = "e00930c5489053f26d86b179cee0d087f846acbb"
SOURCE_VERSION = "0.5.21"
MAX_MANIFEST_BYTES = 8 * 1024 * 1024
MAX_FILE_BYTES = 2 * 1024 * 1024
MAX_TOTAL_BYTES = 32 * 1024 * 1024
MAX_FILES = 64
MAX_REPLACEMENTS = 128


class PatchError(Exception):
    """Only fixed non-sensitive error codes leave this module."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def _need(condition: bool, code: str) -> None:
    if not condition:
        raise PatchError(code)


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _keys(value: Any, required: set[str], code: str) -> dict[str, Any]:
    _need(isinstance(value, dict) and set(value) == required, code)
    return value


def _digest(value: Any) -> str:
    _need(isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) is not None,
          "invalid_digest")
    return value


def _relative(value: Any) -> str:
    _need(isinstance(value, str) and len(value) <= 180, "invalid_source_path")
    _need(re.fullmatch(r"[a-zA-Z0-9_/-]+[.]py", value) is not None,
          "invalid_source_path")
    parts = PurePosixPath(value).parts
    _need(parts and not value.startswith("/") and all(p not in {".", ".."} for p in parts),
          "invalid_source_path")
    _need(str(PurePosixPath(value)) == value, "invalid_source_path")
    return value


def _no_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        _need(key not in result, "duplicate_json_field")
        result[key] = value
    return result


def load_manifest(path: Path) -> dict[str, Any]:
    try:
        with path.open("rb") as handle:
            raw = handle.read(MAX_MANIFEST_BYTES + 1)
        _need(len(raw) <= MAX_MANIFEST_BYTES, "manifest_too_large")
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=_no_duplicates)
        _need(isinstance(value, dict), "invalid_manifest")
        return value
    except PatchError:
        raise
    except (OSError, ValueError, UnicodeError, RecursionError):
        raise PatchError("manifest_unreadable") from None


def _root(root: Path) -> Path:
    # Do not silently resolve a symlink supplied as the installation root.
    try:
        absolute = Path(os.path.abspath(root))
        for parent in [absolute, *absolute.parents]:
            _need(not parent.is_symlink(), "symlink_source_root")
        _need(absolute.is_dir(), "source_root_missing")
        return absolute
    except PatchError:
        raise
    except OSError:
        raise PatchError("source_root_unreadable") from None


def _target(root: Path, relative: str) -> Path:
    target = root.joinpath(*PurePosixPath(relative).parts)
    current = root
    try:
        for part in PurePosixPath(relative).parts:
            current = current / part
            _need(not current.is_symlink(), "symlink_source_path")
        _need(target.parent.is_dir(), "source_parent_missing")
        _need(target.resolve().is_relative_to(root), "source_path_escape")
        return target
    except PatchError:
        raise
    except OSError:
        raise PatchError("source_path_unreadable") from None


def _read(path: Path) -> tuple[bytes, int, tuple[int, int, int, int]]:
    fd: int | None = None
    try:
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0))
        before = os.fstat(fd)
        _need(stat.S_ISREG(before.st_mode), "source_not_regular")
        _need(before.st_nlink == 1, "source_hardlink_unsupported")
        _need(before.st_size <= MAX_FILE_BYTES, "source_too_large")
        with os.fdopen(fd, "rb") as handle:
            fd = None
            data = handle.read(MAX_FILE_BYTES + 1)
            after = os.fstat(handle.fileno())
        signature = lambda s: (s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns)
        _need(signature(before) == signature(after), "source_changed_during_read")
        _need(len(data) <= MAX_FILE_BYTES, "source_too_large")
        _need(stat.S_IMODE(before.st_mode) & 0o7000 == 0, "special_source_mode")
        return data, stat.S_IMODE(before.st_mode), signature(before)
    except PatchError:
        raise
    except OSError:
        raise PatchError("source_unreadable") from None
    finally:
        if fd is not None:
            os.close(fd)


@dataclass(frozen=True)
class FilePlan:
    relative: str
    path: Path
    original: bytes
    patched: bytes
    mode: int
    signature: tuple[int, int, int, int]


@dataclass(frozen=True)
class PatchPlan:
    root: Path
    patch_set: str
    manifest_sha256: str
    files: tuple[FilePlan, ...]


def prepare(root: Path, manifest: dict[str, Any]) -> PatchPlan:
    """Read and validate every original and every resulting byte before writes."""
    try:
        fields = _keys(manifest, {"schema", "source_revision", "source_version", "patch_set", "files"},
                       "invalid_manifest_fields")
        _need(fields["schema"] == SCHEMA, "unsupported_manifest_schema")
        _need(fields["source_revision"] == SOURCE_REVISION and fields["source_version"] == SOURCE_VERSION,
              "unsupported_source_revision")
        patch_set = fields["patch_set"]
        _need(isinstance(patch_set, str) and re.fullmatch(r"[a-z0-9][a-z0-9-]{0,63}", patch_set) is not None,
              "invalid_patch_set")
        files = fields["files"]
        _need(isinstance(files, list) and 1 <= len(files) <= MAX_FILES, "invalid_file_count")
        canonical = json.dumps(manifest, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("utf-8")
        _need(len(canonical) <= MAX_MANIFEST_BYTES, "manifest_too_large")
        base = _root(root)
        seen: set[str] = set()
        planned: list[FilePlan] = []
        total = 0
        for entry in files:
            e = _keys(entry, {"path", "before_sha256", "after_sha256", "replacements"}, "invalid_file_fields")
            relative = _relative(e["path"])
            _need(relative.casefold() not in seen, "duplicate_source_path")
            seen.add(relative.casefold())
            before_digest, after_digest = _digest(e["before_sha256"]), _digest(e["after_sha256"])
            _need(before_digest != after_digest, "unchanged_patch")
            replacements = e["replacements"]
            _need(isinstance(replacements, list) and 1 <= len(replacements) <= MAX_REPLACEMENTS,
                  "invalid_replacement_count")
            target = _target(base, relative)
            data, mode, signature = _read(target)
            _need(_sha(data) == before_digest, "original_digest_mismatch")
            text = data.decode("utf-8", errors="strict")
            _need(not text.startswith("\ufeff"), "source_bom_unsupported")
            for replacement in replacements:
                r = _keys(replacement, {"before", "after"}, "invalid_replacement_fields")
                old, new = r["before"], r["after"]
                _need(isinstance(old, str) and isinstance(new, str) and old != "" and old != new,
                      "invalid_replacement")
                _need("\x00" not in old and "\x00" not in new, "invalid_replacement")
                _need(text.count(old) == 1, "replacement_not_unique")
                text = text.replace(old, new, 1)
                _need(len(text) <= MAX_FILE_BYTES, "patched_source_too_large")
            patched = text.encode("utf-8")
            _need(len(patched) <= MAX_FILE_BYTES, "patched_source_too_large")
            _need(_sha(patched) == after_digest, "patched_digest_mismatch")
            # Syntactic validity only; no imports or code execution.
            compile(patched, relative, "exec", dont_inherit=True)
            total += len(data) + len(patched)
            _need(total <= MAX_TOTAL_BYTES, "source_set_too_large")
            planned.append(FilePlan(relative, target, data, patched, mode, signature))
        return PatchPlan(base, patch_set, _sha(canonical), tuple(planned))
    except PatchError:
        raise
    except (OSError, ValueError, TypeError, SyntaxError, UnicodeError, RecursionError):
        raise PatchError("patch_validation_failed") from None


def _check_current(plan: PatchPlan, item: FilePlan, expected: bytes, *, original: bool) -> None:
    _need(_target(plan.root, item.relative) == item.path, "source_path_changed")
    current, mode, signature = _read(item.path)
    _need(current == expected and mode == item.mode, "source_changed_before_write")
    if original:
        _need(signature == item.signature, "source_identity_changed_before_write")


def _stage(path: Path, data: bytes, mode: int) -> Path:
    name: str | None = None
    try:
        with tempfile.NamedTemporaryFile(prefix=".occ3-source-", suffix=".tmp", dir=path.parent, delete=False) as handle:
            name = handle.name
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(name, mode)
        return Path(name)
    except BaseException:
        if name is not None:
            try:
                Path(name).unlink(missing_ok=True)
            except OSError:
                pass
        raise


def _sync_parent(path: Path) -> None:
    if os.name != "posix":
        return
    fd = os.open(path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def proof(plan: PatchPlan, *, applied: bool) -> dict[str, Any]:
    return {"schema": SCHEMA, "source_revision": SOURCE_REVISION,
            "source_version": SOURCE_VERSION, "patch_set": plan.patch_set,
            "manifest_sha256": plan.manifest_sha256, "applied": applied,
            "runtime_imported": False, "gpu_proof": False,
            "files": [{"path": f.relative, "before_sha256": _sha(f.original),
                       "after_sha256": _sha(f.patched)} for f in plan.files]}


def apply(plan: PatchPlan) -> dict[str, Any]:
    """Apply only a prepared plan; a caught partial write is restored if safe.

    Use only in an exclusive disposable build tree. This is not a live
    updater and makes no atomicity claim against process/host termination.
    """
    staged: dict[Path, Path] = {}
    completed: list[FilePlan] = []
    try:
        for item in plan.files:
            _check_current(plan, item, item.original, original=True)
            staged[item.path] = _stage(item.path, item.patched, item.mode)
        for item in plan.files:
            _check_current(plan, item, item.original, original=True)
        for item in plan.files:
            _check_current(plan, item, item.original, original=True)
            os.replace(staged[item.path], item.path)
            completed.append(item)
            _sync_parent(item.path)
        for item in plan.files:
            _check_current(plan, item, item.patched, original=False)
        return proof(plan, applied=True)
    except BaseException as error:
        restore_failed = False
        for item in reversed(completed):
            backup: Path | None = None
            try:
                _check_current(plan, item, item.patched, original=False)
                backup = _stage(item.path, item.original, item.mode)
                os.replace(backup, item.path)
                _sync_parent(item.path)
            except BaseException:
                restore_failed = True
            finally:
                if backup is not None:
                    try:
                        backup.unlink(missing_ok=True)
                    except OSError:
                        restore_failed = True
        if restore_failed:
            raise PatchError("partial_apply_restore_failed_discard_tree") from None
        if isinstance(error, PatchError):
            raise error from None
        raise PatchError("apply_failed_originals_restored") from None
    finally:
        for temporary in staged.values():
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                # No success receipt may be used without a clean build tree.
                raise PatchError("temporary_cleanup_failed_discard_tree") from None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Pinned source overlay for disposable image builds only")
    parser.add_argument("action", choices=("verify", "apply"))
    parser.add_argument("--root", type=Path, required=True, help="explicit sglang/srt directory")
    parser.add_argument("--manifest", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        plan = prepare(args.root, load_manifest(args.manifest))
        result = apply(plan) if args.action == "apply" else proof(plan, applied=False)
        print(json.dumps({"ok": True, **result}, sort_keys=True))
        return 0
    except PatchError as error:
        print(json.dumps({"ok": False, "code": error.code}, sort_keys=True))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
