"""Real local auth/API + isolated loopback cloud transport; all data synthetic."""

from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
import stat
import threading
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from services.api.app import create_app
from services.api.cloud_connection import ATTEMPTS_KEY, ConnectionUnavailable, PrivateVault
from services.api.cloud_outbox import Scope
from services.api.cloud_transport import CloudTransport
from services.api.pilot_identity import add_site, add_user, grant_user, initialise
from services.api.store import Store, PASSWORD as DEMO_PASSWORD
from test_pilot_identity import BASE, EMAIL, PASSWORD, client_for, update_headers

ORIGIN = "https://synthetic-control.example.test"
CODE = "c" * 43
TOKEN = "t" * 64


class Remote:
    def __init__(self):
        self.identity = {"device_id": str(uuid4()), "organisation_id": str(uuid4()), "pharmacy_id": str(uuid4()),
                         "organisation_name": "Synthetic cloud group", "pharmacy_name": "Synthetic cloud branch",
                         "name": "Synthetic laptop", "platform": "MACOS", "app_version": "pilot-cloud-sync-1"}
        self.calls = []
        self.entered, self.release = threading.Event(), threading.Event()
        self.release.set()
        self.gate = None
        self.identity_status = 200
        self.enrol_status = 201

    @contextmanager
    def serve(self):
        remote = self
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def reply(self, status, value):
                raw = json.dumps(value).encode()
                self.send_response(status)
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def do_POST(self):
                body = self.rfile.read(int(self.headers.get("Content-Length", "0")))
                remote.calls.append(("POST", self.path, json.loads(body)))
                if remote.gate == "enrol":
                    remote.entered.set()
                    assert remote.release.wait(5)
                self.reply(remote.enrol_status, {"device_id": remote.identity["device_id"], "device_token": TOKEN,
                                                "heartbeat_interval_seconds": 30})

            def do_GET(self):
                remote.calls.append(("GET", self.path, self.headers.get("Authorization")))
                if remote.gate == "identity":
                    remote.entered.set()
                    assert remote.release.wait(5)
                self.reply(remote.identity_status, remote.identity)

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        server.daemon_threads = True
        thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": .01}, daemon=True)
        thread.start()
        try:
            yield f"http://127.0.0.1:{server.server_port}"
        finally:
            self.release.set()
            server.shutdown()
            server.server_close()
            thread.join(timeout=3)
            assert not thread.is_alive()


@pytest.fixture
def paired_workspace(tmp_path):
    app = create_app(tmp_path / "pilot.db", tmp_path / "web", mode="pilot")
    initial = initialise(app.state.store, "Synthetic Local Group", "Synthetic Local Branch", EMAIL, "Synthetic Manager", PASSWORD)
    remote = Remote()
    with remote.serve() as origin:
        app.state.cloud_connection.transport_factory = lambda selected: CloudTransport(origin, allow_local_test=True)
        try:
            yield app, initial, client_for(app), remote
        finally:
            remote.release.set()
            app.state.interactions.close()


def prepare(client, **changes):
    return client.post("/api/cloud-connection/prepare", json={"origin": ORIGIN, "code": CODE, "name": "Synthetic laptop", "manager_password": PASSWORD, **changes})


def confirmation(client, prepared, **changes):
    item = prepared["preparation"]
    identity = item["identity"]
    return client.post("/api/cloud-connection/confirm", json={"preparation_id": item["preparation_id"], "origin": item["origin"],
        "organisation_id": identity["organisation_id"], "pharmacy_id": identity["pharmacy_id"], "device_id": identity["device_id"],
        "local_site_id": item["local_site_id"], "manager_password": PASSWORD, **changes})


def configured(client):
    result = prepare(client)
    assert result.status_code == 201, result.text
    confirmed = confirmation(client, result.json())
    assert confirmed.status_code == 200, confirmed.text
    return confirmed.json()


def action(client, connection, operation, **changes):
    item = connection["connection"]
    return client.post("/api/cloud-connection/" + operation, json={"binding_id": item["binding_id"],
        "expected_generation": item["generation"], "manager_password": PASSWORD, **changes})


def test_pair_confirm_paused_explicit_resume_provider_and_disconnect(paired_workspace):
    app, initial, client, remote = paired_workspace
    assert client.get("/api/cloud-connection").json()["connection"] is None
    prepared = prepare(client)
    assert prepared.status_code == 201, prepared.text
    value = prepared.json()
    assert value["preparation"]["identity"] == remote.identity
    assert value["local_site"]["id"] == initial["site"]["id"]
    assert value["monitoring_status"] == "UNKNOWN"
    result = confirmation(client, value)
    assert result.status_code == 200, result.text
    saved = result.json()
    assert saved["connection"]["state"] == "PAUSED" and saved["preparation"] is None
    assert saved["connection"]["credential_available"] is True
    assert saved["connection"]["organisation_id"] != initial["user"]["organisation_id"]
    with app.state.store.transaction() as conn:
        assert app.state.cloud_connection.delivery_context(conn) is None
        row = dict(conn.execute("SELECT * FROM cloud_sync_bindings").fetchone())
        scope = Scope(row["installation_id"], row["organisation_id"], row["site_id"])
        assert scope.organisation_id == initial["user"]["organisation_id"]
        assert app.state.cloud_connection.source_context(conn, scope).credential == TOKEN
        assert app.state.cloud_connection.source_context(conn, Scope(scope.installation_id, scope.organisation_id, str(uuid4()))) is None
    resumed = action(client, saved, "resume")
    assert resumed.status_code == 200, resumed.text
    assert resumed.json()["connection"]["state"] == "ACTIVE"
    with app.state.store.transaction() as conn:
        delivery = app.state.cloud_connection.delivery_context(conn)
        assert delivery.credential == TOKEN and TOKEN not in repr(delivery)
        assert delivery.binding.generation == resumed.json()["connection"]["generation"]
    disconnected = action(client, resumed.json(), "disconnect")
    assert disconnected.status_code == 200
    assert disconnected.json()["connection"]["state"] == "DISCONNECTED"
    with app.state.store.transaction() as conn:
        assert app.state.cloud_connection.delivery_context(conn) is None
        assert app.state.cloud_connection.source_context(conn, scope) is None
    assert {path for _, path, _ in remote.calls} == {"/device-api/enrol", "/device-api/identity"}
    for response in (prepared, result, resumed, disconnected):
        assert TOKEN not in response.text and CODE not in response.text and PASSWORD not in response.text
    with app.state.store.transaction() as conn:
        dump = "\n".join(conn.iterdump())
        assert TOKEN not in dump and CODE not in dump and PASSWORD not in dump
    files = list(app.state.cloud_connection.vault.directory.glob("*.json"))
    assert len(files) == 1
    material = json.loads(files[0].read_bytes())
    assert material["credential"] == TOKEN and len(material["key"]) == 43
    if os.name != "nt":
        assert stat.S_IMODE(files[0].stat().st_mode) == 0o600


@pytest.mark.parametrize("field,value", [("code", "short"), ("code", "!"*43), ("origin", "http://example.test"),
    ("origin", "https://example.test/path"), ("origin", "https://user:pass@example.test"), ("name", "bad\nname"), ("unexpected", TOKEN)])
def test_invalid_input_is_rejected_before_http_without_secret_echo(paired_workspace, field, value):
    app, _, client, remote = paired_workspace
    response = prepare(client, **{field: value})
    assert response.status_code == 422 and not remote.calls
    assert TOKEN not in response.text and CODE not in response.text and PASSWORD not in response.text
    assert not app.state.cloud_connection.vault.directory.exists()


@pytest.mark.parametrize("header", ["Origin", "X-CSRF-Token", "X-AisleSignals-Site"])
def test_origin_csrf_current_site_required_before_remote_code_consumption(paired_workspace, header):
    _, _, client, remote = paired_workspace
    client.headers[header] = "https://unrelated.example" if header == "Origin" else "incorrect"
    assert prepare(client).status_code in {403, 409}
    assert remote.calls == []


def test_reviewer_and_anonymous_cannot_pair_or_see_connection(paired_workspace):
    app, initial, _, remote = paired_workspace
    add_user(app.state.store, "reviewer@example.test", "Synthetic Reviewer", PASSWORD, [initial["site"]["id"]])
    reviewer = client_for(app, "reviewer@example.test")
    anonymous = TestClient(app, base_url=BASE, headers={"Origin": BASE})
    for client, expected in ((reviewer, 403), (anonymous, 401)):
        assert client.get("/api/cloud-connection").status_code == expected
        assert prepare(client).status_code == expected
    assert remote.calls == []


def test_synthetic_mode_never_pairs_or_loads_credentials(tmp_path):
    app = create_app(tmp_path / "synthetic.db", tmp_path / "web", mode="synthetic")
    try:
        client = client_for(app, "manager@harbour.demo", DEMO_PASSWORD)
        assert client.get("/api/cloud-connection").status_code == 403
        assert prepare(client, manager_password=DEMO_PASSWORD).status_code == 403
        with app.state.store.transaction() as conn:
            assert app.state.cloud_connection.delivery_context(conn) is None
        assert not app.state.cloud_connection.vault.directory.exists()
    finally:
        app.state.interactions.close()


def test_wrong_password_persists_bounded_throttle_without_remote_calls(paired_workspace):
    _, _, client, remote = paired_workspace
    for _ in range(5):
        response = prepare(client, manager_password="Incorrect synthetic passphrase!")
        assert response.status_code == 401 and response.json()["error"]["code"] == "REAUTH_REQUIRED"
    assert prepare(client).status_code == 429
    assert client.get("/api/cloud-connection").status_code == 200
    assert remote.calls == []


@pytest.mark.parametrize("change", [{"organisation_id": str(uuid4())}, {"pharmacy_id": str(uuid4())},
    {"device_id": str(uuid4())}, {"local_site_id": str(uuid4())}, {"origin": "https://other.example.test"}])
def test_confirmation_requires_exact_displayed_remote_and_local_identity(paired_workspace, change):
    app, _, client, remote = paired_workspace
    prepared = prepare(client).json()
    calls = len(remote.calls)
    response = confirmation(client, prepared, **change)
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "IDENTITY_CONFIRMATION_MISMATCH"
    assert len(remote.calls) == calls
    with app.state.store.transaction() as conn:
        assert conn.execute("SELECT count(*) FROM cloud_sync_bindings").fetchone()[0] == 0


def test_expired_and_replayed_confirmation_never_rebinds(paired_workspace):
    app, _, client, remote = paired_workspace
    prepared = prepare(client).json()
    assert confirmation(client, prepared).status_code == 200
    assert confirmation(client, prepared).status_code == 409
    assert sum(method == "POST" for method, _, _ in remote.calls) == 1
    with app.state.store.transaction() as conn:
        assert conn.execute("SELECT count(*) FROM cloud_sync_bindings").fetchone()[0] == 1


def test_expired_preparation_and_duplicate_code_do_not_repeat_enrolment(paired_workspace):
    app, _, client, remote = paired_workspace
    prepared = prepare(client).json()
    app.state.cloud_connection.clock = lambda: 4102444800
    assert confirmation(client, prepared).status_code == 409
    assert prepare(client).json()["error"]["code"] == "CODE_ALREADY_ATTEMPTED"
    assert sum(method == "POST" for method, _, _ in remote.calls) == 1


def test_remote_identity_change_rejected_before_local_binding_commit(paired_workspace):
    app, _, client, remote = paired_workspace
    prepared = prepare(client).json()
    remote.identity["pharmacy_id"] = str(uuid4())
    result = confirmation(client, prepared)
    assert result.status_code == 409 and result.json()["error"]["code"] == "REMOTE_IDENTITY_CHANGED"
    with app.state.store.transaction() as conn:
        assert conn.execute("SELECT count(*) FROM cloud_sync_bindings").fetchone()[0] == 0


@pytest.mark.parametrize("stage", ["enrol", "identity"])
def test_revocation_during_network_has_no_database_lock_and_cannot_publish_preparation(paired_workspace, stage):
    app, initial, client, remote = paired_workspace
    remote.gate = stage
    remote.release.clear()
    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(prepare, client)
        try:
            assert remote.entered.wait(3)
            # This commits while the network call is blocked: no SQLite/auth lock.
            with app.state.store.transaction() as conn:
                conn.execute("UPDATE account_security SET enabled=0 WHERE user_id=?", (initial["user"]["id"],))
        finally:
            remote.release.set()
        response = pending.result(timeout=5)
    assert response.status_code == 401
    with app.state.store.transaction() as conn:
        assert conn.execute("SELECT count(*) FROM cloud_sync_bindings").fetchone()[0] == 0
        assert app.state.cloud_connection.attempts(conn)[0]["status"] == "UNCERTAIN"
    assert TOKEN not in response.text and CODE not in response.text


def test_network_failure_records_uncertain_enrolment_and_never_retries_one_use_code(paired_workspace):
    app, _, client, remote = paired_workspace
    remote.identity_status = 503
    response = prepare(client)
    assert response.status_code == 502 and response.json()["error"]["code"] == "CONNECTION_UNCERTAIN"
    state = client.get("/api/cloud-connection").json()
    assert state["preparation"]["status"] == "UNCERTAIN"
    assert state["preparation"]["remote_device_id"] == remote.identity["device_id"]
    assert prepare(client).status_code == 409
    assert sum(method == "POST" for method, _, _ in remote.calls) == 1
    with app.state.store.transaction() as conn:
        assert conn.execute("SELECT count(*) FROM cloud_sync_bindings").fetchone()[0] == 0


def test_confirmation_cannot_cross_local_branch_or_another_manager_session(paired_workspace):
    app, initial, client, remote = paired_workspace
    branch = add_site(app.state.store, initial["user"]["organisation_id"], "Synthetic second local branch")
    grant_user(app.state.store, EMAIL, branch["id"], "MANAGER")
    client = client_for(app)
    prepared = prepare(client).json()
    other = client_for(app)
    assert confirmation(other, prepared).status_code == 404
    switched = client.post("/api/session/site", json={"site_id": branch["id"]})
    update_headers(client, switched.json())
    assert confirmation(client, prepared).status_code == 404
    assert client.get("/api/cloud-connection").json()["preparation"] is None
    assert len(remote.calls) == 2


def test_other_branch_sees_no_remote_identity_and_cannot_control_binding(paired_workspace):
    app, initial, client, _ = paired_workspace
    branch = add_site(app.state.store, initial["user"]["organisation_id"], "Synthetic second local branch")
    grant_user(app.state.store, EMAIL, branch["id"], "MANAGER")
    client = client_for(app)
    connection = configured(client)
    switched = client.post("/api/session/site", json={"site_id": branch["id"]})
    update_headers(client, switched.json())
    result = client.get("/api/cloud-connection").json()
    assert result["connection"] is None and result["occupied_elsewhere"] is True
    assert action(client, connection, "resume").status_code == 404
    assert "Synthetic cloud" not in json.dumps(result)


def test_pause_fences_remote_resume_already_in_flight(paired_workspace):
    app, _, client, remote = paired_workspace
    connection = configured(client)
    remote.gate = "identity"
    remote.entered.clear()
    remote.release.clear()
    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(action, client, connection, "resume")
        try:
            assert remote.entered.wait(3)
            paused = action(client, connection, "pause")
            assert paused.status_code == 200
            assert paused.json()["connection"]["generation"] > connection["connection"]["generation"]
        finally:
            remote.release.set()
        result = pending.result(timeout=5)
    assert result.status_code == 409
    assert client.get("/api/cloud-connection").json()["connection"]["state"] == "PAUSED"


def test_private_path_refused_before_remote_code_and_original_target_preserved(paired_workspace, tmp_path):
    app, _, client, remote = paired_workspace
    outside = tmp_path / "unrelated"
    outside.mkdir(mode=0o755)
    marker = outside / "sentinel"
    marker.write_bytes(b"synthetic unrelated bytes")
    try:
        app.state.cloud_connection.vault.directory.symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("Host cannot create synthetic symbolic links")
    original_mode = outside.stat().st_mode
    assert prepare(client).status_code == 503
    assert remote.calls == []
    assert marker.read_bytes() == b"synthetic unrelated bytes" and outside.stat().st_mode == original_mode
    assert list(outside.iterdir()) == [marker]


def test_missing_or_changed_private_key_fails_closed_for_provider_and_resume(paired_workspace):
    app, _, client, _ = paired_workspace
    connection = configured(client)
    resumed = action(client, connection, "resume").json()
    path = next(app.state.cloud_connection.vault.directory.glob("*.json"))
    value = json.loads(path.read_bytes())
    value["key"] = "A" * 43
    path.write_text(json.dumps(value))
    with app.state.store.transaction() as conn:
        with pytest.raises(ConnectionUnavailable, match="KEY_UNAVAILABLE"):
            app.state.cloud_connection.delivery_context(conn)
    status = client.get("/api/cloud-connection")
    assert status.json()["connection"]["credential_available"] is False
    assert TOKEN not in status.text
    assert action(client, resumed, "resume").status_code == 503


def test_source_provider_exact_installation_and_access_revocation_generation_fence(paired_workspace):
    app, _, client, _ = paired_workspace
    connection = action(client, configured(client), "resume").json()
    service = app.state.cloud_connection
    with app.state.store.transaction() as conn:
        ctx = service.delivery_context(conn)
        assert service.source_context(conn, Scope(str(uuid4()), ctx.scope.organisation_id, ctx.scope.site_id)) is None
        assert service.access_revoked(conn, ctx, now=datetime.now(timezone.utc)) is True
        assert service.delivery_context(conn) is None
        assert service.access_revoked(conn, ctx, now=datetime.now(timezone.utc)) is False
    state = client.get("/api/cloud-connection").json()
    assert state["connection"]["state"] == "PAUSED"
    assert state["connection"]["generation"] == connection["connection"]["generation"] + 1
    assert state["connection"]["error_code"] == "ACCESS_REVOKED"


def test_confirm_revocation_during_remote_identity_check_never_creates_binding(paired_workspace):
    app, initial, client, remote = paired_workspace
    prepared = prepare(client).json()
    remote.gate = "identity"
    remote.entered.clear()
    remote.release.clear()
    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(confirmation, client, prepared)
        try:
            assert remote.entered.wait(3)
            with app.state.store.transaction() as conn:
                conn.execute("UPDATE account_security SET enabled=0 WHERE user_id=?", (initial["user"]["id"],))
        finally:
            remote.release.set()
        response = pending.result(timeout=5)
    assert response.status_code == 401
    with app.state.store.transaction() as conn:
        assert conn.execute("SELECT count(*) FROM cloud_sync_bindings").fetchone()[0] == 0


def test_missing_private_key_does_not_prevent_authorized_pause(paired_workspace):
    app, _, client, _ = paired_workspace
    active = action(client, configured(client), "resume").json()
    next(app.state.cloud_connection.vault.directory.glob("*.json")).unlink()
    response = action(client, active, "pause")
    assert response.status_code == 200
    assert response.json()["connection"]["state"] == "PAUSED"
    assert response.json()["connection"]["credential_available"] is False
    with app.state.store.transaction() as conn:
        assert app.state.cloud_connection.delivery_context(conn) is None


def test_private_write_failure_after_enrol_preserves_uncertain_management_receipt(paired_workspace, monkeypatch):
    app, _, client, remote = paired_workspace
    def fail(*args):
        raise ConnectionUnavailable()
    monkeypatch.setattr(app.state.cloud_connection.vault, "write", fail)
    response = prepare(client)
    assert response.status_code == 502
    assert response.json()["error"]["code"] == "CONNECTION_UNCERTAIN"
    state = client.get("/api/cloud-connection").json()
    assert state["preparation"]["remote_device_id"] == remote.identity["device_id"]
    assert len(remote.calls) == 1
    assert prepare(client).status_code == 409


def test_delivery_summary_uses_scoped_receipts_and_actual_worker_state(paired_workspace):
    from dataclasses import dataclass
    from datetime import timedelta
    from services.api.cloud_observation import MappedObservation, source_event_id
    app, _, client, _ = paired_workspace
    active = action(client, configured(client), "resume").json()
    @dataclass
    class Worker:
        running: bool = False
    app.state.cloud_delivery = Worker()
    service = app.state.cloud_connection
    stamp = service.stamp()
    with app.state.store.transaction() as conn:
        ctx = service.delivery_context(conn)
        for _ in range(2):
            entity_id = str(uuid4())
            source_id = source_event_id(installation_id=ctx.scope.installation_id, binding_id=ctx.binding.id,
                organisation_id=ctx.scope.organisation_id, site_id=ctx.scope.site_id, entity_kind="interaction", entity_id=entity_id)
            payload = {"source_event_id": source_id, "event_code": "POSSIBLE_CONCEALMENT", "source_label": "Camera source · local observation",
                       "occurred_at": stamp.isoformat().replace("+00:00", "Z"), "historical": True}
            ctx.outbox.enqueue(conn, ctx.scope, ctx.binding, MappedObservation("interaction", entity_id, payload, stamp, stamp+timedelta(hours=24)), now=stamp)
        claim = ctx.outbox.claim(conn, ctx.scope, ctx.binding, now=stamp)
        ctx.outbox.acknowledge(conn, ctx.scope, ctx.binding, claim, receipt_id=str(uuid4()), now=stamp)
    result = client.get("/api/cloud-connection").json()
    assert result["delivery"] == {"pending": 1, "received": 1, "blocked": 0, "withdrawal_pending": 0, "worker_running": False}
    app.state.cloud_delivery.running = True
    assert client.get("/api/cloud-connection").json()["delivery"]["worker_running"] is True
    app.state.cloud_delivery.running = False
    disconnected = action(client, active, "disconnect").json()
    assert disconnected["delivery"]["withdrawal_pending"] == 1
    assert disconnected["delivery"]["received"] == 1
    assert disconnected["monitoring_status"] == "UNKNOWN"
