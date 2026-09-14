"""Explicitly started, metadata-only outbox delivery with bounded owned I/O.

This module is safe to import in a multiprocessing spawn child: no app, Store,
default database, credentials, network or worker is opened during import.
"""

from dataclasses import dataclass
from datetime import datetime, timezone
import hmac
import json
import math
import multiprocessing
import threading
import time

from .cloud_outbox import OutboxError
from .cloud_media_outbox import MediaClaim, MediaOutbox
from .cloud_transport import CloudTransport, CloudTransportError, canonical_id
from .evidence_crypto import EvidenceError, PilotDatabaseLock


MAX_IPC = 8192
HALT_CODES = frozenset({"KEY_UNAVAILABLE", "CIPHERTEXT_INVALID", "CLOCK_ROLLBACK"})
REMOTE_PERMANENT = frozenset({"VALIDATION_FAILED", "REQUEST_REFUSED", "EVENT_CONFLICT",
                              "INVALID_EVENT_TIME", "REDIRECT_REFUSED", "RECEIPT_MISMATCH"})
REMOTE_CODES = REMOTE_PERMANENT | frozenset({"NETWORK_UNAVAILABLE", "INVALID_RESPONSE",
    "SERVICE_UNAVAILABLE", "RATE_LIMITED", "SYNC_LIMIT", "ACCESS_REVOKED"})


@dataclass(frozen=True)
class DeliveryPolicy:
    interval_seconds: float = 2
    request_seconds: float = 8
    lease_seconds: int = 20
    recheck_seconds: float = .25
    retry_base_seconds: int = 5
    max_retry_seconds: int = 300
    observation_attempts: int = 8
    total_attempts: int = 16
    reconciliation_rows: int = 50

    def __post_init__(self):
        for value, lower, upper in ((self.interval_seconds, .05, 60),
                                   (self.request_seconds, .1, 30),
                                   (self.recheck_seconds, .05, 1)):
            if type(value) not in (int, float) or not math.isfinite(value) or not lower <= value <= upper:
                raise ValueError("INVALID_DELIVERY_POLICY")
        for value, upper in ((self.lease_seconds, 60), (self.retry_base_seconds, 3600),
                             (self.max_retry_seconds, 3600), (self.observation_attempts, 100),
                             (self.total_attempts, 200), (self.reconciliation_rows, 200)):
            if type(value) is not int or not 1 <= value <= upper:
                raise ValueError("INVALID_DELIVERY_POLICY")
        if (self.lease_seconds < self.request_seconds + 4
                or self.max_retry_seconds < self.retry_base_seconds
                or self.total_attempts <= self.observation_attempts):
            raise ValueError("INVALID_DELIVERY_POLICY")


@dataclass(frozen=True)
class DeliveryResult:
    status: str
    operation: str | None = None
    error_code: str | None = None
    retry_seconds: int = 0


def _send_json(pipe, value):
    raw = json.dumps(value, separators=(",", ":"), allow_nan=False).encode("utf-8")
    if len(raw) > MAX_IPC:
        raise ValueError("IPC_LIMIT")
    pipe.send_bytes(raw)


def _receive_json(pipe):
    raw = pipe.recv_bytes(MAX_IPC)
    value = json.loads(raw.decode("utf-8"))
    if not isinstance(value, dict):
        raise ValueError("IPC_INVALID")
    return value


def _valid_receipt(value, request):
    if not isinstance(value, dict):
        return False
    try:
        if request["operation"] == "OBSERVATION":
            if (set(value) != {"id", "received", "source_state"} or value["received"] is not True
                    or type(value["source_state"]) is not str
                    or value["source_state"] not in {"AVAILABLE", "EXPIRED", "WITHDRAWN"}):
                return False
            canonical_id(value["id"])
            return request["receipt_id"] is None or value["id"] == request["receipt_id"]
        if request["operation"] == "WITHDRAWAL":
            return (set(value) == {"source_event_id", "withdrawn"} and value["withdrawn"] is True
                    and value["source_event_id"] == request["source_id"])
        if request["operation"] == "MEDIA_MANIFEST":
            return ({"evidence_id", "upload_required", "state"}.issubset(value)
                    and value["state"] in {"PENDING", "READY"} and type(value["upload_required"]) is bool)
        if request["operation"] == "MEDIA_CONTENT":
            return ({"evidence_id", "state"}.issubset(value) and value["evidence_id"] == request["evidence_id"]
                    and value["state"] == "READY")
        return False
    except (ValueError, TypeError):
        return False


def _request_child(pipe):
    """Standalone spawn target; no database or filesystem credential access."""
    try:
        _send_json(pipe, {"ready": True})
        if not pipe.poll(35):
            return
        request = _receive_json(pipe)
        observation_fields = {"origin", "credential", "operation", "payload", "source_id", "deadline", "receipt_id", "allow_local_test"}
        manifest_fields = {"origin", "credential", "operation", "payload", "source_id", "allow_local_test"}
        content_fields = {"origin", "credential", "operation", "source_id", "evidence_id", "content_type", "sha256", "byte_count", "allow_local_test"}
        if frozenset(request) not in {frozenset(observation_fields), frozenset(manifest_fields), frozenset(content_fields)}:
            raise ValueError
        transport = CloudTransport(request["origin"], allow_local_test=request["allow_local_test"])
        if request["operation"] == "OBSERVATION":
            result = transport.observation(request["credential"], request["payload"],
                expires_at=datetime.fromisoformat(request["deadline"]),
                expected_source_event_id=request["source_id"], expected_receipt_id=request["receipt_id"])
        elif request["operation"] == "WITHDRAWAL":
            result = transport.withdrawal(request["credential"], request["payload"],
                                          expected_source_event_id=request["source_id"])
        elif request["operation"] == "MEDIA_MANIFEST":
            result = transport.evidence_manifest(request["credential"], request["source_id"], request["payload"])
        elif request["operation"] == "MEDIA_CONTENT":
            content = pipe.recv_bytes(350 * 1024)
            if len(content) != request["byte_count"]:
                raise ValueError
            result = transport.evidence_content(request["credential"], request["evidence_id"], content,
                                                content_type=request["content_type"], sha256=request["sha256"])
        else:
            raise ValueError
        _send_json(pipe, {"ok": True, "result": result})
    except CloudTransportError as error:
        try:
            _send_json(pipe, {"ok": False, "code": error.code if error.code in REMOTE_CODES else "REQUEST_REFUSED",
                              "retry_after": error.retry_after})
        except Exception:
            pass
    except Exception:
        try:
            _send_json(pipe, {"ok": False, "code": "INVALID_RESPONSE", "retry_after": None})
        except Exception:
            pass
    finally:
        pipe.close()


class OwnedRequest:
    """One spawn child, with an overall parent deadline including startup/DNS.

    A timeout/cancellation is uncertain delivery. The remote server may have
    received the request even though its owned local client is now terminated.
    """

    def __init__(self):
        self._process = None
        self._pipe = None
        self._process_lock = threading.Lock()

    @property
    def alive(self):
        with self._process_lock:
            return self._process is not None and self._process.is_alive()

    def close(self):
        with self._process_lock:
            try:
                return self._close()
            except Exception:
                # Preserve the handle/ownership for another explicit stop;
                # inability to kill/reap is never a successful shutdown.
                return False

    def _close(self):
        process = self._process
        if process is not None:
            if process.is_alive():
                process.terminate()
                process.join(timeout=.5)
            if process.is_alive():
                process.kill()
                process.join(timeout=.5)
            if process.is_alive():
                return False
            if process.pid is not None:
                process.join(timeout=0)
            process.close()
            self._process = None
        if self._pipe is not None:
            self._pipe.close()
            self._pipe = None
        return True

    def perform(self, request, *, timeout, cancel, authorize, recheck_seconds, content=None):
        if self.alive:
            raise RuntimeError("REQUEST_OWNER_BUSY")
        deadline, next_check = time.monotonic() + timeout, 0
        context = multiprocessing.get_context("spawn")
        parent, child = context.Pipe(duplex=True)
        self._pipe = parent
        self._process = context.Process(target=_request_child, args=(child,), daemon=True,
                                        name="aislesignals-cloud-request")
        done, timed_out, cancelled = threading.Event(), threading.Event(), threading.Event()
        watchdog = None

        def watch():
            while not done.is_set():
                if cancel.is_set():
                    cancelled.set()
                    self.close()
                    return
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    timed_out.set()
                    self.close()
                    return
                done.wait(min(.05, remaining))

        sent = False
        try:
            with self._process_lock:
                self._process.start()
            child.close()
            # Revalidation may wait on SQLite. This independent owner watchdog
            # still terminates HTTP at its deadline/stop while that callback is
            # blocked; no SQL lock can prolong a live network request.
            watchdog = threading.Thread(target=watch, name="aislesignals-request-deadline", daemon=True)
            watchdog.start()
            while True:
                if cancel.is_set():
                    return {"ok": False, "code": "CANCELLED", "retry_after": None}
                current = time.monotonic()
                if current >= deadline:
                    return {"ok": False, "code": "REQUEST_TIMEOUT", "retry_after": None}
                if sent and current >= next_check:
                    if not authorize():
                        return {"ok": False, "code": "CONTEXT_CHANGED", "retry_after": None}
                    next_check = current + recheck_seconds
                if parent.poll(min(.05, max(0, deadline-current))):
                    value = _receive_json(parent)
                    if not sent:
                        if value != {"ready": True}:
                            raise ValueError
                        # Credential/payload enters the child only after this
                        # fresh authorization transaction has committed.
                        if cancel.is_set() or not authorize() or time.monotonic() >= deadline:
                            return {"ok": False, "code": "CONTEXT_CHANGED", "retry_after": None}
                        _send_json(parent, request)
                        if request.get("operation") == "MEDIA_CONTENT":
                            if type(content) is not bytes or len(content) != request.get("byte_count"):
                                raise ValueError
                            parent.send_bytes(content)
                        sent, next_check = True, time.monotonic() + recheck_seconds
                    else:
                        if value.get("ok") is True and set(value) == {"ok", "result"} and _valid_receipt(value["result"], request):
                            return value
                        if (value.get("ok") is False and set(value) == {"ok", "code", "retry_after"}
                                and isinstance(value["code"], str) and value["code"] in REMOTE_CODES
                                and (value["retry_after"] is None or type(value["retry_after"]) is int and 5 <= value["retry_after"] <= 3600)):
                            return value
                        raise ValueError
                elif not self.alive:
                    raise ValueError
        except Exception as error:
            code = "CANCELLED" if cancelled.is_set() else "REQUEST_TIMEOUT" if timed_out.is_set() else getattr(error, "code", None)
            return {"ok": False, "code": code if code in HALT_CODES | {"CANCELLED", "REQUEST_TIMEOUT"} else "NETWORK_UNAVAILABLE", "retry_after": None}
        finally:
            done.set()
            child.close()
            self.close()
            if watchdog is not None:
                watchdog.join(timeout=2)


class CloudDelivery:
    """Provider + scoped source callback are mandatory trusted dependencies."""

    def __init__(self, store, provider, *, source_state, media_source=None, media_reader=None, policy=DeliveryPolicy(),
                 clock=None, allow_local_test=False, request_factory=OwnedRequest):
        if (not callable(source_state) or not isinstance(policy, DeliveryPolicy) or type(allow_local_test) is not bool
                or (media_source is None) != (media_reader is None)
                or media_source is not None and (not callable(media_source) or not callable(media_reader))):
            raise ValueError("INVALID_DELIVERY_CONFIG")
        self.store, self.provider, self.source_state, self.policy = store, provider, source_state, policy
        self.media_source, self.media_reader = media_source, media_reader
        self.media = MediaOutbox()
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.allow_local_test = allow_local_test
        self._request = request_factory()
        self._stop = threading.Event()
        self._pass_lock = threading.Lock()
        self._lifecycle_lock = threading.Lock()
        self._thread = None
        self._owner = None
        self._halted = False
        self._cursor = None
        self._cursor_binding = None
        self.last_result = None

    @property
    def running(self):
        return self._thread is not None and self._thread.is_alive()

    def _same(self, before, after):
        return (after is not None and before.scope == after.scope and before.binding == after.binding
                and before.target == after.target and hmac.compare_digest(before.credential, after.credential))

    @staticmethod
    def _row(conn, context, item_id):
        row = conn.execute("""SELECT i.*,b.clock_ms AS binding_clock_ms FROM cloud_sync_items i JOIN cloud_sync_bindings b ON b.id=i.binding_id
            WHERE i.id=? AND b.id=? AND b.installation_id=? AND b.organisation_id=? AND b.site_id=?""",
            (item_id, context.binding.id, context.scope.installation_id,
             context.scope.organisation_id, context.scope.site_id)).fetchone()
        return dict(row) if row is not None else None

    def _source_reason(self, conn, context, row, now):
        if now >= datetime.fromisoformat(row["deadline_iso"].replace("Z", "+00:00")):
            return "LOCAL_EXPIRED"
        state = self.source_state(conn, context, row["entity_kind"], row["entity_id"])
        if state == "AVAILABLE":
            return None
        if state not in {"DELETED", "EXPIRED", "INELIGIBLE"}:
            raise ValueError("INVALID_SOURCE_STATE")
        return {"DELETED": "LOCAL_DELETED", "EXPIRED": "LOCAL_EXPIRED",
                "INELIGIBLE": "LOCAL_EXPORT_REMOVED"}[state]

    def _reconcile(self, conn, context, now):
        if self._cursor_binding != context.binding.id:
            self._cursor = None
            self._cursor_binding = context.binding.id
        rows = conn.execute("""SELECT id FROM cloud_sync_items WHERE binding_id=?
            AND operation='OBSERVATION' AND state IN ('PENDING','LEASED','RECEIVED')
            AND (? IS NULL OR id>?) ORDER BY id LIMIT ?""",
            (context.binding.id, self._cursor, self._cursor, self.policy.reconciliation_rows)).fetchall()
        if not rows and self._cursor is not None:
            self._cursor = None
            return self._reconcile(conn, context, now)
        for item in rows:
            self._cursor = item["id"]
            row = self._row(conn, context, item["id"])
            reason = self._source_reason(conn, context, row, now)
            if reason:
                context.outbox.withdraw(conn, context.scope, context.binding, row["id"], reason=reason, now=now)
        self.media.reconcile(conn, context.scope, context.binding, now=now)
        context.outbox.prune_terminal(conn, context.scope, context.binding, now=now, limit=100)

    def _authorize(self, context, claim):
        if self._stop.is_set():
            return False
        with self.store.transaction() as conn:
            current = self.provider.delivery_context(conn)
            if not self._same(context, current):
                return False
            now = self.clock()
            row = self._row(conn, current, claim.id)
            if row is not None and int(now.timestamp()*1000) < row["binding_clock_ms"]:
                raise OutboxError("CLOCK_ROLLBACK")
            if (row is None or row["state"] != "LEASED" or row["lease_token"] != claim.token
                    or row["claim_generation"] != current.binding.generation or row["operation"] != claim.operation
                    or now >= claim.lease_until):
                return False
            if claim.operation == "OBSERVATION":
                reason = self._source_reason(conn, current, row, now)
                if reason:
                    current.outbox.withdraw(conn, current.scope, current.binding, claim.id, reason=reason, now=now)
                    return False
            return True

    def _authorize_media(self, context, claim):
        if self._stop.is_set() or self.media_source is None:
            return False
        with self.store.transaction() as conn:
            current = self.provider.delivery_context(conn)
            if not self._same(context, current):
                return False
            row = conn.execute("SELECT * FROM cloud_media_items WHERE id=? AND binding_id=?", (claim.id, current.binding.id)).fetchone()
            expected = 'LEASED_MANIFEST' if claim.operation == 'MEDIA_MANIFEST' else 'LEASED_CONTENT'
            return bool(row is not None and row['state'] == expected and row['lease_token'] == claim.token
                and row['claim_generation'] == current.binding.generation and self.clock() < claim.lease_until
                and self.media_source(conn, current, claim))

    def run_once(self):
        if not self._pass_lock.acquire(blocking=False):
            return DeliveryResult("BUSY")
        context = None
        try:
            if self._stop.is_set() or self._halted:
                return DeliveryResult("STOPPED")
            if self.store.mode != "pilot":
                return DeliveryResult("DISABLED")
            try:
                self._owner = PilotDatabaseLock(self.store.path + ".cloud-delivery")
            except EvidenceError:
                return DeliveryResult("BUSY")
            with self.store.transaction() as conn:
                context = self.provider.delivery_context(conn)
                if context is None:
                    return DeliveryResult("DISABLED")
                now = self.clock()
                self._reconcile(conn, context, now)
                claim = context.outbox.claim(conn, context.scope, context.binding, now=now,
                                             lease_seconds=self.policy.lease_seconds)
                if claim is None:
                    claim = self.media.claim(conn, context.scope, context.binding, now=now,
                                             lease_seconds=self.policy.lease_seconds) if self.media_source is not None else None
                    if claim is None:
                        return DeliveryResult("IDLE")
                row = self._row(conn, context, claim.id)
                receipt_id = row["observation_receipt_id"] if claim.operation == "OBSERVATION" else None
            remaining = (claim.lease_until-self.clock()).total_seconds() - 2
            if remaining <= 0:
                # Do not start a request whose lease/source cannot cover its
                # deadline and cleanup margin. claim expiry creates withdrawal.
                return DeliveryResult("FENCED", claim.operation)
            content = None
            if isinstance(claim, MediaClaim):
                if claim.operation == 'MEDIA_MANIFEST':
                    request = {"origin": context.target.origin, "credential": context.credential,
                        "operation": claim.operation, "payload": claim.manifest, "source_id": claim.source_event_id,
                        "allow_local_test": self.allow_local_test}
                else:
                    try:
                        content = self.media_reader(claim)
                    except Exception:
                        with self.store.transaction() as conn:
                            current = self.provider.delivery_context(conn)
                            if self._same(context, current) and self._authorize_media_in_transaction(conn, current, claim):
                                self.media.reject(conn, current.scope, current.binding, claim,
                                                  code='LOCAL_CORRUPTION', now=self.clock())
                        return DeliveryResult('BLOCKED', claim.operation, 'LOCAL_CORRUPTION')
                    request = {"origin": context.target.origin, "credential": context.credential,
                        "operation": claim.operation, "source_id": claim.source_event_id, "evidence_id": claim.evidence_id,
                        "content_type": claim.content_type, "sha256": claim.sha256, "byte_count": claim.byte_count,
                        "allow_local_test": self.allow_local_test}
                authorize = lambda: self._authorize_media(context, claim)
            else:
                request = {"origin": context.target.origin, "credential": context.credential,
                    "operation": claim.operation, "payload": dict(claim.payload), "source_id": claim.source_event_id,
                    "deadline": claim.deadline.isoformat(), "receipt_id": receipt_id,
                    "allow_local_test": self.allow_local_test}
                authorize = lambda: self._authorize(context, claim)
            request_args = dict(timeout=min(self.policy.request_seconds, remaining), cancel=self._stop,
                                authorize=authorize, recheck_seconds=self.policy.recheck_seconds)
            if isinstance(claim, MediaClaim):
                request_args['content'] = content
            response = self._request.perform(request, **request_args)
            if self._request.alive:
                self._halted = True
                return DeliveryResult("BLOCKED", claim.operation, "REQUEST_STOP_FAILED")
            return self._settle_media(context, claim, response) if isinstance(claim, MediaClaim) else self._settle(context, claim, response)
        except Exception as error:
            code = getattr(error, "code", None)
            if code in HALT_CODES:
                self._halted = True
                if context is not None:
                    try:
                        with self.store.transaction() as conn:
                            self.provider.pause_error(conn, context, code=code, now=self.clock())
                    except Exception:
                        pass
                return DeliveryResult("BLOCKED", error_code=code)
            return DeliveryResult("UNAVAILABLE", error_code="DELIVERY_UNAVAILABLE")
        finally:
            if not self._request.alive and self._owner is not None:
                self._owner.close()
                self._owner = None
            self._pass_lock.release()

    def _settle_media(self, context, claim, response):
        with self.store.transaction() as conn:
            current = self.provider.delivery_context(conn)
            if not self._same(context, current) or not self._authorize_media_in_transaction(conn, current, claim):
                return DeliveryResult('FENCED', claim.operation)
            now = self.clock()
            if response.get('code') == 'ACCESS_REVOKED':
                self.provider.access_revoked(conn, current, now=now)
                return DeliveryResult('BLOCKED', claim.operation, 'ACCESS_REVOKED')
            if response.get('ok') is True:
                result = response['result']
                if claim.operation == 'MEDIA_MANIFEST':
                    self.media.acknowledge_manifest(conn, current.scope, current.binding, claim,
                        evidence_id=result['evidence_id'], upload_required=result['upload_required'],
                        state=result['state'], now=now)
                else:
                    self.media.acknowledge_content(conn, current.scope, current.binding, claim,
                        evidence_id=result['evidence_id'], state=result['state'], now=now)
                return DeliveryResult('DELIVERED', claim.operation)
            code = response.get('code')
            if code in {'CANCELLED', 'CONTEXT_CHANGED'}:
                return DeliveryResult('FENCED', claim.operation)
            row = conn.execute('SELECT attempts FROM cloud_media_items WHERE id=?', (claim.id,)).fetchone()
            if code in REMOTE_PERMANENT or row['attempts'] >= self.policy.total_attempts:
                self.media.reject(conn, current.scope, current.binding, claim, code='REMOTE_REJECTED', now=now)
                return DeliveryResult('BLOCKED', claim.operation, 'REMOTE_REJECTED')
            delay = min(self.policy.max_retry_seconds, self.policy.retry_base_seconds * 2**min(row['attempts']-1, 12))
            if type(response.get('retry_after')) is int:
                delay = max(delay, response['retry_after'])
            self.media.retry(conn, current.scope, current.binding, claim, now=now, delay_seconds=delay)
            return DeliveryResult('RETRY', claim.operation, code if code in REMOTE_CODES | {'REQUEST_TIMEOUT'} else 'NETWORK_UNAVAILABLE', delay)

    def _authorize_media_in_transaction(self, conn, context, claim):
        row = conn.execute('SELECT * FROM cloud_media_items WHERE id=? AND binding_id=?', (claim.id, context.binding.id)).fetchone()
        expected = 'LEASED_MANIFEST' if claim.operation == 'MEDIA_MANIFEST' else 'LEASED_CONTENT'
        return bool(row is not None and row['state'] == expected and row['lease_token'] == claim.token
                    and row['claim_generation'] == context.binding.generation and self.clock() < claim.lease_until
                    and self.media_source(conn, context, claim))

    def _settle(self, context, claim, response):
        if self._stop.is_set():
            return DeliveryResult("FENCED", claim.operation)
        with self.store.transaction() as conn:
            current = self.provider.delivery_context(conn)
            if not self._same(context, current):
                return DeliveryResult("FENCED", claim.operation)
            now = self.clock()
            if response.get("code") == "ACCESS_REVOKED":
                self.provider.access_revoked(conn, current, now=now)
                return DeliveryResult("BLOCKED", claim.operation, "ACCESS_REVOKED")
            if response.get("code") in HALT_CODES:
                self._halted = True
                self.provider.pause_error(conn, current, code=response["code"], now=now)
                return DeliveryResult("BLOCKED", claim.operation, response["code"])
            row = self._row(conn, current, claim.id)
            if (row is None or row["state"] != "LEASED" or row["lease_token"] != claim.token
                    or row["claim_generation"] != current.binding.generation or row["operation"] != claim.operation
                    or now >= claim.lease_until):
                return DeliveryResult("FENCED", claim.operation)
            if claim.operation == "OBSERVATION":
                reason = self._source_reason(conn, current, row, now)
                if reason:
                    current.outbox.withdraw(conn, current.scope, current.binding, claim.id, reason=reason, now=now)
                    return DeliveryResult("FENCED", claim.operation)
            if response.get("ok") is True:
                result = response["result"]
                # The child uses strict CloudTransport receipt validation.
                receipt = result["id"] if claim.operation == "OBSERVATION" else result["source_event_id"]
                current.outbox.acknowledge(conn, current.scope, current.binding, claim, receipt_id=receipt, now=now)
                return DeliveryResult("DELIVERED" if claim.operation == "OBSERVATION" else "WITHDRAWN", claim.operation)
            code = response.get("code")
            if code in {"CANCELLED", "CONTEXT_CHANGED"}:
                return DeliveryResult("FENCED", claim.operation)
            maximum = self.policy.observation_attempts if claim.operation == "OBSERVATION" else self.policy.total_attempts
            if code in REMOTE_PERMANENT or row["attempts"] >= maximum:
                current.outbox.reject(conn, current.scope, current.binding, claim,
                    code="REMOTE_CONFLICT" if code in {"EVENT_CONFLICT", "RECEIPT_MISMATCH"} else "REMOTE_REJECTED", now=now)
                return DeliveryResult("BLOCKED", claim.operation, "REMOTE_REJECTED")
            delay = min(self.policy.max_retry_seconds, self.policy.retry_base_seconds * 2**min(row["attempts"]-1, 12))
            if type(response.get("retry_after")) is int:
                delay = max(delay, response["retry_after"])
            current.outbox.retry(conn, current.scope, current.binding, claim, now=now, delay_seconds=delay)
            return DeliveryResult("RETRY", claim.operation,
                code if code in REMOTE_CODES | {"REQUEST_TIMEOUT"} else "NETWORK_UNAVAILABLE", delay)

    def start(self):
        with self._lifecycle_lock:
            if self.running or self._request.alive or self._pass_lock.locked():
                return False
            self._stop.clear()
            self._halted = False
            self._thread = threading.Thread(target=self._run, name="aislesignals-cloud-delivery", daemon=True)
            self._thread.start()
            return True

    def _run(self):
        while not self._stop.is_set() and not self._halted:
            self.last_result = self.run_once()
            if self._stop.is_set() or self._halted:
                break
            delay = 10 if self.last_result.status == "UNAVAILABLE" else self.policy.interval_seconds
            self._stop.wait(delay)

    def stop(self, *, timeout=6):
        if type(timeout) not in (int, float) or not math.isfinite(timeout) or not 0 < timeout <= 60:
            raise ValueError("INVALID_STOP_TIMEOUT")
        with self._lifecycle_lock:
            self._stop.set()
            thread = self._thread
            if thread is threading.current_thread():
                return False
            if thread is not None:
                thread.join(timeout=timeout)
            if self.running or not self._pass_lock.acquire(blocking=False):
                return False
            try:
                stopped = self._request.close()
                if stopped and self._owner is not None:
                    self._owner.close()
                    self._owner = None
                return stopped
            finally:
                self._pass_lock.release()
