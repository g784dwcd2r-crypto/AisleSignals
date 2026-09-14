"""Strict metadata contracts and real disposable PostgreSQL lifecycle checks."""

from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import os
from threading import Event
from uuid import uuid4

import psycopg
import pytest
from pydantic import ValidationError

from control_test_support import bootstrap, disposable_postgres, invited_client, new_client, pharmacy, reset_database
from test_control_operations import bearer, device, review_body
from services.cloud import device_sync as sync


def observation(**changes):
    stamp = datetime.now(timezone.utc)
    return {"source_event_id": str(uuid4()), "event_code": "POSSIBLE_PRODUCT_TAKE",
            "source_label": "Synthetic camera label", "occurred_at": stamp.isoformat(),
            "historical": True, "expires_at": (stamp + timedelta(hours=1)).isoformat(), **changes}


@pytest.mark.parametrize("patch", [
    {"historical": False}, {"historical": 1}, {"historical": "true"},
    {"source_event_id": "not-a-uuid"}, {"source_event_id": " " + str(uuid4())},
    {"source_label": ""}, {"source_label": "\t"}, {"source_label": "synthetic\nlabel"},
    {"source_label": 123}, {"event_code": "THEFT_CONFIRMED"}, {"occurred_at": 1234567890},
    {"occurred_at": "2026-09-14T12:00:00"}, {"expires_at": None},
    {"occurred_at": "0001-01-01T00:00:00+23:59"},
    {"expires_at": "9999-12-31T23:59:59-23:59"},
    {"expires_at": "2026-09-14"}, {"pharmacy_id": str(uuid4())},
    {"organisation_id": str(uuid4())}, {"frames": ["synthetic-do-not-upload"]},
])
def test_observation_contract_rejects_coercions_authority_and_media(patch):
    with pytest.raises(ValidationError):
        sync.Observation.model_validate(observation(**patch))


def test_expiry_interval_and_utc_hash_normalization():
    base = observation(occurred_at="2026-09-14T12:00:00Z", expires_at="2026-09-15T12:00:00Z")
    one = sync.Observation.model_validate(base)
    two = sync.Observation.model_validate({**base, "occurred_at":"2026-09-14T13:00:00+01:00"})
    assert sync.payload_hash(one) == sync.payload_hash(two)
    for end in ["2026-09-14T12:00:00Z", "2026-09-15T12:00:01Z", "2026-09-14T11:00:00Z"]:
        with pytest.raises(ValidationError):
            sync.Observation.model_validate({**base, "expires_at": end})


@pytest.fixture(scope="module")
def cluster():
    if os.environ.get("CLOUD_RUN_POSTGRES_TESTS") != "1":
        pytest.skip("Explicit disposable PostgreSQL opt-in required")
    with disposable_postgres() as settings:
        yield settings


@pytest.fixture
def workspace(cluster):
    reset_database(cluster)
    owner = new_client(cluster)
    session, _ = bootstrap(owner, cluster)
    north, south = pharmacy(owner, "Synthetic North"), pharmacy(owner, "Synthetic South")
    yield cluster, owner, session, north, south
    owner.close()


def ingest(client, laptop, body):
    return client.post("/device-api/sync/v1/observations", headers=bearer(laptop), json=body)


def withdraw(client, laptop, identifier, reason="LOCAL_DELETED"):
    return client.post("/device-api/sync/v1/withdrawals", headers=bearer(laptop), json={"source_event_id": identifier, "reason": reason})


def cleanup(owner, laptop, limit=100):
    with owner.app.state.control_store.transaction() as conn:
        row = conn.execute("SELECT organisation_id FROM aislesignals_control.devices WHERE id=%s", (laptop["device_id"],)).fetchone()
        conn.execute("SELECT id FROM aislesignals_control.organisations WHERE id=%s FOR SHARE", (row["organisation_id"],))
        current = conn.execute("SELECT * FROM aislesignals_control.devices WHERE id=%s FOR UPDATE", (laptop["device_id"],)).fetchone()
        return sync.cleanup_device_sources(conn, current, limit=limit)


def test_available_retry_changed_payload_and_legacy_protocol_guard(workspace):
    _, owner, _, north, _ = workspace
    laptop, body = device(owner, north), observation()
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: ingest(owner, laptop, body), range(2)))
    assert [r.status_code for r in results] == [201, 201]
    assert results[0].json() == results[1].json()
    receipt = results[0].json()
    assert set(receipt) == {"id", "received", "source_state"}
    assert receipt["source_state"] == "AVAILABLE"
    alert = owner.get("/control-api/alerts/" + receipt["id"]).json()
    assert alert["historical"] is True and alert["timestamp_basis"] == "LAPTOP_REPORTED"
    assert datetime.fromisoformat(alert["source_expires_at"].replace("Z","+00:00")) == datetime.fromisoformat(body["expires_at"])
    assert ingest(owner, laptop, {**body, "source_label": "changed"}).status_code == 409
    assert ingest(owner, laptop, {**body, "expires_at": (datetime.now(timezone.utc)+timedelta(hours=2)).isoformat()}).status_code == 409
    legacy = {key:value for key,value in body.items() if key != "expires_at"}
    assert owner.post("/device-api/alerts", headers=bearer(laptop), json=legacy).status_code == 409
    assert len(owner.get("/control-api/alerts").json()["items"]) == 1


def test_delete_before_arrival_stays_withdrawn_and_never_resurrects_legacy(workspace):
    settings, owner, _, north, _ = workspace
    laptop, body = device(owner, north), observation()
    first = withdraw(owner, laptop, body["source_event_id"])
    assert first.status_code == 200
    with psycopg.connect(settings.database_url) as conn:
        original = conn.execute("SELECT withdrawn_at,withdrawal_reason FROM aislesignals_control.device_sync_receipts").fetchone()
    assert withdraw(owner, laptop, body["source_event_id"], "LOCAL_EXPIRED").json() == first.json()
    result = ingest(owner, laptop, body)
    assert result.status_code == 201 and result.json()["source_state"] == "WITHDRAWN"
    assert ingest(owner, laptop, body).json() == result.json()
    assert ingest(owner, laptop, {**body,"source_label":"changed"}).status_code == 409
    legacy = {key:value for key,value in body.items() if key != "expires_at"}
    assert owner.post("/device-api/alerts", headers=bearer(laptop), json=legacy).status_code == 409
    assert owner.get("/control-api/alerts").json()["items"] == []
    with psycopg.connect(settings.database_url) as conn:
        assert conn.execute("SELECT withdrawn_at,withdrawal_reason FROM aislesignals_control.device_sync_receipts").fetchone() == original
        assert conn.execute("SELECT count(*) FROM aislesignals_control.alerts").fetchone()[0] == 0


def test_expiry_hides_sources_before_cleanup_and_preserves_reviewed_case(workspace):
    settings, owner, _, north, _ = workspace
    laptop, body = device(owner, north), observation()
    receipt = ingest(owner, laptop, body).json()
    review = owner.post("/control-api/alerts/"+receipt["id"]+"/review", json=review_body(note="Synthetic independently authored note", title="Synthetic staff case"))
    assert review.status_code == 200
    case = review.json()["incident"]
    assert case["source_unavailable"] is False
    with psycopg.connect(settings.database_url) as conn:
        conn.execute("UPDATE aislesignals_control.alerts SET source_expires_at=clock_timestamp()-interval '1 second'")
        conn.execute("UPDATE aislesignals_control.device_sync_receipts SET expires_at=clock_timestamp()-interval '1 second'")
    assert owner.get("/control-api/alerts").json()["items"] == []
    missing = owner.get("/control-api/alerts/"+receipt["id"])
    assert missing.status_code == 404 and body["source_label"] not in missing.text
    assert owner.post("/control-api/alerts/"+receipt["id"]+"/review", json=review_body(2)).status_code == 404
    assert owner.post("/control-api/alerts/"+receipt["id"]+"/acknowledge", json={"expected_version":2}).status_code == 404
    dashboard = owner.get("/control-api/dashboard").json()
    assert dashboard["summary"]["open_alerts"] == 0
    assert dashboard["summary"]["reviewed_incidents"] == 1
    kept = owner.get("/control-api/incidents/"+case["id"]).json()
    assert kept["source_unavailable"] is True and kept["notes"] == case["notes"] and kept["title"] == case["title"]
    assert body["source_label"] not in str(dashboard)
    assert cleanup(owner, laptop)["sources_purged"] == 1
    with psycopg.connect(settings.database_url) as conn:
        row = conn.execute("SELECT event_code,title,source_label,occurred_at,review_note FROM aislesignals_control.alerts").fetchone()
        assert row[:4] == (None, None, None, None) and row[4] == case["notes"]
    assert ingest(owner, laptop, body).json() == {**receipt, "source_state":"EXPIRED"}
    assert owner.patch("/control-api/incidents/"+case["id"], json={"expected_version":1,"notes":"Synthetic updated independent note"}).status_code == 200


def test_withdrawal_purges_unreviewed_alert_and_legacy_alert_without_losing_receipt(workspace):
    settings, owner, _, north, _ = workspace
    laptop, body = device(owner, north), observation()
    receipt = ingest(owner, laptop, body).json()
    assert withdraw(owner, laptop, body["source_event_id"], "LOCAL_EXPORT_REMOVED").status_code == 200
    assert ingest(owner, laptop, body).json() == {**receipt,"source_state":"WITHDRAWN"}
    with psycopg.connect(settings.database_url) as conn:
        assert conn.execute("SELECT count(*) FROM aislesignals_control.alerts").fetchone()[0] == 0
    legacy = {key:value for key,value in observation().items() if key != "expires_at"}
    created = owner.post("/device-api/alerts", headers=bearer(laptop), json=legacy)
    assert created.status_code == 201
    assert withdraw(owner, laptop, legacy["source_event_id"]).status_code == 200
    assert owner.post("/device-api/alerts", headers=bearer(laptop), json=legacy).status_code == 409


def test_expired_arrival_is_payloadless_and_future_or_ancient_admission_rejects(workspace):
    settings, owner, _, north, _ = workspace
    laptop = device(owner, north)
    stamp = datetime.now(timezone.utc)
    body = observation(occurred_at=(stamp-timedelta(hours=2)).isoformat(), expires_at=(stamp-timedelta(hours=1)).isoformat())
    result = ingest(owner, laptop, body)
    assert result.status_code == 201 and result.json()["source_state"] == "EXPIRED"
    assert ingest(owner, laptop, body).json() == result.json()
    assert owner.get("/control-api/alerts").json()["items"] == []
    with psycopg.connect(settings.database_url) as conn:
        assert conn.execute("SELECT count(*) FROM aislesignals_control.alerts").fetchone()[0] == 0
        assert conn.execute("SELECT count(*) FROM aislesignals_control.device_sync_receipts").fetchone()[0] == 1
    for offset in [timedelta(minutes=6), -timedelta(days=31)]:
        start = stamp+offset
        invalid = observation(occurred_at=start.isoformat(),expires_at=(start+timedelta(hours=1)).isoformat())
        assert ingest(owner, laptop, invalid).status_code == 422


def test_device_and_tenant_isolation_revocation_and_no_nested_authority(workspace):
    settings, owner, _, north, south = workspace
    one, two = device(owner, north), device(owner, south)
    body = observation()
    receipt = ingest(owner, one, body).json()
    assert withdraw(owner, two, body["source_event_id"]).status_code == 200
    assert owner.get("/control-api/alerts/"+receipt["id"]).status_code == 200
    assert ingest(owner, two, body).json()["source_state"] == "WITHDRAWN"
    manager, _, _, _ = invited_client(owner, settings, role="MANAGER", pharmacy_ids=[south["id"]])
    with manager:
        assert manager.get("/control-api/alerts/"+receipt["id"]).status_code == 404
        assert manager.get("/control-api/alerts",params={"pharmacy_id":north["id"]}).status_code == 403
    assert owner.post("/device-api/sync/v1/withdrawals",json={"source_event_id":body["source_event_id"],"reason":"LOCAL_DELETED"}).status_code == 401
    for inject in [{"pharmacy_id":north["id"]},{"organisation_id":str(uuid4())},{"frames":["synthetic-private"]}]:
        response=ingest(owner, one, {**observation(),**inject})
        assert response.status_code == 422 and "synthetic-private" not in response.text
    assert owner.post("/control-api/devices/"+one["device_id"]+"/revoke",json={"expected_version":1}).status_code == 200
    assert ingest(owner,one,body).status_code == 401
    assert withdraw(owner,one,body["source_event_id"]).status_code == 401


def test_quotas_do_not_block_known_withdrawal_or_replay(workspace, monkeypatch):
    _, owner, _, north, _ = workspace
    laptop, body = device(owner,north), observation()
    receipt=ingest(owner,laptop,body).json()
    monkeypatch.setattr(sync,"DAILY_INTAKE_LIMIT",1)
    monkeypatch.setattr(sync,"DAILY_UNKNOWN_WITHDRAWAL_LIMIT",0)
    monkeypatch.setattr(sync,"MAX_RECEIPTS",1)
    assert ingest(owner,laptop,observation()).status_code == 429
    assert withdraw(owner,laptop,str(uuid4())).status_code == 429
    assert withdraw(owner,laptop,body["source_event_id"]).status_code == 200
    assert ingest(owner,laptop,body).json() == {**receipt,"source_state":"WITHDRAWN"}
    assert withdraw(owner,laptop,body["source_event_id"]).status_code == 200


def test_bounded_cleanup_prunes_old_payloadless_receipts_and_rejects_original_replay(workspace):
    settings, owner, _, north, _ = workspace
    laptop=device(owner,north)
    ids=[str(uuid4()) for _ in range(3)]
    for identifier in ids:
        assert withdraw(owner,laptop,identifier).status_code == 200
    with psycopg.connect(settings.database_url) as conn:
        conn.execute("UPDATE aislesignals_control.device_sync_receipts SET withdrawn_at=clock_timestamp()-interval '32 days',created_at=clock_timestamp()-interval '32 days'")
    assert cleanup(owner,laptop,limit=2)["receipts_pruned"] == 2
    with psycopg.connect(settings.database_url) as conn:
        assert conn.execute("SELECT count(*) FROM aislesignals_control.device_sync_receipts").fetchone()[0] == 1
    assert cleanup(owner,laptop,limit=2)["receipts_pruned"] == 1
    old=datetime.now(timezone.utc)-timedelta(days=33)
    body=observation(source_event_id=ids[0],occurred_at=old.isoformat(),expires_at=(old+timedelta(hours=1)).isoformat())
    assert ingest(owner,laptop,body).status_code == 422
    legacy={key:value for key,value in body.items() if key!="expires_at"}
    assert owner.post("/device-api/alerts",headers=bearer(laptop),json=legacy).status_code == 422


def test_withdrawal_quota_cannot_be_reset_by_late_arrival(workspace, monkeypatch):
    _, owner, _, north, _ = workspace
    laptop, body=device(owner,north), observation()
    monkeypatch.setattr(sync,"DAILY_UNKNOWN_WITHDRAWAL_LIMIT",1)
    assert withdraw(owner,laptop,body["source_event_id"]).status_code == 200
    assert ingest(owner,laptop,body).json()["source_state"] == "WITHDRAWN"
    assert withdraw(owner,laptop,str(uuid4())).status_code == 429
    assert withdraw(owner,laptop,body["source_event_id"]).status_code == 200


def test_revocation_while_request_waits_rechecks_device_before_acceptance(workspace, monkeypatch):
    _, owner, _, north, _=workspace
    laptop, body=device(owner,north), observation()
    original=sync.device_transaction
    entered, release=Event(), Event()
    @contextmanager
    def paused(request):
        entered.set()
        assert release.wait(8)
        with original(request) as scoped:
            yield scoped
    monkeypatch.setattr(sync,"device_transaction",paused)
    with ThreadPoolExecutor(max_workers=1) as pool:
        pending=pool.submit(ingest,owner,laptop,body)
        try:
            assert entered.wait(5)
            assert owner.post("/control-api/devices/"+laptop["device_id"]+"/revoke",json={"expected_version":1}).status_code == 200
        finally:
            release.set()
        assert pending.result(timeout=5).status_code == 401
    assert owner.get("/control-api/alerts").json()["items"] == []


def test_cross_organisation_withdrawal_and_reads_have_no_source_authority(workspace):
    import base64
    import secrets
    import time
    from services.cloud.control_auth import _insert_user, hash_password, totp_code
    settings, owner, _, north, _=workspace
    laptop, body=device(owner,north), observation()
    receipt=ingest(owner,laptop,body).json()
    other=new_client(settings)
    secret=base64.b32encode(secrets.token_bytes(20)).decode()
    with other.app.state.control_store.transaction() as conn:
        org=str(uuid4())
        conn.execute("INSERT INTO aislesignals_control.organisations(id,name) VALUES(%s,%s)",(org,"Synthetic second group"))
        _insert_user(conn,settings,organisation_id=org,name="Synthetic second owner",email="second@example.test",password_hash=hash_password("Synthetic-second-password-489"),secret=secret,counter=-1,role="OWNER",pharmacy_ids=[])
    challenge=other.post("/control-api/login",json={"email":"second@example.test","password":"Synthetic-second-password-489"})
    assert challenge.status_code == 200
    session=other.post("/control-api/login/mfa",json={"challenge_token":challenge.json()["challenge_token"],"code":totp_code(secret,int(time.time())//30)})
    assert session.status_code == 200
    other.headers["X-CSRF-Token"]=session.json()["csrf_token"]
    with other:
        other_device=device(other,pharmacy(other,"Synthetic unrelated branch"))
        assert other.get("/control-api/alerts/"+receipt["id"]).status_code == 404
        assert withdraw(other,other_device,body["source_event_id"]).status_code == 200
        assert ingest(other,other_device,body).json()["source_state"] == "WITHDRAWN"
        assert owner.get("/control-api/alerts/"+receipt["id"]).status_code == 200
        assert other.get("/control-api/alerts").json()["items"] == []


def test_withdrawn_reviewed_legacy_case_keeps_independent_notes_and_scope(workspace):
    settings, owner, _, north, south=workspace
    laptop=device(owner,north)
    body={key:value for key,value in observation().items() if key!="expires_at"}
    received=owner.post("/device-api/alerts",headers=bearer(laptop),json=body).json()
    before=owner.get("/control-api/alerts/"+received["id"]).json()
    assert before["timestamp_basis"] == "SOURCE_REPORTED" and before["source_expires_at"] is None
    reviewed=owner.post("/control-api/alerts/"+received["id"]+"/review",json=review_body(note="Synthetic staff-only note",title="Synthetic authored case"))
    assert reviewed.status_code == 200
    case=reviewed.json()["incident"]
    assert withdraw(owner,laptop,body["source_event_id"]).status_code == 200
    kept=owner.get("/control-api/incidents/"+case["id"]).json()
    assert kept == {**case,"source_unavailable":True}
    with psycopg.connect(settings.database_url) as conn:
        assert conn.execute("SELECT source_label,review_note FROM aislesignals_control.alerts WHERE id=%s",(received["id"],)).fetchone() == (None,"Synthetic staff-only note")
    manager,_,_,_=invited_client(owner,settings,role="MANAGER",pharmacy_ids=[south["id"]])
    with manager:
        assert manager.get("/control-api/incidents/"+case["id"]).status_code == 404
    assert owner.post("/device-api/alerts",headers=bearer(laptop),json=body).status_code == 409
