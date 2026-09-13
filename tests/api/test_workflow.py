import hashlib
import json
import sqlite3
import time
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from services.api.app import COOKIE, create_app
from services.api.store import Store, digest, encode, ident

BASE = "http://127.0.0.1:8765"
PASSWORD = "AisleDemo!2026"


@pytest.fixture
def app(tmp_path):
    return create_app(tmp_path / "test.db", tmp_path / "web")


def sign_in(app, email="manager@harbour.demo"):
    client = TestClient(app, base_url=BASE, headers={"Origin": BASE})
    result = client.post("/api/login", json={"email": email, "password": PASSWORD})
    assert result.status_code == 200, result.text
    client.headers["X-CSRF-Token"] = result.json()["csrf_token"]
    return client


@pytest.fixture
def client(app):
    with sign_in(app) as client:
        yield client


def write(client, url, body, key=None, method="post"):
    return getattr(client, method)(
        url, json=body, headers={"Idempotency-Key": key or str(uuid4())}
    )


def manual(client):
    result = write(
        client,
        "/api/incidents",
        {
            "title": "Synthetic manual event",
            "notes": "A staff member recorded a synthetic event for follow-up.",
        },
    )
    assert result.status_code == 200, result.text
    return result.json()


def patch(client, item, **changes):
    response = write(
        client,
        f"/api/incidents/{item['id']}",
        {"expected_version": item["version"], **changes},
        method="patch",
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_persistent_seed_and_manual_record_survive_restart(app, client):
    initial = client.get("/api/bootstrap").json()
    assert len(initial["candidates"]) == 4
    assert initial["site"]["monthly_price_cents"] == 6000
    assert initial["budget"] == dict(
        monthly_cap_cents=500,
        spent_cents=0,
        engine="LOCAL_TEMPLATE",
        cloud_enabled=False,
    )
    item = manual(client)
    restarted = create_app(app.state.store.path)
    other = sign_in(restarted)
    refreshed = other.get("/api/bootstrap").json()
    assert len(refreshed["candidates"]) == 4
    assert any(record["id"] == item["id"] for record in refreshed["incidents"])


def test_duplicate_creation_and_changed_payload_conflict(client):
    body = {"title": "Manual event", "notes": "Synthetic reviewed context"}
    first = write(client, "/api/incidents", body, "same-action")
    assert write(client, "/api/incidents", body, "same-action").json() == first.json()
    changed = write(
        client,
        "/api/incidents",
        {**body, "notes": "Changed synthetic input"},
        "same-action",
    )
    assert changed.status_code == 409
    assert len(client.get("/api/bootstrap").json()["incidents"]) == 1
    assert client.post("/api/incidents", json=body).status_code == 400


def test_candidate_review_is_atomic_and_terminal(client):
    candidate = client.get("/api/bootstrap").json()["candidates"][0]
    url = f"/api/candidates/{candidate['id']}/review"
    body = {
        "expected_version": 1,
        "decision": "OPEN_INCIDENT",
        "reason": "Synthetic event needs staff follow-up.",
    }
    converted = write(client, url, body, "review-one")
    assert converted.status_code == 200
    assert converted.json()["status"] == "CONVERTED"
    assert write(client, url, body, "review-one").json() == converted.json()
    stale = write(client, url, body)
    assert stale.status_code == 409
    assert stale.json()["error"]["current_version"] == 2
    terminal = write(client, url, {**body, "expected_version": 2})
    assert terminal.status_code == 409
    records = client.get("/api/bootstrap").json()["incidents"]
    assert len(records) == 1
    assert records[0]["classification"] == "SUSPECTED_INCIDENT"
    assert records[0]["candidate_id"] == candidate["id"]


def test_simultaneous_review_creates_one_incident(app, client):
    candidate = client.get("/api/bootstrap").json()["candidates"][0]
    other = sign_in(app, "reviewer@harbour.demo")
    body = {
        "expected_version": 1,
        "decision": "OPEN_INCIDENT",
        "reason": "Synthetic human review for follow-up.",
    }

    def review(c):
        return write(c, f"/api/candidates/{candidate['id']}/review", body)

    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(review, [client, other]))
    assert sorted(r.status_code for r in responses) == [200, 409]
    assert len(client.get("/api/bootstrap").json()["incidents"]) == 1


def test_acknowledge_and_dismiss_do_not_create_incident(client):
    candidate = client.get("/api/bootstrap").json()["candidates"][0]
    url = f"/api/candidates/{candidate['id']}"
    ack = write(client, url + "/acknowledge", {"expected_version": 1}).json()
    assert ack["status"] == "ACKNOWLEDGED"
    dismissed = write(
        client,
        url + "/review",
        {
            "expected_version": 2,
            "decision": "DISMISS",
            "reason": "Synthetic item was returned to shelf.",
        },
    )
    assert dismissed.status_code == 200
    assert dismissed.json()["incident_id"] is None
    assert client.get("/api/bootstrap").json()["incidents"] == []


def test_complete_incident_flow_and_export_digest(client):
    item = manual(client)
    url = f"/api/incidents/{item['id']}"
    assert (
        write(
            client,
            url + "/close",
            {
                "expected_version": item["version"],
                "reason": "Review completed by staff.",
            },
        ).status_code
        == 409
    )
    item = patch(
        client,
        item,
        classification="INSUFFICIENT_EVIDENCE",
        outcome="NO_LOSS_ESTABLISHED",
    )
    task = {
        "expected_version": item["version"],
        "title": "Review the synthetic event",
        "assignee": "Demo reviewer",
        "due_at": "2026-09-14T12:00:00Z",
    }
    added = write(client, url + "/tasks", task, "task-one")
    item = added.json()
    assert write(client, url + "/tasks", task, "task-one").json() == item
    assert (
        write(
            client,
            url + "/close",
            {
                "expected_version": item["version"],
                "reason": "Review completed by staff.",
            },
        ).status_code
        == 409
    )
    item = write(
        client,
        url + f"/tasks/{item['tasks'][0]['id']}/complete",
        {"expected_version": item["version"]},
    ).json()
    item = write(client, url + "/draft", {"expected_version": item["version"]}).json()
    assert item["draft"]["engine"] == "LOCAL_TEMPLATE"
    assert "A staff member recorded" not in item["draft"]["text"]
    item = write(
        client, url + "/draft/approve", {"expected_version": item["version"]}
    ).json()
    assert item["draft"]["approved"] is True
    item = patch(client, item, notes="Updated reviewed synthetic facts.")
    assert item["draft"] is None
    item = write(
        client,
        url + "/close",
        {
            "expected_version": item["version"],
            "reason": "Synthetic review finished, no loss established.",
        },
    ).json()
    assert item["status"] == "CLOSED"
    assert (
        write(
            client,
            url,
            {"expected_version": item["version"], "title": "Changed title"},
            method="patch",
        ).status_code
        == 409
    )
    item = write(client, url + "/draft", {"expected_version": item["version"]}).json()
    assert "Status: CLOSED" in item["draft"]["text"]
    item = write(
        client, url + "/draft/approve", {"expected_version": item["version"]}
    ).json()
    assert item["draft"]["approved"] is True
    exported = write(
        client,
        url + "/export",
        {"expected_version": item["version"], "purpose": "Synthetic manager review"},
    )
    assert exported.status_code == 200
    result = exported.json()
    assert (
        result["sha256"]
        == hashlib.sha256(encode(result["record"]).encode()).hexdigest()
    )
    assert "attachment" in exported.headers["content-disposition"]
    assert result["record"]["incident"]["status"] == "CLOSED"
    assert exported.headers["cache-control"] == "no-store"
    item = write(
        client,
        url + "/reopen",
        {
            "expected_version": item["version"],
            "reason": "Additional synthetic information arrived.",
        },
    ).json()
    assert item["status"] == "OPEN"
    assert any(
        entry["action"] == "INCIDENT_EXPORTED"
        for entry in client.get("/api/bootstrap").json()["audit"]
    )


def test_money_invariants_and_role_restrictions(app, client):
    reviewer = sign_in(app, "reviewer@harbour.demo")
    item = manual(client)
    url = f"/api/incidents/{item['id']}"
    for changes in (
        {"loss_cents": 10},
        {"classification": "STORE_CONFIRMED_LOSS"},
        {"recovered_cents": 0},
    ):
        assert (
            write(
                reviewer,
                url,
                {"expected_version": item["version"], **changes},
                method="patch",
            ).status_code
            == 403
        )
    for changes in (
        {"loss_cents": -1},
        {"loss_cents": True},
        {"loss_cents": "10"},
        {"loss_cents": 100},
        {"recovered_cents": 0},
        {"outcome": "LOSS_RECORDED"},
    ):
        assert (
            write(
                client,
                url,
                {"expected_version": item["version"], **changes},
                method="patch",
            ).status_code
            == 422
        )
    item = patch(
        client,
        item,
        classification="STORE_CONFIRMED_LOSS",
        outcome="LOSS_RECORDED",
        loss_cents=1200,
        recovered_cents=400,
    )
    for changes in (
        {"recovered_cents": 1201},
        {"classification": "BENIGN"},
        {"outcome": "NO_LOSS_ESTABLISHED"},
    ):
        assert (
            write(
                client,
                url,
                {"expected_version": item["version"], **changes},
                method="patch",
            ).status_code
            == 422
        )
    assert (
        write(
            reviewer,
            url + "/export",
            {"expected_version": item["version"], "purpose": "Synthetic review record"},
        ).status_code
        == 403
    )
    item = write(
        client,
        url + "/close",
        {
            "expected_version": item["version"],
            "reason": "Manager completed synthetic review.",
        },
    ).json()
    assert (
        write(
            reviewer,
            url + "/reopen",
            {"expected_version": item["version"], "reason": "Another synthetic review"},
        ).status_code
        == 403
    )


def test_org_and_site_isolation_including_media_export_and_task_routes(app, client):
    outsider = sign_in(app, "manager@liffey.demo")
    harbour = client.get("/api/bootstrap").json()
    other = outsider.get("/api/bootstrap").json()
    assert harbour["site"]["id"] != other["site"]["id"]
    candidate = harbour["candidates"][0]
    item = manual(client)
    url = f"/api/incidents/{item['id']}"
    assert outsider.get(candidate["evidence_url"]).status_code == 404
    assert (
        write(
            outsider,
            f"/api/candidates/{candidate['id']}/review",
            {
                "expected_version": 1,
                "decision": "OPEN_INCIDENT",
                "reason": "Synthetic attempted other site",
            },
        ).status_code
        == 404
    )
    assert (
        write(
            outsider,
            url,
            {"expected_version": 1, "title": "Incorrect tenant"},
            method="patch",
        ).status_code
        == 404
    )
    assert (
        write(
            outsider,
            url + "/export",
            {"expected_version": 1, "purpose": "Synthetic attempted other site"},
        ).status_code
        == 404
    )
    assert (
        write(
            outsider, url + f"/tasks/{uuid4()}/complete", {"expected_version": 1}
        ).status_code
        == 404
    )
    # An unauthorised second site inside the SAME organisation must also be invisible.
    with app.state.store.transaction() as conn:
        user = dict(
            conn.execute(
                "SELECT * FROM users WHERE email='manager@harbour.demo'"
            ).fetchone()
        )
        fake = {**user, "site_id": ident()}
        secret = {
            **item,
            "id": ident(),
            "title": "Same organisation, unauthorised site",
        }
        Store.put(conn, fake, "incident", secret)
    assert all(
        i["id"] != secret["id"]
        for i in client.get("/api/bootstrap").json()["incidents"]
    )
    assert (
        write(
            client,
            f"/api/incidents/{secret['id']}/export",
            {"expected_version": 1, "purpose": "Synthetic site scope check"},
        ).status_code
        == 404
    )
    forged = write(
        client,
        "/api/incidents",
        {
            "title": "Tenant forgery",
            "notes": "Synthetic invalid request",
            "site_id": other["site"]["id"],
        },
    )
    assert forged.status_code == 422


def test_simulator_shift_health_historical_and_duplicate_source(client):
    make = lambda scenario, event: write(
        client, "/api/simulator", {"scenario": scenario, "source_event_id": event}
    )
    assert make("SHELF_EVENT", "event-one").status_code == 409
    assert write(client, "/api/shift", {"active": True}).status_code == 200
    first = make("SHELF_EVENT", "event-one")
    assert first.status_code == 200
    assert make("SHELF_EVENT", "event-one").json() == first.json()
    assert make("RETURNED_ITEM", "event-one").status_code == 409
    assert make("CAMERA_OFFLINE", "offline-one").status_code == 200
    assert make("SHELF_EVENT", "event-two").status_code == 409
    historical = make("HISTORICAL_EVENT", "historical-one").json()
    assert historical["historical"] is True
    assert make("CAMERA_FROZEN", "frozen-one").status_code == 200
    assert client.get("/api/bootstrap").json()["cameras"][0]["status"] == "FROZEN"
    assert make("CAMERA_RECOVERED", "recovered-one").status_code == 200
    assert make("SHELF_EVENT", "event-two").status_code == 200
    assert write(client, "/api/shift", {"active": False}).status_code == 200
    assert make("CAMERA_OFFLINE", "offline-two").status_code == 200


def test_assistance_transitions_are_scoped_and_do_not_imply_arrival(app, client):
    result = write(
        client,
        "/api/assistance",
        {"reason": "A synthetic colleague assistance request"},
        "assist-one",
    )
    assert result.status_code == 200
    request = result.json()
    assert (
        write(
            client,
            "/api/assistance",
            {"reason": "A synthetic colleague assistance request"},
            "assist-one",
        ).json()
        == request
    )
    url = f"/api/assistance/{request['id']}/transition"
    outsider = sign_in(app, "manager@liffey.demo")
    assert write(outsider, url, {"status": "ACKNOWLEDGED"}).status_code == 404
    assert write(client, url, {"status": "ACKNOWLEDGED"}).status_code == 200
    assert write(client, url, {"status": "ACKNOWLEDGED"}).status_code == 409
    assert write(client, url, {"status": "RESOLVED"}).status_code == 200
    assert write(client, url, {"status": "ACKNOWLEDGED"}).status_code == 409
    assert any(
        "does not mean a colleague has arrived" in a["detail"]
        for a in client.get("/api/bootstrap").json()["audit"]
    )


def test_session_security_boundaries_and_logout(app, client):
    cookie = client.cookies.get(COOKIE)
    anonymous = TestClient(app, base_url=BASE)
    assert anonymous.get("/api/bootstrap").status_code == 401
    no_csrf = client.post(
        "/api/shift", json={"active": True}, headers={"X-CSRF-Token": "wrong"}
    )
    assert no_csrf.status_code == 403
    foreign = client.post(
        "/api/shift",
        json={"active": True},
        headers={"Origin": "https://attacker.invalid"},
    )
    assert foreign.status_code == 403
    assert foreign.headers["cache-control"] == "no-store"
    assert (
        client.get("/api/health", headers={"Host": "attacker.invalid"}).status_code
        == 400
    )
    assert (
        client.get("/api/health", headers={"X-Forwarded-Host": "localhost"}).status_code
        == 400
    )
    assert (
        client.get("/api/health", headers={"Forwarded": "host=localhost"}).status_code
        == 400
    )
    assert (
        client.get("/api/health", headers={"Host": "localhost:9999"}).status_code == 400
    )
    assert (
        client.get("/api/health", headers={"Sec-Fetch-Site": "cross-site"}).status_code
        == 403
    )
    assert write(client, "/api/logout", {}).status_code == 200
    anonymous.cookies.set(COOKIE, cookie)
    assert anonymous.get("/api/session").status_code == 401
    with app.state.store.transaction() as conn:
        audit_bodies = " ".join(
            row[0]
            for row in conn.execute("SELECT body FROM entities WHERE kind='audit'")
        )
    assert PASSWORD not in audit_bodies and cookie not in audit_bodies


@pytest.mark.parametrize(
    "field,age", [("last_seen", 901), ("created_at", 8 * 3600 + 1)]
)
def test_expired_session_denies_api_and_evidence(app, client, field, age):
    candidate = next(
        c
        for c in client.get("/api/bootstrap").json()["candidates"]
        if c["evidence_url"]
    )
    with app.state.store.transaction() as conn:
        conn.execute(f"UPDATE sessions SET {field}=?", (time.time() - age,))
    result = client.get(candidate["evidence_url"])
    assert result.status_code == 401
    assert result.headers["cache-control"] == "no-store"
    assert client.get("/api/session").status_code == 401


def test_evidence_is_authenticated_synthetic_and_missing_is_explicit(app, client):
    candidates = client.get("/api/bootstrap").json()["candidates"]
    available = next(c for c in candidates if c["evidence_url"])
    missing = next(c for c in candidates if c["media_status"] == "MISSING")
    result = client.get(available["evidence_url"])
    assert result.status_code == 200
    assert "image/svg+xml" in result.headers["content-type"]
    assert "NO REAL CAMERA FOOTAGE" in result.text
    assert result.headers["cache-control"] == "no-store"
    assert client.get(f"/api/evidence/{missing['id']}").status_code == 404
    assert client.get(available["evidence_url"] + "?token=forbidden").status_code == 400
    anonymous = TestClient(app, base_url=BASE)
    assert anonymous.get(available["evidence_url"]).status_code == 401


def test_login_throttle_and_cookie_flags(app):
    c = TestClient(app, base_url=BASE, headers={"Origin": BASE})
    for _ in range(5):
        assert (
            c.post(
                "/api/login",
                json={"email": "manager@harbour.demo", "password": "wrong"},
            ).status_code
            == 401
        )
    assert (
        c.post(
            "/api/login", json={"email": "manager@harbour.demo", "password": PASSWORD}
        ).status_code
        == 429
    )
    result = c.post(
        "/api/login", json={"email": "manager@liffey.demo", "password": PASSWORD}
    )
    assert result.status_code == 200
    cookie = result.headers["set-cookie"]
    assert (
        "HttpOnly" in cookie
        and "SameSite=strict" in cookie
        and "Max-Age=28800" in cookie
    )
    assert "Domain=" not in cookie


def test_input_rejections_do_not_echo_secrets_and_invalid_writes_do_not_mutate(client):
    before = client.get("/api/bootstrap").json()
    result = write(
        client,
        "/api/incidents",
        {"title": "ok", "notes": "private raw input must not be echoed"},
    )
    assert result.status_code == 422
    assert "private raw" not in result.text
    assert (
        client.post(
            "/api/shift",
            content='{"active":true}',
            headers={"Content-Type": "text/plain"},
        ).status_code
        == 415
    )
    assert client.post("/api/shift", json={"active": "true"}).status_code == 422
    assert (
        client.post(
            "/api/incidents",
            content="x" * 66000,
            headers={"Content-Type": "application/json"},
        ).status_code
        == 413
    )
    after = client.get("/api/bootstrap").json()
    assert before["incidents"] == after["incidents"]


def test_configuration_static_boundary_and_schema_guard(tmp_path, monkeypatch):
    db = tmp_path / "configured.db"
    web = tmp_path / "dist"
    web.mkdir()
    (web / "index.html").write_text("<html>synthetic app</html>")
    (web / "app.js").write_text("export const demo=true;")
    (tmp_path / "secret.txt").write_text("private")
    monkeypatch.setenv("AISLESIGNALS_DB_PATH", str(db))
    monkeypatch.setenv("AISLESIGNALS_WEB_DIST", str(web))
    application = create_app()
    c = TestClient(application, base_url=BASE)
    assert c.get("/").status_code == 200
    assert c.get("/incidents/example").status_code == 200
    assert c.get("/app.js").status_code == 200
    assert c.get("/api/unknown").status_code == 404
    assert c.get("/assets/missing.js").status_code == 404
    assert c.get("/%2e%2e/secret.txt").status_code == 404
    assert c.get("/.env").status_code == 404
    assert application.state.store.path == str(db)
    with sqlite3.connect(db) as conn:
        conn.execute("PRAGMA user_version=99")
    with pytest.raises(RuntimeError, match="schema"):
        create_app()
    monkeypatch.setenv("AISLESIGNALS_MODE", "live")
    with pytest.raises(ValueError, match="synthetic"):
        create_app(tmp_path / "never-created.db")
    assert not (tmp_path / "never-created.db").exists()


def test_bootstrap_bounds_collection_size(app, client):
    with app.state.store.transaction() as conn:
        user = dict(
            conn.execute(
                "SELECT * FROM users WHERE email='manager@harbour.demo'"
            ).fetchone()
        )
        for index in range(205):
            Store.audit(
                conn,
                user,
                "SYNTHETIC_TEST",
                "site",
                user["site_id"],
                f"Synthetic bounded-list entry {index}",
            )
    assert len(client.get("/api/bootstrap").json()["audit"]) == 200


def test_passive_bootstrap_and_evidence_do_not_extend_idle_session(app, client):
    candidate = next(
        c
        for c in client.get("/api/bootstrap").json()["candidates"]
        if c["evidence_url"]
    )
    idle_started = time.time() - 300
    with app.state.store.transaction() as conn:
        conn.execute("UPDATE sessions SET last_seen=?", (idle_started,))
    assert client.get("/api/bootstrap").status_code == 200
    assert client.get(candidate["evidence_url"]).status_code == 200
    with app.state.store.transaction() as conn:
        assert (
            conn.execute("SELECT last_seen FROM sessions").fetchone()[0] == idle_started
        )
    assert client.get("/api/session").status_code == 200
    with app.state.store.transaction() as conn:
        assert (
            conn.execute("SELECT last_seen FROM sessions").fetchone()[0] > idle_started
        )
        conn.execute("UPDATE sessions SET last_seen=?", (time.time() - 901,))
    assert client.get("/api/bootstrap").status_code == 401


def test_configured_runtime_port_is_explicit_and_validated(tmp_path, monkeypatch):
    monkeypatch.setenv("AISLESIGNALS_PORT", "8799")
    application = create_app(tmp_path / "port.db")
    c = TestClient(
        application,
        base_url="http://127.0.0.1:8799",
        headers={"Origin": "http://127.0.0.1:8799"},
    )
    assert c.get("/api/health").status_code == 200
    assert (
        c.post(
            "/api/login", json={"email": "manager@harbour.demo", "password": PASSWORD}
        ).status_code
        == 200
    )
    assert c.get("/api/health", headers={"Host": "127.0.0.1:8765"}).status_code == 400
    assert (
        c.get("/api/health", headers={"Origin": "http://127.0.0.1:8765"}).status_code
        == 403
    )
    monkeypatch.setenv("AISLESIGNALS_PORT", "70000")
    with pytest.raises(ValueError, match="port number"):
        create_app(tmp_path / "invalid-port.db")
    assert not (tmp_path / "invalid-port.db").exists()


def test_expired_synthetic_evidence_disappears_from_bootstrap(app, client):
    from datetime import datetime, timezone, timedelta

    item = next(
        c
        for c in client.get("/api/bootstrap").json()["candidates"]
        if c["evidence_url"]
    )
    old_url = item["evidence_url"]
    with app.state.store.transaction() as conn:
        user = dict(
            conn.execute(
                "SELECT * FROM users WHERE email='manager@harbour.demo'"
            ).fetchone()
        )
        item["received_at"] = (
            datetime.now(timezone.utc) - timedelta(hours=73)
        ).isoformat()
        Store.put(conn, user, "candidate", item)
    view = next(
        c
        for c in client.get("/api/bootstrap").json()["candidates"]
        if c["id"] == item["id"]
    )
    assert view["media_status"] == "MISSING" and view["evidence_url"] is None
    assert client.get(old_url).status_code == 404


def test_replacement_login_revokes_previous_browser_cookie(app, client):
    old_cookie = client.cookies.get(COOKIE)
    result = client.post(
        "/api/login", json={"email": "manager@liffey.demo", "password": PASSWORD}
    )
    assert result.status_code == 200
    assert client.cookies.get(COOKIE) != old_cookie
    stale = TestClient(app, base_url=BASE)
    stale.cookies.set(COOKIE, old_cookie)
    assert stale.get("/api/session").status_code == 401
    assert stale.get("/api/bootstrap").status_code == 401
    assert client.get("/api/bootstrap").json()["site"]["name"] == "Liffey Pharmacy"
    with app.state.store.transaction() as conn:
        assert (
            conn.execute(
                "SELECT COUNT(*) FROM sessions WHERE token_hash=?",
                (digest(old_cookie),),
            ).fetchone()[0]
            == 0
        )


def test_chunked_body_rejected_before_remaining_chunks_are_consumed(app):
    import asyncio

    chunks = [b"x" * 40_000, b"y" * 40_000, b"never read"]
    consumed = []
    output = []
    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": "/api/login",
        "raw_path": b"/api/login",
        "query_string": b"",
        "root_path": "",
        "headers": [
            (b"host", b"127.0.0.1:8765"),
            (b"origin", BASE.encode()),
            (b"content-type", b"application/json"),
            (b"transfer-encoding", b"chunked"),
        ],
        "client": ("127.0.0.1", 1111),
        "server": ("127.0.0.1", 8765),
    }

    async def receive():
        chunk = chunks[len(consumed)]
        consumed.append(chunk)
        return {
            "type": "http.request",
            "body": chunk,
            "more_body": len(consumed) < len(chunks),
        }

    async def send(message):
        output.append(message)

    asyncio.run(app(scope, receive, send))
    assert len(consumed) == 2
    start = next(
        message for message in output if message["type"] == "http.response.start"
    )
    assert start["status"] == 413
    assert (b"cache-control", b"no-store") in start["headers"]


def test_small_chunked_json_still_uses_normal_request_validation(app):
    c = TestClient(app, base_url=BASE, headers={"Origin": BASE})
    pieces = iter(
        [b'{"email":"manager@harbour.demo",', b'"password":"AisleDemo!2026"}']
    )
    result = c.post(
        "/api/login", content=pieces, headers={"Content-Type": "application/json"}
    )
    assert result.status_code == 200
    assert result.json()["user"]["role"] == "MANAGER"
