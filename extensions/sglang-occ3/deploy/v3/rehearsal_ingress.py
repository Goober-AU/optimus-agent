"""Opt-in disposable static-DNS routing proof, not production rollback proof.

Both tiny CPU backends stay running while their names change. This reserves the
old IP and proves nginx's stale address before the SAME lifecycle reload used
by production. No serving container, model mount, GPU, secret, image pull/build,
production route, or supervisor is used. Imports perform no external operations.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
import hashlib
import ipaddress
import json
import math
import os
from pathlib import Path
import re
import secrets
import time
import urllib.error
import urllib.request
from urllib.parse import urlencode

from .docker_api import DockerAPI, DockerError, container_id, image_id
from .lifecycle import (Failure, Journal, REHEARSAL_LABEL, capture_ingress,
                        checked_inspect, is_running, private_json, read_json,
                        refresh_ingress)

SCHEMA = "occ3-static-dns-rehearsal/v1"
LEDGER_SCHEMA = "occ3-static-dns-ownership/v1"
ROLE_LABEL = "occ3.v3.rehearsal.role"
INTENT_LABEL = "occ3.v3.rehearsal.intent"
_CONFIG_FIELDS = ("Image", "User", "WorkingDir", "Entrypoint", "Cmd", "Healthcheck",
                  "Tty", "OpenStdin", "AttachStdin")
_HOST_FIELDS = ("Runtime", "DeviceRequests", "Devices", "Binds", "Mounts", "Privileged",
                "CapDrop", "SecurityOpt", "NetworkMode", "IpcMode", "PidMode", "AutoRemove",
                "RestartPolicy", "PublishAllPorts", "PortBindings")
_EMPTY_HOST_LISTS = frozenset(("DeviceRequests", "Devices", "Binds", "Mounts"))
FIXTURE_TOKEN = "occ3-public-routing-fixture-not-a-credential"
CPU_ENV = ["PATH=/opt/sglang/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
           "NVIDIA_VISIBLE_DEVICES=void", "NVIDIA_DRIVER_CAPABILITIES=",
           "CUDA_VISIBLE_DEVICES=-1", "HIP_VISIBLE_DEVICES=-1", "ROCR_VISIBLE_DEVICES=-1",
           "LD_PRELOAD=", "LD_AUDIT=", "LD_LIBRARY_PATH=", "GCONV_PATH=", "LOCPATH=",
           "PYTHONPATH=", "PYTHONHOME=", "PYTHONSTARTUP=", "PYTHONUSERBASE=", "ENV=", "BASH_ENV="]
_SAFE_IMAGE_ENV = frozenset("""
HOME HOSTNAME LANG LANGUAGE LC_ALL LC_CTYPE TERM DEBIAN_FRONTEND LIBRARY_PATH
HF_HOME HF_HUB_OFFLINE CUDA_HOME CUDA_VERSION NVARCH NCCL_VERSION GDRCOPY_HOME
FLASHINFER_VERSION SGLANG_RUST_BUILD_MODE SGLANG_BUILD_COMMIT SGLANG_BUILD_URL
SGLANG_IMAGE_TAG NGINX_VERSION NJS_VERSION NJS_RELEASE PKG_RELEASE DYNPKG_RELEASE
PYTHON_VERSION PYTHON_SHA256 PYTHON_PIP_VERSION PYTHON_SETUPTOOLS_VERSION
PYTHON_GET_PIP_URL PYTHON_GET_PIP_SHA256 GPG_KEY
""".split())


def _image_environment(document):
    values = document.get("Config", {}).get("Env") or []
    if not isinstance(values, list) or len(values) > 256:
        raise Failure("image environment exceeds safe profile")
    overridden = {item.split("=", 1)[0] for item in CPU_ENV}
    result, seen, size = {}, set(), 0
    for item in values:
        if not isinstance(item, str) or "=" not in item or "\0" in item:
            raise Failure("image environment malformed")
        key, value = item.split("=", 1)
        size += len(item)
        if key in seen or size > 131072:
            raise Failure("image environment ambiguous or unbounded")
        seen.add(key)
        if key in {"LD_PRELOAD", "LD_AUDIT", "GCONV_PATH", "LOCPATH", "PYTHONPATH", "PYTHONHOME",
                   "PYTHONSTARTUP", "PYTHONUSERBASE", "ENV", "BASH_ENV"} and value:
            raise Failure("image has executable environment hook")
        if any(marker in key for marker in ("SECRET", "TOKEN", "PASSWORD", "CREDENTIAL", "API_KEY")):
            raise Failure("image environment outside public metadata profile")
        if key in overridden:
            continue
        if key not in _SAFE_IMAGE_ENV and not key.startswith(("NV_", "NVIDIA_")):
            raise Failure("image environment has unaudited executable settings")
        result[key] = value
    return result
BACKEND = r"""
import json, os, socket
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlsplit, parse_qs
class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass
    def do_GET(self):
        query = parse_qs(urlsplit(self.path).query)
        nonce = query.get('request', [''])[0]
        if (urlsplit(self.path).path != '/probe' or len(nonce) != 32 or
                self.headers.get('X-OCC3-Fixture') != 'occ3-public-routing-fixture-not-a-credential'):
            self.send_error(404)
            return
        payload = json.dumps({'schema': 'occ3-routing-backend/v1',
            'owner': os.environ['OCC3_OWNER'], 'backend': os.environ['OCC3_BACKEND'],
            'nonce': os.environ['OCC3_RESPONSE_NONCE'], 'request_nonce': nonce,
            'route_addresses': sorted({entry[4][0] for entry in socket.getaddrinfo(
                os.environ['OCC3_ROUTE_HOST'], 8081, socket.AF_INET, socket.SOCK_STREAM)})}).encode()
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(payload)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('Connection', 'close')
        self.end_headers()
        self.wfile.write(payload)
HTTPServer(('0.0.0.0', 8081), Handler).serve_forever()
"""


def _names(owner):
    if not isinstance(owner, str) or not re.fullmatch(r"[a-f0-9]{16}", owner):
        raise Failure("invalid disposable owner")
    prefix = "occ3-v3-rehearsal-" + owner
    return {role: prefix + "-" + role for role in (
        "original", "candidate", "preserved", "failed", "proxy", "net")}


def nginx_config(owner):
    """One fixed config, no includes, variables, dynamic resolver or cache."""
    names = _names(owner)
    return ("worker_processes 1;\npid /tmp/occ3-routing.pid;\nerror_log stderr warn;\n"
            "events { worker_connections 64; }\nhttp {\n"
            "  access_log off;\n  client_body_temp_path /tmp/occ3-client;\n"
            "  proxy_temp_path /tmp/occ3-proxy;\n  fastcgi_temp_path /tmp/occ3-fastcgi;\n"
            "  uwsgi_temp_path /tmp/occ3-uwsgi;\n  scgi_temp_path /tmp/occ3-scgi;\n"
            "  server { listen 8080;\n    location = /probe {\n"
            "      proxy_pass http://" + names["original"] + ":8081;\n"
            "      proxy_http_version 1.0;\n      proxy_set_header Connection close;\n"
            "      proxy_set_header X-OCC3-Fixture " + FIXTURE_TOKEN + ";\n"
            "      proxy_connect_timeout 2s;\n      proxy_read_timeout 2s;\n"
            "    }\n  }\n}\n").encode()


def nginx_dump(owner):
    # nginx -T prints this header, the complete file, then one newline. Refuse
    # other versions/extra includes rather than trusting a captured dump alone.
    return b"# configuration file /etc/nginx/nginx.conf:\n" + nginx_config(owner) + b"\n"


def fixture_request(image, owner, role, network, nonce=None):
    image_id(image)
    if role not in ("original", "candidate", "proxy") or network != _names(owner)["net"]:
        raise Failure("invalid disposable role or network")
    body = {
        "Image": image, "User": "65534:65534", "WorkingDir": "/tmp", "Env": list(CPU_ENV),
        "Labels": {REHEARSAL_LABEL: owner, ROLE_LABEL: role}, "Healthcheck": {"Test": ["NONE"]},
        "Tty": False, "OpenStdin": False, "AttachStdin": False,
        "HostConfig": {"Runtime": "runc", "DeviceRequests": [], "Devices": [], "Binds": [],
            "Mounts": [], "Privileged": False, "CapDrop": ["ALL"], "SecurityOpt": ["no-new-privileges"],
            "NetworkMode": network, "IpcMode": "private", "PidMode": "", "AutoRemove": False,
            "RestartPolicy": {"Name": "no", "MaximumRetryCount": 0}, "PublishAllPorts": False,
            "PortBindings": {}},
        "NetworkingConfig": {"EndpointsConfig": {network: {"Aliases": []}}},
    }
    if role == "proxy":
        body.update(Entrypoint=["nginx"], Cmd=["-g", "daemon off;"], ExposedPorts={"8080/tcp": {}})
        body["HostConfig"]["PortBindings"] = {"8080/tcp": [{"HostIp": "127.0.0.1", "HostPort": "0"}]}
    else:
        if not isinstance(nonce, str) or not re.fullmatch(r"[a-f0-9]{32}", nonce):
            raise Failure("invalid fixture response nonce")
        body.update(Entrypoint=["python3", "-I", "-S"], Cmd=["-c", BACKEND], ExposedPorts={"8081/tcp": {}})
        body["Env"] += ["OCC3_OWNER=" + owner, "OCC3_BACKEND=" + role, "OCC3_RESPONSE_NONCE=" + nonce,
                        "OCC3_ROUTE_HOST=" + _names(owner)["original"]]
    return body


def _ledger(output, ledger):
    private_json(Path(output) / "ownership.json", ledger)


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                     allow_nan=False).encode()).hexdigest()


def _environment(values):
    if not isinstance(values, list) or any(not isinstance(value, str) or "=" not in value for value in values):
        raise Failure("invalid effective fixture environment")
    result = dict(value.split("=", 1) for value in values)
    if len(result) != len(values):
        raise Failure("ambiguous effective fixture environment")
    return result


def _container_contract(config, host, network_name, network_id):
    """Digest all create-specified fields; normalize only Engine null empties.

    Independent literal policy in _container is still mandatory. Image-inherited
    labels and exposed ports are merged before recording this expected digest.
    Runtime IDs, addresses, timestamps, and generated endpoint aliases are not
    create-config fields and have separate identity/isolation checks.
    """
    normalized_host = {key: host.get(key) for key in _HOST_FIELDS}
    for key in _EMPTY_HOST_LISTS:
        if normalized_host[key] is None:
            normalized_host[key] = []
    if normalized_host["PortBindings"] is None:
        normalized_host["PortBindings"] = {}
    labels, ports = config.get("Labels") or {}, config.get("ExposedPorts") or {}
    if (not isinstance(labels, dict) or not isinstance(ports, dict)
            or any(not isinstance(key, str) or not isinstance(value, str) for key, value in labels.items())
            or any(not re.fullmatch(r"[0-9]{1,5}/(tcp|udp|sctp)", key) or value != {}
                   for key, value in ports.items())):
        raise Failure("invalid inherited fixture labels or exposed ports")
    return {"config": {key: config.get(key) for key in _CONFIG_FIELDS},
            "environment_sha256": _digest(_environment(config.get("Env"))),
            "labels_sha256": _digest(labels), "exposed_ports": ports,
            "host": normalized_host, "network_name": network_name, "network_id": network_id}


def _record_intent(output, ledger, role, request, image_document=None):
    """Durable exact intent precedes a single create. Never overwrite/retry it."""
    if role in ledger["create_intents"]:
        raise Failure("disposable create already attempted")
    names = _names(ledger["owner"])
    nonce = secrets.token_hex(16)
    request = deepcopy(request)
    request["Labels"][INTENT_LABEL] = nonce
    intent = {"name": names["net" if role == "network" else role], "nonce": nonce,
              "kind": "network" if role == "network" else "container", "status": "pending",
              "request_sha256": _digest(request)}
    if role == "network":
        contract = {"name": names["net"], "labels": request["Labels"], "driver": "bridge",
                    "internal": True, "scope": "local", "attachable": False, "ingress": False,
                    "ipv6": False, "options": {}, "ipam_profile": "automatic-private-ipv4"}
    else:
        config = deepcopy(request)
        image_config = image_document.get("Config", {})
        config["Labels"] = {**(image_config.get("Labels") or {}), **request["Labels"]}
        config["ExposedPorts"] = {**(image_config.get("ExposedPorts") or {}), **request["ExposedPorts"]}
        inherited = ledger["image_environment"]["nginx" if role == "proxy" else "backend"]
        config["Env"] = [key + "=" + value for key, value in
                         {**inherited, **_environment(request["Env"])}.items()]
        contract = _container_contract(config, request["HostConfig"], names["net"], ledger["network_id"])
        intent["image"] = request["Image"]
    intent.update(contract=contract, contract_sha256=_digest(contract))
    ledger["create_intents"][role] = intent
    ledger["operation"] = "create_" + role
    _ledger(output, ledger)
    return request


def _validate_ledger(ledger):
    if not isinstance(ledger, dict) or ledger.get("schema") != LEDGER_SCHEMA:
        raise Failure("invalid ownership ledger")
    names = _names(ledger.get("owner"))
    images = ledger.get("images", {})
    image_id(images.get("backend"))
    image_id(images.get("nginx"))
    image_environments = ledger.get("image_environment", {})
    if not isinstance(image_environments, dict) or set(image_environments) != {"backend", "nginx"}:
        raise Failure("image environment attestation absent")
    for environment in image_environments.values():
        if not isinstance(environment, dict):
            raise Failure("image environment attestation invalid")
        checked = _image_environment({"Config": {"Env": [key + "=" + value for key, value in environment.items()]}})
        if checked != environment:
            raise Failure("unsafe inherited environment in ownership ledger")
    nonces = ledger.get("nonces", {})
    if (not isinstance(nonces, dict) or set(nonces) != {"original", "candidate"}
            or any(not isinstance(value, str) or not re.fullmatch(r"[a-f0-9]{32}", value) for value in nonces.values())
            or nonces["original"] == nonces["candidate"]):
        raise Failure("invalid distinct fixture nonces")
    intents = ledger.get("create_intents")
    if not isinstance(intents, dict) or set(intents) - {"network", "original", "candidate", "proxy"}:
        raise Failure("invalid disposable create intents")
    for role, intent in intents.items():
        if (not isinstance(intent, dict) or intent.get("name") != names["net" if role == "network" else role]
                or intent.get("kind") != ("network" if role == "network" else "container")
                or intent.get("status") not in {"pending", "bound", "unresolved"}
                or not isinstance(intent.get("nonce"), str) or not re.fullmatch(r"[a-f0-9]{32}", intent["nonce"])
                or not isinstance(intent.get("request_sha256"), str)
                or not re.fullmatch(r"[a-f0-9]{64}", intent["request_sha256"])
                or not isinstance(intent.get("contract"), dict)
                or intent.get("contract_sha256") != _digest(intent["contract"])):
            raise Failure("invalid durable create intent")
        if role != "network":
            if (intent.get("image") != images["nginx" if role == "proxy" else "backend"]
                    or intent["contract"].get("network_name") != names["net"]
                    or intent["contract"].get("network_id") != ledger.get("network_id")):
                raise Failure("container intent image or network mismatch")
        if intent["status"] == "bound":
            container_id(intent.get("id"))
            recorded = (ledger.get("network_id") if role == "network"
                        else ledger.get("containers", {}).get(role, {}).get("id"))
            if recorded != intent["id"]:
                raise Failure("bound create intent lacks exact owned identity")
    identities = []
    if not isinstance(ledger.get("containers"), dict) or set(ledger["containers"]) - {"original", "candidate", "proxy"}:
        raise Failure("invalid container ownership ledger")
    for role, entry in ledger["containers"].items():
        if not isinstance(entry, dict):
            raise Failure("invalid owned identity entry")
        identities.append(container_id(entry.get("id")))
        intent = intents.get(role, {})
        if (entry.get("image") != images["nginx" if role == "proxy" else "backend"]
                or intent.get("status") != "bound" or intent.get("id") != entry["id"]):
            raise Failure("owned image or intent ledger mismatch")
    if len(identities) != len(set(identities)):
        raise Failure("duplicate owned identity")
    if ledger.get("network_id") is not None:
        container_id(ledger["network_id"])
        if intents.get("network", {}).get("status") != "bound" or intents["network"].get("id") != ledger["network_id"]:
            raise Failure("owned network intent mismatch")
    return names


def _network_settings(document, ledger):
    """Independent default bridge policy; generated subnet is pinned at bind."""
    names = _names(ledger["owner"])
    intent = ledger["create_intents"]["network"]
    expected_labels = {REHEARSAL_LABEL: ledger["owner"], INTENT_LABEL: intent["nonce"]}
    if (document.get("Id") != ledger["network_id"] or document.get("Name") != names["net"]
            or document.get("Labels") != expected_labels or document.get("Driver") != "bridge"
            or document.get("Internal") is not True or document.get("Scope") != "local"
            or document.get("Attachable") is not False or document.get("Ingress") is not False
            or document.get("EnableIPv6") is not False or document.get("EnableIPv4", True) is not True
            or document.get("ConfigOnly", False) is not False
            or document.get("ConfigFrom") not in (None, {}, {"Network": ""})
            or document.get("Options") not in (None, {})):
        raise Failure("disposable network identity or isolation mismatch")
    ipam = document.get("IPAM", {})
    entries = ipam.get("Config")
    if (ipam.get("Driver") != "default" or ipam.get("Options") not in (None, {})
            or not isinstance(entries, list) or len(entries) != 1
            or not isinstance(entries[0], dict) or set(entries[0]) != {"Subnet", "Gateway"}):
        raise Failure("disposable network IPAM profile mismatch")
    try:
        subnet = ipaddress.IPv4Network(entries[0]["Subnet"], strict=True)
        gateway = ipaddress.IPv4Address(entries[0]["Gateway"])
        private_blocks = ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16")
        if (not any(subnet.subnet_of(ipaddress.IPv4Network(block)) for block in private_blocks)
                or not 8 <= subnet.prefixlen <= 30 or gateway not in subnet
                or gateway in (subnet.network_address, subnet.broadcast_address)):
            raise ValueError
    except (ValueError, TypeError):
        raise Failure("disposable network subnet unsafe") from None
    members = document.get("Containers") or {}
    if not isinstance(members, dict) or set(members) - {entry["id"] for entry in ledger["containers"].values()}:
        raise Failure("unknown endpoint on disposable network")
    return {"name": names["net"], "labels": expected_labels, "driver": "bridge", "internal": True,
            "scope": "local", "attachable": False, "ingress": False, "ipv6": False, "options": {},
            "ipam_profile": "automatic-private-ipv4", "subnet": str(subnet), "gateway": str(gateway)}


def _network(api, ledger):
    _validate_ledger(ledger)
    document = api.inspect_network(container_id(ledger.get("network_id")))
    settings = _network_settings(document, ledger)
    intent = ledger["create_intents"]["network"]
    static = {key: value for key, value in settings.items() if key not in {"subnet", "gateway"}}
    if _digest(static) != intent["contract_sha256"]:
        raise Failure("network create contract mismatch")
    if _digest(settings) != intent.get("settings_sha256"):
        raise Failure("owned network settings drift")
    return document


def _container(api, ledger, role, *, running=False):
    names = _validate_ledger(ledger)
    entry = ledger["containers"][role]
    document = checked_inspect(api, entry["id"], entry["image"])
    allowed = {"original": {names["original"], names["preserved"]},
               "candidate": {names["candidate"], names["original"], names["failed"]},
               "proxy": {names["proxy"]}}[role]
    config, host = document.get("Config", {}), document.get("HostConfig", {})
    labels = config.get("Labels", {})
    intent = ledger["create_intents"][role]
    if (labels.get(INTENT_LABEL) != intent["nonce"]
            or _digest(_container_contract(config, host, names["net"], ledger["network_id"]))
               != intent["contract_sha256"]):
        raise Failure("disposable actual config differs from durable create intent")
    # Independent policy: never use fixture_request as the validation oracle.
    expected_config = {"User": "65534:65534", "WorkingDir": "/tmp", "Healthcheck": {"Test": ["NONE"]},
                       "Entrypoint": ["nginx"] if role == "proxy" else ["python3", "-I", "-S"],
                       "Cmd": ["-g", "daemon off;"] if role == "proxy" else ["-c", BACKEND]}
    if (any(config.get(key) != value for key, value in expected_config.items())
            or any(config.get(key) is not False for key in ("Tty", "OpenStdin", "AttachStdin"))
            or config.get("Volumes") or config.get("OnBuild")):
        raise Failure("disposable executable or access configuration drift")
    environment = config.get("Env")
    if not isinstance(environment, list) or any(not isinstance(value, str) for value in environment):
        raise Failure("disposable environment invalid")
    image_environment = ledger.get("image_environment", {}).get("nginx" if role == "proxy" else "backend", {})
    wanted_environment = {**image_environment, **dict(value.split("=", 1) for value in CPU_ENV)}
    if role != "proxy":
        wanted_environment.update(OCC3_OWNER=ledger["owner"], OCC3_BACKEND=role,
                                  OCC3_RESPONSE_NONCE=ledger["nonces"][role], OCC3_ROUTE_HOST=names["original"])
    if any("=" not in value for value in environment):
        raise Failure("disposable environment malformed")
    actual_environment = dict(value.split("=", 1) for value in environment)
    if len(actual_environment) != len(environment) or actual_environment != wanted_environment:
        raise Failure("disposable environment drift")
    bindings = host.get("PortBindings")
    if bindings is None and role != "proxy":
        bindings = {}
    expected_bindings = ({"8080/tcp": [{"HostIp": "127.0.0.1", "HostPort": "0"}]}
                         if role == "proxy" else {})
    if bindings != expected_bindings:
        raise Failure("disposable host port bindings drift")
    if host.get("SecurityOpt") != ["no-new-privileges"]:
        raise Failure("disposable no-new-privileges policy drift")
    if (host.get("CapAdd") or host.get("CapDrop") != ["ALL"] or host.get("Tmpfs")
            or host.get("IpcMode") != "private" or host.get("PidMode", "") or host.get("UTSMode", "")
            or host.get("AutoRemove") or host.get("PublishAllPorts")
            or (host.get("RestartPolicy") or {}).get("Name") != "no"
            or (host.get("RestartPolicy") or {}).get("MaximumRetryCount", 0) != 0
            or document.get("RestartCount", 0) != 0):
        raise Failure("disposable runtime policy drift")
    if entry.get("epoch") is not None and document.get("State", {}).get("StartedAt") != entry["epoch"]:
        raise Failure("disposable container restarted outside fixture")
    endpoint = document.get("NetworkSettings", {}).get("Networks", {}).get(names["net"], {})
    if (document.get("Name", "").removeprefix("/") not in allowed
            or labels.get(REHEARSAL_LABEL) != ledger["owner"] or labels.get(ROLE_LABEL) != role
            or document.get("Mounts") or any(host.get(key) for key in (
                "Mounts", "Binds", "VolumesFrom", "DeviceRequests", "Devices", "DeviceCgroupRules", "Privileged"))
            or host.get("Runtime") != "runc" or host.get("NetworkMode") != names["net"]
            or set(document.get("NetworkSettings", {}).get("Networks", {})) - {names["net"]}
            or endpoint.get("NetworkID", "") not in ("", ledger["network_id"])):
        raise Failure("disposable container identity or isolation mismatch")
    if role != "proxy" and any((document.get("NetworkSettings", {}).get("Ports") or {}).values()):
        raise Failure("backend published an unexpected host port")
    if running and (not is_running(document) or endpoint.get("NetworkID") != ledger["network_id"]):
        raise Failure("disposable container not running on owned network")
    return document


def _require_created(document, intent, network_name, network_id):
    state = document.get("State", {})
    if (document.get("Name") != "/" + intent["name"] or state.get("Status") != "created"
            or state.get("Running") is not False or any(state.get(key) for key in ("Paused", "Restarting", "Dead", "Pid", "Error"))
            or state.get("StartedAt", "") not in ("", "0001-01-01T00:00:00Z")
            or document.get("RestartCount", 0) != 0):
        raise Failure("create reconciliation requires exact never-started fixture")
    networks = document.get("NetworkSettings", {}).get("Networks", {})
    if set(networks) != {network_name}:
        raise Failure("created fixture network membership mismatch")
    endpoint = networks[network_name]
    allowed_aliases = {intent["name"], document["Id"], document["Id"][:12]}
    if (endpoint.get("NetworkID", "") not in ("", network_id)
            or endpoint.get("IPAMConfig") not in (None, {}) or endpoint.get("Links") not in (None, [])
            or endpoint.get("DriverOpts") not in (None, {}) or endpoint.get("GlobalIPv6Address")
            or any(not isinstance(endpoint.get(key) or [], list)
                   or set(endpoint.get(key) or []) - allowed_aliases for key in ("Aliases", "DNSNames"))):
        raise Failure("created fixture endpoint configuration mismatch")


def _bind_created(api, output, ledger, role, document, *, uncertain=False):
    """Bind only independently verified immutable IDs, never start/retry here."""
    intent = ledger["create_intents"][role]
    if intent["status"] == "bound":
        raise Failure("create intent already bound")
    identity = container_id(document.get("Id"))
    exact_name = intent["name"] if role == "network" else "/" + intent["name"]
    if document.get("Name") != exact_name:
        raise Failure("exact create name lookup mismatch")
    trial = deepcopy(ledger)
    bound = trial["create_intents"][role]
    bound.update(status="bound", id=identity, reconciled_after_uncertainty=bool(uncertain))
    if role == "network":
        trial["network_id"] = identity
        settings = _network_settings(document, trial)
        bound["settings_sha256"] = _digest(settings)
        # Recheck by immutable ID; name results alone never authorize cleanup.
        _network(api, trial)
    else:
        if document.get("Image") != intent["image"]:
            raise Failure("exact create name image mismatch")
        trial["containers"][role] = {"id": identity, "image": intent["image"]}
        _require_created(document, intent, _names(ledger["owner"])["net"], ledger["network_id"])
        actual = _container(api, trial, role)
        _require_created(actual, intent, _names(ledger["owner"])["net"], ledger["network_id"])
        _network(api, trial)
    trial["operation"] = "reconciled_" + role if uncertain else "bound_" + role
    _ledger(output, trial)
    ledger.clear()
    ledger.update(trial)


def _reconcile_intents(api, output, ledger):
    """One exact-name lookup per unresolved intent, with no external mutations.

    Explicit cleanup can replay this read-only step after an interrupted run.
    Missing/ambiguous/foreign occupants remain unresolved, never inferred absent.
    """
    _validate_ledger(ledger)
    captured, unresolved = [], []
    for role in ("network", "original", "candidate", "proxy"):
        intent = ledger["create_intents"].get(role)
        if not intent or intent["status"] == "bound":
            continue
        try:
            document = (api.inspect_network_name(intent["name"]) if role == "network"
                        else api.inspect(intent["name"]))
            _bind_created(api, output, ledger, role, document, uncertain=True)
            captured.append(role)
        except Exception as error:
            ledger["create_intents"][role].update(status="unresolved", error=type(error).__name__)
            ledger["operation"] = "unresolved_" + role
            _ledger(output, ledger)
            unresolved.append(role)
    return {"read_only": True, "captured_for_cleanup": captured, "unresolved": unresolved,
            "attempt_resumed": False, "create_retried": False}


def _address(document, network):
    endpoint = document.get("NetworkSettings", {}).get("Networks", {}).get(network, {})
    value = endpoint.get("IPAddress")
    try:
        address = ipaddress.IPv4Address(value)
    except (ipaddress.AddressValueError, TypeError):
        raise Failure("backend address missing or invalid") from None
    if address.is_unspecified or address.is_loopback or address.is_multicast:
        raise Failure("backend address unsafe")
    return str(address)


def _proxy_url(document):
    ports = document.get("NetworkSettings", {}).get("Ports", {})
    bindings = ports.get("8080/tcp") or []
    if len(bindings) != 1 or bindings[0].get("HostIp") != "127.0.0.1":
        raise Failure("proxy must publish only one loopback port")
    value = bindings[0].get("HostPort", "")
    if not isinstance(value, str) or not value.isascii() or not value.isdecimal() or not 1024 <= int(value) <= 65535:
        raise Failure("proxy published port invalid")
    if any(key != "8080/tcp" and value for key, value in ports.items()):
        raise Failure("unexpected proxy published port")
    return "http://127.0.0.1:" + value


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def request_proxy(url, nonce, timeout):
    """Private bounded response, no proxy-env/redirect/direct-backend fallback."""
    request = urllib.request.Request(url + "/probe?" + urlencode({"request": nonce}),
                                     headers={"Connection": "close", "Cache-Control": "no-store"})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect())
    try:
        with opener.open(request, timeout=timeout) as response:
            raw = response.read(4097)
            if len(raw) > 4096:
                return {"status": response.status, "body": None}
            return {"status": response.status, "body": json.loads(raw)}
    except (OSError, urllib.error.URLError, ValueError):
        return {"status": None, "body": None}


def _observe(api, ledger, url, expected, deadline, *, requester, clock, pause,
             expected_dns, transitions=()):
    started, samples, requests = clock(), 0, 0
    while clock() < deadline:
        proxy = _container(api, ledger, "proxy", running=True)
        _network(api, ledger)
        if _proxy_url(proxy) != url:
            raise Failure("proxy endpoint changed")
        nonce = secrets.token_hex(16)
        response = requester(url, nonce, min(2, max(0.01, deadline - clock())))
        requests += 1
        _container(api, ledger, "proxy", running=True)
        if response.get("status") == 200:
            body = response.get("body")
            if (not isinstance(body, dict) or body.get("schema") != "occ3-routing-backend/v1"
                    or body.get("owner") != ledger["owner"] or body.get("request_nonce") != nonce
                    or body.get("backend") not in ("original", "candidate")
                    or body.get("nonce") != ledger["nonces"][body["backend"]]):
                raise Failure("proxy response did not prove owned backend")
            addresses = body.get("route_addresses")
            if not isinstance(addresses, list) or not 1 <= len(addresses) <= 4:
                raise Failure("fixture DNS evidence missing")
            try:
                if any(str(ipaddress.IPv4Address(value)) != value for value in addresses):
                    raise ValueError
            except (ValueError, TypeError):
                raise Failure("fixture DNS evidence invalid") from None
            if expected is not None and body["backend"] != expected and body["backend"] not in transitions:
                raise Failure("proxy unexpectedly changed backend before reload")
            if (expected is None or body["backend"] == expected) and addresses == [expected_dns]:
                samples += 1
                if samples == 3:
                    return {"ok": True, "backend": body["backend"], "nonce_matched": True,
                            "fresh_requests": requests, "consecutive_matches": samples,
                            "dns_address_exact": expected_dns,
                            "duration_s": clock() - started, "via_owned_nginx": True}
            else:
                samples = 0
        else:
            samples = 0
        pause(min(0.05, max(0, deadline - clock())))
    raise Failure("proxy semantic response deadline")


def cleanup_owned(api, output_dir, *, confirmed=False):
    """Explicit replay-safe cleanup; prevalidate ALL IDs before stopping ANY."""
    if confirmed is not True:
        raise Failure("explicit disposable cleanup confirmation required")
    output = Path(output_dir)
    ledger = read_json(output / "ownership.json")
    _validate_ledger(ledger)
    reconciliation = _reconcile_intents(api, output, ledger)
    if reconciliation["unresolved"]:
        raise Failure("unresolved create intent blocks all cleanup mutations")
    existing = []
    for role in ("proxy", "candidate", "original"):
        if role not in ledger["containers"]:
            continue
        try:
            document = _container(api, ledger, role)
            existing.append((role, document))
        except DockerError as error:
            if error.status != 404:
                raise
    network = None
    if ledger.get("network_id"):
        try:
            network = _network(api, ledger)
        except DockerError as error:
            if error.status != 404:
                raise
    if existing and network is None:
        raise Failure("owned network missing while containers remain")
    for role, document in existing:
        _container(api, ledger, role)
        ledger["operation"] = "cleanup_" + role
        _ledger(output, ledger)
        if document.get("State", {}).get("Running"):
            api.stop(document["Id"], seconds=1)
        if _container(api, ledger, role).get("State", {}).get("Running"):
            raise Failure("owned container did not stop")
        api.remove(document["Id"])
        ledger.setdefault("removed_ids", []).append(document["Id"])
        _ledger(output, ledger)
    if network is not None:
        if _network(api, ledger).get("Containers"):
            raise Failure("owned network still has endpoints")
        api.remove_network(ledger["network_id"])
    ledger.update(operation="cleanup_complete", cleanup_complete=True)
    _ledger(output, ledger)
    proof = {"ok": True, "exact_ids_only": True, "network_removed": network is not None,
             "containers_removed": len(existing), "create_reconciliation": reconciliation}
    private_json(output / "cleanup-result.json", proof)
    return proof


def _restore(api, journal, ledger, url, deadline, *, requester, clock, pause):
    names = _names(ledger["owner"])
    _network(api, ledger)
    original = _container(api, ledger, "original", running=True)
    candidate = _container(api, ledger, "candidate")
    if candidate["Name"] == "/" + names["original"]:
        journal.intent("routing_restore_rename_candidate")
        api.rename(candidate["Id"], names["failed"])
    if original["Name"] == "/" + names["preserved"]:
        journal.intent("routing_restore_rename_original")
        api.rename(original["Id"], names["original"])
    restored = _container(api, ledger, "original", running=True)
    if restored["Name"] != "/" + names["original"]:
        raise Failure("routing original name not restored")
    original_ip = _address(restored, names["net"])
    dns_proof = _observe(api, ledger, url, None, deadline, requester=requester,
                         clock=clock, pause=pause, expected_dns=original_ip)
    proof = refresh_ingress(api, journal, journal.read(), deadline, recovery=True)
    observed = _observe(api, ledger, url, "original", deadline, requester=requester,
                        clock=clock, pause=pause, expected_dns=original_ip, transitions=("candidate",))
    if proof.get("ok") is not True or proof.get("operation") != "recovery_ingress_reload":
        raise Failure("shared recovery reload proof absent")
    return {"reload": proof, "response": observed, "pre_reload_dns": dns_proof, "routing_only": True}


def run_rehearsal(api, backend_image_id, nginx_image_id, output_dir, *, authorized=False,
                  timeout_s=60, owner=None, requester=request_proxy,
                  clock=time.monotonic, pause=time.sleep):
    """Root-run only. Fresh output directory; tests inject FakeDocker/requester."""
    if authorized is not True:
        raise Failure("explicit disposable routing authorization required")
    image_id(backend_image_id)
    image_id(nginx_image_id)
    if (isinstance(timeout_s, bool) or not isinstance(timeout_s, (int, float))
            or not math.isfinite(timeout_s) or not 1 <= timeout_s <= 300):
        raise Failure("invalid disposable deadline")
    owner = owner or secrets.token_hex(8)
    names = _names(owner)
    output = Path(output_dir)
    output.mkdir(mode=0o700, parents=False, exist_ok=False)
    if output.is_symlink():
        raise Failure("unsafe output directory")
    ledger = {"schema": LEDGER_SCHEMA, "owner": owner, "network_id": None, "containers": {}, "create_intents": {},
              "images": {"backend": backend_image_id, "nginx": nginx_image_id},
              "image_environment": {"backend": {}, "nginx": {}},
              "nonces": {"original": secrets.token_hex(16), "candidate": secrets.token_hex(16)},
              "scope": "disposable_static_dns_only", "operation": "admitted"}
    _ledger(output, ledger)
    _validate_ledger(ledger)
    result = {"schema": SCHEMA, "result": "FAIL", "scope": "disposable_static_dns_only",
              "owner": owner, "production_touched": False, "production_rollback_proven": False,
              "live_inference_proven": False, "controller_death_tested": False, "gates": {}}
    started, journal, mutated, url = clock(), None, False, None
    old_deadline = getattr(api, "deadline", None)
    deadline = started + timeout_s
    api.deadline = deadline
    try:
        image_documents = {}
        for role, pinned in (("backend", backend_image_id), ("nginx", nginx_image_id)):
            document = api.inspect_image(pinned)
            if (document.get("Id") != pinned or document.get("Config", {}).get("Volumes")
                    or document.get("Config", {}).get("OnBuild")):
                raise Failure("pinned existing mount-free onbuild-free image required")
            ledger["image_environment"][role] = _image_environment(document)
            image_documents[role] = document
        network_request = _record_intent(output, ledger, "network", {
            "Name": names["net"], "Labels": {REHEARSAL_LABEL: owner}, "Driver": "bridge",
            "Internal": True, "CheckDuplicate": True})
        identity = api.create_network(names["net"], network_request["Labels"])
        _bind_created(api, output, ledger, "network", api.inspect_network(container_id(identity)))
        for role in ("original", "candidate", "proxy"):
            image_role = "nginx" if role == "proxy" else "backend"
            image = ledger["images"][image_role]
            body = fixture_request(image, owner, role, names["net"], ledger["nonces"].get(role))
            body = _record_intent(output, ledger, role, body, image_documents[image_role])
            identity = api.create(body, names[role])
            _bind_created(api, output, ledger, role, api.inspect(container_id(identity)))
        # A complete stopped-fixture barrier precedes the very first start.
        _network(api, ledger)
        for role in ("original", "candidate", "proxy"):
            document = _container(api, ledger, role)
            _require_created(document, ledger["create_intents"][role], names["net"], ledger["network_id"])
        original_id = ledger["containers"]["original"]["id"]
        candidate_id = ledger["containers"]["candidate"]["id"]
        proxy_id = ledger["containers"]["proxy"]["id"]
        api.start(original_id)
        original = _container(api, ledger, "original", running=True)
        if not isinstance(original.get("State", {}).get("StartedAt"), str) or not original["State"]["StartedAt"]:
            raise Failure("original start epoch absent")
        ledger["containers"]["original"]["epoch"] = original["State"]["StartedAt"]
        _ledger(output, ledger)
        original_ip = _address(original, names["net"])
        _container(api, ledger, "proxy")
        api.put_file(proxy_id, "/etc/nginx", "nginx.conf", nginx_config(owner))
        api.start(proxy_id)
        proxy = _container(api, ledger, "proxy", running=True)
        if not isinstance(proxy.get("State", {}).get("StartedAt"), str) or not proxy["State"]["StartedAt"]:
            raise Failure("proxy start epoch absent")
        ledger["containers"]["proxy"]["epoch"] = proxy["State"]["StartedAt"]
        _ledger(output, ledger)
        url = _proxy_url(proxy)
        baseline = _observe(api, ledger, url, "original", deadline,
                            requester=requester, clock=clock, pause=pause, expected_dns=original_ip)
        result["gates"]["baseline_original"] = baseline
        dumped = api.nginx(proxy_id, "config", timeout=min(10, max(0.01, deadline - clock())))
        if dumped.get("exit_code") != 0 or dumped.get("stdout") != nginx_dump(owner):
            raise Failure("nginx dump does not match independently generated fixture")
        digest = hashlib.sha256(nginx_dump(owner)).hexdigest()
        plan = {"attempt_id": "routing-" + owner, "mode": "rehearsal", "rehearsal_ingress": True,
                "rehearsal_owner": owner, "routing_only": True,
                "original_id": original_id, "original_image_id": backend_image_id,
                "candidate_image_id": backend_image_id, "ingress_base_url": url,
                "ingress": {"container_id": proxy_id, "image_id": nginx_image_id,
                            "container_name": names["proxy"], "network_name": names["net"],
                            "network_id": ledger["network_id"], "config_sha256": digest}}
        journal = Journal.create(output / "routing-attempt", plan, original)
        journal.update(candidate_id=candidate_id, phase="ROUTING_ONLY_READY")
        capture_ingress(api, journal)
        private_json(output / "routing-authorization.json", {"approved": True, "scope": SCHEMA,
                     "owner": owner, "original_id": original_id, "candidate_id": candidate_id,
                     "proxy_id": proxy_id, "network_id": ledger["network_id"]}, exclusive=True)
        journal.update(authorization_consumed=True)
        journal.intent("routing_preserve_original_running", mutation_intent=True)
        mutated = True
        api.rename(original_id, names["preserved"])
        _container(api, ledger, "candidate")
        journal.intent("routing_rename_candidate")
        api.rename(candidate_id, names["original"])
        journal.intent("routing_start_candidate")
        api.start(candidate_id)
        candidate = _container(api, ledger, "candidate", running=True)
        if not isinstance(candidate.get("State", {}).get("StartedAt"), str) or not candidate["State"]["StartedAt"]:
            raise Failure("candidate start epoch absent")
        ledger["containers"]["candidate"]["epoch"] = candidate["State"]["StartedAt"]
        _ledger(output, ledger)
        old = _container(api, ledger, "original", running=True)
        candidate_ip = _address(candidate, names["net"])
        if _address(old, names["net"]) != original_ip or candidate_ip == original_ip:
            raise Failure("different stable backend addresses not proven")
        result["gates"]["different_addresses"] = {"ok": True, "original_ip": original_ip,
                                                   "candidate_ip": candidate_ip, "old_backend_still_running": True}
        result["gates"]["stale_before_reload"] = _observe(api, ledger, url, "original", deadline,
            requester=requester, clock=clock, pause=pause, expected_dns=candidate_ip)
        reloaded_at = clock()
        candidate_reload = refresh_ingress(api, journal, journal.read(), deadline)
        if candidate_reload.get("ok") is not True or candidate_reload.get("operation") != "candidate_ingress_reload":
            raise Failure("shared candidate reload proof absent")
        candidate_response = _observe(api, ledger, url, "candidate", deadline,
            requester=requester, clock=clock, pause=pause, expected_dns=candidate_ip, transitions=("original",))
        result["gates"]["candidate_after_shared_reload"] = {
            "ok": True, "reload": candidate_reload, "response": candidate_response,
            "reload_to_verified_s": clock() - reloaded_at}
        restored_at = clock()
        restored = _restore(api, journal, ledger, url, deadline, requester=requester, clock=clock, pause=pause)
        result["gates"]["original_after_shared_restore"] = {"ok": True, **restored,
                                                            "restore_to_verified_s": clock() - restored_at}
        journal.update(phase="ROUTING_ONLY_VERIFIED")
        result["resource_ids"] = {role: entry["id"] for role, entry in ledger["containers"].items()}
        result["network_id"] = ledger["network_id"]
        result["cleanup"] = cleanup_owned(api, output, confirmed=True)
        result["result"] = "PASS"
    except BaseException as error:
        result["error"] = type(error).__name__
        if any(intent["status"] != "bound" for intent in ledger["create_intents"].values()):
            try:
                # A failed/expired create gets a separate bounded read-only
                # capture window, never another create or a fixture start.
                api.deadline = clock() + min(10, timeout_s)
                result["create_reconciliation"] = _reconcile_intents(api, output, ledger)
            except BaseException as reconcile_error:
                result["create_reconciliation"] = {"read_only": True, "ok": False,
                                                     "error": type(reconcile_error).__name__}
        if mutated and journal is not None:
            try:
                recovery_deadline = clock() + min(30, timeout_s)
                api.deadline = recovery_deadline
                result["failure_routing_restoration"] = _restore(
                    api, journal, ledger, url, recovery_deadline,
                    requester=requester, clock=clock, pause=pause)
            except BaseException as recovery_error:
                result["failure_routing_restoration"] = {"ok": False, "error": type(recovery_error).__name__}
        result["fixtures_retained"] = True
    finally:
        api.deadline = old_deadline
        result["duration_s"] = clock() - started
        private_json(output / "result.json", result)
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    run = commands.add_parser("run")
    run.add_argument("--backend-image-id", required=True)
    run.add_argument("--nginx-image-id", required=True)
    run.add_argument("--output-dir", required=True)
    run.add_argument("--execute-disposable-static-dns", action="store_true", required=True)
    run.add_argument("--timeout-s", type=float, default=60)
    run.add_argument("--docker-socket", default="/var/run/docker.sock")
    cleanup = commands.add_parser("cleanup")
    cleanup.add_argument("--output-dir", required=True)
    cleanup.add_argument("--confirm-owned-disposable-cleanup", action="store_true", required=True)
    cleanup.add_argument("--docker-socket", default="/var/run/docker.sock")
    status = commands.add_parser("status")
    status.add_argument("--output-dir", required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "status":
            result = read_json(Path(args.output_dir) / "result.json")
        else:
            if os.name != "posix":
                raise Failure("actual Docker routing rehearsals require Linux")
            api = DockerAPI(args.docker_socket, timeout=5)
            if args.command == "run":
                result = run_rehearsal(api, args.backend_image_id, args.nginx_image_id, args.output_dir,
                                       authorized=args.execute_disposable_static_dns, timeout_s=args.timeout_s)
            else:
                result = cleanup_owned(api, args.output_dir, confirmed=args.confirm_owned_disposable_cleanup)
        print(json.dumps(result, sort_keys=True, separators=(",", ":")))
        return 0 if result.get("result") == "PASS" or result.get("ok") is True else 1
    except Exception as error:
        print(json.dumps({"result": "FAIL", "error": type(error).__name__}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
