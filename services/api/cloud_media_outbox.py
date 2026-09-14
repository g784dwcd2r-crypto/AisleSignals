"""Durable references to encrypted local evidence selected for cloud delivery.

The queue stores an immutable frame reference and digest, never image bytes.
All operations require a caller-owned transaction.  Remote deletion is owned by
the parent observation withdrawal; evidence is never sent after that begins.
"""

from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
import math
import re
import sqlite3
from uuid import UUID, uuid4

from .cloud_outbox import BindingRef, OutboxError, Scope

SCHEMA_VERSION = 1
MAX_JPEG_BYTES = 350 * 1024
STATES = frozenset({"PENDING_MANIFEST", "LEASED_MANIFEST", "PENDING_CONTENT",
                    "LEASED_CONTENT", "READY", "CANCELLED", "EXPIRED", "WITHDRAWN", "BLOCKED"})


def _stamp(value):
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise OutboxError("INVALID_CLOCK")
    stamp = value.timestamp() * 1000
    if not math.isfinite(stamp) or not 0 <= stamp <= 253402300799000:
        raise OutboxError("INVALID_CLOCK")
    return int(stamp)


def _uuid(value):
    try:
        if not isinstance(value, str) or str(UUID(value)) != value or UUID(value).int == 0:
            raise ValueError
    except (ValueError, TypeError, AttributeError):
        raise OutboxError("INVALID_IDENTIFIER") from None
    return value


@contextmanager
def _operation(conn):
    if not isinstance(conn, sqlite3.Connection) or not conn.in_transaction:
        raise OutboxError("TRANSACTION_REQUIRED")
    name = "media_" + uuid4().hex
    conn.execute("SAVEPOINT " + name)
    try:
        yield
    except BaseException:
        conn.execute("ROLLBACK TO " + name)
        raise
    finally:
        conn.execute("RELEASE " + name)


def install_schema(conn):
    with _operation(conn):
        exists = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='cloud_media_schema'").fetchone()
        if exists:
            rows = conn.execute("SELECT version FROM cloud_media_schema").fetchall()
            if len(rows) != 1 or rows[0][0] != SCHEMA_VERSION:
                raise OutboxError("SCHEMA_INCOMPATIBLE")
        for statement in (
            "CREATE TABLE IF NOT EXISTS cloud_media_schema(version INTEGER NOT NULL)",
            """CREATE TABLE IF NOT EXISTS cloud_media_items(
                id TEXT PRIMARY KEY,
                observation_item_id TEXT NOT NULL UNIQUE REFERENCES cloud_sync_items(id),
                binding_id TEXT NOT NULL REFERENCES cloud_sync_bindings(id),
                source_event_id TEXT NOT NULL,
                entity_kind TEXT NOT NULL,
                entity_id TEXT NOT NULL,
                kind TEXT NOT NULL CHECK(kind='OVERVIEW'),
                content_type TEXT NOT NULL CHECK(content_type='image/jpeg'),
                frame_index INTEGER NOT NULL,
                frame_sha256 TEXT NOT NULL,
                frame_bytes INTEGER NOT NULL,
                deadline_ms INTEGER NOT NULL,
                created_ms INTEGER NOT NULL,
                state TEXT NOT NULL CHECK(state IN ('PENDING_MANIFEST','LEASED_MANIFEST','PENDING_CONTENT',
                    'LEASED_CONTENT','READY','CANCELLED','EXPIRED','WITHDRAWN','BLOCKED')),
                attempts INTEGER NOT NULL DEFAULT 0,
                ever_attempted INTEGER NOT NULL DEFAULT 0,
                next_ms INTEGER NOT NULL,
                lease_token TEXT,
                lease_until_ms INTEGER,
                claim_generation INTEGER,
                evidence_id TEXT,
                settled_token TEXT,
                terminal_ms INTEGER,
                error_code TEXT,
                UNIQUE(binding_id,source_event_id,kind))""",
            "CREATE INDEX IF NOT EXISTS cloud_media_due ON cloud_media_items(binding_id,state,next_ms)",
            """CREATE TRIGGER IF NOT EXISTS cloud_media_item_immutable
                BEFORE UPDATE OF id,observation_item_id,binding_id,source_event_id,entity_kind,entity_id,
                    kind,content_type,frame_index,frame_sha256,frame_bytes,deadline_ms,created_ms
                ON cloud_media_items BEGIN SELECT RAISE(ABORT,'immutable cloud media'); END""",
        ):
            conn.execute(statement)
        if not exists:
            conn.execute("INSERT INTO cloud_media_schema VALUES(?)", (SCHEMA_VERSION,))


def mark_restored(conn, *, now):
    """Fence media from a restored snapshot without claiming remote deletion."""
    with _operation(conn):
        exists = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='cloud_media_items'").fetchone()
        if not exists:
            return {"media_blocked": 0}
        stamp = _stamp(now)
        conn.execute("""UPDATE cloud_media_items SET state='BLOCKED',error_code='RESTORED_REQUIRES_MANAGEMENT',
            lease_token=NULL,lease_until_ms=NULL,claim_generation=NULL,settled_token=NULL,terminal_ms=NULL
            WHERE state!='WITHDRAWN' AND (ever_attempted!=0 OR attempts>0 OR evidence_id IS NOT NULL
                OR state IN ('LEASED_MANIFEST','LEASED_CONTENT','PENDING_CONTENT'))""")
        conn.execute("""UPDATE cloud_media_items SET state='CANCELLED',error_code='RESTORED',terminal_ms=?,
            lease_token=NULL,lease_until_ms=NULL,claim_generation=NULL,settled_token=NULL
            WHERE state NOT IN ('READY','WITHDRAWN','BLOCKED')""", (stamp,))
        return {"media_blocked": conn.execute(
            "SELECT count(*) FROM cloud_media_items WHERE error_code='RESTORED_REQUIRES_MANAGEMENT'").fetchone()[0]}


@dataclass(frozen=True)
class MediaClaim:
    id: str
    binding: BindingRef
    token: str
    operation: str
    source_event_id: str
    entity_kind: str
    entity_id: str
    kind: str
    content_type: str
    frame_index: int
    sha256: str
    byte_count: int
    evidence_id: str | None
    lease_until: datetime

    @property
    def manifest(self):
        return {"schema_version": 1, "kind": self.kind, "content_type": self.content_type,
                "byte_count": self.byte_count, "sha256": self.sha256}


class MediaOutbox:
    def enqueue_overview(self, conn, scope, ref, observation_item_id, item, *, now):
        """Select exactly one model-cited sampled frame; no media file is read."""
        with _operation(conn):
            stamp = _stamp(now)
            if not isinstance(scope, Scope) or not isinstance(ref, BindingRef):
                raise OutboxError("INVALID_SCOPE")
            _uuid(observation_item_id)
            parent = conn.execute("""SELECT * FROM cloud_sync_items WHERE id=? AND binding_id=?
                AND operation='OBSERVATION'""", (observation_item_id, ref.id)).fetchone()
            if parent is None:
                raise OutboxError("OBSERVATION_NOT_FOUND")
            try:
                indices = item["result"]["evidence_frame_indices"]
                frames = item["frames"]
                index = indices[-1]
                frame = frames[index]
                sha256, size = frame["sha256"], frame["bytes"]
            except (KeyError, IndexError, TypeError):
                raise OutboxError("MEDIA_REFERENCE_INVALID") from None
            if (not isinstance(indices, list) or not indices or type(index) is not int
                    or not isinstance(sha256, str) or not re.fullmatch(r"[0-9a-f]{64}", sha256)
                    or type(size) is not int or not 1 <= size <= MAX_JPEG_BYTES):
                raise OutboxError("MEDIA_REFERENCE_INVALID")
            old = conn.execute("SELECT * FROM cloud_media_items WHERE observation_item_id=?", (observation_item_id,)).fetchone()
            if old is not None:
                if (old["frame_index"], old["frame_sha256"], old["frame_bytes"]) != (index, sha256, size):
                    raise OutboxError("MEDIA_CONFLICT")
                return old["id"]
            media_id = str(uuid4())
            conn.execute("""INSERT INTO cloud_media_items(id,observation_item_id,binding_id,source_event_id,
                entity_kind,entity_id,kind,content_type,frame_index,frame_sha256,frame_bytes,deadline_ms,
                created_ms,state,next_ms) VALUES(?,?,?,?,?,?,'OVERVIEW','image/jpeg',?,?,?,?,?,'PENDING_MANIFEST',?)""",
                (media_id, observation_item_id, ref.id, parent["source_event_id"], parent["entity_kind"],
                 parent["entity_id"], index, sha256, size, parent["deadline_ms"], stamp, stamp))
            return media_id

    @staticmethod
    def _sync_parent(conn, binding_id, now):
        conn.execute("""UPDATE cloud_media_items SET state='WITHDRAWN',terminal_ms=coalesce(cloud_media_items.terminal_ms,?),
            error_code='PARENT_WITHDRAWN',lease_token=NULL,lease_until_ms=NULL,claim_generation=NULL
            FROM cloud_sync_items p WHERE cloud_media_items.observation_item_id=p.id
                AND cloud_media_items.binding_id=? AND cloud_media_items.state!='WITHDRAWN' AND p.state='WITHDRAWN'""",
            (now, binding_id))
        conn.execute("""UPDATE cloud_media_items SET state=CASE WHEN p.withdrawal_reason='LOCAL_EXPIRED' THEN 'EXPIRED'
                ELSE 'CANCELLED' END,
                terminal_ms=coalesce(cloud_media_items.terminal_ms,?), error_code='PARENT_WITHDRAWN',
                lease_token=NULL,lease_until_ms=NULL,claim_generation=NULL
            FROM cloud_sync_items p WHERE cloud_media_items.observation_item_id=p.id
                AND cloud_media_items.binding_id=? AND cloud_media_items.state NOT IN ('READY','WITHDRAWN','BLOCKED')
                AND (p.operation='WITHDRAWAL' OR p.state IN ('CANCELLED','EXPIRED','BLOCKED'))""",
            (now, binding_id))

    def claim(self, conn, scope, ref, *, now, lease_seconds=20):
        with _operation(conn):
            stamp = _stamp(now)
            if type(lease_seconds) is not int or not 1 <= lease_seconds <= 60:
                raise OutboxError("INVALID_LEASE")
            binding = conn.execute("""SELECT state,generation FROM cloud_sync_bindings WHERE id=? AND installation_id=?
                AND organisation_id=? AND site_id=?""", (ref.id, scope.installation_id,
                scope.organisation_id, scope.site_id)).fetchone()
            if binding is None or binding["generation"] != ref.generation or binding["state"] not in {"ACTIVE", "PAUSED"}:
                return None
            self._sync_parent(conn, ref.id, stamp)
            conn.execute("""UPDATE cloud_media_items SET state=CASE state WHEN 'LEASED_MANIFEST' THEN 'PENDING_MANIFEST'
                ELSE 'PENDING_CONTENT' END,lease_token=NULL,lease_until_ms=NULL,claim_generation=NULL
                WHERE binding_id=? AND state IN ('LEASED_MANIFEST','LEASED_CONTENT') AND lease_until_ms<=?""", (ref.id, stamp))
            held = conn.execute("SELECT 1 FROM cloud_sync_capacity WHERE installation_id=? AND until_ms>?",
                                (scope.installation_id, stamp)).fetchone()
            if held:
                return None
            row = conn.execute("""SELECT m.* FROM cloud_media_items m JOIN cloud_sync_items p ON p.id=m.observation_item_id
                WHERE m.binding_id=? AND m.state IN ('PENDING_MANIFEST','PENDING_CONTENT') AND m.next_ms<=?
                AND m.deadline_ms>? AND p.operation='OBSERVATION' AND p.state='RECEIVED'
                ORDER BY m.created_ms,m.id LIMIT 1""", (ref.id, stamp, stamp)).fetchone()
            if row is None or binding["state"] != "ACTIVE":
                return None
            token, until = str(uuid4()), min(stamp + lease_seconds * 1000, row["deadline_ms"])
            operation = "MEDIA_MANIFEST" if row["state"] == "PENDING_MANIFEST" else "MEDIA_CONTENT"
            leased = "LEASED_MANIFEST" if operation == "MEDIA_MANIFEST" else "LEASED_CONTENT"
            conn.execute("""UPDATE cloud_media_items SET state=?,lease_token=?,lease_until_ms=?,claim_generation=?,
                ever_attempted=1,attempts=min(attempts+1,2147483647) WHERE id=?""",
                (leased, token, until, ref.generation, row["id"]))
            conn.execute("""INSERT INTO cloud_sync_capacity VALUES(?,?,?) ON CONFLICT(installation_id)
                DO UPDATE SET token=excluded.token,until_ms=excluded.until_ms""", (scope.installation_id, token, until))
            return MediaClaim(row["id"], ref, token, operation, row["source_event_id"], row["entity_kind"],
                row["entity_id"], row["kind"], row["content_type"], row["frame_index"], row["frame_sha256"],
                row["frame_bytes"], row["evidence_id"], datetime.fromtimestamp(until/1000, timezone.utc))

    def reconcile(self, conn, scope, ref, *, now):
        """Mirror parent withdrawal and remove only old settled references."""
        with _operation(conn):
            stamp = _stamp(now)
            if conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='cloud_media_items'").fetchone() is None:
                return 0
            self._sync_parent(conn, ref.id, stamp)
            # Parent observations are retained for 31 days. Remove settled media
            # one day earlier so its foreign key can never block parent pruning.
            return conn.execute("""DELETE FROM cloud_media_items WHERE binding_id=? AND terminal_ms<=?
                AND state IN ('CANCELLED','EXPIRED','WITHDRAWN')""", (ref.id, stamp - 30 * 86_400_000)).rowcount

    def _lease(self, conn, ref, claim, now):
        if not isinstance(claim, MediaClaim) or claim.binding != ref:
            raise OutboxError("STALE_LEASE")
        row = conn.execute("SELECT * FROM cloud_media_items WHERE id=? AND binding_id=?", (claim.id, ref.id)).fetchone()
        expected = "LEASED_MANIFEST" if claim.operation == "MEDIA_MANIFEST" else "LEASED_CONTENT"
        if (row is None or row["state"] != expected or row["lease_token"] != claim.token
                or row["claim_generation"] != ref.generation or row["lease_until_ms"] <= now):
            raise OutboxError("STALE_LEASE")
        return row

    def retry(self, conn, scope, ref, claim, *, now, delay_seconds):
        with _operation(conn):
            stamp = _stamp(now)
            self._lease(conn, ref, claim, stamp)
            if type(delay_seconds) is not int or not 1 <= delay_seconds <= 3600:
                raise OutboxError("INVALID_BACKOFF")
            pending = "PENDING_MANIFEST" if claim.operation == "MEDIA_MANIFEST" else "PENDING_CONTENT"
            conn.execute("UPDATE cloud_media_items SET state=?,next_ms=?,lease_token=NULL,lease_until_ms=NULL,claim_generation=NULL,error_code='RETRY' WHERE id=?",
                         (pending, stamp + delay_seconds*1000, claim.id))
            conn.execute("DELETE FROM cloud_sync_capacity WHERE installation_id=? AND token=?", (scope.installation_id, claim.token))

    def acknowledge_manifest(self, conn, scope, ref, claim, *, evidence_id, upload_required, state, now):
        with _operation(conn):
            stamp = _stamp(now)
            row = self._lease(conn, ref, claim, stamp)
            _uuid(evidence_id)
            if claim.operation != "MEDIA_MANIFEST" or type(upload_required) is not bool or state not in {"PENDING", "READY"}:
                raise OutboxError("INVALID_RECEIPT")
            if row["evidence_id"] is not None and row["evidence_id"] != evidence_id:
                raise OutboxError("RECEIPT_CONFLICT")
            if (upload_required and state != "PENDING") or (not upload_required and state != "READY"):
                raise OutboxError("INVALID_RECEIPT")
            local = "PENDING_CONTENT" if upload_required else "READY"
            conn.execute("""UPDATE cloud_media_items SET state=?,evidence_id=?,next_ms=?,settled_token=?,
                terminal_ms=?,lease_token=NULL,lease_until_ms=NULL,claim_generation=NULL,error_code=NULL WHERE id=?""",
                (local, evidence_id, stamp, claim.token, stamp if local == "READY" else None, claim.id))
            conn.execute("DELETE FROM cloud_sync_capacity WHERE installation_id=? AND token=?", (scope.installation_id, claim.token))

    def acknowledge_content(self, conn, scope, ref, claim, *, evidence_id, state, now):
        with _operation(conn):
            stamp = _stamp(now)
            row = self._lease(conn, ref, claim, stamp)
            if claim.operation != "MEDIA_CONTENT" or state != "READY" or row["evidence_id"] != evidence_id:
                raise OutboxError("INVALID_RECEIPT")
            conn.execute("""UPDATE cloud_media_items SET state='READY',settled_token=?,terminal_ms=?,
                lease_token=NULL,lease_until_ms=NULL,claim_generation=NULL,error_code=NULL WHERE id=?""",
                (claim.token, stamp, claim.id))
            conn.execute("DELETE FROM cloud_sync_capacity WHERE installation_id=? AND token=?", (scope.installation_id, claim.token))

    def reject(self, conn, scope, ref, claim, *, code, now):
        with _operation(conn):
            stamp = _stamp(now)
            self._lease(conn, ref, claim, stamp)
            conn.execute("""UPDATE cloud_media_items SET state='BLOCKED',error_code=?,terminal_ms=?,
                lease_token=NULL,lease_until_ms=NULL,claim_generation=NULL WHERE id=?""", (code, stamp, claim.id))
            conn.execute("DELETE FROM cloud_sync_capacity WHERE installation_id=? AND token=?", (scope.installation_id, claim.token))
