"""Explicit manager pairing and private provider for local metadata delivery.

Construction has no disk/network effects. Routes close the existing local auth
context before HTTP and authenticate again before committing a connection.
"""

from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
import base64
import hashlib
import json
import os
from pathlib import Path
import platform
import secrets
import sqlite3
import threading
import time
from uuid import uuid4

from fastapi import Request
from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator

from scripts import cloud_companion as private
from .cloud_outbox import BindingRef, Outbox, OutboxError, Scope, Target
from .cloud_transport import CloudTransport, CloudTransportError, canonical_id, safe_name, token_value
from .pilot_identity import PASSWORD_ITERATIONS
from .store import Store, digest, encode, password_hash

ATTEMPTS_KEY = "cloud_connection_attempts"
TTL = 300
MAX_ATTEMPTS = 32
PAUSE_CODES = frozenset({"ACCESS_REVOKED", "KEY_UNAVAILABLE", "CIPHERTEXT_INVALID", "CLOCK_ROLLBACK"})


class ConnectionUnavailable(Exception):
    def __init__(self, code="KEY_UNAVAILABLE"):
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class DeliveryContext:
    scope: Scope
    binding: BindingRef
    target: Target
    outbox: Outbox = field(repr=False)
    credential: str = field(repr=False)


class PrivateVault:
    """One immutable private JSON file per random handle, outside backup entries."""

    def __init__(self, database):
        self.directory = Path(str(Path(database).absolute()) + ".cloud-private")

    def path(self, handle):
        return self.directory / (canonical_id(handle) + ".json")

    def ensure(self, handle):
        try:
            private.ensure_new_destination(self.path(handle))
        except (OSError, ValueError):
            raise ConnectionUnavailable() from None

    def write(self, handle, value):
        try:
            private.write_new(self.path(handle), value)
        except (OSError, ValueError):
            raise ConnectionUnavailable() from None

    def read(self, handle):
        try:
            path = self.path(handle)
            native = private.windows_storage()
            if native is not None:
                raw = native.read_bytes(path, 4096)
            else:
                with private._private_parent(path) as (directory, name):
                    fd = private._open_private(directory, name, os.O_RDONLY)
                    with os.fdopen(fd, "rb") as stream:
                        raw = stream.read(4097)
            if len(raw) > 4096:
                raise ValueError
            def unique(pairs):
                value = {}
                for key, item in pairs:
                    if key in value:
                        raise ValueError
                    value[key] = item
                return value
            value = json.loads(raw, object_pairs_hook=unique)
            expected = {"version", "handle", "binding_id", "installation_id", "organisation_id", "site_id",
                        "origin", "device_id", "credential", "key"}
            if not isinstance(value, dict) or set(value) != expected or type(value["version"]) is not int or value["version"] != 1:
                raise ValueError
            for key in ("handle", "binding_id", "installation_id", "device_id"):
                canonical_id(value[key])
            if value["handle"] != handle or private.server_url(value["origin"]) != value["origin"]:
                raise ValueError
            Scope(value["installation_id"], value["organisation_id"], value["site_id"])
            token_value(value["credential"])
            key = base64.b64decode(value["key"] + "=", altchars=b"-_", validate=True)
            if len(key) != 32 or base64.urlsafe_b64encode(key).decode().rstrip("=") != value["key"]:
                raise ValueError
            return value, key
        except (OSError, ValueError, TypeError, KeyError, RecursionError):
            raise ConnectionUnavailable() from None


class CloudConnection:
    def __init__(self, store, *, transport_factory=CloudTransport, vault=None, clock=time.time):
        self.store = store
        self.transport_factory = transport_factory
        self.vault = vault or PrivateVault(store.path)
        self.clock = clock
        self.io_lock = threading.Lock()

    def stamp(self):
        return datetime.fromtimestamp(self.clock(), timezone.utc)

    def installation(self, conn):
        row = conn.execute("SELECT value FROM runtime_settings WHERE key='installation_id'").fetchone()
        try:
            return canonical_id(row[0])
        except (TypeError, ValueError):
            raise ConnectionUnavailable("INSTALLATION_UNAVAILABLE") from None

    def attempts(self, conn):
        row = conn.execute("SELECT value FROM runtime_settings WHERE key=?", (ATTEMPTS_KEY,)).fetchone()
        return json.loads(row[0]) if row else []

    def save_attempts(self, conn, values):
        conn.execute("INSERT INTO runtime_settings VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                     (ATTEMPTS_KEY, encode(values)))

    def fingerprint(self, conn):
        return digest(encode([tuple(row) for row in conn.execute(
            "SELECT id,generation,state FROM cloud_sync_bindings ORDER BY id")]))

    def material(self, record):
        value, key = self.vault.read(record["credential_handle"])
        checks = {"binding_id": record["id"], "installation_id": record["installation_id"],
                  "organisation_id": record["organisation_id"], "site_id": record["site_id"],
                  "origin": record["origin"], "device_id": record["device_id"]}
        if any(value[name] != expected for name, expected in checks.items()) or (
                "key_hash" in record and not secrets.compare_digest(hashlib.sha256(key).hexdigest(), record["key_hash"])):
            raise ConnectionUnavailable()
        return Outbox(key), value["credential"]

    def _read_context(self, conn, scope=None, *, paused=False):
        if not conn.in_transaction:
            raise ConnectionUnavailable("TRANSACTION_REQUIRED")
        if self.store.mode != "pilot":
            return None
        installation = self.installation(conn)
        if scope is not None and (not isinstance(scope, Scope) or scope.installation_id != installation):
            return None
        rows = conn.execute("SELECT * FROM cloud_sync_bindings WHERE installation_id=? AND state IN ('ACTIVE','PAUSED')", (installation,)).fetchall()
        for row in rows:
            record = dict(row)
            current = Scope(installation, record["organisation_id"], record["site_id"])
            if (not paused and record["state"] != "ACTIVE") or (scope is not None and current != scope):
                continue
            box, credential = self.material(record)
            return DeliveryContext(current, BindingRef(record["id"], record["generation"]),
                                   Target(record["origin"], record["cloud_organisation_id"], record["pharmacy_id"],
                                          record["device_id"], record["credential_handle"]), box, credential)
        return None

    def delivery_context(self, conn):
        return self._read_context(conn)

    def source_context(self, conn, scope):
        """Trusted local deletion hooks only; PAUSED material grants no sending."""
        if not isinstance(scope, Scope):
            raise ConnectionUnavailable("INVALID_CONTEXT")
        return self._read_context(conn, scope, paused=True)

    def pause_error(self, conn, ctx, *, code, now):
        if not conn.in_transaction or code not in PAUSE_CODES or not isinstance(ctx, DeliveryContext):
            raise ConnectionUnavailable("INVALID_CONTEXT")
        if self.store.mode != "pilot" or ctx.scope.installation_id != self.installation(conn):
            return False
        row = conn.execute("SELECT * FROM cloud_sync_bindings WHERE id=? AND installation_id=? AND organisation_id=? AND site_id=? AND generation=? AND state='ACTIVE'",
                           (ctx.binding.id, ctx.scope.installation_id, ctx.scope.organisation_id, ctx.scope.site_id, ctx.binding.generation)).fetchone()
        if not row or Outbox._view(dict(row)).target != ctx.target:
            return False
        self._pause_row(conn, dict(row))
        conn.execute("INSERT INTO runtime_settings VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", ("cloud_connection_error:" + ctx.binding.id, code))
        Store.audit(conn, {"organisation_id": ctx.scope.organisation_id, "site_id": ctx.scope.site_id, "name": "Cloud connection"},
                    "CLOUD_CONNECTION_PAUSED", "cloud_binding", ctx.binding.id, code)
        return True

    @staticmethod
    def _pause_row(conn, record):
        """An authority-checked stop never needs the missing encryption key."""
        if record["generation"] >= 2147483647:
            # Still close the sending gate if there is no safe next generation.
            conn.execute("UPDATE cloud_sync_bindings SET state='PAUSED' WHERE id=?", (record["id"],))
        else:
            conn.execute("UPDATE cloud_sync_bindings SET state='PAUSED',generation=generation+1 WHERE id=?", (record["id"],))
        conn.execute("UPDATE cloud_sync_items SET state='PENDING',lease_token=NULL,lease_until_ms=NULL,claim_generation=NULL WHERE binding_id=? AND state='LEASED'", (record["id"],))

    def access_revoked(self, conn, ctx, *, now):
        return self.pause_error(conn, ctx, code="ACCESS_REVOKED", now=now)


class ConnectionInput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    manager_password: SecretStr = Field(min_length=1, max_length=256)


class Prepare(ConnectionInput):
    origin: str = Field(min_length=8, max_length=2048)
    code: SecretStr = Field(min_length=43, max_length=43)
    name: str = Field(min_length=1, max_length=100)

    @field_validator("origin")
    @classmethod
    def origin_valid(cls, value):
        return private.server_url(value)

    @field_validator("code")
    @classmethod
    def code_valid(cls, value):
        token_value(value.get_secret_value(), enrolment=True)
        return value

    @field_validator("name")
    @classmethod
    def name_valid(cls, value):
        return safe_name(value, 100)


class Confirm(ConnectionInput):
    preparation_id: str = Field(min_length=36, max_length=36)
    origin: str = Field(max_length=2048)
    organisation_id: str = Field(min_length=36, max_length=36)
    pharmacy_id: str = Field(min_length=36, max_length=36)
    device_id: str = Field(min_length=36, max_length=36)
    local_site_id: str = Field(min_length=36, max_length=36)

    @field_validator("preparation_id", "organisation_id", "pharmacy_id", "device_id", "local_site_id")
    @classmethod
    def identifier(cls, value):
        return canonical_id(value)


class Change(ConnectionInput):
    binding_id: str = Field(min_length=36, max_length=36)
    expected_generation: int = Field(ge=1, le=2147483647)

    @field_validator("binding_id")
    @classmethod
    def identifier(cls, value):
        return canonical_id(value)


def install_cloud_connection(app, context, problem):
    service = CloudConnection(app.state.store)
    app.state.cloud_connection = service
    authenticated = contextmanager(context)

    def manager(ctx):
        if ctx.store.mode != "pilot":
            problem(403, "PILOT_REQUIRED", "Cloud connections require a protected pilot workspace.")
        ctx.manager()

    def reauth(ctx, supplied):
        stamp, namespace = service.clock(), "__cloud_reauth__:" + ctx.user["id"]
        ctx.conn.execute("DELETE FROM login_attempts WHERE at<?", (stamp-900,))
        if ctx.conn.execute("SELECT count(*) FROM login_attempts WHERE email=?", (namespace,)).fetchone()[0] >= 5:
            problem(429, "REAUTH_RATE_LIMITED", "Too many incorrect passphrases. Wait fifteen minutes.")
        security = ctx.conn.execute("SELECT password_iterations FROM account_security WHERE user_id=? AND enabled=1", (ctx.user["id"],)).fetchone()
        actual = password_hash(supplied.get_secret_value(), ctx.user["salt"], security[0] if security else PASSWORD_ITERATIONS)
        if not security or not secrets.compare_digest(actual, ctx.user["password_hash"]):
            ctx.conn.execute("INSERT INTO login_attempts(ip,email,at) VALUES(?,?,?)", ("cloud-reauth", namespace, stamp))
            ctx.conn.commit()
            problem(401, "REAUTH_REQUIRED", "Enter your current manager passphrase to confirm this change.")

    def actor(ctx):
        return [ctx.user["id"], ctx.user["organisation_id"], ctx.user["site_id"], ctx.token_hash,
                digest(ctx.user["salt"] + ctx.user["password_hash"]), service.installation(ctx.conn)]

    def fresh(ctx, expected):
        manager(ctx)
        if actor(ctx) != expected:
            problem(409, "CONNECTION_CONTEXT_CHANGED", "The account or branch changed. Reload the connection status.")

    def entries(ctx):
        return service.attempts(ctx.conn)

    def preparation(ctx, identifier):
        record = next((item for item in entries(ctx) if item["preparation_id"] == identifier), None)
        if not record or record["actor"] != actor(ctx):
            problem(404, "PREPARATION_NOT_AVAILABLE", "This connection preparation is not available.")
        if record["expires_at"] <= service.clock() or record["status"] != "PREPARED":
            problem(409, "PREPARATION_EXPIRED", "This preparation expired or was already used. Reload the connection status.")
        if record["fingerprint"] != service.fingerprint(ctx.conn):
            problem(409, "CONNECTION_CHANGED", "The connection changed. Reload and review the current state.")
        return record

    def binding(ctx, body):
        row = ctx.conn.execute("SELECT * FROM cloud_sync_bindings WHERE id=? AND installation_id=? AND organisation_id=? AND site_id=?",
            (body.binding_id, service.installation(ctx.conn), ctx.user["organisation_id"], ctx.user["site_id"])).fetchone()
        if not row:
            problem(404, "CONNECTION_NOT_AVAILABLE", "This connection is not available to this branch.")
        if row["generation"] != body.expected_generation:
            problem(409, "CONNECTION_CHANGED", "The connection changed. Reload and review its current state.")
        return dict(row)

    def public(ctx):
        records = entries(ctx)
        rows = [dict(row) for row in ctx.conn.execute("SELECT * FROM cloud_sync_bindings WHERE installation_id=? ORDER BY (state IN ('ACTIVE','PAUSED')) DESC,created_ms DESC,id", (service.installation(ctx.conn),))]
        own = next((row for row in rows if (row["organisation_id"], row["site_id"]) == (ctx.user["organisation_id"], ctx.user["site_id"])), None)
        connection = None
        delivery = {"pending": 0, "received": 0, "blocked": 0, "withdrawal_pending": 0, "worker_running": False}
        if own:
            available = False
            error = ctx.conn.execute("SELECT value FROM runtime_settings WHERE key=?", ("cloud_connection_error:" + own["id"],)).fetchone()
            if own["state"] in {"ACTIVE", "PAUSED"}:
                try:
                    service.material(own)
                    available = True
                except ConnectionUnavailable:
                    error = ["KEY_UNAVAILABLE"]
            identity = next((item.get("identity") for item in records if item["id"] == own["id"]), None)
            connection = {"binding_id": own["id"], "generation": own["generation"], "state": own["state"],
                "origin": own["origin"], "organisation_id": own["cloud_organisation_id"], "pharmacy_id": own["pharmacy_id"],
                "device_id": own["device_id"], "credential_available": available, "error_code": error[0] if error else None,
                "identity": identity}
            counts = ctx.conn.execute("""SELECT
                coalesce(sum(operation='OBSERVATION' AND state IN ('PENDING','LEASED')),0),
                coalesce(sum(observation_receipt_id IS NOT NULL),0),
                coalesce(sum(state='BLOCKED'),0),
                coalesce(sum(operation='WITHDRAWAL' AND state!='WITHDRAWN'),0)
                FROM cloud_sync_items WHERE binding_id=?""", (own["id"],)).fetchone()
            delivery.update(zip(("pending", "received", "blocked", "withdrawal_pending"), counts))
            worker = getattr(app.state, "cloud_delivery", None)
            delivery["worker_running"] = bool(worker is not None and worker.running)
        pending = next((item for item in reversed(records) if item["actor"] == actor(ctx)
                        and item["status"] in {"PREPARING", "PREPARED", "UNCERTAIN"} and item["expires_at"] > service.clock()), None)
        projection = None if pending is None else {"preparation_id": pending["preparation_id"], "status": pending["status"],
            "expires_at": datetime.fromtimestamp(pending["expires_at"], timezone.utc).isoformat().replace("+00:00", "Z"),
            "origin": pending["origin"], "local_site_id": ctx.user["site_id"], "identity": pending.get("identity"),
            "remote_device_id": pending.get("device_id")}
        site = ctx.get("site", ctx.user["site_id"])
        return {"enabled": True, "local_site": {"id": site["id"], "name": site["name"]}, "connection": connection,
                "preparation": projection, "occupied_elsewhere": any(row["state"] in {"ACTIVE", "PAUSED"} and
                    (row["organisation_id"], row["site_id"]) != (ctx.user["organisation_id"], ctx.user["site_id"]) for row in rows),
                "monitoring_status": "UNKNOWN", "delivery": delivery}

    def uncertain(record):
        # Remote code consumption cannot be rolled back. Keep a bounded local
        # receipt even if the manager session was revoked during the request.
        with service.store.transaction() as conn:
            values = service.attempts(conn)
            for item in values:
                if item["preparation_id"] == record["preparation_id"] and item["status"] == "PREPARING":
                    item.update(status="UNCERTAIN", device_id=record.get("device_id"))
            service.save_attempts(conn, values)

    def connection_error():
        problem(503, "CONNECTION_STORAGE_UNAVAILABLE", "Private connection storage is unavailable. Check the laptop's private storage before pairing.")

    @app.get("/api/cloud-connection")
    def status(request: Request):
        with authenticated(request) as ctx:
            manager(ctx)
            return public(ctx)

    @app.post("/api/cloud-connection/prepare", status_code=201)
    def prepare(body: Prepare, request: Request):
        if not service.io_lock.acquire(blocking=False):
            problem(409, "CONNECTION_BUSY", "Another connection check is running. Wait and reload its status.")
        record = None
        try:
            with authenticated(request) as ctx:
                manager(ctx)
                reauth(ctx, body.manager_password)
                if ctx.conn.execute("SELECT 1 FROM cloud_sync_bindings WHERE state IN ('ACTIVE','PAUSED') LIMIT 1").fetchone():
                    problem(409, "CONNECTION_EXISTS", "Disconnect the current laptop binding before preparing another.")
                values = entries(ctx)
                code_hash = digest(body.code.get_secret_value())
                if any(item["code_hash"] == code_hash for item in values):
                    problem(409, "CODE_ALREADY_ATTEMPTED", "This one-use code was already attempted. Check the cloud console before requesting a new code.")
                if any(item["status"] in {"PREPARING", "PREPARED"} and item["expires_at"] > service.clock() for item in values):
                    problem(409, "CONNECTION_BUSY", "An existing preparation must expire before another is started.")
                if len(values) >= MAX_ATTEMPTS:
                    problem(409, "CONNECTION_LIMIT", "The local connection preparation limit is reached. Review previous registrations before configuring more.")
                record = {"id": str(uuid4()), "preparation_id": str(uuid4()), "credential_handle": str(uuid4()),
                    "installation_id": service.installation(ctx.conn), "organisation_id": ctx.user["organisation_id"],
                    "site_id": ctx.user["site_id"], "actor": actor(ctx), "origin": body.origin,
                    "expires_at": service.clock()+TTL, "status": "PREPARING", "fingerprint": service.fingerprint(ctx.conn),
                    "code_hash": code_hash}
                try:
                    service.vault.ensure(record["credential_handle"])
                except ConnectionUnavailable:
                    connection_error()
                values.append(record)
                service.save_attempts(ctx.conn, values)
            # The short authentication transaction has committed and closed.
            try:
                transport = service.transport_factory(body.origin)
                result = transport.enrol(body.code.get_secret_value(), name=body.name,
                    platform="MACOS" if platform.system() == "Darwin" else "WINDOWS" if platform.system() == "Windows" else "OTHER")
                record["device_id"] = result["device_id"]
                service.vault.write(record["credential_handle"], {"version": 1, "handle": record["credential_handle"],
                    "binding_id": record["id"], "installation_id": record["installation_id"],
                    "organisation_id": record["organisation_id"], "site_id": record["site_id"], "origin": record["origin"],
                    "device_id": result["device_id"], "credential": result["device_token"],
                    "key": base64.urlsafe_b64encode(secrets.token_bytes(32)).decode().rstrip("=")})
                identity = transport.identity(result["device_token"], expected_device_id=result["device_id"])
            except (CloudTransportError, ConnectionUnavailable, ValueError, OSError):
                uncertain(record)
                problem(502, "CONNECTION_UNCERTAIN", "Registration may have succeeded. Inspect the selected cloud console's Laptops page and revoke any unconfirmed registration before using a new code.")
            try:
                with authenticated(request) as ctx:
                    fresh(ctx, record["actor"])
                    if service.clock() >= record["expires_at"] or record["fingerprint"] != service.fingerprint(ctx.conn):
                        problem(409, "CONNECTION_CHANGED", "The preparation expired or the connection changed. Inspect the cloud registration before starting again.")
                    values = entries(ctx)
                    current = next(item for item in values if item["preparation_id"] == record["preparation_id"])
                    current.update(status="PREPARED", device_id=result["device_id"], identity=identity)
                    service.save_attempts(ctx.conn, values)
                    ctx.audit("CLOUD_CONNECTION_PREPARED", "cloud_binding", record["id"], "Remote identity awaits explicit manager confirmation.")
                    return public(ctx)
            except Exception:
                uncertain(record)
                raise
        finally:
            service.io_lock.release()

    @app.post("/api/cloud-connection/confirm")
    def confirm(body: Confirm, request: Request):
        if not service.io_lock.acquire(blocking=False):
            problem(409, "CONNECTION_BUSY", "Another connection check is running. Wait and reload its status.")
        try:
            with authenticated(request) as ctx:
                manager(ctx)
                reauth(ctx, body.manager_password)
                record = preparation(ctx, body.preparation_id)
                identity = record["identity"]
                if body.local_site_id != ctx.user["site_id"] or body.origin != record["origin"] or any(
                        getattr(body, key) != identity[key] for key in ("organisation_id", "pharmacy_id", "device_id")):
                    problem(409, "IDENTITY_CONFIRMATION_MISMATCH", "Confirm the exact displayed cloud organisation, pharmacy, device and local branch.")
                try:
                    box, credential = service.material(record)
                except ConnectionUnavailable:
                    connection_error()
            try:
                checked = service.transport_factory(record["origin"]).identity(credential, expected_device_id=identity["device_id"])
                if checked != identity:
                    problem(409, "REMOTE_IDENTITY_CHANGED", "The cloud identity changed. Inspect the remote registration before preparing again.")
            except CloudTransportError:
                problem(502, "CONNECTION_CHECK_FAILED", "The cloud identity could not be rechecked. No local connection was activated.")
            with authenticated(request) as ctx:
                fresh(ctx, record["actor"])
                current = preparation(ctx, body.preparation_id)
                # Reload private material too: a file replacement cannot alter
                # the key/credential used to commit the validated remote identity.
                try:
                    fresh_box, fresh_credential = service.material(current)
                except ConnectionUnavailable:
                    connection_error()
                if fresh_credential != credential or fresh_box._key_hash != box._key_hash:
                    connection_error()
                scope = Scope(record["installation_id"], ctx.user["organisation_id"], ctx.user["site_id"])
                try:
                    box.create_binding(ctx.conn, scope, binding_id=record["id"], expected_generation=0,
                        target=Target(record["origin"], identity["organisation_id"], identity["pharmacy_id"], identity["device_id"], record["credential_handle"]), now=service.stamp())
                except OutboxError:
                    problem(409, "CONNECTION_CHANGED", "The connection changed. Reload and review its current state.")
                values = entries(ctx)
                next(item for item in values if item["preparation_id"] == record["preparation_id"])["status"] = "CONFIRMED"
                service.save_attempts(ctx.conn, values)
                ctx.audit("CLOUD_CONNECTION_CONFIRMED", "cloud_binding", record["id"], "Manager confirmed the remote pharmacy; metadata sharing remains paused.")
                return public(ctx)
        finally:
            service.io_lock.release()

    def change(operation, body, request):
        with authenticated(request) as ctx:
            manager(ctx)
            reauth(ctx, body.manager_password)
            record = binding(ctx, body)
            snapshot = actor(ctx)
            if record["state"] not in {"ACTIVE", "PAUSED"}:
                problem(409, "CONNECTION_CLOSED", "This connection is closed. Prepare a new registration after management review.")
            if operation == "pause":
                # Stop intent fences an in-flight resume even if already paused,
                # and cannot be prevented by a missing/corrupt private key.
                service._pause_row(ctx.conn, record)
                ctx.audit("CLOUD_CONNECTION_PAUSE", "cloud_binding", body.binding_id, "Manager paused local metadata sharing.")
                return public(ctx)
            try:
                box, credential = service.material(record)
            except ConnectionUnavailable:
                connection_error()
        if operation == "resume":
            try:
                identity = service.transport_factory(record["origin"]).identity(credential, expected_device_id=record["device_id"])
                if identity["organisation_id"] != record["cloud_organisation_id"] or identity["pharmacy_id"] != record["pharmacy_id"]:
                    problem(409, "REMOTE_IDENTITY_CHANGED", "The remote pharmacy identity changed. Sharing remains paused.")
            except CloudTransportError:
                problem(502, "CONNECTION_CHECK_FAILED", "The remote device could not be verified. Sharing was not resumed.")
        with authenticated(request) as ctx:
            fresh(ctx, snapshot)
            current = binding(ctx, body)
            try:
                fresh_box, fresh_credential = service.material(current)
            except ConnectionUnavailable:
                connection_error()
            if fresh_credential != credential or fresh_box._key_hash != box._key_hash:
                connection_error()
            scope = Scope(current["installation_id"], ctx.user["organisation_id"], ctx.user["site_id"])
            try:
                if operation == "disconnect":
                    box.disconnect(ctx.conn, scope, BindingRef(body.binding_id, body.expected_generation), now=service.stamp())
                else:
                    box.set_paused(ctx.conn, scope, BindingRef(body.binding_id, body.expected_generation), paused=False, now=service.stamp())
            except OutboxError:
                problem(409, "CONNECTION_CHANGED", "The connection changed or its clock needs review. Reload the current state.")
            ctx.conn.execute("DELETE FROM runtime_settings WHERE key=?", ("cloud_connection_error:" + body.binding_id,))
            ctx.audit("CLOUD_CONNECTION_" + operation.upper(), "cloud_binding", body.binding_id,
                      "Manager changed local metadata sharing. No remote deletion receipt is implied.")
            return public(ctx)

    @app.post("/api/cloud-connection/pause")
    def pause(body: Change, request: Request):
        return change("pause", body, request)

    @app.post("/api/cloud-connection/resume")
    def resume(body: Change, request: Request):
        if not service.io_lock.acquire(blocking=False):
            problem(409, "CONNECTION_BUSY", "Another connection check is running. Wait and reload its status.")
        try:
            return change("resume", body, request)
        finally:
            service.io_lock.release()

    @app.post("/api/cloud-connection/disconnect")
    def disconnect(body: Change, request: Request):
        return change("disconnect", body, request)
