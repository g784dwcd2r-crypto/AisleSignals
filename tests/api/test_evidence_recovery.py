"""Synthetic-only evidence tests; no pharmacy/model accuracy claim."""

import hashlib
import io
import json
import sqlite3
import time
import zipfile
from pathlib import Path, PureWindowsPath
from uuid import uuid4

import pytest
from PIL import Image
from fastapi.testclient import TestClient

from services.api.app import create_app
from services.api.evidence_backup import create_backup, restore_backup, encrypt_archive
from services.api.evidence_crypto import EvidenceCipher, EvidenceError, MAGIC, PilotDatabaseLock, key_path
from services.api.pilot_identity import add_site, grant_user, initialise
from services.api.store import encode
from test_interactions import MockProvider, complete, jpeg, sample, submit, wait_job, ACTIVE_JOBS, ACTIVE_LOCK
from test_pilot_identity import client_for, update_headers, EMAIL, PASSWORD

BACKUP_PASSWORD = "Synthetic archive Passphrase 2026!"


@pytest.fixture
def pilot_evidence(tmp_path):
    app = create_app(tmp_path / "pilot.db", tmp_path / "web", mode="pilot")
    initial = initialise(app.state.store, "Synthetic Test Group", "Synthetic Branch North", EMAIL, "Test Manager", PASSWORD)
    app.state.interactions.provider = MockProvider()
    yield app, initial
    app.state.interactions.provider.release.set()
    app.state.interactions.close()
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        with ACTIVE_LOCK:
            active = any(service is app.state.interactions for service, _ in ACTIVE_JOBS.values())
        if not active:
            break
        time.sleep(.01)
    app.state.interactions.close()


def test_pilot_media_is_encrypted_authenticated_and_bound_to_scope(pilot_evidence):
    app, initial = pilot_evidence
    client = client_for(app)
    item = complete(client)
    path = app.state.interactions.directory(item["id"]) / "0.jpg"
    ciphertext = path.read_bytes()
    assert ciphertext.startswith(MAGIC) and jpeg() not in ciphertext
    recovered = client.get(item["frames"][0]["url"]).content
    with Image.open(io.BytesIO(recovered)) as image:
        assert image.size == (96, 64) and image.getpixel((0, 0))[2] > 240
    cipher = app.state.interactions.cipher
    with app.state.store.transaction() as conn:
        stored = app.state.store.get(conn, initial["user"], "interaction", item["id"])
    for mutated, index in [({**stored, "site_id": str(uuid4())}, 0), ({**stored, "organisation_id": str(uuid4())}, 0), ({**stored, "id": str(uuid4())}, 0), (stored, 1)]:
        with pytest.raises(EvidenceError, match="authentication"):
            cipher.decrypt(ciphertext, mutated, index)
    path.write_bytes(ciphertext[:-1] + bytes([ciphertext[-1] ^ 1]))
    assert client.get(item["frames"][0]["url"]).status_code == 404


def test_key_outside_media_and_missing_key_never_replaced(pilot_evidence):
    app, _ = pilot_evidence
    client = client_for(app)
    complete(client)
    db = Path(app.state.store.path)
    assert key_path(db).parent != app.state.interactions.evidence_root
    app.state.interactions.close()
    key_path(db).unlink()
    with pytest.raises(EvidenceError, match="missing or invalid"):
        create_app(db, mode="pilot")
    assert not key_path(db).exists()


def test_revocation_and_branch_switch_cancel_background_publication(pilot_evidence):
    app, initial = pilot_evidence
    branch = add_site(app.state.store, initial["site"]["organisation_id"], "Synthetic Branch South")
    grant_user(app.state.store, EMAIL, branch["id"], "MANAGER")
    client = client_for(app)
    provider = app.state.interactions.provider
    provider.release.clear()
    response = submit(client)
    assert response.status_code == 202
    assert provider.started.wait(2)
    switched = client.post("/api/session/site", json={"site_id": branch["id"]})
    assert switched.status_code == 200
    update_headers(client, switched.json())
    provider.release.set()
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        with app.state.store.transaction() as conn:
            saved = app.state.store.get(conn, initial["user"], "interaction", response.json()["id"])
        if saved["status"] == "cancelled":
            break
        time.sleep(.01)
    assert saved["status"] == "cancelled" and saved["frames"] == []
    assert client.get("/api/interactions/jobs/" + saved["id"]).status_code == 404


@pytest.mark.parametrize("low_action", ["NORMAL_SHOPPING", "UNCLEAR", "TAKE_PRODUCT", "RETURN_PRODUCT", "PLACE_IN_BASKET"])
def test_rolling_retention_keeps_reviewed_and_possible_concealment(pilot_evidence, monkeypatch, low_action):
    import services.api.interactions as interactions
    monkeypatch.setattr(interactions, "SITE_LIMIT", 3)
    app, initial = pilot_evidence
    client = client_for(app)
    protected = complete(client)
    normal = complete(client)
    reviewed = complete(client)
    assert client.post(f"/api/interactions/{reviewed['id']}/review", json={"outcome": "UNCLEAR"}).status_code == 200
    with app.state.store.transaction() as conn:
        item = app.state.store.get(conn, initial["user"], "interaction", normal["id"])
        item["result"].update(action=low_action, alarm_eligible=False)
        app.state.store.put(conn, initial["user"], "interaction", item)
    new = complete(client)
    assert client.get(f"/api/interactions/jobs/{normal['id']}").status_code == 404
    assert not app.state.interactions.directory(normal["id"]).exists()
    for item in [protected, reviewed, new]:
        assert client.get(f"/api/interactions/jobs/{item['id']}").status_code == 200
    assert submit(client).status_code == 429
    assert len(client.get("/api/interactions").json()["items"]) == 3
    assert client.get("/api/interactions/status").json()["evidence_policy"]["encryption"] == "AES-256-GCM"


def test_backup_refuses_active_pilot_then_restores_matching_media_and_revokes_sessions(pilot_evidence, tmp_path):
    app, initial = pilot_evidence
    client = client_for(app)
    item = complete(client)
    expected_frame = client.get(item["frames"][0]["url"]).content
    archive = tmp_path / "private.asbackup"
    with pytest.raises(EvidenceError, match="Stop the pilot"):
        create_backup(app.state.store.path, archive, BACKUP_PASSWORD)
    assert not archive.exists()
    with app.state.store.transaction() as conn:
        conn.execute("INSERT INTO runtime_settings VALUES('first_owner_setup', ?)",
                     ('{"token_hash":"synthetic-expired-capability","expires_at":9999999999}',))
    app.state.interactions.close()
    report = create_backup(app.state.store.path, archive, BACKUP_PASSWORD)
    assert report["encrypted"] and report["media_frames"] == 3
    raw = archive.read_bytes()
    assert EMAIL.encode() not in raw and b"SQLite format 3" not in raw
    target = tmp_path / "restored"
    restored_report = restore_backup(archive, target, BACKUP_PASSWORD)
    assert restored_report["sessions_revoked"] and restored_report["requires_account_access_review"]
    restored = create_app(target / "aislesignals.db", mode="pilot")
    try:
        locked = TestClient(restored, base_url="http://127.0.0.1:8765", headers={"Origin": "http://127.0.0.1:8765"})
        assert locked.post("/api/login", json={"email": EMAIL, "password": PASSWORD}).status_code == 401
        with restored.state.store.transaction() as conn:
            assert conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0] == 0
            assert conn.execute("SELECT 1 FROM runtime_settings WHERE key='first_owner_setup'").fetchone() is None
            assert conn.execute("SELECT COUNT(*) FROM account_security WHERE enabled=1").fetchone()[0] == 0
            # Explicit local operator reauthorisation, never automatic restore.
            conn.execute("UPDATE account_security SET enabled=1 WHERE user_id=?", (initial["user"]["id"],))
        fresh = client_for(restored)
        assert fresh.get(item["frames"][0]["url"]).content == expected_frame
        with restored.state.store.transaction() as conn:
            assert conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0] == 1  # only fresh login
    finally:
        restored.state.interactions.close()
    with pytest.raises(EvidenceError, match="new, absent"):
        restore_backup(archive, target, BACKUP_PASSWORD)


def test_restore_matches_windows_relative_paths_to_portable_archive_names(pilot_evidence, tmp_path, monkeypatch):
    """Exercise the Windows separator boundary on every host, not just CI."""
    app, _ = pilot_evidence
    complete(client_for(app))
    app.state.interactions.close()
    archive = tmp_path / "portable.asbackup"
    create_backup(app.state.store.path, archive, BACKUP_PASSWORD)
    original_glob = Path.glob

    class WindowsFramePath:
        def __init__(self, path):
            self.path = path

        def relative_to(self, root):
            return PureWindowsPath(*self.path.relative_to(root).parts)

    def windows_evidence_glob(path, pattern, *args, **kwargs):
        paths = original_glob(path, pattern, *args, **kwargs)
        if pattern == "*/*" and path.name.endswith(".interaction-evidence"):
            return (WindowsFramePath(item) for item in paths)
        return paths

    monkeypatch.setattr(Path, "glob", windows_evidence_glob)
    assert restore_backup(archive, tmp_path / "portable-restored", BACKUP_PASSWORD)["restored"] is True


@pytest.mark.parametrize("damage", ["password", "ciphertext", "truncated"])
def test_bad_backup_never_publishes_partial_restore(pilot_evidence, tmp_path, damage):
    app, _ = pilot_evidence
    complete(client_for(app))
    app.state.interactions.close()
    archive = tmp_path / "safe.asbackup"
    create_backup(app.state.store.path, archive, BACKUP_PASSWORD)
    password = BACKUP_PASSWORD
    if damage == "password":
        password = "Another wrong Password 2026!"
    else:
        data = archive.read_bytes()
        archive.write_bytes(data[:-100] if damage == "truncated" else data[:100] + bytes([data[100] ^ 1]) + data[101:])
    with pytest.raises(EvidenceError):
        restore_backup(archive, tmp_path / "not-created", password)
    assert not (tmp_path / "not-created").exists()
    assert not list(tmp_path.glob(".aisle-restore-*"))


@pytest.mark.parametrize("name", ["../escape", "/tmp/escape", "evidence/../../escape", "evidence/invalid/0.jpg"])
def test_authenticated_archive_still_rejects_traversal(tmp_path, name):
    plain, archive = tmp_path / "unsafe.zip", tmp_path / "unsafe.asbackup"
    with zipfile.ZipFile(plain, "w") as zipped:
        zipped.writestr(name, b"unsafe")
        zipped.writestr("manifest.json", "{}")
    encrypt_archive(plain, archive, BACKUP_PASSWORD)
    with pytest.raises(EvidenceError, match="Unsafe"):
        restore_backup(archive, tmp_path / "not-created", BACKUP_PASSWORD)
    assert not (tmp_path / "not-created").exists()


def test_backup_refuses_cross_scope_media_even_with_matching_hash(pilot_evidence, tmp_path):
    app, initial = pilot_evidence
    item = complete(client_for(app))
    with app.state.store.transaction() as conn:
        stored = app.state.store.get(conn, initial["user"], "interaction", item["id"])
        stored["site_id"] = str(uuid4())
        conn.execute("UPDATE entities SET body=? WHERE id=?", (encode(stored), item["id"]))
    app.state.interactions.close()
    with pytest.raises(EvidenceError, match="scope"):
        create_backup(app.state.store.path, tmp_path / "absent.asbackup", BACKUP_PASSWORD)
    assert not (tmp_path / "absent.asbackup").exists()


def test_low_disk_rejects_before_persisting_sample(pilot_evidence, monkeypatch):
    import services.api.interactions as interactions
    from collections import namedtuple
    app, _ = pilot_evidence
    client = client_for(app)
    usage = namedtuple("usage", "total used free")
    monkeypatch.setattr(interactions.shutil, "disk_usage", lambda _: usage(1024, 1023, 1))
    response = submit(client)
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "EVIDENCE_STORAGE_UNAVAILABLE"
    assert app.state.interactions.provider.calls == 0
    assert not list(app.state.interactions.evidence_root.iterdir())


@pytest.mark.parametrize("entry_kind", ["oversized", "duplicate", "compressed"])
def test_restore_rejects_bounded_or_ambiguous_entries(tmp_path, monkeypatch, entry_kind):
    import services.api.evidence_backup as backup
    plain, archive = tmp_path / "unsafe.zip", tmp_path / "unsafe.asbackup"
    monkeypatch.setattr(backup, "MAX_DATABASE", 20)
    with zipfile.ZipFile(plain, "w") as zipped:
        zipped.writestr("manifest.json", "{}")
        if entry_kind == "duplicate":
            with pytest.warns(UserWarning):
                zipped.writestr("manifest.json", "{}")
        else:
            zipped.writestr("database.sqlite", b"A" * (21 if entry_kind == "oversized" else 1),
                            compress_type=zipfile.ZIP_DEFLATED if entry_kind == "compressed" else zipfile.ZIP_STORED)
    encrypt_archive(plain, archive, BACKUP_PASSWORD)
    with pytest.raises(EvidenceError):
        restore_backup(archive, tmp_path / "absent", BACKUP_PASSWORD)
    assert not (tmp_path / "absent").exists()


def test_restore_cancels_inflight_jobs_and_removes_expired_samples(pilot_evidence, tmp_path):
    app, initial = pilot_evidence
    client = client_for(app)
    inflight, old = complete(client), complete(client)
    with app.state.store.transaction() as conn:
        for item_id, changes in [(inflight["id"], {"status": "running"}), (old["id"], {"expires_at": "2020-01-01T00:00:00Z"})]:
            stored = app.state.store.get(conn, initial["user"], "interaction", item_id)
            stored.update(changes)
            app.state.store.put(conn, initial["user"], "interaction", stored)
    app.state.interactions.close()
    archive, target = tmp_path / "recovery.asbackup", tmp_path / "restored"
    create_backup(app.state.store.path, archive, BACKUP_PASSWORD)
    restore_backup(archive, target, BACKUP_PASSWORD)
    with sqlite3.connect(target / "aislesignals.db") as conn:
        item = json.loads(conn.execute("SELECT body FROM entities WHERE id=?", (inflight["id"],)).fetchone()[0])
        assert item["status"] == "cancelled" and item["frames"] == []
        assert not conn.execute("SELECT 1 FROM entities WHERE id=?", (old["id"],)).fetchone()
    assert not list((target / "aislesignals.db.interaction-evidence").iterdir())


def test_cli_never_echoes_mistaken_passphrase_argument(capsys):
    from scripts.pilot_backup import main
    with pytest.raises(SystemExit):
        main(["create", "--db", "test.db", "--output", "new.asbackup", "--passphrase", BACKUP_PASSWORD])
    assert BACKUP_PASSWORD not in capsys.readouterr().err


def test_busy_ordinary_shopping_stream_continues_with_bounded_recent_history(pilot_evidence, monkeypatch):
    import services.api.interactions as interactions
    app, _ = pilot_evidence
    monkeypatch.setattr(interactions, "SITE_LIMIT", 3)
    provider = app.state.interactions.provider
    analyze = provider.analyze

    def ordinary(frames):
        return {**analyze(frames), "action": "TAKE_PRODUCT", "alarm_eligible": False}

    monkeypatch.setattr(provider, "analyze", ordinary)
    client = client_for(app)
    ids = [complete(client)["id"] for _ in range(8)]
    saved = client.get("/api/interactions").json()["items"]
    assert {item["id"] for item in saved} == set(ids[-3:])
    assert len(list(app.state.interactions.evidence_root.iterdir())) == 3


def test_disabled_account_cannot_publish_model_result(pilot_evidence):
    app, initial = pilot_evidence
    client = client_for(app)
    provider = app.state.interactions.provider
    provider.release.clear()
    response = submit(client)
    assert response.status_code == 202 and provider.started.wait(2)
    with app.state.store.transaction() as conn:
        # Keep the session row deliberately: resolver must consult enabled state.
        conn.execute("UPDATE account_security SET enabled=0 WHERE user_id=?", (initial["user"]["id"],))
    provider.release.set()
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        with app.state.store.transaction() as conn:
            item = app.state.store.get(conn, initial["user"], "interaction", response.json()["id"])
        if item["status"] == "cancelled":
            break
        time.sleep(.01)
    assert item["status"] == "cancelled" and item["frames"] == []
    assert client.get("/api/interactions").status_code == 401
