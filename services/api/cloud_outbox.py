"""Inactive transactional storage foundation for metadata-only cloud delivery.

Every public operation requires a caller-owned SQLite transaction and trusted
scope. This module does not authenticate callers, install itself, access keys
on disk, send HTTP, inspect entities, or claim that monitoring is running.
"""

from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import hmac
import json
import math
import os
import re
import sqlite3
from types import MappingProxyType
from typing import Mapping
from urllib.parse import urlsplit
from uuid import UUID, uuid4

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from .cloud_observation import MappedObservation, canonical_payload, source_event_id, validate_payload

SCHEMA_VERSION = 1
DAY_MS = 86_400_000
MAGIC = b"ASOUTBOX1\x00"
WITHDRAWAL_RESERVE = 256
SOURCE_KINDS = {"interaction", "live_event"}
WITHDRAWAL_REASONS = {"LOCAL_DELETED", "LOCAL_EXPIRED", "LOCAL_EXPORT_REMOVED"}
TERMINAL_CODES = {"REMOTE_REJECTED", "REMOTE_CONFLICT", "LOCAL_CORRUPTION"}


class OutboxError(ValueError):
    """Bounded code only; never include payload, target, key or driver text."""

    def __init__(self, code):
        self.code = code
        super().__init__(code)


def _uuid(value):
    try:
        if not isinstance(value, str) or str(UUID(value)) != value or UUID(value).int == 0:
            raise ValueError
    except (ValueError, TypeError, AttributeError):
        raise OutboxError("INVALID_IDENTIFIER") from None
    return value


def _scope_id(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", value):
        raise OutboxError("INVALID_SCOPE")
    return value


def _stamp(value):
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise OutboxError("INVALID_CLOCK")
    stamp = value.timestamp() * 1000
    if not math.isfinite(stamp) or not 0 <= stamp <= 253402300799000:
        raise OutboxError("INVALID_CLOCK")
    return int(stamp)


def _date(value):
    return datetime.fromtimestamp(value / 1000, timezone.utc)


def _iso(value):
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


@dataclass(frozen=True)
class Scope:
    installation_id: str
    organisation_id: str
    site_id: str

    def __post_init__(self):
        _uuid(self.installation_id)
        _scope_id(self.organisation_id)
        _scope_id(self.site_id)


@dataclass(frozen=True)
class BindingRef:
    id: str
    generation: int

    def __post_init__(self):
        _uuid(self.id)
        if type(self.generation) is not int or not 1 <= self.generation <= 2_147_483_647:
            raise OutboxError("INVALID_GENERATION")


@dataclass(frozen=True)
class Target:
    origin: str
    organisation_id: str
    pharmacy_id: str
    device_id: str
    credential_handle: str

    def __post_init__(self):
        for value in (self.organisation_id, self.pharmacy_id, self.device_id, self.credential_handle):
            _uuid(value)
        try:
            url = urlsplit(self.origin)
            if (not isinstance(self.origin, str) or len(self.origin) > 2048
                    or any(c.isspace() or ord(c) < 32 or ord(c) == 127 for c in self.origin)
                    or url.scheme != "https" or not url.hostname or url.username or url.password
                    or url.path or url.query or url.fragment or url.port == 0):
                raise ValueError
            if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9.-]*", url.hostname):
                raise ValueError
        except (TypeError, ValueError, AttributeError):
            raise OutboxError("INVALID_TARGET") from None


@dataclass(frozen=True)
class Binding:
    ref: BindingRef
    state: str
    activated_at: datetime
    target: Target


@dataclass(frozen=True)
class Claim:
    id: str
    binding: BindingRef
    token: str
    operation: str
    source_event_id: str
    payload: Mapping
    lease_until: datetime
    admitted_at: datetime
    deadline: datetime


@dataclass(frozen=True)
class Limits:
    pending_per_binding: int = 500
    pending_per_installation: int = 1000
    records_per_binding: int = 20000
    records_per_installation: int = 40000
    bytes_per_binding: int = 1024 * 1024
    bytes_per_installation: int = 2 * 1024 * 1024
    daily_per_binding: int = 500
    bindings_per_installation: int = 32

    def __post_init__(self):
        for value in self.__dict__.values():
            if type(value) is not int or not 1 <= value <= 100_000_000:
                raise OutboxError("INVALID_LIMIT")


@contextmanager
def _operation(conn):
    if not isinstance(conn, sqlite3.Connection) or not conn.in_transaction:
        raise OutboxError("TRANSACTION_REQUIRED")
    savepoint = "outbox_" + uuid4().hex
    conn.execute("SAVEPOINT " + savepoint)
    try:
        yield
    except BaseException:
        conn.execute("ROLLBACK TO " + savepoint)
        raise
    finally:
        conn.execute("RELEASE " + savepoint)


def _rows(conn, query, parameters=()):
    cursor = conn.execute(query, parameters)
    names = [column[0] for column in cursor.description]
    return [dict(zip(names, row)) for row in cursor.fetchall()]


def install_schema(conn):
    """Add only our tables inside the caller's transaction; no PRAGMA/commit."""
    with _operation(conn):
        exists = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='cloud_sync_schema'").fetchone()
        if exists:
            rows = conn.execute("SELECT version FROM cloud_sync_schema").fetchall()
            if len(rows) != 1 or rows[0][0] != SCHEMA_VERSION:
                raise OutboxError("SCHEMA_INCOMPATIBLE")
        statements = [
            "CREATE TABLE IF NOT EXISTS cloud_sync_schema(version INTEGER NOT NULL)",
            """CREATE TABLE IF NOT EXISTS cloud_sync_bindings(
                id TEXT PRIMARY KEY, installation_id TEXT NOT NULL, organisation_id TEXT NOT NULL,
                site_id TEXT NOT NULL, generation INTEGER NOT NULL, origin TEXT NOT NULL,
                cloud_organisation_id TEXT NOT NULL, pharmacy_id TEXT NOT NULL, device_id TEXT NOT NULL,
                credential_handle TEXT NOT NULL, key_hash TEXT NOT NULL, state TEXT NOT NULL
                CHECK(state IN ('ACTIVE','PAUSED','DISCONNECTED','RESTORED')),
                created_ms INTEGER NOT NULL, activated_ms INTEGER NOT NULL, clock_ms INTEGER NOT NULL,
                UNIQUE(origin,device_id))""",
            """CREATE UNIQUE INDEX IF NOT EXISTS cloud_sync_one_binding
                ON cloud_sync_bindings(installation_id) WHERE state IN ('ACTIVE','PAUSED')""",
            """CREATE TABLE IF NOT EXISTS cloud_sync_items(
                id TEXT PRIMARY KEY, binding_id TEXT NOT NULL REFERENCES cloud_sync_bindings(id),
                entity_kind TEXT NOT NULL, entity_id TEXT NOT NULL, source_event_id TEXT NOT NULL,
                observation_hash TEXT NOT NULL, admitted_ms INTEGER NOT NULL, deadline_ms INTEGER NOT NULL,
                admitted_iso TEXT NOT NULL, deadline_iso TEXT NOT NULL,
                created_ms INTEGER NOT NULL, operation TEXT NOT NULL CHECK(operation IN ('OBSERVATION','WITHDRAWAL')),
                state TEXT NOT NULL CHECK(state IN ('PENDING','LEASED','RECEIVED','WITHDRAWN','EXPIRED','CANCELLED','BLOCKED')),
                payload BLOB, payload_hash TEXT NOT NULL, attempts INTEGER NOT NULL DEFAULT 0,
                ever_attempted INTEGER NOT NULL DEFAULT 0, next_ms INTEGER NOT NULL,
                lease_token TEXT, lease_until_ms INTEGER, claim_generation INTEGER,
                receipt_id TEXT, observation_receipt_id TEXT, receipt_ms INTEGER, settled_token TEXT, terminal_ms INTEGER,
                withdrawal_reason TEXT, error_code TEXT,
                UNIQUE(binding_id,entity_kind,entity_id), UNIQUE(binding_id,source_event_id))""",
            "CREATE INDEX IF NOT EXISTS cloud_sync_due ON cloud_sync_items(binding_id,state,next_ms)",
            """CREATE TABLE IF NOT EXISTS cloud_sync_capacity(
                installation_id TEXT PRIMARY KEY, token TEXT NOT NULL, until_ms INTEGER NOT NULL)""",
            """CREATE TRIGGER IF NOT EXISTS cloud_sync_binding_immutable
                BEFORE UPDATE OF id,installation_id,organisation_id,site_id,origin,cloud_organisation_id,
                    pharmacy_id,device_id,credential_handle,key_hash,created_ms ON cloud_sync_bindings
                BEGIN SELECT RAISE(ABORT,'immutable cloud binding'); END""",
            """CREATE TRIGGER IF NOT EXISTS cloud_sync_item_immutable
                BEFORE UPDATE OF id,binding_id,entity_kind,entity_id,source_event_id,observation_hash,
                    admitted_ms,deadline_ms,admitted_iso,deadline_iso,created_ms ON cloud_sync_items
                BEGIN SELECT RAISE(ABORT,'immutable cloud observation'); END""",
        ]
        for statement in statements:
            conn.execute(statement)
        if not exists:
            conn.execute("INSERT INTO cloud_sync_schema VALUES(?)", (SCHEMA_VERSION,))


class Outbox:
    def __init__(self, key: bytes, limits: Limits = Limits()):
        if type(key) is not bytes or len(key) != 32:
            raise OutboxError("KEY_UNAVAILABLE")
        self._cipher = AESGCM(key)
        self._key_hash = hashlib.sha256(key).hexdigest()
        self.limits = limits

    def _binding(self, conn, scope, ref, now):
        if not isinstance(scope, Scope) or not isinstance(ref, BindingRef):
            raise OutboxError("INVALID_SCOPE")
        rows = _rows(conn, """SELECT * FROM cloud_sync_bindings WHERE id=? AND installation_id=?
            AND organisation_id=? AND site_id=?""", (ref.id, scope.installation_id, scope.organisation_id, scope.site_id))
        if not rows:
            raise OutboxError("NOT_FOUND")
        row = rows[0]
        if row["generation"] != ref.generation:
            raise OutboxError("STALE_GENERATION")
        if not hmac.compare_digest(row["key_hash"], self._key_hash):
            raise OutboxError("KEY_UNAVAILABLE")
        if now < row["clock_ms"]:
            raise OutboxError("CLOCK_ROLLBACK")
        conn.execute("UPDATE cloud_sync_bindings SET clock_ms=? WHERE id=?", (now, ref.id))
        return row

    @staticmethod
    def _view(row):
        return Binding(BindingRef(row["id"], row["generation"]), row["state"], _date(row["activated_ms"]),
                       Target(row["origin"], row["cloud_organisation_id"], row["pharmacy_id"], row["device_id"], row["credential_handle"]))

    @staticmethod
    def _aad(scope, binding_id, item_id, operation, timing):
        return json.dumps(["aislesignals-cloud-outbox", SCHEMA_VERSION, scope.installation_id,
                           scope.organisation_id, scope.site_id, binding_id, item_id, operation, *timing], separators=(",", ":")).encode()

    def _encrypt(self, scope, binding_id, item_id, operation, raw, timing):
        if len(raw) > 2048:
            raise OutboxError("PAYLOAD_LIMIT")
        nonce = os.urandom(12)
        return MAGIC + nonce + self._cipher.encrypt(nonce, raw, self._aad(scope, binding_id, item_id, operation, timing))

    @staticmethod
    def _timing(row):
        return (row["admitted_ms"], row["deadline_ms"], row["admitted_iso"], row["deadline_iso"])

    def _decode(self, scope, ref, row):
        encrypted = row["payload"]
        try:
            if not isinstance(encrypted, bytes) or not encrypted.startswith(MAGIC):
                raise ValueError
            raw = self._cipher.decrypt(encrypted[len(MAGIC):len(MAGIC)+12], encrypted[len(MAGIC)+12:],
                                       self._aad(scope, ref.id, row["id"], row["operation"], self._timing(row)))
            if not hmac.compare_digest(hashlib.sha256(raw).hexdigest(), row["payload_hash"]):
                raise ValueError
            value = json.loads(raw)
            if row["operation"] == "OBSERVATION":
                value = validate_payload(value)
            elif (set(value) != {"source_event_id", "reason"} or value["reason"] not in WITHDRAWAL_REASONS):
                raise ValueError
            if value["source_event_id"] != row["source_event_id"]:
                raise ValueError
            return MappingProxyType(dict(value))
        except (ValueError, TypeError, KeyError, InvalidTag):
            raise OutboxError("CIPHERTEXT_INVALID") from None

    def create_binding(self, conn, scope, *, binding_id, expected_generation, target, now):
        with _operation(conn):
            if not isinstance(scope, Scope) or not isinstance(target, Target) or type(expected_generation) is not int or expected_generation != 0:
                raise OutboxError("INVALID_BINDING")
            _uuid(binding_id)
            stamp = _stamp(now)
            existing = _rows(conn, "SELECT * FROM cloud_sync_bindings WHERE id=?", (binding_id,))
            if existing:
                raise OutboxError("BINDING_EXISTS")
            count = conn.execute("SELECT count(*) FROM cloud_sync_bindings WHERE installation_id=?", (scope.installation_id,)).fetchone()[0]
            if count >= self.limits.bindings_per_installation:
                raise OutboxError("BINDING_LIMIT")
            try:
                conn.execute("""INSERT INTO cloud_sync_bindings VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                             (binding_id, scope.installation_id, scope.organisation_id, scope.site_id, 1, target.origin,
                              target.organisation_id, target.pharmacy_id, target.device_id, target.credential_handle,
                              self._key_hash, "PAUSED", stamp, stamp, stamp))
            except sqlite3.IntegrityError:
                raise OutboxError("BINDING_CONFLICT") from None
            return self._view(self._binding(conn, scope, BindingRef(binding_id, 1), stamp))

    def set_paused(self, conn, scope, ref, *, paused, now):
        with _operation(conn):
            stamp = _stamp(now)
            binding = self._binding(conn, scope, ref, stamp)
            if type(paused) is not bool or binding["state"] not in {"ACTIVE", "PAUSED"}:
                raise OutboxError("BINDING_DISCONNECTED")
            state = "PAUSED" if paused else "ACTIVE"
            if state == binding["state"]:
                return self._view(binding)
            if ref.generation == 2_147_483_647:
                raise OutboxError("GENERATION_EXHAUSTED")
            conn.execute("UPDATE cloud_sync_bindings SET state=?,generation=generation+1,activated_ms=? WHERE id=?",
                         (state, stamp if not paused else binding["activated_ms"], ref.id))
            self._release(conn, ref.id)
            return self._view(self._binding(conn, scope, BindingRef(ref.id, ref.generation + 1), stamp))

    @staticmethod
    def _release(conn, binding_id):
        conn.execute("""UPDATE cloud_sync_items SET state='PENDING',lease_token=NULL,lease_until_ms=NULL,
            claim_generation=NULL WHERE binding_id=? AND state='LEASED'""", (binding_id,))

    def enqueue(self, conn, scope, ref, observation: MappedObservation, *, now):
        with _operation(conn):
            stamp = _stamp(now)
            binding = self._binding(conn, scope, ref, stamp)
            if binding["state"] != "ACTIVE":
                raise OutboxError("BINDING_NOT_ACTIVE")
            if not isinstance(observation, MappedObservation) or observation.entity_kind not in SOURCE_KINDS:
                raise OutboxError("INVALID_OBSERVATION")
            expected = source_event_id(installation_id=scope.installation_id, binding_id=ref.id,
                                       organisation_id=scope.organisation_id, site_id=scope.site_id,
                                       entity_kind=observation.entity_kind, entity_id=observation.entity_id)
            try:
                payload = validate_payload(observation.payload)
                raw = canonical_payload(payload)
            except (ValueError, TypeError, KeyError):
                raise OutboxError("INVALID_PAYLOAD") from None
            if payload["source_event_id"] != expected:
                raise OutboxError("SOURCE_ID_MISMATCH")
            admitted, deadline = _stamp(observation.admitted_at), _stamp(observation.deadline)
            if not admitted < deadline <= admitted + DAY_MS or admitted > stamp:
                raise OutboxError("INVALID_DEADLINE")
            admitted_iso, deadline_iso = _iso(observation.admitted_at), _iso(observation.deadline)
            if payload["occurred_at"] != admitted_iso:
                raise OutboxError("ADMISSION_TIME_MISMATCH")
            observation_hash = hashlib.sha256(raw + b"\n" + deadline_iso.encode()).hexdigest()
            old = _rows(conn, "SELECT * FROM cloud_sync_items WHERE binding_id=? AND entity_kind=? AND entity_id=?",
                        (ref.id, observation.entity_kind, observation.entity_id))
            if old:
                if not hmac.compare_digest(old[0]["observation_hash"], observation_hash) or old[0]["admitted_ms"] != admitted:
                    raise OutboxError("OBSERVATION_CONFLICT")
                return old[0]["id"]
            if admitted < binding["activated_ms"]:
                raise OutboxError("STALE_ADMISSION")
            if stamp >= deadline:
                raise OutboxError("OBSERVATION_EXPIRED")
            item_id = str(uuid4())
            encrypted = self._encrypt(scope, ref.id, item_id, "OBSERVATION", raw, (admitted, deadline, admitted_iso, deadline_iso))
            self._quota(conn, scope, ref, stamp, len(encrypted))
            conn.execute("""INSERT INTO cloud_sync_items(id,binding_id,entity_kind,entity_id,source_event_id,
                observation_hash,admitted_ms,deadline_ms,admitted_iso,deadline_iso,created_ms,operation,state,payload,payload_hash,next_ms)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,'OBSERVATION','PENDING',?,?,?)""",
                         (item_id, ref.id, observation.entity_kind, observation.entity_id, expected, observation_hash,
                          admitted, deadline, admitted_iso, deadline_iso, stamp, encrypted, hashlib.sha256(raw).hexdigest(), stamp))
            return item_id

    def _quota(self, conn, scope, ref, now, size):
        for selected, identifier, pending_limit, record_limit, byte_limit in (
            ("b.id", ref.id, self.limits.pending_per_binding, self.limits.records_per_binding, self.limits.bytes_per_binding),
            ("b.installation_id", scope.installation_id, self.limits.pending_per_installation,
             self.limits.records_per_installation, self.limits.bytes_per_installation),
        ):
            row = _rows(conn, f"""SELECT count(*) AS records,coalesce(sum(CASE
                WHEN (i.state IN ('CANCELLED','EXPIRED') AND i.ever_attempted=0)
                    OR (i.state='WITHDRAWN' AND i.receipt_id IS NOT NULL) THEN 0
                ELSE max(coalesce(length(i.payload),0),?) END),0) AS bytes,
                coalesce(sum(i.operation='OBSERVATION' AND i.state IN ('PENDING','LEASED','BLOCKED')),0) AS pending
                FROM cloud_sync_items i JOIN cloud_sync_bindings b ON b.id=i.binding_id WHERE {selected}=?""",
                        (WITHDRAWAL_RESERVE, identifier))[0]
            if row["records"] >= record_limit or row["pending"] >= pending_limit or row["bytes"] + max(size, WITHDRAWAL_RESERVE) > byte_limit:
                raise OutboxError("QUEUE_LIMIT")
        daily = conn.execute("SELECT count(*) FROM cloud_sync_items WHERE binding_id=? AND created_ms>?", (ref.id, now - DAY_MS)).fetchone()[0]
        if daily >= self.limits.daily_per_binding:
            raise OutboxError("DAILY_LIMIT")

    def _withdraw(self, conn, scope, ref, row, reason, now):
        if row["operation"] == "WITHDRAWAL":
            return
        if not row["ever_attempted"]:
            conn.execute("""UPDATE cloud_sync_items SET state=?,payload=NULL,lease_token=NULL,lease_until_ms=NULL,
                claim_generation=NULL,terminal_ms=? WHERE id=?""", ("EXPIRED" if reason == "LOCAL_EXPIRED" else "CANCELLED", now, row["id"]))
            return
        raw = json.dumps({"source_event_id": row["source_event_id"], "reason": reason}, sort_keys=True, separators=(",", ":")).encode()
        encrypted = self._encrypt(scope, ref.id, row["id"], "WITHDRAWAL", raw, self._timing(row))
        if len(encrypted) > WITHDRAWAL_RESERVE:
            raise OutboxError("WITHDRAWAL_RESERVE_INVALID")
        conn.execute("""UPDATE cloud_sync_items SET operation='WITHDRAWAL',state='PENDING',payload=?,payload_hash=?,
            next_ms=?,withdrawal_reason=?,lease_token=NULL,lease_until_ms=NULL,claim_generation=NULL,
            settled_token=NULL,terminal_ms=NULL WHERE id=?""",
                     (encrypted, hashlib.sha256(raw).hexdigest(), now, reason, row["id"]))

    def withdraw(self, conn, scope, ref, item_id, *, reason, now):
        with _operation(conn):
            stamp = _stamp(now)
            self._binding(conn, scope, ref, stamp)
            if reason not in WITHDRAWAL_REASONS:
                raise OutboxError("INVALID_REASON")
            row = self._item(conn, ref, item_id)
            self._withdraw(conn, scope, ref, row, reason, stamp)

    def _expire(self, conn, scope, ref, now):
        rows = _rows(conn, """SELECT * FROM cloud_sync_items WHERE binding_id=? AND operation='OBSERVATION'
            AND deadline_ms<=? AND state NOT IN ('EXPIRED','CANCELLED')""", (ref.id, now))
        for row in rows:
            self._withdraw(conn, scope, ref, row, "LOCAL_EXPIRED", now)

    def expire(self, conn, scope, ref, *, now):
        with _operation(conn):
            stamp = _stamp(now)
            self._binding(conn, scope, ref, stamp)
            self._expire(conn, scope, ref, stamp)

    def disconnect(self, conn, scope, ref, *, now, restored=False):
        with _operation(conn):
            stamp = _stamp(now)
            binding = self._binding(conn, scope, ref, stamp)
            if type(restored) is not bool:
                raise OutboxError("INVALID_STATE")
            if binding["state"] in {"DISCONNECTED", "RESTORED"}:
                return self._view(binding)
            if ref.generation == 2_147_483_647:
                raise OutboxError("GENERATION_EXHAUSTED")
            for row in _rows(conn, "SELECT * FROM cloud_sync_items WHERE binding_id=?", (ref.id,)):
                self._withdraw(conn, scope, ref, row, "LOCAL_EXPORT_REMOVED", stamp)
            self._release(conn, ref.id)
            conn.execute("UPDATE cloud_sync_bindings SET state=?,generation=generation+1 WHERE id=?",
                         ("RESTORED" if restored else "DISCONNECTED", ref.id))
            return self._view(self._binding(conn, scope, BindingRef(ref.id, ref.generation + 1), stamp))

    @staticmethod
    def _item(conn, ref, item_id):
        _uuid(item_id)
        rows = _rows(conn, "SELECT * FROM cloud_sync_items WHERE id=? AND binding_id=?", (item_id, ref.id))
        if not rows:
            raise OutboxError("NOT_FOUND")
        return rows[0]

    def claim(self, conn, scope, ref, *, now, lease_seconds=15):
        with _operation(conn):
            stamp = _stamp(now)
            binding = self._binding(conn, scope, ref, stamp)
            if type(lease_seconds) is not int or not 1 <= lease_seconds <= 60:
                raise OutboxError("INVALID_LEASE")
            if binding["state"] not in {"ACTIVE", "PAUSED"}:
                return None
            self._expire(conn, scope, ref, stamp)
            conn.execute("""UPDATE cloud_sync_items SET state='PENDING',lease_token=NULL,lease_until_ms=NULL,
                claim_generation=NULL WHERE binding_id=? AND state='LEASED' AND lease_until_ms<=?""", (ref.id, stamp))
            # Cancellation changes item state, but cannot prove an old HTTP
            # request ended. Retain this separate capacity reservation until
            # settlement or its bounded lease deadline, even across bindings.
            held = conn.execute("SELECT 1 FROM cloud_sync_capacity WHERE installation_id=? AND until_ms>?",
                                (scope.installation_id, stamp)).fetchone()
            if held:
                return None
            rows = _rows(conn, """SELECT * FROM cloud_sync_items WHERE binding_id=? AND state='PENDING'
                AND next_ms<=? AND (operation='WITHDRAWAL' OR ?='ACTIVE')
                ORDER BY operation='WITHDRAWAL' DESC,created_ms,id LIMIT 1""", (ref.id, stamp, binding["state"]))
            if not rows:
                return None
            row = rows[0]
            payload = self._decode(scope, ref, row)
            token = str(uuid4())
            until = stamp + lease_seconds * 1000
            if row["operation"] == "OBSERVATION":
                until = min(until, row["deadline_ms"])
            conn.execute("""UPDATE cloud_sync_items SET state='LEASED',lease_token=?,lease_until_ms=?,
                claim_generation=?,ever_attempted=1,attempts=min(attempts+1,2147483647) WHERE id=?""",
                         (token, until, ref.generation, row["id"]))
            conn.execute("""INSERT INTO cloud_sync_capacity VALUES(?,?,?) ON CONFLICT(installation_id)
                DO UPDATE SET token=excluded.token,until_ms=excluded.until_ms""", (scope.installation_id, token, until))
            return Claim(row["id"], ref, token, row["operation"], row["source_event_id"], payload, _date(until),
                         datetime.fromisoformat(row["admitted_iso"].replace("Z", "+00:00")),
                         datetime.fromisoformat(row["deadline_iso"].replace("Z", "+00:00")))

    def _lease(self, conn, scope, ref, claim, now):
        binding = self._binding(conn, scope, ref, now)
        if not isinstance(claim, Claim) or claim.binding != ref or binding["state"] not in {"ACTIVE", "PAUSED"}:
            raise OutboxError("STALE_LEASE")
        row = self._item(conn, ref, claim.id)
        if (row["state"] != "LEASED" or row["lease_token"] != claim.token or row["claim_generation"] != ref.generation
                or row["lease_until_ms"] <= now or row["operation"] != claim.operation):
            raise OutboxError("STALE_LEASE")
        return row

    def retry(self, conn, scope, ref, claim, *, now, delay_seconds):
        with _operation(conn):
            stamp = _stamp(now)
            self._lease(conn, scope, ref, claim, stamp)
            if type(delay_seconds) is not int or not 1 <= delay_seconds <= 3600:
                raise OutboxError("INVALID_BACKOFF")
            conn.execute("""UPDATE cloud_sync_items SET state='PENDING',next_ms=?,lease_token=NULL,
                lease_until_ms=NULL,claim_generation=NULL,error_code='RETRY' WHERE id=?""", (stamp + delay_seconds * 1000, claim.id))
            conn.execute("DELETE FROM cloud_sync_capacity WHERE installation_id=? AND token=?", (scope.installation_id, claim.token))

    def acknowledge(self, conn, scope, ref, claim, *, receipt_id, now):
        with _operation(conn):
            stamp = _stamp(now)
            _uuid(receipt_id)
            self._binding(conn, scope, ref, stamp)
            if not isinstance(claim, Claim) or claim.binding != ref:
                raise OutboxError("STALE_LEASE")
            row = self._item(conn, ref, claim.id)
            if row["state"] in {"RECEIVED", "WITHDRAWN"} and row["settled_token"] == claim.token:
                if row["receipt_id"] != receipt_id:
                    raise OutboxError("RECEIPT_CONFLICT")
                return
            self._lease(conn, scope, ref, claim, stamp)
            conn.execute("""UPDATE cloud_sync_items SET state=?,payload=NULL,receipt_id=?,receipt_ms=?,
                settled_token=?,terminal_ms=?,lease_token=NULL,lease_until_ms=NULL,claim_generation=NULL,error_code=NULL WHERE id=?""",
                         ("RECEIVED" if row["operation"] == "OBSERVATION" else "WITHDRAWN", receipt_id,
                          stamp, claim.token, stamp, claim.id))
            if row["operation"] == "OBSERVATION":
                conn.execute("UPDATE cloud_sync_items SET observation_receipt_id=? WHERE id=?", (receipt_id, claim.id))
            conn.execute("DELETE FROM cloud_sync_capacity WHERE installation_id=? AND token=?", (scope.installation_id, claim.token))

    def reject(self, conn, scope, ref, claim, *, code, now):
        """Stop replaying rejected payloads, retaining uncertain-send obligations."""
        with _operation(conn):
            stamp = _stamp(now)
            row = self._lease(conn, scope, ref, claim, stamp)
            if code not in TERMINAL_CODES:
                raise OutboxError("INVALID_ERROR_CODE")
            if row["operation"] == "OBSERVATION":
                self._withdraw(conn, scope, ref, row, "LOCAL_EXPORT_REMOVED", stamp)
                conn.execute("UPDATE cloud_sync_items SET error_code=? WHERE id=?", (code, row["id"]))
            else:
                conn.execute("""UPDATE cloud_sync_items SET state='BLOCKED',payload=NULL,error_code=?,
                    terminal_ms=?,lease_token=NULL,lease_until_ms=NULL,claim_generation=NULL WHERE id=?""", (code, stamp, row["id"]))
            conn.execute("DELETE FROM cloud_sync_capacity WHERE installation_id=? AND token=?", (scope.installation_id, claim.token))

    def prune_terminal(self, conn, scope, ref, *, now, limit=500):
        """Bounded 31-day cleanup; unresolved/uncertain obligations never qualify."""
        with _operation(conn):
            stamp = _stamp(now)
            self._binding(conn, scope, ref, stamp)
            if type(limit) is not int or not 1 <= limit <= 1000:
                raise OutboxError("INVALID_LIMIT")
            rows = conn.execute("""SELECT id FROM cloud_sync_items WHERE binding_id=? AND terminal_ms<=?
                AND ((state IN ('CANCELLED','EXPIRED') AND ever_attempted=0)
                    OR (state='WITHDRAWN' AND receipt_id IS NOT NULL))
                ORDER BY terminal_ms,id LIMIT ?""", (ref.id, stamp - 31 * DAY_MS, limit)).fetchall()
            for row in rows:
                conn.execute("DELETE FROM cloud_sync_items WHERE binding_id=? AND id=?", (ref.id, row[0]))
            return len(rows)

    def inspect(self, conn, scope, ref, *, now):
        """Bounded operational metadata only; no payload or credential value."""
        with _operation(conn):
            stamp = _stamp(now)
            binding = self._binding(conn, scope, ref, stamp)
            rows = _rows(conn, """SELECT id,source_event_id,operation,state,attempts,ever_attempted,
                deadline_ms,next_ms,receipt_id,observation_receipt_id,error_code,withdrawal_reason FROM cloud_sync_items
                WHERE binding_id=? ORDER BY created_ms DESC,id LIMIT 200""", (ref.id,))
            totals = _rows(conn, """SELECT count(*) AS total_records,
                coalesce(sum(operation='WITHDRAWAL' AND state!='WITHDRAWN'),0) AS obligations
                FROM cloud_sync_items WHERE binding_id=?""", (ref.id,))[0]
            return {"binding": self._view(binding), "items": rows,
                    "total_records": totals["total_records"], "withdrawal_obligations": totals["obligations"],
                    "monitoring_status": "UNKNOWN"}
