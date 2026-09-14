"""Transactional source admission and lifecycle hooks; no HTTP or media reads."""

from datetime import datetime, timezone
import json
import hashlib
import hmac

from .cloud_observation import AdmissionSnapshot, BindingSnapshot, Exclusion, MappedObservation, ObservationScope, canonical_payload, map_observation
from .cloud_outbox import Scope


def utcnow():
    return datetime.now(timezone.utc)


class CloudHooks:
    def __init__(self, store, provider, *, clock=utcnow):
        self.store, self.provider, self.clock = store, provider, clock

    def _scope(self, conn, user):
        row = conn.execute("SELECT value FROM runtime_settings WHERE key='installation_id'").fetchone()
        return Scope(row[0], user['organisation_id'], user['site_id'])

    @staticmethod
    def _matches(ctx, scope):
        return ctx is not None and ctx.scope == scope

    def admit(self, conn, user):
        if self.store.mode != 'pilot':
            return None
        try:
            scope = self._scope(conn, user)
            ctx = self.provider.delivery_context(conn)
            if not self._matches(ctx, scope):
                return None
            row = conn.execute("SELECT activated_ms FROM cloud_sync_bindings WHERE id=? AND generation=? AND state='ACTIVE'",
                               (ctx.binding.id, ctx.binding.generation)).fetchone()
            if row is None:
                return None
            return {'binding_id': ctx.binding.id, 'generation': ctx.binding.generation,
                    'activated_at': datetime.fromtimestamp(row[0] / 1000, timezone.utc).isoformat()}
        except Exception:
            # A cloud credential problem must not prevent local camera analysis.
            return None

    def _map(self, ctx, kind, item, *, backlog=False):
        meta = item.get('_cloud_admission')
        if not isinstance(meta, dict) or set(meta) != {'binding_id', 'generation', 'activated_at'}:
            return Exclusion.ADMISSION_MISSING
        try:
            if meta['binding_id'] != ctx.binding.id:
                return Exclusion.BINDING_CHANGED
            admission = AdmissionSnapshot(meta['binding_id'], meta['generation'])
            # Backlog already passed admission. A pause/resume fences its lease,
            # not its immutable admission identity or original activation time.
            generation = admission.generation if backlog else ctx.binding.generation
            binding = BindingSnapshot(ctx.scope.installation_id, ctx.binding.id, generation,
                                      ctx.scope.organisation_id, ctx.scope.site_id,
                                      datetime.fromisoformat(meta['activated_at']), enabled=True)
            return map_observation(database_mode=self.store.mode,
                                   scope=ObservationScope(ctx.scope.installation_id, ctx.scope.organisation_id, ctx.scope.site_id),
                                   binding=binding, admission=admission, entity_kind=kind, item=item, now=self.clock())
        except (TypeError, ValueError, KeyError, OverflowError):
            return Exclusion.INVALID_OBSERVATION

    def publish(self, conn, user, kind, item):
        if self.store.mode != 'pilot':
            return
        try:
            ctx = self.provider.delivery_context(conn)
            if not self._matches(ctx, self._scope(conn, user)):
                item['cloud_sync'] = {'state': 'NOT_QUEUED', 'code': 'CONNECTION_INACTIVE'}
                return
            observation = self._map(ctx, kind, item)
            if not isinstance(observation, MappedObservation):
                item['cloud_sync'] = {'state': 'NOT_QUEUED', 'code': observation.value}
                return
            ctx.outbox.enqueue(conn, ctx.scope, ctx.binding, observation, now=self.clock())
            item['cloud_sync'] = {'state': 'QUEUED'}
        except Exception:
            # enqueue's savepoint prevents a partial queue row. The local result
            # remains reviewable, explicitly marked as not exported.
            item['cloud_sync'] = {'state': 'NOT_QUEUED', 'code': 'SYNC_UNAVAILABLE'}

    def withdraw(self, conn, user, kind, entity_id, *, reason):
        if self.store.mode != 'pilot':
            return
        scope = self._scope(conn, user)
        try:
            ctx = self.provider.source_context(conn, scope)
            if not self._matches(ctx, scope):
                return
            rows = conn.execute("SELECT id FROM cloud_sync_items WHERE binding_id=? AND entity_kind=? AND entity_id=?",
                                (ctx.binding.id, kind, entity_id)).fetchall()
            for row in rows:
                ctx.outbox.withdraw(conn, scope, ctx.binding, row[0], reason=reason, now=self.clock())
        except Exception:
            # Local deletion remains available with lost credentials. Its absence
            # is rechecked by the sender before any later send; remote expiry is
            # independently enforced. Never fabricate a withdrawal receipt.
            self.store.audit(conn, user, 'CLOUD_REMOVAL_UNCONFIRMED', kind, entity_id,
                             'Local source removed; cloud removal is unconfirmed. Check the cloud connection.')

    def source_state(self, conn, ctx, kind, entity_id):
        if self.store.mode != 'pilot':
            return 'INELIGIBLE'
        row = conn.execute("SELECT body FROM entities WHERE kind=? AND id=? AND organisation_id=? AND site_id=?",
                           (kind, entity_id, ctx.scope.organisation_id, ctx.scope.site_id)).fetchone()
        if row is None:
            return 'DELETED'
        try:
            mapped = self._map(ctx, kind, json.loads(row[0]), backlog=True)
        except (TypeError, ValueError):
            return 'INELIGIBLE'
        if mapped == Exclusion.EXPIRED:
            return 'EXPIRED'
        if not isinstance(mapped, MappedObservation) or mapped.entity_id != entity_id or mapped.entity_kind != kind:
            return 'INELIGIBLE'
        queued = conn.execute("""SELECT observation_hash, admitted_iso, deadline_iso, source_event_id
            FROM cloud_sync_items WHERE binding_id=? AND entity_kind=? AND entity_id=?""",
            (ctx.binding.id, kind, entity_id)).fetchone()
        if queued is None:
            return 'INELIGIBLE'
        admitted = mapped.admitted_at.isoformat().replace('+00:00', 'Z')
        deadline = mapped.deadline.isoformat().replace('+00:00', 'Z')
        fingerprint = hashlib.sha256(canonical_payload(mapped.payload) + b"\n" + deadline.encode()).hexdigest()
        if (not hmac.compare_digest(queued[0], fingerprint) or queued[1] != admitted
                or queued[2] != deadline or queued[3] != mapped.payload['source_event_id']):
            return 'INELIGIBLE'
        return 'AVAILABLE'
