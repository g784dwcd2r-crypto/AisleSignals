"""Bounded loopback health probes; no footage, inference, redirects or process adoption.

The absolute deadline includes connection, headers and body. Cancellation closes
the socket instead of abandoning a worker thread. Endpoint health establishes
responsiveness only; a healthy model catalogue does not prove inference progress.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
import json
import math
import socket
import time

HEALTH_INTERVAL_SECONDS = 2.0
HEALTH_TIMEOUT_SECONDS = 1.0
FAILURES_BEFORE_RECOVERY = 3
MAX_BODY_BYTES = 32_768
MAX_HEADER_BYTES = 8_192


@dataclass(frozen=True)
class ProbeResult:
    state: str
    reason: str
    elapsed_ms: int = 0


@dataclass
class HealthState:
    state: str = "NOT_CHECKED"
    consecutive_failures: int = 0
    last_result: ProbeResult | None = None

    def record(self, result: ProbeResult) -> bool:
        """Return whether a persistent fault needs owned-service recovery."""
        if result.state == "CANCELLED":
            return False
        self.last_result = result
        if result.state == "READY":
            self.consecutive_failures = 0
            self.state = "READY"
        else:
            self.consecutive_failures += 1
            self.state = "UNHEALTHY" if self.consecutive_failures >= FAILURES_BEFORE_RECOVERY else "SUSPECT"
        return self.state == "UNHEALTHY"


def expected_health(name: str, body: object) -> bool:
    if not isinstance(body, dict):
        return False
    if name == "api":
        return body.get("status") == "ok" and body.get("mode") == "pilot"
    if name == "vision":
        models = body.get("data")
        return isinstance(models, list) and any(
            isinstance(model, dict) and model.get("id") == "qwen3-vl:4b" for model in models
        )
    return False


async def _response(port: int, path: str, token: str) -> tuple[int, object]:
    writer = None
    try:
        reader, writer = await asyncio.open_connection(
            "127.0.0.1", port, family=socket.AF_INET, limit=MAX_HEADER_BYTES
        )
        headers = [f"GET {path} HTTP/1.0", f"Host: 127.0.0.1:{port}",
                   "Accept: application/json", "Accept-Encoding: identity", "Connection: close"]
        if token:
            headers.append("Authorization: Bearer " + token)
        writer.write(("\r\n".join(headers) + "\r\n\r\n").encode("ascii"))
        await writer.drain()
        raw_headers = await reader.readuntil(b"\r\n\r\n")
        if len(raw_headers) > MAX_HEADER_BYTES:
            raise ValueError("Oversized health headers")
        lines = raw_headers.decode("ascii").split("\r\n")
        protocol, status, *_ = lines[0].split(" ")
        if protocol not in {"HTTP/1.0", "HTTP/1.1"} or len(status) != 3 or not status.isdigit():
            raise ValueError("Invalid health status")
        if status != "200":
            return int(status), None  # Never follow a Location header.
        fields = {}
        for line in lines[1:]:
            if not line:
                continue
            key, value = line.split(":", 1)
            key = key.strip().lower()
            if key in fields:
                raise ValueError("Ambiguous health headers")
            fields[key] = value.strip()
        if fields.get("content-encoding", "identity").lower() != "identity" or "transfer-encoding" in fields:
            raise ValueError("Unsupported health encoding")
        if "content-length" in fields:
            raw_length = fields["content-length"]
            if not raw_length.isascii() or not raw_length.isdigit():
                raise ValueError("Invalid health size")
            length = int(raw_length)
            if length > MAX_BODY_BYTES:
                raise ValueError("Oversized health response")
            body = await reader.readexactly(length)
        else:
            body = bytearray()
            while True:
                chunk = await reader.read(min(4096, MAX_BODY_BYTES + 1 - len(body)))
                if not chunk:
                    break
                body.extend(chunk)
                if len(body) > MAX_BODY_BYTES:
                    raise ValueError("Oversized health response")
        return 200, json.loads(body)
    finally:
        if writer is not None:
            # close() releases transport resources without an unbounded TLS or
            # peer shutdown wait. The enclosing asyncio.run drains callbacks.
            writer.close()


def probe_service(name: str, port: int, path: str, token: str = "", *,
                  timeout: float = HEALTH_TIMEOUT_SECONDS, stop_event=None) -> ProbeResult:
    if (type(port) is not int or not 1024 <= port <= 65535
            or path not in {"/api/health", "/v1/models"}
            or name not in {"api", "vision"}
            or not isinstance(token, str) or len(token) > 256
            or any(ord(char) < 33 or ord(char) > 126 for char in token)
            or type(timeout) not in {int, float} or not math.isfinite(timeout) or not 0 < timeout <= 5):
        raise ValueError("Unsupported local health probe configuration")
    started = time.monotonic()

    async def bounded():
        if stop_event is not None and stop_event.is_set():
            return "CANCELLED", "STOP_REQUESTED"
        task = asyncio.create_task(_response(port, path, token))
        try:
            while True:
                if stop_event is not None and stop_event.is_set():
                    return "CANCELLED", "STOP_REQUESTED"
                remaining = timeout - (time.monotonic() - started)
                if remaining <= 0:
                    return "UNAVAILABLE", "DEADLINE_EXCEEDED"
                done, _ = await asyncio.wait({task}, timeout=min(0.05, remaining))
                if stop_event is not None and stop_event.is_set():
                    return "CANCELLED", "STOP_REQUESTED"
                if time.monotonic() - started >= timeout:
                    return "UNAVAILABLE", "DEADLINE_EXCEEDED"
                if task in done:
                    status, body = task.result()
                    if status != 200:
                        return "UNAVAILABLE", "HTTP_NOT_READY"
                    return ("READY", "EXPECTED_RESPONSE") if expected_health(name, body) else ("INVALID", "UNEXPECTED_RESPONSE")
        except (OSError, ValueError, UnicodeError, asyncio.IncompleteReadError, asyncio.LimitOverrunError):
            return "UNAVAILABLE", "INVALID_OR_UNREACHABLE"
        finally:
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    state, reason = asyncio.run(bounded())
    return ProbeResult(state, reason, round((time.monotonic() - started) * 1000))
