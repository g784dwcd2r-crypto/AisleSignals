"""Real local API administration against isolated synthetic account fixtures."""

import json
import os
import stat
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from services.api.app import create_app
from services.api.pilot_admin import SETUP_KEY, issue_setup_token, setup_token_path
from services.api.pilot_identity import (
    _new_site, _new_user, add_site, add_user, grant_user, initialise, main,
)
from services.api.store import Store
from test_pilot_identity import BASE, EMAIL, PASSWORD, client_for, update_headers

NEW_PASSWORD = "Synthetic new account passphrase 583!"


@pytest.fixture
def empty(tmp_path):
    app = create_app(tmp_path / "admin.db", tmp_path / "web", mode="pilot")
    yield app
    app.state.interactions.close()


@pytest.fixture
def admin(empty):
    initial = initialise(empty.state.store, "Synthetic Admin Group", "Synthetic North", EMAIL, "Synthetic Manager", PASSWORD)
    return empty, initial, client_for(empty)


def anonymous(app):
    return TestClient(app, base_url=BASE, headers={"Origin": BASE})


def setup_payload(token, **changes):
    return {"organisation_name": "Synthetic Setup Group", "branch_name": "Synthetic Setup Branch",
            "name": "Synthetic Setup Manager", "email": EMAIL, "password": PASSWORD,
            "setup_token": token, **changes}


def create_account(client, site, **changes):
    return client.post("/api/admin/users", json={"name": "Synthetic Reviewer", "email": "reviewer.admin@example.test",
        "password": NEW_PASSWORD, "branches": [{"site_id": site, "role": "REVIEWER"}],
        "manager_password": PASSWORD, **changes})


def action(client, user, operation, **changes):
    roster = client.get("/api/admin/users").json().get("users", [])
    version = next((item["version"] for item in roster if item["id"] == user), "0" * 64)
    return client.post(f"/api/admin/users/{user}/{operation}", json={"manager_password": PASSWORD, "expected_version": version, **changes})


def test_private_setup_token_atomic_first_owner_then_named_login(empty):
    client = anonymous(empty)
    assert client.get("/api/setup/status").json() == {"available": True, "token_required": True, "token_ttl_seconds": 900}
    code = issue_setup_token(empty.state.store)
    path = setup_token_path(empty.state.store)
    assert json.loads(path.read_text())["token"] == code["token"]
    if os.name != "nt":
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
    with empty.state.store.transaction() as conn:
        state = conn.execute("SELECT value FROM runtime_settings WHERE key=?", (SETUP_KEY,)).fetchone()[0]
        assert code["token"] not in state
    for route in ("/api/runtime", "/api/setup/status"):
        assert code["token"] not in client.get(route).text
    response = client.post("/api/setup", json=setup_payload(code["token"]))
    assert response.status_code == 201 and response.json() == {"created": True, "sign_in_required": True}
    assert "set-cookie" not in response.headers
    assert not path.exists()
    assert client.get("/api/setup/status").json()["available"] is False
    assert client.post("/api/setup", json=setup_payload(code["token"])).status_code == 409
    with pytest.raises(ValueError, match="unavailable"):
        issue_setup_token(empty.state.store)
    logged = client_for(empty)
    assert logged.get("/api/admin/users").json()["users"][0]["email"] == EMAIL


def test_first_owner_claim_race_creates_exactly_one_manager(empty):
    token = issue_setup_token(empty.state.store)["token"]
    def claim(index):
        return anonymous(empty).post("/api/setup", json=setup_payload(token, email=f"race{index}@example.test")).status_code
    with ThreadPoolExecutor(max_workers=2) as workers:
        statuses = list(workers.map(claim, range(2)))
    assert sorted(statuses) == [201, 409]
    with empty.state.store.transaction() as conn:
        assert conn.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM entities WHERE kind='site'").fetchone()[0] == 1


def test_setup_rejects_absent_rotated_expired_and_reused_codes(empty):
    client = anonymous(empty)
    assert client.post("/api/setup", json=setup_payload("wrong")).status_code == 400
    old = issue_setup_token(empty.state.store)["token"]
    fresh = issue_setup_token(empty.state.store)["token"]
    assert client.post("/api/setup", json=setup_payload(old)).status_code == 400
    with empty.state.store.transaction() as conn:
        state = json.loads(conn.execute("SELECT value FROM runtime_settings WHERE key=?", (SETUP_KEY,)).fetchone()[0])
        state["expires_at"] = 0
        conn.execute("UPDATE runtime_settings SET value=? WHERE key=?", (json.dumps(state), SETUP_KEY))
    assert client.post("/api/setup", json=setup_payload(fresh)).status_code == 400
    assert client.get("/api/setup/status").json()["available"] is True


def test_setup_rate_limit_survives_new_token_and_no_invalid_claim_changes_data(empty):
    client = anonymous(empty)
    for _ in range(5):
        assert client.post("/api/setup", json=setup_payload("wrong")).status_code == 400
    code = issue_setup_token(empty.state.store)["token"]
    assert client.post("/api/setup", json=setup_payload(code)).status_code == 429
    with empty.state.store.transaction() as conn:
        assert conn.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 0


def test_setup_requires_local_origin_and_valid_password_without_consuming_code(empty):
    client = anonymous(empty)
    code = issue_setup_token(empty.state.store)["token"]
    rejected = client.post("/api/setup", json=setup_payload(code), headers={"Origin": "https://unrelated.example"})
    assert rejected.status_code == 403
    weak = client.post("/api/setup", json=setup_payload(code, password="aaaaaaaaaaaaaaaa"))
    assert weak.status_code == 422 and code not in weak.text
    assert client.post("/api/setup", json=setup_payload(code)).status_code == 201


def test_existing_disabled_workspace_cannot_be_reclaimed(admin):
    app, _, _ = admin
    with app.state.store.transaction() as conn:
        conn.execute("UPDATE account_security SET enabled=0")
    client = anonymous(app)
    assert client.get("/api/runtime").json()["setup_required"] is True
    assert client.get("/api/setup/status").json()["available"] is False
    assert client.post("/api/setup", json=setup_payload("anything")).status_code == 409


def test_setup_token_cli_refuses_noninteractive_output(empty, monkeypatch, capsys):
    import sys
    monkeypatch.setattr(sys.stdin, "isatty", lambda: False)
    assert main(["--db", empty.state.store.path, "setup-token"]) == 2
    assert "interactive" in capsys.readouterr().err
    assert not setup_token_path(empty.state.store).exists()


def test_public_or_demo_or_reviewer_cannot_administer(admin, tmp_path):
    app, initial, _ = admin
    assert anonymous(app).get("/api/admin/users").status_code == 401
    add_user(app.state.store, "reviewer.scope@example.test", "Synthetic Reviewer", NEW_PASSWORD, [initial["site"]["id"]])
    reviewer = client_for(app, "reviewer.scope@example.test", NEW_PASSWORD)
    assert reviewer.get("/api/admin/users").status_code == 403
    assert reviewer.post("/api/admin/sites", json={"name": "Self escalation", "manager_password": NEW_PASSWORD}).status_code == 403
    demo = create_app(tmp_path / "demo.db", mode="synthetic")
    try:
        visitor = anonymous(demo)
        assert visitor.get("/api/setup/status").status_code == 403
        login = visitor.post("/api/login", json={"email": "manager@harbour.demo", "password": "AisleDemo!2026"})
        update_headers(visitor, login.json())
        assert visitor.get("/api/admin/users").status_code == 403
        assert visitor.post("/api/admin/sites", json={"name": "Denied", "manager_password": "AisleDemo!2026"}).status_code == 403
    finally:
        demo.state.interactions.close()


def test_create_branch_user_and_mixed_roles_without_credential_disclosure(admin):
    app, initial, client = admin
    created = client.post("/api/admin/sites", json={"name": "Synthetic South", "manager_password": PASSWORD})
    assert created.status_code == 201
    site = created.json()["site"]
    assert site["role"] == "MANAGER" and site["organisation_id"] == initial["site"]["organisation_id"]
    assert {s["id"] for s in client.get("/api/admin/sites").json()["sites"]} == {initial["site"]["id"], site["id"]}
    assert client.post("/api/admin/sites", json={"name": "synthetic SOUTH", "manager_password": PASSWORD}).status_code == 409
    created = create_account(client, initial["site"]["id"], branches=[{"site_id": initial["site"]["id"], "role": "REVIEWER"}, {"site_id": site["id"], "role": "MANAGER"}])
    assert created.status_code == 201
    user = created.json()["user"]
    assert {b["role"] for b in user["branches"]} == {"MANAGER", "REVIEWER"}
    assert user["enabled"] and user["can_manage_account"]
    assert create_account(client, initial["site"]["id"]).status_code == 409
    account = client_for(app, "reviewer.admin@example.test", NEW_PASSWORD)
    assert account.get("/api/session").json()["user"]["role"] == "REVIEWER"
    serialized = json.dumps(client.get("/api/admin/users").json())
    with app.state.store.transaction() as conn:
        audits = " ".join(row[0] for row in conn.execute("SELECT body FROM entities WHERE kind='audit'"))
    for secret in (PASSWORD, NEW_PASSWORD, "password_hash", '"salt"'):
        assert secret not in serialized and secret not in audits


def test_sensitive_admin_changes_require_csrf_branch_and_current_passphrase(admin):
    app, initial, client = admin
    assert create_account(client, initial["site"]["id"], manager_password="incorrect").json()["error"]["code"] == "REAUTH_REQUIRED"
    assert client.get("/api/session").status_code == 200
    old_csrf = client.headers.pop("X-CSRF-Token")
    assert create_account(client, initial["site"]["id"]).status_code == 403
    client.headers["X-CSRF-Token"] = old_csrf
    client.headers["X-AisleSignals-Site"] = str(uuid4())
    assert create_account(client, initial["site"]["id"]).status_code == 409
    with app.state.store.transaction() as conn:
        assert conn.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 1


def test_manager_reauth_attempt_limit_does_not_revoke_valid_read_session(admin):
    _, initial, client = admin
    for _ in range(5):
        assert create_account(client, initial["site"]["id"], manager_password="incorrect").status_code == 401
    assert create_account(client, initial["site"]["id"]).status_code == 429
    assert client.get("/api/session").status_code == 200


def test_manager_cannot_grant_unmanaged_branch_or_self_escalate(admin):
    app, initial, client = admin
    hidden = add_site(app.state.store, initial["site"]["organisation_id"], "Synthetic Restricted")
    grant_user(app.state.store, EMAIL, hidden["id"], "REVIEWER")
    client = client_for(app)
    assert hidden["id"] not in {s["id"] for s in client.get("/api/admin/sites").json()["sites"]}
    assert create_account(client, hidden["id"]).status_code == 404
    result = action(client, initial["user"]["id"], "access", site_id=hidden["id"], role="MANAGER")
    assert result.status_code == 404
    switched = client.post("/api/session/site", json={"site_id": hidden["id"]})
    update_headers(client, switched.json())
    assert client.get("/api/admin/sites").status_code == 403


def test_partial_branch_manager_cannot_disable_or_reset_shared_account(admin):
    app, initial, client = admin
    other = add_site(app.state.store, initial["site"]["organisation_id"], "Synthetic Hidden Branch")
    user = add_user(app.state.store, "shared@example.test", "Synthetic Shared", NEW_PASSWORD, [initial["site"]["id"], other["id"]])
    visible = next(u for u in client.get("/api/admin/users").json()["users"] if u["id"] == user["id"])
    assert visible["can_manage_account"] is False
    assert [b["site_id"] for b in visible["branches"]] == [initial["site"]["id"]]
    assert other["id"] not in json.dumps(visible)
    assert action(client, user["id"], "enabled", enabled=False).status_code == 403
    assert action(client, user["id"], "password", password=PASSWORD).status_code == 403
    assert action(client, user["id"], "access", site_id=initial["site"]["id"], role="MANAGER").status_code == 200


def test_cross_organisation_and_unassigned_account_identifiers_are_not_authority(admin):
    app, initial, client = admin
    with app.state.store.transaction() as conn:
        site = _new_site(conn, app.state.store, str(uuid4()), "Synthetic Other Group", "Synthetic Other Branch")
        other = _new_user(conn, app.state.store, "other.group@example.test", "Synthetic Other", NEW_PASSWORD, [site["id"]], "MANAGER")
    assert other["email"] not in client.get("/api/admin/users").text
    assert create_account(client, site["id"]).status_code == 404
    assert action(client, other["id"], "enabled", enabled=False).status_code == 404
    assert action(client, other["id"], "password", password=PASSWORD).status_code == 404
    assert action(client, other["id"], "access", site_id=initial["site"]["id"], role="MANAGER").status_code == 404


def test_disable_enable_and_reset_revoke_sessions_but_do_not_reset_enabled_state(admin):
    app, initial, client = admin
    user = create_account(client, initial["site"]["id"]).json()["user"]
    account = client_for(app, "reviewer.admin@example.test", NEW_PASSWORD)
    assert action(client, user["id"], "enabled", enabled=False).status_code == 200
    assert account.get("/api/session").status_code == 401
    changed = action(client, user["id"], "password", password="Synthetic replacement passphrase 843!")
    assert changed.status_code == 200 and changed.json()["sign_in_required"] is False
    denied = anonymous(app).post("/api/login", json={"email": user["email"], "password": "Synthetic replacement passphrase 843!"})
    assert denied.status_code == 401
    assert action(client, user["id"], "enabled", enabled=True).status_code == 200
    fresh = client_for(app, user["email"], "Synthetic replacement passphrase 843!")
    assert fresh.get("/api/session").status_code == 200


def test_access_revokes_sessions_and_last_manager_and_last_grant_stay_protected(admin):
    app, initial, client = admin
    current_id, site = initial["user"]["id"], initial["site"]["id"]
    assert action(client, current_id, "enabled", enabled=False).json()["error"]["code"] == "LAST_MANAGER_REQUIRED"
    assert action(client, current_id, "access", site_id=site, role="REVIEWER").json()["error"]["code"] == "LAST_MANAGER_REQUIRED"
    reviewer = create_account(client, site).json()["user"]
    assert action(client, reviewer["id"], "access", site_id=site, role=None).json()["error"]["code"] == "BRANCH_REQUIRED"
    account = client_for(app, reviewer["email"], NEW_PASSWORD)
    assert action(client, reviewer["id"], "access", site_id=site, role="MANAGER").status_code == 200
    assert account.get("/api/session").status_code == 401
    assert action(client, current_id, "access", site_id=site, role="REVIEWER").status_code == 200
    assert client.get("/api/session").status_code == 401


def test_duplicate_branch_grants_unknown_fields_and_secret_errors_are_bounded(admin):
    _, initial, client = admin
    site = initial["site"]["id"]
    duplicate = create_account(client, site, branches=[{"site_id": site, "role": "REVIEWER"}] * 2)
    assert duplicate.status_code == 422
    invalid = create_account(client, site, password="aaaaaaaaaaaaaaaa", organisation_id=str(uuid4()))
    assert invalid.status_code == 422
    for response in (duplicate, invalid):
        assert PASSWORD not in response.text and NEW_PASSWORD not in response.text


@pytest.mark.parametrize("email", ["hidden\x00name@example.test", "hidden\u200bname@example.test"])
def test_control_characters_cannot_create_unroundtrippable_account_addresses(admin, email):
    _, initial, client = admin
    assert create_account(client, initial["site"]["id"], email=email).status_code == 422


def test_self_password_reset_requires_new_sign_in(admin):
    app, initial, client = admin
    result = action(client, initial["user"]["id"], "password", password=NEW_PASSWORD)
    assert result.json() == {"ok": True, "sign_in_required": True}
    assert client.get("/api/session").status_code == 401
    assert client_for(app, EMAIL, NEW_PASSWORD).get("/api/admin/users").status_code == 200


@pytest.mark.parametrize("operation,changes", [
    ("enabled", {"enabled": True}),
    ("password", {"password": "Synthetic stale replacement 503!"}),
    ("access", {"role": "MANAGER"}),
])
def test_stale_account_version_cannot_overwrite_newer_manager_change(admin, operation, changes):
    app, initial, client = admin
    site = initial["site"]["id"]
    user = create_account(client, site).json()["user"]
    old_version = user["version"]
    assert action(client, user["id"], "enabled", enabled=False).status_code == 200
    if operation == "access":
        changes = {**changes, "site_id": site}
    response = action(client, user["id"], operation, expected_version=old_version, **changes)
    assert response.status_code == 409 and response.json()["error"]["code"] == "ADMIN_ACCOUNT_CHANGED"
    current = next(u for u in client.get("/api/admin/users").json()["users"] if u["id"] == user["id"])
    assert current["enabled"] is False and current["version"] != old_version
    assert current["branches"][0]["role"] == "REVIEWER"


def test_two_manager_edits_with_same_snapshot_have_one_winner(admin):
    app, initial, first = admin
    second = client_for(app)
    user = create_account(first, initial["site"]["id"]).json()["user"]
    common = {"manager_password": PASSWORD, "expected_version": user["version"]}
    def edit(item):
        client, route, body = item
        return client.post(f"/api/admin/users/{user['id']}/{route}", json={**common, **body}).status_code
    with ThreadPoolExecutor(max_workers=2) as workers:
        statuses = list(workers.map(edit, [
            (first, "enabled", {"enabled": False}),
            (second, "password", {"password": "Synthetic concurrent replacement 748!"}),
        ]))
    assert sorted(statuses) == [200, 409]
