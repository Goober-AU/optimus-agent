"""Offline capture contracts; these stubs do not prove GPU execution."""

from __future__ import annotations

import copy
import sys
import types
from dataclasses import dataclass

import pytest

from occ3_sglang import capture


@dataclass(frozen=True)
class Device:
    type: str = "cuda"
    index: int = 0


class Tensor:
    """Small list-backed tensor with copy/index operations recorded."""

    def __init__(self, data, *, name, events, dtype="float32", device=None):
        self.data = data
        self.name = name
        self.events = events
        self.dtype = dtype
        self.device = device or Device()

    @property
    def shape(self):
        def dims(data):
            if not isinstance(data, list):
                return ()
            return (len(data),) + (dims(data[0]) if data else ())
        return dims(self.data)

    @property
    def ndim(self):
        return len(self.shape)

    def numel(self):
        size = 1
        for dim in self.shape:
            size *= dim
        return size

    def is_floating_point(self):
        return self.dtype == "float32"

    def element_size(self):
        return 4

    def _new(self, data, device=None):
        return Tensor(data, name=self.name, events=self.events,
                      dtype=self.dtype, device=device or self.device)

    def __getitem__(self, index):
        self.events.append((self.name, "select"))
        if isinstance(index, Tensor):
            selected = [self.data[i] for i in index.data]
        elif isinstance(index, tuple):
            selected = self.data[index[0]][index[1]]
        else:
            selected = self.data[index]
        return self._new(copy.deepcopy(selected))

    def detach(self):
        self.events.append((self.name, "detach"))
        return self

    def clone(self):
        self.events.append((self.name, "clone"))
        return self._new(copy.deepcopy(self.data))

    def to(self, device):
        self.events.append((self.name, "to", device))
        return self._new(copy.deepcopy(self.data), Device(device))

    def tolist(self):
        self.events.append((self.name, "tolist"))
        return copy.deepcopy(self.data)


@pytest.fixture(autouse=True)
def runtime(monkeypatch):
    capture._captures.clear()
    capture._registered = False
    monkeypatch.setenv("OCC3_CAPTURE_ENABLED", "1")
    monkeypatch.delenv("OCC3_CAPTURE_DIR", raising=False)
    state = types.SimpleNamespace(
        events=[], saved=[], registrations=[], topology_calls=0,
        parallel=types.SimpleNamespace(tp_size=1, dp_size=1, pp_size=1, attn_cp_size=1),
        distributed_initialized=False, world_size=1, rank=0,
    )
    torch = types.ModuleType("torch")
    torch.Tensor = Tensor
    torch.int32, torch.int64 = "int32", "int64"
    def save(tensor, file):
        state.saved.append((tensor, file))
        file.write(b"synthetic tensor bytes")
    torch.save = save
    dist = types.ModuleType("torch.distributed")
    dist.is_available = lambda: True
    dist.is_initialized = lambda: state.distributed_initialized
    dist.get_world_size = lambda: state.world_size
    dist.get_rank = lambda: state.rank
    torch.distributed = dist
    monkeypatch.setitem(sys.modules, "torch", torch)
    monkeypatch.setitem(sys.modules, "torch.distributed", dist)

    for name in ("sglang", "sglang.srt", "sglang.srt.plugins"):
        module = types.ModuleType(name)
        module.__path__ = []
        monkeypatch.setitem(sys.modules, name, module)
    context = types.ModuleType("sglang.srt.runtime_context")
    def get_parallel():
        state.topology_calls += 1
        return state.parallel
    context.get_parallel = get_parallel
    monkeypatch.setitem(sys.modules, context.__name__, context)
    hooks = types.ModuleType("sglang.srt.plugins.hook_registry")
    hooks.HookType = types.SimpleNamespace(AROUND="around")
    hooks.HookRegistry = types.SimpleNamespace(
        register=lambda *args: state.registrations.append(args)
    )
    monkeypatch.setitem(sys.modules, hooks.__name__, hooks)
    yield state
    capture._captures.clear()
    capture._registered = False


def example(runtime, *, size=2, width=4, opt_indices=None):
    reqs = [types.SimpleNamespace(rid=f"req-{i}") for i in range(size)]
    for i in range(size) if opt_indices is None else opt_indices:
        capture.opt_in_request(reqs[i])
    batch = types.SimpleNamespace(
        reqs=reqs, spec_algorithm=types.SimpleNamespace(name="EAGLE"), device="cuda"
    )
    hidden = Tensor([[float(i * 10), float(i * 10 + 1)]
                     for i in range(size * width)], name="hidden", events=runtime.events)
    indices = Tensor([[i * width, i * width + 1] + [-1] * (width - 2)
                      for i in range(size)], name="indices", events=runtime.events,
                     dtype="int64")
    lengths = Tensor([2] * size, name="lengths", events=runtime.events, dtype="int32")
    result = types.SimpleNamespace(
        logits_output=types.SimpleNamespace(hidden_states=hidden),
        accept_index=indices, accept_lens=lengths,
        speculative_num_draft_tokens=width,
    )
    kwargs = dict(topk=1, num_draft_tokens=width, finalize_tree_path=True)
    return batch, result, kwargs


def observe(batch, result, kwargs):
    calls = []
    def original(*args, **kw):
        calls.append((args, kw))
        return result
    assert capture._capture_around(original, batch, **kwargs) is result
    assert calls == [((batch,), kwargs)]


def assert_blocked(batch, reason):
    for req in batch.reqs:
        entry = capture.get_capture(req.rid)
        assert entry["status"] == "blocked"
        assert entry["reason"] == reason
        assert "hidden_states" not in capture._captures[req.rid]
    assert capture.capture_count() == 0


def test_dormant_without_env(runtime, monkeypatch):
    monkeypatch.delenv("OCC3_CAPTURE_ENABLED")
    capture.register_capture_hook()
    assert capture._registered is False
    assert runtime.registrations == []
    batch, result, kwargs = example(runtime)
    observe(batch, result, kwargs)
    assert runtime.events == []
    assert capture._captures == {}


def test_armed_with_env_registers_once(runtime):
    capture.register_capture_hook()
    capture.register_capture_hook()
    assert capture._registered is True
    assert runtime.registrations == [(capture.TARGET, capture._capture_around, "around")]


class UntouchableResult:
    def __getattribute__(self, name):
        raise AssertionError("unrelated result was inspected")


@pytest.mark.parametrize("rid", ["normal-request", "occ3-send-channel", "occ3-capture-source"])
def test_no_consent_has_no_tensor_topology_or_disk_activity(runtime, monkeypatch, tmp_path, rid):
    monkeypatch.setenv("OCC3_CAPTURE_DIR", str(tmp_path / "must-not-exist"))
    batch = types.SimpleNamespace(reqs=[types.SimpleNamespace(rid=rid)])
    observe(batch, UntouchableResult(), {})
    assert runtime.events == []
    assert runtime.topology_calls == 0
    assert runtime.saved == []
    assert not (tmp_path / "must-not-exist").exists()
    assert capture._captures == {}


def test_consent_is_exact_request_not_rid_or_boolean(runtime):
    original = types.SimpleNamespace(rid="same-rid")
    capture.opt_in_request(original)
    other = types.SimpleNamespace(rid="same-rid")
    setattr(other, capture._CONSENT_ATTR, True)
    observe(types.SimpleNamespace(reqs=[other]), UntouchableResult(), {})
    assert runtime.events == []
    assert runtime.topology_calls == 0
    assert capture._captures == {}


def test_old_forward_batch_rids_shape_is_not_used(runtime):
    observe(types.SimpleNamespace(rids=["req-0"]), UntouchableResult(), {})
    assert capture._captures == {}
    assert runtime.events == []


def test_mixed_consent_rejects_before_tensor_inspection(runtime):
    batch, _, _ = example(runtime, opt_indices=[0])
    observe(batch, UntouchableResult(), {})
    assert capture.get_capture("req-0")["reason"] == "mixed_request_consent"
    assert capture.get_capture("req-1") is None
    assert runtime.events == []
    assert runtime.topology_calls == 0


@pytest.mark.parametrize("field", ["tp_size", "dp_size", "pp_size", "attn_cp_size"])
def test_unproven_parallel_topology_does_not_copy(runtime, field):
    setattr(runtime.parallel, field, 2)
    batch, result, kwargs = example(runtime)
    observe(batch, result, kwargs)
    assert_blocked(batch, "unsupported_topology")
    assert runtime.events == []


def test_unknown_topology_fails_closed(runtime):
    runtime.parallel = None
    batch, result, kwargs = example(runtime)
    observe(batch, result, kwargs)
    assert_blocked(batch, "unsupported_topology")
    assert runtime.events == []


@pytest.mark.parametrize("world_size,rank", [(8, 0), (1, 1)])
def test_distributed_world_mismatch_does_not_copy(runtime, world_size, rank):
    runtime.distributed_initialized = True
    runtime.world_size, runtime.rank = world_size, rank
    batch, result, kwargs = example(runtime)
    observe(batch, result, kwargs)
    assert_blocked(batch, "unsupported_topology")
    assert runtime.events == []


def test_chain_captures_each_request_without_padding_or_other_rows(runtime):
    batch, result, kwargs = example(runtime)
    observe(batch, result, kwargs)
    for i in range(2):
        entry = capture._captures[f"req-{i}"]
        start = i * 4
        assert entry["hidden_states"].data == result.logits_output.hidden_states.data[start:start + 2]
        assert entry["rids"] == [f"req-{i}"]
        assert entry["shape"] == [2, 2]
        assert entry["layout"] == "chain"
        assert entry["semantics"] == "verifier_accepted_observability_only"
        assert entry["capture_row_indices"] == [start, start + 1]
        assert "hidden_states" not in capture.get_capture(f"req-{i}")
    assert capture.capture_count() == 2
    assert runtime.saved == []


@pytest.mark.parametrize("compacted", [True, False])
def test_tree_and_compacted_layouts_have_different_safe_mappings(runtime, compacted):
    batch, result, kwargs = example(runtime)
    kwargs.update(topk=2, finalize_tree_path=compacted)
    result.accept_index.data = [[0, 2, -1, -1], [4, 7, 6, -1]]
    result.accept_lens.data = [2, 3]
    observe(batch, result, kwargs)
    for i, rows in enumerate(([0, 1], [4, 5, 6]) if compacted else ([0, 2], [4, 7, 6])):
        entry = capture._captures[f"req-{i}"]
        assert entry["hidden_states"].data == [result.logits_output.hidden_states.data[n] for n in rows]
        assert entry["capture_row_indices"] == list(rows)
        assert entry["layout"] == ("compacted" if compacted else "tree")


def test_keyword_batch_argument_and_eagle3(runtime):
    batch, result, kwargs = example(runtime)
    batch.spec_algorithm.name = "EAGLE3"
    assert capture._capture_around(lambda **kw: result, batch=batch, **kwargs) is result
    assert capture.capture_count() == 2


@pytest.mark.parametrize("algorithm", ["UNO", "STANDALONE", "FROZEN_KV_MTP", "unknown"])
def test_other_algorithms_do_not_copy(runtime, algorithm):
    batch, result, kwargs = example(runtime)
    batch.spec_algorithm.name = algorithm
    observe(batch, result, kwargs)
    assert_blocked(batch, "unsupported_algorithm")
    assert runtime.events == []


@pytest.mark.parametrize("key,value", [
    ("topk", None), ("topk", True), ("num_draft_tokens", 0),
    ("num_draft_tokens", 3), ("finalize_tree_path", None), ("finalize_tree_path", 1),
])
def test_unknown_layout_refuses_before_copy(runtime, key, value):
    batch, result, kwargs = example(runtime)
    kwargs[key] = value
    observe(batch, result, kwargs)
    assert_blocked(batch, "unsupported_layout")
    assert runtime.events == []


@pytest.mark.parametrize("bad", ["missing", "cpu", "device_mismatch", "hidden_shape", "length_shape", "index_dtype"])
def test_bad_tensor_metadata_refuses_before_copy(runtime, bad):
    batch, result, kwargs = example(runtime)
    if bad == "missing":
        result.logits_output.hidden_states = None
    elif bad == "cpu":
        result.logits_output.hidden_states.device = Device("cpu")
    elif bad == "device_mismatch":
        result.accept_index.device = Device("cuda", 1)
    elif bad == "hidden_shape":
        result.logits_output.hidden_states.data.pop()
    elif bad == "length_shape":
        result.accept_lens.data.pop()
    else:
        result.accept_index.dtype = "float32"
    observe(batch, result, kwargs)
    assert_blocked(batch, "unsupported_tensor_layout")
    assert runtime.events == []


@pytest.mark.parametrize("row,count", [
    ([0, -1, -1, -1], 2), ([0, 4, -1, -1], 2), ([0, 0, -1, -1], 2),
    ([0, 1, 2, -1], 2), ([0, 1, -1, -1], 0), ([0, 1, -1, -1], 5),
    ([0, 1, -1, -1], 1.5), ([0, 2, -1, -1], 2),
])
def test_invalid_acceptance_cannot_index_hidden_rows(runtime, row, count):
    batch, result, kwargs = example(runtime)
    result.accept_index.data[0] = row
    result.accept_lens.data[0] = count
    observe(batch, result, kwargs)
    assert_blocked(batch, "invalid_acceptance")
    assert not any(event[0] == "hidden" for event in runtime.events)


def test_bounds_reject_large_observation_before_hidden_copy(runtime, monkeypatch):
    monkeypatch.setattr(capture, "_MAX_CAPTURE_BYTES", 1)
    batch, result, kwargs = example(runtime)
    observe(batch, result, kwargs)
    assert_blocked(batch, "invalid_acceptance")
    assert not any(event[0] == "hidden" for event in runtime.events)


def test_bounds_reject_large_index_metadata_before_any_copy(runtime, monkeypatch):
    monkeypatch.setattr(capture, "_MAX_ACCEPT_INDICES", 1)
    batch, result, kwargs = example(runtime)
    observe(batch, result, kwargs)
    assert_blocked(batch, "unsupported_tensor_layout")
    assert runtime.events == []


def test_duplicate_request_ids_do_not_copy(runtime):
    batch, result, kwargs = example(runtime)
    batch.reqs[1].rid = batch.reqs[0].rid
    observe(batch, result, kwargs)
    assert_blocked(batch, "unsupported_request_batch")
    assert runtime.events == []


def test_snapshot_failure_preserves_result_and_explicit_blocked_status(runtime, monkeypatch):
    batch, result, kwargs = example(runtime)
    observe(batch, result, kwargs)
    def fail_clone(self):
        raise RuntimeError("synthetic snapshot failure")
    monkeypatch.setattr(Tensor, "clone", fail_clone)
    observe(batch, result, kwargs)
    assert_blocked(batch, "snapshot_failed")
    assert runtime.saved == []


def test_original_inference_exception_is_not_hidden(runtime):
    batch, _, kwargs = example(runtime)
    def original(*args, **kw):
        raise ValueError("synthetic inference failure")
    with pytest.raises(ValueError, match="synthetic inference failure"):
        capture._capture_around(original, batch, **kwargs)
    assert runtime.events == []
    assert capture._captures == {}


def test_opt_out_revokes_consent_and_drops_snapshot(runtime):
    batch, result, kwargs = example(runtime, size=1)
    observe(batch, result, kwargs)
    capture.opt_out_request(batch.reqs[0])
    runtime.events.clear()
    observe(batch, UntouchableResult(), {})
    assert capture.get_capture("req-0") is None
    assert runtime.events == []


def test_opt_in_requires_valid_request_id(runtime):
    with pytest.raises(ValueError):
        capture.opt_in_request(types.SimpleNamespace(rid=None))


def test_opted_dump_uses_hash_not_untrusted_request_path(runtime, monkeypatch, dump_fs):
    monkeypatch.setenv("OCC3_CAPTURE_DIR", "/research/dumps")
    batch, result, kwargs = example(runtime, size=1)
    batch.reqs[0].rid = "../../outside\\bad"
    observe(batch, result, kwargs)
    assert len(runtime.saved) == 1
    tensor, writer = runtime.saved[0]
    paths = dump_fs.files()
    assert len(paths) == 1 and paths[0].startswith("/research/dumps/occ3-capture-")
    assert "outside" not in paths[0] and paths[0].endswith(".pt")
    assert tensor.shape == (2, 2)
    assert capture.get_capture(batch.reqs[0].rid)["dump_status"] == "stored"
    assert not hasattr(writer, "name") and not hasattr(writer, "fileno")


def test_real_snapshot_entries_are_bounded(runtime):
    for i in range(capture._MAX_CAPTURES + 4):
        batch, result, kwargs = example(runtime, size=1)
        batch.reqs[0].rid = f"bounded-{i}"
        observe(batch, result, kwargs)
    assert capture.capture_count() == capture._MAX_CAPTURES
    assert len(capture._captures) == capture._MAX_CAPTURES
    assert capture.get_capture("bounded-0") is None
    assert capture.get_capture(f"bounded-{capture._MAX_CAPTURES + 3}")["status"] == "captured"


# The designated project interpreter is Windows. This POSIX model exercises the
# descriptor/permission/quota contract, NOT a real Linux filesystem or torch.save.
class DumpFilesystem:
    O_RDONLY, O_WRONLY, O_CREAT, O_EXCL = 1, 2, 4, 8
    O_NOFOLLOW, O_CLOEXEC, O_DIRECTORY, O_NONBLOCK = 16, 32, 64, 128

    def __init__(self):
        import stat
        self.stat_module = stat
        self.nodes, self.fds, self.opens, self.removed = {}, {}, [], []
        self.counter, self.next_fd = 0, 100
        self.locked, self.publish_calls = [], []
        self.fail_lock = self.fail_unlink = self.fail_publish = False
        self.add("/", kind="dir", mode=0o755, uid=0)
        self.add("/research", kind="dir", mode=0o700)
        self.add("/research/dumps", kind="dir", mode=0o700)

    def add(self, path, *, kind="file", mode=0o600, uid=1000, nlink=1, data=b"", size=None):
        self.counter += 1
        kinds = {"file": self.stat_module.S_IFREG, "dir": self.stat_module.S_IFDIR,
                 "symlink": self.stat_module.S_IFLNK, "fifo": self.stat_module.S_IFIFO}
        node = types.SimpleNamespace(kind=kind, mode=kinds[kind] | mode,
            uid=uid, nlink=nlink, ino=self.counter, data=bytearray(data), size=size)
        self.nodes[path] = node
        return node

    def path(self, path, dir_fd=None):
        import posixpath
        if path.startswith("/"):
            return path
        return posixpath.join(self.fds[dir_fd][1], path)

    def info(self, node):
        return types.SimpleNamespace(st_mode=node.mode, st_uid=node.uid,
            st_nlink=node.nlink, st_dev=1, st_ino=node.ino,
            st_size=len(node.data) if node.size is None else node.size)

    def open(self, path, flags, mode=0o777, *, dir_fd=None):
        path = self.path(path, dir_fd)
        self.opens.append((path, flags, mode))
        node = self.nodes.get(path)
        if flags & self.O_CREAT:
            if node is not None and flags & self.O_EXCL:
                raise FileExistsError("private existing name")
            if node is None:
                node = self.add(path, mode=mode)
        if node is None:
            raise FileNotFoundError("private missing name")
        if node.kind == "symlink" and flags & self.O_NOFOLLOW:
            raise OSError("private symlink target")
        if flags & self.O_DIRECTORY and node.kind != "dir":
            raise NotADirectoryError("private non-directory")
        self.next_fd += 1
        self.fds[self.next_fd] = (node, path)
        return self.next_fd

    def close(self, fd):
        assert fd in self.fds
        self.fds.pop(fd)

    def fstat(self, fd):
        return self.info(self.fds[fd][0])

    def stat(self, path, *, dir_fd, follow_symlinks):
        assert follow_symlinks is False
        resolved = self.path(path, dir_fd)
        if resolved not in self.nodes:
            raise FileNotFoundError("private missing name")
        return self.info(self.nodes[resolved])

    def mkdir(self, path, mode, *, dir_fd):
        resolved = self.path(path, dir_fd)
        if resolved in self.nodes:
            raise FileExistsError("private existing name")
        self.add(resolved, kind="dir", mode=mode)

    def scandir(self, fd):
        import posixpath
        parent = self.fds[fd][1]
        entries = []
        for path, node in list(self.nodes.items()):
            if path != parent and posixpath.dirname(path) == parent:
                def node_stat(*, follow_symlinks, node=node):
                    assert follow_symlinks is False
                    return self.info(node)
                entries.append(types.SimpleNamespace(name=posixpath.basename(path), stat=node_stat))
        class Scan:
            def __enter__(self):
                return iter(entries)
            def __exit__(self, *args):
                return False
        return Scan()

    def fdopen(self, fd, mode, buffering):
        import io
        assert mode == "wb" and buffering == 0
        node = self.fds[fd][0]
        owner = self
        class Raw(io.BytesIO):
            def fileno(self):
                return fd
            def flush(self):
                if not self.closed:
                    node.data = bytearray(self.getvalue())
            def close(self):
                if not self.closed:
                    self.flush()
                    super().close()
                    owner.close(fd)
        return Raw(node.data)

    def fsync(self, fd):
        assert fd in self.fds

    def unlink(self, path, *, dir_fd):
        if self.fail_unlink:
            raise OSError("private cleanup failure")
        path = self.path(path, dir_fd)
        self.nodes.pop(path)
        self.removed.append(path)

    def lock(self, fd):
        if self.fail_lock:
            raise capture._DumpError("blocked_busy")
        self.locked.append(fd)

    def publish(self, fd, temporary, final):
        self.publish_calls.append((temporary, final))
        src, dst = self.path(temporary, fd), self.path(final, fd)
        if dst in self.nodes:
            raise capture._DumpError("blocked_publish_conflict")
        if self.fail_publish:
            raise OSError("private publish failure")
        self.nodes[dst] = self.nodes.pop(src)

    def files(self):
        return [path for path, node in self.nodes.items() if node.kind == "file"]


@pytest.fixture
def dump_fs(monkeypatch):
    import threading
    fs = DumpFilesystem()
    monkeypatch.setattr(capture, "_dump_os", fs)
    monkeypatch.setattr(capture, "_dump_support", lambda: (1000, fs.lock, fs.publish))
    monkeypatch.setattr(capture, "_dump_lock", threading.Lock())
    return fs


def dump_once(runtime, *, directory="/research/dumps", rid="private-request"):
    entry = {"hidden_states": Tensor([[1.0, 2.0]], name="hidden", events=runtime.events)}
    capture._dump(directory, rid, entry)
    return entry


def test_dump_unsupported_platform_fails_before_files_or_serialization(runtime, monkeypatch, tmp_path):
    monkeypatch.setattr(capture, "sys", types.SimpleNamespace(platform="win32"))
    entry = dump_once(runtime, directory=str(tmp_path / "not-created"))
    assert entry["dump_status"] == "unsupported_platform"
    assert not runtime.saved and not (tmp_path / "not-created").exists()


def test_dump_exclusive_private_creation_and_closed_descriptors(runtime, dump_fs):
    dump_fs.nodes.pop("/research/dumps")
    entry = dump_once(runtime)
    assert entry["dump_status"] == "stored" and not dump_fs.fds
    assert dump_fs.nodes["/research/dumps"].mode & 0o777 == 0o700
    [path] = dump_fs.files()
    assert dump_fs.nodes[path].mode & 0o777 == 0o600
    assert dump_fs.nodes[path].nlink == 1 and dump_fs.nodes[path].uid == 1000
    created = [flags for name, flags, _ in dump_fs.opens if ".tmp" in name]
    assert len(created) == 1 and created[0] & dump_fs.O_EXCL
    assert created[0] & dump_fs.O_NOFOLLOW and created[0] & dump_fs.O_CLOEXEC
    assert len(dump_fs.locked) == 1 and len(dump_fs.publish_calls) == 1


@pytest.mark.parametrize("directory", ["relative", "/research/../dumps", "/research//dumps",
    "/research/./dumps", "/", "/research/absent/dumps", "/research/" + "x" * 256,
    "/research/dumps\x00private"])
def test_dump_invalid_destination_never_serializes(runtime, dump_fs, directory):
    assert dump_once(runtime, directory=directory)["dump_status"] == "blocked_destination"
    assert runtime.saved == [] and dump_fs.files() == [] and not dump_fs.fds


@pytest.mark.parametrize("fault", ["mode", "owner", "symlink", "ancestor_symlink", "ancestor_write", "ancestor_owner"])
def test_dump_rejects_untrusted_directory_without_permission_repair(runtime, dump_fs, fault):
    target = dump_fs.nodes["/research/dumps"]
    if fault == "mode":
        target.mode |= 0o077
    elif fault == "owner":
        target.uid = 2000
    elif fault == "symlink":
        dump_fs.add("/research/dumps", kind="symlink")
    elif fault == "ancestor_symlink":
        dump_fs.add("/research", kind="symlink")
    elif fault == "ancestor_write":
        dump_fs.nodes["/research"].mode |= 0o022
    else:
        dump_fs.nodes["/research"].uid = 2000
    before = {name: (node.mode, node.uid, node.ino) for name, node in dump_fs.nodes.items()}
    entry = dump_once(runtime)
    assert entry["dump_status"] in ("blocked_permissions", "blocked_destination", "failed")
    assert not runtime.saved and not dump_fs.fds
    assert before == {name: (node.mode, node.uid, node.ino) for name, node in dump_fs.nodes.items()}


@pytest.mark.parametrize("fault", ["symlink", "hardlink", "owner", "mode", "fifo", "directory", "oversize"])
def test_dump_inventory_rejects_unsafe_entries_and_never_deletes_them(runtime, dump_fs, fault):
    node = dump_fs.add("/research/dumps/unrelated", data=b"preserve")
    if fault in ("symlink", "fifo"):
        node = dump_fs.add("/research/dumps/unrelated", kind=fault)
    elif fault == "directory":
        node = dump_fs.add("/research/dumps/unrelated", kind="dir", mode=0o700)
    elif fault == "hardlink":
        node.nlink = 2
    elif fault == "owner":
        node.uid = 2000
    elif fault == "mode":
        node.mode |= 0o040
    else:
        node.size = capture._MAX_DUMP_FILE_BYTES + 1
    entry = dump_once(runtime)
    assert entry["dump_status"] in ("blocked_permissions", "blocked_quota")
    assert not runtime.saved and not dump_fs.removed and not dump_fs.fds
    assert dump_fs.nodes["/research/dumps/unrelated"] is node


def test_dump_count_and_total_bytes_reserved_before_serialization(runtime, dump_fs, monkeypatch):
    monkeypatch.setattr(capture, "_MAX_DUMP_FILES", 2)
    dump_fs.add("/research/dumps/one", data=b"1")
    dump_fs.add("/research/dumps/two", data=b"2")
    assert dump_once(runtime)["dump_status"] == "blocked_quota"
    assert not runtime.saved
    dump_fs.nodes.pop("/research/dumps/two")
    monkeypatch.setattr(capture, "_MAX_DUMP_TOTAL_BYTES", capture._MAX_DUMP_FILE_BYTES)
    assert dump_once(runtime)["dump_status"] == "blocked_quota"
    assert not runtime.saved and not dump_fs.removed


def test_dump_both_quotas_hold_across_repeated_calls(runtime, dump_fs, monkeypatch):
    monkeypatch.setattr(capture, "_MAX_DUMP_FILES", 2)
    monkeypatch.setattr(capture, "_MAX_DUMP_FILE_BYTES", 12)
    monkeypatch.setattr(capture, "_MAX_DUMP_TOTAL_BYTES", 24)
    def save(tensor, file):
        runtime.saved.append((tensor, file))
        file.write(b"a" * 12)
    monkeypatch.setattr(sys.modules["torch"], "save", save)
    assert dump_once(runtime)["dump_status"] == "stored"
    assert dump_once(runtime)["dump_status"] == "stored"
    assert dump_once(runtime)["dump_status"] == "blocked_quota"
    assert len(runtime.saved) == len(dump_fs.files()) == 2
    assert sum(len(dump_fs.nodes[p].data) for p in dump_fs.files()) == 24


@pytest.mark.parametrize("fault", ["write", "seek", "swallowed", "empty", "exception"])
def test_dump_serializer_is_bounded_and_own_temp_is_removed(runtime, dump_fs, monkeypatch, caplog, fault):
    monkeypatch.setattr(capture, "_MAX_DUMP_FILE_BYTES", 8)
    def save(tensor, file):
        if fault == "empty":
            return
        if fault == "exception":
            file.write(b"123")
            raise RuntimeError("SYNTHETIC-DUMP-PRIVATE-PATH-SECRET")
        if fault == "seek":
            file.seek(9)
        elif fault == "swallowed":
            try:
                file.write(b"012345678")
            except Exception:
                pass
        else:
            file.write(b"012345678")
    monkeypatch.setattr(sys.modules["torch"], "save", save)
    entry = dump_once(runtime)
    assert entry["dump_status"] in ("blocked_serialized_size", "failed_serialization", "failed")
    assert len(dump_fs.removed) == 1 and ".tmp" in dump_fs.removed[0]
    assert not dump_fs.files() and not dump_fs.publish_calls and not dump_fs.fds
    assert "SYNTHETIC-DUMP-PRIVATE-PATH-SECRET" not in str(entry.get("dump_status")) + caplog.text


def test_dump_bounded_writer_allows_in_limit_seek_without_size_reset(runtime, dump_fs, monkeypatch):
    monkeypatch.setattr(capture, "_MAX_DUMP_FILE_BYTES", 8)
    def save(tensor, file):
        assert file.write(b"12345678") == 8
        assert file.seek(-2, 2) == 6 and file.tell() == 6
        file.write(b"AB")
    monkeypatch.setattr(sys.modules["torch"], "save", save)
    assert dump_once(runtime)["dump_status"] == "stored"
    assert bytes(dump_fs.nodes[dump_fs.files()[0]].data) == b"123456AB"


def test_dump_serialization_changes_inventory_refuses_publish_and_only_removes_own_temp(runtime, dump_fs, monkeypatch):
    original = sys.modules["torch"].save
    def save(tensor, file):
        original(tensor, file)
        dump_fs.add("/research/dumps/unrelated", data=b"preserve")
    monkeypatch.setattr(sys.modules["torch"], "save", save)
    assert dump_once(runtime)["dump_status"] == "blocked_directory_changed"
    assert dump_fs.files() == ["/research/dumps/unrelated"]
    assert len(dump_fs.removed) == 1 and dump_fs.removed[0].endswith(".tmp")


def test_dump_failed_cleanup_retains_private_temp_and_reports_uncertainty(runtime, dump_fs, monkeypatch):
    dump_fs.fail_publish = dump_fs.fail_unlink = True
    entry = dump_once(runtime)
    assert entry["dump_status"] == "cleanup_unconfirmed"
    assert len(dump_fs.files()) == 1 and dump_fs.files()[0].endswith(".tmp")
    assert not dump_fs.removed and not dump_fs.fds


def test_dump_cleanup_never_unlinks_replacement_inode(runtime, dump_fs, monkeypatch):
    def save(tensor, file):
        temporary = [name for name in dump_fs.files() if name.endswith(".tmp")][0]
        dump_fs.add(temporary, data=b"unrelated replacement")
        raise RuntimeError("private serialization failure")
    monkeypatch.setattr(sys.modules["torch"], "save", save)
    assert dump_once(runtime)["dump_status"] == "cleanup_unconfirmed"
    assert not dump_fs.removed and len(dump_fs.files()) == 1
    assert bytes(dump_fs.nodes[dump_fs.files()[0]].data) == b"unrelated replacement"


def test_dump_publish_conflict_never_overwrites_or_deletes_unrelated_file(runtime, dump_fs, monkeypatch):
    def conflict(fd, temporary, final):
        target = dump_fs.path(final, fd)
        dump_fs.add(target, data=b"unrelated final")
        dump_fs.publish(fd, temporary, final)
    monkeypatch.setattr(capture, "_dump_support", lambda: (1000, dump_fs.lock, conflict))
    assert dump_once(runtime)["dump_status"] == "blocked_publish_conflict"
    assert len(dump_fs.files()) == 1 and bytes(dump_fs.nodes[dump_fs.files()[0]].data) == b"unrelated final"
    assert len(dump_fs.removed) == 1 and dump_fs.removed[0].endswith(".tmp")


def test_dump_directory_lock_conflict_has_no_serialization(runtime, dump_fs):
    dump_fs.fail_lock = True
    assert dump_once(runtime)["dump_status"] == "blocked_busy"
    assert not runtime.saved and not dump_fs.files() and not dump_fs.fds


def test_dump_concurrent_same_process_call_is_refused_without_quota_race(runtime, dump_fs, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    nested = []
    def save(tensor, file):
        with ThreadPoolExecutor(max_workers=1) as executor:
            nested.append(executor.submit(dump_once, runtime).result(timeout=1))
        file.write(b"private bytes")
    monkeypatch.setattr(sys.modules["torch"], "save", save)
    assert dump_once(runtime)["dump_status"] == "stored"
    assert nested[0]["dump_status"] == "blocked_busy"
    assert len(dump_fs.files()) == len(dump_fs.publish_calls) == 1
    assert not dump_fs.fds


def test_dump_metadata_contains_no_path_or_serialized_tensor(runtime, dump_fs):
    entry = dump_once(runtime, rid="../../SYNTHETIC-RID-SECRET")
    assert {key: value for key, value in entry.items() if key != "hidden_states"} == {"dump_status": "stored"}
    assert "SYNTHETIC-RID-SECRET" not in str(dump_fs.files())


@pytest.mark.parametrize("rid", ["", "x" * 4097, None])
def test_dump_request_id_bound_precedes_filesystem_and_serializer(runtime, dump_fs, rid):
    assert dump_once(runtime, rid=rid)["dump_status"] == "blocked_request_id"
    assert not dump_fs.opens and not runtime.saved


def test_dump_temp_hardlink_race_is_not_published_or_unlinked_as_owned(runtime, dump_fs, monkeypatch):
    original = sys.modules["torch"].save
    def save(tensor, file):
        original(tensor, file)
        [temporary] = dump_fs.files()
        dump_fs.nodes[temporary].nlink = 2
    monkeypatch.setattr(sys.modules["torch"], "save", save)
    assert dump_once(runtime)["dump_status"] == "cleanup_unconfirmed"
    assert not dump_fs.publish_calls and not dump_fs.removed
    assert len(dump_fs.files()) == 1 and dump_fs.files()[0].endswith(".tmp")


def test_dump_tp8_still_refuses_before_any_dump_support(runtime, monkeypatch):
    runtime.parallel.tp_size = 8
    monkeypatch.setenv("OCC3_CAPTURE_DIR", "/research/dumps")
    def forbidden():
        raise AssertionError("unsupported topology must not inspect dump filesystem")
    monkeypatch.setattr(capture, "_dump_support", forbidden)
    batch, result, kwargs = example(runtime, size=1)
    observe(batch, result, kwargs)
    assert_blocked(batch, "unsupported_topology")
    assert not runtime.saved and not runtime.events
