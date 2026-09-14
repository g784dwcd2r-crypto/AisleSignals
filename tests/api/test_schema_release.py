import sqlite3
import pytest
from services.api.store import Store
from test_cloud_store_migration import make_v2


def test_identity_schema_prevents_legacy_binary_reopening(tmp_path):
    path = tmp_path / 'pilot.db'
    Store(path, mode='pilot')
    with sqlite3.connect(path) as conn:
        assert conn.execute('PRAGMA user_version').fetchone()[0] == 3
        assert conn.execute('SELECT COUNT(*) FROM users').fetchone()[0] == 0
        # The released v2 reader's gate accepts exactly 0/1/2; its rejection
        # occurs before executescript. Keep this compatibility boundary explicit.
        with pytest.raises(RuntimeError, match='Unsupported'):
            version = conn.execute('PRAGMA user_version').fetchone()[0]
            if version not in (0, 1, 2):
                raise RuntimeError('Unsupported prototype database schema.')


def test_additive_legacy_demo_migration_keeps_records(tmp_path):
    path = tmp_path / 'demo.db'
    store = Store(path)
    make_v2(path)
    with store.transaction() as conn:
        before = conn.execute('SELECT id,body FROM entities ORDER BY id').fetchall()
        conn.execute('PRAGMA user_version=1')
        conn.execute('DROP TABLE session_scopes')
        conn.execute('DROP TABLE memberships')
        conn.execute('DROP TABLE account_security')
        conn.execute('DROP TABLE runtime_settings')
    migrated = Store(path)
    with migrated.transaction() as conn:
        assert conn.execute('PRAGMA user_version').fetchone()[0] == 3
        assert [tuple(r) for r in conn.execute('SELECT id,body FROM entities ORDER BY id')] == [tuple(r) for r in before]


def test_restored_disabled_account_requires_explicit_operator_enable(tmp_path):
    from services.api.pilot_identity import initialise, enable_user
    store = Store(tmp_path / 'pilot.db', mode='pilot')
    account = initialise(store, 'Synthetic restore group', 'Synthetic restore branch',
                         'restore@example.invalid', 'Synthetic operator', 'SyntheticPassphrase45!')
    with store.transaction() as conn:
        conn.execute('UPDATE account_security SET enabled=0')
    enable_user(store, 'restore@example.invalid')
    with store.transaction() as conn:
        assert conn.execute('SELECT enabled FROM account_security').fetchone()[0] == 1
        assert conn.execute('SELECT COUNT(*) FROM sessions').fetchone()[0] == 0


def test_operator_access_report_excludes_password_material(tmp_path):
    import json
    from services.api.pilot_identity import initialise, list_access
    store = Store(tmp_path / 'pilot.db', mode='pilot')
    initialise(store, 'Synthetic access group', 'Synthetic access branch',
               'access@example.invalid', 'Synthetic operator', 'SyntheticPassphrase45!')
    report = list_access(store)
    assert report['accounts'][0]['branches'][0]['role'] == 'MANAGER'
    text = json.dumps(report)
    assert 'salt' not in text and 'password' not in text and 'SyntheticPassphrase' not in text
