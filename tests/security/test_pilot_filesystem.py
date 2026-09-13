"""Independent local-account confidentiality regressions, using synthetic data."""

import os
import sqlite3
import stat
from contextlib import contextmanager

import pytest

from services.api.pilot_identity import initialise
from services.api.store import Store


@contextmanager
def normal_umask():
    previous = os.umask(0o022)
    try:
        yield
    finally:
        os.umask(previous)


def mode(path):
    return stat.S_IMODE(path.stat().st_mode)


def symlink_or_skip(source, target, *, directory=False):
    try:
        source.symlink_to(target, target_is_directory=directory)
    except (OSError, NotImplementedError):
        pytest.skip("This platform/account cannot create the symbolic-link fixture.")


@pytest.mark.skipif(os.name == "nt", reason="POSIX modes; Windows ACL acceptance is separate")
def test_direct_account_setup_keeps_database_and_new_ancestors_private(tmp_path):
    parent = tmp_path / "new-installation" / "nested-data"
    db = parent / "pilot.db"
    with normal_umask():
        store = Store(db, mode="pilot")
        initialise(store, "Synthetic Review Group", "Synthetic Review Branch",
                   "review.manager@example.test", "Synthetic Review Manager",
                   "Synthetic test-only passphrase 975!")
        with store.transaction() as conn:
            conn.execute("INSERT INTO runtime_settings VALUES('security-test','synthetic')")
            for suffix in ("", "-wal", "-shm"):
                path = db.with_name(db.name + suffix)
                if path.exists():
                    assert mode(path) == 0o600
    assert mode(parent) == mode(parent.parent) == 0o700
    assert mode(db) == 0o600


@pytest.mark.skipif(os.name == "nt", reason="POSIX modes; Windows ACL acceptance is separate")
def test_existing_parent_is_not_silently_chmodded(tmp_path):
    existing = tmp_path / "shared-project"
    existing.mkdir(mode=0o755)
    before = mode(existing)
    with normal_umask():
        db = existing / "pilot.db"
        Store(db, mode="pilot")
    assert mode(existing) == before
    assert mode(db) == 0o600


@pytest.mark.skipif(os.name == "nt", reason="POSIX modes; Windows ACL acceptance is separate")
def test_existing_pilot_is_hardened_but_demo_mode_mismatch_is_unchanged(tmp_path):
    pilot, demo = tmp_path / "pilot.db", tmp_path / "demo.db"
    Store(pilot, mode="pilot")
    Store(demo)
    pilot.chmod(0o644)
    demo.chmod(0o644)
    before = demo.read_bytes()
    Store(pilot, mode="pilot")
    assert mode(pilot) == 0o600
    with pytest.raises(RuntimeError, match="mode mismatch"):
        Store(demo, mode="pilot")
    assert mode(demo) == 0o644
    assert demo.read_bytes() == before


@pytest.mark.parametrize("where", ["parent", "database", "-wal", "-shm", "-journal"])
def test_pilot_rejects_symbolic_paths_without_touching_link_targets(tmp_path, where):
    target = tmp_path / "unrelated"
    target.mkdir()
    sentinel = target / "sentinel"
    sentinel.write_bytes(b"Synthetic unrelated file must remain unchanged")
    before = sentinel.read_bytes()
    base = tmp_path / "installation"
    if where == "parent":
        symlink_or_skip(base, target, directory=True)
        db = base / "pilot.db"
    else:
        base.mkdir()
        db = base / "pilot.db"
        link = db if where == "database" else base / (db.name + where)
        symlink_or_skip(link, sentinel)
    with pytest.raises(ValueError, match="symbolic"):
        Store(db, mode="pilot")
    assert sentinel.read_bytes() == before
    assert not (target / "pilot.db").exists()


def test_pilot_rejects_directory_as_sqlite_sidecar(tmp_path):
    db = tmp_path / "pilot.db"
    (tmp_path / "pilot.db-wal").mkdir()
    with pytest.raises(ValueError, match="regular"):
        Store(db, mode="pilot")
    assert not db.exists()


def test_pilot_refuses_future_schema_without_changing_its_content(tmp_path):
    db = tmp_path / "future.db"
    with sqlite3.connect(db) as conn:
        conn.execute("PRAGMA user_version=999")
    before = db.read_bytes()
    with pytest.raises(RuntimeError, match="Unsupported"):
        Store(db, mode="pilot")
    assert db.read_bytes() == before
