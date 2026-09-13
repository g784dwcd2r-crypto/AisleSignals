"""Real isolated loopback faults, including slow headers and trickled bodies."""
from contextlib import contextmanager
import json
import socket
import threading
import time

import pytest

from scripts.service_health import HealthState, ProbeResult, probe_service


@contextmanager
def raw_service(responder):
    stopped = threading.Event()
    closed = threading.Event()
    requests = []
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen()
    listener.settimeout(0.1)

    def serve():
        try:
            while not stopped.is_set():
                try:
                    connection, _ = listener.accept()
                except socket.timeout:
                    continue
                with connection:
                    connection.settimeout(1)
                    requests.append(connection.recv(2048))
                    try:
                        responder(connection, stopped)
                        # Detect cancellation releasing the client socket.
                        if connection.recv(1) == b"":
                            closed.set()
                    except (OSError, BrokenPipeError):
                        pass
                return
        finally:
            listener.close()

    worker = threading.Thread(target=serve, daemon=True)
    worker.start()
    try:
        yield listener.getsockname()[1], requests, closed
    finally:
        stopped.set()
        worker.join(timeout=2)
        assert not worker.is_alive(), "Synthetic health server did not stop"


def response(body, *, status=200, headers=b""):
    return (f"HTTP/1.1 {status} Test\r\nContent-Length: {len(body)}\r\n".encode()
            + headers + b"\r\n" + body)


@pytest.mark.parametrize("name,path,body", [
    ("api", "/api/health", {"status": "ok", "mode": "pilot"}),
    ("vision", "/v1/models", {"data": [{"id": "qwen3-vl:4b"}]}),
])
def test_bounded_probe_accepts_expected_response_without_proxy_or_dns(name, path, body, monkeypatch):
    monkeypatch.setenv("http_proxy", "http://127.0.0.1:1")
    monkeypatch.setattr(socket, "getaddrinfo", lambda *_: pytest.fail("Health must use numeric loopback"))
    payload = response(json.dumps(body).encode())
    with raw_service(lambda connection, _: connection.sendall(payload)) as (port, requests, _):
        result = probe_service(name, port, path, "synthetic-private-token")
    assert result.state == "READY"
    assert b"Authorization: Bearer synthetic-private-token\r\n" in requests[0]
    assert "synthetic-private-token" not in repr(result)


@pytest.mark.parametrize("payload,state", [
    (response(b"{}", status=302, headers=b"Location: https://example.invalid/private\r\n"), "UNAVAILABLE"),
    (response(b"{}", status=503), "UNAVAILABLE"),
    (response(b'{"status":"ok","mode":"synthetic-prototype"}'), "INVALID"),
    (response(b'{"data":[{"id":"other-model"}]}'), "INVALID"),
    (response(b"not-json"), "UNAVAILABLE"),
    (response(b"{}", headers=b"Content-Encoding: gzip\r\n"), "UNAVAILABLE"),
    (response(b"{}", headers=b"Content-Length: 2\r\n"), "UNAVAILABLE"),
    (response(b"{}", headers=b"Transfer-Encoding: chunked\r\n"), "UNAVAILABLE"),
    (b"HTTP/1.1 200 OK\r\nContent-Length: 32769\r\n\r\n", "UNAVAILABLE"),
    (b"HTTP/1.1 200 OK\r\nX-Long: " + b"x" * 9000, "UNAVAILABLE"),
])
def test_unhealthy_invalid_redirect_and_oversized_responses_never_ready(payload, state):
    with raw_service(lambda connection, _: connection.sendall(payload)) as (port, _, _):
        result = probe_service("api", port, "/api/health")
    assert result.state == state


@pytest.mark.parametrize("prefix", [b"HTTP/1.1 200 OK\r\nX-Trickle: ",
                                     b"HTTP/1.1 200 OK\r\nContent-Length: 1000\r\n\r\n"])
def test_absolute_deadline_bounds_slow_headers_and_trickled_body(prefix):
    def trickle(connection, stopped):
        connection.sendall(prefix)
        for _ in range(100):
            if stopped.wait(0.02):
                return
            connection.sendall(b" ")
    with raw_service(trickle) as (port, _, _):
        before = time.monotonic()
        result = probe_service("api", port, "/api/health", timeout=0.2)
        elapsed = time.monotonic() - before
    assert result.reason == "DEADLINE_EXCEEDED"
    assert elapsed < 1.5  # The peer's continual progress never extends the 0.2s budget.


def test_stop_cancels_hung_request_and_releases_socket():
    stop = threading.Event()
    with raw_service(lambda *_: None) as (port, _, closed):
        timer = threading.Timer(0.1, stop.set)
        timer.start()
        try:
            before = time.monotonic()
            result = probe_service("vision", port, "/v1/models", timeout=5, stop_event=stop)
            assert time.monotonic() - before < 1.5
            assert result.state == "CANCELLED"
            assert closed.wait(1)
        finally:
            timer.cancel()


def test_health_streak_resets_after_transient_failure_and_ignores_stop():
    state = HealthState()
    assert not state.record(ProbeResult("READY", "EXPECTED_RESPONSE"))
    for _ in range(2):
        assert not state.record(ProbeResult("UNAVAILABLE", "DEADLINE_EXCEEDED"))
    assert state.state == "SUSPECT" and state.consecutive_failures == 2
    assert not state.record(ProbeResult("READY", "EXPECTED_RESPONSE"))
    assert state.state == "READY" and state.consecutive_failures == 0
    assert not state.record(ProbeResult("CANCELLED", "STOP_REQUESTED"))
    assert state.state == "READY"
    for _ in range(2):
        assert not state.record(ProbeResult("INVALID", "UNEXPECTED_RESPONSE"))
    assert state.record(ProbeResult("UNAVAILABLE", "HTTP_NOT_READY"))
    assert state.state == "UNHEALTHY"


@pytest.mark.parametrize("values", [
    {"port": 80}, {"port": True}, {"path": "//example.invalid/"},
    {"path": "/api/health\r\nX: injected"}, {"token": "private\r\nX: injected"},
    {"timeout": float("nan")}, {"timeout": True}, {"timeout": 0},
])
def test_probe_configuration_cannot_redirect_or_inject_headers(values):
    arguments = {"name": "api", "port": 23456, "path": "/api/health", **values}
    with pytest.raises(ValueError):
        probe_service(**arguments)
