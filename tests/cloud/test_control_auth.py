"""Cloud identity regressions; real PostgreSQL tests are explicitly opt-in."""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import importlib
import json
import os
from pathlib import Path
import secrets
import time
from uuid import uuid4

from cryptography.fernet import Fernet
import psycopg
import pytest
from pydantic import ValidationError

from services.cloud.config import CloudSettings, ConfigurationError
import services.cloud.control_auth as control_auth
from services.cloud.control_auth import hash_password, verify_password, totp_code, verify_totp
from services.cloud.control_models import SetupBegin, InvitationCreate, UserUpdate, PharmacyUpdate
from services.cloud.control_store import ControlError, ControlStore, COOKIE_NAME, cookie_name, require_origin, token_hash, validate_principal, Principal
from services.cloud.database import MIGRATIONS, migration_sources
from services.cloud.migrate import migrate, MigrationError
from control_test_support import disposable_postgres, reset_database, new_client, bootstrap, pharmacy, invited_client

REAL_PG = pytest.mark.skipif(os.environ.get("CLOUD_RUN_POSTGRES_TESTS") != "1", reason="Explicit opt-in required for disposable PostgreSQL")


@pytest.fixture(scope="module")
def database_settings():
    with disposable_postgres() as settings:
        yield settings


@pytest.fixture
def settings(database_settings):
    reset_database(database_settings)
    return database_settings


@pytest.fixture
def owner(settings):
    with new_client(settings) as client:
        session, secret = bootstrap(client, settings)
        yield client, session, secret


def test_password_hash_is_salted_and_checks_correctly():
    first = hash_password("A synthetic passphrase 123")
    second = hash_password("A synthetic passphrase 123")
    assert first != second
    assert verify_password("A synthetic passphrase 123", first)
    assert not verify_password("wrong", first)
    assert not verify_password("wrong", None)
    assert not verify_password("wrong", "pbkdf2-sha256$999999999$ab$cd")
    assert "synthetic" not in first


@pytest.mark.parametrize("timestamp,expected", [(59,"94287082"),(1111111109,"07081804"),(1111111111,"14050471"),(1234567890,"89005924"),(2000000000,"69279037"),(20000000000,"65353130")])
def test_totp_rfc6238_sha1_vectors(timestamp, expected):
    secret = "GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ"  # RFC public test vector
    assert totp_code(secret, timestamp // 30, digits=8) == expected
    assert verify_totp(secret, expected[-6:], now=timestamp) == timestamp // 30
    assert verify_totp(secret, expected[-6:], last_counter=timestamp // 30, now=timestamp) is None


def test_totp_rejects_non_ascii_and_outside_time_window():
    secret = "GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ"
    assert verify_totp(secret, "１２３４５６", now=1234567890) is None
    assert verify_totp(secret, totp_code(secret, 100), now=30 * 102) is None
    assert verify_totp(secret, totp_code(secret, 100), now=30 * 101) == 100


@pytest.mark.parametrize("field,value", [("password","short"),("name","\n"),("organisation_name","  "),("email","a@b"),("token","x"*10)])
def test_setup_model_rejects_unsafe_or_invalid_input(field, value):
    data = dict(token="x"*43,organisation_name="Synthetic",name="Owner",email="a@example.test",password="Long synthetic password")
    data[field] = value
    with pytest.raises(ValidationError):
        SetupBegin(**data)


@pytest.mark.parametrize("change", [{"active": None},{"expected_version": True,"active":False},{"unexpected":"OWNER"},{}])
def test_strict_admin_updates(change):
    data = {"expected_version":1,**change}
    with pytest.raises(ValidationError):
        UserUpdate(**data)
    with pytest.raises(ValidationError):
        PharmacyUpdate(**data)


@pytest.mark.parametrize("role,ids", [("OWNER",[str(uuid4())]),("MANAGER",[]),("REVIEWER",["not-uuid"]),("REVIEWER",[str(uuid4()).upper()])])
def test_invite_scope_is_strict(role, ids):
    with pytest.raises(ValidationError):
        InvitationCreate(name="Staff",email="s@example.test",role=role,pharmacy_ids=ids)


def test_auth_config_secrets_are_hidden_and_invalid_values_are_not_echoed():
    key, token = Fernet.generate_key().decode(), secrets.token_urlsafe(32)
    settings = CloudSettings.from_env({"CLOUD_ENV":"development","CLOUD_AUTH_KEY":key,"CLOUD_BOOTSTRAP_TOKEN":token})
    assert key not in repr(settings) and token not in repr(settings)
    for field in ("CLOUD_AUTH_KEY", "CLOUD_BOOTSTRAP_TOKEN"):
        with pytest.raises(ConfigurationError) as error:
            CloudSettings.from_env({"CLOUD_ENV":"development","CLOUD_AUTH_KEY":key,field:"invalid-secret-value"})
        assert "invalid-secret-value" not in str(error.value)


def test_cumulative_migrations_detect_changed_earlier_file(tmp_path):
    directory = Path(__file__).parents[2] / "services/cloud/migrations"
    # Resolve repo root without depending on invocation cwd.
    directory = Path(__file__).resolve().parents[2] / "services/cloud/migrations"
    for source in directory.glob("*.sql"):
        (tmp_path / source.name).write_bytes(source.read_bytes())
    before = migration_sources(tmp_path)
    (tmp_path / "002_identity.sql").write_text((tmp_path / "002_identity.sql").read_text() + "\n-- Changed old migration\n")
    after = migration_sources(tmp_path)
    assert before[0] == after[0]
    assert before[-1][2] != after[-1][2]


@REAL_PG
def test_bootstrap_requires_secret_mfa_and_is_one_time(settings):
    with new_client(settings) as client:
        assert client.get("/control-api/setup/status").json() == {"configured":True,"needs_setup":True}
        payload = {"token":"x"*43,"organisation_name":"Synthetic","name":"Owner","email":"owner@example.test","password":"Synthetic password 123"}
        assert client.post("/control-api/setup/begin",json=payload).status_code == 401
        with psycopg.connect(settings.database_url) as conn:
            assert conn.execute("SELECT count(*) FROM aislesignals_control.users").fetchone() == (0,)
        session, secret = bootstrap(client, settings)
        assert session["user"]["role"] == "OWNER" and session["pharmacies"] == []
        assert client.get("/control-api/setup/status").json() == {"configured":True,"needs_setup":False}
        payload["token"] = settings.bootstrap_token
        assert client.post("/control-api/setup/begin",json=payload).status_code == 409
        assert client.get("/control-api/session").status_code == 200
        with psycopg.connect(settings.database_url) as conn:
            data = conn.execute("SELECT password_hash,totp_encrypted FROM aislesignals_control.users").fetchone()
            assert secret not in data[1] and "Synthetic password" not in data[0]
            assert conn.execute("SELECT payload_encrypted FROM aislesignals_control.auth_challenges WHERE consumed_at IS NOT NULL").fetchone() == (None,)


@REAL_PG
def test_bootstrap_invalid_mfa_attempts_commit_and_expire(settings):
    with new_client(settings) as client:
        response = client.post("/control-api/setup/begin",json={"token":settings.bootstrap_token,"organisation_name":"Synthetic","name":"Owner","email":"owner@example.test","password":"Synthetic password 123"})
        challenge = response.json()
        for _ in range(6):
            assert client.post("/control-api/setup/complete",json={"challenge_token":challenge["challenge_token"],"code":"nototp"}).status_code == 422
        # Validation errors never enter the credential challenge, numeric wrong
        # codes do; calculate a guaranteed nonmatching code for this time window.
        valid = {totp_code(challenge["totp_secret"], int(time.time())//30 + offset) for offset in (-1,0,1)}
        wrong = next(str(i).zfill(6) for i in range(10) if str(i).zfill(6) not in valid)
        for _ in range(6):
            assert client.post("/control-api/setup/complete",json={"challenge_token":challenge["challenge_token"],"code":wrong}).status_code == 401
        assert client.post("/control-api/setup/complete",json={"challenge_token":challenge["challenge_token"],"code":totp_code(challenge["totp_secret"],int(time.time())//30)}).status_code == 429
        with psycopg.connect(settings.database_url) as conn:
            assert conn.execute("SELECT attempts FROM aislesignals_control.auth_challenges").fetchone() == (6,)
            assert conn.execute("SELECT count(*) FROM aislesignals_control.users").fetchone() == (0,)


@REAL_PG
def test_bootstrap_claim_race_has_exactly_one_owner(settings):
    starts = []
    for index in range(2):
        with new_client(settings) as client:
            r = client.post("/control-api/setup/begin",json={"token":settings.bootstrap_token,"organisation_name":"Synthetic","name":"Owner","email":f"owner{index}@example.test","password":"Synthetic password 123"})
            starts.append(r.json())
    def complete(challenge):
        with new_client(settings) as client:
            return client.post("/control-api/setup/complete",json={"challenge_token":challenge["challenge_token"],"code":totp_code(challenge["totp_secret"],int(time.time())//30)}).status_code
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(complete,starts)) == [200,409]
    with psycopg.connect(settings.database_url) as conn:
        assert conn.execute("SELECT count(*) FROM aislesignals_control.users").fetchone() == (1,)
        assert conn.execute("SELECT count(*) FROM aislesignals_control.organisations").fetchone() == (1,)


@REAL_PG
def test_password_alone_never_creates_session_and_totp_replay_rejected(settings, owner, monkeypatch):
    owner_client, session, secret = owner
    with new_client(settings) as client:
        r = client.post("/control-api/login",json={"email":"OWNER@example.test","password":"Synthetic-only-password-123"})
        assert r.status_code == 200 and r.json()["mfa_required"] is True
        assert not client.cookies and client.get("/control-api/session").status_code == 401
        with psycopg.connect(settings.database_url) as conn:
            consumed_counter = conn.execute(
                "SELECT last_totp_counter FROM aislesignals_control.users WHERE id=%s",
                (session["user"]["id"],),
            ).fetchone()[0]
        verification_time = [(consumed_counter - 1) * 30]

        def fixed_verify_totp(secret_value, code, last_counter=-1, *, now=None):
            return verify_totp(secret_value, code, last_counter, now=verification_time[0])

        monkeypatch.setattr(control_auth, "verify_totp", fixed_verify_totp)
        body = {"challenge_token":r.json()["challenge_token"],"code":totp_code(secret,consumed_counter)}
        assert client.post("/control-api/login/mfa",json=body).status_code == 401
        verification_time[0] = (consumed_counter + 1) * 30
        body["code"] = totp_code(secret,consumed_counter + 1)
        r = client.post("/control-api/login/mfa",json=body)
        assert r.status_code == 200
        assert client.get("/control-api/session").json()["user"]["id"] == session["user"]["id"]
        assert client.post("/control-api/login/mfa",json=body).status_code == 401
        assert "HttpOnly" in r.headers["set-cookie"] and "SameSite=strict" in r.headers["set-cookie"]


@REAL_PG
def test_cookie_staging_is_host_only_secure_httponly(settings):
    # PostgreSQL remains a disposable loopback database; staging is used solely
    # to exercise cookie/Origin policy, never external credentials.
    staging = replace(settings,environment="staging")
    with new_client(staging) as client:
        bootstrap(client,staging)
        assert COOKIE_NAME in client.cookies
        cookie = next(iter(client.cookies.jar))
        assert cookie.secure and not cookie.domain_specified and cookie.path == "/"
        assert cookie.has_nonstandard_attr("HttpOnly")
        assert client.get("/control-api/session").status_code == 200


@REAL_PG
def test_csrf_origin_and_unauthenticated_requests_are_excluded(settings, owner):
    client,session,_ = owner
    body = {"name":"Blocked"}
    assert client.post("/control-api/pharmacies",json=body,headers={"X-CSRF-Token":"wrong"}).status_code == 403
    assert client.post("/control-api/pharmacies",json=body,headers={"Origin":"https://attacker.example"}).status_code == 403
    assert client.post("/control-api/pharmacies",json=body,headers={"Origin":"null"}).status_code == 403
    assert client.post("/control-api/pharmacies",json=body,headers={"Origin":"https://testserver.attacker.example"}).status_code == 403
    assert client.post("/control-api/pharmacies",json=body,headers={"Sec-Fetch-Site":"cross-site"}).status_code == 403
    with new_client(settings) as anonymous:
        assert anonymous.get("/control-api/users").status_code == 401
        assert anonymous.post("/control-api/pharmacies",json=body).status_code == 401
        assert anonymous.post("/control-api/login",json={"email":"owner@example.test","password":"Synthetic-only-password-123"},headers={"Origin":""}).status_code == 403
    assert client.get("/control-api/pharmacies").json() == {"items":[]}


@REAL_PG
def test_missing_auth_configuration_fails_closed(settings):
    for config in [replace(settings,auth_key=None),replace(settings,database_url=None)]:
        with new_client(config) as client:
            assert client.get("/control-api/setup/status").json() == {"configured":False,"needs_setup":False}
            assert client.post("/control-api/login",json={"email":"x@example.test","password":"anything"}).status_code == 503
    with new_client(replace(settings,bootstrap_token=None)) as client:
        assert client.get("/control-api/setup/status").json() == {"configured":False,"needs_setup":False}
        assert client.post("/control-api/setup/begin",json={"token":"x"*43,"organisation_name":"X","name":"X","email":"x@example.test","password":"Synthetic password 123"}).status_code == 503


@REAL_PG
def test_persistent_login_limit_survives_new_app_instance(settings, owner):
    client,_,_ = owner
    for _ in range(10):
        assert client.post("/control-api/login",json={"email":"nonexistent@example.test","password":"wrong"}).status_code == 401
    with new_client(settings) as restarted:
        assert restarted.post("/control-api/login",json={"email":"nonexistent@example.test","password":"wrong"}).status_code == 429
    with psycopg.connect(settings.database_url) as conn:
        values = conn.execute("SELECT bucket,count FROM aislesignals_control.auth_attempts").fetchall()
        assert any(count==11 for _,count in values)
        assert all("nonexistent" not in bucket for bucket,_ in values)


@REAL_PG
def test_invite_enforces_mfa_single_use_and_scoped_authority(settings, owner):
    client,_,_ = owner
    harbour,other = pharmacy(client),pharmacy(client,"Synthetic Other")
    reviewer,session,_,invitation = invited_client(client,settings,pharmacy_ids=[harbour["id"]])
    with reviewer:
        assert [p["id"] for p in session["pharmacies"]] == [harbour["id"]]
        assert [p["id"] for p in reviewer.get("/control-api/pharmacies").json()["items"]] == [harbour["id"]]
        assert reviewer.get("/control-api/users").status_code == 403
        assert reviewer.get("/control-api/invitations").status_code == 403
        assert reviewer.post("/control-api/pharmacies",json={"name":"No"}).status_code == 403
        assert reviewer.patch("/control-api/users/"+session["user"]["id"],json={"expected_version":1,"role":"OWNER","pharmacy_ids":[]}).status_code == 403
        assert reviewer.post("/control-api/invitations/begin",json={"token":invitation["token"],"name":"No","password":"Synthetic password 123"}).status_code == 400
    with psycopg.connect(settings.database_url) as conn:
        stored = conn.execute("SELECT token_hash,consumed_at FROM aislesignals_control.invitations").fetchone()
        assert stored[0] == token_hash(invitation["token"]) and stored[1] is not None
        assert invitation["token"] not in stored[0]


@REAL_PG
def test_owner_can_list_and_revoke_pending_invitation_without_exposing_token(settings, owner):
    client,_,_ = owner
    branch = pharmacy(client)
    invitation = client.post("/control-api/invitations", json={
        "email": "pending-visible@example.test", "name": "Pending Visible",
        "role": "REVIEWER", "pharmacy_ids": [branch["id"]],
    }).json()
    listed = client.get("/control-api/invitations")
    assert listed.status_code == 200
    items = listed.json()["items"]
    assert len(items) == 1
    assert items[0]["email"] == "pending-visible@example.test"
    assert items[0]["pharmacy_ids"] == [branch["id"]]
    assert "token" not in repr(items) and invitation["token"] not in repr(items)

    invitation_id = items[0]["id"]
    response = client.delete(f"/control-api/invitations/{invitation_id}")
    assert response.status_code == 200 and response.json() == {"status": "revoked"}
    assert client.get("/control-api/invitations").json() == {"items": []}
    with new_client(settings) as anonymous:
        rejected = anonymous.post("/control-api/invitations/begin", json={
            "token": invitation["token"], "name": "Pending Visible",
            "password": "Synthetic password 123",
        })
        assert rejected.status_code == 400
    assert client.delete(f"/control-api/invitations/{invitation_id}").status_code == 404


@REAL_PG
def test_membership_changes_revoke_session_and_reject_stale_edit(settings, owner):
    client,_,_ = owner
    first,second = pharmacy(client),pharmacy(client,"Synthetic Other")
    reviewer,session,_,_ = invited_client(client,settings,pharmacy_ids=[first["id"]])
    uid = session["user"]["id"]
    response = client.patch("/control-api/users/"+uid,json={"expected_version":1,"pharmacy_ids":[second["id"]]})
    assert response.status_code == 200 and response.json()["version"] == 2
    assert reviewer.get("/control-api/session").status_code == 401
    stale = client.patch("/control-api/users/"+uid,json={"expected_version":1,"active":False})
    assert stale.status_code == 409 and stale.json()["error"]["code"] == "VERSION_CONFLICT"
    assert next(u for u in client.get("/control-api/users").json()["items"] if u["id"]==uid)["active"] is True
    reviewer.close()


@REAL_PG
def test_last_owner_cannot_disable_or_demote_self(settings, owner):
    client,session,_ = owner
    path = "/control-api/users/" + session["user"]["id"]
    assert client.patch(path,json={"expected_version":1,"active":False}).json()["error"]["code"] == "LAST_OWNER"
    p = pharmacy(client)
    assert client.patch(path,json={"expected_version":1,"role":"REVIEWER","pharmacy_ids":[p["id"]]}).json()["error"]["code"] == "LAST_OWNER"
    assert client.get("/control-api/session").status_code == 200


@REAL_PG
def test_owner_removal_revokes_created_invitations(settings, owner):
    client,session,_ = owner
    p = pharmacy(client)
    second,second_session,_,_ = invited_client(client,settings,role="OWNER",email="second@example.test")
    pending = client.post("/control-api/invitations",json={"email":"pending@example.test","name":"Pending","role":"REVIEWER","pharmacy_ids":[p["id"]]}).json()
    assert second.patch("/control-api/users/"+session["user"]["id"],json={"expected_version":1,"active":False}).status_code == 200
    with new_client(settings) as anonymous:
        assert anonymous.post("/control-api/invitations/begin",json={"token":pending["token"],"name":"Pending","password":"Synthetic password 123"}).status_code == 400
    assert client.get("/control-api/session").status_code == 401
    second.close()


@REAL_PG
def test_pharmacy_duplicate_and_stale_edit_protection(settings, owner):
    client,_,_ = owner
    p = pharmacy(client)
    assert client.post("/control-api/pharmacies",json={"name":"synthetic harbour"}).status_code == 409
    assert client.patch("/control-api/pharmacies/"+p["id"],json={"expected_version":1,"address":"Updated"}).status_code == 200
    assert client.patch("/control-api/pharmacies/"+p["id"],json={"expected_version":1,"active":False}).status_code == 409
    assert client.get("/control-api/pharmacies").json()["items"][0]["active"] is True


@REAL_PG
def test_session_expiry_logout_and_revocation_persist(settings, owner):
    client,session,_ = owner
    old_cookie = client.cookies.get(cookie_name(settings))
    assert client.post("/control-api/logout",json={}).status_code == 200
    client.cookies.set(cookie_name(settings),old_cookie,domain="testserver.local",path="/")
    assert client.get("/control-api/session").status_code == 401
    with psycopg.connect(settings.database_url) as conn:
        assert conn.execute("SELECT revoked_at IS NOT NULL FROM aislesignals_control.sessions").fetchone() == (True,)


@REAL_PG
@pytest.mark.parametrize("field,sql", [("absolute","expires_at=CURRENT_TIMESTAMP-interval '1 second'"),("idle","last_seen_at=CURRENT_TIMESTAMP-interval '31 minutes'")])
def test_session_timeout_is_server_enforced(settings, owner, field, sql):
    client,_,_ = owner
    with psycopg.connect(settings.database_url) as conn:
        conn.execute("UPDATE aislesignals_control.sessions SET "+sql)
    assert client.get("/control-api/session").status_code == 401


@REAL_PG
def test_cross_organisation_ids_and_nested_metadata_create_no_authority(settings, owner):
    client,_,_ = owner
    with psycopg.connect(settings.database_url) as conn:
        org_id,foreign_id = uuid4(),uuid4()
        conn.execute("INSERT INTO aislesignals_control.organisations(id,name) VALUES(%s,'Synthetic foreign')",(org_id,))
        conn.execute("INSERT INTO aislesignals_control.pharmacies(id,organisation_id,name) VALUES(%s,%s,'Foreign')",(foreign_id,org_id))
    assert client.patch("/control-api/pharmacies/"+str(foreign_id),json={"expected_version":1,"active":False}).status_code == 404
    assert client.post("/control-api/invitations",json={"email":"foreign@example.test","name":"Foreign","role":"REVIEWER","pharmacy_ids":[str(foreign_id)]}).status_code == 403
    assert client.post("/control-api/pharmacies",json={"name":"Extra","organisation_id":str(org_id)}).status_code == 422
    assert client.get("/control-api/pharmacies").json() == {"items":[]}


@REAL_PG
def test_database_membership_foreign_key_rejects_cross_org(settings, owner):
    client,session,_ = owner
    with psycopg.connect(settings.database_url) as conn:
        org_id,pid = uuid4(),uuid4()
        conn.execute("INSERT INTO aislesignals_control.organisations(id,name) VALUES(%s,'Foreign')",(org_id,))
        conn.execute("INSERT INTO aislesignals_control.pharmacies(id,organisation_id,name) VALUES(%s,%s,'Foreign')",(pid,org_id))
    with pytest.raises(psycopg.errors.ForeignKeyViolation), psycopg.connect(settings.database_url) as conn:
        conn.execute("INSERT INTO aislesignals_control.user_pharmacies(user_id,pharmacy_id,organisation_id) VALUES(%s,%s,%s)",(session["user"]["id"],pid,session["organisation"]["id"]))


@REAL_PG
def test_migration_upgrade_preserves_v1_and_rejects_changed_predecessor(settings, monkeypatch, tmp_path):
    module = importlib.import_module("services.cloud.migrate")
    source = Path(__file__).resolve().parents[2] / "services/cloud/migrations"
    for file in source.glob("*.sql"):
        (tmp_path/file.name).write_bytes(file.read_bytes())
    path = tmp_path/"002_identity.sql"
    path.write_text(path.read_text()+"\n-- tampered predecessor\n")
    monkeypatch.setattr(module,"MIGRATIONS",migration_sources(tmp_path))
    with pytest.raises(MigrationError,match="incompatible"):
        module.migrate(settings)
    with psycopg.connect(settings.database_url) as conn:
        assert conn.execute("SELECT version,checksum FROM aislesignals_control.schema_version").fetchone() == (MIGRATIONS[-1][0],MIGRATIONS[-1][2])


@REAL_PG
def test_auth_secrets_never_appear_in_audit(settings, owner):
    client,session,secret = owner
    p = pharmacy(client)
    invitation = client.post("/control-api/invitations",json={"email":"audit@example.test","name":"Synthetic","role":"REVIEWER","pharmacy_ids":[p["id"]]}).json()
    with psycopg.connect(settings.database_url) as conn:
        rows = conn.execute("SELECT * FROM aislesignals_control.audit_entries").fetchall()
    text = repr(rows)
    assert rows and "OWNER_BOOTSTRAPPED" in text and "USER_INVITED" in text
    for value in (settings.auth_key,settings.bootstrap_token,secret,session["csrf_token"],invitation["token"],"Synthetic-only-password-123"):
        assert value not in text


@REAL_PG
def test_invitation_expiry_and_bootstrap_rotation_invalidate_pending_enrolment(settings, owner):
    client,_,_ = owner
    p = pharmacy(client)
    invitation = client.post("/control-api/invitations",json={"email":"expired@example.test","name":"Expired","role":"REVIEWER","pharmacy_ids":[p["id"]]}).json()
    with new_client(settings) as anonymous:
        challenge = anonymous.post("/control-api/invitations/begin",json={"token":invitation["token"],"name":"Expired","password":"Synthetic password 123"}).json()
        with psycopg.connect(settings.database_url) as conn:
            conn.execute("UPDATE aislesignals_control.invitations SET expires_at=CURRENT_TIMESTAMP-interval '1 second'")
        response = anonymous.post("/control-api/invitations/complete",json={"challenge_token":challenge["challenge_token"],"code":totp_code(challenge["totp_secret"],int(time.time())//30)})
        assert response.status_code == 400 and not anonymous.cookies
    assert len(client.get("/control-api/users").json()["items"]) == 1


@REAL_PG
def test_bootstrap_rotation_invalidates_previous_enrolment(settings):
    with new_client(settings) as client:
        challenge = client.post("/control-api/setup/begin",json={"token":settings.bootstrap_token,"organisation_name":"Synthetic","name":"Owner","email":"owner@example.test","password":"Synthetic password 123"}).json()
    with new_client(replace(settings,bootstrap_token=secrets.token_urlsafe(32))) as restarted:
        response = restarted.post("/control-api/setup/complete",json={"challenge_token":challenge["challenge_token"],"code":totp_code(challenge["totp_secret"],int(time.time())//30)})
        assert response.status_code == 401 and not restarted.cookies
    with psycopg.connect(settings.database_url) as conn:
        assert conn.execute("SELECT count(*) FROM aislesignals_control.users").fetchone() == (0,)


@REAL_PG
def test_same_totp_two_login_challenges_only_one_session(settings, owner):
    client,_,secret = owner
    challenges = [client.post("/control-api/login",json={"email":"owner@example.test","password":"Synthetic-only-password-123"}).json() for _ in range(2)]
    code = totp_code(secret,int(time.time())//30+1)
    def complete(challenge):
        with new_client(settings) as fresh:
            return fresh.post("/control-api/login/mfa",json={"challenge_token":challenge["challenge_token"],"code":code}).status_code
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(complete,challenges)) == [200,401]
    with psycopg.connect(settings.database_url) as conn:
        assert conn.execute("SELECT count(*) FROM aislesignals_control.sessions").fetchone() == (2,)


@REAL_PG
def test_concurrent_owner_disable_preserves_last_owner(settings, owner):
    client,session,_ = owner
    second,second_session,_,_ = invited_client(client,settings,role="OWNER",email="second@example.test")
    def disable(args):
        current,uid = args
        return current.patch("/control-api/users/"+uid,json={"expected_version":1,"active":False}).status_code
    with ThreadPoolExecutor(max_workers=2) as pool:
        statuses = list(pool.map(disable,[(client,session["user"]["id"]),(second,second_session["user"]["id"])]))
    assert sorted(statuses) == [200,409]
    with psycopg.connect(settings.database_url) as conn:
        assert conn.execute("SELECT count(*) FROM aislesignals_control.users WHERE active=true AND role='OWNER'").fetchone() == (1,)
    second.close()


@REAL_PG
def test_disabled_pharmacy_revokes_member_and_member_can_still_be_disabled(settings, owner):
    client,_,_ = owner
    p = pharmacy(client)
    reviewer,session,_,_ = invited_client(client,settings,pharmacy_ids=[p["id"]])
    assert client.patch("/control-api/pharmacies/"+p["id"],json={"expected_version":1,"active":False}).status_code == 200
    assert reviewer.get("/control-api/session").status_code == 401
    assert client.patch("/control-api/users/"+session["user"]["id"],json={"expected_version":1,"active":False}).status_code == 200
    reviewer.close()


@REAL_PG
def test_pending_login_cannot_survive_owner_access_revocation(settings, owner):
    client,_,_ = owner
    p = pharmacy(client)
    reviewer,session,secret,_ = invited_client(client,settings,pharmacy_ids=[p["id"]])
    login = reviewer.post("/control-api/login",json={"email":"reviewer@example.test","password":"Synthetic-only-password-123"}).json()
    assert client.patch("/control-api/users/"+session["user"]["id"],json={"expected_version":1,"active":False}).status_code == 200
    result = reviewer.post("/control-api/login/mfa",json={"challenge_token":login["challenge_token"],"code":totp_code(secret,int(time.time())//30+1)})
    assert result.status_code == 401
    reviewer.close()


@REAL_PG
def test_failed_mfa_challenge_expires_even_with_correct_code(settings, owner):
    client,_,secret = owner
    challenge = client.post("/control-api/login",json={"email":"owner@example.test","password":"Synthetic-only-password-123"}).json()
    with psycopg.connect(settings.database_url) as conn:
        conn.execute("UPDATE aislesignals_control.auth_challenges SET expires_at=CURRENT_TIMESTAMP-interval '1 second'")
    with new_client(settings) as fresh:
        result = fresh.post("/control-api/login/mfa",json={"challenge_token":challenge["challenge_token"],"code":totp_code(secret,int(time.time())//30+1)})
        assert result.status_code == 401 and not fresh.cookies


@REAL_PG
def test_actual_v1_upgrade_preserves_original_checksum_and_existing_metadata(settings):
    with psycopg.connect(settings.database_url) as conn:
        conn.execute("DROP SCHEMA aislesignals_control CASCADE")
        conn.execute(MIGRATIONS[0][1])
        conn.execute("INSERT INTO aislesignals_control.schema_version(singleton,version,checksum) VALUES(true,1,%s)",(MIGRATIONS[0][2],))
    migrate(settings)
    with psycopg.connect(settings.database_url) as conn:
        assert conn.execute("SELECT version,checksum FROM aislesignals_control.schema_version").fetchone() == (MIGRATIONS[-1][0],MIGRATIONS[-1][2])
        assert conn.execute("SELECT count(*) FROM aislesignals_control.users").fetchone() == (0,)


@REAL_PG
def test_concurrent_invite_consumption_is_single_use(settings, owner):
    client,_,_ = owner
    p = pharmacy(client)
    invitation = client.post("/control-api/invitations",json={"email":"race@example.test","name":"Race","role":"REVIEWER","pharmacy_ids":[p["id"]]}).json()
    with new_client(settings) as anonymous:
        challenge = anonymous.post("/control-api/invitations/begin",json={"token":invitation["token"],"name":"Race","password":"Synthetic password 123"}).json()
    body = {"challenge_token":challenge["challenge_token"],"code":totp_code(challenge["totp_secret"],int(time.time())//30)}
    def complete(_):
        with new_client(settings) as fresh:
            return fresh.post("/control-api/invitations/complete",json=body).status_code
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(complete,range(2))) == [200,401]
    with psycopg.connect(settings.database_url) as conn:
        assert conn.execute("SELECT count(*) FROM aislesignals_control.users WHERE email='race@example.test'").fetchone() == (1,)
