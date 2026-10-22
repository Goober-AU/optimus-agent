"""Dormant, request-scoped EAGLE hidden-state observability.

The existing OCC3_CAPTURE_ENABLED flag permits hook registration, not consent.
A trusted scheduler-side caller must call opt_in_request on each exact Req.
This is not a GenerateReqInput field and is not enabled by the prompt-KV API.
Only single-device EAGLE layouts audited against SGLang 0.5.21 are supported.
Captured rows are verifier-accepted observations, not committed output states
or a portable latent-transfer payload. Mixed-consent batches fail closed.
"""

from __future__ import annotations

import hashlib
import logging
import os
import stat
import sys
import threading
import time
import uuid
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

TARGET = "sglang.srt.speculative.eagle_worker_common.run_eagle_verify"

_MAX_CAPTURES = 8
_MAX_CAPTURE_BYTES = 8 * 1024 * 1024
_MAX_ACCEPT_INDICES = 4096
_MAX_DUMP_FILE_BYTES = 9 * 1024 * 1024
_MAX_DUMP_FILES = 16
_MAX_DUMP_TOTAL_BYTES = 64 * 1024 * 1024
_dump_lock = threading.Lock()
_dump_os = os  # Private filesystem seam for deterministic offline contract tests.
_CONSENT = object()
_CONSENT_ATTR = "_occ3_hidden_observability_consent"
_captures: Dict[str, Dict[str, Any]] = {}
_registered = False


def _enabled() -> bool:
    return os.environ.get("OCC3_CAPTURE_ENABLED", "").lower() in ("1", "true", "yes")


def opt_in_request(req: object) -> None:
    """Grant local observability consent to this scheduler Req, not its rid."""
    rid = getattr(req, "rid", None)
    if not isinstance(rid, str) or not rid:
        raise ValueError("capture opt-in requires a nonempty request id")
    setattr(req, _CONSENT_ATTR, _CONSENT)


def opt_out_request(req: object) -> None:
    """Revoke consent and remove this request's local observation."""
    setattr(req, _CONSENT_ATTR, None)
    _captures.pop(getattr(req, "rid", None), None)


def register_capture_hook() -> None:
    """Register once when the environment allows explicit request opt-in."""
    global _registered
    if _registered:
        return
    if not _enabled():
        logger.info("occ3-bridge: capture hook dormant (OCC3_CAPTURE_ENABLED unset)")
        return
    from sglang.srt.plugins.hook_registry import HookRegistry, HookType

    HookRegistry.register(TARGET, _capture_around, HookType.AROUND)
    _registered = True
    logger.info("occ3-bridge: request-scoped observability hook armed on %s", TARGET)


def _capture_around(original_fn, *args, **kwargs):
    result = original_fn(*args, **kwargs)
    try:
        _store(result, args, kwargs)
    except Exception:
        logger.error("occ3-bridge: capture observation failed")
    return result


def _remember(rid: str, entry: Dict[str, Any]) -> None:
    _captures.pop(rid, None)
    _captures[rid] = entry
    while len(_captures) > _MAX_CAPTURES:
        _captures.pop(next(iter(_captures)))


def _block(reqs, reason: str) -> None:
    for req in reqs:
        rid = getattr(req, "rid", None)
        if isinstance(rid, str) and rid:
            _remember(rid, {
                "status": "blocked",
                "reason": reason,
                "rids": [rid],
                "semantics": "verifier_accepted_observability_only",
            })


def _topology_ok() -> bool:
    """Fail closed unless runtime configuration proves a single device."""
    try:
        from sglang.srt.runtime_context import get_parallel
        import torch.distributed as dist

        parallel = get_parallel()
        for name in ("tp_size", "dp_size", "pp_size", "attn_cp_size"):
            value = getattr(parallel, name, None)
            if type(value) is not int or value != 1:
                return False
        if dist.is_available() and dist.is_initialized():
            return dist.get_world_size() == 1 and dist.get_rank() == 0
        return True
    except Exception:
        return False


def _store(result: Any, args: tuple, kwargs: Dict[str, Any]) -> None:
    if not _enabled():
        return
    batch = args[0] if args else kwargs.get("batch")
    reqs = getattr(batch, "reqs", None)
    if not isinstance(reqs, (list, tuple)) or not reqs:
        return
    opted = [r for r in reqs if getattr(r, _CONSENT_ATTR, None) is _CONSENT]
    if not opted:
        return
    if len(opted) != len(reqs):
        _block(opted, "mixed_request_consent")
        return

    # No tensor inspection or copy occurs before every request has opted in.
    rids = [getattr(r, "rid", None) for r in reqs]
    if (
        len(reqs) > _MAX_CAPTURES
        or any(not isinstance(rid, str) or not rid for rid in rids)
        or len(set(rids)) != len(rids)
    ):
        _block(opted, "unsupported_request_batch")
        return
    if not _topology_ok():
        _block(opted, "unsupported_topology")
        return
    if getattr(getattr(batch, "spec_algorithm", None), "name", None) not in (
        "EAGLE", "EAGLE3"
    ):
        _block(opted, "unsupported_algorithm")
        return

    topk = kwargs.get("topk")
    width = kwargs.get("num_draft_tokens")
    compacted = kwargs.get("finalize_tree_path")
    if (
        type(topk) is not int or topk < 1
        or type(width) is not int or width < 1
        or type(compacted) is not bool
        or getattr(result, "speculative_num_draft_tokens", None) != width
    ):
        _block(opted, "unsupported_layout")
        return
    layout = "chain" if topk == 1 else "compacted" if compacted else "tree"

    try:
        import torch

        hidden = getattr(getattr(result, "logits_output", None), "hidden_states", None)
        indices = getattr(result, "accept_index", None)
        lengths = getattr(result, "accept_lens", None)
        tensors = (hidden, indices, lengths)
        if not all(isinstance(t, torch.Tensor) for t in tensors):
            raise ValueError("missing tensors")
        if not all(t.device.type == "cuda" for t in tensors):
            raise ValueError("not CUDA observations")
        if not all(t.device == hidden.device for t in tensors):
            raise ValueError("different devices")
        if (
            hidden.ndim != 2 or hidden.shape[0] != len(reqs) * width
            or hidden.shape[1] <= 0 or not hidden.is_floating_point()
            or lengths.shape != (len(reqs),)
            or indices.ndim != 2 or indices.shape[0] != len(reqs)
            or not 1 <= indices.shape[1] <= width
            or indices.numel() > _MAX_ACCEPT_INDICES
            or indices.dtype not in (torch.int32, torch.int64)
            or lengths.dtype not in (torch.int32, torch.int64)
        ):
            raise ValueError("unknown tensor layout")
    except Exception:
        _block(opted, "unsupported_tensor_layout")
        return

    try:
        counts = lengths.detach().to("cpu").tolist()
        tree_indices = indices.detach().to("cpu").tolist()
        selections = []
        for i, (count, row) in enumerate(zip(counts, tree_indices)):
            if type(count) is not int or not 1 <= count <= indices.shape[1]:
                raise ValueError("invalid accepted length")
            valid = row[:count]
            start, end = i * width, (i + 1) * width
            if (
                any(type(n) is not int or not start <= n < end for n in valid)
                or len(set(valid)) != count
                or any(n != -1 for n in row[count:])
                or count * hidden.shape[1] * hidden.element_size() > _MAX_CAPTURE_BYTES
            ):
                raise ValueError("invalid accepted indices or observation bound")
            if layout == "chain" and valid != list(range(start, start + count)):
                raise ValueError("noncontiguous chain")
            selections.append(valid)
    except Exception:
        _block(opted, "invalid_acceptance")
        return

    entries = []
    try:
        for i, (rid, count, valid) in enumerate(zip(rids, counts, selections)):
            if layout == "tree":
                rows = hidden[indices[i, :count]]
                row_indices = valid
            else:
                start = i * width
                rows = hidden[start:start + count]
                row_indices = list(range(start, start + count))
            # Snapshot on the forward stream before a later CUDA graph replay.
            captured_cpu = rows.detach().clone().to("cpu")
            entries.append((rid, {
                "status": "captured",
                "semantics": "verifier_accepted_observability_only",
                "layout": layout,
                "hidden_states": captured_cpu,
                "accept_index": valid,
                "capture_row_indices": row_indices,
                "rids": [rid],
                "shape": list(captured_cpu.shape),
                "captured_at": time.time(),
            }))
    except Exception:
        _block(opted, "snapshot_failed")
        return
    for rid, entry in entries:
        _remember(rid, entry)
        dump_dir = os.environ.get("OCC3_CAPTURE_DIR", "")
        if dump_dir:
            _dump(dump_dir, rid, entry)


class _DumpError(Exception):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


class _BoundedDumpWriter:
    """Only expose bounded file-object operations to the serializer."""
    def __init__(self, raw, limit):
        self._raw, self.limit, self.high_water = raw, limit, 0
        self.failed = False

    def _refuse(self):
        self.failed = True
        raise _DumpError("blocked_serialized_size")

    def write(self, data):
        view = memoryview(data)
        if self.failed or not view.c_contiguous:
            self._refuse()
        view = view.cast("B")
        end = self._raw.tell() + view.nbytes
        if end > self.limit:
            self._refuse()
        count = self._raw.write(view)
        if count != view.nbytes:
            self.failed = True
            raise _DumpError("failed_serialization")
        self.high_water = max(self.high_water, end)
        return count

    def seek(self, offset, whence=0):
        if type(offset) is not int or type(whence) is not int or whence not in (0, 1, 2):
            self._refuse()
        base = (0, self._raw.tell(), self.high_water)[whence]
        target = base + offset
        if self.failed or not 0 <= target <= self.limit:
            self._refuse()
        return self._raw.seek(target)

    def tell(self):
        return self._raw.tell()

    def flush(self):
        self._raw.flush()


def _dump_support():
    """Linux fd-relative/no-replace semantics only; never weaken on Windows."""
    if sys.platform != "linux":
        raise _DumpError("unsupported_platform")
    required = ("O_NOFOLLOW", "O_DIRECTORY", "O_CLOEXEC", "O_NONBLOCK", "geteuid")
    if (any(not hasattr(os, name) for name in required)
            or not {os.open, os.mkdir, os.stat, os.unlink} <= os.supports_dir_fd
            or os.stat not in os.supports_follow_symlinks
            or os.scandir not in os.supports_fd):
        raise _DumpError("unsupported_platform")
    try:
        import ctypes
        import errno
        import fcntl
        libc = ctypes.CDLL(None, use_errno=True)
        rename = libc.renameat2
        rename.argtypes = (ctypes.c_int, ctypes.c_char_p, ctypes.c_int,
                           ctypes.c_char_p, ctypes.c_uint)
        rename.restype = ctypes.c_int
    except Exception:
        raise _DumpError("unsupported_platform") from None

    def lock_directory(fd):
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            code = "blocked_busy" if exc.errno in (errno.EAGAIN, errno.EWOULDBLOCK) else "unsupported_filesystem"
            raise _DumpError(code) from None

    def publish(fd, temporary, final):
        # RENAME_NOREPLACE: atomic publication must never replace another entry.
        if rename(fd, os.fsencode(temporary), fd, os.fsencode(final), 1) != 0:
            code = "blocked_publish_conflict" if ctypes.get_errno() == errno.EEXIST else "failed_publish"
            raise _DumpError(code)

    return os.geteuid(), lock_directory, publish


def _private_stat(info, uid, *, directory=False):
    kind = stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode)
    mode = 0o700 if directory else 0o600
    if (not kind or info.st_uid != uid or stat.S_IMODE(info.st_mode) != mode
            or (not directory and info.st_nlink != 1)):
        raise _DumpError("blocked_permissions")


def _ancestor_stat(info, uid):
    if (not stat.S_ISDIR(info.st_mode) or info.st_uid not in (0, uid)
            or (stat.S_IMODE(info.st_mode) & 0o022
                and not (info.st_uid == 0 and info.st_mode & stat.S_ISVTX))):
        raise _DumpError("blocked_destination")


def _open_dump_directory(path, uid):
    # Open every component without following links. Only the final directory may
    # be created. Do not chmod existing paths or create broad parent trees.
    if (not isinstance(path, str) or len(path) > 4096
            or not path.startswith("/") or "\x00" in path):
        raise _DumpError("blocked_destination")
    parts = path.rstrip("/").split("/")[1:]
    if (not parts or len(parts) > 128 or len(os.fsencode(path)) > 4096
            or any(part in ("", ".", "..") or len(os.fsencode(part)) > 255 for part in parts)):
        raise _DumpError("blocked_destination")
    flags = _dump_os.O_RDONLY | _dump_os.O_DIRECTORY | _dump_os.O_NOFOLLOW | _dump_os.O_CLOEXEC
    fd = _dump_os.open("/", flags)
    try:
        _ancestor_stat(_dump_os.fstat(fd), uid)
        for i, part in enumerate(parts):
            last = i == len(parts) - 1
            try:
                child = _dump_os.open(part, flags, dir_fd=fd)
            except FileNotFoundError:
                if not last:
                    raise _DumpError("blocked_destination") from None
                try:
                    _dump_os.mkdir(part, 0o700, dir_fd=fd)
                except FileExistsError:
                    pass  # One race-aware reopen; no retry loop or permission repair.
                child = _dump_os.open(part, flags, dir_fd=fd)
            _dump_os.close(fd)
            fd = child
            info = _dump_os.fstat(fd)
            _private_stat(info, uid, directory=True) if last else _ancestor_stat(info, uid)
        return fd
    except BaseException:
        _dump_os.close(fd)
        raise


def _dump_inventory(fd, uid, *, exclude=None):
    inventory = {}
    total = 0
    with _dump_os.scandir(fd) as entries:
        for entry in entries:
            if entry.name == exclude:
                continue
            if len(inventory) >= _MAX_DUMP_FILES:
                raise _DumpError("blocked_quota")
            listed = entry.stat(follow_symlinks=False)
            _private_stat(listed, uid)
            flags = _dump_os.O_RDONLY | _dump_os.O_NOFOLLOW | _dump_os.O_CLOEXEC | _dump_os.O_NONBLOCK
            item = _dump_os.open(entry.name, flags, dir_fd=fd)
            try:
                actual = _dump_os.fstat(item)
                _private_stat(actual, uid)
                if (actual.st_dev, actual.st_ino) != (listed.st_dev, listed.st_ino):
                    raise _DumpError("blocked_directory_changed")
                total += actual.st_size
                if (actual.st_size < 0 or actual.st_size > _MAX_DUMP_FILE_BYTES
                        or total > _MAX_DUMP_TOTAL_BYTES):
                    raise _DumpError("blocked_quota")
                inventory[entry.name] = (actual.st_dev, actual.st_ino, actual.st_size)
            finally:
                _dump_os.close(item)
    return inventory, total


def _remove_own_temp(fd, name, owned):
    """Never unlink a replacement inode, a published file or an unrelated entry."""
    try:
        current = _dump_os.stat(name, dir_fd=fd, follow_symlinks=False)
        if (current.st_dev, current.st_ino) != owned or current.st_nlink != 1:
            return False
        _dump_os.unlink(name, dir_fd=fd)
        return True
    except FileNotFoundError:
        return False  # Missing is not proof that our private temporary was retired.
    except Exception:
        return False


def _dump(dump_dir: str, rid: str, entry: Dict[str, Any]) -> None:
    """Optional Linux-only private, quota-bounded observability export.

    OS root and the effective owner remain trusted. All cooperating processes
    must use the directory lock; same-owner malicious filesystem mutation is
    outside POSIX permission isolation. No dump path or exception is returned.
    """
    if not _dump_lock.acquire(blocking=False):
        entry["dump_status"] = "blocked_busy"
        return
    directory = temporary_fd = None
    temporary = owned = None
    published = created = False
    entry["dump_status"] = "failed"
    try:
        if not isinstance(rid, str) or not 0 < len(rid) <= 4096:
            raise _DumpError("blocked_request_id")
        uid, lock_directory, publish = _dump_support()
        directory = _open_dump_directory(dump_dir, uid)
        lock_directory(directory)
        before, total = _dump_inventory(directory, uid)
        # Reserve the worst-case serialized size, not the smaller raw tensor size.
        if (len(before) + 1 > _MAX_DUMP_FILES
                or total + _MAX_DUMP_FILE_BYTES > _MAX_DUMP_TOTAL_BYTES):
            raise _DumpError("blocked_quota")
        token = uuid.uuid4().hex
        temporary = ".occ3-capture-" + token + ".tmp"
        final = "occ3-capture-" + hashlib.sha256(rid.encode("utf-8")).hexdigest() + "-" + token + ".pt"
        flags = _dump_os.O_WRONLY | _dump_os.O_CREAT | _dump_os.O_EXCL | _dump_os.O_NOFOLLOW | _dump_os.O_CLOEXEC
        temporary_fd = _dump_os.open(temporary, flags, 0o600, dir_fd=directory)
        created = True
        info = _dump_os.fstat(temporary_fd)
        owned = (info.st_dev, info.st_ino)
        _private_stat(info, uid)
        if info.st_size != 0:
            raise _DumpError("blocked_destination")
        import torch
        with _dump_os.fdopen(temporary_fd, "wb", buffering=0) as raw:
            temporary_fd = None  # fdopen owns this exact descriptor now.
            bounded = _BoundedDumpWriter(raw, _MAX_DUMP_FILE_BYTES)
            torch.save(entry["hidden_states"], bounded)
            bounded.flush()
            if bounded.failed or not 0 < bounded.high_water <= _MAX_DUMP_FILE_BYTES:
                raise _DumpError("failed_serialization")
            _dump_os.fsync(raw.fileno())
            info = _dump_os.fstat(raw.fileno())
            _private_stat(info, uid)
            if (info.st_dev, info.st_ino) != owned or info.st_size != bounded.high_water:
                raise _DumpError("blocked_directory_changed")
        after, _ = _dump_inventory(directory, uid, exclude=temporary)
        if after != before:
            raise _DumpError("blocked_directory_changed")
        info = _dump_os.stat(temporary, dir_fd=directory, follow_symlinks=False)
        _private_stat(info, uid)
        if (info.st_dev, info.st_ino) != owned:
            raise _DumpError("blocked_directory_changed")
        publish(directory, temporary, final)
        published = True
        _dump_os.fsync(directory)
        entry["dump_status"] = "stored"
    except _DumpError as exc:
        entry["dump_status"] = exc.code
    except Exception:
        entry["dump_status"] = "failed"
    finally:
        try:
            if temporary_fd is not None:
                try:
                    _dump_os.close(temporary_fd)
                except Exception:
                    entry["dump_status"] = "cleanup_unconfirmed"
            if created and not published:
                if owned is None or not _remove_own_temp(directory, temporary, owned):
                    entry["dump_status"] = "cleanup_unconfirmed"
            if directory is not None:
                try:
                    _dump_os.close(directory)  # Releases the directory lock.
                except Exception:
                    entry["dump_status"] = "cleanup_unconfirmed"
        finally:
            _dump_lock.release()


def get_capture(rid: str) -> Optional[Dict[str, Any]]:
    """Latest local observation or explicit blocked status; never return tensors."""
    entry = _captures.get(str(rid))
    if entry is None:
        return None
    return {key: value for key, value in entry.items() if key != "hidden_states"}


def capture_count() -> int:
    """Count retained snapshots, excluding blocked observations."""
    return sum(entry.get("status") == "captured" for entry in _captures.values())
