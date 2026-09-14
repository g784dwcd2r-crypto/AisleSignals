"""Real SQLite migrations using synthetic identities; no network or credentials."""

import sqlite3
from contextlib import closing, contextmanager
from uuid import UUID, uuid4

import pytest

from services.api import cloud_outbox
from services.api.store import Store


# Literal core DDL from the released v0/v1 store (89d1dfb and 704436e),
# independent of the current Store's migration/expected-signature code.
LEGACY_USERS = """CREATE TABLE users (
    id TEXT PRIMARY KEY, email TEXT UNIQUE NOT NULL, name TEXT NOT NULL,
    role TEXT NOT NULL, organisation_id TEXT NOT NULL, site_id TEXT NOT NULL,
    salt TEXT NOT NULL, password_hash TEXT NOT NULL)"""
LEGACY_ENTITIES = """CREATE TABLE entities (
    id TEXT PRIMARY KEY, kind TEXT NOT NULL, organisation_id TEXT NOT NULL,
    site_id TEXT NOT NULL, body TEXT NOT NULL, created_at TEXT NOT NULL)"""


@contextmanager
def database(path):
    with closing(sqlite3.connect(path)) as conn, conn:
        yield conn


def make_v2(path):
    """Represent the released v2 schema, without future outbox/identity objects."""
    with database(path) as conn:
        for name in ("items", "capacity", "bindings", "schema"):
            conn.execute("DROP TABLE cloud_sync_" + name)
        conn.execute("DELETE FROM runtime_settings WHERE key='installation_id'")
        conn.execute("PRAGMA user_version=2")


def snapshot(path):
    with database(path) as conn:
        return (conn.execute("PRAGMA user_version").fetchone()[0],
                conn.execute("PRAGMA journal_mode").fetchone()[0], tuple(conn.iterdump()))


@pytest.mark.parametrize("mode", ["pilot", "synthetic"])
def test_fresh_and_v2_upgrade_keep_stable_installation_and_never_pair(tmp_path, mode):
    path = tmp_path / "synthetic.db"
    Store(path, mode=mode)
    make_v2(path)
    with database(path) as conn:
        previous = conn.execute("SELECT * FROM entities ORDER BY id").fetchall()
        conn.execute("INSERT INTO runtime_settings VALUES('synthetic-setting','retained')")
    migrated = Store(path, mode=mode)
    with migrated.transaction() as conn:
        identifier = conn.execute("SELECT value FROM runtime_settings WHERE key='installation_id'").fetchone()[0]
        assert str(UUID(identifier)) == identifier and UUID(identifier).int
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 3
        assert [tuple(row) for row in conn.execute("SELECT * FROM entities ORDER BY id")] == previous
        assert conn.execute("SELECT value FROM runtime_settings WHERE key='synthetic-setting'").fetchone()[0] == "retained"
        assert conn.execute("SELECT count(*) FROM cloud_sync_bindings").fetchone()[0] == 0
        assert conn.execute("SELECT count(*) FROM cloud_sync_items").fetchone()[0] == 0
    reopened = Store(path, mode=mode)
    with reopened.transaction() as conn:
        assert conn.execute("SELECT value FROM runtime_settings WHERE key='installation_id'").fetchone()[0] == identifier


@pytest.mark.parametrize("mode", ["pilot", "synthetic"])
@pytest.mark.parametrize("stage", ["before", "after"])
def test_failed_migration_preserves_all_original_schema_data_and_version(tmp_path, monkeypatch, mode, stage):
    path = tmp_path / "synthetic.db"
    Store(path, mode=mode)
    make_v2(path)
    with database(path) as conn:
        conn.execute("PRAGMA journal_mode=DELETE")
    before = snapshot(path)
    original = cloud_outbox.install_schema

    def fail(conn):
        assert conn.in_transaction
        if stage == "after":
            original(conn)
            conn.execute("INSERT INTO runtime_settings VALUES('synthetic-migration','must-rollback')")
        raise RuntimeError("synthetic injected migration failure")

    monkeypatch.setattr(cloud_outbox, "install_schema", fail)
    with pytest.raises(RuntimeError, match="synthetic injected"):
        Store(path, mode=mode)
    assert snapshot(path) == before


def test_failed_fresh_schema_creation_rolls_back_base_tables_too(tmp_path, monkeypatch):
    path = tmp_path / "new.db"

    def fail(conn):
        assert conn.in_transaction
        assert conn.execute("SELECT name FROM sqlite_master WHERE name='users'").fetchone()
        raise RuntimeError("synthetic failure")

    monkeypatch.setattr(cloud_outbox, "install_schema", fail)
    with pytest.raises(RuntimeError, match="synthetic failure"):
        Store(path, mode="pilot")
    with database(path) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 0
        assert conn.execute("SELECT count(*) FROM sqlite_master").fetchone()[0] == 0
        assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "delete"


@pytest.mark.parametrize("damage", [
    "future_version", "missing_mode", "missing_identity", "invalid_identity",
    "empty_identity", "missing_trigger", "missing_table", "extra_object", "changed_column", "module_version",
])
def test_incompatible_provenance_rejected_before_mutating_database_or_permissions(tmp_path, damage):
    path = tmp_path / "synthetic.db"
    Store(path, mode="pilot")
    sql = {
        "future_version": "PRAGMA user_version=99",
        "missing_mode": "DELETE FROM runtime_settings WHERE key='mode'",
        "missing_identity": "DELETE FROM runtime_settings WHERE key='installation_id'",
        "invalid_identity": "UPDATE runtime_settings SET value='00000000-0000-0000-0000-000000000000' WHERE key='installation_id'",
        "empty_identity": "UPDATE runtime_settings SET value='' WHERE key='installation_id'",
        "missing_trigger": "DROP TRIGGER cloud_sync_binding_immutable",
        "missing_table": "DROP TABLE cloud_sync_capacity",
        "extra_object": "CREATE TABLE cloud_sync_unknown(id INTEGER)",
        "changed_column": "ALTER TABLE cloud_sync_items ADD COLUMN uncertain TEXT",
        "module_version": "UPDATE cloud_sync_schema SET version=2",
    }[damage]
    with database(path) as conn:
        conn.execute(sql)
    path.chmod(0o640)
    before, modes = snapshot(path), path.stat().st_mode
    with pytest.raises(RuntimeError):
        Store(path, mode="pilot")
    assert snapshot(path) == before
    assert path.stat().st_mode == modes


@pytest.mark.parametrize("original,requested", [("pilot", "synthetic"), ("synthetic", "pilot")])
@pytest.mark.parametrize("version", [2, 3])
def test_mode_mismatch_leaves_data_identity_and_file_mode_untouched(tmp_path, original, requested, version):
    path = tmp_path / "synthetic.db"
    Store(path, mode=original)
    if version == 2:
        make_v2(path)
    path.chmod(0o640)
    before, modes = snapshot(path), path.stat().st_mode
    with pytest.raises(RuntimeError, match="mode mismatch"):
        Store(path, mode=requested)
    assert snapshot(path) == before
    assert path.stat().st_mode == modes


def test_v2_requires_explicit_provenance_even_with_existing_data(tmp_path):
    path = tmp_path / "synthetic.db"
    Store(path)
    make_v2(path)
    with database(path) as conn:
        conn.execute("DELETE FROM runtime_settings WHERE key='mode'")
    before = snapshot(path)
    with pytest.raises(RuntimeError, match="provenance is missing"):
        Store(path)
    assert snapshot(path) == before


@pytest.mark.parametrize("version", [0, 1])
@pytest.mark.parametrize("mode", ["pilot", "synthetic"])
def test_unrecognised_database_is_not_claimed(tmp_path, version, mode):
    path = tmp_path / "unrelated.db"
    with database(path) as conn:
        conn.execute(f"PRAGMA user_version={version}")
        conn.execute("CREATE TABLE unrelated(id INTEGER)")
        conn.execute("INSERT INTO unrelated VALUES(7)")
    path.chmod(0o640)
    before = snapshot(path)
    original_bytes, original_mode = path.read_bytes(), path.stat().st_mode
    with pytest.raises(RuntimeError, match="Unrecognised"):
        Store(path, mode=mode)
    assert snapshot(path) == before
    assert path.read_bytes() == original_bytes
    assert path.stat().st_mode == original_mode


@pytest.mark.parametrize("version", [0, 1])
@pytest.mark.parametrize("mode", ["pilot", "synthetic"])
@pytest.mark.parametrize("shape", [
    "names_only", "index_compatible", "missing_identity", "missing_password", "missing_body",
    "wrong_password_type", "wrong_id_type", "missing_primary_key", "nullable_email",
    "missing_unique_email", "extra_column",
])
def test_same_named_unrelated_tables_are_preserved_before_permission_or_schema_writes(tmp_path, version, mode, shape):
    users, entities = LEGACY_USERS, LEGACY_ENTITIES
    if shape == "names_only":
        users, entities = "CREATE TABLE users(id TEXT PRIMARY KEY)", "CREATE TABLE entities(id TEXT PRIMARY KEY)"
    elif shape == "index_compatible":
        users = "CREATE TABLE users(id TEXT PRIMARY KEY)"
        entities = "CREATE TABLE entities(id TEXT PRIMARY KEY, organisation_id TEXT, site_id TEXT, kind TEXT, created_at TEXT)"
    elif shape == "missing_identity":
        users = users.replace("organisation_id TEXT NOT NULL, ", "")
    elif shape == "missing_password":
        users = users.replace(", password_hash TEXT NOT NULL", "")
    elif shape == "missing_body":
        entities = entities.replace("body TEXT NOT NULL, ", "")
    elif shape == "wrong_password_type":
        users = users.replace("password_hash TEXT", "password_hash BLOB")
    elif shape == "wrong_id_type":
        entities = entities.replace("id TEXT PRIMARY KEY", "id INTEGER PRIMARY KEY")
    elif shape == "missing_primary_key":
        users = users.replace("id TEXT PRIMARY KEY", "id TEXT")
    elif shape == "nullable_email":
        users = users.replace("email TEXT UNIQUE NOT NULL", "email TEXT UNIQUE")
    elif shape == "missing_unique_email":
        users = users.replace("email TEXT UNIQUE NOT NULL", "email TEXT NOT NULL")
    elif shape == "extra_column":
        entities = entities[:-1] + ", unrelated TEXT)"
    path = tmp_path / "same-name-unrelated.db"
    with database(path) as conn:
        conn.execute(f"PRAGMA user_version={version}")
        conn.execute(users)
        conn.execute(entities)
        conn.execute("CREATE TABLE unrelated(payload TEXT)")
        conn.execute("INSERT INTO unrelated VALUES('Preserve this unrelated application')")
    path.chmod(0o640)
    before, original_bytes, original_mode = snapshot(path), path.read_bytes(), path.stat().st_mode
    with pytest.raises(RuntimeError, match="Unrecognised"):
        Store(path, mode=mode)
    assert snapshot(path) == before
    assert path.read_bytes() == original_bytes
    assert path.stat().st_mode == original_mode


@pytest.mark.parametrize("version", [0, 1])
def test_literal_released_legacy_tables_upgrade_and_preserve_their_records(tmp_path, version):
    path = tmp_path / "released-legacy.db"
    with database(path) as conn:
        conn.execute(f"PRAGMA user_version={version}")
        conn.execute(LEGACY_USERS)
        conn.execute(LEGACY_ENTITIES)
        conn.execute("INSERT INTO users VALUES(?,?,?,?,?,?,?,?)", (
            "synthetic-user", "manager@marimina.demo", "Synthetic legacy manager", "MANAGER",
            "synthetic-org", "synthetic-site", "synthetic-salt", "synthetic-password-hash"))
        conn.execute("INSERT INTO entities VALUES(?,?,?,?,?,?)", (
            "synthetic-site", "site", "synthetic-org", "synthetic-site",
            '{"id":"synthetic-site","name":"Synthetic legacy branch"}', "2026-09-14T10:00:00Z"))
        original_users = conn.execute("SELECT * FROM users").fetchall()
        original_entities = conn.execute("SELECT * FROM entities").fetchall()
    migrated = Store(path)
    with migrated.transaction() as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 3
        assert [tuple(row) for row in conn.execute("SELECT * FROM users")] == original_users
        assert [tuple(row) for row in conn.execute("SELECT * FROM entities")] == original_entities
        assert conn.execute("SELECT value FROM runtime_settings WHERE key='mode'").fetchone()[0] == "synthetic"
        assert conn.execute("SELECT count(*) FROM cloud_sync_bindings").fetchone()[0] == 0


@pytest.mark.parametrize("version", [0, 1])
def test_recognised_unmarked_legacy_demo_still_upgrades_without_changing_records(tmp_path, version):
    path = tmp_path / "legacy.db"
    Store(path)
    make_v2(path)
    with database(path) as conn:
        conn.execute(f"PRAGMA user_version={version}")
        conn.execute("DELETE FROM runtime_settings WHERE key='mode'")
        original_entities = conn.execute("SELECT * FROM entities ORDER BY id").fetchall()
        original_users = conn.execute("SELECT * FROM users ORDER BY id").fetchall()
    migrated = Store(path)
    with migrated.transaction() as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 3
        assert conn.execute("SELECT value FROM runtime_settings WHERE key='mode'").fetchone()[0] == "synthetic"
        assert [tuple(row) for row in conn.execute("SELECT * FROM entities ORDER BY id")] == original_entities
        assert [tuple(row) for row in conn.execute("SELECT * FROM users ORDER BY id")] == original_users


@pytest.mark.parametrize("mode,wrong_identity", [("synthetic", False), ("pilot", True)])
def test_existing_binding_cannot_change_installation_or_enter_synthetic_mode(tmp_path, mode, wrong_identity):
    from datetime import datetime, timezone
    path = tmp_path / "synthetic.db"
    Store(path, mode=mode)
    with database(path) as conn:
        installation = conn.execute("SELECT value FROM runtime_settings WHERE key='installation_id'").fetchone()[0]
        conn.execute("BEGIN IMMEDIATE")
        cloud_outbox.Outbox(bytes(range(32))).create_binding(
            conn, cloud_outbox.Scope(str(uuid4()) if wrong_identity else installation, "synthetic-org", "synthetic-site"),
            binding_id=str(uuid4()), expected_generation=0,
            target=cloud_outbox.Target("https://synthetic.example.test", *(str(uuid4()) for _ in range(4))),
            now=datetime.now(timezone.utc))
    before = snapshot(path)
    with pytest.raises(RuntimeError, match="identity|Synthetic"):
        Store(path, mode=mode)
    assert snapshot(path) == before


def test_caller_transaction_still_rolls_back_unrelated_writes(tmp_path):
    store = Store(tmp_path / "synthetic.db", mode="pilot")
    with pytest.raises(RuntimeError):
        with store.transaction() as conn:
            cloud_outbox.install_schema(conn)
            conn.execute("INSERT INTO runtime_settings VALUES('synthetic-data','must-rollback')")
            raise RuntimeError("synthetic cancellation")
    with store.transaction() as conn:
        assert not conn.execute("SELECT 1 FROM runtime_settings WHERE key='synthetic-data'").fetchone()


def test_concurrent_v2_openers_resolve_one_durable_installation(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    path = tmp_path / "concurrent.db"
    Store(path, mode="pilot")
    make_v2(path)
    ready = Barrier(4)

    def initialise():
        ready.wait(timeout=5)
        store = Store(path, mode="pilot")
        with store.transaction() as conn:
            return conn.execute("SELECT value FROM runtime_settings WHERE key='installation_id'").fetchone()[0]

    with ThreadPoolExecutor(max_workers=4) as executor:
        results = list(executor.map(lambda _: initialise(), range(4)))
    assert len(set(results)) == 1
    with database(path) as conn:
        assert conn.execute("SELECT count(*) FROM runtime_settings WHERE key='installation_id'").fetchone()[0] == 1
