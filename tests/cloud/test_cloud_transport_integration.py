"""Real PostgreSQL/API-issued credentials through an owned HTTP test bridge."""

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import json
import os
import threading
from uuid import uuid4

import psycopg
import pytest

from control_test_support import bootstrap, disposable_postgres, new_client, pharmacy, reset_database
from services.api.cloud_transport import CloudTransport, CloudTransportError
from services.api.cloud_observation import source_event_id


pytestmark = pytest.mark.skipif(os.environ.get("CLOUD_RUN_POSTGRES_TESTS") != "1",
                              reason="Explicit disposable PostgreSQL opt-in required")


@contextmanager
def bridge(device):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def respond(self):
            assert self.path in {"/device-api/enrol", "/device-api/identity", "/device-api/heartbeat",
                                 "/device-api/sync/v1/observations", "/device-api/sync/v1/withdrawals"}
            headers = {}
            if "Authorization" in self.headers:
                headers["Authorization"] = self.headers["Authorization"]
            data = self.rfile.read(int(self.headers.get("Content-Length", "0")))
            reply = device.request(self.command, self.path, headers=headers,
                                   **({"json": json.loads(data)} if data else {}))
            self.send_response(reply.status_code)
            self.send_header("Content-Length", str(len(reply.content)))
            self.end_headers()
            self.wfile.write(reply.content)

        do_GET = respond
        do_POST = respond

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    worker = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": .01}, daemon=True)
    worker.start()
    try:
        yield CloudTransport(f"http://127.0.0.1:{server.server_port}", allow_local_test=True)
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=5)
        assert not worker.is_alive()


def observation(admitted=None):
    stamp = admitted or datetime.now(timezone.utc)
    source = source_event_id(installation_id=str(uuid4()), binding_id=str(uuid4()),
                             organisation_id="synthetic-org", site_id="synthetic-site",
                             entity_kind="interaction", entity_id=str(uuid4()))
    return {"source_event_id": source, "event_code": "POSSIBLE_CONCEALMENT",
            "source_label": "Camera 2 of 6 · 3x2 screen grid · local observation",
            "occurred_at": stamp.isoformat().replace("+00:00", "Z"), "historical": True}


@pytest.fixture
def paired_system():
    with disposable_postgres() as settings:
        reset_database(settings)
        with new_client(settings) as owner, new_client(settings) as device:
            bootstrap(owner, settings)
            branch = pharmacy(owner, "Synthetic Sync Transport Branch")
            response = owner.post("/control-api/devices/enrolments", json={
                "pharmacy_id": branch["id"], "name": "Synthetic connection", "platform": "MACOS"})
            assert response.status_code == 201
            with bridge(device) as sender:
                credential = sender.enrol(response.json()["token"], name="Synthetic connection", platform="MACOS")
                yield settings, owner, sender, credential


def test_real_enrolment_identity_heartbeat_and_revocation_contract():
    with disposable_postgres() as settings:
        reset_database(settings)
        with new_client(settings) as owner, new_client(settings) as device:
            bootstrap(owner, settings)
            branch = pharmacy(owner, "Synthetic Transport Branch")
            response = owner.post("/control-api/devices/enrolments", json={
                "pharmacy_id":branch["id"], "name":"Synthetic connection", "platform":"WINDOWS"})
            assert response.status_code == 201
            code = response.json()["token"]
            assert len(code) == 43

            with bridge(device) as transport:
                credential = transport.enrol(code,name="Synthetic connection",platform="WINDOWS")
                assert len(credential["device_token"]) == 64
                identity = transport.identity(credential["device_token"],expected_device_id=credential["device_id"])
                assert identity["pharmacy_id"] == branch["id"]
                assert identity["organisation_id"] == branch["organisation_id"]
                assert transport.heartbeat(credential["device_token"],sequence=1)["ok"] is True
                reply = owner.post(f'/control-api/devices/{credential["device_id"]}/revoke',json={"expected_version":1})
                assert reply.status_code == 200
                with pytest.raises(CloudTransportError,match="^ACCESS_REVOKED$"):
                    transport.identity(credential["device_token"],expected_device_id=credential["device_id"])


def test_real_observation_retry_conflict_withdrawal_and_revocation(paired_system):
    settings, owner, sender, credential = paired_system
    token = credential["device_token"]
    body = observation()
    source = body["source_event_id"]
    deadline = datetime.fromisoformat(body["occurred_at"].replace("Z", "+00:00")) + timedelta(hours=24)
    result = sender.observation(token, body, expires_at=deadline, expected_source_event_id=source)
    assert result["received"] is True and result["source_state"] == "AVAILABLE"
    assert sender.observation(token, body, expires_at=deadline, expected_source_event_id=source,
                              expected_receipt_id=result["id"]) == result
    alert = owner.get("/control-api/alerts/" + result["id"]).json()
    assert alert["historical"] is True and alert["timestamp_basis"] == "LAPTOP_REPORTED"
    assert datetime.fromisoformat(alert["source_expires_at"].replace("Z", "+00:00")) == deadline
    assert len(owner.get("/control-api/alerts").json()["items"]) == 1
    for changed, end in [({**body, "event_code": "RESTRICTED_ZONE_ENTRY"}, deadline),
                         (body, deadline - timedelta(microseconds=1))]:
        with pytest.raises(CloudTransportError, match="^EVENT_CONFLICT$"):
            sender.observation(token, changed, expires_at=end, expected_source_event_id=source)
    withdrawal = {"source_event_id": source, "reason": "LOCAL_DELETED"}
    ack = sender.withdrawal(token, withdrawal, expected_source_event_id=source)
    assert ack == {"source_event_id": source, "withdrawn": True}
    assert sender.withdrawal(token, withdrawal, expected_source_event_id=source) == ack
    assert sender.observation(token, body, expires_at=deadline, expected_source_event_id=source,
                              expected_receipt_id=result["id"]) == {**result, "source_state": "WITHDRAWN"}
    assert owner.get("/control-api/alerts").json()["items"] == []
    with psycopg.connect(settings.database_url) as conn:
        assert conn.execute("SELECT count(*) FROM aislesignals_control.device_sync_receipts").fetchone()[0] == 1
        assert conn.execute("SELECT last_seen_at,monitoring_status,camera_count FROM aislesignals_control.devices").fetchone() == (None, "UNKNOWN", 0)
    assert owner.post(f'/control-api/devices/{credential["device_id"]}/revoke', json={"expected_version": 1}).status_code == 200
    with pytest.raises(CloudTransportError, match="^ACCESS_REVOKED$"):
        sender.observation(token, body, expires_at=deadline, expected_source_event_id=source)
    with pytest.raises(CloudTransportError, match="^ACCESS_REVOKED$"):
        sender.withdrawal(token, withdrawal, expected_source_event_id=source)


def test_real_expired_arrival_and_server_clock_limits(paired_system):
    settings, owner, sender, credential = paired_system
    token, stamp = credential["device_token"], datetime.now(timezone.utc)
    body = observation(stamp - timedelta(hours=2))
    source = body["source_event_id"]
    deadline = stamp - timedelta(hours=1)
    result = sender.observation(token, body, expires_at=deadline, expected_source_event_id=source)
    assert result["source_state"] == "EXPIRED"
    assert sender.observation(token, body, expires_at=deadline, expected_source_event_id=source,
                              expected_receipt_id=result["id"]) == result
    for admitted in [stamp + timedelta(minutes=10), stamp - timedelta(days=31)]:
        invalid = observation(admitted)
        with pytest.raises(CloudTransportError, match="^INVALID_EVENT_TIME$"):
            sender.observation(token, invalid, expires_at=admitted+timedelta(hours=1),
                               expected_source_event_id=invalid["source_event_id"])
    assert owner.get("/control-api/alerts").json()["items"] == []
    with psycopg.connect(settings.database_url) as conn:
        assert conn.execute("SELECT count(*) FROM aislesignals_control.alerts").fetchone()[0] == 0
        assert conn.execute("SELECT expires_at FROM aislesignals_control.device_sync_receipts").fetchone()[0] == deadline


def test_real_withdrawal_before_arrival_never_creates_alert(paired_system):
    settings, owner, sender, credential = paired_system
    token, body = credential["device_token"], observation()
    source = body["source_event_id"]
    deadline = datetime.fromisoformat(body["occurred_at"].replace("Z", "+00:00")) + timedelta(hours=1)
    result = sender.withdrawal(token, {"source_event_id": source, "reason": "LOCAL_EXPORT_REMOVED"},
                               expected_source_event_id=source)
    assert result == {"source_event_id": source, "withdrawn": True}
    receipt = sender.observation(token, body, expires_at=deadline, expected_source_event_id=source)
    assert receipt["source_state"] == "WITHDRAWN"
    assert sender.observation(token, body, expires_at=deadline, expected_source_event_id=source,
                              expected_receipt_id=receipt["id"]) == receipt
    assert owner.get("/control-api/alerts").json()["items"] == []
    with psycopg.connect(settings.database_url) as conn:
        assert conn.execute("SELECT count(*) FROM aislesignals_control.alerts").fetchone()[0] == 0
        assert conn.execute("SELECT withdrawal_reason FROM aislesignals_control.device_sync_receipts").fetchone()[0] == "LOCAL_EXPORT_REMOVED"
