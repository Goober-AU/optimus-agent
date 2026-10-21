"""Concurrent-append regressions for the bash() orphan-process journal.

The journal is one shared JSONL file: a kernel's concurrent bash jobs append
from several threads, and several kernels append from separate processes. The
writer must add whole lines under that concurrency (nothing lost, nothing
interleaved), never rewrite existing bytes (append only, it does not repair
pre-existing corruption), and still fail closed when the journal cannot be
written.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

from rlm import bash

bash_module = sys.modules["rlm.bash"]

_SRC_DIR = str(Path(bash_module.__file__).resolve().parents[1])

# Child-process driver: appends records through the real writer from several
# threads, with the synthetic journal taken from the environment.
_DRIVER = """
import importlib
import sys
import threading


def main() -> None:
    base_pid, threads, records = int(sys.argv[1]), int(sys.argv[2]), int(sys.argv[3])
    module = importlib.import_module("rlm.bash")
    barrier = threading.Barrier(threads)
    failures = []

    def worker(index: int) -> None:
        barrier.wait()
        for i in range(records):
            pid = base_pid + index * records + i
            try:
                written = module._record_journal(pid, active=False)
            except Exception as error:  # noqa: BLE001 - reported to the parent test
                failures.append((pid, repr(error)))
            else:
                if not written:
                    failures.append((pid, "returned False"))

    workers = [threading.Thread(target=worker, args=(i,)) for i in range(threads)]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join()
    if failures:
        sys.stderr.write(repr(failures[:5]))
        raise SystemExit(3)


main()
"""

_JOIN_TIMEOUT = 30.0  # bounded joins: a stuck writer must fail, not hang the suite


def _read_lines(path: str) -> list[bytes]:
    with open(path, "rb") as f:
        data = f.read()
    lines = data.split(b"\n")
    if lines and lines[-1] == b"":
        lines.pop()
    return lines


def _join_bounded(threads: list[threading.Thread]) -> None:
    deadline = time.monotonic() + _JOIN_TIMEOUT
    for thread in threads:
        thread.join(max(0.0, deadline - time.monotonic()))
    for thread in threads:
        if thread.is_alive():
            raise AssertionError("journal writer thread did not finish")


class JournalAppendTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        self.journal = os.path.join(tmp, "journal.jsonl")

    def _record_env(self):
        return mock.patch.dict(
            os.environ,
            {
                "PRIME_AGENT_INTERNAL_ORPHAN_PROCESS_JOURNAL": self.journal,
                "PRIME_AGENT_KERNEL_OWNER_PID": str(os.getpid()),
            },
        )

    def test_concurrent_threads_append_whole_lines(self):
        threads, records = 8, 40
        failures: list[tuple[int, str]] = []
        with self._record_env():
            barrier = threading.Barrier(threads)

            def worker(index: int) -> None:
                barrier.wait()
                for i in range(records):
                    pid = 500000 + index * records + i
                    try:
                        written = bash_module._record_journal(pid, active=False)
                    except Exception as error:  # noqa: BLE001 - reported below
                        failures.append((pid, repr(error)))
                    else:
                        if not written:
                            failures.append((pid, "returned False"))

            workers = [threading.Thread(target=worker, args=(i,), daemon=True) for i in range(threads)]
            for worker in workers:
                worker.start()
            try:
                _join_bounded(workers)
            finally:
                for worker in workers:
                    if worker.is_alive():
                        # The writer is wedged: fail fast rather than leak threads.
                        self.fail("journal writer thread wedged")
        self.assertEqual(failures, [])
        lines = _read_lines(self.journal)
        self.assertEqual(len(lines), threads * records)
        pids = set()
        for line in lines:
            # Binary appends: the line ends with the record's own LF, never a
            # CRLF pair from a text-mode translation.
            self.assertFalse(line.endswith(b"\r"), line[:80])
            record = json.loads(line)
            self.assertEqual(record["version"], 1)
            self.assertFalse(record["active"])
            self.assertEqual(record["ownerPid"], os.getpid())
            pids.add(record["pid"])
        self.assertEqual(len(pids), threads * records)

    def test_concurrent_processes_append_whole_lines(self):
        driver = os.path.join(os.path.dirname(self.journal), "driver.py")
        with open(driver, "w", encoding="utf-8") as f:
            f.write(_DRIVER)
        # A live test kernel exports its own journal/owner role; the children
        # must use the synthetic journal only, never the inherited one.
        env = {
            key: value
            for key, value in os.environ.items()
            if not key.startswith("PRIME_AGENT_INTERNAL_")
        }
        env["PRIME_AGENT_INTERNAL_ORPHAN_PROCESS_JOURNAL"] = self.journal
        env["PRIME_AGENT_KERNEL_OWNER_PID"] = str(os.getpid())
        env["PYTHONPATH"] = _SRC_DIR + os.pathsep + env.get("PYTHONPATH", "")
        creationflags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        processes, threads, records = 4, 3, 20
        procs = []
        self.addCleanup(self._terminate_all, procs)
        for p in range(processes):
            base = 700000 + p * threads * records
            procs.append(
                subprocess.Popen(
                    [sys.executable, driver, str(base), str(threads), str(records)],
                    env=env,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    creationflags=creationflags,
                )
            )
        for proc in procs:
            try:
                _, err = proc.communicate(timeout=120)
            except subprocess.TimeoutExpired:
                self.fail("journal child process timed out")
            self.assertEqual(proc.returncode, 0, err.decode("utf-8", "replace"))
        lines = _read_lines(self.journal)
        self.assertEqual(len(lines), processes * threads * records)
        pids = set()
        for line in lines:
            self.assertFalse(line.endswith(b"\r"), line[:80])
            pids.add(json.loads(line)["pid"])
        self.assertEqual(len(pids), processes * threads * records)

    @staticmethod
    def _terminate_all(procs: list[subprocess.Popen]) -> None:
        for proc in procs:
            if proc.poll() is None:
                proc.kill()
        for proc in procs:
            try:
                proc.communicate(timeout=10)
            except (subprocess.TimeoutExpired, OSError, ValueError):
                pass

    def test_append_preserves_existing_bytes(self):
        # Appends never rewrite history: a legacy truncated fragment stays
        # byte for byte (append does not repair existing corruption), and the
        # new record lands after it as one complete line.
        seed = (
            b'{"version": 1, "pid": 111, "active": false}\n'
            b'{"version": 1, "pid": 112, "active": false}\n'
            b'{"version": 1, "pid": 113, "acti'
        )
        with open(self.journal, "wb") as f:
            f.write(seed)
        with self._record_env():
            self.assertTrue(bash_module._record_journal(990001, active=False))
        with open(self.journal, "rb") as f:
            data = f.read()
        self.assertTrue(data.startswith(seed))
        record = json.loads(data[len(seed):])
        self.assertEqual(record["pid"], 990001)

    def test_unwritable_journal_fails_closed(self):
        with mock.patch.dict(
            os.environ,
            {
                "PRIME_AGENT_INTERNAL_ORPHAN_PROCESS_JOURNAL": os.path.dirname(self.journal),
                "PRIME_AGENT_KERNEL_OWNER_PID": str(os.getpid()),
            },
        ):
            self.assertFalse(bash_module._record_journal(os.getpid(), active=False))

    def test_windows_conversion_failure_closes_raw_handle(self):
        for failure in (OSError("conversion failed"), -1):
            with self.subTest(failure=failure):
                converter = mock.Mock()
                if isinstance(failure, Exception):
                    converter.open_osfhandle.side_effect = failure
                else:
                    converter.open_osfhandle.return_value = failure
                with (
                    mock.patch.object(bash_module, "_IS_POSIX", False),
                    mock.patch.dict(sys.modules, {"msvcrt": converter}),
                    mock.patch.object(bash_module._winjob, "create_file_append", return_value=123),
                    mock.patch.object(bash_module._winjob, "close") as close,
                    mock.patch.object(os, "O_BINARY", 0x8000, create=True),
                ):
                    with self.assertRaises(OSError):
                        bash_module._open_journal_append(self.journal)
                    close.assert_called_once_with(123)

    def test_windows_open_failure_does_not_transfer_invalid_handle(self):
        for handle in (None, False):
            with self.subTest(handle=handle):
                converter = mock.Mock()
                with (
                    mock.patch.object(bash_module, "_IS_POSIX", False),
                    mock.patch.dict(sys.modules, {"msvcrt": converter}),
                    mock.patch.object(bash_module._winjob, "create_file_append", return_value=handle),
                ):
                    with self.assertRaises(OSError):
                        bash_module._open_journal_append(self.journal)
                    converter.open_osfhandle.assert_not_called()

    def test_journal_handles_close_behind_writes(self):
        # A leaked append handle would keep the journal file locked open.
        with self._record_env():
            for i in range(25):
                self.assertTrue(bash_module._record_journal(990100 + i, active=False))
        os.remove(self.journal)
        self.assertFalse(os.path.exists(self.journal))


if __name__ == "__main__":
    unittest.main()
