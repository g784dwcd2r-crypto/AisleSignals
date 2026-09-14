from dataclasses import replace
from datetime import datetime, timedelta, timezone
import hashlib
import os
from pathlib import Path
from uuid import uuid4

import psycopg
import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from control_test_support import NonRecurringMaintenance, bootstrap, disposable_postgres, pharmacy, reset_database
from test_control_operations import bearer, device
from services.cloud.app import create_app
from services.cloud.config import CloudSettings, ConfigurationError
from services.cloud.evidence_crypto import EvidenceCryptoError, open_envelope, seal
from services.cloud.evidence_cleanup import EvidenceCleanup
from services.cloud.evidence_store import EvidenceStoreError, FilesystemEvidenceBlobStore, MemoryEvidenceBlobStore


def evidence_settings(settings, path):
    return replace(settings, evidence_mode="ENCRYPTED", evidence_policy="SHORT_LIVED_V1",
                   evidence_kek=b"k" * 32, evidence_kek_version="synthetic-v1", evidence_store_path=Path(path))


def observation():
    stamp = datetime.now(timezone.utc)
    return {"source_event_id": str(uuid4()), "event_code": "POSSIBLE_CONCEALMENT",
            "source_label": "Synthetic camera", "occurred_at": stamp.isoformat(), "historical": True,
            "expires_at": (stamp + timedelta(hours=1)).isoformat()}


def manifest(content, **changes):
    return {"schema_version": 1, "kind": "OVERVIEW", "content_type": "image/jpeg",
            "byte_count": len(content), "sha256": hashlib.sha256(content).hexdigest(), **changes}


def test_envelope_random_dek_aad_and_tamper_detection():
    content, key, aad = b"synthetic jpeg bytes", b"k" * 32, b"synthetic-scope"
    one = seal(content, kek=key, kek_version="v1", aad=aad)
    two = seal(content, kek=key, kek_version="v1", aad=aad)
    assert one != two and open_envelope(one, kek=key, expected_version="v1", aad=aad) == content
    for envelope, version, associated in [(one[:-1] + bytes([one[-1] ^ 1]), "v1", aad), (one, "v2", aad), (one, "v1", b"other")]:
        with pytest.raises(EvidenceCryptoError):
            open_envelope(envelope, kek=key, expected_version=version, aad=associated)


def test_memory_and_filesystem_store_are_create_only_private_and_bounded(tmp_path):
    for store in (MemoryEvidenceBlobStore(), FilesystemEvidenceBlobStore(tmp_path)):
        key = f"evidence/{uuid4()}.bin"
        assert store.put_if_absent(key, b"encrypted") is True
        assert store.put_if_absent(key, b"changed") is False
        assert store.get(key) == b"encrypted"
        store.delete(key)
        with pytest.raises(EvidenceStoreError):
            store.get(key)
    if os.name != "nt":
        assert stat_mode(tmp_path / "evidence") == 0o700


def stat_mode(path):
    return path.stat().st_mode & 0o777


def test_evidence_configuration_defaults_off_and_fails_closed(tmp_path):
    assert CloudSettings.from_env({"CLOUD_ENV": "development"}).evidence_mode == "METADATA_ONLY"
    base = {"CLOUD_ENV": "development", "CLOUD_EVIDENCE_MODE": "ENCRYPTED",
            "CLOUD_EVIDENCE_POLICY": "SHORT_LIVED_V1", "CLOUD_EVIDENCE_KEK": Fernet.generate_key().decode(),
            "CLOUD_EVIDENCE_KEK_VERSION": "v1", "CLOUD_EVIDENCE_STORE_PATH": str(tmp_path)}
    assert CloudSettings.from_env(base).evidence_mode == "ENCRYPTED"
    for key in ("CLOUD_EVIDENCE_POLICY", "CLOUD_EVIDENCE_KEK", "CLOUD_EVIDENCE_KEK_VERSION", "CLOUD_EVIDENCE_STORE_PATH"):
        with pytest.raises(ConfigurationError):
            CloudSettings.from_env({name: value for name, value in base.items() if name != key})
    with pytest.raises(ConfigurationError):
        CloudSettings.from_env({"CLOUD_ENV": "development", "CLOUD_EVIDENCE_KEK": Fernet.generate_key().decode()})


@pytest.fixture(scope="module")
def cluster():
    if os.environ.get("CLOUD_RUN_POSTGRES_TESTS") != "1":
        pytest.skip("Explicit disposable PostgreSQL opt-in required")
    with disposable_postgres() as settings:
        yield settings


@pytest.fixture
def workspace(cluster, tmp_path):
    reset_database(cluster)
    settings = evidence_settings(cluster, tmp_path)
    store = MemoryEvidenceBlobStore()
    owner = TestClient(
        create_app(settings, maintenance_factory=NonRecurringMaintenance, evidence_store=store),
        base_url="https://testserver", headers={"Origin": "https://testserver"})
    bootstrap(owner, settings)
    north, south = pharmacy(owner, "Synthetic North"), pharmacy(owner, "Synthetic South")
    yield settings, owner, store, north, south
    owner.close()


def ingest(owner, laptop, body):
    response = owner.post("/device-api/sync/v1/observations", headers=bearer(laptop), json=body)
    assert response.status_code == 201, response.text
    return response.json()


def test_full_manifest_upload_alert_download_retry_conflict_and_audit(workspace):
    settings, owner, store, north, _ = workspace
    laptop, source, content = device(owner, north), observation(), b"\xff\xd8synthetic-jpeg\xff\xd9"
    alert = ingest(owner, laptop, source)
    path = f"/device-api/sync/v1/observations/{source['source_event_id']}/evidence"
    created = owner.post(path, headers=bearer(laptop), json=manifest(content))
    assert created.status_code == 201, created.text
    evidence = created.json()
    assert evidence["state"] == "PENDING" and evidence["upload_required"] is True
    assert owner.post(path, headers=bearer(laptop), json=manifest(content)).json() == evidence
    assert owner.post(path, headers=bearer(laptop), json=manifest(content, sha256="0" * 64)).status_code == 409
    upload_path = "/device-api/sync/v1/evidence/" + evidence["evidence_id"]
    uploaded = owner.put(upload_path, headers={**bearer(laptop), "Content-Type": "image/jpeg"}, content=content)
    assert uploaded.status_code == 200, uploaded.text
    assert uploaded.json() == {"evidence_id": evidence["evidence_id"], "state": "READY"}
    assert owner.post(path, headers=bearer(laptop), json=manifest(content)).json() == {
        "evidence_id": evidence["evidence_id"], "upload_required": False, "state": "READY"}
    assert owner.put(upload_path, headers={**bearer(laptop), "Content-Type": "image/jpeg"}, content=content).status_code == 200
    assert owner.put(upload_path, headers={**bearer(laptop), "Content-Type": "image/jpeg"}, content=content[:-1] + b"x").status_code == 409
    alert_view = owner.get("/control-api/alerts/" + alert["id"]).json()
    assert alert_view["evidence_state"] == "READY"
    assert alert_view["evidence"][0]["id"] == evidence["evidence_id"] and alert_view["evidence"][0]["state"] == "READY"
    assert alert_view["evidence"][0]["byte_count"] == len(content)
    downloaded = owner.get(f"/control-api/alerts/{alert['id']}/evidence/{evidence['evidence_id']}")
    assert downloaded.status_code == 200 and downloaded.content == content
    assert downloaded.headers["cache-control"] == "no-store" and downloaded.headers["content-type"] == "image/jpeg"
    with psycopg.connect(settings.database_url) as conn:
        assert conn.execute("SELECT count(*) FROM aislesignals_control.audit_entries WHERE action IN ('EVIDENCE_MANIFEST_RECEIVED','EVIDENCE_UPLOADED','EVIDENCE_VIEWED')").fetchone()[0] == 3
        assert conn.execute("SELECT pg_column_size(e.*) FROM aislesignals_control.evidence_objects e").fetchone()[0] < 4096
    assert len(store._objects) == 1


def test_alert_evidence_aggregate_states_and_nested_route_binding(workspace):
    settings, owner, _, north, _ = workspace
    laptop, source = device(owner, north), observation()
    alert = ingest(owner, laptop, source)
    assert owner.get("/control-api/alerts/" + alert["id"]).json()["evidence_state"] == "NONE"
    path = f"/device-api/sync/v1/observations/{source['source_event_id']}/evidence"
    overview, crop = b"overview", b"crop"
    first = owner.post(path, headers=bearer(laptop), json=manifest(overview)).json()
    second = owner.post(path, headers=bearer(laptop), json=manifest(
        crop, kind="INTERACTION_CROP")).json()
    assert owner.get("/control-api/alerts/" + alert["id"]).json()["evidence_state"] == "PENDING"
    assert owner.put("/device-api/sync/v1/evidence/" + first["evidence_id"],
                     headers={**bearer(laptop), "Content-Type": "image/jpeg"}, content=overview).status_code == 200
    partial = owner.get("/control-api/alerts/" + alert["id"]).json()
    assert partial["evidence_state"] == "PARTIAL"
    assert {item["state"] for item in partial["evidence"]} == {"PENDING", "READY"}
    assert owner.put("/device-api/sync/v1/evidence/" + second["evidence_id"],
                     headers={**bearer(laptop), "Content-Type": "image/jpeg"}, content=crop).status_code == 200
    assert owner.get("/control-api/alerts/" + alert["id"]).json()["evidence_state"] == "READY"

    other_source = observation()
    other_alert = ingest(owner, laptop, other_source)
    assert owner.get(f"/control-api/alerts/{other_alert['id']}/evidence/{first['evidence_id']}").status_code == 404

    with psycopg.connect(settings.database_url) as conn:
        conn.execute("UPDATE aislesignals_control.evidence_objects SET expires_at=clock_timestamp()-INTERVAL '1 second' WHERE alert_id=%s", (alert["id"],))
    expired = owner.get("/control-api/alerts/" + alert["id"]).json()
    assert expired["evidence_state"] == "EXPIRED" and expired["evidence"] == []


def test_branch_device_and_withdrawal_isolation(workspace):
    settings, owner, store, north, south = workspace
    one, two, source, content = device(owner, north), device(owner, south), observation(), b"jpeg"
    alert = ingest(owner, one, source)
    path = f"/device-api/sync/v1/observations/{source['source_event_id']}/evidence"
    evidence = owner.post(path, headers=bearer(one), json=manifest(content)).json()
    assert owner.put("/device-api/sync/v1/evidence/" + evidence["evidence_id"], headers={**bearer(two), "Content-Type":"image/jpeg"}, content=content).status_code == 404
    assert owner.post("/device-api/sync/v1/withdrawals", headers=bearer(one), json={"source_event_id":source["source_event_id"],"reason":"LOCAL_DELETED"}).status_code == 200
    assert owner.get(f"/control-api/alerts/{alert['id']}/evidence/{evidence['evidence_id']}").status_code == 404
    with psycopg.connect(settings.database_url) as conn:
        assert conn.execute("SELECT state FROM aislesignals_control.evidence_objects WHERE id=%s", (evidence["evidence_id"],)).fetchone()[0] == "REVOKED"
        assert conn.execute("SELECT count(*) FROM aislesignals_control.audit_entries WHERE action='EVIDENCE_SOURCE_WITHDRAWN'").fetchone()[0] == 1
    assert alert["id"]


def test_scheduled_cleanup_expires_metadata_deletes_blob_and_audits(workspace):
    settings, owner, store, north, _ = workspace
    laptop, source, content = device(owner, north), observation(), b"expiring"
    alert = ingest(owner, laptop, source)
    path = f"/device-api/sync/v1/observations/{source['source_event_id']}/evidence"
    evidence = owner.post(path, headers=bearer(laptop), json=manifest(content)).json()
    owner.put("/device-api/sync/v1/evidence/" + evidence["evidence_id"],
              headers={**bearer(laptop), "Content-Type": "image/jpeg"}, content=content)
    with psycopg.connect(settings.database_url) as conn:
        conn.execute("UPDATE aislesignals_control.evidence_objects SET expires_at=clock_timestamp()-INTERVAL '1 second' WHERE id=%s", (evidence["evidence_id"],))
    result = EvidenceCleanup(owner.app.state.control_store, owner.app.state.evidence_service).run_once()
    assert (result.status, result.revoked, result.deleted) == ("COMPLETED", 1, 1)
    with psycopg.connect(settings.database_url) as conn:
        assert conn.execute("SELECT state FROM aislesignals_control.evidence_objects WHERE id=%s", (evidence["evidence_id"],)).fetchone()[0] == "DELETED"
        actions = conn.execute("SELECT action FROM aislesignals_control.audit_entries WHERE subject_id=%s ORDER BY created_at", (evidence["evidence_id"],)).fetchall()
        assert {row[0] for row in actions} >= {"EVIDENCE_EXPIRED", "EVIDENCE_BLOB_DELETED"}
    assert store._objects == {}
    view = owner.get("/control-api/alerts/" + alert["id"]).json()
    assert view["evidence_state"] == "EXPIRED" and view["evidence"] == []


@pytest.mark.parametrize("changes", [
    {"schema_version": 2}, {"kind": "FACE"}, {"content_type": "image/png"},
    {"byte_count": 350 * 1024 + 1}, {"duration_ms": 1000}, {"sha256": "A" * 64},
])
def test_manifest_contract_rejects_unsafe_or_oversized_images(changes):
    from pydantic import ValidationError
    from services.cloud.evidence_sync import EvidenceManifest
    with pytest.raises(ValidationError):
        EvidenceManifest.model_validate(manifest(b"jpeg", **changes))
