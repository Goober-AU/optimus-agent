"""Offline FakeDocker tests; never use a Docker socket, HTTP or model runtime."""
from copy import deepcopy
import importlib
import json
from pathlib import Path
import time

import pytest

from deploy.v3 import rehearsal_ingress as ri
from deploy.v3.docker_api import DockerError
from deploy.v3.lifecycle import Failure, read_json

BACKEND_IMAGE = "sha256:" + "a" * 64
NGINX_IMAGE = "sha256:" + "b" * 64
OWNER = "1234567890abcdef"
NETWORK = "f" * 64
PRIVATE_DIAGNOSTIC = "DO_NOT_LOG_SUPPLIED_PRIVATE_DIAGNOSTIC"


class Clock:
    def __init__(self):
        self.value = time.monotonic()
    def now(self):
        return self.value
    def pause(self, duration):
        self.value += duration


class FakeDocker:
    def __init__(self, fault=None):
        self.fault = fault
        self.calls, self.containers, self.images = [], {}, {
            BACKEND_IMAGE: {"Id": BACKEND_IMAGE, "Config": {"Volumes": None}},
            NGINX_IMAGE: {"Id": NGINX_IMAGE, "Config": {"Volumes": None}},
        }
        self.network = None
        self.config = None
        self.cached_backend = None
        self.reload_count = 0
        self.dns_lag = 4 if fault == "dns-lag" else 0
        self.deadline = None
        self.extra_members = {}

    def inspect_image(self, identity):
        self.calls.append(("inspect_image", identity))
        return deepcopy(self.images[identity])

    def create_network(self, name, labels):
        self.calls.append(("create_network", name))
        self.network = {"Id": NETWORK, "Name": name, "Labels": deepcopy(labels),
                        "Driver": "bridge", "Internal": True, "Scope": "local", "Attachable": False,
                        "Ingress": False, "EnableIPv6": False, "Options": {},
                        "IPAM": {"Driver": "default", "Options": None,
                                 "Config": [{"Subnet": "172.30.0.0/16", "Gateway": "172.30.0.1"}]}}
        return NETWORK

    def inspect_network(self, identity):
        self.calls.append(("inspect_network", identity))
        if self.network is None:
            raise DockerError("inspect_network", 404)
        result = deepcopy(self.network)
        result["Containers"] = {key: {"Name": doc["Name"].removeprefix("/")}
                                for key, doc in self.containers.items() if doc["State"]["Running"]}
        result["Containers"].update(self.extra_members)
        return result

    def inspect_network_name(self, name):
        self.calls.append(("inspect_network_name", name))
        if self.network is None or self.network["Name"] != name:
            raise DockerError("inspect_network_name", 404)
        return self.inspect_network(self.network["Id"])

    def create(self, body, name):
        self.calls.append(("create", name))
        identity = f"{len(self.containers) + 1:064x}"
        config = {key: deepcopy(value) for key, value in body.items() if key not in {"HostConfig", "NetworkingConfig"}}
        inherited = dict(value.split("=", 1) for value in self.images[body["Image"]]["Config"].get("Env", []))
        inherited.update(dict(value.split("=", 1) for value in body["Env"]))
        config["Env"] = [key + "=" + value for key, value in inherited.items()]
        config["Labels"] = {**(self.images[body["Image"]]["Config"].get("Labels") or {}), **body["Labels"]}
        config["ExposedPorts"] = {**(self.images[body["Image"]]["Config"].get("ExposedPorts") or {}), **body["ExposedPorts"]}
        self.containers[identity] = {
            "Id": identity, "Image": body["Image"], "Name": "/" + name,
            "Config": config, "HostConfig": deepcopy(body["HostConfig"]), "Mounts": [],
            "State": {"Status": "created", "Running": False, "ExitCode": 0},
            "NetworkSettings": {"Networks": {self.network["Name"]: {"NetworkID": NETWORK, "IPAddress": ""}}, "Ports": {}},
        }
        return identity

    def inspect(self, identity):
        self.calls.append(("inspect", identity))
        if identity not in self.containers:
            matches = [key for key, doc in self.containers.items() if doc["Name"] == "/" + identity]
            if len(matches) != 1:
                raise DockerError("inspect", 409 if matches else 404)
            identity = matches[0]
        return deepcopy(self.containers[identity])

    def role(self, identity):
        return self.containers[identity]["Config"]["Labels"][ri.ROLE_LABEL]

    def by_role(self, role):
        return next(key for key in self.containers if self.role(key) == role)

    def env(self, identity):
        return dict(value.split("=", 1) for value in self.containers[identity]["Config"]["Env"])

    def current_dns(self):
        name = "/" + ri._names(OWNER)["original"]
        return next(key for key, doc in self.containers.items()
                    if doc["Name"] == name and doc["State"]["Running"])

    def start(self, identity):
        self.calls.append(("start", identity))
        doc = self.containers[identity]
        role = self.role(identity)
        doc["State"].update(Status="running", Running=True, StartedAt="fixture-epoch-" + role)
        ip = {"original": "172.30.0.2", "candidate": "172.30.0.3", "proxy": "172.30.0.4"}[role]
        if role == "candidate" and self.fault == "same-ip":
            ip = "172.30.0.2"
        doc["NetworkSettings"]["Networks"][self.network["Name"]]["IPAddress"] = ip
        if role == "proxy":
            doc["NetworkSettings"]["Ports"] = {"8080/tcp": [{"HostIp": "127.0.0.1", "HostPort": "49155"}]}
            self.cached_backend = self.current_dns()
        if role == "candidate" and self.fault == "candidate-exit":
            doc["State"].update(Status="exited", Running=False, ExitCode=7)
        if role == "candidate" and self.fault == "config-drift":
            self.config += b"# private unexpected drift\n"

    def rename(self, identity, name):
        self.calls.append(("rename", identity, name))
        assert not any(doc["Name"] == "/" + name for key, doc in self.containers.items() if key != identity)
        self.containers[identity]["Name"] = "/" + name

    def put_file(self, identity, directory, name, content):
        self.calls.append(("put_file", identity, directory, name))
        assert self.role(identity) == "proxy" and directory == "/etc/nginx" and name == "nginx.conf"
        self.config = content

    def nginx(self, identity, operation, timeout=10):
        self.calls.append(("nginx", identity, operation))
        assert self.role(identity) == "proxy" and timeout > 0
        if operation == "config":
            content = b"# configuration file /etc/nginx/nginx.conf:\n" + self.config + b"\n"
            if self.fault == "initial-config-drift":
                content += b"# additional config\n"
            return {"exit_code": 0, "stdout": content, "stderr": PRIVATE_DIAGNOSTIC.encode()}
        if operation == "test":
            return {"exit_code": 7 if self.fault == "config-test-failure" else 0,
                    "stdout": b"", "stderr": PRIVATE_DIAGNOSTIC.encode()}
        assert operation == "reload"
        if self.fault == "reload-failure":
            return {"exit_code": 9, "stdout": b"", "stderr": PRIVATE_DIAGNOSTIC.encode()}
        self.reload_count += 1
        if self.fault != "no-cutover":
            self.cached_backend = self.current_dns()
        return {"exit_code": 0, "stdout": b"", "stderr": PRIVATE_DIAGNOSTIC.encode()}

    def request(self, url, nonce, timeout):
        self.calls.append(("http_proxy", url, nonce))
        assert url == "http://127.0.0.1:49155" and timeout > 0
        if self.fault == "http-error":
            raise RuntimeError(PRIVATE_DIAGNOSTIC)
        backend = self.cached_backend
        if self.fault == "premature-cutover":
            backend = self.current_dns()
        env = self.env(backend)
        dns = self.current_dns()
        if self.role(dns) == "candidate" and self.reload_count == 0 and self.dns_lag:
            self.dns_lag -= 1
            dns = self.by_role("original")
        if self.fault == "dns-never-updated" and self.role(dns) == "candidate":
            dns = self.by_role("original")
        address = self.containers[dns]["NetworkSettings"]["Networks"][self.network["Name"]]["IPAddress"]
        body = {"schema": "occ3-routing-backend/v1", "owner": env["OCC3_OWNER"],
                "backend": env["OCC3_BACKEND"], "nonce": env["OCC3_RESPONSE_NONCE"],
                "request_nonce": nonce, "route_addresses": [address]}
        if self.fault == "status-only":
            body = {}
        if self.fault == "wrong-nonce":
            body["nonce"] = "not-the-nonce"
        if self.fault == "cached-http-body":
            body["request_nonce"] = "0" * 32
        if self.fault == "missing-dns":
            body.pop("route_addresses")
        return {"status": 200, "body": body}

    def stop(self, identity, seconds=1):
        self.calls.append(("stop", identity, self.reload_count))
        self.containers[identity]["State"].update(Status="exited", Running=False)

    def remove(self, identity):
        self.calls.append(("remove", identity))
        assert not self.containers[identity]["State"]["Running"]
        del self.containers[identity]

    def remove_network(self, identity):
        self.calls.append(("remove_network", identity))
        assert identity == NETWORK and not self.containers and not self.extra_members
        self.network = None


def run(tmp_path, fault=None, *, api=None):
    api = api or FakeDocker(fault)
    clock = Clock()
    output = tmp_path / "fresh-attempt"
    result = ri.run_rehearsal(api, BACKEND_IMAGE, NGINX_IMAGE, output,
        authorized=True, timeout_s=2, owner=OWNER, requester=api.request,
        clock=clock.now, pause=clock.pause)
    return api, result, output


def test_real_shared_primitive_proves_old_new_restored_route_offline(tmp_path):
    api, result, output = run(tmp_path)
    assert result["result"] == "PASS"
    assert result["production_rollback_proven"] is result["controller_death_tested"] is False
    gates = result["gates"]
    assert gates["different_addresses"]["original_ip"] != gates["different_addresses"]["candidate_ip"]
    assert gates["stale_before_reload"]["backend"] == "original"
    assert gates["stale_before_reload"]["dns_address_exact"] == "172.30.0.3"
    assert gates["candidate_after_shared_reload"]["response"]["backend"] == "candidate"
    assert gates["original_after_shared_restore"]["response"]["backend"] == "original"
    assert api.reload_count == 2 and api.network is None and api.containers == {}
    history = read_json(output / "routing-attempt" / "journal.json")["history"]
    operations = [entry["operation"] for entry in history]
    assert "candidate_ingress_reload" in operations and "recovery_ingress_reload" in operations
    assert all(call[2] == 2 for call in api.calls if call[0] == "stop")
    assert ri.cleanup_owned(api, output, confirmed=True)["containers_removed"] == 0
    assert read_json(output / "result.json")["result"] == "PASS"


def test_no_external_action_without_explicit_admission(tmp_path):
    api = FakeDocker()
    with pytest.raises(Failure, match="authorization"):
        ri.run_rehearsal(api, BACKEND_IMAGE, NGINX_IMAGE, tmp_path / "absent")
    assert api.calls == [] and not (tmp_path / "absent").exists()


def test_import_is_inert(monkeypatch):
    monkeypatch.setattr(ri.DockerAPI, "__init__", lambda *a, **k: pytest.fail("Docker constructed during import"))
    importlib.reload(ri)


def test_images_are_existing_immutable_and_no_mount_defaults(tmp_path):
    api = FakeDocker()
    api.images[BACKEND_IMAGE]["Id"] = "sha256:" + "c" * 64
    api, result, _ = run(tmp_path, api=api)
    assert result["result"] == "FAIL" and api.network is None
    assert not any(call[0] == "create" for call in api.calls)
    with pytest.raises(ValueError):
        ri.run_rehearsal(api, "python:latest", NGINX_IMAGE, tmp_path / "not-created", authorized=True)


def test_default_image_volumes_refused(tmp_path):
    api = FakeDocker()
    api.images[NGINX_IMAGE]["Config"]["Volumes"] = {"/unexpected": {}}
    _, result, _ = run(tmp_path, api=api)
    assert result["result"] == "FAIL" and api.network is None


def test_fresh_output_only_and_no_redeployment(tmp_path):
    output = tmp_path / "existing"
    output.mkdir()
    api = FakeDocker()
    with pytest.raises(FileExistsError):
        ri.run_rehearsal(api, BACKEND_IMAGE, NGINX_IMAGE, output, authorized=True)
    assert api.calls == []


@pytest.mark.parametrize("fault", ["same-ip", "premature-cutover", "no-cutover", "status-only", "wrong-nonce",
                                   "cached-http-body", "missing-dns", "dns-never-updated", "candidate-exit"])
def test_false_positive_routes_are_refused(tmp_path, fault):
    api, result, output = run(tmp_path, fault)
    assert result["result"] == "FAIL" and result["fixtures_retained"] is True
    assert not any(call[0] == "remove" for call in api.calls)
    assert len([call for call in api.calls if call[0] == "create"]) == 3
    assert read_json(output / "result.json")["result"] == "FAIL"
    if fault in {"same-ip", "premature-cutover", "dns-never-updated"}:
        history = read_json(output / "routing-attempt" / "journal.json")["history"]
        assert not any(entry["operation"] == "candidate_ingress_reload" for entry in history)


def test_dns_propagation_waits_without_extra_reload_or_redeploy(tmp_path):
    api, result, _ = run(tmp_path, "dns-lag")
    assert result["result"] == "PASS"
    assert result["gates"]["stale_before_reload"]["fresh_requests"] == 7
    assert api.reload_count == 2
    assert len([call for call in api.calls if call[0] == "create"]) == 3


@pytest.mark.parametrize("fault", ["config-drift", "initial-config-drift", "config-test-failure", "reload-failure", "http-error"])
def test_safe_failures_never_log_private_exec_or_http_details(tmp_path, fault):
    api, result, output = run(tmp_path, fault)
    assert result["result"] == "FAIL"
    for path in output.rglob("*.json"):
        assert PRIVATE_DIAGNOSTIC not in path.read_text(encoding="utf-8")
    if fault in {"config-drift", "initial-config-drift", "config-test-failure"}:
        assert not any(call[:1] == ("nginx",) and call[2] == "reload" for call in api.calls)


def test_config_failure_retains_exact_cleanup_and_restores_names_safely(tmp_path):
    api, result, output = run(tmp_path, "config-drift")
    assert result["failure_routing_restoration"]["ok"] is False
    assert api.containers[api.by_role("original")]["Name"] == "/" + ri._names(OWNER)["original"]
    assert ri.cleanup_owned(api, output, confirmed=True)["ok"] is True
    assert not api.containers and api.network is None


@pytest.mark.parametrize("mutation", [
    lambda api: api.containers[api.by_role("original")].update(Image="sha256:" + "c" * 64),
    lambda api: api.containers[api.by_role("proxy")]["Config"]["Labels"].update({ri.REHEARSAL_LABEL: "0" * 16}),
    lambda api: api.containers[api.by_role("original")].update(Name="/production-name"),
    lambda api: api.network.update(Name="production-network"),
    lambda api: api.extra_members.update({"e" * 64: {"Name": "unknown"}}),
    lambda api: api.containers[api.by_role("candidate")]["HostConfig"].update(Devices=[{"PathOnHost": "/dev/nvidia0"}]),
])
def test_cleanup_prevalidates_all_ids_and_refuses_unknown_resources(tmp_path, mutation):
    api, result, output = run(tmp_path, "status-only")
    assert result["result"] == "FAIL"
    mutation(api)
    before = len(api.calls)
    with pytest.raises(Failure):
        ri.cleanup_owned(api, output, confirmed=True)
    assert not any(call[0] in {"stop", "remove", "remove_network"} for call in api.calls[before:])


def test_cleanup_cannot_run_without_explicit_confirmation(tmp_path):
    api = FakeDocker()
    with pytest.raises(Failure, match="confirmation"):
        ri.cleanup_owned(api, tmp_path)
    assert api.calls == []


def test_bodies_have_only_owned_names_and_cpu_runtime():
    names = ri._names(OWNER)
    for role in ("original", "candidate", "proxy"):
        body = ri.fixture_request(BACKEND_IMAGE, OWNER, role, names["net"], "d" * 32)
        host = body["HostConfig"]
        assert host["Runtime"] == "runc" and host["Devices"] == host["DeviceRequests"] == host["Mounts"] == host["Binds"] == []
        assert host["Privileged"] is False and body["Healthcheck"] == {"Test": ["NONE"]}
        assert body["User"] == "65534:65534"
        assert "NVIDIA_VISIBLE_DEVICES=void" in body["Env"]
        assert host["NetworkMode"] == names["net"]
        assert "neo-glm53" not in json.dumps(body) and "neo-k3-ingress" not in json.dumps(body)
    assert b"resolver " not in ri.nginx_config(OWNER) and b"include " not in ri.nginx_config(OWNER)


def test_status_replays_result_without_constructing_docker(tmp_path, monkeypatch, capsys):
    _, result, output = run(tmp_path)
    monkeypatch.setattr(ri, "DockerAPI", lambda *a, **k: pytest.fail("status touched Docker"))
    assert ri.main(["status", "--output-dir", str(output)]) == 0
    assert json.loads(capsys.readouterr().out) == result


@pytest.mark.parametrize("mutation", [
    lambda doc: doc["Config"].update(User="0"),
    lambda doc: doc["Config"].update(Cmd=["unreviewed"]),
    lambda doc: doc["Config"]["Env"].append("NVIDIA_VISIBLE_DEVICES=all"),
    lambda doc: doc["HostConfig"].update(CapAdd=["SYS_ADMIN"]),
    lambda doc: doc["State"].update(StartedAt="outside-restart"),
])
def test_actual_config_and_epoch_drift_refuse_cleanup(tmp_path, mutation):
    api, result, output = run(tmp_path, "status-only")
    assert result["result"] == "FAIL"
    mutation(api.containers[api.by_role("original")])
    before = len(api.calls)
    with pytest.raises(Failure):
        ri.cleanup_owned(api, output, confirmed=True)
    assert not any(call[0] in {"stop", "remove"} for call in api.calls[before:])


def test_inherited_library_search_path_is_cleared_before_start(tmp_path):
    api = FakeDocker()
    api.images[BACKEND_IMAGE]["Config"]["Env"] = ["LD_LIBRARY_PATH=/original/library/path", "LANG=C.UTF-8"]
    api.images[NGINX_IMAGE]["Config"]["Env"] = ["NGINX_VERSION=fixture"]
    _, result, output = run(tmp_path, api=api)
    assert result["result"] == "PASS"
    assert "/original/library/path" not in (output / "ownership.json").read_text(encoding="utf-8")


def test_unknown_image_environment_refused_before_any_creation(tmp_path):
    api = FakeDocker()
    api.images[BACKEND_IMAGE]["Config"]["Env"] = ["LD_SOMETHING_UNREVIEWED=value"]
    _, result, _ = run(tmp_path, api=api)
    assert result["result"] == "FAIL"
    assert not any(call[0] in {"create", "create_network", "start"} for call in api.calls)


@pytest.mark.parametrize("entry", ["LD_PRELOAD=/private.so", "LD_AUDIT=/private.so", "PYTHONPATH=/private", "PYTHONSTARTUP=/private.py", "BASH_ENV=/private.sh"])
def test_image_execution_hooks_refused_before_creation(tmp_path, entry):
    api = FakeDocker()
    api.images[BACKEND_IMAGE]["Config"]["Env"] = [entry]
    _, result, _ = run(tmp_path, api=api)
    assert result["result"] == "FAIL"
    assert not any(call[0] in {"create", "create_network", "start"} for call in api.calls)


def test_inherited_onbuild_is_refused_even_without_fixture_builds(tmp_path):
    api = FakeDocker()
    api.images[BACKEND_IMAGE]["Config"]["OnBuild"] = ["RUN unreviewed"]
    _, result, _ = run(tmp_path, api=api)
    assert result["result"] == "FAIL"
    assert not any(call[0] in {"create", "create_network", "start"} for call in api.calls)


def test_plain_python_image_version_metadata_is_not_execution_hook(tmp_path):
    api = FakeDocker()
    api.images[BACKEND_IMAGE]["Config"]["Env"] = ["PYTHON_VERSION=3.12.0", "PYTHON_SHA256=" + "a" * 64, "GPG_KEY=public-package-fingerprint"]
    _, result, _ = run(tmp_path, api=api)
    assert result["result"] == "PASS"


@pytest.mark.parametrize("role,field,value", [
    ("original", "PortBindings", {"8081/tcp": [{"HostIp": "0.0.0.0", "HostPort": "49155"}]}),
    ("candidate", "PortBindings", {"8081/tcp": [{"HostIp": "127.0.0.1", "HostPort": "49155"}]}),
    ("proxy", "PortBindings", {"8080/tcp": [{"HostIp": "0.0.0.0", "HostPort": "0"}]}),
    ("proxy", "PortBindings", {"8080/tcp": [{"HostIp": "127.0.0.1", "HostPort": "30001"}]}),
    ("original", "SecurityOpt", []), ("candidate", "SecurityOpt", None),
    ("proxy", "SecurityOpt", ["no-new-privileges:false"]),
])
def test_actual_stopped_binding_or_security_drift_refused_before_any_start(tmp_path, role, field, value):
    api = FakeDocker()
    original_inspect = api.inspect
    def inspect_drift(identity):
        document = original_inspect(identity)
        if api.role(identity) == role and document["State"]["Status"] == "created":
            document["HostConfig"][field] = deepcopy(value)
        return document
    api.inspect = inspect_drift
    _, result, _ = run(tmp_path, api=api)
    assert result["result"] == "FAIL"
    assert not any(call[0] == "start" for call in api.calls)
    # The requested/stored create body is still correct; only actual inspection
    # was changed, so this is not merely an assertion about our own builder.
    target = api.containers[api.by_role(role)]
    assert target["HostConfig"][field] != value


@pytest.mark.parametrize("field,value", [
    ("PortBindings", {"8080/tcp": [{"HostIp": "0.0.0.0", "HostPort": "0"}]}),
    ("SecurityOpt", []),
])
def test_corrupted_builder_is_not_the_prestart_validation_oracle(tmp_path, monkeypatch, field, value):
    original_builder = ri.fixture_request
    def broken_builder(image, owner, role, network, nonce=None):
        request = original_builder(image, owner, role, network, nonce)
        if role == "proxy":
            request["HostConfig"][field] = deepcopy(value)
        return request
    monkeypatch.setattr(ri, "fixture_request", broken_builder)
    api, result, _ = run(tmp_path)
    assert result["result"] == "FAIL"
    assert not any(call[0] == "start" for call in api.calls)


class LostCreateDocker(FakeDocker):
    """Only an inert fake: create takes effect, then its response is lost."""
    def __init__(self, target, output, corruption=None):
        super().__init__()
        self.target, self.output, self.corruption = target, output, corruption
        self.durable_intents_seen = []
        self.lookup_failure = None

    def assert_durable_intent(self, role, name, request):
        saved = read_json(self.output / "ownership.json")
        intent = saved["create_intents"][role]
        assert intent["name"] == name and intent["status"] == "pending"
        assert intent["request_sha256"] == ri._digest(request)
        assert intent["contract_sha256"] == ri._digest(intent["contract"])
        assert request["Labels"][ri.INTENT_LABEL] == intent["nonce"]
        assert request["Labels"][ri.REHEARSAL_LABEL] == OWNER
        assert len(intent["nonce"]) == 32 and "id" not in intent
        if role != "network":
            assert intent["image"] == request["Image"]
            assert intent["contract"]["network_id"] == NETWORK
        self.durable_intents_seen.append(role)

    def create_network(self, name, labels):
        self.assert_durable_intent("network", name, {"Name": name, "Labels": labels,
            "Driver": "bridge", "Internal": True, "CheckDuplicate": True})
        identity = super().create_network(name, labels)
        if self.target == "network":
            if self.corruption:
                self.corruption(self, self.network)
            raise DockerError(PRIVATE_DIAGNOSTIC)
        return identity

    def create(self, body, name):
        role = body["Labels"][ri.ROLE_LABEL]
        self.assert_durable_intent(role, name, body)
        identity = super().create(body, name)
        if role == self.target:
            if self.corruption:
                self.corruption(self, self.containers[identity])
            raise DockerError(PRIVATE_DIAGNOSTIC)
        return identity

    def inspect(self, identity):
        if identity == ri._names(OWNER).get(self.target) and self.lookup_failure:
            self.calls.append(("inspect", identity))
            raise DockerError(PRIVATE_DIAGNOSTIC, self.lookup_failure)
        return super().inspect(identity)

    def inspect_network_name(self, name):
        if self.lookup_failure:
            self.calls.append(("inspect_network_name", name))
            raise DockerError(PRIVATE_DIAGNOSTIC, self.lookup_failure)
        return super().inspect_network_name(name)


@pytest.mark.parametrize("target,count", [("network", 0), ("original", 1), ("candidate", 2), ("proxy", 3)])
def test_lost_create_reply_is_durably_captured_for_cleanup_without_start_or_retry(tmp_path, target, count):
    output = tmp_path / "fresh-attempt"
    api = LostCreateDocker(target, output)
    _, result, _ = run(tmp_path, api=api)
    assert result["result"] == "FAIL" and result["fixtures_retained"] is True
    assert result["create_reconciliation"]["captured_for_cleanup"] == [target]
    assert result["create_reconciliation"]["unresolved"] == []
    assert result["create_reconciliation"]["attempt_resumed"] is False
    assert len([call for call in api.calls if call[0] == "create_network"]) == 1
    assert len([call for call in api.calls if call[0] == "create"]) == count
    assert not any(call[0] in {"start", "put_file", "rename", "stop", "remove", "remove_network"} for call in api.calls)
    saved = read_json(output / "ownership.json")
    captured = saved["create_intents"][target]
    assert captured["status"] == "bound" and captured["reconciled_after_uncertainty"] is True
    assert captured["id"] == (NETWORK if target == "network" else api.by_role(target))
    assert len({intent["nonce"] for intent in saved["create_intents"].values()}) == len(saved["create_intents"])
    method, name = ("inspect_network_name", ri._names(OWNER)["net"]) if target == "network" else ("inspect", ri._names(OWNER)[target])
    assert api.calls.count((method, name)) == 1
    assert ri.cleanup_owned(api, output, confirmed=True)["ok"] is True
    assert api.network is None and not api.containers
    assert read_json(output / "result.json")["result"] == "FAIL"
    assert not any(call[0] == "start" for call in api.calls)
    assert PRIVATE_DIAGNOSTIC not in "".join(path.read_text(encoding="utf-8") for path in output.glob("*.json"))


@pytest.mark.parametrize("corruption", [
    lambda api, doc: doc.update(Image="sha256:" + "c" * 64),
    lambda api, doc: doc.update(Name="/unknown-name"),
    lambda api, doc: doc["Config"]["Labels"].update({ri.REHEARSAL_LABEL: "0" * 16}),
    lambda api, doc: doc["Config"]["Labels"].update({ri.INTENT_LABEL: "0" * 32}),
    lambda api, doc: doc["Config"]["Labels"].update({"unexpected": PRIVATE_DIAGNOSTIC}),
    lambda api, doc: doc["Config"].update(Cmd=["unreviewed"]),
    lambda api, doc: doc["Config"].update(Tty=True),
    lambda api, doc: doc["Config"]["Env"].append("LD_PRELOAD=" + PRIVATE_DIAGNOSTIC),
    lambda api, doc: doc["Config"]["ExposedPorts"].update({"9090/tcp": {}}),
    lambda api, doc: doc["State"].update(Status="running", Running=True, StartedAt="outside-start"),
    lambda api, doc: doc["State"].update(StartedAt="outside-prior-start"),
    lambda api, doc: doc["HostConfig"].update(SecurityOpt=[]),
    lambda api, doc: doc["HostConfig"].update(PortBindings={"8081/tcp": [{"HostIp": "0.0.0.0", "HostPort": "8081"}]}),
    lambda api, doc: doc.update(Mounts=[{"Source": "/unknown", "Destination": "/unknown"}]),
    lambda api, doc: doc["NetworkSettings"]["Networks"][ri._names(OWNER)["net"]].update(NetworkID="e" * 64),
    lambda api, doc: doc["NetworkSettings"]["Networks"][ri._names(OWNER)["net"]].update(Aliases=["unknown-backend"]),
    lambda api, doc: doc["NetworkSettings"]["Networks"][ri._names(OWNER)["net"]].update(DriverOpts={"unknown": "value"}),
    lambda api, doc: api.extra_members.update({"e" * 64: {"Name": "unknown"}}),
])
def test_uncertain_container_mismatch_remains_unowned_and_blocks_all_cleanup(tmp_path, corruption):
    output = tmp_path / "fresh-attempt"
    api = LostCreateDocker("candidate", output, corruption)
    _, result, _ = run(tmp_path, api=api)
    assert result["result"] == "FAIL"
    assert result["create_reconciliation"]["unresolved"] == ["candidate"]
    saved = read_json(output / "ownership.json")
    assert "candidate" not in saved["containers"]
    assert saved["create_intents"]["candidate"]["status"] == "unresolved"
    before = len(api.calls)
    with pytest.raises(Failure, match="unresolved create"):
        ri.cleanup_owned(api, output, confirmed=True)
    assert not any(call[0] in {"create", "create_network", "start", "rename", "stop", "remove", "remove_network"}
                   for call in api.calls[before:])
    assert api.by_role("original") in api.containers
    assert not any(call[0] == "start" for call in api.calls)
    assert PRIVATE_DIAGNOSTIC not in (output / "ownership.json").read_text(encoding="utf-8")


@pytest.mark.parametrize("corruption", [
    lambda api, doc: doc.update(Name="unknown-network"),
    lambda api, doc: doc["Labels"].update({ri.REHEARSAL_LABEL: "0" * 16}),
    lambda api, doc: doc["Labels"].update({ri.INTENT_LABEL: "0" * 32}),
    lambda api, doc: doc["Labels"].update({"unexpected": "value"}),
    lambda api, doc: doc.update(Driver="host"),
    lambda api, doc: doc.update(Internal=False),
    lambda api, doc: doc.update(Scope="swarm"),
    lambda api, doc: doc.update(Attachable=True),
    lambda api, doc: doc.update(Ingress=True),
    lambda api, doc: doc.update(EnableIPv6=True),
    lambda api, doc: doc.update(ConfigOnly=True),
    lambda api, doc: doc.update(ConfigFrom={"Network": "unknown-config-network"}),
    lambda api, doc: doc.update(Options={"com.docker.network.bridge.name": "production-bridge"}),
    lambda api, doc: doc["IPAM"].update(Driver="unknown"),
    lambda api, doc: doc["IPAM"].update(Options={"unexpected": "value"}),
    lambda api, doc: doc["IPAM"]["Config"][0].update(IPRange="172.30.1.0/24"),
    lambda api, doc: doc["IPAM"]["Config"][0].update(Subnet="8.8.8.0/24", Gateway="8.8.8.1"),
    lambda api, doc: doc["IPAM"]["Config"][0].update(Subnet="192.0.0.0/24", Gateway="192.0.0.1"),
    lambda api, doc: api.extra_members.update({"e" * 64: {"Name": "unknown"}}),
])
def test_uncertain_network_requires_full_owned_default_network_contract(tmp_path, corruption):
    output = tmp_path / "fresh-attempt"
    api = LostCreateDocker("network", output, corruption)
    _, result, _ = run(tmp_path, api=api)
    assert result["result"] == "FAIL" and result["create_reconciliation"]["unresolved"] == ["network"]
    saved = read_json(output / "ownership.json")
    assert saved["network_id"] is None and saved["create_intents"]["network"]["status"] == "unresolved"
    with pytest.raises(Failure, match="unresolved create"):
        ri.cleanup_owned(api, output, confirmed=True)
    assert not any(call[0] in {"create", "start", "stop", "remove", "remove_network"} for call in api.calls)
    assert api.network is not None


@pytest.mark.parametrize("target", ["network", "original"])
@pytest.mark.parametrize("status", [404, 409, 500])
def test_missing_ambiguous_or_failed_exact_lookup_never_means_safe_absence(tmp_path, target, status):
    output = tmp_path / "fresh-attempt"
    api = LostCreateDocker(target, output)
    api.lookup_failure = status
    _, result, _ = run(tmp_path, api=api)
    assert result["create_reconciliation"]["unresolved"] == [target]
    with pytest.raises(Failure, match="unresolved create"):
        ri.cleanup_owned(api, output, confirmed=True)
    assert not any(call[0] in {"start", "stop", "remove", "remove_network"} for call in api.calls)
    # A later explicit cleanup can retry only the read-only exact-name binding.
    api.lookup_failure = None
    before = len(api.calls)
    proof = ri.cleanup_owned(api, output, confirmed=True)
    assert proof["create_reconciliation"]["captured_for_cleanup"] == [target]
    assert not any(call[0] in {"create", "create_network", "start"} for call in api.calls[before:])
    assert api.network is None and not api.containers


def test_interrupted_binding_ledger_is_reconciled_only_for_explicit_cleanup(tmp_path):
    output = tmp_path / "fresh-attempt"
    api = LostCreateDocker("original", output)
    _, result, _ = run(tmp_path, api=api)
    assert result["result"] == "FAIL"
    saved = read_json(output / "ownership.json")
    saved["containers"].pop("original")
    saved["create_intents"]["original"].update(status="pending")
    saved["create_intents"]["original"].pop("id")
    ri.private_json(output / "ownership.json", saved)
    before = len(api.calls)
    proof = ri.cleanup_owned(api, output, confirmed=True)
    assert proof["create_reconciliation"]["captured_for_cleanup"] == ["original"]
    assert not any(call[0] in {"create", "create_network", "start"} for call in api.calls[before:])
    assert api.network is None and not api.containers


def test_bind_journal_write_failure_still_keeps_durable_create_intent(tmp_path, monkeypatch):
    original_ledger, failed = ri._ledger, []
    def lose_binding(output, ledger):
        if ledger["operation"] == "bound_original" and not failed:
            failed.append(True)
            raise OSError(PRIVATE_DIAGNOSTIC)
        original_ledger(output, ledger)
    monkeypatch.setattr(ri, "_ledger", lose_binding)
    api, result, output = run(tmp_path)
    assert result["result"] == "FAIL"
    assert result["create_reconciliation"]["captured_for_cleanup"] == ["original"]
    assert not any(call[0] == "start" for call in api.calls)
    assert len([call for call in api.calls if call[0] == "create"]) == 1
    assert ri.cleanup_owned(api, output, confirmed=True)["ok"] is True


def test_network_generated_subnet_is_pinned_after_first_verified_binding(tmp_path):
    api, result, output = run(tmp_path, "status-only")
    assert result["result"] == "FAIL"
    api.network["IPAM"]["Config"] = [{"Subnet": "172.31.0.0/16", "Gateway": "172.31.0.1"}]
    before = len(api.calls)
    with pytest.raises(Failure, match="settings drift"):
        ri.cleanup_owned(api, output, confirmed=True)
    assert not any(call[0] in {"stop", "remove", "remove_network"} for call in api.calls[before:])


def test_effective_image_labels_and_exposed_ports_are_exactly_attested(tmp_path):
    api = FakeDocker()
    api.images[NGINX_IMAGE]["Config"].update(Labels={"maintainer": "public-fixture"}, ExposedPorts={"80/tcp": {}})
    _, result, output = run(tmp_path, api=api)
    assert result["result"] == "PASS"
    saved = read_json(output / "ownership.json")
    assert saved["create_intents"]["proxy"]["contract"]["exposed_ports"] == {"80/tcp": {}, "8080/tcp": {}}
    assert "public-fixture" not in (output / "ownership.json").read_text(encoding="utf-8")


@pytest.mark.parametrize("role", ["network", "original"])
def test_broken_bidirectional_intent_binding_cannot_silently_skip_cleanup(tmp_path, role):
    api, result, output = run(tmp_path, "status-only")
    assert result["result"] == "FAIL"
    saved = read_json(output / "ownership.json")
    if role == "network":
        saved["network_id"] = None
    else:
        saved["containers"].pop(role)
    ri.private_json(output / "ownership.json", saved)
    before = len(api.calls)
    with pytest.raises(Failure):
        ri.cleanup_owned(api, output, confirmed=True)
    assert not any(call[0] in {"start", "stop", "remove", "remove_network"} for call in api.calls[before:])


def test_all_three_stopped_fixtures_are_revalidated_before_first_start(tmp_path):
    api = FakeDocker()
    original_create = api.create
    def late_drift(body, name):
        identity = original_create(body, name)
        if body["Labels"][ri.ROLE_LABEL] == "proxy":
            api.containers[api.by_role("original")]["HostConfig"]["SecurityOpt"] = []
        return identity
    api.create = late_drift
    _, result, _ = run(tmp_path, api=api)
    assert result["result"] == "FAIL"
    assert len([call for call in api.calls if call[0] == "create"]) == 3
    assert not any(call[0] == "start" for call in api.calls)
