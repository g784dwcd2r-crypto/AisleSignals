"""Read-only laptop checks and an opt-in, single-endpoint RTSP metadata probe.

No URL, address, SDP body, environment value or exception message enters reports.
The direct socket transport deliberately does not follow redirects or SDP URLs.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import ipaddress
import os
import platform
from pathlib import Path
import re
import shutil
import socket
import ssl
import sys
import time
from typing import Callable, Mapping
from urllib.parse import urlsplit

MIN_FREE_BYTES = 5_000_000_000
MAX_RESPONSE_BYTES = 65_536
MAX_HEADER_BYTES = 8_192
PROBE_TIMEOUT_SECONDS = 8.0
ALLOWED_NETWORKS = tuple(ipaddress.ip_network(item) for item in (
    "10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "fc00::/7",
))

MESSAGES = {
    "AUTHORISATION_REQUIRED": "An authorised operator must explicitly enable this one-endpoint probe.",
    "INVALID_ENV_NAME": "Use an environment-variable name, never the stream URL, as the argument.",
    "URL_NOT_SET": "The selected environment variable is empty or missing.",
    "INVALID_URL": "The stream URL does not meet the prototype's restricted input format.",
    "UNSUPPORTED_SCHEME": "Only RTSP or RTSPS is supported by this probe.",
    "CREDENTIALS_UNSUPPORTED": "Embedded credentials and query parameters are unsupported; keep camera authentication enabled.",
    "PRIVATE_IP_REQUIRED": "Use an authorised RFC1918 IPv4 or IPv6 unique-local literal address; DNS names are unsupported.",
    "UNSUPPORTED_PORT": "Use port 554, port 322, or a configured port from 1024 to 65535.",
    "UNSUPPORTED_PATH": "The path must be plain ASCII without traversal, percent encoding, playlists or query parameters.",
    "READINESS_BLOCKED": "Resolve the laptop preflight blockers before probing.",
    "PROBE_TIMEOUT": "The endpoint did not complete the bounded metadata request in time.",
    "CONNECTION_FAILED": "The metadata connection could not be established or completed.",
    "TLS_VERIFICATION_FAILED": "The TLS certificate could not be verified; certificate checks were not disabled.",
    "RESPONSE_TOO_LARGE": "The metadata response exceeded the allowed size.",
    "INVALID_RESPONSE": "The endpoint did not return a supported RTSP metadata response.",
    "AUTHENTICATION_REQUIRED": "The camera requires authentication; credential support is deferred. Do not disable authentication.",
    "REDIRECT_REJECTED": "The endpoint requested a redirect; the probe did not follow it.",
    "RTSP_REJECTED": "The camera rejected the metadata request.",
    "NO_VIDEO_DESCRIPTION": "The response did not describe a video stream.",
    "UNEXPECTED_FAILURE": "The readiness operation failed without exposing device details.",
}


class ProbeError(Exception):
    def __init__(self, code: str):
        self.code = code
        super().__init__(MESSAGES[code])


@dataclass(frozen=True)
class Target:
    url: str = field(repr=False)
    address: str = field(repr=False)
    port: int
    secure: bool
    ipv6: bool


def error_result(code: str) -> dict:
    return {"status": "BLOCKED", "code": code, "message": MESSAGES[code],
            "frames_read": 0, "recorded": False, "redirects_followed": 0}


def preflight(
    disk_path: str | Path = ".", *,
    disk_usage: Callable = shutil.disk_usage,
    which: Callable = shutil.which,
    system: Callable = platform.system,
    machine: Callable = platform.machine,
    python_version: tuple | None = None,
) -> dict:
    """Inventory without serial numbers, hostname, home paths or active scanning."""
    system_name = system()
    family = {"Windows": "WINDOWS", "Darwin": "MACOS"}.get(system_name, "UNSUPPORTED")
    architecture = machine().lower()
    if architecture not in {"arm64", "aarch64", "x86_64", "amd64", "x86", "i386", "i686"}:
        architecture = "other"
    version = python_version if python_version is not None else sys.version_info[:3]
    version = tuple(int(part) for part in version[:3])
    checks = [
        {"id": "operating_system", "status": "PASS" if family != "UNSUPPORTED" else "BLOCKED",
         "detail": "Pilot OS family recognised; exact version and architecture acceptance remain pending."
         if family != "UNSUPPORTED" else "Only Windows and macOS belong to the first pilot."},
        {"id": "python", "status": "PASS" if version >= (3, 11, 0) else "BLOCKED",
         "detail": "Developer utility requires Python 3.11 or later; a bundled installer is not shipped."},
    ]
    free_bytes = None
    try:
        free_bytes = int(disk_usage(disk_path).free)
        checks.append({"id": "free_disk", "status": "PASS" if free_bytes >= MIN_FREE_BYTES else "BLOCKED",
                       "detail": "At least 5 GB of host free space is required by the initial guard."})
    except (OSError, ValueError):
        checks.append({"id": "free_disk", "status": "BLOCKED", "detail": "Available disk space could not be read."})
    ffprobe_present = bool(which("ffprobe"))
    checks.extend([
        {"id": "ffprobe", "status": "PASS" if ffprobe_present else "WARNING",
         "detail": "Existing FFprobe found on PATH; binary was not executed or verified." if ffprobe_present
         else "FFprobe is not on PATH; this metadata probe does not require or install it."},
        {"id": "power", "status": "MANUAL_CHECK_REQUIRED",
         "detail": "Sleep, lid closure, sign-out or power loss can stop monitoring. Power settings were not read or changed."},
        {"id": "workload", "status": "NOT_TESTED",
         "detail": "Camera decode, detector CPU/memory, thermal impact and normal pharmacy work require site testing."},
        {"id": "audio", "status": "NOT_TESTED",
         "detail": "Existing speaker playback, permissions and a staff-heard sound test remain unverified."},
    ])
    return {"schema_version": 1, "utility_version": "0.1.0", "mode": "READINESS_ONLY",
            "status": "BLOCKED" if any(c["status"] == "BLOCKED" for c in checks) else "CHECKS_COMPLETE",
            "platform": {"family": family, "architecture": architecture,
                         "python_version": ".".join(str(part) for part in version)},
            "disk": {"free_bytes": free_bytes, "minimum_free_bytes": MIN_FREE_BYTES},
            "ffprobe_available": ffprobe_present, "checks": checks,
            "monitoring_active": False, "production_ready": False, "hardware_purchased": False,
            "network_probe": {"status": "NOT_REQUESTED", "frames_read": 0, "recorded": False}}


def parse_target(url: str) -> Target:
    if not isinstance(url, str) or not url or len(url) > 2048 or any(ord(c) < 33 or ord(c) > 126 for c in url):
        raise ProbeError("INVALID_URL")
    try:
        parts = urlsplit(url)
        if parts.scheme not in {"rtsp", "rtsps"}:
            raise ProbeError("UNSUPPORTED_SCHEME")
        if parts.username is not None or parts.password is not None or "@" in parts.netloc or parts.query:
            raise ProbeError("CREDENTIALS_UNSUPPORTED")
        if not parts.hostname or parts.fragment or "\\" in url or "%" in url:
            raise ProbeError("INVALID_URL")
        if not re.fullmatch(r"(?:\[[A-Fa-f0-9:]+\]|[0-9.]+)(?::[0-9]+)?", parts.netloc):
            raise ProbeError("PRIVATE_IP_REQUIRED")
        address = ipaddress.ip_address(parts.hostname)
        if not any(address in network for network in ALLOWED_NETWORKS if network.version == address.version):
            raise ProbeError("PRIVATE_IP_REQUIRED")
        # IPv4 network and broadcast addresses are not known without the site's netmask.
        # Explicit commissioning, not address validation, decides the permitted camera.
        explicit_port = parts.port
        port = explicit_port if explicit_port is not None else (322 if parts.scheme == "rtsps" else 554)
        if not (port in {322, 554} or 1024 <= port <= 65535):
            raise ProbeError("UNSUPPORTED_PORT")
    except ProbeError:
        raise
    except (ValueError, TypeError):
        raise ProbeError("PRIVATE_IP_REQUIRED") from None
    path = parts.path
    if (not re.fullmatch(r"/[A-Za-z0-9_./~-]*", path or "/")
            or any(piece in {".", ".."} for piece in path.split("/"))
            or path.lower().endswith((".m3u", ".m3u8", ".pls"))):
        raise ProbeError("UNSUPPORTED_PATH")
    return Target(url, str(address), port, parts.scheme == "rtsps", address.version == 6)


def _parse_headers(data: bytes) -> tuple[int, dict[str, str], bytes]:
    marker = data.find(b"\r\n\r\n")
    if marker == -1 or marker > MAX_HEADER_BYTES:
        raise ProbeError("INVALID_RESPONSE")
    try:
        lines = data[:marker].decode("ascii").split("\r\n")
        match = re.fullmatch(r"RTSP/1\.0 ([1-5][0-9]{2})(?: [\x20-\x7e]*)?", lines[0])
        if not match:
            raise ValueError()
        headers: dict[str, str] = {}
        for line in lines[1:]:
            name, value = line.split(":", 1)
            name = name.lower()
            if not re.fullmatch(r"[a-z][a-z0-9-]*", name) or name in headers:
                raise ValueError()
            headers[name] = value.strip()
        if headers.get("cseq") != "1":
            raise ValueError()
        return int(match.group(1)), headers, data[marker + 4:]
    except (ValueError, UnicodeError, IndexError):
        raise ProbeError("INVALID_RESPONSE") from None


def _content_length(headers: dict[str, str]) -> int:
    value = headers.get("content-length", "0")
    if not re.fullmatch(r"[0-9]{1,6}", value):
        raise ProbeError("INVALID_RESPONSE")
    length = int(value)
    if length > MAX_RESPONSE_BYTES - MAX_HEADER_BYTES:
        raise ProbeError("RESPONSE_TOO_LARGE")
    return length


def socket_transport(target: Target, timeout: float, max_bytes: int) -> bytes:
    """One direct-IP DESCRIBE request. No DNS, retries, auth, PLAY or redirects."""
    deadline = time.monotonic() + timeout
    connection = None
    try:
        connection = socket.socket(socket.AF_INET6 if target.ipv6 else socket.AF_INET, socket.SOCK_STREAM)
        connection.settimeout(max(0.001, deadline - time.monotonic()))
        connection.connect((target.address, target.port))
        if target.secure:
            connection.settimeout(max(0.001, deadline - time.monotonic()))
            connection = ssl.create_default_context().wrap_socket(connection, server_hostname=target.address)
        request = (f"DESCRIBE {target.url} RTSP/1.0\r\nCSeq: 1\r\nAccept: application/sdp\r\n"
                   "User-Agent: AisleSignals-Readiness/0.1\r\n\r\n").encode("ascii")
        connection.settimeout(max(0.001, deadline - time.monotonic()))
        connection.sendall(request)
        response = bytearray()
        target_size = None
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise ProbeError("PROBE_TIMEOUT")
            connection.settimeout(remaining)
            chunk = connection.recv(min(4096, max_bytes + 1 - len(response)))
            if not chunk:
                break
            response.extend(chunk)
            if len(response) > max_bytes:
                raise ProbeError("RESPONSE_TOO_LARGE")
            marker = response.find(b"\r\n\r\n")
            if marker == -1 and len(response) > MAX_HEADER_BYTES:
                raise ProbeError("RESPONSE_TOO_LARGE")
            if marker != -1 and target_size is None:
                status, headers, _ = _parse_headers(bytes(response))
                # Failure and redirect headers are sufficient; do not consume a body.
                if status != 200:
                    return bytes(response)
                target_size = marker + 4 + _content_length(headers)
            if target_size is not None and len(response) >= target_size:
                break
        return bytes(response)
    except ssl.SSLCertVerificationError:
        raise ProbeError("TLS_VERIFICATION_FAILED") from None
    except (socket.timeout, TimeoutError):
        raise ProbeError("PROBE_TIMEOUT") from None
    except (OSError, ssl.SSLError):
        raise ProbeError("CONNECTION_FAILED") from None
    finally:
        if connection is not None:
            connection.close()


def probe_environment(
    env_name: str, authorised: bool, *, environment: Mapping[str, str] | None = None,
    transport: Callable = socket_transport,
) -> dict:
    try:
        if not authorised:
            raise ProbeError("AUTHORISATION_REQUIRED")
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,127}", env_name or ""):
            raise ProbeError("INVALID_ENV_NAME")
        url = (os.environ if environment is None else environment).get(env_name)
        if not url:
            raise ProbeError("URL_NOT_SET")
        target = parse_target(url)
        data = transport(target, PROBE_TIMEOUT_SECONDS, MAX_RESPONSE_BYTES)
        if not isinstance(data, bytes):
            raise ProbeError("INVALID_RESPONSE")
        if len(data) > MAX_RESPONSE_BYTES:
            raise ProbeError("RESPONSE_TOO_LARGE")
        status, headers, body = _parse_headers(data)
        if status in {401, 403}:
            raise ProbeError("AUTHENTICATION_REQUIRED")
        if 300 <= status < 400:
            raise ProbeError("REDIRECT_REJECTED")
        if status != 200:
            raise ProbeError("RTSP_REJECTED")
        length = _content_length(headers)
        if len(body) != length or headers.get("content-type", "").split(";", 1)[0].lower() != "application/sdp":
            raise ProbeError("INVALID_RESPONSE")
        if not re.search(rb"(?:^|\r?\n)m=video [0-9]+ [^\r\n]+", body):
            raise ProbeError("NO_VIDEO_DESCRIPTION")
        return {"status": "METADATA_REACHABLE", "protocol": "RTSPS" if target.secure else "RTSP",
                "video_described": True, "frames_read": 0, "recorded": False,
                "redirects_followed": 0, "authentication_tested": False,
                "message": "An endpoint described video. Decode quality, frame freshness and detection remain untested."}
    except ProbeError as error:
        return error_result(error.code)
    except (socket.timeout, TimeoutError):
        return error_result("PROBE_TIMEOUT")
    except Exception:
        # Third-party/platform exceptions can embed camera URLs. Never serialise them.
        return error_result("UNEXPECTED_FAILURE")
