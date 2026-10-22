"""Offline rehearsal-plan and cleanup regressions. Real Docker runs are separate."""
from __future__ import annotations

import copy
import json
from pathlib import Path
import sys

import pytest

from test_v3_lifecycle import FakeDocker, ORIGINAL, IMAGE, CANDIDATE_IMAGE
from deploy.v3 import lifecycle as life
from deploy.v3 import rehearsal as rehearsal


def test_disposable_request_never_exposes_gpu_model_mount_or_production_names():
    body = rehearsal.original_request(IMAGE, "a" * 16, 41999)
    assert body["HostConfig"]["Runtime"] == "runc"
    assert body["HostConfig"]["DeviceRequests"] == []
    assert not body["HostConfig"].get("Mounts")
    assert not body["HostConfig"].get("Binds")
    assert body["HostConfig"]["PortBindings"]["8081/tcp"][0]["HostIp"] == "127.0.0.1"
    assert body["Labels"][life.REHEARSAL_LABEL] == "a" * 16
    source = Path(rehearsal.__file__).read_text()
    assert "neo-glm53" not in source
    assert "neo-k3-ingress" not in source
    assert "ps -" not in source
    assert "SIGKILL" in source and "SIGSTOP" in source


def test_fixture_images_change_file_content_not_serving_command(tmp_path):
    class Builder:
        def __init__(self):
            self.bodies = []
        def inspect_image(self, identity):
            return {"Id": identity, "Os": "linux", "Config": {"Entrypoint": None,
                    "Cmd": ["python3"], "Env": ["PATH=/usr/local/bin:/usr/bin:/bin"], "Labels": None}}
        def build(self, files, tag):
            self.bodies.append((files, tag))
            return "sha256:" + str(len(self.bodies)) * 64
    api = Builder()
    images = rehearsal.build_fixtures(api, IMAGE, "b" * 16, ledger_dir=tmp_path / "builds")
    assert set(images) == {"original", "healthy", "exit", "unready", "identity-absent",
                           "identity-declined"}
    for files, _ in api.bodies:
        assert files["Dockerfile"].decode().startswith("FROM " + IMAGE + "\n")
        assert "CMD" not in files["Dockerfile"].decode()
    assert b"OCC3_IDENTITY_ABSENT = False" in api.bodies[0][0]["fixture.py"]
    assert b"raise SystemExit(7)" in api.bodies[2][0]["fixture.py"]
    assert b"HEALTH_STATUS = 503" in api.bodies[3][0]["fixture.py"]
    assert b"OCC3_IDENTITY_ABSENT = True\nOCC3_DECLINED = False\nHEALTH_STATUS = 200" in api.bodies[4][0]["fixture.py"]
    assert (b"OCC3_DECLINED = True\nOCC3_DECLINE_CODE = " +
            repr(rehearsal.DECLINED_FIXTURE_CODE).encode() in api.bodies[5][0]["fixture.py"])


def test_cleanup_requires_exact_created_id_and_ownership_label():
    api = FakeDocker()
    api.docs[ORIGINAL]["Config"]["Labels"] = {life.REHEARSAL_LABEL: "a" * 16}
    with pytest.raises(life.Failure, match="unowned"):
        rehearsal.cleanup_owned(api, [(ORIGINAL, IMAGE)], "b" * 16)
    assert api.docs[ORIGINAL]["State"]["Running"]
    assert not any(op in ("remove", "stop") for op, _ in api.events)
    rehearsal.cleanup_owned(api, [(ORIGINAL, IMAGE)], "a" * 16)
    assert ORIGINAL not in api.docs
    assert ("stop", ORIGINAL) in api.events and ("remove", ORIGINAL) in api.events


def test_scenarios_require_actual_death_before_and_after_mutation():
    scenarios = {case["name"]: case for case in rehearsal.SCENARIOS}
    assert scenarios["startup-exit"]["image"] == "exit"
    assert scenarios["running-unready"]["image"] == "unready"
    assert scenarios["controller-kill-before"]["pause"] == "before_mutation"
    assert scenarios["controller-kill-after-rename"]["pause"] == "after_rename"
    assert scenarios["controller-kill-after-start"]["pause"] == "after_start"
    assert scenarios["controller-hang-after-start"]["signal"] == "stop"
    assert scenarios["supervisor-kill-before"]["signal"] == "supervisor-kill"
    assert scenarios["supervisor-kill-after-start"]["signal"] == "supervisor-kill"


def test_occ3_identity_fixture_contract_and_ninth_scenario():
    # The disposable fixture answers the exact executor step-0 identity route.
    source = rehearsal.FIXTURE
    assert "'/occ3/v1/native/runtime/identity'" in source
    assert "OCC3_IDENTITY_ABSENT" in source
    assert "occ3.runtime.identity.v1" in source
    compile("OCC3_IDENTITY_ABSENT = False\nHEALTH_STATUS = 200\n" + rehearsal.FIXTURE,
            "fixture.py", "exec")
    compile("OCC3_IDENTITY_ABSENT = True\nHEALTH_STATUS = 200\n" + rehearsal.FIXTURE,
            "fixture.py", "exec")
    compile("OCC3_IDENTITY_ABSENT = False\nOCC3_DECLINED = True\nOCC3_DECLINE_CODE = "
            + repr(rehearsal.DECLINED_FIXTURE_CODE) + "\nHEALTH_STATUS = 200\n" + rehearsal.FIXTURE,
            "fixture.py", "exec")
    scenarios = {case["name"]: case for case in rehearsal.SCENARIOS}
    assert len(rehearsal.SCENARIOS) == 10
    assert scenarios["occ3-identity-absent"]["image"] == "identity-absent"
    assert not scenarios["occ3-identity-absent"].get("signal")
    assert scenarios["occ3-declined"]["image"] == "identity-declined"
    assert not scenarios["occ3-declined"].get("signal")
    assert not scenarios["occ3-declined"].get("pause")
    # A service-initial abort after mutation must satisfy the scenario evidence contract.
    state = {"history": [{"operation": operation, "at": index} for index, operation in enumerate(
        ("rename_original", "stop_original", "rename_candidate", "start_candidate"))],
        "mutation_intent": True, "candidate_started": True, "candidate_epoch": "fixture-epoch",
        "original_stopped": True, "plan": {}}
    scenario = scenarios["occ3-identity-absent"]
    evidence = rehearsal.assert_scenario_evidence(scenario, state,
        {"verified": True, "result": "RESTORED"}, {"error": "Failure", "heartbeat": 4.0},
        {"State": {}}, False)
    assert evidence["expected_recovery"] == "RESTORED" and evidence["failure_stage_proven"]
    with pytest.raises(life.Failure, match="failure stage"):
        rehearsal.assert_scenario_evidence(scenario, state,
            {"verified": True, "result": "NOOP"}, {"error": "Failure", "heartbeat": 4.0},
            {"State": {}}, False)


def declined_state(**changes):
    state = {"history": [{"operation": operation, "at": index} for index, operation in enumerate(
        ("rename_original", "stop_original", "rename_candidate", "start_candidate"))],
        "mutation_intent": True, "candidate_started": True, "candidate_epoch": "fixture-epoch",
        "original_stopped": True, "restart_count": 0, "gates": {},
        "occ3_decline": {"gate": "service_initial", "field": "occ3_identity",
                         "evidence": {"ok": False, "check": "occ3_identity", "http_status": 503,
                                      "error_code": "occ3_declined",
                                      "declined_code": rehearsal.DECLINED_FIXTURE_CODE,
                                      "stage": "install", "experimental_selected": True,
                                      "preflight_reached": True, "backend_constructed": True,
                                      "worker_readiness_reached": False, "hooks_prepared": True,
                                      "route_mount_attempted": False, "rollback_completed": True}}}
    state.update(changes)
    return state


def test_occ3_declined_scenario_proves_the_exact_code_end_to_end():
    scenario = next(item for item in rehearsal.SCENARIOS if item["name"] == "occ3-declined")
    evidence = rehearsal.assert_scenario_evidence(scenario, declined_state(),
        {"verified": True, "result": "RESTORED"}, {"error": "Failure", "heartbeat": 4.0},
        {"State": {}}, False)
    assert evidence == {"expected_recovery": "RESTORED", "failure_stage_proven": True,
                        "declined_code": rehearsal.DECLINED_FIXTURE_CODE}


@pytest.mark.parametrize("mutation", ["missing", "wrong_code", "wrong_stage", "no_rollback",
                                      "restart_consumed", "gate_journaled", "wrong_gate", "wrong_field"])
def test_occ3_declined_scenario_requires_the_exact_fail_fast_evidence(mutation):
    scenario = next(item for item in rehearsal.SCENARIOS if item["name"] == "occ3-declined")
    state = declined_state()
    if mutation == "missing":
        del state["occ3_decline"]
    elif mutation == "wrong_code":
        state["occ3_decline"]["evidence"]["declined_code"] = "other_code"
    elif mutation == "wrong_stage":
        state["occ3_decline"]["evidence"]["stage"] = "prepare"
    elif mutation == "no_rollback":
        state["occ3_decline"]["evidence"]["rollback_completed"] = False
    elif mutation == "restart_consumed":
        state["restart_count"] = 1
    elif mutation == "gate_journaled":
        state["gates"] = {"service_initial": {"ok": True}}
    elif mutation == "wrong_gate":
        state["occ3_decline"]["gate"] = "service_final"
    else:
        state["occ3_decline"]["field"] = "chat"
    with pytest.raises(life.Failure, match="fail fast"):
        rehearsal.assert_scenario_evidence(scenario, state,
            {"verified": True, "result": "RESTORED"}, {"error": "Failure", "heartbeat": 4.0},
            {"State": {}}, False)


def test_occ3_declined_fixture_answers_status_schema_and_identity_not_ready():
    source = rehearsal.FIXTURE
    assert "'/occ3/v1/status'" in source
    assert "'optimus.occ3.status.v1'" in source
    assert "OCC3_DECLINED" in source and "OCC3_DECLINE_CODE" in source
    assert "{'error': {'code': 'not_ready'}}" in source
    assert "'DECLINED'" in source and "'NATIVE_READY'" in source
    assert "DECLINED_FIXTURE_CODE" not in source


def declined_fixture_answers(requester_key=None):
    """Run the shipped declined fixture handler in-process; no socket, no Docker."""
    import http.server as http_server_module
    source = ("OCC3_IDENTITY_ABSENT = False\nOCC3_DECLINED = True\nOCC3_DECLINE_CODE = "
              + repr(rehearsal.DECLINED_FIXTURE_CODE) + "\nHEALTH_STATUS = 200\n" + rehearsal.FIXTURE)
    served = []
    class StubServer:
        def __init__(self, address, handler):
            served.append(address)
        def serve_forever(self):
            pass
    original_server = http_server_module.HTTPServer
    original_argv = sys.argv
    http_server_module.HTTPServer = StubServer
    sys.argv = ["fixture.py", "--api-key", rehearsal.FIXTURE_KEY]
    try:
        namespace = {}
        exec(compile(source, "fixture.py", "exec"), namespace)
    finally:
        http_server_module.HTTPServer = original_server
        sys.argv = original_argv
    assert served == [("0.0.0.0", 8081)]
    Handler = namespace["Handler"]

    def answer(path, method="GET"):
        import io
        handler = object.__new__(Handler)
        handler.path = path
        handler.headers = {"Authorization": "Bearer " + (requester_key or rehearsal.FIXTURE_KEY)}
        out, state = io.BytesIO(), {}
        handler.wfile = out
        handler.send_response = lambda status: state.update(status=status)
        handler.send_header = lambda *args: None
        handler.end_headers = lambda: None
        handler.rfile = io.BytesIO(b"{}")
        if method == "GET":
            handler.do_GET()
        else:
            handler.do_POST()
        body = out.getvalue()
        return state["status"], (json.loads(body) if body else None)
    return answer


def test_shipped_declined_fixture_drives_the_real_controller_check():
    from deploy.v3 import service_checks as checks
    answer = declined_fixture_answers()
    identity_status, identity_body = answer("/occ3/v1/native/runtime/identity", "POST")
    assert identity_status == 503 and identity_body == {"error": {"code": "not_ready"}}
    status_status, status_body = answer("/occ3/v1/status")
    assert status_status == 200 and status_body["schema"] == "optimus.occ3.status.v1"
    assert status_body["state"] == "DECLINED" and status_body["code"] == rehearsal.DECLINED_FIXTURE_CODE

    def requester(method, url, key, payload, timeout):
        assert key == rehearsal.FIXTURE_KEY
        if url.endswith(checks.OCC3_IDENTITY_PATH):
            status, body = answer("/occ3/v1/native/runtime/identity", "POST")
        else:
            assert url.endswith(checks.OCC3_STATUS_PATH) and method == "GET"
            status, body = answer("/occ3/v1/status")
        return checks.HttpResult(status, body)

    result = checks.check_occ3_identity("http://fixture", rehearsal.FIXTURE_KEY, requester=requester)
    assert result["ok"] is False and result["error_code"] == "occ3_declined"
    assert result["http_status"] == 503 and result["declined_code"] == rehearsal.DECLINED_FIXTURE_CODE
    assert result["stage"] == "install" and result["rollback_completed"] is True
    assert result["worker_readiness_reached"] is False and result["route_mount_attempted"] is False


def test_shipped_declined_fixture_keeps_keyless_requests_unauthorized():
    answer = declined_fixture_answers(requester_key="wrong-key")
    assert answer("/occ3/v1/status") == (401, {"error": "unauthorized"})
    assert answer("/occ3/v1/native/runtime/identity", "POST") == (401, {"error": "unauthorized"})


def test_rehearsal_mode_cannot_target_unowned_original():
    api = FakeDocker()
    with pytest.raises(life.Failure, match="disposable"):
        life.assert_rehearsal_original(api.docs[ORIGINAL], {"rehearsal_owner": "a" * 16})
    document = copy.deepcopy(api.docs[ORIGINAL])
    document["Name"] = "/occ3-v3-rehearsal-" + "a" * 16 + "-original"
    document["Config"]["Labels"] = {life.REHEARSAL_LABEL: "a" * 16}
    life.assert_rehearsal_original(document, {"rehearsal_owner": "a" * 16})
    document["HostConfig"]["DeviceRequests"] = [{"Capabilities": [["gpu"]]}]
    with pytest.raises(life.Failure):
        life.assert_rehearsal_original(document, {"rehearsal_owner": "a" * 16})


def test_rehearsal_helper_uses_production_prepare_launch_and_recovery_interfaces():
    assert rehearsal.prepare is life.prepare
    from deploy.v3 import supervisor
    assert rehearsal.launch is supervisor.launch
    assert supervisor.recover is life.recover
    source = Path(rehearsal.__file__).read_text()
    assert "process_identity(pid) != expected_identity" in source
    assert "signal.pidfd_send_signal" in source
    assert "os.kill(" not in source
    assert 'recovery.get("verified") is not True' in source


@pytest.mark.parametrize("field,value", [("OnBuild", ["RUN touch /unsafe"]),
    ("Volumes", {"/data": {}}), ("Env", ["LD_PRELOAD=/unsafe.so"]),
    ("Env", ["NVIDIA_VISIBLE_DEVICES=all"]), ("Entrypoint", ["/startup.sh"]),
    ("Healthcheck", {"Test": ["CMD", "unsafe"]})])
def test_fixture_base_rejected_before_build_for_inherited_side_effects(tmp_path, field, value):
    document = {"Id": IMAGE, "Os": "linux", "Config": {"Cmd": ["python3"], "Env": ["PATH=/usr/bin"]}}
    document["Config"][field] = value
    class ImageOnly:
        def inspect_image(self, identity):
            return document
        def build(self, *args):
            raise AssertionError("unsafe image reached build")
    with pytest.raises(life.Failure):
        rehearsal.build_fixtures(ImageOnly(), IMAGE, "a" * 16, ledger_dir=tmp_path / "builds")


def test_raw_original_fixture_mount_is_refused_while_still_unstarted(tmp_path):
    api = FixtureDocker(lambda doc: doc.update(Mounts=[
        {"Type": "volume", "Source": "/inherited", "Destination": "/data", "RW": True}]))
    with pytest.raises(life.Failure):
        rehearsal.create_original_fixture(api, IMAGE, "a" * 16, 41999,
            "occ3-v3-rehearsal-" + "a" * 16 + "-original", ledger_path=tmp_path / "fixture.json")
    assert not any(operation == "start" for operation, _ in api.events)


def test_running_unready_cannot_pass_from_verified_early_noop():
    scenario = next(item for item in rehearsal.SCENARIOS if item["name"] == "running-unready")
    with pytest.raises(life.Failure, match="failure stage"):
        rehearsal.assert_scenario_evidence(scenario, {"history": []},
            {"result": "NOOP", "verified": True}, {}, {"State": {}}, False)


def test_running_unready_requires_started_http503_and_actual_startup_deadline():
    scenario = next(item for item in rehearsal.SCENARIOS if item["name"] == "running-unready")
    state = {"history": [{"operation": "start_candidate", "at": 1}], "mutation_intent": True,
             "candidate_started": True, "candidate_epoch": "epoch", "original_stopped": True,
             "plan": {"startup_timeout_s": 5},
             "readiness_observations": {"checks": 4, "last_http_status": 503, "last_ok": False, "started_monotonic": 2}}
    recovery = {"verified": True, "result": "RESTORED"}
    assert rehearsal.assert_scenario_evidence(scenario, state, recovery,
               {"error": "RunningUnready", "heartbeat": 7.1}, {"State": {}}, False)["failure_stage_proven"]
    for bad in ({"error": "Failure", "heartbeat": 7.1}, {"error": "RunningUnready", "heartbeat": 6.9}):
        with pytest.raises(life.Failure):
            rehearsal.assert_scenario_evidence(scenario, state, recovery, bad, {"State": {}}, False)


def test_pidfd_rejects_pid_reuse_after_open_without_numeric_signal(monkeypatch):
    calls = []
    monkeypatch.setattr(rehearsal.os, "pidfd_open", lambda pid, flags: calls.append(("open", pid)) or 77, raising=False)
    monkeypatch.setattr(rehearsal.os, "close", lambda fd: calls.append(("close", fd)))
    monkeypatch.setattr(rehearsal.signal, "pidfd_send_signal", lambda *args: calls.append(("signal", args)), raising=False)
    monkeypatch.setattr(rehearsal, "process_identity", lambda pid: {"pid": pid, "start_ticks": "replacement"})
    with pytest.raises(life.Failure, match="identity"):
        rehearsal.signal_owned_process(999, {"pid": 999, "start_ticks": "expected"}, 9)
    assert calls == [("open", 999), ("close", 77)]


def test_pidfd_requires_nonnull_identity_and_signals_pinned_descriptor(monkeypatch):
    calls = []
    monkeypatch.setattr(rehearsal.os, "pidfd_open", lambda pid, flags: calls.append(("open", pid)) or 77, raising=False)
    monkeypatch.setattr(rehearsal.os, "close", lambda fd: calls.append(("close", fd)))
    monkeypatch.setattr(rehearsal.signal, "pidfd_send_signal", lambda *args: calls.append(("signal", args)), raising=False)
    expected = {"pid": 999, "start_ticks": "expected"}
    monkeypatch.setattr(rehearsal, "process_identity", lambda pid: expected)
    with pytest.raises(life.Failure):
        rehearsal.signal_owned_process(999, None, 9)
    assert calls == []
    rehearsal.signal_owned_process(999, expected, 9)
    assert calls == [("open", 999), ("signal", (77, 9, None, 0)), ("close", 77)]


class FixtureDocker(FakeDocker):
    def __init__(self, drift=None):
        super().__init__()
        self.drift = drift

    def inspect_image(self, identity):
        return {"Id": identity, "Os": "linux", "Config": {
            "Cmd": ["python3"], "Entrypoint": None, "Env": ["PATH=/usr/bin", "LANG=C.UTF-8"],
            "Labels": {"fixture-base": "vetted"}, "User": "", "Volumes": None, "OnBuild": None}}

    def create(self, body, name):
        identity = super().create(body, name)
        image = self.inspect_image(body["Image"])["Config"]
        config = self.docs[identity]["Config"]
        config["Env"] = image["Env"] + config["Env"]
        config["Labels"] = {**image["Labels"], **config["Labels"]}
        self.docs[identity]["NetworkSettings"] = {"Networks": {"bridge": {"NetworkID": "", "Aliases": None}}}
        if self.drift:
            self.drift(self.docs[identity])
        return identity


@pytest.mark.parametrize("section,field,value", [
    ("Config", "Env", ["PATH=/usr/bin", "LANG=C.UTF-8", "NVIDIA_VISIBLE_DEVICES=void", "CUDA_VISIBLE_DEVICES=-1", "LD_PRELOAD=/unsafe.so"]),
    ("Config", "Env", ["NVIDIA_VISIBLE_DEVICES=all", "CUDA_VISIBLE_DEVICES=-1"]),
    ("Config", "Env", ["PATH=/usr/bin", "PATH=/usr/bin", "NVIDIA_VISIBLE_DEVICES=void", "CUDA_VISIBLE_DEVICES=-1"]),
    ("Config", "Entrypoint", ["sh", "-c"]), ("Config", "Cmd", ["unsafe"]),
    ("Config", "User", "0"), ("Config", "WorkingDir", "/host"),
    ("Config", "Healthcheck", {"Test": ["CMD", "unsafe"]}), ("Config", "Volumes", {"/data": {}}),
    ("Config", "ExposedPorts", {"80/tcp": {}}), ("Config", "OnBuild", ["unsafe"]),
    ("HostConfig", "PortBindings", {"8081/tcp": [{"HostIp": "0.0.0.0", "HostPort": "41999"}]}),
    ("HostConfig", "PortBindings", {"8081/tcp": [{"HostIp": "127.0.0.1", "HostPort": "42000"}]}),
    ("HostConfig", "NetworkMode", "host"), ("HostConfig", "Runtime", "nvidia"),
    ("HostConfig", "DeviceRequests", [{"Capabilities": [["gpu"]]}]),
    ("HostConfig", "Devices", [{"PathOnHost": "/dev/mem"}]), ("HostConfig", "Binds", ["/:/host"]),
    ("HostConfig", "Mounts", [{"Type": "bind", "Source": "/", "Target": "/host"}]),
    ("HostConfig", "Tmpfs", {"/tmp": "rw"}), ("HostConfig", "Privileged", True),
    ("HostConfig", "CapAdd", ["SYS_ADMIN"]), ("HostConfig", "CapDrop", []),
    ("HostConfig", "SecurityOpt", ["seccomp=unconfined"]), ("HostConfig", "ReadonlyRootfs", False),
    ("HostConfig", "PidMode", "host"), ("HostConfig", "IpcMode", "host"),
    ("HostConfig", "UTSMode", "host"), ("HostConfig", "CgroupnsMode", "host"),
    ("HostConfig", "GroupAdd", ["0"]), ("HostConfig", "UsernsMode", "host"),
    ("HostConfig", "RestartPolicy", {"Name": "always", "MaximumRetryCount": 0}),
    ("HostConfig", "AutoRemove", True), ("HostConfig", "PublishAllPorts", True),
    ("HostConfig", "LogConfig", {"Type": "fluentd", "Config": {"fluentd-address": "host:80"}}),
    ("HostConfig", "UnknownPrivilege", True), ("Config", "UnknownHook", "unsafe"),
    ("HostConfig", "MaskedPaths", []), ("HostConfig", "ReadonlyPaths", []),
    ("HostConfig", "OomKillDisable", True), ("Config", "StopSignal", "SIGSTOP"),
    ("NetworkSettings", "Ports", {"8081/tcp": [{"HostIp": "0.0.0.0", "HostPort": "41999"}]}),
    ("NetworkSettings", "Networks", {"bridge": {}, "unknown": {}}),
    ("NetworkSettings", "Networks", {"bridge": {"Aliases": ["unknown-service"]}}),
    ("NetworkSettings", "Networks", {"bridge": {"IPAMConfig": {"IPv4Address": "1.2.3.4"}}}),
])
def test_actual_fixture_role_drift_is_refused_before_any_start(tmp_path, section, field, value):
    api = FixtureDocker(lambda document: document[section].update({field: value}))
    with pytest.raises(life.Failure):
        rehearsal.create_original_fixture(api, IMAGE, "a" * 16, 41999,
            "occ3-v3-rehearsal-" + "a" * 16 + "-original", ledger_path=tmp_path / "fixture.json")
    assert any(operation == "create" for operation, _ in api.events)
    assert not any(operation == "start" for operation, _ in api.events)


def test_actual_fixture_policy_is_independent_of_request_builder(tmp_path, monkeypatch):
    builder = rehearsal.original_request
    def corrupted(*args, **kwargs):
        body = builder(*args, **kwargs)
        body["HostConfig"]["PortBindings"]["8081/tcp"][0]["HostIp"] = "0.0.0.0"
        return body
    monkeypatch.setattr(rehearsal, "original_request", corrupted)
    api = FixtureDocker()
    with pytest.raises(life.Failure):
        rehearsal.create_original_fixture(api, IMAGE, "a" * 16, 41999,
            "occ3-v3-rehearsal-" + "a" * 16 + "-original", ledger_path=tmp_path / "fixture.json")
    assert not any(operation == "start" for operation, _ in api.events)


def test_actual_fixture_accepts_only_documented_engine_empty_equivalents(tmp_path):
    def docker_defaults(document):
        document["HostConfig"].update(Binds=None, Mounts=[], CapAdd=None, UTSMode="",
            SecurityOpt=["no-new-privileges:true"])
        document["Config"].update(Volumes=None, Domainname="")
        document["NetworkSettings"]["Networks"]["bridge"].update(NetworkID="8" * 64, Aliases=None, IPAMConfig=None)
    api = FixtureDocker(docker_defaults)
    identity = rehearsal.create_original_fixture(api, IMAGE, "a" * 16, 41999,
        "occ3-v3-rehearsal-" + "a" * 16 + "-original", ledger_path=tmp_path / "fixture.json")
    assert ("start", identity) in api.events
    assert api.docs[identity]["State"]["Running"]


@pytest.mark.parametrize("point", ["create", "start"])
def test_basic_fixture_lost_reply_has_durable_identity_and_never_retries_or_cleans(tmp_path, point):
    path = tmp_path / "fixture.json"
    class LostReply(FixtureDocker):
        def create(self, body, name):
            ledger = life.read_json(path)
            assert ledger["phase"] == "CREATE_INTENT" and ledger["name"] == name
            assert ledger["container_id"] is None and len(ledger["request_sha256"]) == 64
            assert ledger["resource_nonce"] == body["Labels"]["occ3.v3.resource-nonce"]
            assert ledger["network_contract"]["mode"] == "bridge"
            assert ledger["port_contract"]["host_ip"] == "127.0.0.1"
            identity = super().create(body, name)
            if point == "create":
                raise OSError("synthetic lost create response")
            return identity
        def start(self, identity):
            ledger = life.read_json(path)
            assert ledger["phase"] == "START_INTENT" and ledger["container_id"] == identity
            assert len(ledger["actual_configuration_sha256"]) == 64
            super().start(identity)
            if point == "start":
                raise OSError("synthetic lost start response")
    api = LostReply()
    with pytest.raises(OSError):
        rehearsal.create_original_fixture(api, IMAGE, "a" * 16, 41999,
            "occ3-v3-rehearsal-" + "a" * 16 + "-original", ledger_path=path)
    record = life.read_json(path)
    assert record["result"] == "FAIL" and not record["cleanup_complete"]
    assert record["container_id"] == "2" * 64 and record["cleanup_admissible"]
    assert record["reconciliation"] == "verified_identity_for_cleanup_only"
    assert api.docs["2" * 64]["State"]["Running"] is (point == "start")
    assert len([op for op, _ in api.events if op == "create"]) == 1
    assert len([op for op, _ in api.events if op == "start"]) == (1 if point == "start" else 0)
    assert not any(op in ("stop", "remove", "rename") for op, _ in api.events)


@pytest.mark.parametrize("fault", ["foreign", "missing", "config"])
def test_uncertain_basic_fixture_unverified_occupant_blocks_all_cleanup(tmp_path, fault):
    path = tmp_path / "fixture.json"
    class Unverified(FixtureDocker):
        def create(self, body, name):
            identity = super().create(body, name)
            if fault == "foreign":
                self.docs[identity]["Config"]["Labels"]["occ3.v3.resource-nonce"] = "f" * 32
            elif fault == "missing":
                del self.docs[identity]
            else:
                self.docs[identity]["HostConfig"]["ReadonlyRootfs"] = False
            raise OSError("synthetic unknown create response")
    api = Unverified()
    with pytest.raises(OSError):
        rehearsal.create_original_fixture(api, IMAGE, "a" * 16, 41999,
            "occ3-v3-rehearsal-" + "a" * 16 + "-original", ledger_path=path)
    record = life.read_json(path)
    assert record["reconciliation"] == "unresolved" and not record["cleanup_admissible"]
    assert record["container_id"] is None and record["result"] == "FAIL"
    with pytest.raises(life.Failure, match="blocks cleanup"):
        rehearsal.cleanup_original_fixture(api, path)
    assert not any(op in ("start", "stop", "remove", "rename") for op, _ in api.events)


def test_explicit_basic_cleanup_requires_fresh_exact_full_config_and_keeps_failure_receipt(tmp_path):
    path = tmp_path / "fixture.json"
    class LostStart(FixtureDocker):
        def start(self, identity):
            super().start(identity)
            raise OSError("synthetic lost start response")
    api = LostStart()
    with pytest.raises(OSError):
        rehearsal.create_original_fixture(api, IMAGE, "a" * 16, 41999,
            "occ3-v3-rehearsal-" + "a" * 16 + "-original", ledger_path=path)
    original_events = list(api.events)
    assert not any(op in ("stop", "remove") for op, _ in original_events)
    result = rehearsal.cleanup_original_fixture(api, path)
    assert result["cleanup_complete"] and result["container_id"] == "2" * 64
    record = life.read_json(path)
    assert record["result"] == "FAIL" and record["cleanup_complete"]
    assert "2" * 64 not in api.docs
    assert [op for op, _ in api.events[len(original_events):] if op in ("stop", "remove")] == ["stop", "remove"]


@pytest.mark.parametrize("point", ["create", "start"])
def test_run_scenario_retains_full_basic_ledger_at_lost_reply_boundary(tmp_path, monkeypatch, point):
    import types
    class NoSocket:
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass
        def bind(self, address):
            assert address == ("127.0.0.1", 0)
        def getsockname(self):
            return ("127.0.0.1", 41999)
    class LostReply(FixtureDocker):
        def create(self, body, name):
            identity = super().create(body, name)
            if point == "create":
                raise OSError("synthetic lost create response")
            return identity
        def start(self, identity):
            super().start(identity)
            if point == "start":
                raise OSError("synthetic lost start response")
    monkeypatch.setattr(rehearsal, "os", types.SimpleNamespace(name="posix"))
    monkeypatch.setattr(rehearsal, "socket", types.SimpleNamespace(socket=lambda: NoSocket()))
    api = LostReply()
    output = tmp_path / "scenario"
    result = rehearsal.run_scenario(api, {"original": IMAGE, "exit": CANDIDATE_IMAGE}, output, rehearsal.SCENARIOS[0])
    assert result["result"] == "FAIL"
    record = life.read_json(output / "original-fixture.json")
    assert record["container_id"] == "2" * 64 and record["owner"]
    assert record["name"] == api.docs[record["container_id"]]["Name"].lstrip("/")
    assert record["result"] == "FAIL" and record["cleanup_complete"] is False
    assert not any(op in ("stop", "remove") for op, _ in api.events)


def test_uncertain_build_retains_durable_hashed_artifact_intent_without_guess_or_retry(tmp_path):
    class Builder:
        def __init__(self):
            self.calls = 0
        def inspect_image(self, identity):
            return {"Id": identity, "Os": "linux", "Config": {"Cmd": ["python3"], "Env": ["PATH=/usr/bin"]}}
        def build(self, files, tag):
            self.calls += 1
            record = life.read_json(tmp_path / "builds" / "original.json")
            assert record["phase"] == "BUILD_INTENT" and record["tag"] == tag
            assert len(record["resource_nonce"]) == 32 and record["base_image_id"] == IMAGE
            assert all(len(value) == 64 for value in record["files_sha256"].values())
            raise OSError("synthetic lost build response")
    api = Builder()
    with pytest.raises(OSError):
        rehearsal.build_fixtures(api, IMAGE, "a" * 16, ledger_dir=tmp_path / "builds")
    record = life.read_json(tmp_path / "builds" / "original.json")
    assert record["phase"] == "BUILD_UNCERTAIN" and record["image_id"] is None
    assert record["unresolved_backend_artifact"] and record["artifacts_retained"]
    assert not record["cleanup_complete"] and api.calls == 1


def test_basic_fixture_normal_path_records_id_before_start_and_complete_actual_config(tmp_path):
    api = FixtureDocker()
    path = tmp_path / "fixture.json"
    identity = rehearsal.create_original_fixture(api, IMAGE, "a" * 16, 41999,
        "occ3-v3-rehearsal-" + "a" * 16 + "-original", ledger_path=path)
    record = life.read_json(path)
    assert record["container_id"] == identity and record["phase"] == "START_ACKNOWLEDGED"
    assert len(record["expected_configuration_sha256"]) == 64
    assert len(record["actual_configuration_sha256"]) == 64
    assert record["result"] != "PASS" and record["cleanup_complete"] is False


def test_basic_ledger_handles_vetted_engine_empty_fields_without_builder_oracle(tmp_path):
    def empty_engine_fields(document):
        document["HostConfig"]["DeviceRequests"] = None
        document["HostConfig"]["SecurityOpt"] = ["no-new-privileges:true"]
        document["Config"]["Healthcheck"].update(Interval=0, Timeout=0, StartPeriod=0, StartInterval=0, Retries=0)
        # Engine may append the image's Env after the explicit CPU overrides.
        document["Config"]["Env"] = list(reversed(document["Config"]["Env"]))
    api = FixtureDocker(empty_engine_fields)
    path = tmp_path / "fixture.json"
    identity = rehearsal.create_original_fixture(api, IMAGE, "a" * 16, 41999,
        "occ3-v3-rehearsal-" + "a" * 16 + "-original", ledger_path=path)
    assert life.read_json(path)["container_id"] == identity
    assert ("start", identity) in api.events
