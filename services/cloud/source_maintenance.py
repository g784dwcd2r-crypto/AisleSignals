"""Bounded physical source cleanup, explicitly started by an app lifespan.

Only stored organisation/device rows establish cleanup scope. No bearer token,
browser endpoint, camera access or work at import/construction is involved.
"""

from dataclasses import dataclass
import math
import threading
import time

import psycopg

from .control_store import ControlError, ControlStore
from .device_sync import cleanup_device_sources


# Global to this database; distinct from the authentication throttle lock.
OWNER_LOCK = 6802449210735


@dataclass(frozen=True)
class MaintenancePolicy:
    devices_per_pass: int = 20
    rows_per_device: int = 100
    interval_seconds: float = 30
    max_backoff_seconds: float = 300
    pass_seconds: float = 5
    statement_timeout_ms: int = 1000
    lock_timeout_ms: int = 200

    def __post_init__(self):
        for value, maximum in ((self.devices_per_pass, 100), (self.rows_per_device, 500),
                               (self.statement_timeout_ms, 5000), (self.lock_timeout_ms, 1000)):
            if type(value) is not int or not 1 <= value <= maximum:
                raise ValueError("INVALID_MAINTENANCE_POLICY")
        for value, lower, upper in ((self.interval_seconds, .05, 3600),
                                    (self.max_backoff_seconds, .05, 3600),
                                    (self.pass_seconds, .05, 30)):
            if type(value) not in (int, float) or not math.isfinite(value) or not lower <= value <= upper:
                raise ValueError("INVALID_MAINTENANCE_POLICY")
        if self.max_backoff_seconds < self.interval_seconds:
            raise ValueError("INVALID_MAINTENANCE_POLICY")


@dataclass(frozen=True)
class PassResult:
    status: str
    devices_seen: int = 0
    devices_cleaned: int = 0
    devices_busy: int = 0
    sources_purged: int = 0
    receipts_pruned: int = 0
    error_code: str | None = None


class _Cancelled(Exception):
    pass


class SourceMaintenance:
    """One owner thread; PostgreSQL also excludes passes from other instances.

    ``run_once`` is safe for an explicit maintenance call without starting a
    thread. ``start``/``stop`` are synchronous: lifespan callers should offload
    stop/join from the ASGI event loop. Repeated start cannot duplicate workers.
    """

    def __init__(self, store: ControlStore, *, policy: MaintenancePolicy = MaintenancePolicy()):
        if not isinstance(store, ControlStore) or not isinstance(policy, MaintenancePolicy):
            raise ValueError("INVALID_MAINTENANCE_CONFIG")
        self.store, self.policy = store, policy
        self._cursor = None
        self._stop = threading.Event()
        self._idle = threading.Event()
        self._idle.set()
        self._pass_lock = threading.Lock()
        self._lifecycle_lock = threading.Lock()
        self._connection_lock = threading.Lock()
        self._connection = None
        self._thread = None
        self._last_result = None
        self._failures = 0
        self._retry_delay = 0

    @property
    def running(self):
        thread = self._thread
        return thread is not None and thread.is_alive()

    @property
    def last_result(self):
        return self._last_result

    @property
    def retry_delay_seconds(self):
        return self._retry_delay

    def _check_cancel(self):
        if self._stop.is_set():
            raise _Cancelled

    def _cancel_connection(self, timeout=1):
        with self._connection_lock:
            conn = self._connection
        if conn is not None and not conn.closed:
            try:
                conn.cancel_safe(timeout=timeout)
            except Exception:
                # Never log or expose a database URL, backend error or query.
                pass

    def run_once(self) -> PassResult:
        if not self._pass_lock.acquire(blocking=False):
            return PassResult("BUSY")
        self._idle.clear()
        try:
            result = self._pass()
            self._last_result = result
            return result
        finally:
            self._idle.set()
            self._pass_lock.release()

    def _pass(self):
        deadline = time.monotonic() + self.policy.pass_seconds
        seen = cleaned = busy = purged = pruned = 0
        cursor, partial = self._cursor, False
        try:
            self._check_cancel()
            with self.store.transaction() as conn:
                with self._connection_lock:
                    self._connection = conn
                try:
                    self._check_cancel()
                    conn.execute("SELECT set_config('statement_timeout',%s,true), set_config('lock_timeout',%s,true)",
                                 (str(self.policy.statement_timeout_ms), str(self.policy.lock_timeout_ms)))
                    owner = conn.execute("SELECT pg_try_advisory_xact_lock(%s) AS owned", (OWNER_LOCK,)).fetchone()
                    if not owner["owned"]:
                        return PassResult("BUSY")
                    devices = conn.execute("""SELECT id,organisation_id FROM aislesignals_control.devices
                        WHERE (%s::uuid IS NULL OR id>%s::uuid) ORDER BY id LIMIT %s""",
                        (cursor, cursor, self.policy.devices_per_pass)).fetchall()
                    if not devices and cursor is not None:
                        cursor = None
                        devices = conn.execute("""SELECT id,organisation_id FROM aislesignals_control.devices
                            ORDER BY id LIMIT %s""", (self.policy.devices_per_pass,)).fetchall()
                    for candidate in devices:
                        self._check_cancel()
                        remaining_ms = int((deadline - time.monotonic()) * 1000)
                        if remaining_ms < 8:
                            partial = True
                            break
                        # A cleanup contains several statements. Tighten their
                        # individual limits as the cooperative pass budget ends.
                        statement_ms = min(self.policy.statement_timeout_ms, max(1, remaining_ms // 8))
                        conn.execute("SELECT set_config('statement_timeout',%s,true), set_config('lock_timeout',%s,true)",
                                     (str(statement_ms), str(min(statement_ms, self.policy.lock_timeout_ms))))
                        seen += 1
                        cursor = candidate["id"]
                        try:
                            with conn.transaction():
                                org = conn.execute("""SELECT id FROM aislesignals_control.organisations
                                    WHERE id=%s FOR SHARE SKIP LOCKED""", (candidate["organisation_id"],)).fetchone()
                                if org is None:
                                    busy += 1
                                    continue
                                current = conn.execute("""SELECT id,organisation_id FROM aislesignals_control.devices
                                    WHERE id=%s AND organisation_id=%s FOR UPDATE SKIP LOCKED""",
                                    (candidate["id"], org["id"])).fetchone()
                                if current is None:
                                    busy += 1
                                    continue
                                self._check_cancel()
                                result = cleanup_device_sources(conn, current, limit=self.policy.rows_per_device)
                                self._check_cancel()
                            cleaned += 1
                            purged += result["sources_purged"]
                            pruned += result["receipts_pruned"]
                        except psycopg.Error:
                            self._check_cancel()
                            # Savepoint rollback preserves earlier devices, and
                            # advancing the cursor keeps a contended one fair.
                            busy += 1
                            partial = True
                    self._check_cancel()
                finally:
                    with self._connection_lock:
                        self._connection = None
            # Do not publish committed counts/cursor until transaction commit.
            self._cursor = cursor
            return PassResult("PARTIAL" if partial else "COMPLETED", seen, cleaned, busy, purged, pruned,
                              "DEVICE_RETRY" if partial else None)
        except _Cancelled:
            return PassResult("CANCELLED")
        except (ControlError, psycopg.Error):
            return PassResult("CANCELLED" if self._stop.is_set() else "UNAVAILABLE",
                              error_code=None if self._stop.is_set() else "DATABASE_UNAVAILABLE")
        except Exception:
            return PassResult("CANCELLED" if self._stop.is_set() else "UNAVAILABLE",
                              error_code=None if self._stop.is_set() else "MAINTENANCE_FAILED")

    def start(self) -> bool:
        with self._lifecycle_lock:
            if self.running or not self._idle.is_set():
                return False
            self._stop.clear()
            self._failures = 0
            self._retry_delay = 0
            self._thread = threading.Thread(target=self._run, name="aislesignals-source-maintenance", daemon=True)
            self._thread.start()
            return True

    def _run(self):
        while not self._stop.is_set():
            result = self.run_once()
            if result.status == "CANCELLED":
                break
            if result.status in {"UNAVAILABLE", "PARTIAL"}:
                self._failures = min(10, self._failures + 1)
                self._retry_delay = min(self.policy.max_backoff_seconds,
                                        self.policy.interval_seconds * 2**self._failures)
            else:
                self._failures = 0
                self._retry_delay = self.policy.interval_seconds
            self._stop.wait(self._retry_delay)

    def stop(self, *, timeout=6) -> bool:
        """Cancel this owner's work; True only when its thread/pass has ended.

        A False result is a shutdown failure, never permission to start another
        worker or close a database underneath its surviving owner.
        """
        if type(timeout) not in (int, float) or not math.isfinite(timeout) or not 0 < timeout <= 30:
            raise ValueError("INVALID_STOP_TIMEOUT")
        deadline = time.monotonic() + timeout
        with self._lifecycle_lock:
            self._stop.set()
            self._cancel_connection(timeout=min(1, timeout))
            thread = self._thread
            if thread is threading.current_thread():
                return False
            if thread is not None:
                thread.join(timeout=max(0, deadline-time.monotonic()))
            ended = self._idle.wait(timeout=max(0, deadline-time.monotonic()))
            return ended and not self.running
