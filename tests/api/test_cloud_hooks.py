"""Real SQLite source/outbox transactions; no network or customer data."""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest

from services.api.cloud_hooks import CloudHooks
from services.api.cloud_outbox import Outbox, Scope, Target
from services.api.store import Store

NOW = datetime(2026, 9, 14, 12, tzinfo=timezone.utc)


class Fixture:
    def __init__(self, tmp_path):
        self.store = Store(tmp_path / 'pilot.sqlite', mode='pilot')
        self.now = NOW
        self.box = Outbox(b's' * 32)
        with self.store.transaction() as conn:
            installation = conn.execute("SELECT value FROM runtime_settings WHERE key='installation_id'").fetchone()[0]
            self.scope = Scope(installation, 'synthetic-org', 'synthetic-site')
            self.user = {'organisation_id': self.scope.organisation_id, 'site_id': self.scope.site_id, 'name': 'Synthetic Manager'}
            self.target = Target('https://synthetic.example.test', *(str(uuid4()) for _ in range(4)))
            self.binding = self.box.create_binding(conn, self.scope, binding_id=str(uuid4()), expected_generation=0,
                                                  target=self.target, now=NOW-timedelta(hours=1))
            self.binding = self.box.set_paused(conn, self.scope, self.binding.ref, paused=False, now=NOW-timedelta(hours=1))
        self.hooks = CloudHooks(self.store, self, clock=lambda: self.now)

    def delivery_context(self, conn):
        return self.source_context(conn, self.scope) if self.binding.state == 'ACTIVE' else None

    def source_context(self, conn, scope):
        if scope != self.scope or self.binding.state not in {'ACTIVE', 'PAUSED'}:
            return None
        return SimpleNamespace(scope=self.scope, binding=self.binding.ref, target=self.target, outbox=self.box)

    def item(self, conn, **changes):
        return dict(id=str(uuid4()), **self.user, source_kind='SCREEN_CAPTURE', source_label='Synthetic private title',
                    created_at=NOW.isoformat(), expires_at=(NOW+timedelta(hours=24)).isoformat(), status='completed',
                    result={'action': 'POSSIBLE_CONCEALMENT', 'alarm_eligible': True},
                    _cloud_admission=self.hooks.admit(conn, self.user), **changes)

    def save(self, conn, item):
        self.hooks.publish(conn, self.user, 'interaction', item)
        self.store.put(conn, self.user, 'interaction', item)

    def pause(self, conn, paused):
        self.binding = self.box.set_paused(conn, self.scope, self.binding.ref, paused=paused, now=self.now)


@pytest.fixture
def local(tmp_path):
    return Fixture(tmp_path)


def test_source_and_minimal_encrypted_observation_commit_or_rollback_together(local):
    with pytest.raises(RuntimeError):
        with local.store.transaction() as conn:
            item = local.item(conn)
            local.save(conn, item)
            assert item['cloud_sync'] == {'state': 'QUEUED'}
            assert conn.execute('SELECT count(*) FROM cloud_sync_items').fetchone()[0] == 1
            raise RuntimeError('Synthetic transaction failure')
    with local.store.transaction() as conn:
        assert conn.execute("SELECT count(*) FROM entities WHERE kind='interaction'").fetchone()[0] == 0
        assert conn.execute('SELECT count(*) FROM cloud_sync_items').fetchone()[0] == 0
        item = local.item(conn)
        local.save(conn, item)
        claim = local.box.claim(conn, local.scope, local.binding.ref, now=NOW)
        assert set(claim.payload) == {'source_event_id', 'event_code', 'source_label', 'occurred_at', 'historical'}
        assert 'Synthetic private title' not in str(dict(claim.payload))
        assert local.hooks.source_state(conn, local.delivery_context(conn), 'interaction', item['id']) == 'AVAILABLE'


@pytest.mark.parametrize('kind', ['missing-admission', 'recorded', 'normal-shopping', 'stale-generation'])
def test_ineligible_completion_remains_local_and_is_not_queued(local, kind):
    with local.store.transaction() as conn:
        item = local.item(conn)
        if kind == 'missing-admission':
            item.pop('_cloud_admission')
        elif kind == 'recorded':
            item['source_kind'] = 'RECORDED_VIDEO'
        elif kind == 'normal-shopping':
            item['result'] = {'action': 'RETURN_PRODUCT', 'alarm_eligible': False}
        else:
            local.pause(conn, True)
            local.pause(conn, False)
        local.save(conn, item)
        assert item['cloud_sync']['state'] == 'NOT_QUEUED'
        assert conn.execute('SELECT count(*) FROM cloud_sync_items').fetchone()[0] == 0
        assert local.store.get(conn, local.user, 'interaction', item['id'])['status'] == 'completed'


def test_admitted_backlog_survives_pause_resume_but_deleted_source_queues_withdrawal(local):
    with local.store.transaction() as conn:
        item = local.item(conn)
        local.save(conn, item)
        claim = local.box.claim(conn, local.scope, local.binding.ref, now=NOW)
        source_id = claim.source_event_id
        local.box.acknowledge(conn, local.scope, local.binding.ref, claim, receipt_id=str(uuid4()), now=NOW)
        local.pause(conn, True)
        local.hooks.withdraw(conn, local.user, 'interaction', item['id'], reason='LOCAL_DELETED')
        conn.execute('DELETE FROM entities WHERE id=?', (item['id'],))
        local.pause(conn, False)
        claim = local.box.claim(conn, local.scope, local.binding.ref, now=NOW)
        assert claim.operation == 'WITHDRAWAL' and claim.source_event_id == source_id
        assert local.hooks.source_state(conn, local.delivery_context(conn), 'interaction', item['id']) == 'DELETED'


def test_source_check_does_not_reject_existing_backlog_on_resume_or_export_modified_sources(local):
    with local.store.transaction() as conn:
        item = local.item(conn)
        local.save(conn, item)
        local.pause(conn, True)
        local.now += timedelta(minutes=1)
        local.pause(conn, False)
        ctx = local.delivery_context(conn)
        assert local.hooks.source_state(conn, ctx, 'interaction', item['id']) == 'AVAILABLE'
        item['source_kind'] = 'RECORDED_VIDEO'
        local.store.put(conn, local.user, 'interaction', item)
        assert local.hooks.source_state(conn, ctx, 'interaction', item['id']) == 'INELIGIBLE'
        item['source_kind'] = 'SCREEN_CAPTURE'
        local.store.put(conn, local.user, 'interaction', item)
        local.now = NOW+timedelta(days=1)
        assert local.hooks.source_state(conn, ctx, 'interaction', item['id']) == 'EXPIRED'


def test_scope_and_synthetic_guards_prevent_admission(local):
    with local.store.transaction() as conn:
        assert local.hooks.admit(conn, {**local.user, 'site_id': 'other-site'}) is None
        local.store.mode = 'synthetic'
        assert local.hooks.admit(conn, local.user) is None
        local.store.mode = 'pilot'


@pytest.mark.parametrize('field,value', [
    ('id', str(uuid4())),
    ('created_at', (NOW+timedelta(seconds=1)).isoformat()),
    ('expires_at', (NOW+timedelta(hours=23)).isoformat()),
])
def test_source_check_rejects_changed_identity_or_immutable_times(local, field, value):
    import json
    with local.store.transaction() as conn:
        item = local.item(conn)
        local.save(conn, item)
        original_id = item['id']
        item[field] = value
        conn.execute("UPDATE entities SET body=? WHERE kind='interaction' AND id=?", (json.dumps(item), original_id))
        local.now += timedelta(minutes=1)
        assert local.hooks.source_state(conn, local.delivery_context(conn), 'interaction', original_id) == 'INELIGIBLE'
