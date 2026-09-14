"""Synthetic private runtime reports; never read a real launcher's report."""
import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import Event
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from services.api.app import create_app
from services.api.pilot_identity import initialise, add_site, grant_user
from test_pilot_identity import EMAIL, PASSWORD, client_for, BASE
from test_interactions import MockProvider, submit, wait_job, sample


def write_report(path, **changes):
    stamp = datetime.now(timezone.utc).isoformat()
    report = dict(schema_version=1, runtime_id="a" * 32, recovery_generation=0,
                  generated_at=stamp, state="SERVICES_READY", detail="PRIVATE /secret/path", rearm_required=False,
                  services=[dict(name=name, running=True, disabled=False, health="READY", last_checked_at=stamp)
                            for name in ("api", "vision")])
    report.update(changes)
    path.write_text(json.dumps(report))
    return report


@pytest.fixture
def installation(tmp_path, monkeypatch, request):
    monkeypatch.setenv("AISLESIGNALS_PILOT_SUPERVISED_CHILD", "1")
    monkeypatch.setenv("AISLESIGNALS_VISION_DISABLED", "1" if getattr(request, "param", False) else "0")
    app = create_app(tmp_path / "pilot.db", tmp_path / "web", mode="pilot")
    initialise(app.state.store, "Synthetic runtime group", "Synthetic runtime branch", EMAIL, "Synthetic Manager", PASSWORD)
    app.state.interactions.provider = MockProvider()
    path = tmp_path / "runtime-status.json"
    write_report(path)
    client = client_for(app)
    try:
        yield app, client, path
    finally:
        app.state.interactions.provider.release.set()
        app.state.interactions.close()


def test_authenticated_projection_has_no_private_fields_and_does_not_refresh_idle_session(installation):
    app, client, _ = installation
    anonymous = TestClient(app, base_url=BASE)
    assert anonymous.get("/api/runtime/health").status_code == 401
    with app.state.store.transaction() as conn:
        before = conn.execute("SELECT last_seen FROM sessions").fetchone()[0]
    response = client.get("/api/runtime/health")
    value = response.json()
    assert response.headers["Cache-Control"] == "no-store"
    assert set(value) == {"api_id", "runtime_id", "recovery_generation", "context", "site_id", "supervised", "state", "monitoring_allowed", "product_available", "report_age_ms"}
    assert value["monitoring_allowed"] and len(value["context"]) == 64
    assert "PRIVATE" not in response.text and "/secret/path" not in response.text
    with app.state.store.transaction() as conn:
        assert conn.execute("SELECT last_seen FROM sessions").fetchone()[0] == before


def test_health_completes_while_real_interaction_admission_holds_writer_lock(installation, monkeypatch):
    app, client, _ = installation
    client.headers["X-AisleSignals-Runtime"] = client.get("/api/runtime/health").json()["context"]
    entered, release = Event(), Event()
    original_put = app.state.store.put

    def hold_admission(conn, user, kind, item):
        if kind == "interaction" and item["status"] == "pending":
            assert conn.in_transaction
            entered.set()
            assert release.wait(5), "Synthetic admission was not released"
        return original_put(conn, user, kind, item)

    monkeypatch.setattr(app.state.store, "put", hold_admission)
    with ThreadPoolExecutor(max_workers=2) as pool:
        admission = pool.submit(submit, client)
        try:
            assert entered.wait(2), "The real admission did not reach its write transaction"
            # The writer stays held until this read has finished. A BEGIN
            # IMMEDIATE health dependency cannot pass, regardless of disk speed.
            heartbeat = pool.submit(client.get, "/api/runtime/health")
            response = heartbeat.result(timeout=0.8)
            assert response.status_code == 200, response.text
            assert response.json()["monitoring_allowed"] is True
            assert not release.is_set() and not admission.done()
        finally:
            release.set()
        response = admission.result(timeout=2)
        assert response.status_code == 202, response.text
        assert wait_job(client, response.json()["id"])["status"] == "completed"


@pytest.mark.parametrize("change,code", [
    ("DELETE FROM sessions", "SESSION_EXPIRED"),
    ("UPDATE sessions SET last_seen=last_seen-901", "SESSION_EXPIRED"),
    ("UPDATE sessions SET created_at=created_at-28801", "SESSION_EXPIRED"),
    ("UPDATE account_security SET enabled=0", "AUTH_REQUIRED"),
    ("DELETE FROM memberships", "AUTH_REQUIRED"),
    ("DELETE FROM session_scopes", "AUTH_REQUIRED"),
    ("UPDATE session_scopes SET organisation_id='unrelated-synthetic-group'", "AUTH_REQUIRED"),
    ("UPDATE users SET organisation_id='unrelated-synthetic-group'", "AUTH_REQUIRED"),
    ("DELETE FROM entities WHERE kind='site'", "AUTH_REQUIRED"),
])
def test_health_uses_committed_authority_and_never_writes_to_reject_it(installation, change, code):
    app, client, _ = installation
    writer = sqlite3.connect(app.state.store.path)
    try:
        writer.execute("BEGIN IMMEDIATE")
        writer.execute(change)
        with ThreadPoolExecutor(max_workers=1) as pool:
            # Uncommitted authority changes cannot leak into the read snapshot.
            response = pool.submit(client.get, "/api/runtime/health").result(timeout=0.8)
            assert response.status_code == 200
            writer.commit()
            before = writer.execute("SELECT * FROM sessions").fetchall()
            writer.execute("BEGIN IMMEDIATE")
            # Once committed, the same cookie fails even while a separate writer
            # is reserved. Expiry rejection must not wait to delete that session.
            response = pool.submit(client.get, "/api/runtime/health").result(timeout=0.8)
            assert response.status_code == 401, response.text
            assert response.json()["error"]["code"] == code
            assert writer.execute("SELECT * FROM sessions").fetchall() == before
    finally:
        writer.rollback()
        writer.close()


def test_health_rereads_branch_rotation_and_rejects_previous_cookie(installation):
    app, _, _ = installation
    with app.state.store.transaction() as conn:
        organisation_id = conn.execute("SELECT organisation_id FROM users").fetchone()[0]
    other = add_site(app.state.store, organisation_id, "Synthetic second runtime branch")
    grant_user(app.state.store, EMAIL, other["id"], "MANAGER")
    client = client_for(app)
    original_site = client.get("/api/runtime/health").json()["site_id"]
    previous = TestClient(app, base_url=BASE, cookies=dict(client.cookies))
    switched = client.post("/api/session/site", json={"site_id": other["id"]})
    assert switched.status_code == 200, switched.text
    assert client.get("/api/runtime/health").json()["site_id"] == other["id"] != original_site
    assert previous.get("/api/runtime/health").status_code == 401


@pytest.mark.parametrize("damage,repair", [
    ("PRAGMA user_version=999", "PRAGMA user_version=3"),
    ("UPDATE runtime_settings SET value='synthetic' WHERE key='mode'",
     "UPDATE runtime_settings SET value='pilot' WHERE key='mode'"),
    ("UPDATE runtime_settings SET value='invalid-private-identity' WHERE key='installation_id'", None),
])
def test_health_still_validates_schema_mode_and_installation_provenance(installation, damage, repair):
    app, client, _ = installation
    with sqlite3.connect(app.state.store.path) as conn:
        identity = conn.execute("SELECT value FROM runtime_settings WHERE key='installation_id'").fetchone()[0]
        conn.execute(damage)
    try:
        response = client.get("/api/runtime/health")
        assert response.status_code == 503, response.text
        assert response.json()["error"]["code"] == "STORAGE_UNAVAILABLE"
        assert "invalid-private-identity" not in response.text
        assert "context" not in response.json()
    finally:
        with sqlite3.connect(app.state.store.path) as conn:
            if repair:
                conn.execute(repair)
            else:
                conn.execute("UPDATE runtime_settings SET value=? WHERE key='installation_id'", (identity,))


def test_health_connection_itself_cannot_mutate_session_state(installation, monkeypatch):
    app, client, _ = installation
    with app.state.store.transaction() as conn:
        before = [tuple(row) for row in conn.execute("SELECT * FROM sessions")]

    def unexpected_write(conn, session):
        conn.execute("DELETE FROM sessions")
        raise AssertionError("A read-only health connection allowed a write")

    monkeypatch.setattr(app.state.store, "resolve_session_user", unexpected_write)
    response = client.get("/api/runtime/health")
    assert response.status_code == 503, response.text
    with app.state.store.transaction() as conn:
        assert [tuple(row) for row in conn.execute("SELECT * FROM sessions")] == before


@pytest.mark.parametrize("damage", ["missing", "stale", "future", "suspect", "bad_json", "symlink", "large", "old_probe", "bad_identity"])
def test_report_failures_block_monitoring_but_leave_casework_available(installation, damage):
    _, client, path = installation
    token = client.get("/api/runtime/health").json()["context"]
    if damage == "missing": path.unlink()
    elif damage == "stale": write_report(path, generated_at=(datetime.now(timezone.utc)-timedelta(seconds=9)).isoformat())
    elif damage == "future": write_report(path, generated_at=(datetime.now(timezone.utc)+timedelta(seconds=3)).isoformat())
    elif damage == "suspect":
        value=write_report(path, state="DEGRADED"); value["services"][1]["health"]="SUSPECT"; path.write_text(json.dumps(value))
    elif damage == "bad_json": path.write_text("{")
    elif damage == "large": path.write_text(" " * 32769)
    elif damage == "bad_identity": write_report(path, recovery_generation=True)
    elif damage == "old_probe":
        value=write_report(path); value["services"][0]["last_checked_at"]=(datetime.now(timezone.utc)-timedelta(seconds=9)).isoformat(); path.write_text(json.dumps(value))
    else:
        target=path.with_name("private.json"); path.rename(target); path.symlink_to(target)
    assert client.get("/api/runtime/health").json()["monitoring_allowed"] is False
    assert client.post("/api/interactions/jobs", json={}, headers={"X-AisleSignals-Runtime":token}).status_code == 409
    assert client.get("/api/bootstrap").status_code == 200
    assert client.get("/api/interactions").status_code == 200


def test_generation_fences_submission_results_and_retries_but_allows_cancellation(installation):
    app, client, path = installation
    original=client.get("/api/runtime/health").json()["context"]
    assert submit(client).status_code == 409
    client.headers["X-AisleSignals-Runtime"]=original
    app.state.interactions.provider.release.clear()
    result=submit(client)
    assert result.status_code == 202, result.text
    item=result.json()["id"]
    write_report(path, recovery_generation=1, state="REARM_REQUIRED", rearm_required=True)
    assert client.get(f"/api/interactions/jobs/{item}").status_code == 409
    assert submit(client).status_code == 409
    assert client.post(f"/api/interactions/jobs/{item}/cancel",json={}).status_code == 200
    updated=client.get("/api/runtime/health").json()
    assert updated["context"] != original and updated["monitoring_allowed"]
    client.headers["X-AisleSignals-Runtime"]=updated["context"]
    assert client.get(f"/api/interactions/jobs/{item}").status_code == 409
    with app.state.store.transaction() as conn:
        stored = json.loads(conn.execute("SELECT body FROM entities WHERE id=?", (item,)).fetchone()[0])
    assert stored["status"] == "cancelled"


def test_generation_changed_inside_request_discards_response(installation):
    app, client, path = installation
    token=client.get("/api/runtime/health").json()["context"]
    client.headers["X-AisleSignals-Runtime"]=token
    # Change the private generation during the real route's storage write.
    put = app.state.store.put
    def rotate(conn, user, kind, item):
        if kind == "interaction": write_report(path, recovery_generation=1)
        return put(conn, user, kind, item)
    app.state.store.put = rotate
    response=submit(client)
    assert response.status_code == 409, response.text
    assert response.json()["error"]["code"] == "RUNTIME_CONTEXT_CHANGED"


@pytest.mark.parametrize("installation", [True], indirect=True)
def test_disabled_model_keeps_api_monitoring_available_without_claiming_model_health(installation):
    _, client, path = installation
    report=write_report(path, state="DEGRADED", recovery_generation=1)
    report["services"][1].update(disabled=True, running=False, health="STOPPED", last_checked_at=None)
    path.write_text(json.dumps(report))
    value=client.get("/api/runtime/health").json()
    assert value["monitoring_allowed"] is True
    assert value["product_available"] is False


def test_worker_cannot_publish_after_runtime_generation_changes(installation):
    import time
    app, client, path = installation
    client.headers["X-AisleSignals-Runtime"] = client.get("/api/runtime/health").json()["context"]
    app.state.interactions.provider.release.clear()
    accepted = submit(client)
    assert accepted.status_code == 202
    item_id = accepted.json()["id"]
    assert app.state.interactions.provider.started.wait(2)
    write_report(path, recovery_generation=1)
    app.state.interactions.provider.release.set()
    for _ in range(100):
        with app.state.store.transaction() as conn:
            item = json.loads(conn.execute("SELECT body FROM entities WHERE id=?", (item_id,)).fetchone()[0])
        if item["status"] == "cancelled": break
        time.sleep(.02)
    assert item["status"] == "cancelled" and item["result"] is None and item["frames"] == []
    assert list((app.state.interactions.evidence_root / item_id).glob("*.jpg")) == []


@pytest.mark.parametrize("installation", [True], indirect=True)
def test_casework_only_absent_vision_service_is_explicitly_disabled(installation):
    _, client, path = installation
    report = write_report(path, state="DEGRADED")
    report["services"] = report["services"][:1]
    path.write_text(json.dumps(report))
    value = client.get("/api/runtime/health").json()
    assert value["monitoring_allowed"] and value["product_available"] is False


def test_report_cannot_disable_missing_model_without_process_interlock(installation):
    _, client, path = installation
    report = write_report(path)
    report["services"] = report["services"][:1]
    path.write_text(json.dumps(report))
    assert client.get("/api/runtime/health").json()["monitoring_allowed"] is False


def test_completed_history_remains_reviewable_but_cannot_replay_as_new_runtime_job(installation):
    _, client, path = installation
    client.headers["X-AisleSignals-Runtime"] = client.get("/api/runtime/health").json()["context"]
    body, key = sample(), str(uuid4())
    response = submit(client, body, key)
    item_id = response.json()["id"]
    assert wait_job(client, item_id)["status"] == "completed"
    write_report(path, recovery_generation=1)
    client.headers["X-AisleSignals-Runtime"] = client.get("/api/runtime/health").json()["context"]
    assert submit(client, body, key).status_code == 409
    assert client.get(f"/api/interactions/jobs/{item_id}").status_code == 409
    history = client.get("/api/interactions").json()["items"]
    assert len(history) == 1 and history[0]["id"] == item_id
    assert client.post(f"/api/interactions/{item_id}/review", json={"expected_version": 1, "outcome": "UNCLEAR"}).status_code == 200
