"""Bounded Docker Engine Unix-socket calls. Request bodies never enter argv/logs."""
from __future__ import annotations

import http.client
import io
import json
import re
import socket
import tarfile
import time
from urllib.parse import quote, urlencode


class DockerError(RuntimeError):
    def __init__(self, operation: str, status: int | None = None):
        self.operation, self.status = operation, status
        super().__init__(f"docker {operation} failed (status {status or 'transport'})")


def container_id(value: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value):
        raise ValueError("full immutable container ID required")
    return value


def image_id(value: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"sha256:[0-9a-f]{64}", value):
        raise ValueError("immutable image ID required")
    return value


class UnixConnection(http.client.HTTPConnection):
    def __init__(self, path: str, timeout: float):
        super().__init__("localhost", timeout=timeout)
        self.path = path

    def connect(self):
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.settimeout(self.timeout)
        self.sock.connect(self.path)


class DockerAPI:
    def __init__(self, socket_path: str = "/var/run/docker.sock", timeout: float = 10,
                 version: str | None = None):
        if version is not None and not re.fullmatch(r"v1\.[0-9]+", version):
            raise ValueError("invalid Docker API version")
        self.socket_path, self.timeout = socket_path, timeout
        self.requested_version, self.version = version, None
        self.deadline = None

    def _exchange(self, method, path, *, body=None, raw=None, timeout=None,
                  limit=4 * 1024 * 1024, content_type="application/json"):
        effective_timeout = timeout or self.timeout
        if self.deadline is not None:
            remaining = self.deadline - time.monotonic()
            if remaining <= 0:
                raise DockerError("deadline")
            effective_timeout = min(effective_timeout, remaining)
        connection = UnixConnection(self.socket_path, effective_timeout)
        operation = method + " " + path.split("?")[0].split("/")[-1]
        try:
            data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
            connection.request(method, path, body=data, headers={"Content-Type": content_type})
            response = connection.getresponse()
            content = response.read(limit + 1)
            if len(content) > limit or not 200 <= response.status < 300:
                raise DockerError(operation, response.status)
            return content
        except (OSError, http.client.HTTPException, ValueError):
            raise DockerError(operation) from None
        finally:
            connection.close()

    def negotiate(self):
        if self.version is None:
            try:
                data = json.loads(self._exchange("GET", "/version", limit=65536))
                value = data["ApiVersion"]
                if not isinstance(value, str) or not re.fullmatch(r"1\.[0-9]+", value):
                    raise ValueError
                current = "v" + value
                if self.requested_version is not None and self.requested_version != current:
                    raise ValueError
                self.version = current
            except (ValueError, KeyError, TypeError, UnicodeError):
                raise DockerError("full-fidelity API negotiation") from None
        return self.version

    def request(self, method, path, **kwargs):
        return self._exchange(method, "/" + self.negotiate() + path, **kwargs)

    def json(self, method, path, **kwargs):
        content = self.request(method, path, **kwargs)
        try:
            return json.loads(content) if content else {}
        except (ValueError, UnicodeError):
            raise DockerError("decode response") from None

    def inspect(self, identity):
        return self.json("GET", "/containers/" + quote(identity, safe="") + "/json")

    def inspect_image(self, identity):
        return self.json("GET", "/images/" + quote(image_id(identity), safe="") + "/json")

    def create(self, body, name):
        result = self.json("POST", "/containers/create?" + urlencode({"name": name}), body=body)
        return container_id(result.get("Id"))

    def start(self, identity):
        self.request("POST", "/containers/" + container_id(identity) + "/start")

    def stop(self, identity, seconds=5):
        self.request("POST", "/containers/" + container_id(identity) + "/stop?" +
                     urlencode({"t": seconds}), timeout=seconds + self.timeout)

    def rename(self, identity, name):
        self.request("POST", "/containers/" + container_id(identity) + "/rename?" +
                     urlencode({"name": name}))

    def remove(self, identity):
        # No force/prefix deletion. Evidence is retained unless this exact ID is stopped.
        self.request("DELETE", "/containers/" + container_id(identity) + "?v=false&force=false")

    def logs(self, identity, limit=65536):
        data = self.request("GET", "/containers/" + container_id(identity) +
                            "/logs?stdout=true&stderr=true&tail=64", limit=limit)
        if data and data[0] in (0, 1, 2) and data[1:4] == b"\x00\x00\x00":
            pieces, offset = [], 0
            while offset < len(data):
                if offset + 8 > len(data):
                    raise DockerError("truncated logs")
                size = int.from_bytes(data[offset + 4:offset + 8], "big")
                if offset + 8 + size > len(data):
                    raise DockerError("truncated logs")
                pieces.append(data[offset + 8:offset + 8 + size])
                offset += 8 + size
            data = b"".join(pieces)
        return data.decode("utf-8", "strict")

    def put_file(self, identity, directory, name, content):
        if "/" in name or name in (".", ".."):
            raise ValueError("archive filename must be a basename")
        stream = io.BytesIO()
        with tarfile.open(fileobj=stream, mode="w") as archive:
            info = tarfile.TarInfo(name)
            info.size, info.mode = len(content), 0o444
            archive.addfile(info, io.BytesIO(content))
        self.request("PUT", "/containers/" + container_id(identity) + "/archive?" +
                     urlencode({"path": directory}), raw=stream.getvalue(),
                     content_type="application/x-tar")

    def inspect_network(self, identity):
        return self.json("GET", "/networks/" + container_id(identity))

    def inspect_network_name(self, name):
        if not isinstance(name, str) or not re.fullmatch(r"occ3-v3-rehearsal-[a-f0-9]{16}-net", name):
            raise ValueError("exact owned disposable network name required")
        document = self.json("GET", "/networks/" + quote(name, safe=""))
        if document.get("Name") != name:
            raise DockerError("exact disposable network lookup")
        container_id(document.get("Id"))
        return document

    def create_network(self, name, labels):
        result = self.json("POST", "/networks/create", body={"Name": name, "Driver": "bridge",
                           "Internal": True, "CheckDuplicate": True, "Labels": labels})
        return container_id(result.get("Id"))

    def remove_network(self, identity):
        self.request("DELETE", "/networks/" + container_id(identity))

    def connect_network(self, identity, container, aliases=()):
        self.request("POST", "/networks/" + container_id(identity) + "/connect",
                     body={"Container": container_id(container), "EndpointConfig": {"Aliases": list(aliases)}})

    def nginx(self, identity, operation, timeout=10):
        end = time.monotonic() + timeout
        def remaining():
            value = end - time.monotonic()
            if value <= 0:
                raise DockerError("nginx exec deadline")
            return value
        commands = {"config": ["nginx", "-T"], "test": ["nginx", "-t"],
                    "reload": ["nginx", "-s", "reload"]}
        if operation not in commands:
            raise ValueError("only fixed nginx validation/reload operations are allowed")
        result = self.json("POST", "/containers/" + container_id(identity) + "/exec", body={
            "AttachStdin": False, "AttachStdout": True, "AttachStderr": True,
            "Tty": False, "Privileged": False, "Cmd": commands[operation]}, timeout=remaining())
        execution = container_id(result.get("Id"))
        data = self.request("POST", "/exec/" + execution + "/start",
                            body={"Detach": False, "Tty": False}, timeout=remaining(), limit=2 * 1024 * 1024)
        info = self.json("GET", "/exec/" + execution + "/json", timeout=remaining())
        if info.get("Running") or type(info.get("ExitCode")) is not int:
            raise DockerError("nginx exec incomplete")
        stdout, stderr, offset = bytearray(), bytearray(), 0
        while offset < len(data):
            if offset + 8 > len(data) or data[offset + 1:offset + 4] != b"\x00\x00\x00":
                raise DockerError("invalid exec stream")
            channel = data[offset]
            size = int.from_bytes(data[offset + 4:offset + 8], "big")
            if channel not in (1, 2) or offset + 8 + size > len(data):
                raise DockerError("invalid exec stream")
            (stdout if channel == 1 else stderr).extend(data[offset + 8:offset + 8 + size])
            offset += 8 + size
        return {"exit_code": info["ExitCode"], "stdout": bytes(stdout), "stderr": bytes(stderr)}

    def build(self, files, tag, timeout=120):
        stream = io.BytesIO()
        with tarfile.open(fileobj=stream, mode="w") as archive:
            for name, content in files.items():
                if name.startswith("/") or ".." in name.split("/"):
                    raise ValueError("unsafe build filename")
                info = tarfile.TarInfo(name)
                info.size, info.mode = len(content), 0o444
                archive.addfile(info, io.BytesIO(content))
        data = self.request("POST", "/build?" + urlencode({"t": tag, "rm": "true",
                            "pull": "false", "networkmode": "none"}), raw=stream.getvalue(),
                            content_type="application/x-tar", timeout=timeout)
        for line in data.splitlines():
            event = json.loads(line)
            if "error" in event or "errorDetail" in event:
                raise DockerError("fixture build")
        result = self.json("GET", "/images/" + quote(tag, safe="") + "/json")
        return image_id(result.get("Id"))
