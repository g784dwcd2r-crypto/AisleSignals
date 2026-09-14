"""Synthetic SQLite + owned HTTPS endpoints; no default app or customer data."""

from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import importlib.util
import ipaddress
import json
import multiprocessing
import os
from pathlib import Path
import ssl
import sys
import threading
import time
from uuid import uuid4

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID
import pytest

from services.api.cloud_delivery import CloudDelivery, DeliveryPolicy, OwnedRequest, _valid_receipt
from services.api.cloud_observation import MappedObservation, source_event_id
from services.api.cloud_outbox import BindingRef, Outbox, Scope, Target
from services.api.store import Store


TOKEN = "s" * 64


@pytest.fixture
def certificate(tmp_path, monkeypatch):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Synthetic loopback")])
    now = datetime.now(timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
        .serial_number(x509.random_serial_number()).not_valid_before(now-timedelta(minutes=1))
        .not_valid_after(now+timedelta(hours=1))
        .add_extension(x509.SubjectAlternativeName([x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]), critical=False)
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True).sign(key, hashes.SHA256()))
    cert_file, key_file = tmp_path / "synthetic-cert.pem", tmp_path / "synthetic-tls-key.pem"
    cert_file.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key_file.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                           serialization.NoEncryption()))
    monkeypatch.setenv("SSL_CERT_FILE", str(cert_file))
    return cert_file, key_file


@contextmanager
def endpoint(certificate, respond):
    calls = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            raw = self.rfile.read(int(self.headers.get("Content-Length", "0")))
            call = {"path": self.path, "headers": dict(self.headers), "raw": raw, "body": json.loads(raw)}
            calls.append(call)
            status, value = respond(call)
            data = json.dumps(value).encode()
            try:
                self.send_response(status)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
            except (BrokenPipeError, ConnectionResetError, ssl.SSLError):
                pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(*map(str, certificate))
    server.socket = context.wrap_socket(server.socket, server_side=True)
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": .01}, daemon=True)
    thread.start()
    try:
        yield f"https://127.0.0.1:{server.server_port}", calls
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)
        assert not thread.is_alive()


def receipt(call):
    if call["path"].endswith("withdrawals"):
        return 200, {"source_event_id": call["body"]["source_event_id"], "withdrawn": True}
    return 201, {"id": str(uuid4()), "received": True, "source_state": "AVAILABLE"}


@dataclass(frozen=True)
class Context:
    scope: Scope
    binding: BindingRef
    target: Target
    outbox: Outbox = field(repr=False)
    credential: str = field(repr=False)


class Provider:
    """Narrow provider test double; real scoped state and Outbox stay in SQLite."""

    def __init__(self, store, scope, target, key, credential):
        self.store, self.scope, self.target = store, scope, target
        self.box, self.credential = Outbox(key), credential
        self.errors = []

    def delivery_context(self, conn):
        row = conn.execute("SELECT id,generation FROM cloud_sync_bindings WHERE installation_id=? AND state='ACTIVE'",
                           (self.scope.installation_id,)).fetchone()
        if self.store.mode != "pilot" or row is None:
            return None
        return Context(self.scope, BindingRef(row["id"], row["generation"]), self.target, self.box, self.credential)

    def access_revoked(self, conn, ctx, *, now):
        return self.pause_error(conn, ctx, code="ACCESS_REVOKED", now=now)

    def pause_error(self, conn, ctx, *, code, now):
        current = self.delivery_context(conn)
        if current and current.binding == ctx.binding:
            if code == "CLOCK_ROLLBACK":
                conn.execute("UPDATE cloud_sync_bindings SET state='PAUSED',generation=generation+1 WHERE id=?", (ctx.binding.id,))
            else:
                self.box.set_paused(conn, ctx.scope, ctx.binding, paused=True, now=now)
            self.errors.append(code)
            return True
        return False


class Local:
    def __init__(self, path, origin, token=TOKEN):
        self.store = Store(path, mode="pilot")
        self.now = datetime.now(timezone.utc)
        with self.store.transaction() as conn:
            installation = conn.execute("SELECT value FROM runtime_settings WHERE key='installation_id'").fetchone()[0]
            scope = Scope(installation, "synthetic-org", "synthetic-site")
            self.provider = Provider(self.store, scope, Target(origin, *(str(uuid4()) for _ in range(4))), bytes(range(32)), token)
            binding = self.provider.box.create_binding(conn, scope, binding_id=str(uuid4()), expected_generation=0,
                                                        target=self.provider.target, now=self.now)
            self.provider.box.set_paused(conn, scope, binding.ref, paused=False, now=self.now)

    def enqueue(self):
        entity_id = str(uuid4())
        with self.store.transaction() as conn:
            ctx = self.provider.delivery_context(conn)
            source = source_event_id(installation_id=ctx.scope.installation_id, binding_id=ctx.binding.id,
                organisation_id=ctx.scope.organisation_id, site_id=ctx.scope.site_id,
                entity_kind="interaction", entity_id=entity_id)
            payload = {"source_event_id": source, "event_code": "POSSIBLE_CONCEALMENT",
                "source_label": "Camera 1 of 4 · 2x2 screen grid · local observation", "historical": True,
                "occurred_at": self.now.isoformat().replace("+00:00", "Z")}
            conn.execute("INSERT INTO entities(id,kind,organisation_id,site_id,body,created_at) VALUES(?,?,?,?,?,?)",
                (entity_id, "interaction", ctx.scope.organisation_id, ctx.scope.site_id,
                 json.dumps({"source_state": "AVAILABLE"}), self.now.isoformat()))
            return ctx.outbox.enqueue(conn, ctx.scope, ctx.binding,
                MappedObservation("interaction", entity_id, payload, self.now, self.now+timedelta(hours=24)), now=self.now)

    def source_state(self, conn, ctx, kind, entity_id):
        row = conn.execute("SELECT body FROM entities WHERE id=? AND kind=? AND organisation_id=? AND site_id=?",
                           (entity_id, kind, ctx.scope.organisation_id, ctx.scope.site_id)).fetchone()
        return json.loads(row["body"])["source_state"] if row else "DELETED"

    def row(self, item):
        with self.store.transaction() as conn:
            return dict(conn.execute("SELECT * FROM cloud_sync_items WHERE id=?", (item,)).fetchone())

    def change_source(self, item, state):
        with self.store.transaction() as conn:
            row = conn.execute("SELECT entity_id FROM cloud_sync_items WHERE id=?", (item,)).fetchone()
            if state == "DELETED":
                conn.execute("DELETE FROM entities WHERE id=?", (row[0],))
            else:
                conn.execute("UPDATE entities SET body=? WHERE id=?", (json.dumps({"source_state": state}), row[0]))

    def sender(self, **options):
        return CloudDelivery(self.store, self.provider, source_state=self.source_state, clock=lambda: self.now, **options)

    def pause(self):
        with self.store.transaction() as conn:
            ctx = self.provider.delivery_context(conn)
            return ctx.outbox.set_paused(conn, ctx.scope, ctx.binding, paused=True, now=self.now)

    def resume(self, binding):
        with self.store.transaction() as conn:
            return self.provider.box.set_paused(conn, self.provider.scope, binding.ref, paused=False, now=self.now)


def assert_no_request_child():
    assert not [child for child in multiprocessing.active_children() if child.name == "aislesignals-cloud-request"]


def test_actual_https_delivery_receipt_then_deleted_source_withdrawal(tmp_path, certificate):
    local = None

    def responding(call):
        # This writer succeeding proves HTTP is outside the caller's SQLite lock.
        with local.store.transaction() as conn:
            conn.execute("INSERT OR REPLACE INTO runtime_settings VALUES('synthetic-http-unlocked','yes')")
        return receipt(call)

    with endpoint(certificate, responding) as (origin, calls):
        local = Local(tmp_path / "pilot.db", origin)
        item = local.enqueue()
        sender = local.sender()
        assert sender.run_once().status == "DELIVERED"
        original = local.row(item)
        assert original["state"] == "RECEIVED" and original["payload"] is None
        local.change_source(item, "DELETED")
        assert sender.run_once().status == "WITHDRAWN"
        saved = local.row(item)
        assert saved["state"] == "WITHDRAWN" and saved["receipt_id"] == original["source_event_id"]
        assert saved["observation_receipt_id"] == original["receipt_id"]
        assert len(calls) == 2
        assert set(calls[0]["body"]) == {"source_event_id", "event_code", "source_label", "occurred_at", "historical", "expires_at"}
        assert calls[1]["body"] == {"source_event_id": original["source_event_id"], "reason": "LOCAL_DELETED"}
        assert not any("cookie" == key.lower() for call in calls for key in call["headers"])
        assert sender.stop()
    assert_no_request_child()


def test_transient_retry_preserves_exact_body_ciphertext_uuid_and_deadline(tmp_path, certificate):
    failures = [True]

    def responding(call):
        if failures:
            failures.pop()
            return 503, {}
        return receipt(call)

    with endpoint(certificate, responding) as (origin, calls):
        local = Local(tmp_path / "pilot.db", origin)
        item = local.enqueue()
        original = local.row(item)
        sender = local.sender()
        result = sender.run_once()
        assert result.status == "RETRY" and result.retry_seconds == 5
        saved = local.row(item)
        assert saved["payload"] == original["payload"] and saved["source_event_id"] == original["source_event_id"]
        assert sender.run_once().status == "IDLE"
        local.now += timedelta(seconds=6)
        assert sender.run_once().status == "DELIVERED"
        assert calls[0]["raw"] == calls[1]["raw"]
    assert_no_request_child()


@pytest.mark.parametrize("state", ["DELETED", "EXPIRED", "INELIGIBLE"])
def test_unattempted_missing_or_ineligible_sources_never_send(tmp_path, state):
    local = Local(tmp_path / "pilot.db", "https://synthetic.example.test")
    item = local.enqueue()
    local.change_source(item, state)
    assert local.sender().run_once().status == "IDLE"
    row = local.row(item)
    assert row["state"] in {"EXPIRED", "CANCELLED"} and row["payload"] is None and row["attempts"] == 0


def test_expired_received_source_sends_withdrawal_only(tmp_path, certificate):
    with endpoint(certificate, receipt) as (origin, calls):
        local = Local(tmp_path / "pilot.db", origin)
        item = local.enqueue()
        sender = local.sender()
        assert sender.run_once().status == "DELIVERED"
        local.now += timedelta(hours=25)
        assert sender.run_once().status == "WITHDRAWN"
        assert calls[-1]["body"] == {"source_event_id": local.row(item)["source_event_id"], "reason": "LOCAL_EXPIRED"}


def test_access_revocation_pauses_and_never_retries(tmp_path, certificate):
    with endpoint(certificate, lambda _: (401, {"secret": TOKEN})) as (origin, calls):
        local = Local(tmp_path / "pilot.db", origin)
        item = local.enqueue()
        sender = local.sender()
        result = sender.run_once()
        assert (result.status, result.error_code) == ("BLOCKED", "ACCESS_REVOKED")
        assert local.provider.errors == ["ACCESS_REVOKED"]
        assert sender.run_once().status == "DISABLED" and len(calls) == 1
        assert local.row(item)["receipt_id"] is None


def test_pause_resume_preserves_valid_backlog_and_synthetic_restored_never_send(tmp_path, certificate):
    with endpoint(certificate, receipt) as (origin, calls):
        local = Local(tmp_path / "pilot.db", origin)
        item = local.enqueue()
        original = local.row(item)
        paused = local.pause()
        sender = local.sender()
        assert sender.run_once().status == "DISABLED"
        local.now += timedelta(seconds=2)
        local.resume(paused)
        assert sender.run_once().status == "DELIVERED"
        assert local.row(item)["source_event_id"] == original["source_event_id"]
        with local.store.transaction() as conn:
            ctx = local.provider.delivery_context(conn)
            ctx.outbox.disconnect(conn, ctx.scope, ctx.binding, now=local.now, restored=True)
        assert sender.run_once().status == "DISABLED" and len(calls) == 1
    synthetic = Store(tmp_path / "synthetic.db")
    worker = CloudDelivery(synthetic, None, source_state=lambda *_: pytest.fail("no source access"))
    assert worker.run_once().status == "DISABLED"
    assert not Path(str(synthetic.path) + ".cloud-delivery.pilot-lock").exists()


def test_change_during_flight_terminates_owned_child_and_fences_late_receipt(tmp_path, certificate):
    entered, release = threading.Event(), threading.Event()

    def responding(call):
        entered.set()
        assert release.wait(5)
        return receipt(call)

    with endpoint(certificate, responding) as (origin, calls):
        local = Local(tmp_path / "pilot.db", origin)
        item = local.enqueue()
        sender = local.sender()
        with ThreadPoolExecutor(max_workers=1) as pool:
            pending = pool.submit(sender.run_once)
            try:
                assert entered.wait(4)
                assert local.sender().run_once().status == "BUSY"
                paused = local.pause()
                local.resume(paused)
                assert pending.result(timeout=3).status == "FENCED"
            finally:
                release.set()
        assert local.row(item)["receipt_id"] is None and len(calls) == 1
        assert sender.stop()
    assert_no_request_child()


def test_delete_during_flight_preserves_uncertainty_for_withdrawal(tmp_path, certificate):
    entered, release = threading.Event(), threading.Event()

    def responding(call):
        if call["path"].endswith("observations"):
            entered.set()
            assert release.wait(5)
        return receipt(call)

    with endpoint(certificate, responding) as (origin, calls):
        local = Local(tmp_path / "pilot.db", origin)
        item = local.enqueue()
        sender = local.sender()
        with ThreadPoolExecutor(max_workers=1) as pool:
            pending = pool.submit(sender.run_once)
            try:
                assert entered.wait(4)
                local.change_source(item, "DELETED")
                assert pending.result(timeout=3).status == "FENCED"
            finally:
                release.set()
        row = local.row(item)
        assert row["operation"] == "WITHDRAWAL" and row["receipt_id"] is None
        local.now += timedelta(seconds=21)
        assert sender.run_once().status == "WITHDRAWN"
        assert calls[-1]["body"]["source_event_id"] == row["source_event_id"]


def test_total_deadline_stops_hung_request_and_releases_actual_owner(tmp_path, certificate):
    entered, release = threading.Event(), threading.Event()

    def responding(call):
        entered.set()
        assert release.wait(5)
        return receipt(call)

    with endpoint(certificate, responding) as (origin, calls):
        local = Local(tmp_path / "pilot.db", origin)
        item = local.enqueue()
        sender = local.sender(policy=DeliveryPolicy(request_seconds=1))
        try:
            start = time.monotonic()
            result = sender.run_once()
            assert entered.is_set() and result.status == "RETRY" and result.error_code == "REQUEST_TIMEOUT"
            assert time.monotonic()-start < 3
            assert local.row(item)["state"] == "PENDING" and local.row(item)["receipt_id"] is None
            assert not sender._request.alive and sender.stop()
        finally:
            release.set()
    assert_no_request_child()


def test_stop_interrupts_request_and_repeated_start_does_not_duplicate(tmp_path, certificate):
    entered, release = threading.Event(), threading.Event()

    def responding(call):
        entered.set()
        assert release.wait(5)
        return receipt(call)

    with endpoint(certificate, responding) as (origin, calls):
        local = Local(tmp_path / "pilot.db", origin)
        item = local.enqueue()
        sender = local.sender()
        assert sender.start() is True and sender.start() is False
        try:
            assert entered.wait(4)
            assert sender.stop(timeout=3) is True
            assert not sender.running and local.row(item)["receipt_id"] is None
            assert sender.stop() is True
        finally:
            release.set()
        assert len(calls) == 1
    assert_no_request_child()


def test_deadline_still_kills_http_while_sql_authorization_callback_is_blocked(tmp_path, certificate):
    entered, release_http = threading.Event(), threading.Event()
    rechecking, release_check = threading.Event(), threading.Event()

    def responding(call):
        entered.set()
        assert release_http.wait(6)
        return receipt(call)

    with endpoint(certificate, responding) as (origin, calls):
        local = Local(tmp_path / "pilot.db", origin)
        local.enqueue()
        sender = local.sender(policy=DeliveryPolicy(request_seconds=1))
        original = sender.source_state

        def blocked_check(conn, ctx, kind, item):
            if entered.is_set():
                rechecking.set()
                assert release_check.wait(5)
            return original(conn, ctx, kind, item)

        sender.source_state = blocked_check
        with ThreadPoolExecutor(max_workers=1) as pool:
            pending = pool.submit(sender.run_once)
            try:
                assert rechecking.wait(4)
                deadline = time.monotonic()+2
                while sender._request.alive and time.monotonic() < deadline:
                    time.sleep(.02)
                assert not sender._request.alive
                assert not pending.done()  # callback is still holding its SQL transaction
            finally:
                release_check.set()
                release_http.set()
            assert pending.result(timeout=3).error_code == "REQUEST_TIMEOUT"
        assert sender.stop()
    assert_no_request_child()


def test_clock_rollback_after_claim_prevents_http_and_fences_binding(tmp_path, certificate):
    with endpoint(certificate, receipt) as (origin, calls):
        local = Local(tmp_path / "pilot.db", origin)
        local.enqueue()

        class BackwardClockRequest(OwnedRequest):
            def perform(self, request, **options):
                local.now -= timedelta(seconds=1)
                return super().perform(request, **options)

        sender = local.sender(request_factory=BackwardClockRequest)
        result = sender.run_once()
        assert result.status == "BLOCKED" and result.error_code == "CLOCK_ROLLBACK"
        assert local.provider.errors == ["CLOCK_ROLLBACK"] and calls == []
        assert sender.stop()


def test_bounded_attempts_retain_blocked_withdrawal_not_fake_receipt(tmp_path, certificate):
    with endpoint(certificate, lambda _: (503, {})) as (origin, calls):
        local = Local(tmp_path / "pilot.db", origin)
        item = local.enqueue()
        sender = local.sender(policy=DeliveryPolicy(observation_attempts=1, total_attempts=2))
        assert sender.run_once().status == "BLOCKED"
        assert local.row(item)["operation"] == "WITHDRAWAL"
        assert sender.run_once().status == "BLOCKED"
        assert local.row(item)["state"] == "BLOCKED" and local.row(item)["receipt_id"] is None
        assert sender.run_once().status == "IDLE" and len(calls) == 2


def test_corrupt_ciphertext_pauses_without_advancing_claim_or_network(tmp_path):
    local = Local(tmp_path / "pilot.db", "https://synthetic.example.test")
    item = local.enqueue()
    with local.store.transaction() as conn:
        conn.execute("UPDATE cloud_sync_items SET payload=? WHERE id=?", (b"synthetic-corrupt", item))
    sender = local.sender()
    result = sender.run_once()
    assert (result.status, result.error_code) == ("BLOCKED", "CIPHERTEXT_INVALID")
    assert local.provider.errors == ["CIPHERTEXT_INVALID"]
    assert local.row(item)["attempts"] == 0 and local.row(item)["payload"] == b"synthetic-corrupt"
    assert sender.run_once().status == "STOPPED"


def test_failed_owned_child_stop_retains_owner_and_returns_false(tmp_path):
    local = Local(tmp_path / "pilot.db", "https://synthetic.example.test")
    local.enqueue()

    class UnstoppedRequest:
        alive = False

        def perform(self, *_args, **_options):
            self.alive = True
            return {"ok": False, "code": "REQUEST_TIMEOUT", "retry_after": None}

        def close(self):
            return not self.alive

    sender = local.sender(request_factory=UnstoppedRequest)
    result = sender.run_once()
    assert result.status == "BLOCKED" and result.error_code == "REQUEST_STOP_FAILED"
    try:
        assert sender.stop() is False and sender.start() is False
        assert local.sender().run_once().status == "BUSY"
    finally:
        sender._request.alive = False  # Synthetic fault cleared; no process existed.
        assert sender.stop() is True


@pytest.mark.parametrize("result", [{"id": str(uuid4()), "received": 1, "source_state": "AVAILABLE"},
    {"id": str(uuid4()), "received": True, "source_state": "MONITORING"},
    {"id": str(uuid4()), "received": True, "source_state": "AVAILABLE", "secret": TOKEN}])
def test_parent_refuses_malformed_child_receipts(result):
    assert not _valid_receipt(result, {"operation": "OBSERVATION", "receipt_id": None})


@pytest.mark.parametrize("values", [{"request_seconds": float("inf")}, {"lease_seconds": 8},
    {"reconciliation_rows": 0}, {"total_attempts": 8}, {"interval_seconds": False}])
def test_policy_bounds(values):
    with pytest.raises(ValueError, match="INVALID_DELIVERY_POLICY"):
        DeliveryPolicy(**values)


def test_real_cloud_api_retry_expiry_deletion_and_device_revocation(tmp_path, certificate):
    if os.environ.get("CLOUD_RUN_POSTGRES_TESTS") != "1":
        pytest.skip("Explicit owned PostgreSQL integration opt-in required")
    support_path = Path(__file__).resolve().parents[1] / "cloud" / "control_test_support.py"
    spec = importlib.util.spec_from_file_location("delivery_test_support", support_path)
    support = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(support)
    with support.disposable_postgres() as settings:
        support.reset_database(settings)
        with support.new_client(settings) as owner, support.new_client(settings) as api:
            support.bootstrap(owner, settings)
            branch = support.pharmacy(owner, "Synthetic sender branch")
            enrolled = owner.post("/control-api/devices/enrolments", json={"pharmacy_id": branch["id"],
                "name": "Synthetic sender", "platform": "MACOS"}).json()
            laptop = api.post("/device-api/enrol", json={"token": enrolled["token"], "name": "Synthetic sender",
                "platform": "MACOS", "app_version": "synthetic-delivery-test"}).json()
            fail_first = [True]

            def respond(call):
                reply = api.post(call["path"], headers={"Authorization": call["headers"]["Authorization"]}, json=call["body"])
                # Simulate receipt loss AFTER the real cloud transaction commits.
                if fail_first and reply.status_code == 201:
                    fail_first.pop()
                    return 503, {}
                return reply.status_code, reply.json()

            with endpoint(certificate, respond) as (origin, calls):
                local = Local(tmp_path / "real-api-pilot.db", origin, laptop["device_token"])
                sender = local.sender()
                item = local.enqueue()
                assert sender.run_once().status == "RETRY"
                local.now += timedelta(seconds=6)
                assert sender.run_once().status == "DELIVERED"
                assert calls[0]["raw"] == calls[1]["raw"]
                alerts = owner.get("/control-api/alerts").json()["items"]
                assert len(alerts) == 1
                dashboard = owner.get("/control-api/dashboard").json()
                assert dashboard["summary"]["open_alerts"] == 1
                reviewed = owner.post("/control-api/alerts/" + alerts[0]["id"] + "/review", json={
                    "expected_version": alerts[0]["version"], "outcome": "SUSPECTED_INCIDENT",
                    "note": "Synthetic independent staff review note", "create_incident": True,
                    "title": "Synthetic staff-authored case"})
                assert reviewed.status_code == 200, reviewed.text
                incident = reviewed.json()["incident"]
                local.change_source(item, "DELETED")
                assert sender.run_once().status == "WITHDRAWN"
                assert owner.get("/control-api/alerts").json()["items"] == []
                kept = owner.get("/control-api/incidents/" + incident["id"]).json()
                assert kept["source_unavailable"] is True
                assert kept["notes"] == "Synthetic independent staff review note"
                assert kept["title"] == "Synthetic staff-authored case"
                dashboard = owner.get("/control-api/dashboard").json()
                assert dashboard["summary"]["open_alerts"] == 0
                assert dashboard["summary"]["reviewed_incidents"] == 1
                second = local.enqueue()
                assert sender.run_once().status == "DELIVERED"
                local.change_source(second, "EXPIRED")
                assert sender.run_once().status == "WITHDRAWN"
                assert owner.get("/control-api/alerts").json()["items"] == []
                local.enqueue()
                assert owner.post("/control-api/devices/" + laptop["device_id"] + "/revoke", json={"expected_version": 1}).status_code == 200
                assert sender.run_once().error_code == "ACCESS_REVOKED"
                assert sender.run_once().status == "DISABLED"
                assert sender.stop()
    assert_no_request_child()
