"""Named account, immutable group and branch-session boundaries in pilot mode."""

import json
import sqlite3
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from services.api.app import COOKIE, create_app
from services.api.pilot_identity import (
    PASSWORD_ITERATIONS, add_site, add_user, disable_user, grant_user,
    initialise, main, revoke_site_access, set_password, validate_password,
)
from services.api.store import PASSWORD as DEMO_PASSWORD, Store, digest, ident

BASE = "http://127.0.0.1:8765"
PASSWORD = "Synthetic-only Test Passphrase 73!"
EMAIL = "named.manager@example.test"


@pytest.fixture
def pilot(tmp_path):
    app = create_app(tmp_path / "pilot.db", tmp_path / "web", mode="pilot")
    initial = initialise(app.state.store, "Synthetic Test Group", "Synthetic North Branch", EMAIL, "Named Test Manager", PASSWORD)
    try:
        yield app, initial
    finally:
        app.state.interactions.close()


def client_for(app, email=EMAIL, password=PASSWORD):
    client = TestClient(app, base_url=BASE, headers={"Origin": BASE})
    response = client.post("/api/login", json={"email": email, "password": password})
    assert response.status_code == 200, response.text
    update_headers(client, response.json())
    return client


def update_headers(client, session):
    client.headers.update({"X-CSRF-Token": session["csrf_token"], "X-AisleSignals-Site": session["current_site_id"]})


def manual(client, key=None):
    return client.post("/api/incidents", json={"title": "Synthetic test entry", "notes": "Authorised fixture text only."}, headers={"Idempotency-Key": key or str(uuid4())})


def second_branch(pilot, role="MANAGER"):
    app, initial = pilot
    site = add_site(app.state.store, initial["site"]["organisation_id"], "Synthetic South Branch")
    grant_user(app.state.store, EMAIL, site["id"], role)
    return site


def test_empty_pilot_has_no_public_accounts_or_observations(tmp_path):
    app = create_app(tmp_path / "empty.db", mode="pilot")
    client = TestClient(app, base_url=BASE, headers={"Origin": BASE})
    runtime = client.get("/api/runtime")
    assert runtime.json() == {"mode": "pilot", "setup_required": True, "local_only": True, "authentication": "local_named_password", "mfa_enabled": False}
    assert client.get("/api/health").json()["mode"] == "pilot"
    assert client.get("/api/bootstrap").status_code == 401
    for email in ("manager@harbour.demo", "manager@marimina.demo", "manager@liffey.demo"):
        assert client.post("/api/login", json={"email": email, "password": DEMO_PASSWORD}).status_code == 401
    with app.state.store.transaction() as conn:
        assert conn.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM entities").fetchone()[0] == 0
    app.state.interactions.close()


def test_database_mode_cannot_convert_or_seed_customer_data(tmp_path):
    demo_path, pilot_path = tmp_path / "demo.db", tmp_path / "pilot.db"
    demo = Store(demo_path)
    with demo.transaction() as conn:
        snapshot = [tuple(row) for row in conn.execute("SELECT * FROM users ORDER BY id")]
        # Treat old unmarked files as demo too.
        conn.execute("DROP TABLE runtime_settings")
    with pytest.raises(RuntimeError, match="mode mismatch"):
        Store(demo_path, mode="pilot")
    with sqlite3.connect(demo_path) as conn:
        assert conn.execute("SELECT * FROM users ORDER BY id").fetchall() == snapshot
        assert not conn.execute("SELECT 1 FROM sqlite_master WHERE name='runtime_settings'").fetchone()
    Store(pilot_path, mode="pilot")
    with pytest.raises(RuntimeError, match="mode mismatch"):
        Store(pilot_path, mode="synthetic")
    with sqlite3.connect(pilot_path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 0


def test_initialise_is_atomic_one_time_and_never_logs_password(tmp_path):
    store = Store(tmp_path / "init.db", mode="pilot")
    with pytest.raises(ValueError, match="password"):
        initialise(store, "Synthetic Group", "Synthetic Branch", EMAIL, "Test Manager", "short")
    with store.transaction() as conn:
        assert conn.execute("SELECT COUNT(*) FROM entities").fetchone()[0] == 0
    initial = initialise(store, "Synthetic Group", "Synthetic Branch", EMAIL, "Test Manager", PASSWORD)
    with pytest.raises(ValueError, match="already initialised"):
        initialise(store, "Replacement Group", "Replacement Branch", EMAIL, "Replacement", PASSWORD)
    with store.transaction() as conn:
        user = conn.execute("SELECT * FROM users").fetchone()
        assert user["password_hash"] != PASSWORD
        assert len(user["salt"]) == 64
        assert conn.execute("SELECT password_iterations FROM account_security").fetchone()[0] == PASSWORD_ITERATIONS
        audit = json.dumps(store.listing(conn, initial["user"], "audit"))
        assert PASSWORD not in audit and user["password_hash"] not in audit and user["salt"] not in audit


@pytest.mark.parametrize("password", ["short", DEMO_PASSWORD, "aaaaaaaaaaaaaaaaaaaa", " test passphrase 234", "test passphrase 234 ", "new\npassphrase 2345", "x" * 257])
def test_weak_demo_oversize_and_unroundtrippable_password_rejected(password):
    with pytest.raises(ValueError):
        validate_password(password)


def test_named_login_empty_bootstrap_and_session_cookie(pilot):
    app, initial = pilot
    client = client_for(app)
    session = client.get("/api/session").json()
    bootstrap = client.get("/api/bootstrap").json()
    assert session["mode"] == bootstrap["mode"] == "pilot"
    assert session["current_site_id"] == initial["site"]["id"]
    assert session["allowed_sites"] == bootstrap["allowed_sites"]
    assert session["allowed_sites"][0]["organisation_id"] == initial["site"]["organisation_id"]
    assert bootstrap["site"]["name"] == "Synthetic North Branch"
    assert all(bootstrap[key] == [] for key in ("cameras", "candidates", "incidents", "assistance"))
    assert client.get("/api/runtime").json()["setup_required"] is False
    login = client.post("/api/login", json={"email": EMAIL, "password": PASSWORD})
    cookie = login.headers["set-cookie"].lower()
    assert "httponly" in cookie and "samesite=strict" in cookie
    assert "password" not in json.dumps(login.json()).lower()


def test_all_authenticated_pilot_writes_require_current_branch_and_csrf(pilot):
    app, initial = pilot
    client = client_for(app)
    del client.headers["X-AisleSignals-Site"]
    assert manual(client).json()["error"]["code"] == "SITE_CONTEXT_CHANGED"
    client.headers["X-AisleSignals-Site"] = str(uuid4())
    assert manual(client).status_code == 409
    client.headers["X-AisleSignals-Site"] = initial["site"]["id"]
    del client.headers["X-CSRF-Token"]
    assert manual(client).status_code == 403
    assert client.get("/api/bootstrap").json()["incidents"] == []


def test_branch_switch_rotates_cookie_revokes_old_job_authority_and_checks_role(pilot):
    app, initial = pilot
    site = second_branch(pilot, role="REVIEWER")
    client = client_for(app)
    original = client.cookies.get(COOKIE)
    old_csrf = client.headers["X-CSRF-Token"]
    item = manual(client).json()
    response = client.post("/api/session/site", json={"site_id": site["id"]})
    assert response.status_code == 200
    assert response.json()["user"]["role"] == "REVIEWER"
    assert client.cookies.get(COOKIE) != original
    assert response.json()["csrf_token"] != old_csrf
    with app.state.store.transaction() as conn:
        assert conn.execute("SELECT 1 FROM sessions WHERE token_hash=?", (digest(original),)).fetchone() is None
        assert conn.execute("SELECT 1 FROM session_scopes WHERE token_hash=?", (digest(original),)).fetchone() is None
        current = conn.execute("SELECT * FROM sessions WHERE token_hash=?", (digest(client.cookies.get(COOKIE)),)).fetchone()
        assert Store.resolve_session_user(conn, current)["site_id"] == site["id"]
    # An old tab's csrf and branch cannot write using the new shared cookie.
    assert manual(client).status_code == 403
    update_headers(client, response.json())
    assert client.get("/api/bootstrap").json()["incidents"] == []
    denied = client.patch(f"/api/incidents/{item['id']}", json={"expected_version": 1, "notes": "Other branch update"})
    assert denied.status_code == 404
    assert manual(client).status_code == 200


def test_branch_switch_cannot_expand_membership_or_extend_absolute_session(pilot):
    app, initial = pilot
    unauthorised = add_site(app.state.store, initial["site"]["organisation_id"], "Unassigned Test Branch")
    client = client_for(app)
    original = client.cookies.get(COOKIE)
    for site_id in (unauthorised["id"], str(uuid4())):
        assert client.post("/api/session/site", json={"site_id": site_id}).status_code == 404
    assert client.cookies.get(COOKIE) == original
    site = second_branch(pilot)
    client = client_for(app)
    with app.state.store.transaction() as conn:
        created = conn.execute("SELECT created_at FROM sessions WHERE token_hash=?", (digest(client.cookies.get(COOKIE)),)).fetchone()[0]
    assert client.post("/api/session/site", json={"site_id": site["id"]}).status_code == 200
    with app.state.store.transaction() as conn:
        assert conn.execute("SELECT created_at FROM sessions WHERE token_hash=?", (digest(client.cookies.get(COOKIE)),)).fetchone()[0] == created


def test_same_account_retry_key_cannot_replay_previous_branch_record(pilot):
    app, _ = pilot
    site = second_branch(pilot)
    client = client_for(app)
    key = str(uuid4())
    original = manual(client, key)
    assert original.status_code == 200
    response = client.post("/api/session/site", json={"site_id": site["id"]})
    update_headers(client, response.json())
    retry = manual(client, key)
    assert retry.status_code == 409
    assert original.json()["id"] not in retry.text
    assert client.get("/api/bootstrap").json()["incidents"] == []


def test_role_change_and_removal_revoke_existing_sessions(pilot):
    app, initial = pilot
    email = "named.reviewer@example.test"
    add_user(app.state.store, email, "Named Reviewer", PASSWORD, [initial["site"]["id"]])
    client = client_for(app, email)
    grant_user(app.state.store, email, initial["site"]["id"], "MANAGER")
    assert client.get("/api/session").status_code == 401
    client = client_for(app, email)
    assert client.get("/api/session").json()["user"]["role"] == "MANAGER"
    revoke_site_access(app.state.store, email, initial["site"]["id"])
    assert client.get("/api/session").status_code == 401
    assert client.post("/api/login", json={"email": email, "password": PASSWORD}).status_code == 401


def test_disable_and_password_reset_revoke_sessions_without_reenable(pilot):
    app, initial = pilot
    email = "named.reviewer@example.test"
    add_user(app.state.store, email, "Named Reviewer", PASSWORD, [initial["site"]["id"]])
    client = client_for(app, email)
    new_password = "Replacement test passphrase 94!"
    set_password(app.state.store, email, new_password)
    assert client.get("/api/session").status_code == 401
    assert client.post("/api/login", json={"email": email, "password": PASSWORD}).status_code == 401
    client = client_for(app, email, new_password)
    disable_user(app.state.store, email)
    assert client.get("/api/session").status_code == 401
    set_password(app.state.store, email, PASSWORD)
    assert client.post("/api/login", json={"email": email, "password": PASSWORD}).status_code == 401


def test_last_manager_cannot_be_accidentally_removed(pilot):
    app, initial = pilot
    for operation in (
        lambda: disable_user(app.state.store, EMAIL),
        lambda: revoke_site_access(app.state.store, EMAIL, initial["site"]["id"]),
        lambda: grant_user(app.state.store, EMAIL, initial["site"]["id"], "REVIEWER"),
    ):
        with pytest.raises(ValueError, match="last manager"):
            operation()
    assert client_for(app).get("/api/session").status_code == 200


def test_group_authority_cannot_be_reassigned_through_membership(pilot):
    app, _ = pilot
    foreign = dict(site_id=ident(), organisation_id=ident())
    with app.state.store.transaction() as conn:
        app.state.store.put(conn, foreign, "site", dict(id=foreign["site_id"], name="Foreign fixture branch", organisation_name="Foreign fixture group"))
    with pytest.raises(ValueError, match="another organisation"):
        grant_user(app.state.store, EMAIL, foreign["site_id"])
    with pytest.raises(ValueError, match="same organisation"):
        add_user(app.state.store, "new.person@example.test", "New Person", PASSWORD, [pilot[1]["site"]["id"], foreign["site_id"]])
    client = client_for(app)
    assert client.post("/api/session/site", json={"site_id": foreign["site_id"]}).status_code == 404
    assert foreign["organisation_id"] not in client.get("/api/bootstrap").text


def test_session_resolver_rechecks_disabled_and_removed_membership(pilot):
    app, initial = pilot
    client = client_for(app)
    with app.state.store.transaction() as conn:
        session = conn.execute("SELECT * FROM sessions WHERE token_hash=?", (digest(client.cookies.get(COOKIE)),)).fetchone()
        assert Store.resolve_session_user(conn, session)
        conn.execute("UPDATE account_security SET enabled=0 WHERE user_id=?", (initial["user"]["id"],))
        assert Store.resolve_session_user(conn, session) is None
        conn.execute("UPDATE account_security SET enabled=1 WHERE user_id=?", (initial["user"]["id"],))
        conn.execute("DELETE FROM memberships WHERE user_id=?", (initial["user"]["id"],))
        assert Store.resolve_session_user(conn, session) is None
    assert client.get("/api/bootstrap").status_code == 401


def test_pilot_blocks_simulated_observations(pilot):
    app, _ = pilot
    response = client_for(app).post("/api/simulator", json={"scenario": "SHELF_EVENT", "source_event_id": "synthetic-fixture"}, headers={"Idempotency-Key": str(uuid4())})
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "SIMULATOR_DISABLED"


def test_pilot_login_limit_survives_restart(pilot):
    app, _ = pilot
    client = TestClient(app, base_url=BASE, headers={"Origin": BASE})
    for _ in range(5):
        assert client.post("/api/login", json={"email": EMAIL, "password": "incorrect"}).status_code == 401
    app.state.interactions.close()
    restarted = create_app(app.state.store.path, mode="pilot")
    client = TestClient(restarted, base_url=BASE, headers={"Origin": BASE})
    assert client.post("/api/login", json={"email": EMAIL, "password": PASSWORD}).status_code == 429
    restarted.state.interactions.close()


def test_cli_never_accepts_password_via_argument_or_stdin(tmp_path, capsys, monkeypatch):
    args = ["--db", str(tmp_path / "cli.db"), "init", "--organisation", "Test Group", "--branch", "Test Branch", "--email", EMAIL, "--name", "Test Manager"]
    with pytest.raises(SystemExit):
        main([*args, "--password", PASSWORD])
    rejected = capsys.readouterr()
    assert PASSWORD not in rejected.out + rejected.err
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    assert main(args) == 2
    result = capsys.readouterr()
    assert "interactive terminal" in result.err
    assert PASSWORD not in result.out + result.err
