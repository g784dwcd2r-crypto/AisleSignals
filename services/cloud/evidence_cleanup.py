"""Bounded expiry and physical deletion for encrypted cloud evidence."""

from dataclasses import dataclass
import threading
from uuid import uuid4

from .control_operations import SCHEMA
from .evidence_store import EvidenceStoreError


OWNER_LOCK = 6802449210736


@dataclass(frozen=True)
class EvidenceCleanupResult:
    status: str
    revoked: int = 0
    deleted: int = 0
    deferred: int = 0


class EvidenceCleanup:
    """One database-elected worker deletes expired envelopes in bounded passes."""

    def __init__(self, store, service, *, interval_seconds=30, batch_size=25):
        if interval_seconds <= 0 or not 1 <= batch_size <= 100:
            raise ValueError("INVALID_EVIDENCE_CLEANUP_POLICY")
        self.store, self.service = store, service
        self.interval_seconds, self.batch_size = interval_seconds, batch_size
        self._stop = threading.Event()
        self._thread = None
        self._last_result = None

    @property
    def running(self):
        return self._thread is not None and self._thread.is_alive()

    @property
    def last_result(self):
        return self._last_result

    def run_once(self):
        if not self.service.enabled:
            return EvidenceCleanupResult("DISABLED")
        try:
            with self.store.transaction() as conn:
                if not conn.execute("SELECT pg_try_advisory_xact_lock(%s) AS owned", (OWNER_LOCK,)).fetchone()["owned"]:
                    return EvidenceCleanupResult("BUSY")
                due = conn.execute(f"""SELECT * FROM {SCHEMA}.evidence_objects
                    WHERE state IN ('PENDING','AVAILABLE') AND
                    (expires_at<=clock_timestamp() OR
                     (state='PENDING' AND created_at<clock_timestamp()-INTERVAL '15 minutes'))
                    ORDER BY expires_at,created_at,id LIMIT %s FOR UPDATE SKIP LOCKED""",
                    (self.batch_size,)).fetchall()
                for row in due:
                    action = "EVIDENCE_PENDING_EXPIRED" if row["state"] == "PENDING" else "EVIDENCE_EXPIRED"
                    conn.execute(f"UPDATE {SCHEMA}.evidence_objects SET state='REVOKED',revoked_at=clock_timestamp() WHERE id=%s", (row["id"],))
                    conn.execute(f"""INSERT INTO {SCHEMA}.audit_entries
                        (id,organisation_id,actor_user_id,action,subject_id,pharmacy_id)
                        VALUES(%s,%s,NULL,%s,%s,%s)""",
                        (str(uuid4()), row["organisation_id"], action, row["id"], row["pharmacy_id"]))
                revoked = conn.execute(f"""SELECT * FROM {SCHEMA}.evidence_objects WHERE state='REVOKED'
                    AND (delete_retry_at IS NULL OR delete_retry_at<=clock_timestamp())
                    ORDER BY COALESCE(delete_retry_at,revoked_at),revoked_at,id LIMIT %s""",
                    (self.batch_size,)).fetchall()
                candidates = [dict(row) for row in revoked]

            removed = []
            failed = []
            for row in candidates:
                try:
                    self.service.store.delete(row["object_key"])
                    removed.append(row)
                except EvidenceStoreError:
                    failed.append(row)

            deleted = 0
            if removed or failed:
                with self.store.transaction() as conn:
                    for row in removed:
                        changed = conn.execute(f"""UPDATE {SCHEMA}.evidence_objects
                            SET state='DELETED',deleted_at=clock_timestamp()
                            WHERE id=%s AND organisation_id=%s AND pharmacy_id=%s AND state='REVOKED'
                            RETURNING id""", (row["id"], row["organisation_id"], row["pharmacy_id"])).fetchone()
                        if changed is None:
                            continue
                        conn.execute(f"""INSERT INTO {SCHEMA}.audit_entries
                            (id,organisation_id,actor_user_id,action,subject_id,pharmacy_id)
                            VALUES(%s,%s,NULL,'EVIDENCE_BLOB_DELETED',%s,%s)""",
                            (str(uuid4()), row["organisation_id"], row["id"], row["pharmacy_id"]))
                        deleted += 1
                    for row in failed:
                        changed = conn.execute(f"""UPDATE {SCHEMA}.evidence_objects
                            SET delete_attempts=LEAST(delete_attempts+1,1000000),
                                delete_error_at=clock_timestamp(),
                                delete_retry_at=clock_timestamp()+make_interval(
                                    secs=>LEAST(3600,CAST(power(2,LEAST(delete_attempts,12)) AS integer)))
                            WHERE id=%s AND organisation_id=%s AND pharmacy_id=%s AND state='REVOKED'
                            RETURNING id""", (row["id"], row["organisation_id"], row["pharmacy_id"])).fetchone()
                        if changed is not None:
                            conn.execute(f"""INSERT INTO {SCHEMA}.audit_entries
                                (id,organisation_id,actor_user_id,action,subject_id,pharmacy_id)
                                VALUES(%s,%s,NULL,'EVIDENCE_BLOB_DELETE_RETRY',%s,%s)""",
                                (str(uuid4()), row["organisation_id"], row["id"], row["pharmacy_id"]))
            return EvidenceCleanupResult("COMPLETED", len(due), deleted, len(failed))
        except Exception:
            # The worker exposes only a stable status; it never leaks storage or
            # database details. A later pass retries idempotent physical deletes.
            return EvidenceCleanupResult("UNAVAILABLE")

    def start(self):
        if not self.service.enabled:
            return True
        if self.running:
            return False
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="aislesignals-evidence-cleanup", daemon=True)
        self._thread.start()
        return True

    def _run(self):
        # Waiting first keeps startup bounded; readiness independently verifies
        # the exact schema before traffic is admitted.
        while not self._stop.wait(self.interval_seconds):
            self._last_result = self.run_once()

    def stop(self, *, timeout=6):
        self._stop.set()
        if self._thread is not None and self._thread is not threading.current_thread():
            self._thread.join(timeout)
        return not self.running
