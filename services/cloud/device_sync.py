"""Explicit short-lived observation metadata and device-bound withdrawal.

No worker, media upload or local authority is created here. Register the router
only with the existing cloud request/security boundary. Cleanup callers must
hold the organisation then device lock, as device_transaction does.
"""

from datetime import datetime, timedelta, timezone
import re
from typing import Literal
from uuid import UUID, uuid4

from fastapi import APIRouter, Request
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, StrictBool, field_validator, model_validator

from .control_operations import LABELS, SCHEMA, device_transaction, fail, now, payload_hash

DAILY_INTAKE_LIMIT = 1000
DAILY_UNKNOWN_WITHDRAWAL_LIMIT = 1000
MAX_RECEIPTS = 64000
REPLAY_DAYS = 31


class SourceKey(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=False)
    source_event_id: UUID

    @field_validator("source_event_id", mode="before")
    @classmethod
    def canonical_uuid(cls, value):
        if not isinstance(value, str) or str(UUID(value)) != value:
            raise ValueError("Use a canonical source event identifier.")
        return value


class Observation(SourceKey):
    event_code: Literal["POSSIBLE_CONCEALMENT", "POSSIBLE_PRODUCT_TAKE", "POSSIBLE_PRODUCT_RETURN", "REPEATED_HAND_TO_WAIST", "RESTRICTED_ZONE_ENTRY", "CAMERA_UNAVAILABLE", "MODEL_UNAVAILABLE"]
    source_label: str = Field(min_length=1, max_length=120)
    occurred_at: AwareDatetime
    historical: StrictBool
    expires_at: AwareDatetime

    @field_validator("source_label")
    @classmethod
    def label(cls, value):
        if not value.strip() or any(ord(c) < 32 or ord(c) == 127 for c in value):
            raise ValueError("Use a printable camera label.")
        return value

    @field_validator("occurred_at", "expires_at", mode="before")
    @classmethod
    def timestamp(cls, value):
        if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|[+-]\d{2}:\d{2})", value):
            raise ValueError("Use an aware ISO timestamp.")
        try:
            stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
            if stamp.utcoffset() is None:
                raise ValueError
            return stamp.astimezone(timezone.utc)
        except (ValueError, OverflowError):
            raise ValueError("Use an aware ISO timestamp.")

    @model_validator(mode="after")
    def lifetime(self):
        if self.historical is not True or not timedelta(0) < self.expires_at-self.occurred_at <= timedelta(hours=24):
            raise ValueError("Only historical observations with at most 24 hours of source availability are accepted.")
        return self


class Withdrawal(SourceKey):
    reason: Literal["LOCAL_DELETED", "LOCAL_EXPIRED", "LOCAL_EXPORT_REMOVED"]


def source_state(row, stamp):
    if row["withdrawn_at"] is not None:
        return "WITHDRAWN"
    if row["expires_at"] is not None and row["expires_at"] <= stamp:
        return "EXPIRED"
    return "AVAILABLE"


def _purge_alerts(conn, device_id, ids):
    if not ids:
        return 0
    # Review notes/outcome/identity and separately authored incidents stay intact.
    conn.execute(f"""UPDATE {SCHEMA}.alerts SET event_code=NULL,title=NULL,source_label=NULL,
        occurred_at=NULL,source_purged=true WHERE device_id=%s AND id=ANY(%s::uuid[])""", (device_id, ids))
    conn.execute(f"""DELETE FROM {SCHEMA}.alerts a WHERE device_id=%s AND id=ANY(%s::uuid[])
        AND review_outcome IS NULL AND review_note IS NULL
        AND NOT EXISTS(SELECT 1 FROM {SCHEMA}.incidents i WHERE i.alert_id=a.id)""", (device_id, ids))
    return len(ids)


def cleanup_device_sources(conn, device, *, limit=100):
    """One bounded pass under the existing organisation/device lock.

    Staff reads do not depend on cleanup: unavailable sources are filtered by
    database wall clock. A scheduler can call this helper for a locked device;
    this module does not start a background service.
    """
    if type(limit) is not int or not 1 <= limit <= 500:
        raise ValueError("Use a bounded cleanup batch.")
    rows = conn.execute(f"""SELECT id FROM {SCHEMA}.alerts WHERE device_id=%s
        AND NOT source_purged AND (source_withdrawn_at IS NOT NULL OR source_expires_at<=clock_timestamp())
        ORDER BY received_at,id LIMIT %s FOR UPDATE""", (device["id"], limit)).fetchall()
    purged = _purge_alerts(conn, device["id"], [row["id"] for row in rows])
    deleted = conn.execute(f"""WITH expired AS (
        SELECT device_id,source_event_id FROM {SCHEMA}.device_sync_receipts WHERE device_id=%s
        AND COALESCE(withdrawn_at,expires_at)<clock_timestamp()-INTERVAL '31 days'
        ORDER BY created_at,source_event_id LIMIT %s)
        DELETE FROM {SCHEMA}.device_sync_receipts r USING expired e
        WHERE r.device_id=e.device_id AND r.source_event_id=e.source_event_id RETURNING r.source_event_id""", (device["id"], limit)).fetchall()
    return {"sources_purged": purged, "receipts_pruned": len(deleted)}


def _quota(conn, device_id, *, withdrawal=False):
    count = conn.execute(f"SELECT count(*) AS n FROM {SCHEMA}.device_sync_receipts WHERE device_id=%s", (device_id,)).fetchone()["n"]
    if count >= MAX_RECEIPTS:
        fail(429, "SYNC_LIMIT", "The source synchronization limit has been reached.")
    if withdrawal:
        count = conn.execute(f"""SELECT count(*) AS n FROM {SCHEMA}.device_sync_receipts
            WHERE device_id=%s AND created_at>clock_timestamp()-INTERVAL '1 day' AND withdrawal_first=true""", (device_id,)).fetchone()["n"]
        limit = DAILY_UNKNOWN_WITHDRAWAL_LIMIT
    else:
        count = conn.execute(f"""SELECT count(*) AS n FROM (
            SELECT source_event_id FROM {SCHEMA}.device_sync_receipts WHERE device_id=%s AND created_at>clock_timestamp()-INTERVAL '1 day'
            UNION SELECT source_event_id FROM {SCHEMA}.alerts WHERE device_id=%s AND received_at>clock_timestamp()-INTERVAL '1 day') recent""", (device_id, device_id)).fetchone()["n"]
        limit = DAILY_INTAKE_LIMIT
    if count >= limit:
        fail(429, "SYNC_LIMIT", "The daily source synchronization limit has been reached.")


def create_device_sync_router():
    router = APIRouter(prefix="/device-api/sync/v1")

    @router.post("/observations", status_code=201)
    def observation(body: Observation, request: Request):
        if body.occurred_at > now() + timedelta(minutes=5):
            fail(422, "INVALID_EVENT_TIME", "The laptop capture time is too far in the future.")
        with device_transaction(request) as (conn, device):
            stamp, digest = now(), payload_hash(body)
            previous = conn.execute(f"SELECT * FROM {SCHEMA}.device_sync_receipts WHERE device_id=%s AND source_event_id=%s", (device["id"], body.source_event_id)).fetchone()
            if previous is not None:
                if previous["payload_hash"] is not None and previous["payload_hash"] != digest:
                    fail(409, "EVENT_CONFLICT", "This source identifier was already received with different content.")
                if previous["payload_hash"] is None:
                    conn.execute(f"UPDATE {SCHEMA}.device_sync_receipts SET payload_hash=%s,expires_at=%s WHERE device_id=%s AND source_event_id=%s", (digest, body.expires_at, device["id"], body.source_event_id))
                result = {"id": previous["receipt_id"], "received": True, "source_state": source_state(previous, stamp)}
                cleanup_device_sources(conn, device)
                return result
            legacy = conn.execute(f"SELECT * FROM {SCHEMA}.alerts WHERE device_id=%s AND source_event_id=%s", (device["id"], body.source_event_id)).fetchone()
            if legacy is not None:
                if legacy["timestamp_basis"] != "LAPTOP_REPORTED" or legacy["payload_hash"] != digest:
                    fail(409, "EVENT_CONFLICT", "This source identifier is already bound to another observation.")
                return {"id": legacy["id"], "received": True, "source_state": "WITHDRAWN" if legacy["source_withdrawn_at"] else "EXPIRED"}
            if body.occurred_at < stamp - timedelta(days=30):
                fail(422, "INVALID_EVENT_TIME", "This observation is outside the source replay window.")
            cleanup_device_sources(conn, device)
            _quota(conn, device["id"])
            identifier = str(uuid4())
            conn.execute(f"""INSERT INTO {SCHEMA}.device_sync_receipts
                (device_id,source_event_id,organisation_id,pharmacy_id,receipt_id,payload_hash,expires_at)
                VALUES (%s,%s,%s,%s,%s,%s,%s)""", (device["id"], body.source_event_id, device["organisation_id"], device["pharmacy_id"], identifier, digest, body.expires_at))
            available = body.expires_at > stamp
            if available:
                conn.execute(f"""INSERT INTO {SCHEMA}.alerts
                    (id,organisation_id,pharmacy_id,device_id,source_event_id,payload_hash,event_code,title,source_label,occurred_at,historical,source_expires_at,timestamp_basis)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,true,%s,'LAPTOP_REPORTED')""", (identifier, device["organisation_id"], device["pharmacy_id"], device["id"], body.source_event_id, digest, body.event_code, LABELS[body.event_code], body.source_label, body.occurred_at, body.expires_at))
            return {"id": identifier, "received": True, "source_state": "AVAILABLE" if available else "EXPIRED"}

    @router.post("/withdrawals")
    def withdrawal(body: Withdrawal, request: Request):
        with device_transaction(request) as (conn, device):
            row = conn.execute(f"SELECT * FROM {SCHEMA}.device_sync_receipts WHERE device_id=%s AND source_event_id=%s", (device["id"], body.source_event_id)).fetchone()
            alert = conn.execute(f"SELECT id FROM {SCHEMA}.alerts WHERE device_id=%s AND source_event_id=%s FOR UPDATE", (device["id"], body.source_event_id)).fetchone()
            stamp = now()
            if row is None:
                if alert is None:
                    cleanup_device_sources(conn, device)
                    _quota(conn, device["id"], withdrawal=True)
                conn.execute(f"""INSERT INTO {SCHEMA}.device_sync_receipts
                    (device_id,source_event_id,organisation_id,pharmacy_id,receipt_id,withdrawn_at,withdrawal_reason,withdrawal_first)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,true)""", (device["id"], body.source_event_id, device["organisation_id"], device["pharmacy_id"], alert["id"] if alert else str(uuid4()), stamp, body.reason))
            elif row["withdrawn_at"] is None:
                conn.execute(f"UPDATE {SCHEMA}.device_sync_receipts SET withdrawn_at=%s,withdrawal_reason=%s WHERE device_id=%s AND source_event_id=%s", (stamp, body.reason, device["id"], body.source_event_id))
            if alert is not None:
                conn.execute(f"UPDATE {SCHEMA}.alerts SET source_withdrawn_at=COALESCE(source_withdrawn_at,%s) WHERE id=%s", (stamp, alert["id"]))
                _purge_alerts(conn, device["id"], [alert["id"]])
            cleanup_device_sources(conn, device)
            # Repeated reasons do not renew retention or rewrite the first reason.
            return {"source_event_id": body.source_event_id, "withdrawn": True}

    return router
