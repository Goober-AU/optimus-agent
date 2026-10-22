"""Secret-safe Docker create bodies and independent inspect parity.

Only Config.Image changes. Name and immutable container/image IDs are lifecycle
metadata, not launch configuration. Entrypoint, Cmd (including empty arguments),
Env order/duplicates, all accepted Config fields and HostConfig fields are exact.
No CLI string is constructed and no credential or credential digest is evidence.

HostConfig.OomKillDisable is the single documented spelling exception: Docker
Engine 29.6.2 (API 1.55) coerces an explicitly present null to false at create
while an omitted key inspects as null, so the builder omits the key when the
original value is null and parity treats null/false (and absent) as one
effective setting; true must still match true exactly.

Network allowlist: endpoint IDs and automatically allocated addresses/MACs can
change; NetworkID may be empty on an unstarted endpoint, otherwise it must match.
Only exact original full/12-character container IDs are removed from Aliases.
Original user aliases (including the original name if explicitly present) stay.
Only exact candidate name/full/short ID additions are ignored during comparison.
DNSNames may replace the original generated name/ID with candidate name/ID; all
other DNSNames must match when Docker has populated them. Docker Engine 29.6.2
(API 1.55) also populates the renamed container's own Config.Hostname into
DNSNames, so the original's hostname is generated identity material on both
sides of the DNSNames comparison, exactly like name/ID aliases. A populated
DNSNames list is never rewritten again by that engine: it survives stop,
rename-back and restart with the stale previous-name entry intact, so the
lifecycle's own staging names are generated identity material on both sides
too (recognized only by their reserved occ3-v3 construction grammar). Static
IPAMConfig, endpoint DriverOpts/GwPriority/Links and HostConfig.NetworkMode
stay exact. Hostname is preserved, even when it was originally a generated
container ID.

Mount support is deliberately narrow: explicit Linux binds and named local
volumes, with ro/rw and private/rprivate propagation only. Anonymous volumes,
VolumesFrom, tmpfs, relabel/recursive shared modes and unknown mount fields fail
closed. Unsupported Engine Config/HostConfig/endpoint fields fail closed too.
"""
from __future__ import annotations

from copy import deepcopy
import json
import posixpath
import re

PROBE_SCRIPT_PATH = "/opt/occ3-bridge/deploy/v3/artifact_preflight.py"
PROBE_SCHEMA = "occ3-artifact-preflight/v1"
LOADER_PROFILE = "sglang-0.5.21-glm-dsa-native-fp8"
PROBE_ENV_OVERRIDES = {
    "PATH": "/opt/sglang/bin:/usr/local/bin:/usr/bin:/bin",
    "NVIDIA_VISIBLE_DEVICES": "void", "NVIDIA_DRIVER_CAPABILITIES": "",
    "CUDA_VISIBLE_DEVICES": "-1", "HIP_VISIBLE_DEVICES": "-1", "ROCR_VISIBLE_DEVICES": "-1",
    "LD_PRELOAD": "", "LD_AUDIT": "", "LD_LIBRARY_PATH": "", "GCONV_PATH": "", "LOCPATH": "",
    "PYTHONPATH": "", "PYTHONHOME": "", "PYTHONSTARTUP": "", "PYTHONUSERBASE": "", "ENV": "", "BASH_ENV": "",
}
_EXECUTABLE_ENV = frozenset({"LD_PRELOAD", "LD_AUDIT", "GCONV_PATH", "LOCPATH",
    "PYTHONPATH", "PYTHONHOME", "PYTHONSTARTUP", "PYTHONUSERBASE", "ENV", "BASH_ENV"})
_SAFE_INHERITED_ENV = frozenset("""
HOME HOSTNAME LANG LANGUAGE LC_ALL LC_CTYPE TERM DEBIAN_FRONTEND LIBRARY_PATH
HF_HOME HF_HUB_OFFLINE CUDA_HOME CUDA_VERSION NVARCH NCCL_VERSION GDRCOPY_HOME
FLASHINFER_VERSION SGLANG_RUST_BUILD_MODE SGLANG_BUILD_COMMIT SGLANG_BUILD_URL SGLANG_IMAGE_TAG
PYTHON_VERSION PYTHON_SHA256 PYTHON_PIP_VERSION PYTHON_SETUPTOOLS_VERSION
PYTHON_GET_PIP_URL PYTHON_GET_PIP_SHA256 GPG_KEY
""".split())
# Audited original launch profile. New loader-affecting flags require a new
# source review, not silent best-effort artifact coverage.
_PROBE_FLAGS = frozenset("""
--model-path --model --tokenizer-path --trust-remote-code --served-model-name
--tp-size --tp --context-length --reasoning-parser --tool-call-parser --host --port
--default-chat-template-kwargs --speculative-algorithm --speculative-num-steps
--speculative-eagle-topk --speculative-num-draft-tokens --speculative-adaptive
--speculative-adaptive-config --chunked-prefill-size --max-running-requests
--cuda-graph-max-bs-decode --mem-fraction-static --kv-cache-dtype --api-key
--load-format --revision --tokenizer-revision --tokenizer-mode --log-level
""".split())


class ConfigError(ValueError):
    """A bounded diagnostic containing no captured values."""


_CONFIG_FIELDS = frozenset("""
Hostname Domainname User AttachStdin AttachStdout AttachStderr ExposedPorts Tty
OpenStdin StdinOnce Env Cmd Healthcheck ArgsEscaped Image Volumes WorkingDir
Entrypoint NetworkDisabled MacAddress OnBuild Labels StopSignal StopTimeout Shell
""".split())
_HOST_FIELDS = frozenset("""
Binds ContainerIDFile LogConfig NetworkMode PortBindings RestartPolicy AutoRemove
VolumeDriver VolumesFrom ConsoleSize CapAdd CapDrop CgroupnsMode Dns DnsOptions
DnsSearch ExtraHosts GroupAdd IpcMode Cgroup Links OomScoreAdj PidMode Privileged
PublishAllPorts ReadonlyRootfs SecurityOpt UTSMode UsernsMode ShmSize Runtime
Isolation CpuShares Memory NanoCpus CgroupParent BlkioWeight BlkioWeightDevice
BlkioDeviceReadBps BlkioDeviceWriteBps BlkioDeviceReadIOps BlkioDeviceWriteIOps
CpuPeriod CpuQuota CpuRealtimePeriod CpuRealtimeRuntime CpusetCpus CpusetMems
Devices DeviceCgroupRules DeviceRequests MemoryReservation MemorySwap
MemorySwappiness OomKillDisable PidsLimit Ulimits CpuCount CpuPercent IOMaximumIOps
IOMaximumBandwidth Mounts MaskedPaths ReadonlyPaths Tmpfs Init StorageOpt Sysctls
KernelMemory KernelMemoryTCP Annotations
""".split())
_ENDPOINT_CONFIG = frozenset({"IPAMConfig", "Links", "Aliases", "DriverOpts", "GwPriority"})
_ENDPOINT_RUNTIME = frozenset("""
NetworkID EndpointID Gateway IPAddress MacAddress IPPrefixLen IPv6Gateway
GlobalIPv6Address GlobalIPv6PrefixLen DNSNames
""".split())
_ACTUAL_MOUNT_FIELDS = frozenset({
    "Type", "Name", "Source", "Destination", "Driver", "Mode", "RW", "Propagation",
})
_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}\Z")
_IMAGE_ID = re.compile(r"sha256:[0-9a-f]{64}\Z")
# Docker Engine 29.6.2 (API 1.55) never rewrites an endpoint DNSNames list
# after the rename that populated it: the list survives stop, rename-back and
# restart with the stale previous-name entry intact (2026-10-21 DGX rounds
# 1-3), so a container that was ever renamed carries its rename history in
# DNSNames forever. The lifecycle's own staging names are therefore generated
# identity material on both sides of the DNSNames comparison. The exact
# strings lifecycle.py constructs are prefix + "-candidate"/"-preserved"/
# "-failed" (plus the nonce-bound create spellings prefix + "-candidate-n"
# + 32 hex and prefix + "-candidate-artifact-n" + 32 hex), where the prefix
# is "occ3-v3-rehearsal-" + a 16-hex owner (rehearsal) or "occ3-v3-" + the
# attempt ID (production). That prefix is not part of any inspect document,
# so the names are recognized by this reserved construction grammar alone -
# never by fuzzy matching - and any entry outside the grammar stays exact
# DNS content on both sides.
_LIFECYCLE_STAGING_NAME = re.compile(
    r"occ3-v3-(?:rehearsal-[a-f0-9]{16}|[a-z0-9][a-z0-9-]{7,47})"
    r"-(?:candidate|preserved|failed)(?:-(?:artifact-)?n[a-f0-9]{32})?\Z")


def _fail(code: str) -> None:
    raise ConfigError(code)


def _object(value, code: str) -> dict:
    if not isinstance(value, dict):
        _fail(code)
    return value


def _strings(value, code: str, nullable: bool = True) -> list:
    if value is None and nullable:
        return []
    if not isinstance(value, list) or any(not isinstance(x, str) or "\0" in x for x in value):
        _fail(code)
    return value


def _path(value) -> str:
    if (not isinstance(value, str) or not value.startswith("/") or "\0" in value
            or "\\" in value or posixpath.normpath(value) != value or value.startswith("//")):
        _fail("unsupported_mount_path")
    return value


def _inside(path: str, root: str) -> bool:
    return path == root or path.startswith(root.rstrip("/") + "/")


def _actual_mounts(doc: dict) -> dict:
    """Parse raw top-level inspect Mounts, never NetworkSettings.Mounts."""
    mounts = doc.get("Mounts")
    if not isinstance(mounts, list):
        _fail("actual_mounts_missing")
    out = {}
    for mount in mounts:
        _object(mount, "actual_mount_invalid")
        if set(mount) - _ACTUAL_MOUNT_FIELDS:
            _fail("unsupported_actual_mount_field")
        if not {"Type", "Source", "Destination", "RW"} <= set(mount):
            _fail("actual_mount_incomplete")
        if mount["Type"] not in {"bind", "volume"} or type(mount["RW"]) is not bool:
            _fail("unsupported_actual_mount_type")
        destination = _path(mount["Destination"])
        _path(mount["Source"])
        if destination in out:
            _fail("duplicate_actual_mount_destination")
        if mount.get("Propagation", "") not in {"", "private", "rprivate"}:
            _fail("unsupported_mount_propagation")
        mode = mount.get("Mode", "")
        mode_rw = _mount_mode(mode, mount["Type"])
        if ({"ro", "rw"} & set(mode.split(","))) and mode_rw != mount["RW"]:
            _fail("actual_mount_mode_inconsistent")
        if mount["Type"] == "volume" and (not mount.get("Name") or mount.get("Driver") != "local"):
            _fail("unsupported_volume")
        out[destination] = mount
    return out


def _mount_mode(mode, kind: str) -> bool:
    if not isinstance(mode, str):
        _fail("unsupported_mount_mode")
    parts = mode.split(",") if mode else []
    allowed = {"ro", "rw", "private", "rprivate"} if kind == "bind" else {"ro", "rw", "nocopy"}
    if (any(p not in allowed for p in parts) or len(parts) != len(set(parts))
            or {"ro", "rw"} <= set(parts) or {"private", "rprivate"} <= set(parts)):
        _fail("unsupported_mount_mode")
    return "ro" not in parts


def _declared_mounts(host: dict, config: dict, actual: dict) -> None:
    """Builder-only validation of source declarations against actual inspect."""
    if host.get("VolumesFrom") or host.get("Tmpfs"):
        _fail("unsupported_mount_configuration")
    declarations = {}
    for bind in _strings(host.get("Binds"), "invalid_binds"):
        parts = bind.split(":")
        if len(parts) not in {2, 3}:
            _fail("unsupported_bind")
        source, target = parts[:2]
        kind = "bind" if source.startswith("/") else "volume"
        if kind == "bind":
            _path(source)
        elif not _NAME.fullmatch(source):
            _fail("unsupported_volume")
        target = _path(target)
        mode = parts[2] if len(parts) == 3 else ""
        rw = _mount_mode(mode, kind)
        if target in declarations:
            _fail("duplicate_declared_mount_destination")
        declarations[target] = (kind, source, rw)
    mounts = host.get("Mounts")
    if mounts is not None and not isinstance(mounts, list):
        _fail("invalid_declared_mounts")
    for mount in mounts or []:
        _object(mount, "invalid_declared_mount")
        if set(mount) - {"Type", "Source", "Target", "ReadOnly", "Consistency", "BindOptions", "VolumeOptions"}:
            _fail("unsupported_declared_mount_field")
        kind = mount.get("Type")
        if kind not in {"bind", "volume"}:
            _fail("unsupported_declared_mount_type")
        target = _path(mount.get("Target"))
        source = mount.get("Source")
        if kind == "bind":
            _path(source)
        elif not isinstance(source, str) or not _NAME.fullmatch(source):
            _fail("anonymous_or_unsupported_volume")
        if type(mount.get("ReadOnly", False)) is not bool:
            _fail("invalid_mount_readonly")
        if mount.get("Consistency", "") not in {"", "consistent"}:
            _fail("unsupported_mount_consistency")
        bind_opts = mount.get("BindOptions") or {}
        volume_opts = mount.get("VolumeOptions") or {}
        _object(bind_opts, "invalid_bind_options")
        _object(volume_opts, "invalid_volume_options")
        if kind == "bind":
            if volume_opts or set(bind_opts) - {"Propagation", "NonRecursive", "ReadOnlyNonRecursive", "ReadOnlyForceRecursive", "CreateMountpoint"}:
                _fail("unsupported_bind_options")
            if bind_opts.get("Propagation", "rprivate") not in {"private", "rprivate"}:
                _fail("unsupported_mount_propagation")
            if bind_opts.get("CreateMountpoint"):
                _fail("refuse_host_mountpoint_creation")
            if any(type(v) is not bool for k, v in bind_opts.items() if k != "Propagation"):
                _fail("invalid_bind_options")
        elif bind_opts or set(volume_opts) - {"NoCopy"}:
            _fail("unsupported_volume_options")
        elif "NoCopy" in volume_opts and type(volume_opts["NoCopy"]) is not bool:
            _fail("invalid_volume_options")
        if target in declarations:
            _fail("duplicate_declared_mount_destination")
        declarations[target] = (kind, source, not mount.get("ReadOnly", False))
    if set(declarations) != set(actual):
        _fail("declared_actual_mount_destinations_differ")
    for destination, (kind, source, rw) in declarations.items():
        mount = actual[destination]
        if (mount["Type"] != kind or mount["RW"] != rw
                or (mount["Source"] if kind == "bind" else mount["Name"]) != source):
            _fail("declared_actual_mount_mapping_differs")
    for destination in config.get("Volumes") or {}:
        if destination not in actual:
            _fail("anonymous_image_volume")


def _validate_ports(host: dict, network_settings: dict) -> None:
    bindings = host.get("PortBindings") or {}
    _object(bindings, "invalid_port_bindings")
    for port, entries in bindings.items():
        if not isinstance(port, str) or not re.fullmatch(r"[0-9]+/(tcp|udp|sctp)", port):
            _fail("invalid_container_port")
        if not isinstance(entries, list) or not entries:
            _fail("dynamic_published_ports_unsupported")
        for item in entries:
            if (not isinstance(item, dict) or set(item) - {"HostIp", "HostPort"}
                    or not isinstance(item.get("HostIp", ""), str)):
                _fail("invalid_port_binding")
            value = item.get("HostPort")
            if not isinstance(value, str) or not value.isascii() or not value.isdecimal() or not 1 <= int(value) <= 65535:
                _fail("dynamic_or_invalid_host_port")
    runtime_ports = network_settings.get("Ports")
    if runtime_ports:
        _object(runtime_ports, "invalid_actual_ports")
        published = {key: value for key, value in runtime_ports.items() if value}
        if published != bindings:
            _fail("actual_published_ports_differ")


def _validate_structure(doc: dict) -> tuple[dict, dict, dict]:
    _object(doc, "invalid_inspect")
    config = _object(doc.get("Config"), "missing_config")
    host = _object(doc.get("HostConfig"), "missing_host_config")
    if set(config) - _CONFIG_FIELDS or set(host) - _HOST_FIELDS:
        _fail("unsupported_engine_configuration_field")
    for name in ("Entrypoint", "Cmd", "Env"):
        if name not in config:
            _fail("missing_launch_array")
        _strings(config[name], "invalid_launch_array")
    if host.get("ContainerIDFile") or host.get("AutoRemove") or host.get("Links"):
        _fail("unsupported_host_configuration")
    if host.get("PublishAllPorts"):
        _fail("dynamic_published_ports_unsupported")
    if not isinstance(host.get("NetworkMode"), str) or host["NetworkMode"].startswith("container:"):
        _fail("unsupported_network_mode")
    network_settings = _object(doc.get("NetworkSettings"), "missing_network_settings")
    _validate_ports(host, network_settings)
    networks = _object(network_settings.get("Networks"), "missing_networks")
    for endpoint in networks.values():
        _object(endpoint, "invalid_endpoint")
        if set(endpoint) - _ENDPOINT_CONFIG - _ENDPOINT_RUNTIME:
            _fail("unsupported_endpoint_field")
        _strings(endpoint.get("Aliases"), "invalid_network_aliases")
        _strings(endpoint.get("DNSNames"), "invalid_network_dns_names")
        ipam = endpoint.get("IPAMConfig")
        if ipam is not None:
            _object(ipam, "invalid_ipam_config")
            if set(ipam) - {"IPv4Address", "IPv6Address", "LinkLocalIPs"}:
                _fail("unsupported_ipam_field")
    _declared_mounts(host, config, _actual_mounts(doc))
    return config, host, networks


def _identity_aliases(doc: dict, include_name: bool) -> set[str]:
    identifier = doc.get("Id")
    values = set()
    if isinstance(identifier, str) and re.fullmatch(r"[0-9a-f]{64}", identifier):
        values.update((identifier, identifier[:12]))
    if include_name and isinstance(doc.get("Name"), str):
        values.add(doc["Name"].removeprefix("/"))
    return values


def build_create_request(original_inspect: dict, candidate_image: str, candidate_name: str) -> dict:
    """Return the private POST /containers/create body; name is a query parameter.

    The result contains the original credential and must not be logged, put in
    process argv, or persisted except in an explicitly private journal.
    """
    if not isinstance(candidate_name, str) or not _NAME.fullmatch(candidate_name):
        _fail("invalid_candidate_name")
    if not isinstance(candidate_image, str) or not candidate_image or any(c.isspace() for c in candidate_image) or "\0" in candidate_image:
        _fail("invalid_candidate_image")
    config, host, networks = _validate_structure(original_inspect)
    body = deepcopy(config)
    body["Image"] = candidate_image
    host_body = deepcopy(host)
    if host_body.get("OomKillDisable") is None:
        # Docker Engine 29.6.2 (API 1.55) coerces an explicitly present null
        # OomKillDisable to false at create while an omitted key inspects as
        # null. Copying the original's raw null would therefore produce a
        # candidate whose HostConfig differs from the original. Omit the key
        # so the daemon keeps the original's null semantics; explicit
        # false/true originals keep their value and round-trip exactly.
        host_body.pop("OomKillDisable", None)
    body["HostConfig"] = host_body
    endpoints = {}
    generated_ids = _identity_aliases(original_inspect, include_name=False)
    for name, endpoint in networks.items():
        copied = {key: deepcopy(value) for key, value in endpoint.items() if key in _ENDPOINT_CONFIG}
        if copied.get("Aliases") is not None:
            copied["Aliases"] = [a for a in copied["Aliases"] if a not in generated_ids]
        endpoints[name] = copied
    body["NetworkingConfig"] = {"EndpointsConfig": endpoints}
    return body


def assert_candidate_parity(original_inspect: dict, candidate_inspect: dict, expected_image_id: str) -> dict:
    """Independent raw-inspect check; never calls the request builder.

    Mount records are compared directly BEFORE any source-declaration validation.
    Even a broken/mocked builder or declaration checker cannot hide a missing
    top-level Mounts entry. Errors intentionally contain no captured values.
    """
    _object(original_inspect, "invalid_original_inspect")
    _object(candidate_inspect, "invalid_candidate_inspect")
    if not isinstance(expected_image_id, str) or not _IMAGE_ID.fullmatch(expected_image_id):
        _fail("expected_image_must_be_immutable_id")
    if candidate_inspect.get("Image") != expected_image_id:
        _fail("candidate_image_id_differs")
    # This raw comparison is separate from build_create_request/_declared_mounts.
    left_raw = _actual_mounts(original_inspect)
    right_raw = _actual_mounts(candidate_inspect)
    if set(left_raw) != set(right_raw):
        _fail("actual_mount_destinations_differ")
    for destination in left_raw:
        if left_raw[destination] != right_raw[destination]:
            _fail("actual_mount_record_differs")
    left_config, left_host, left_networks = _validate_structure(original_inspect)
    right_config, right_host, right_networks = _validate_structure(candidate_inspect)
    if set(left_config) != set(right_config):
        _fail("config_field_set_differs")
    for field in left_config:
        if field != "Image" and left_config[field] != right_config[field]:
            _fail("Config." + field + "_differs")
    if left_host != right_host:
        # Docker Engine 29.6.2 (API 1.55) coerces an explicitly present null
        # OomKillDisable to false at create while an omitted key inspects as
        # null, so null, false and absent are one effective setting (the OOM
        # killer stays enabled) and byte-exact parity is unattainable in one
        # direction on that daemon. This field alone tolerates that spelling
        # difference; true must still match true, every other field keeps the
        # exact comparison, and no null is blanket-normalized.
        if not (left_host.get("OomKillDisable") in (None, False)
                and right_host.get("OomKillDisable") in (None, False)):
            _fail("HostConfig_differs")
        if {key: value for key, value in left_host.items() if key != "OomKillDisable"} != \
           {key: value for key, value in right_host.items() if key != "OomKillDisable"}:
            _fail("HostConfig_differs")
    for field in ("Path", "Args"):
        if ((field in original_inspect) != (field in candidate_inspect)
                or original_inspect.get(field) != candidate_inspect.get(field)):
            _fail("effective_launch_differs")
    if set(left_networks) != set(right_networks):
        _fail("network_membership_differs")
    for name in left_networks:
        left, right = left_networks[name], right_networks[name]
        for field in _ENDPOINT_CONFIG - {"Aliases"}:
            if (field in left) != (field in right) or left.get(field) != right.get(field):
                _fail("network_endpoint_configuration_differs")
        original_ids = _identity_aliases(original_inspect, include_name=False)
        candidate_generated = _identity_aliases(candidate_inspect, include_name=True)
        # Docker Engine 29.6.2 (API 1.55) populates the endpoint DNSNames list
        # [current-name, 12-char-id, Config.Hostname] when a RUNNING container
        # is renamed and the populated list survives stop/start, so the
        # original's Config.Hostname is generated identity material on both
        # sides of the DNSNames comparison, exactly like name/ID aliases.
        hostname = left_config.get("Hostname")
        hostname_identity = {hostname} if isinstance(hostname, str) and hostname else set()
        original_dns_identity = _identity_aliases(original_inspect, include_name=True) | hostname_identity
        aliases = [a for a in left.get("Aliases") or [] if a not in original_ids]
        actual_aliases = [a for a in right.get("Aliases") or [] if a not in candidate_generated or a in aliases]
        if sorted(aliases) != sorted(actual_aliases):
            _fail("network_aliases_differ")
        if right.get("NetworkID") and right.get("NetworkID") != left.get("NetworkID"):
            _fail("network_id_differs")
        if right.get("DNSNames"):
            # DNSNames is daemon-generated identity material plus this
            # lifecycle's own staging names, never configured DNS content:
            # a fresh default-bridge endpoint inspects null, renaming a
            # RUNNING container replaces the list with
            # [new-name, 12-char-id, Config.Hostname], and that populated
            # list then survives stop, rename-back and restart unchanged,
            # so a renamed container carries its rename history forever
            # (Docker Engine 29.6.2, API 1.55; 2026-10-21 DGX rounds 1-3).
            # Each side therefore drops its own generated identity and any
            # lifecycle staging name; every other entry must then match
            # exactly in both directions.
            expected_dns = {name for name in left.get("DNSNames") or []
                            if name not in original_dns_identity
                            and not _LIFECYCLE_STAGING_NAME.fullmatch(name)}
            actual_dns = {name for name in right["DNSNames"]
                          if name not in candidate_generated
                          and not _LIFECYCLE_STAGING_NAME.fullmatch(name)}
            # An explicitly retained original name alias is not a generated change.
            actual_dns -= original_dns_identity & set(aliases)
            # The right container's own Hostname is identical to the original's
            # by the exact Config comparison above; it is identity, not DNS
            # content, so it is subtracted from this side as well.
            actual_dns -= hostname_identity
            if actual_dns != expected_dns:
                _fail("network_dns_names_differ")
    return {
        "schema": "occ3-candidate-parity/v1", "result": "PASS",
        "image_id_exact": True, "launch_arrays_exact": True,
        "environment_exact": True, "host_config_exact": True,
        "actual_mounts_exact": True, "actual_mount_count": len(left_raw),
        "network_options_exact": True, "network_count": len(left_networks),
    }


def _launch_args(doc: dict) -> list[str]:
    config = _object(doc.get("Config"), "missing_config")
    return _strings(config.get("Entrypoint"), "invalid_entrypoint") + _strings(config.get("Cmd"), "invalid_cmd")


def _flag(argv: list[str], names: set[str], required: bool = False):
    values = []
    for index, element in enumerate(argv):
        if element in names:
            if index + 1 >= len(argv) or argv[index + 1].startswith("--"):
                _fail("launch_flag_missing_value")
            values.append(argv[index + 1])
        elif element.split("=", 1)[0] in names and "=" in element:
            values.append(element.split("=", 1)[1])
    if len(values) > 1 or (required and not values) or (values and not values[0]):
        _fail("ambiguous_or_missing_launch_flag")
    return values[0] if values else None


def extract_api_key(original_inspect: dict) -> str:
    """Return one exact --api-key value privately, without format assumptions."""
    return _flag(_launch_args(original_inspect), {"--api-key"}, required=True)


def build_artifact_probe_request(original_inspect: dict, candidate_image: str, probe_name: str) -> dict:
    """Non-serving, isolated stdlib probe; path-only spec, never serving argv/env.

    Probe has the same mounts/User/WorkingDir and discretionary access settings.
    It explicitly overrides image healthchecks, GPU visibility, runtime, devices,
    network, restart and IPC/PID sharing. No original serving setting is changed.
    """
    config, host, _ = _validate_structure(original_inspect)
    if not isinstance(probe_name, str) or not _NAME.fullmatch(probe_name):
        _fail("invalid_probe_name")
    if not isinstance(candidate_image, str) or not candidate_image or "\0" in candidate_image:
        _fail("invalid_candidate_image")
    argv = _launch_args(original_inspect)
    if (len(argv) < 3 or posixpath.basename(argv[0]) not in {"python", "python3", "python3.12", "python3.11"}
            or argv[1:3] != ["-m", "sglang.launch_server"]):
        _fail("unsupported_artifact_launch_form")
    argv = argv[3:]
    if any(x.startswith("--") and x.split("=", 1)[0] not in _PROBE_FLAGS for x in argv):
        _fail("unsupported_external_launch_configuration")
    if _flag(argv, {"--speculative-algorithm"}) not in {None, "EAGLE"}:
        _fail("unsupported_probe_speculative_loader")
    if _flag(argv, {"--tokenizer-mode"}) not in {None, "auto"}:
        _fail("unsupported_probe_tokenizer_mode")
    workdir = config.get("WorkingDir") or "/"
    _path(workdir)
    def local_path(value):
        if not isinstance(value, str) or not value or "://" in value or "\0" in value or "\\" in value:
            _fail("unsupported_artifact_path")
        return _path(posixpath.normpath(posixpath.join(workdir, value)))
    model = local_path(_flag(argv, {"--model-path", "--model"}, True))
    tokenizer_value = _flag(argv, {"--tokenizer-path"})
    tokenizer = local_path(tokenizer_value) if tokenizer_value is not None else model
    load_format = _flag(argv, {"--load-format"}) or "auto"
    if load_format not in {"auto", "safetensors"}:
        _fail("unsupported_probe_weight_format")
    if _flag(argv, {"--speculative-draft-model-path"}) is not None:
        _fail("separate_draft_model_probe_unsupported")
    required_json = []
    adaptive = _flag(argv, {"--speculative-adaptive-config"})
    if adaptive is not None:
        required_json.append(local_path(adaptive))
    mounts = _actual_mounts(original_inspect)
    roots = sorted(mounts)
    if not roots or host.get("Privileged"):
        _fail("unsafe_probe_mount_or_privilege")
    for destination, mount in mounts.items():
        for path in (destination, mount["Source"]):
            if path == "/" or any(_inside(path, denied) for denied in ("/dev", "/proc", "/sys", "/run", "/var/run")):
                _fail("unsafe_probe_mount")
        if _inside(PROBE_SCRIPT_PATH, destination):
            _fail("probe_script_shadowed_by_mount")
    if any(not any(_inside(path, root) for root in roots) for path in [model, tokenizer, *required_json]):
        _fail("artifact_not_under_original_mount")
    if host.get("CapAdd") or host.get("DeviceCgroupRules"):
        _fail("unsupported_probe_access_configuration")
    spec = {"schema": PROBE_SCHEMA, "model_path": model, "tokenizer_path": tokenizer,
            "allowed_roots": roots, "required_json": required_json, "load_format": load_format,
            "loader_profile": LOADER_PROFILE}
    probe_host = {key: deepcopy(host[key]) for key in (
        "Binds", "Mounts", "VolumeDriver", "GroupAdd", "UsernsMode", "SecurityOpt",
        "CapDrop", "ReadonlyRootfs", "MaskedPaths", "ReadonlyPaths", "Ulimits",
    ) if key in host}
    probe_host.update({"Runtime": "runc", "DeviceRequests": [], "Devices": [],
                       "DeviceCgroupRules": [], "Privileged": False, "CapAdd": [],
                       "NetworkMode": "none", "IpcMode": "private", "PidMode": "",
                       "PortBindings": {}, "PublishAllPorts": False, "AutoRemove": False,
                       "RestartPolicy": {"Name": "no", "MaximumRetryCount": 0}})
    return {
        "Image": candidate_image, "Entrypoint": ["python3", "-I", "-S"],
        "Cmd": [PROBE_SCRIPT_PATH, "--spec", json.dumps(spec, separators=(",", ":"))],
        "User": config.get("User", ""), "WorkingDir": config.get("WorkingDir", ""),
        "Env": probe_environment(),
        "Healthcheck": {"Test": ["NONE"]}, "NetworkDisabled": True,
        "AttachStdin": False, "AttachStdout": True, "AttachStderr": True,
        "OpenStdin": False, "Tty": False, "HostConfig": probe_host,
    }


def assert_artifact_probe_parity(original_inspect: dict, probe_inspect: dict, expected_image_id: str) -> dict:
    """Check the actual unstarted probe independently of its request builder."""
    original_config, original_host, _ = _validate_structure(original_inspect)
    _object(probe_inspect, "invalid_probe_inspect")
    if (not isinstance(expected_image_id, str) or not _IMAGE_ID.fullmatch(expected_image_id)
            or probe_inspect.get("Image") != expected_image_id):
        _fail("probe_image_id_differs")
    original_mounts, probe_mounts = _actual_mounts(original_inspect), _actual_mounts(probe_inspect)
    if original_mounts != probe_mounts:
        _fail("probe_actual_mounts_differ")
    config = _object(probe_inspect.get("Config"), "missing_probe_config")
    host = _object(probe_inspect.get("HostConfig"), "missing_probe_host_config")
    state = _object(probe_inspect.get("State"), "missing_probe_state")
    if state.get("Status") != "created" or state.get("Running") is not False:
        _fail("probe_must_be_unstarted")
    for field in ("User", "WorkingDir"):
        if config.get(field, "") != original_config.get(field, ""):
            _fail("probe_user_or_workdir_differs")
    for field in ("Binds", "Mounts", "VolumeDriver", "GroupAdd", "UsernsMode", "SecurityOpt",
                  "CapDrop", "ReadonlyRootfs", "MaskedPaths", "ReadonlyPaths", "Ulimits"):
        if host.get(field) != original_host.get(field):
            _fail("probe_mount_or_access_configuration_differs")
    if host.get("Runtime") != "runc" or host.get("Privileged") is not False:
        _fail("probe_gpu_runtime_or_privilege")
    if any(host.get(field) for field in ("DeviceRequests", "Devices", "DeviceCgroupRules", "CapAdd",
                                        "PortBindings", "PublishAllPorts", "AutoRemove", "VolumesFrom", "Tmpfs")):
        _fail("probe_device_or_host_exposure")
    if (host.get("NetworkMode") != "none" or host.get("IpcMode") != "private"
            or host.get("PidMode", "") or config.get("NetworkDisabled") is not True):
        _fail("probe_namespace_exposure")
    policy = host.get("RestartPolicy") or {}
    if policy.get("Name") != "no" or policy.get("MaximumRetryCount", 0) != 0:
        _fail("probe_restart_policy_unsafe")
    network_settings = _object(probe_inspect.get("NetworkSettings"), "missing_probe_network_settings")
    networks = network_settings.get("Networks")
    if (not isinstance(networks, dict) or set(networks) - {"none"}
            or any((network_settings.get("Ports") or {}).values())):
        _fail("probe_network_exposure")
    if (_object(config.get("Healthcheck"), "probe_healthcheck_missing").get("Test") != ["NONE"]
            or config.get("Entrypoint") != ["python3", "-I", "-S"]):
        _fail("probe_executable_or_healthcheck_differs")
    cmd = _strings(config.get("Cmd"), "invalid_probe_cmd", nullable=False)
    if len(cmd) != 3 or cmd[:2] != [PROBE_SCRIPT_PATH, "--spec"] or len(cmd[2]) > 65536:
        _fail("probe_command_differs")
    try:
        spec = json.loads(cmd[2])
    except (ValueError, RecursionError):
        _fail("probe_spec_invalid")
    original_argv = _launch_args(original_inspect)
    if (len(original_argv) < 3 or original_argv[1:3] != ["-m", "sglang.launch_server"]
            or posixpath.basename(original_argv[0]) not in {"python", "python3", "python3.12", "python3.11"}):
        _fail("unsupported_artifact_launch_form")
    original_argv = original_argv[3:]
    if any(x.startswith("--") and x.split("=", 1)[0] not in _PROBE_FLAGS for x in original_argv):
        _fail("unsupported_external_launch_configuration")
    if _flag(original_argv, {"--speculative-algorithm"}) not in {None, "EAGLE"}:
        _fail("unsupported_probe_speculative_loader")
    if _flag(original_argv, {"--tokenizer-mode"}) not in {None, "auto"}:
        _fail("unsupported_probe_tokenizer_mode")
    workdir = original_config.get("WorkingDir") or "/"
    model = posixpath.normpath(posixpath.join(workdir, _flag(original_argv, {"--model-path", "--model"}, True)))
    tokenizer = _flag(original_argv, {"--tokenizer-path"})
    tokenizer = posixpath.normpath(posixpath.join(workdir, tokenizer)) if tokenizer else model
    adaptive = _flag(original_argv, {"--speculative-adaptive-config"})
    expected_spec = {
        "schema": PROBE_SCHEMA, "model_path": model, "tokenizer_path": tokenizer,
        "allowed_roots": sorted(original_mounts), "load_format": _flag(original_argv, {"--load-format"}) or "auto",
        "required_json": [posixpath.normpath(posixpath.join(workdir, adaptive))] if adaptive else [],
        "loader_profile": LOADER_PROFILE,
    }
    if spec != expected_spec:
        _fail("probe_spec_not_original_artifacts")
    environment = _strings(config.get("Env"), "invalid_probe_environment", nullable=False)
    actual_environment = _bounded_environment(environment)
    for key, expected in PROBE_ENV_OVERRIDES.items():
        if actual_environment.get(key) != expected:
            _fail("probe_environment_exposure")
    for key, value in actual_environment.items():
        if key not in PROBE_ENV_OVERRIDES:
            _assert_baseline_metadata_env(original_inspect, key, value)
    original_key = extract_api_key(original_inspect)
    if original_key in json.dumps(config):
        _fail("probe_contains_original_credential")
    return {"schema": "occ3-artifact-probe-parity/v1", "result": "PASS",
            "actual_mounts_exact": True, "actual_mount_count": len(probe_mounts),
            "permission_configuration_exact": True, "cpu_only_configuration": True,
            "isolated_stdlib_command_exact": True, "unstarted": True}


def _bounded_environment(values) -> dict:
    values = _strings(values, "invalid_probe_environment")
    if len(values) > 256 or sum(len(item) for item in values) > 131072:
        _fail("probe_environment_unbounded")
    result = {}
    for item in values:
        if "=" not in item:
            _fail("probe_environment_malformed")
        key, value = item.split("=", 1)
        if key in result or not re.fullmatch(r"[A-Z][A-Z0-9_]*", key):
            _fail("probe_environment_ambiguous")
        result[key] = value
    return result


def _assert_baseline_metadata_env(original, key, value):
    if (any(marker in key for marker in ("SECRET", "TOKEN", "PASSWORD", "CREDENTIAL", "API_KEY"))
            or (key not in _SAFE_INHERITED_ENV and not key.startswith(("NV_", "NVIDIA_")))):
        _fail("unsafe_inherited_probe_environment")
    baseline = _strings(_object(original.get("Config"), "missing_config").get("Env"), "invalid_original_environment")
    if [item.split("=", 1)[1] for item in baseline if item.startswith(key + "=")] != [value]:
        _fail("inherited_probe_environment_not_attested")


def assert_probe_image(image_inspect: dict, expected_image_id: str, original_inspect: dict) -> dict:
    """Vetted image metadata BEFORE probe creation; not a binary/source attestation.

    Explicit overrides do not excuse an image with executable startup hooks.
    The baseline-attested library search path is cleared for the stdlib probe.
    Image Entrypoint/Cmd/Healthcheck are overridden and checked independently in
    the actual stopped container. The serving image/configuration is untouched.
    """
    _object(image_inspect, "invalid_probe_image_inspect")
    if (not isinstance(expected_image_id, str) or not _IMAGE_ID.fullmatch(expected_image_id)
            or image_inspect.get("Id") != expected_image_id):
        _fail("probe_image_identity_mismatch")
    config = _object(image_inspect.get("Config"), "missing_probe_image_config")
    if config.get("Volumes") or config.get("OnBuild"):
        _fail("probe_image_has_mount_or_build_inheritance")
    environment = _bounded_environment(config.get("Env"))
    baseline = _strings(_object(original_inspect.get("Config"), "missing_config").get("Env"), "invalid_original_environment")
    for key, value in environment.items():
        if key in _EXECUTABLE_ENV and value:
            _fail("probe_image_executable_hook")
        if key == "LD_LIBRARY_PATH" and value:
            if [item.split("=", 1)[1] for item in baseline if item.startswith(key + "=")] != [value]:
                _fail("probe_image_library_path_not_attested")
        elif key not in PROBE_ENV_OVERRIDES:
            _assert_baseline_metadata_env(original_inspect, key, value)
    return {"schema": "occ3-probe-image/v1", "result": "PASS", "image_id_exact": True,
            "no_inherited_mounts_or_onbuild": True, "environment_vetted": True,
            "source_and_binary_attestation_still_required": True}


def probe_environment() -> list[str]:
    """Fresh fixed CPU/stdlib environment overrides; contains no captured values."""
    return [key + "=" + value for key, value in PROBE_ENV_OVERRIDES.items()]
