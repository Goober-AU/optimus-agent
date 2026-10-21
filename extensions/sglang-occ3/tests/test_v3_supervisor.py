"""Independent guardian decisions and real OS process death, with offline Docker."""
from __future__ import annotations

import copy
import os
from pathlib import Path
import subprocess
import sys
import time

import pytest

from test_v3_lifecycle import (CANDIDATE, CANDIDATE_IMAGE, ORIGINAL, IMAGE,
                               approval, assert_restored, authorized, prepared, swapped)
from deploy.v3 import lifecycle as life
from deploy.v3 import supervisor as sup


def tick(journal, api, **changes):
    now = time.monotonic()
    state = journal.read()
    args = {"child_code": None, "now": now, "begun": now - 0.01,
            "heartbeat": {"attempt_id": state["attempt_id"], "pid": state.get("controller_pid"),
                          "heartbeat": now, "status": "running"}, "api": api}
    args.update(changes)
    return sup.decide(journal, state, **args)


def test_zero_exit_without_all_gates_is_failure(tmp_path):
    api, journal, _ = authorized(tmp_path)
    assert tick(journal, api, child_code=0) == "controller_exit"
    assert tick(journal, api, child_code=-9) == "controller_exit"
    state = journal.read()
    state.update(phase="GATES_COMPLETE", restart_count=1,
                 gates={name: {"ok": True} for name in sup.REQUIRED_GATES})
    assert sup.complete(state)
    del state["gates"]["native_final"]
    assert not sup.complete(state)


def test_guardian_detects_deadline_hang_exit_and_unsanctioned_restart(tmp_path):
    api, journal, _ = authorized(tmp_path)
    assert tick(journal, api, begun=time.monotonic() - journal.read()["plan"]["deployment_timeout_s"] - 1) == "deployment_deadline"
    state = journal.read()
    assert tick(journal, api, heartbeat={"attempt_id": state["attempt_id"], "pid": None,
                                         "heartbeat": 0}) == "controller_unresponsive"
    swapped(api, journal)
    api.docs[CANDIDATE]["State"].update(Running=False, Status="exited", ExitCode=7)
    assert tick(journal, api) == "candidate_exit"
    api.docs[CANDIDATE]["State"].update(Running=True, Status="running", StartedAt="unplanned-epoch")
    assert tick(journal, api) == "candidate_unplanned_restart"


def test_single_restart_permit_is_bound_and_expires(tmp_path, monkeypatch):
    api, journal, services = authorized(tmp_path)
    swapped(api, journal)
    state = journal.read()
    journal.gate("live_before", {"ok": True})
    request = {"nonce": "fixture-nonce", "candidate_id": CANDIDATE, "epoch": state["candidate_epoch"]}
    journal.update(phase="RESTART_REQUESTED", restart_request=request)
    monkeypatch.setattr(sup, "process_identity", lambda pid: {"pid": pid, "start_ticks": "offline"})
    guardian = sup.Supervisor(api, journal, services=services, lock_root=tmp_path / "locks", fence_root=tmp_path / "fences")
    guardian.permit_restart(journal.read())
    assert journal.read()["restart_count"] == 1
    permit = life.read_json(journal.directory / "restart-permit.json")
    assert sup.restart_permitted(journal, journal.read(), time.monotonic())
    assert not sup.restart_permitted(journal, journal.read(), permit["expires_monotonic"] + 1)
    first = (journal.directory / "restart-permit.json").read_bytes()
    guardian.permit_restart(journal.read())
    assert (journal.directory / "restart-permit.json").read_bytes() == first
    journal.update(restart_done=True)
    assert not sup.restart_permitted(journal, journal.read(), time.monotonic())


@pytest.mark.parametrize("after_mutation", [False, True])
def test_actual_killed_process_triggers_same_guardian_recovery(tmp_path, monkeypatch, after_mutation):
    api, journal, services = prepared(tmp_path, rollback_timeout_s=3)
    monkeypatch.setattr(sup, "process_identity", lambda pid: {"pid": pid, "start_ticks": "offline"})
    handles = []
    def spawn_then_kill():
        # This is a real OS process, not a fake exit code. Docker remains offline.
        interpreter = getattr(sys, "_base_executable", sys.executable) if os.name == "nt" else sys.executable
        child = subprocess.Popen([interpreter, "-c", "import threading; threading.Event().wait(60)"],
                                 stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        handles.append(child)
        if after_mutation:
            swapped(api, journal)
        child.kill()
        child.wait(timeout=3)
        return child
    guardian = sup.Supervisor(api, journal, services=services, process_factory=spawn_then_kill,
                              lock_root=tmp_path / "locks", fence_root=tmp_path / "fences")
    try:
        assert guardian.run(approval(journal)) == 1
        assert handles[0].returncode != 0
        result = life.read_json(journal.directory / "supervisor-result.json")
        assert result["verified"] and result["reason"] == "controller_exit"
        assert result["detection_to_verified_s"] >= 0
        assert_restored(api, journal)
        if not after_mutation:
            assert result["result"] == "NOOP"
            assert not any(op in ("start", "stop", "rename") for op, _ in api.events)
    finally:
        for child in handles:
            if child.poll() is None:
                child.kill()
                child.wait(timeout=3)


def test_spawn_oserror_recovers_noop_without_original_mutation(tmp_path, monkeypatch):
    api, journal, services = prepared(tmp_path)
    monkeypatch.setattr(sup, "process_identity", lambda pid: {"pid": pid, "start_ticks": "offline"})
    def broken_spawn():
        raise OSError("synthetic failure")
    guardian = sup.Supervisor(api, journal, services=services, process_factory=broken_spawn,
                              lock_root=tmp_path / "locks", fence_root=tmp_path / "fences")
    assert guardian.run(approval(journal)) == 1
    assert journal.read()["phase"] == "NOOP"
    assert not any(op in ("start", "stop", "rename") for op, _ in api.events)


def test_stale_supervisor_invocation_refuses_without_recovery(tmp_path, monkeypatch):
    api, journal, services = authorized(tmp_path)
    monkeypatch.setattr(sup, "process_identity", lambda pid: {"pid": pid, "start_ticks": "offline"})
    guardian = sup.Supervisor(api, journal, services=services, lock_root=tmp_path / "locks", fence_root=tmp_path / "fences")
    with pytest.raises(life.Failure, match="stale"):
        guardian.run(approval(journal))
    assert not (journal.directory / "recovery-result.json").exists()
    assert not any(op in ("start", "stop", "rename") for op, _ in api.events)


def test_controller_guard_binds_process_epoch_nonce_and_lease(tmp_path, monkeypatch):
    api, journal, _ = authorized(tmp_path)
    identity = {"pid": 12345, "start_ticks": "old"}
    journal.update(supervision_nonce="offline-nonce")
    document = {"attempt_id": journal.read()["attempt_id"], "pid": 12345,
                "identity": identity, "heartbeat": time.monotonic(), "status": "watching",
                "controller_pid": os.getpid(), "nonce": "offline-nonce"}
    monkeypatch.setattr(life, "process_identity", lambda pid: identity)
    life.private_json(journal.directory / "supervisor.json", document)
    life.check_supervisor(journal)
    for field, bad in (("controller_pid", 99), ("nonce", "wrong"), ("heartbeat", 0), ("status", "recovering")):
        altered = dict(document, **{field: bad})
        life.private_json(journal.directory / "supervisor.json", altered)
        with pytest.raises(life.Failure):
            life.check_supervisor(journal)
    life.private_json(journal.directory / "supervisor.json", document)
    monkeypatch.setattr(life, "process_identity", lambda pid: {"pid": 12345, "start_ticks": "reused"})
    with pytest.raises(life.Failure):
        life.check_supervisor(journal)


def test_unavailable_validator_blocks_before_authorization_or_fence(tmp_path, monkeypatch):
    api, journal, services = prepared(tmp_path)
    services.admission_ok = False
    monkeypatch.setattr(sup, "process_identity", lambda pid: {"pid": pid, "start_ticks": "offline"})
    guardian = sup.Supervisor(api, journal, services=services, lock_root=tmp_path / "locks", fence_root=tmp_path / "fences")
    with pytest.raises(life.Failure, match="before authorization"):
        guardian.run(approval(journal))
    assert journal.read()["phase"] == "PREPARED"
    assert not journal.read()["authorization_consumed"]
    assert not (tmp_path / "fences" / (ORIGINAL + ".json")).exists()
    assert not any(op in ("start", "stop", "rename") for op, _ in api.events)


def test_persistent_fence_survives_actual_guardian_kill_and_refuses_other_attempt(tmp_path, monkeypatch):
    api, journal, services = prepared(tmp_path)
    fences = tmp_path / "fences"
    locks = tmp_path / "locks"
    locks.mkdir()
    child_script = (
        "import sys, threading; from pathlib import Path; "
        "from deploy.v3.lifecycle import Journal, FileLock; "
        "from deploy.v3.supervisor import claim_original_fence; "
        "j=Journal(sys.argv[1]); lock=FileLock(Path(sys.argv[3])/(j.read()['original_id']+'.lock')); "
        "lock.__enter__(); claim_original_fence(j,sys.argv[2],{'pid':0,'start_ticks':'offline'}); "
        "threading.Event().wait(60)"
    )
    # Windows venv python.exe is a redirector; pin its real base interpreter so kill targets the lock owner.
    interpreter = getattr(sys, "_base_executable", sys.executable) if os.name == "nt" else sys.executable
    child = subprocess.Popen([interpreter, "-c", child_script, str(journal.directory), str(fences), str(locks)],
                             stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    try:
        end = time.monotonic() + 5
        fence = fences / (ORIGINAL + ".json")
        while not fence.exists() and child.poll() is None and time.monotonic() < end:
            time.sleep(0.01)
        assert fence.exists(), child.stderr.read() if child.poll() is not None else b"missing fence"
        child.kill()
        child.wait(timeout=3)
        # The original guardian lock is released, but the durable attempt fence remains.
        with life.FileLock(locks / (ORIGINAL + ".lock")):
            pass
        other_dir = tmp_path / "other"
        other_dir.mkdir()
        other_api, other, other_services = prepared(other_dir, attempt_id="offline-other-attempt")
        monkeypatch.setattr(sup, "process_identity", lambda pid: {"pid": pid, "start_ticks": "offline"})
        guardian = sup.Supervisor(other_api, other, services=other_services,
                                  lock_root=locks, fence_root=fences)
        with pytest.raises(life.Failure, match="fenced"):
            guardian.run(approval(other))
        assert not other.read()["authorization_consumed"]
        assert not any(op in ("start", "stop", "rename") for op, _ in other_api.events)
        # Existing recovery is not blocked by the admission fence.
        assert life.recover(api, journal, services=services)["verified"]
        assert fence.exists()
    finally:
        if child.poll() is None:
            child.kill()
            child.wait(timeout=3)
        child.stderr.close()


def test_guardian_never_commits_complete_with_unsettled_secret_helper(tmp_path, monkeypatch):
    api, journal, services = prepared(tmp_path)
    monkeypatch.setattr(sup, "process_identity", lambda pid: {"pid": pid, "start_ticks": "offline"})
    class Finished:
        pid = 999
        def poll(self):
            return 0
    def finish_gates():
        swapped(api, journal)
        journal.update(phase="GATES_COMPLETE", restart_count=1,
            gates={name: {"ok": True} for name in sup.REQUIRED_GATES})
        return Finished()
    monkeypatch.setattr(sup, "reconcile_helpers", lambda *args: [{"removed": False}])
    monkeypatch.setattr(life, "reconcile_helpers", lambda *args: [{"removed": False}])
    guardian = sup.Supervisor(api, journal, services=services, process_factory=finish_gates,
        lock_root=tmp_path / "locks", fence_root=tmp_path / "fences")
    assert guardian.run(approval(journal)) == 2
    assert journal.read()["phase"] == "RESTORE_FAILED"
    result = life.read_json(journal.directory / "supervisor-result.json")
    assert result["service_restored"] is True and result["verified"] is False
    assert result["cleanup_complete"] is False
    assert_restored(api, journal)
    assert (tmp_path / "fences" / (ORIGINAL + ".json")).exists()


def test_guardian_requires_operation_settlement_even_without_pending_helper_files(tmp_path, monkeypatch):
    api, journal, services = prepared(tmp_path)
    monkeypatch.setattr(sup, "process_identity", lambda pid: {"pid": pid, "start_ticks": "offline"})
    class Finished:
        pid = 999
        def poll(self):
            return 0
    def finish_gates():
        swapped(api, journal)
        journal.update(phase="GATES_COMPLETE", restart_count=1,
            gates={name: {"ok": True} for name in sup.REQUIRED_GATES})
        return Finished()
    monkeypatch.setattr(sup, "reconcile_helpers", lambda *args: [])
    monkeypatch.setattr(sup, "reconcile_helper_operations", lambda *args: [{"settled": False}])
    monkeypatch.setattr(life, "reconcile_helper_operations", lambda *args: [{"settled": False}])
    guardian = sup.Supervisor(api, journal, services=services, process_factory=finish_gates,
        lock_root=tmp_path / "locks", fence_root=tmp_path / "fences")
    assert guardian.run(approval(journal)) == 2
    result = life.read_json(journal.directory / "supervisor-result.json")
    assert result["helper_cleanup"] == [] and not result["helper_operations_settled"]
    assert result["service_restored"] and not result["verified"]
    assert journal.read()["phase"] == "RESTORE_FAILED"
    assert_restored(api, journal)
