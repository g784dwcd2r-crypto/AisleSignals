"""Read-only device identity through real bearer auth and owned PostgreSQL.

Run with CLOUD_RUN_POSTGRES_TESTS=1. All data and credentials are synthetic;
control_test_support creates a new cluster, never a configured customer DB.
"""

from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
import os
import secrets
from threading import Event
from uuid import uuid4

import psycopg
import pytest

from control_test_support import bootstrap, disposable_postgres, new_client, pharmacy, reset_database
from services.cloud.control_operations import fingerprint

pytestmark = pytest.mark.skipif(os.environ.get("CLOUD_RUN_POSTGRES_TESTS") != "1", reason="Explicit disposable PostgreSQL opt-in required")
SCHEMA = "aislesignals_control"
FIELDS = {"device_id", "organisation_id", "organisation_name", "pharmacy_id", "pharmacy_name", "name", "platform", "app_version"}


@pytest.fixture(scope="module")
def cluster():
    with disposable_postgres() as settings:
        yield settings


@pytest.fixture
def workspace(cluster):
    reset_database(cluster)
    with new_client(cluster) as client:
        session, _ = bootstrap(client, cluster)
        branch = pharmacy(client, "Synthetic Identity Harbour")
        yield cluster, client, session, branch


def enrolled(client, branch, *, name="Synthetic Identity MacBook", platform="MACOS"):
    response = client.post("/control-api/devices/enrolments", json={"pharmacy_id": branch["id"], "name": name, "platform": platform})
    assert response.status_code == 201, response.text
    code = response.json()["token"]
    response = client.post("/device-api/enrol", json={"token": code, "name": name, "platform": platform, "app_version": "synthetic-identity-1"})
    assert response.status_code == 201, response.text
    # Do not break existing strict enrolment clients to provide identity.
    assert set(response.json()) == {"device_id", "device_token", "heartbeat_interval_seconds"}
    return response.json(), code


def identity(client, device, **kwargs):
    return client.get("/device-api/identity", headers={"Authorization": "Bearer " + device["device_token"]}, **kwargs)


def snapshot(settings):
    """Snapshot all application tables; SELECT row locks are not state changes."""
    with psycopg.connect(settings.database_url) as conn:
        tables = conn.execute("SELECT tablename FROM pg_tables WHERE schemaname=%s ORDER BY tablename", (SCHEMA,)).fetchall()
        return {name: conn.execute(psycopg.sql.SQL("SELECT row_to_json(t)::text FROM {}.{} t ORDER BY 1").format(psycopg.sql.Identifier(SCHEMA), psycopg.sql.Identifier(name))).fetchall() for (name,) in tables}


def no_secrets(response, *values):
    for value in values:
        assert value not in response.text
    for key in ("device_token", "credential_hash", "token_hash", "password_hash", "csrf_token", "review", "users", "items"):
        assert f'"{key}"' not in response.text
    assert response.headers["cache-control"] == "no-store"
    assert "set-cookie" not in response.headers


@pytest.mark.parametrize("platform", ["MACOS", "WINDOWS", "OTHER"])
def test_identity_returns_exact_server_target_without_any_state_or_activity_change(workspace, platform):
    settings, client, session, branch = workspace
    device, code = enrolled(client, branch, platform=platform)
    before = snapshot(settings)
    first = identity(client, device)
    assert first.status_code == 200, first.text
    assert first.json() == {
        "device_id": device["device_id"], "organisation_id": branch["organisation_id"],
        "organisation_name": session["organisation"]["name"], "pharmacy_id": branch["id"],
        "pharmacy_name": branch["name"], "name": "Synthetic Identity MacBook",
        "platform": platform, "app_version": "synthetic-identity-1",
    }
    assert set(first.json()) == FIELDS
    assert identity(client, device).json() == first.json()
    assert snapshot(settings) == before
    no_secrets(first, device["device_token"], code, fingerprint(device["device_token"]), session["csrf_token"])
    with psycopg.connect(settings.database_url) as conn:
        row = conn.execute(f"SELECT last_seen_at,heartbeat_sequence,monitoring_status,camera_count FROM {SCHEMA}.devices WHERE id=%s", (device["device_id"],)).fetchone()
    assert row == (None, -1, "UNKNOWN", 0)


@pytest.mark.parametrize("credential", ["missing", "wrong", "enrolment", "cookie", "short"])
def test_invalid_bearer_and_staff_cookie_never_return_identity(workspace, credential):
    settings, client, session, branch = workspace
    device, code = enrolled(client, branch)
    value = {"missing": None, "wrong": secrets.token_urlsafe(48), "enrolment": code,
             "cookie": None, "short": "short-synthetic"}[credential]
    headers = {"Authorization": "Bearer " + value} if value else {}
    before = snapshot(settings)
    if credential == "missing":
        with new_client(settings) as anonymous:
            response = anonymous.get("/device-api/identity", headers=headers)
    else:
        # The authenticated owner's cookie cannot grant device authority.
        response = client.get("/device-api/identity", headers=headers)
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "DEVICE_UNAUTHORIZED"
    no_secrets(response, device["device_token"], code, session["csrf_token"], value or "unrelated-synthetic-secret")
    assert snapshot(settings) == before


@pytest.mark.parametrize("change", ["revoke", "disable-pharmacy"])
def test_identity_refuses_current_revocation_and_inactive_pharmacy(workspace, change):
    settings, client, _, branch = workspace
    device, _ = enrolled(client, branch)
    assert identity(client, device).status_code == 200
    if change == "revoke":
        response = client.post(f"/control-api/devices/{device['device_id']}/revoke", json={"expected_version": 1})
    else:
        response = client.patch(f"/control-api/pharmacies/{branch['id']}", json={"expected_version": 1, "active": False})
    assert response.status_code == 200
    before = snapshot(settings)
    response = identity(client, device)
    assert response.status_code == 401
    no_secrets(response, device["device_token"], branch["name"], device["device_id"])
    assert snapshot(settings) == before


def test_query_header_or_path_ids_cannot_select_another_pharmacy_or_device(workspace):
    settings, client, _, branch = workspace
    device, _ = enrolled(client, branch)
    another = pharmacy(client, "Synthetic Unrelated Branch")
    other, _ = enrolled(client, another, name="Synthetic Different Laptop", platform="WINDOWS")
    response = client.get("/device-api/identity", headers={"Authorization": "Bearer " + device["device_token"],
        "X-Pharmacy-Id": another["id"], "X-Device-Id": other["device_id"]},
        params={"pharmacy_id": another["id"], "device_id": other["device_id"], "organisation_id": str(uuid4())})
    assert response.status_code == 200
    assert response.json()["device_id"] == device["device_id"]
    assert response.json()["pharmacy_id"] == branch["id"]
    no_secrets(response, another["name"], other["device_id"], other["device_token"])
    assert client.get(f"/device-api/identity/{other['device_id']}", headers={"Authorization": "Bearer " + device["device_token"]}).status_code == 404
    # A bearer has no new pharmacy/device directory or staff-data authority.
    with new_client(settings) as device_only:
        for path in ("/control-api/pharmacies", "/control-api/devices", "/control-api/users"):
            assert device_only.get(path, headers={"Authorization": "Bearer " + device["device_token"]}).status_code == 401


def test_other_organisation_bearer_can_only_read_its_own_server_join(workspace):
    settings, client, _, branch = workspace
    first, _ = enrolled(client, branch)
    second = {"device_id": str(uuid4()), "device_token": secrets.token_urlsafe(48)}
    organisation_id, pharmacy_id = str(uuid4()), str(uuid4())
    # Minimal synthetic second-tenant fixture; production bearer validation is
    # exercised unchanged, with only a hash persisted in its actual device row.
    with psycopg.connect(settings.database_url) as conn:
        conn.execute(f"INSERT INTO {SCHEMA}.organisations(id,name) VALUES(%s,%s)", (organisation_id, "Synthetic Separate Group"))
        conn.execute(f"INSERT INTO {SCHEMA}.pharmacies(id,organisation_id,name) VALUES(%s,%s,%s)", (pharmacy_id, organisation_id, "Synthetic Separate Pharmacy"))
        conn.execute(f"INSERT INTO {SCHEMA}.devices(id,organisation_id,pharmacy_id,name,platform,credential_hash,app_version) VALUES(%s,%s,%s,%s,%s,%s,%s)",
                     (second["device_id"], organisation_id, pharmacy_id, "Synthetic Separate Laptop", "OTHER", fingerprint(second["device_token"]), "synthetic-separate-1"))
    response = identity(client, second, params={"device_id": first["device_id"], "pharmacy_id": branch["id"], "organisation_id": branch["organisation_id"]})
    assert response.status_code == 200
    assert response.json() == {"device_id": second["device_id"], "organisation_id": organisation_id, "organisation_name": "Synthetic Separate Group",
        "pharmacy_id": pharmacy_id, "pharmacy_name": "Synthetic Separate Pharmacy", "name": "Synthetic Separate Laptop", "platform": "OTHER", "app_version": "synthetic-separate-1"}
    no_secrets(response, first["device_id"], branch["id"], branch["name"], first["device_token"], second["device_token"])
    assert identity(client, first).json()["organisation_id"] == branch["organisation_id"]


def test_identity_reflects_current_names_and_reported_app_version_but_never_changes_binding(workspace):
    _, client, _, branch = workspace
    device, _ = enrolled(client, branch)
    response = client.patch(f"/control-api/pharmacies/{branch['id']}", json={"expected_version": 1, "name": "Synthetic Renamed Pharmacy"})
    assert response.status_code == 200
    response = client.post("/device-api/heartbeat", headers={"Authorization": "Bearer " + device["device_token"]},
        json={"sequence": 0, "monitoring_status": "UNKNOWN", "camera_count": 0, "app_version": "synthetic-identity-2"})
    assert response.status_code == 200
    response = identity(client, device)
    assert response.json()["pharmacy_name"] == "Synthetic Renamed Pharmacy"
    assert response.json()["app_version"] == "synthetic-identity-2"
    assert response.json()["pharmacy_id"] == branch["id"]
    assert response.json()["device_id"] == device["device_id"]


@pytest.mark.parametrize("change", ["revoke", "disable-pharmacy"])
def test_identity_waits_for_authority_transaction_then_rechecks_current_state(workspace, monkeypatch, change):
    settings, client, _, branch = workspace
    device, _ = enrolled(client, branch)
    entered = Event()
    store = client.app.state.control_store
    original = store.transaction

    class Connection:
        def __init__(self, conn):
            self.conn = conn

        def execute(self, query, params=None):
            if "organisations" in query and "FOR SHARE" in query:
                entered.set()
            return self.conn.execute(query, params)

    @contextmanager
    def observed():
        with original() as conn:
            yield Connection(conn)

    monkeypatch.setattr(store, "transaction", observed)
    # The test owns this cluster and holds the same organisation-first lock as
    # administration. No sleeps or fake API/authentication results are used.
    with psycopg.connect(settings.database_url) as changing, ThreadPoolExecutor(max_workers=1) as executor:
        changing.execute(f"SELECT id FROM {SCHEMA}.organisations WHERE id=%s FOR UPDATE", (branch["organisation_id"],))
        if change == "revoke":
            changing.execute(f"UPDATE {SCHEMA}.devices SET revoked_at=CURRENT_TIMESTAMP WHERE id=%s", (device["device_id"],))
        else:
            changing.execute(f"UPDATE {SCHEMA}.pharmacies SET active=false WHERE id=%s", (branch["id"],))
        pending = executor.submit(identity, client, device)
        try:
            assert entered.wait(2), "Identity never reached the organisation authority lock"
        finally:
            changing.commit()
        response = pending.result(timeout=5)
    assert response.status_code == 401
    no_secrets(response, device["device_token"], device["device_id"], branch["name"])
