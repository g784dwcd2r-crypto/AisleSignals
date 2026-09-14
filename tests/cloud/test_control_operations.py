"""Real, process-owned PostgreSQL/API operations checks with synthetic identities.

Explicit invocation: CLOUD_RUN_POSTGRES_TESTS=1 .venv312/bin/python -m pytest
 tests/cloud/test_control_operations.py -q. Never uses a supplied database URL.
"""

from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import json
import os
from threading import Event
from uuid import uuid4

import psycopg
import pytest

from control_test_support import bootstrap, disposable_postgres, invited_client, new_client, pharmacy, reset_database

pytestmark = pytest.mark.skipif(os.environ.get("CLOUD_RUN_POSTGRES_TESTS") != "1", reason="Explicit disposable PostgreSQL opt-in required")


@pytest.fixture(scope="module")
def cluster():
    with disposable_postgres() as settings:
        yield settings


@pytest.fixture
def workspace(cluster):
    reset_database(cluster)
    owner = new_client(cluster)
    identity, _ = bootstrap(owner, cluster)
    north, south = pharmacy(owner, "Synthetic Harbour"), pharmacy(owner, "Synthetic South")
    yield cluster, owner, identity, north, south
    owner.close()


def code(client, branch, *, name="Synthetic MacBook", platform="MACOS"):
    response = client.post("/control-api/devices/enrolments", json={"pharmacy_id": branch["id"], "name": name, "platform": platform})
    assert response.status_code == 201, response.text
    return response.json()["token"]


def connect(client, token, *, name="Synthetic MacBook", platform="MACOS"):
    return client.post("/device-api/enrol", json={"token": token, "name": name, "platform": platform, "app_version": "synthetic-0.1"})


def device(client, branch):
    response = connect(client, code(client, branch))
    assert response.status_code == 201, response.text
    return response.json()


def bearer(device):
    return {"Authorization": "Bearer " + device["device_token"]}


def heartbeat(client, laptop, **changes):
    return client.post("/device-api/heartbeat", headers=bearer(laptop), json={"sequence": 0, "monitoring_status": "ACTIVE", "camera_count": 4, "app_version": "synthetic-0.1", **changes})


def alert_input(**changes):
    return {"source_event_id": str(uuid4()), "event_code": "POSSIBLE_CONCEALMENT", "source_label": "Synthetic Camera 2", "occurred_at": datetime.now(timezone.utc).isoformat(), "historical": False, **changes}


def ingest(client, laptop, body=None):
    return client.post("/device-api/alerts", headers=bearer(laptop), json=body or alert_input())


def review_body(version=1, **changes):
    return {"expected_version": version, "outcome": "UNCLEAR", "note": "Synthetic staff review: visible actions need context; no theft conclusion.", "create_incident": True, "title": "Synthetic reviewed follow-up", **changes}


def assert_private(response, *secrets):
    text = response.text
    for secret in secrets:
        assert secret not in text
    for field in ("credential_hash", "token_hash", "password_hash", "totp_encrypted", "device_token", "csrf_token"):
        assert f'"{field}"' not in text


def test_device_connection_heartbeat_retries_staleness_and_no_secret_directory(workspace):
    settings, owner, _, north, _ = workspace
    # Exercise a non-UTC database default even when CI itself runs in UTC.
    with psycopg.connect(settings.database_url) as conn:
        conn.execute("ALTER DATABASE postgres SET timezone TO 'Asia/Kathmandu'")
    token = code(owner, north)
    laptop_response = connect(owner, token)
    assert laptop_response.status_code == 201
    laptop = laptop_response.json()
    assert connect(owner, token).status_code == 401
    listing = owner.get("/control-api/devices")
    assert_private(listing, token, laptop["device_token"])
    assert listing.json()["items"][0]["connection_status"] == "NEVER_CONNECTED"
    first = heartbeat(owner, laptop)
    assert first.status_code == 200
    assert heartbeat(owner, laptop).json() == first.json()
    assert heartbeat(owner, laptop, camera_count=6).status_code == 409
    assert heartbeat(owner, laptop, sequence=1, monitoring_status="ACTIVE", camera_count=0).status_code == 422
    with psycopg.connect(settings.database_url) as conn:
        conn.execute("UPDATE aislesignals_control.devices SET last_seen_at=CURRENT_TIMESTAMP-interval '121 seconds' WHERE id=%s", (laptop["device_id"],))
    # Replaying an acknowledged heartbeat cannot keep an absent laptop online.
    assert heartbeat(owner, laptop).status_code == 200
    stale = owner.get("/control-api/devices").json()["items"][0]
    assert (stale["connection_status"], stale["monitoring_status"]) == ("OFFLINE", "UNKNOWN")
    summary = owner.get("/control-api/dashboard").json()["summary"]
    assert summary["connected_laptops"] == 0 and summary["total_laptops"] == 1
    assert heartbeat(owner, laptop, sequence=1).status_code == 200
    assert heartbeat(owner, laptop, sequence=0).status_code == 409
    assert owner.get("/control-api/dashboard").json()["summary"]["connected_laptops"] == 1


def test_branch_lists_details_writes_and_nested_cases_never_escape_current_memberships(workspace):
    settings, owner, _, north, south = workspace
    northern, southern = device(owner, north), device(owner, south)
    north_alert = ingest(owner, northern).json()["id"]
    south_alert = ingest(owner, southern).json()["id"]
    reviewed = owner.post(f"/control-api/alerts/{south_alert}/review", json=review_body()).json()
    manager, _, _, _ = invited_client(owner, settings, role="MANAGER", pharmacy_ids=[north["id"]], email="north.manager@example.test")
    with manager:
        for path in ("/dashboard", "/devices", "/alerts", "/incidents"):
            response = manager.get("/control-api" + path)
            assert response.status_code == 200
            assert south["id"] not in response.text and south["name"] not in response.text
            assert manager.get("/control-api" + path, params={"pharmacy_id": south["id"]}).status_code == 403
        assert manager.get(f"/control-api/alerts/{north_alert}").status_code == 200
        assert manager.get(f"/control-api/alerts/{south_alert}").status_code == 404
        assert manager.post(f"/control-api/alerts/{south_alert}/acknowledge", json={"expected_version": 1}).status_code == 404
        assert manager.post(f"/control-api/alerts/{south_alert}/review", json=review_body()).status_code == 404
        case_id = reviewed["incident"]["id"]
        assert manager.get(f"/control-api/incidents/{case_id}").status_code == 404
        assert manager.patch(f"/control-api/incidents/{case_id}", json={"expected_version": 1, "notes": "Unauthorised change"}).status_code == 404
        assert manager.post(f"/control-api/devices/{southern['device_id']}/revoke", json={"expected_version": 1}).status_code == 404
        assert manager.post("/control-api/devices/enrolments", json={"pharmacy_id": south["id"], "name": "Not permitted", "platform": "WINDOWS"}).status_code == 403
        assert manager.get("/control-api/users").status_code == 403
        assert manager.post("/control-api/pharmacies", json={"name": "Not permitted"}).status_code == 403


def test_reviewer_can_review_but_cannot_enrol_or_revoke_laptops(workspace):
    settings, owner, _, north, _ = workspace
    laptop = device(owner, north)
    event = ingest(owner, laptop).json()["id"]
    reviewer, _, _, _ = invited_client(owner, settings, pharmacy_ids=[north["id"]])
    with reviewer:
        assert reviewer.post("/control-api/devices/enrolments", json={"pharmacy_id": north["id"], "name": "Not permitted", "platform": "MACOS"}).status_code == 403
        assert reviewer.post(f"/control-api/devices/{laptop['device_id']}/revoke", json={"expected_version": 1}).status_code == 403
        result = reviewer.post(f"/control-api/alerts/{event}/review", json=review_body())
        assert result.status_code == 200
        assert result.json()["incident"]["classification"] == "UNCLEAR"
        assert result.json()["incident"]["reviewed_by"]


@pytest.mark.parametrize("change", ["disable", "scope", "role"])
def test_unconsumed_laptop_code_loses_authority_with_issuer(workspace, change):
    settings, owner, _, north, south = workspace
    manager, session, _, _ = invited_client(owner, settings, role="MANAGER", pharmacy_ids=[north["id"]], email="issuer@example.test")
    token = code(manager, north)
    user_id = session["user"]["id"]
    patch = {"active": False} if change == "disable" else {"pharmacy_ids": [south["id"]]} if change == "scope" else {"role": "REVIEWER"}
    response = owner.patch(f"/control-api/users/{user_id}", json={"expected_version": 1, **patch})
    assert response.status_code == 200, response.text
    assert manager.get("/control-api/devices").status_code == 401
    assert connect(owner, token).status_code == 401
    assert owner.get("/control-api/devices").json()["items"] == []
    manager.close()


def test_enrolment_expiry_mismatch_and_parallel_consumption_do_not_create_extra_devices(workspace):
    settings, owner, _, north, _ = workspace
    token = code(owner, north)
    assert connect(owner, token, platform="WINDOWS").status_code == 401
    with psycopg.connect(settings.database_url) as conn:
        conn.execute("UPDATE aislesignals_control.device_enrolments SET expires_at=CURRENT_TIMESTAMP-interval '1 second'")
    assert connect(owner, token).status_code == 401
    fresh = code(owner, north)
    with ThreadPoolExecutor(max_workers=2) as executor:
        responses = list(executor.map(lambda _: connect(owner, fresh), range(2)))
    assert sorted(response.status_code for response in responses) == [201, 401]
    assert len(owner.get("/control-api/devices").json()["items"]) == 1


def test_device_revoke_and_pharmacy_disable_reject_all_later_bearer_requests(workspace):
    _, owner, _, north, _ = workspace
    laptop = device(owner, north)
    assert heartbeat(owner, laptop).status_code == 200
    revoked = owner.post(f"/control-api/devices/{laptop['device_id']}/revoke", json={"expected_version": 1})
    assert revoked.status_code == 200
    assert revoked.json()["connection_status"] == "REVOKED"
    assert_private(revoked, laptop["device_token"])
    assert heartbeat(owner, laptop, sequence=1).status_code == 401
    assert ingest(owner, laptop).status_code == 401
    assert owner.post(f"/control-api/devices/{laptop['device_id']}/revoke", json={"expected_version": 1}).status_code == 409
    other = device(owner, north)
    pending = code(owner, north)
    assert owner.patch(f"/control-api/pharmacies/{north['id']}", json={"expected_version": 1, "active": False}).status_code == 200
    assert heartbeat(owner, other).status_code == 401
    assert ingest(owner, other).status_code == 401
    assert connect(owner, pending).status_code == 401
    assert owner.get("/control-api/devices").json()["items"] == []


def test_alert_ingestion_idempotency_historical_labelling_and_bearer_receipt_has_no_staff_notes(workspace):
    _, owner, _, north, _ = workspace
    laptop = device(owner, north)
    payload = alert_input(occurred_at=(datetime.now(timezone.utc)-timedelta(minutes=4)).isoformat())
    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _: ingest(owner, laptop, payload), range(2)))
    assert [response.status_code for response in results] == [201, 201]
    assert results[0].json() == results[1].json()
    alert_id = results[0].json()["id"]
    assert set(results[0].json()) == {"id", "received"}
    assert owner.get(f"/control-api/alerts/{alert_id}").json()["historical"] is True
    reviewed = owner.post(f"/control-api/alerts/{alert_id}/review", json=review_body(note="Synthetic private staff notes"))
    assert reviewed.status_code == 200
    assert ingest(owner, laptop, payload).json() == results[0].json()
    assert_private(ingest(owner, laptop, payload), "Synthetic private staff notes", laptop["device_token"])
    assert ingest(owner, laptop, {**payload, "source_label": "Changed camera"}).status_code == 409
    for injection in ({"pharmacy_id": north["id"]}, {"review": {"note": "forged"}}, {"camera_password": "synthetic-do-not-echo"}):
        response = ingest(owner, laptop, {**alert_input(), **injection})
        assert response.status_code == 422
        assert "synthetic-do-not-echo" not in response.text
    assert len(owner.get("/control-api/alerts").json()["items"]) == 1


def test_concurrent_staff_review_creates_one_case_and_stale_edits_conflict(workspace):
    _, owner, _, north, _ = workspace
    alert_id = ingest(owner, device(owner, north)).json()["id"]
    body = review_body()
    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _: owner.post(f"/control-api/alerts/{alert_id}/review", json=body), range(2)))
    assert sorted(response.status_code for response in results) == [200, 409]
    success = next(response.json() for response in results if response.status_code == 200)
    case = success["incident"]
    assert len(owner.get("/control-api/incidents").json()["items"]) == 1
    assert owner.post(f"/control-api/alerts/{alert_id}/review", json=review_body(version=2)).status_code == 409
    changed = owner.patch(f"/control-api/incidents/{case['id']}", json={"expected_version": 1, "status": "CLOSED", "notes": "Synthetic staff closed this follow-up."})
    assert changed.status_code == 200 and changed.json()["version"] == 2
    assert owner.patch(f"/control-api/incidents/{case['id']}", json={"expected_version": 1, "status": "OPEN"}).status_code == 409
    dashboard = owner.get("/control-api/dashboard").json()
    assert dashboard["summary"]["reviewed_incidents"] == 1 and dashboard["summary"]["open_alerts"] == 0


def test_late_principal_is_rechecked_inside_review_transaction_after_owner_revokes_access(workspace, monkeypatch):
    from services.cloud import control_operations
    settings, owner, _, north, _ = workspace
    reviewer, identity, _, _ = invited_client(owner, settings, pharmacy_ids=[north["id"]])
    alert_id = ingest(owner, device(owner, north)).json()["id"]
    entered, release = Event(), Event()
    original = control_operations.transaction

    @contextmanager
    def paused(request, principal):
        if principal.user_id == identity["user"]["id"]:
            entered.set()
            assert release.wait(8), "Synthetic race gate timed out"
        with original(request, principal) as scoped:
            yield scoped

    monkeypatch.setattr(control_operations, "transaction", paused)
    with ThreadPoolExecutor(max_workers=1) as executor:
        pending = executor.submit(reviewer.post, f"/control-api/alerts/{alert_id}/review", json=review_body())
        try:
            assert entered.wait(5)
            changed = owner.patch(f"/control-api/users/{identity['user']['id']}", json={"expected_version": 1, "active": False})
            assert changed.status_code == 200
        finally:
            release.set()
        assert pending.result(timeout=5).status_code == 401
    assert owner.get("/control-api/incidents").json()["items"] == []
    assert owner.get(f"/control-api/alerts/{alert_id}").json()["status"] == "OPEN"
    reviewer.close()


def test_wrong_authentication_boundary_csrf_and_invalid_bodies_do_not_expose_secrets(workspace):
    settings, owner, _, north, _ = workspace
    laptop = device(owner, north)
    stranger = new_client(settings)
    with stranger:
        assert stranger.get("/control-api/devices", headers=bearer(laptop)).status_code == 401
        assert stranger.post("/control-api/devices/enrolments", headers=bearer(laptop), json={"pharmacy_id": north["id"], "name": "Rejected", "platform": "MACOS"}).status_code == 401
    assert owner.post("/device-api/heartbeat", json={"sequence": 0, "monitoring_status": "ACTIVE", "camera_count": 4, "app_version": "synthetic"}).status_code == 401
    mutation = {"pharmacy_id": north["id"], "name": "Rejected", "platform": "MACOS"}
    assert owner.post("/control-api/devices/enrolments", headers={"X-CSRF-Token": "invalid"}, json=mutation).status_code == 403
    assert owner.post("/control-api/devices/enrolments", headers={"Origin": "https://evil.example"}, json=mutation).status_code == 403
    response = owner.post("/device-api/enrol", json={"token": "synthetic-secret-invalid", "name": "Rejected", "platform": "OTHER", "app_version": "synthetic"})
    assert response.status_code == 422
    assert_private(response, "synthetic-secret-invalid", laptop["device_token"])
    assert response.headers["cache-control"] == "no-store"


def test_separate_organisation_cannot_read_or_modify_any_operational_record(workspace):
    """Second tenant uses a synthetic hashed/encrypted seed, then real password/MFA login."""
    import base64
    import secrets
    import time
    from services.cloud.control_auth import _insert_user, hash_password, totp_code
    settings, owner, _, north, _ = workspace
    laptop = device(owner, north)
    alert_id = ingest(owner, laptop).json()["id"]
    case_id = owner.post(f"/control-api/alerts/{alert_id}/review", json=review_body()).json()["incident"]["id"]
    outsider = new_client(settings)
    secret = base64.b32encode(secrets.token_bytes(20)).decode()
    with outsider.app.state.control_store.transaction() as conn:
        org_id = str(uuid4())
        conn.execute("INSERT INTO aislesignals_control.organisations(id,name) VALUES(%s,%s)", (org_id, "Synthetic Unrelated Group"))
        _insert_user(conn, settings, organisation_id=org_id, name="Synthetic Other Owner", email="other.owner@example.test", password_hash=hash_password("Synthetic-other-password-489"), secret=secret, counter=-1, role="OWNER", pharmacy_ids=[])
    challenge = outsider.post("/control-api/login", json={"email": "other.owner@example.test", "password": "Synthetic-other-password-489"})
    assert challenge.status_code == 200
    session = outsider.post("/control-api/login/mfa", json={"challenge_token": challenge.json()["challenge_token"], "code": totp_code(secret, int(time.time())//30)})
    assert session.status_code == 200
    outsider.headers["X-CSRF-Token"] = session.json()["csrf_token"]
    with outsider:
        for path in ("/devices", "/alerts", "/incidents"):
            assert outsider.get("/control-api"+path).json()["items"] == []
            assert outsider.get("/control-api"+path, params={"pharmacy_id": north["id"]}).status_code == 403
        assert outsider.get(f"/control-api/alerts/{alert_id}").status_code == 404
        assert outsider.get(f"/control-api/incidents/{case_id}").status_code == 404
        assert outsider.patch(f"/control-api/incidents/{case_id}", json={"expected_version": 1, "status": "CLOSED"}).status_code == 404
        assert outsider.post(f"/control-api/devices/{laptop['device_id']}/revoke", json={"expected_version": 1}).status_code == 404


def test_device_credentials_and_enrolment_codes_are_hashes_only_in_persistent_records(workspace):
    from hashlib import sha256
    settings, owner, _, north, _ = workspace
    token = code(owner, north)
    laptop = connect(owner, token).json()
    with psycopg.connect(settings.database_url) as conn:
        assert conn.execute("SELECT token_hash FROM aislesignals_control.device_enrolments").fetchone()[0] == sha256(token.encode()).hexdigest()
        assert conn.execute("SELECT credential_hash FROM aislesignals_control.devices").fetchone()[0] == sha256(laptop["device_token"].encode()).hexdigest()
        audits = json.dumps(conn.execute("SELECT row_to_json(a) FROM aislesignals_control.audit_entries a").fetchall(), default=str)
    assert token not in audits and laptop["device_token"] not in audits
    assert "DEVICE_ENROLLED" in audits and "DEVICE_ENROLMENT_CREATED" in audits
