"""Actual loopback HTTP tests with synthetic credentials and owned servers."""

from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import threading
from uuid import NAMESPACE_URL, uuid4, uuid5

import pytest

from services.api import cloud_transport as transport


CODE = "x" * 43
TOKEN = "z" * 64
DEVICE = str(uuid4())
SOURCE = str(uuid5(NAMESPACE_URL, "synthetic-cloud-transport-source"))
OTHER_SOURCE = str(uuid5(NAMESPACE_URL, "synthetic-other-transport-source"))
RECEIPT = str(uuid4())
ADMITTED = datetime(2026, 9, 14, 12, 0, 0, 123456, timezone.utc)
DEADLINE = ADMITTED + timedelta(hours=24)


def observation_payload(**changes):
    return {"source_event_id": SOURCE, "event_code": "POSSIBLE_CONCEALMENT",
            "source_label": "Camera 2 of 6 · 3x2 screen grid · local observation",
            "occurred_at": ADMITTED.isoformat().replace("+00:00", "Z"),
            "historical": True, **changes}


def identity():
    return {"device_id": DEVICE, "organisation_id": str(uuid4()),
            "organisation_name": "Synthetic group", "pharmacy_id": str(uuid4()),
            "pharmacy_name": "Synthetic branch", "name": "Synthetic laptop",
            "platform": "WINDOWS", "app_version": "synthetic-1"}


@contextmanager
def endpoint(reply, status=200, headers=None):
    records = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def respond(self):
            length = int(self.headers.get("Content-Length", "0"))
            records.append({"method": self.command, "path": self.path,
                            "headers": dict(self.headers), "body": self.rfile.read(length)})
            raw = reply if isinstance(reply, bytes) else json.dumps(reply).encode()
            self.send_response(status)
            self.send_header("Content-Length", str(len(raw)))
            for name, value in (headers or {}).items():
                self.send_header(name, value)
            self.end_headers()
            try:
                self.wfile.write(raw)
            except (BrokenPipeError, ConnectionResetError):
                pass

        do_GET = respond
        do_POST = respond

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": .01}, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", records
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)
        assert not thread.is_alive()


def client(origin):
    return transport.CloudTransport(origin, allow_local_test=True)


def test_identity_uses_exact_origin_no_body_no_ambient_proxy_or_cookies(monkeypatch):
    result = identity()
    with endpoint(result) as (origin, calls):
        monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:1")
        monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:1")
        monkeypatch.setenv("NO_PROXY", "")
        assert client(origin).identity(TOKEN, expected_device_id=DEVICE) == result
    assert len(calls) == 1
    request = calls[0]
    assert request["method"] == "GET" and request["path"] == "/device-api/identity"
    assert request["body"] == b"" and request["headers"]["Authorization"] == "Bearer " + TOKEN
    assert not any(name.lower() == "cookie" for name in request["headers"])
    assert TOKEN not in repr(client(origin))


def test_no_redirect_credential_forwarding():
    with endpoint(identity()) as (destination, stolen):
        with endpoint({}, 307, {"Location": destination + "/device-api/identity"}) as (origin, calls):
            with pytest.raises(transport.CloudTransportError, match="^REDIRECT_REFUSED$"):
                client(origin).identity(TOKEN, expected_device_id=DEVICE)
        assert len(calls) == 1 and stolen == []


@pytest.mark.parametrize("url", ["http://console.example.test", "http://127.0.0.1:99", "https://user:secret@console.example.test", "https://console.example.test/path", "https://console.example.test?code=secret"])
def test_normal_client_rejects_unsafe_origins(url):
    with pytest.raises(ValueError):
        transport.CloudTransport(url)


@pytest.mark.parametrize("change", [lambda x:x.update(device_id=str(uuid4())), lambda x:x.update(pharmacy_id="invalid"), lambda x:x.update(organisation_name="bad\nname"), lambda x:x.update(platform=[]), lambda x:x.update(private_token=TOKEN)])
def test_server_identity_cannot_change_device_or_smuggle_extra_fields(change):
    result = identity()
    change(result)
    with endpoint(result) as (origin, _):
        with pytest.raises(transport.CloudTransportError) as error:
            client(origin).identity(TOKEN, expected_device_id=DEVICE)
    assert error.value.code in {"IDENTITY_MISMATCH", "INVALID_RESPONSE"}
    assert TOKEN not in str(error.value)


@pytest.mark.parametrize("raw", [b'[]', b'{"x":1,"x":2}', b'{"x":NaN}', b'\xff', b'x' * 65537, b'{' * 2048])
def test_malformed_or_oversized_success_is_not_accepted(raw):
    with endpoint(raw) as (origin, _):
        with pytest.raises(transport.CloudTransportError, match="^INVALID_RESPONSE$"):
            client(origin).identity(TOKEN, expected_device_id=DEVICE)


@pytest.mark.parametrize("status,body,expected", [(401,{"secret":TOKEN},"ACCESS_REVOKED"), (403,{},"ACCESS_REVOKED"), (500,{},"SERVICE_UNAVAILABLE"), (409,{"error":{"code":"HEARTBEAT_CONFLICT","message":TOKEN}},"HEARTBEAT_CONFLICT"), (409,{"error":{"code":TOKEN}},"REQUEST_REFUSED"), (422,{},"VALIDATION_FAILED"), (429,{},"RATE_LIMITED")])
def test_error_categories_never_return_untrusted_messages(status, body, expected):
    with endpoint(body,status) as (origin, _):
        with pytest.raises(transport.CloudTransportError) as error:
            client(origin).identity(TOKEN, expected_device_id=DEVICE)
    assert error.value.code == expected and TOKEN not in str(error.value)


@pytest.mark.parametrize("header,expected", [("1",5),("120",120),("999999",3600),("not-a-date",None),("-1",None)])
def test_rate_limit_hints_are_bounded(header, expected):
    with endpoint({},429,{"Retry-After":header}) as (origin, _):
        with pytest.raises(transport.CloudTransportError) as error:
            client(origin).identity(TOKEN, expected_device_id=DEVICE)
    assert error.value.retry_after == expected


@pytest.mark.parametrize("status,expected", [(200,"NETWORK_UNAVAILABLE"),(409,"REQUEST_REFUSED"),(429,"RATE_LIMITED")])
def test_truncated_chunked_responses_always_have_bounded_categories(status, expected):
    with endpoint(b"10\r\nabc\r\n",status,{"Transfer-Encoding":"chunked"}) as (origin, _):
        with pytest.raises(transport.CloudTransportError) as error:
            client(origin).identity(TOKEN, expected_device_id=DEVICE)
    assert error.value.code == expected


def test_enrolment_has_one_explicit_request_and_exact_response():
    response = {"device_id":DEVICE,"device_token":TOKEN,"heartbeat_interval_seconds":30}
    with endpoint(response,201) as (origin, calls):
        assert client(origin).enrol(CODE,name="Synthetic laptop",platform="WINDOWS") == response
    assert len(calls) == 1
    assert json.loads(calls[0]["body"]) == {"token":CODE,"name":"Synthetic laptop","platform":"WINDOWS","app_version":transport.VERSION}
    assert "Authorization" not in calls[0]["headers"]


def test_uncertain_enrolment_does_not_retry():
    with endpoint(b"broken",201) as (origin, calls):
        with pytest.raises(transport.CloudTransportError, match="INVALID_RESPONSE"):
            client(origin).enrol(CODE,name="Synthetic laptop",platform="MACOS")
        assert len(calls) == 1


def test_heartbeat_keeps_monitoring_unknown_and_exact_retry_payload():
    response = {"ok":True,"server_time":"2026-09-14T01:00:00Z"}
    with endpoint(response) as (origin, calls):
        sender = client(origin)
        assert sender.heartbeat(TOKEN,sequence=10) == response
        assert sender.heartbeat(TOKEN,sequence=10) == response
    assert calls[0]["body"] == calls[1]["body"]
    assert json.loads(calls[0]["body"]) == {"sequence":10,"monitoring_status":"UNKNOWN","camera_count":0,"app_version":transport.VERSION}


@pytest.mark.parametrize("sequence", [True,-1,1.5,9007199254740992])
def test_invalid_sequence_stops_before_network(sequence, monkeypatch):
    monkeypatch.setattr(transport,"build_opener",lambda *_:pytest.fail("must not send"))
    with pytest.raises(ValueError):
        transport.CloudTransport("https://console.example.test").heartbeat(TOKEN,sequence=sequence)


@pytest.mark.parametrize("response", [{"ok":1,"server_time":"2026-09-14T01:00:00Z"},{"ok":True,"server_time":"2026-09-14T01:00:00"},{"ok":True,"server_time":"invalid"},{"ok":True,"server_time":None},{"ok":True,"server_time":"2026-09-14T01:00:00Z","extra":TOKEN}])
def test_malformed_heartbeat_receipt_refuses(response):
    with endpoint(response) as (origin, _):
        with pytest.raises(transport.CloudTransportError, match="INVALID_RESPONSE"):
            client(origin).heartbeat(TOKEN,sequence=10)


@pytest.mark.parametrize("state", ["AVAILABLE", "EXPIRED", "WITHDRAWN"])
def test_observation_exact_immutable_request_and_same_receipt_retry(state):
    response = {"id": RECEIPT, "received": True, "source_state": state}
    body = observation_payload()
    with endpoint(response, 201, {"Set-Cookie": "synthetic=must-not-be-sent"}) as (origin, calls):
        sender = client(origin)
        assert sender.observation(TOKEN, body, expires_at=DEADLINE,
                                  expected_source_event_id=SOURCE) == response
        assert sender.observation(TOKEN, body, expires_at=DEADLINE,
                                  expected_source_event_id=SOURCE, expected_receipt_id=RECEIPT) == response
    assert len(calls) == 2 and calls[0]["body"] == calls[1]["body"]
    assert json.loads(calls[0]["body"]) == {**body, "expires_at": "2026-09-15T12:00:00.123456Z"}
    assert "expires_at" not in body
    for call in calls:
        assert call["path"] == "/device-api/sync/v1/observations" and call["method"] == "POST"
        assert call["headers"]["Authorization"] == "Bearer " + TOKEN
        assert not any(k.lower() == "cookie" for k in call["headers"])


@pytest.mark.parametrize("change", [
    {"historical": False}, {"historical": 1}, {"frames": ["private"]},
    {"organisation_id": DEVICE}, {"notes": "private"}, {"event_code": "THEFT_CONFIRMED"},
    {"event_code": "POSSIBLE_PRODUCT_TAKE"}, {"source_label": "Staff browser window title"},
    {"source_event_id": DEVICE}, {"occurred_at": "2026-09-14T12:00:00"},
    {"expires_at": "2026-09-15T12:00:00Z"},
])
def test_observation_rejects_unmapped_payload_before_network(change, monkeypatch):
    monkeypatch.setattr(transport, "build_opener", lambda *_: pytest.fail("must not send"))
    with pytest.raises(ValueError):
        transport.CloudTransport("https://console.example.test").observation(
            TOKEN, observation_payload(**change), expires_at=DEADLINE, expected_source_event_id=SOURCE)


@pytest.mark.parametrize("deadline", [None, "2026-09-15T12:00:00Z", ADMITTED.replace(tzinfo=None),
                                      ADMITTED, ADMITTED-timedelta(microseconds=1),
                                      DEADLINE+timedelta(microseconds=1)])
def test_invalid_deadline_stops_before_network(deadline, monkeypatch):
    monkeypatch.setattr(transport, "build_opener", lambda *_: pytest.fail("must not send"))
    with pytest.raises(ValueError, match="^INVALID_DEADLINE$"):
        transport.CloudTransport("https://console.example.test").observation(
            TOKEN, observation_payload(), expires_at=deadline, expected_source_event_id=SOURCE)


def test_offset_deadline_is_same_utc_instant_and_does_not_renew_lifetime():
    response = {"id": RECEIPT, "received": True, "source_state": "EXPIRED"}
    with endpoint(response, 201) as (origin, calls):
        client(origin).observation(TOKEN, observation_payload(),
                                   expires_at=DEADLINE.astimezone(timezone(timedelta(hours=1))),
                                   expected_source_event_id=SOURCE)
    assert json.loads(calls[0]["body"])["expires_at"] == "2026-09-15T12:00:00.123456Z"


@pytest.mark.parametrize("response,code", [
    ({"id": RECEIPT, "received": 1, "source_state": "AVAILABLE"}, "INVALID_RESPONSE"),
    ({"id": "bad", "received": True, "source_state": "AVAILABLE"}, "INVALID_RESPONSE"),
    ({"id": "00000000-0000-0000-0000-000000000000", "received": True, "source_state": "AVAILABLE"}, "INVALID_RESPONSE"),
    ({"id": RECEIPT, "received": True, "source_state": []}, "INVALID_RESPONSE"),
    ({"id": RECEIPT, "received": True, "source_state": "MONITORING"}, "INVALID_RESPONSE"),
    ({"id": RECEIPT, "received": True, "source_state": "AVAILABLE", "secret": TOKEN}, "INVALID_RESPONSE"),
    ({"id": DEVICE, "received": True, "source_state": "AVAILABLE"}, "RECEIPT_MISMATCH"),
])
def test_observation_strict_receipt_and_known_identity(response, code):
    with endpoint(response, 201) as (origin, calls):
        with pytest.raises(transport.CloudTransportError, match=f"^{code}$"):
            client(origin).observation(TOKEN, observation_payload(), expires_at=DEADLINE,
                                       expected_source_event_id=SOURCE, expected_receipt_id=RECEIPT)
        assert len(calls) == 1


@pytest.mark.parametrize("reason", sorted(transport.WITHDRAWAL_REASONS))
def test_withdrawal_exact_repeated_request_and_matching_source_receipt(reason):
    body = {"source_event_id": SOURCE, "reason": reason}
    response = {"source_event_id": SOURCE, "withdrawn": True}
    with endpoint(response) as (origin, calls):
        sender = client(origin)
        for _ in range(2):
            assert sender.withdrawal(TOKEN, body, expected_source_event_id=SOURCE) == response
    assert len(calls) == 2 and calls[0]["body"] == calls[1]["body"]
    assert calls[0]["path"] == "/device-api/sync/v1/withdrawals"
    assert json.loads(calls[0]["body"]) == body


@pytest.mark.parametrize("body", [None, {}, {"source_event_id": SOURCE, "reason": []},
    {"source_event_id": SOURCE, "reason": "private narrative"},
    {"source_event_id": SOURCE, "reason": "LOCAL_DELETED", "notes": "private"},
    {"source_event_id": DEVICE, "reason": "LOCAL_DELETED"}])
def test_withdrawal_strict_minimal_body_before_network(body, monkeypatch):
    monkeypatch.setattr(transport, "build_opener", lambda *_: pytest.fail("must not send"))
    with pytest.raises(ValueError):
        transport.CloudTransport("https://console.example.test").withdrawal(
            TOKEN, body, expected_source_event_id=SOURCE)


@pytest.mark.parametrize("operation", ["observation", "withdrawal"])
def test_mismatched_expected_source_never_sends(operation, monkeypatch):
    monkeypatch.setattr(transport, "build_opener", lambda *_: pytest.fail("must not send"))
    sender = transport.CloudTransport("https://console.example.test")
    with pytest.raises(ValueError, match="^SOURCE_ID_MISMATCH$"):
        if operation == "observation":
            sender.observation(TOKEN, observation_payload(), expires_at=DEADLINE,
                               expected_source_event_id=OTHER_SOURCE)
        else:
            sender.withdrawal(TOKEN, {"source_event_id": SOURCE, "reason": "LOCAL_DELETED"},
                              expected_source_event_id=OTHER_SOURCE)


@pytest.mark.parametrize("response,code", [
    ({"source_event_id": SOURCE, "withdrawn": 1}, "INVALID_RESPONSE"),
    ({"source_event_id": SOURCE, "withdrawn": True, "secret": TOKEN}, "INVALID_RESPONSE"),
    ({"source_event_id": DEVICE, "withdrawn": True}, "INVALID_RESPONSE"),
    ({"source_event_id": OTHER_SOURCE, "withdrawn": True}, "RECEIPT_MISMATCH"),
])
def test_withdrawal_strict_echo_response(response, code):
    with endpoint(response) as (origin, calls):
        with pytest.raises(transport.CloudTransportError, match=f"^{code}$"):
            client(origin).withdrawal(TOKEN, {"source_event_id": SOURCE, "reason": "LOCAL_DELETED"},
                                      expected_source_event_id=SOURCE)
        assert len(calls) == 1


@pytest.mark.parametrize("operation", ["observation", "withdrawal"])
def test_sync_redirect_never_forwards_or_retries(operation):
    with endpoint({}) as (destination, stolen):
        with endpoint({}, 307, {"Location": destination + "/private"}) as (origin, calls):
            sender = client(origin)
            with pytest.raises(transport.CloudTransportError, match="^REDIRECT_REFUSED$"):
                if operation == "observation":
                    sender.observation(TOKEN, observation_payload(), expires_at=DEADLINE,
                                       expected_source_event_id=SOURCE)
                else:
                    sender.withdrawal(TOKEN, {"source_event_id": SOURCE, "reason": "LOCAL_DELETED"},
                                      expected_source_event_id=SOURCE)
            assert len(calls) == 1 and stolen == []


def test_sync_limit_is_bounded_with_no_remote_message():
    with endpoint({"error": {"code": "SYNC_LIMIT", "message": TOKEN}}, 429,
                  {"Retry-After": "120"}) as (origin, _):
        with pytest.raises(transport.CloudTransportError) as error:
            client(origin).observation(TOKEN, observation_payload(), expires_at=DEADLINE,
                                       expected_source_event_id=SOURCE)
    assert error.value.code == "SYNC_LIMIT" and error.value.retry_after == 120
    assert TOKEN not in str(error.value)


@pytest.mark.parametrize("route", ["alerts", "sync/v1/observations?x=1", "../identity", "https://foreign.test"])
def test_private_call_cannot_expand_fixed_route_allowlist(route, monkeypatch):
    monkeypatch.setattr(transport, "build_opener", lambda *_: pytest.fail("must not send"))
    with pytest.raises(ValueError, match="^INVALID_OPERATION$"):
        transport.CloudTransport("https://console.example.test")._call(route, method="POST")
