"""Scoped pharmacy operations and explicit metadata-only laptop connections."""

from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
import secrets
from typing import Literal
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, Request
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, StrictBool, StrictInt, field_validator

from .control_store import ControlError, audit, require_principal, validate_principal

SCHEMA = "aislesignals_control"
LABELS = {
    "POSSIBLE_CONCEALMENT": "Possible concealment — review needed",
    "POSSIBLE_PRODUCT_TAKE": "Possible product pickup — review needed",
    "POSSIBLE_PRODUCT_RETURN": "Possible product return — review needed",
    "REPEATED_HAND_TO_WAIST": "Repeated hand-to-waist movement",
    "RESTRICTED_ZONE_ENTRY": "Restricted zone entry",
    "CAMERA_UNAVAILABLE": "Camera coverage unavailable",
    "MODEL_UNAVAILABLE": "Local model unavailable",
}


def now():
    return datetime.now(timezone.utc)


def fail(status, code, message):
    raise ControlError(status, code, message)


def fingerprint(value):
    return sha256(value.encode()).hexdigest()


def payload_hash(body):
    return fingerprint(json.dumps(body.model_dump(mode="json"), sort_keys=True, separators=(",", ":")))


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    @field_validator("*", mode="after")
    @classmethod
    def printable(cls, value):
        if isinstance(value, str) and any(ord(character) < 32 and character not in "\n\t" for character in value):
            raise ValueError("Use printable text.")
        return value


class Enrolment(Input):
    pharmacy_id: UUID
    name: str = Field(min_length=1, max_length=100)
    platform: Literal["MACOS", "WINDOWS", "OTHER"]


class DeviceEnrol(Input):
    token: str = Field(min_length=32, max_length=128)
    name: str = Field(min_length=1, max_length=100)
    platform: Literal["MACOS", "WINDOWS", "OTHER"]
    app_version: str = Field(min_length=1, max_length=64)


class Version(Input):
    expected_version: StrictInt = Field(ge=1)


class Review(Version):
    outcome: Literal["NORMAL_SHOPPING", "UNCLEAR", "SUSPECTED_INCIDENT"]
    note: str = Field(min_length=1, max_length=2000)
    create_incident: StrictBool
    title: str | None = Field(default=None, min_length=1, max_length=160)


class IncidentPatch(Version):
    status: Literal["OPEN", "CLOSED"] | None = None
    notes: str | None = Field(default=None, min_length=1, max_length=4000)


class Heartbeat(Input):
    sequence: StrictInt = Field(ge=0, le=9007199254740991)
    monitoring_status: Literal["ACTIVE", "STOPPED", "DEGRADED", "UNKNOWN"]
    camera_count: StrictInt = Field(ge=0, le=64)
    app_version: str = Field(min_length=1, max_length=64)


class DeviceAlert(Input):
    source_event_id: UUID
    event_code: Literal["POSSIBLE_CONCEALMENT", "POSSIBLE_PRODUCT_TAKE", "POSSIBLE_PRODUCT_RETURN", "REPEATED_HAND_TO_WAIST", "RESTRICTED_ZONE_ENTRY", "CAMERA_UNAVAILABLE", "MODEL_UNAVAILABLE"]
    source_label: str = Field(min_length=1, max_length=120)
    occurred_at: AwareDatetime
    historical: StrictBool


def pharmacy_ids(conn, principal, selected=None):
    rows = conn.execute(
        f"SELECT id FROM {SCHEMA}.pharmacies WHERE organisation_id=%s AND active=true ORDER BY name",
        (principal.organisation_id,),
    ).fetchall()
    permitted = [str(row["id"]) for row in rows if principal.role == "OWNER" or str(row["id"]) in principal.pharmacy_ids]
    if selected is not None:
        if str(selected) not in permitted:
            fail(403, "PHARMACY_ACCESS_DENIED", "This pharmacy is not available to your account.")
        return [str(selected)]
    return permitted


def version_matches(row, expected):
    if row["version"] != expected:
        fail(409, "VERSION_CONFLICT", "This record changed. Refresh it before saving again.")


def manage(principal):
    if principal.role not in {"OWNER", "MANAGER"}:
        fail(403, "MANAGER_REQUIRED", "A pharmacy manager must perform this action.")


def device_view(row):
    state = "REVOKED" if row["revoked_at"] else "NEVER_CONNECTED" if row["last_seen_at"] is None else "ONLINE" if now() - row["last_seen_at"] <= timedelta(seconds=120) else "OFFLINE"
    return {
        **{key: row[key] for key in ("id", "pharmacy_id", "pharmacy_name", "name", "platform", "app_version", "last_seen_at", "camera_count", "version", "revoked_at")},
        "connection_status": state,
        "monitoring_status": row["monitoring_status"] if state == "ONLINE" else "UNKNOWN",
    }


def alert_view(row):
    return {
        **{key: row[key] for key in ("id", "pharmacy_id", "pharmacy_name", "device_id", "device_name", "source_event_id", "event_code", "title", "source_label", "occurred_at", "received_at", "historical", "status", "version", "incident_id", "timestamp_basis", "source_expires_at")},
        "review": None if row["review_outcome"] is None else {"outcome": row["review_outcome"], "note": row["review_note"], "by": row["reviewed_by"], "at": row["reviewed_at"]},
    }


def incident_view(row):
    return {key: row[key] for key in ("id", "pharmacy_id", "pharmacy_name", "alert_id", "title", "classification", "status", "notes", "reviewed_by", "reviewed_at", "created_at", "version", "source_unavailable")}


def source_available_sql(alias="r"):
    # Fixed internal aliases only. Use wall clock rather than transaction start
    # time so a request delayed by a lock cannot revive an expired source.
    prefix = alias + "." if alias else ""
    return f"({prefix}source_withdrawn_at IS NULL AND NOT {prefix}source_purged AND ({prefix}source_expires_at IS NULL OR {prefix}source_expires_at>clock_timestamp()))"


def select_rows(conn, principal, kind, selected=None, record_id=None, status=None, limit=200):
    permitted = pharmacy_ids(conn, principal, selected)
    if kind == "devices":
        columns, joins, order, view = "r.*, p.name AS pharmacy_name", "", "r.created_at", device_view
    elif kind == "alerts":
        columns, joins, order, view = "r.*, p.name AS pharmacy_name, d.name AS device_name, i.id AS incident_id", f"JOIN {SCHEMA}.devices d ON d.id=r.device_id AND d.organisation_id=r.organisation_id LEFT JOIN {SCHEMA}.incidents i ON i.alert_id=r.id AND i.organisation_id=r.organisation_id", "r.received_at", alert_view
    else:
        columns, joins, order, view = f"r.*, p.name AS pharmacy_name, NOT {source_available_sql('a')} AS source_unavailable", f"JOIN {SCHEMA}.alerts a ON a.id=r.alert_id AND a.organisation_id=r.organisation_id AND a.pharmacy_id=r.pharmacy_id", "r.created_at", incident_view
    params = [principal.organisation_id, permitted]
    where = "r.organisation_id=%s AND r.pharmacy_id=ANY(%s::uuid[])"
    if kind == "alerts":
        where += " AND " + source_available_sql()
    if record_id is not None:
        where += " AND r.id=%s"
        params.append(record_id)
    if status is not None:
        where += " AND r.status=%s"
        params.append(status)
    rows = conn.execute(f"SELECT {columns} FROM {SCHEMA}.{kind} r JOIN {SCHEMA}.pharmacies p ON p.id=r.pharmacy_id AND p.organisation_id=r.organisation_id {joins} WHERE {where} ORDER BY {order} DESC,r.id LIMIT %s", (*params, limit)).fetchall()
    if record_id is not None and not rows:
        fail(404, "NOT_FOUND", "This record is not available.")
    return [view(row) for row in rows]


def locked_record(conn, principal, kind, record_id):
    permitted = pharmacy_ids(conn, principal)
    row = conn.execute(f"SELECT * FROM {SCHEMA}.{kind} WHERE organisation_id=%s AND pharmacy_id=ANY(%s::uuid[]) AND id=%s FOR UPDATE", (principal.organisation_id, permitted, record_id)).fetchone()
    if row is None:
        fail(404, "NOT_FOUND", "This record is not available.")
    if kind == "alerts" and (row["source_withdrawn_at"] is not None or row["source_purged"] or
                             (row["source_expires_at"] is not None and row["source_expires_at"] <= now())):
        fail(404, "NOT_FOUND", "This record is not available.")
    return row


@contextmanager
def transaction(request, principal):
    with request.app.state.control_store.transaction() as conn:
        yield conn, validate_principal(conn, principal)


@contextmanager
def device_transaction(request):
    header = request.headers.get("authorization", "")
    if not header.startswith("Bearer ") or not 32 <= len(header[7:]) <= 128:
        fail(401, "DEVICE_UNAUTHORIZED", "Connect this laptop using a new enrollment code.")
    with request.app.state.control_store.transaction() as conn:
        match = conn.execute(f"SELECT organisation_id FROM {SCHEMA}.devices WHERE credential_hash=%s", (fingerprint(header[7:]),)).fetchone()
        if match is None:
            fail(401, "DEVICE_UNAUTHORIZED", "Connect this laptop using a new enrollment code.")
        # Same lock order as account revocation: organisation, then child rows.
        conn.execute(f"SELECT id FROM {SCHEMA}.organisations WHERE id=%s FOR SHARE", (match["organisation_id"],)).fetchone()
        row = conn.execute(f"SELECT d.* FROM {SCHEMA}.devices d JOIN {SCHEMA}.pharmacies p ON p.id=d.pharmacy_id AND p.organisation_id=d.organisation_id WHERE d.credential_hash=%s AND d.revoked_at IS NULL AND p.active=true FOR UPDATE OF d", (fingerprint(header[7:]),)).fetchone()
        if row is None:
            fail(401, "DEVICE_UNAUTHORIZED", "Connect this laptop using a new enrollment code.")
        yield conn, row


def create_operations_router():
    router = APIRouter(prefix="/control-api")

    @router.get("/dashboard")
    def dashboard(request: Request, pharmacy_id: UUID | None = None, principal=Depends(require_principal)):
        with transaction(request, principal) as (conn, current):
            permitted = pharmacy_ids(conn, current, pharmacy_id)
            counts = conn.execute(f"""SELECT
                (SELECT count(*) FROM {SCHEMA}.devices WHERE organisation_id=%s AND pharmacy_id=ANY(%s::uuid[]) AND revoked_at IS NULL) AS total_laptops,
                (SELECT count(*) FROM {SCHEMA}.devices WHERE organisation_id=%s AND pharmacy_id=ANY(%s::uuid[]) AND revoked_at IS NULL AND last_seen_at>CURRENT_TIMESTAMP-INTERVAL '120 seconds') AS connected_laptops,
                (SELECT count(*) FROM {SCHEMA}.alerts WHERE organisation_id=%s AND pharmacy_id=ANY(%s::uuid[]) AND status<>'REVIEWED' AND {source_available_sql('')}) AS open_alerts,
                (SELECT count(*) FROM {SCHEMA}.incidents WHERE organisation_id=%s AND pharmacy_id=ANY(%s::uuid[])) AS reviewed_incidents""", tuple([current.organisation_id, permitted] * 4)).fetchone()
            return {"generated_at": now(), "summary": {"pharmacies": len(permitted), **counts},
                    **{kind: select_rows(conn, current, kind, pharmacy_id, limit=8) for kind in ("devices", "alerts", "incidents")}}

    @router.get("/devices")
    def devices(request: Request, pharmacy_id: UUID | None = None, principal=Depends(require_principal)):
        with transaction(request, principal) as (conn, current):
            return {"items": select_rows(conn, current, "devices", pharmacy_id)}

    @router.post("/devices/enrolments", status_code=201)
    def enrolment(body: Enrolment, request: Request, principal=Depends(require_principal)):
        with transaction(request, principal) as (conn, current):
            manage(current)
            pharmacy_ids(conn, current, body.pharmacy_id)
            # Serialize enrollment limits for the selected branch.
            conn.execute(f"SELECT id FROM {SCHEMA}.pharmacies WHERE id=%s FOR UPDATE", (body.pharmacy_id,))
            count = conn.execute(f"SELECT count(*) AS count FROM {SCHEMA}.device_enrolments WHERE pharmacy_id=%s AND expires_at>CURRENT_TIMESTAMP AND consumed_at IS NULL", (body.pharmacy_id,)).fetchone()["count"]
            if count >= 10:
                fail(429, "ENROLMENT_LIMIT", "Wait for an existing enrollment code to expire before creating another.")
            token, identifier, expires = secrets.token_urlsafe(32), str(uuid4()), now() + timedelta(minutes=10)
            conn.execute(f"INSERT INTO {SCHEMA}.device_enrolments(id,organisation_id,pharmacy_id,name,platform,token_hash,created_by,expires_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)", (identifier, current.organisation_id, body.pharmacy_id, body.name, body.platform, fingerprint(token), current.user_id, expires))
            audit(conn, current, "DEVICE_ENROLMENT_CREATED", identifier, str(body.pharmacy_id))
            return {"token": token, "expires_at": expires}

    @router.post("/devices/{device_id}/revoke")
    def revoke(device_id: UUID, body: Version, request: Request, principal=Depends(require_principal)):
        with transaction(request, principal) as (conn, current):
            manage(current)
            row = locked_record(conn, current, "devices", device_id)
            version_matches(row, body.expected_version)
            if row["revoked_at"] is None:
                conn.execute(f"UPDATE {SCHEMA}.devices SET revoked_at=CURRENT_TIMESTAMP,version=version+1 WHERE id=%s", (device_id,))
                audit(conn, current, "DEVICE_REVOKED", str(device_id), str(row["pharmacy_id"]))
            return select_rows(conn, current, "devices", record_id=device_id)[0]

    @router.get("/alerts")
    def alerts(request: Request, pharmacy_id: UUID | None = None, status: Literal["OPEN", "ACKNOWLEDGED", "REVIEWED"] | None = None, principal=Depends(require_principal)):
        with transaction(request, principal) as (conn, current):
            return {"items": select_rows(conn, current, "alerts", pharmacy_id, status=status)}

    @router.get("/alerts/{alert_id}")
    def alert(alert_id: UUID, request: Request, principal=Depends(require_principal)):
        with transaction(request, principal) as (conn, current):
            return select_rows(conn, current, "alerts", record_id=alert_id)[0]

    @router.post("/alerts/{alert_id}/acknowledge")
    def acknowledge(alert_id: UUID, body: Version, request: Request, principal=Depends(require_principal)):
        with transaction(request, principal) as (conn, current):
            row = locked_record(conn, current, "alerts", alert_id)
            version_matches(row, body.expected_version)
            if row["status"] == "OPEN":
                conn.execute(f"UPDATE {SCHEMA}.alerts SET status='ACKNOWLEDGED',version=version+1 WHERE id=%s", (alert_id,))
                audit(conn, current, "ALERT_ACKNOWLEDGED", str(alert_id), str(row["pharmacy_id"]))
            return select_rows(conn, current, "alerts", record_id=alert_id)[0]

    @router.post("/alerts/{alert_id}/review")
    def review(alert_id: UUID, body: Review, request: Request, principal=Depends(require_principal)):
        with transaction(request, principal) as (conn, current):
            row = locked_record(conn, current, "alerts", alert_id)
            version_matches(row, body.expected_version)
            if row["status"] == "REVIEWED":
                fail(409, "ALREADY_REVIEWED", "This alert has already been reviewed.")
            reviewer = conn.execute(f"SELECT name FROM {SCHEMA}.users WHERE id=%s", (current.user_id,)).fetchone()["name"]
            reviewed_at, incident_id = now(), None
            conn.execute(f"UPDATE {SCHEMA}.alerts SET status='REVIEWED',version=version+1,review_outcome=%s,review_note=%s,reviewed_by=%s,reviewed_at=%s WHERE id=%s", (body.outcome, body.note, reviewer, reviewed_at, alert_id))
            if body.create_incident:
                incident_id = str(uuid4())
                conn.execute(f"INSERT INTO {SCHEMA}.incidents(id,organisation_id,pharmacy_id,alert_id,title,classification,notes,reviewed_by,reviewed_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)", (incident_id, current.organisation_id, row["pharmacy_id"], alert_id, body.title or row["title"], body.outcome, body.note, reviewer, reviewed_at))
                audit(conn, current, "REVIEWED_INCIDENT_CREATED", incident_id, str(row["pharmacy_id"]))
            audit(conn, current, "ALERT_REVIEWED", str(alert_id), str(row["pharmacy_id"]))
            return {"alert": select_rows(conn, current, "alerts", record_id=alert_id)[0], "incident": select_rows(conn, current, "incidents", record_id=incident_id)[0] if incident_id else None}

    @router.get("/incidents")
    def incidents(request: Request, pharmacy_id: UUID | None = None, principal=Depends(require_principal)):
        with transaction(request, principal) as (conn, current):
            return {"items": select_rows(conn, current, "incidents", pharmacy_id)}

    @router.get("/incidents/{incident_id}")
    def incident(incident_id: UUID, request: Request, principal=Depends(require_principal)):
        with transaction(request, principal) as (conn, current):
            return select_rows(conn, current, "incidents", record_id=incident_id)[0]

    @router.patch("/incidents/{incident_id}")
    def change_incident(incident_id: UUID, body: IncidentPatch, request: Request, principal=Depends(require_principal)):
        if body.status is None and body.notes is None:
            fail(422, "EMPTY_CHANGE", "Choose a status or add review notes.")
        with transaction(request, principal) as (conn, current):
            row = locked_record(conn, current, "incidents", incident_id)
            version_matches(row, body.expected_version)
            conn.execute(f"UPDATE {SCHEMA}.incidents SET status=%s,notes=%s,version=version+1 WHERE id=%s", (body.status or row["status"], body.notes or row["notes"], incident_id))
            audit(conn, current, "INCIDENT_UPDATED", str(incident_id), str(row["pharmacy_id"]))
            return select_rows(conn, current, "incidents", record_id=incident_id)[0]

    return router


def create_device_router():
    router = APIRouter(prefix="/device-api")

    @router.get("/identity")
    def identity(request: Request):
        # Reuse intake authority and lock order. This read is not a heartbeat:
        # it never refreshes monitoring, device activity or a staff session.
        with device_transaction(request) as (conn, device):
            target = conn.execute(
                "SELECT o.name AS organisation_name,p.name AS pharmacy_name "
                f"FROM {SCHEMA}.organisations o JOIN {SCHEMA}.pharmacies p ON p.organisation_id=o.id "
                "WHERE o.id=%s AND p.id=%s AND p.active=true",
                (device["organisation_id"], device["pharmacy_id"]),
            ).fetchone()
            if target is None:
                fail(401, "DEVICE_UNAUTHORIZED", "Connect this laptop using a new enrollment code.")
            return {
                "device_id": device["id"], "organisation_id": device["organisation_id"],
                "organisation_name": target["organisation_name"], "pharmacy_id": device["pharmacy_id"],
                "pharmacy_name": target["pharmacy_name"], "name": device["name"],
                "platform": device["platform"], "app_version": device["app_version"],
            }

    @router.post("/enrol", status_code=201)
    def enrol(body: DeviceEnrol, request: Request):
        with request.app.state.control_store.transaction() as conn:
            match = conn.execute(f"SELECT organisation_id FROM {SCHEMA}.device_enrolments WHERE token_hash=%s", (fingerprint(body.token),)).fetchone()
            if match is None:
                fail(401, "ENROLMENT_INVALID", "This enrollment code is invalid or expired.")
            conn.execute(f"SELECT id FROM {SCHEMA}.organisations WHERE id=%s FOR SHARE", (match["organisation_id"],))
            row = conn.execute(f"SELECT e.* FROM {SCHEMA}.device_enrolments e JOIN {SCHEMA}.pharmacies p ON p.id=e.pharmacy_id AND p.organisation_id=e.organisation_id JOIN {SCHEMA}.users u ON u.id=e.created_by AND u.organisation_id=e.organisation_id WHERE e.token_hash=%s AND e.consumed_at IS NULL AND e.expires_at>CURRENT_TIMESTAMP AND p.active=true AND u.active=true AND (u.role='OWNER' OR (u.role='MANAGER' AND EXISTS(SELECT 1 FROM {SCHEMA}.user_pharmacies up WHERE up.user_id=u.id AND up.pharmacy_id=e.pharmacy_id))) FOR UPDATE OF e", (fingerprint(body.token),)).fetchone()
            if row is None or row["platform"] != body.platform or row["name"] != body.name:
                fail(401, "ENROLMENT_INVALID", "This enrollment code is invalid or does not match the laptop details.")
            identifier, credential = str(uuid4()), secrets.token_urlsafe(48)
            conn.execute(f"UPDATE {SCHEMA}.device_enrolments SET consumed_at=CURRENT_TIMESTAMP WHERE id=%s", (row["id"],))
            conn.execute(f"INSERT INTO {SCHEMA}.devices(id,organisation_id,pharmacy_id,name,platform,credential_hash,app_version) VALUES (%s,%s,%s,%s,%s,%s,%s)", (identifier, row["organisation_id"], row["pharmacy_id"], body.name, body.platform, fingerprint(credential), body.app_version))
            # No credential is logged or stored in plaintext by the service.
            conn.execute(f"INSERT INTO {SCHEMA}.audit_entries(id,organisation_id,actor_user_id,action,subject_id,pharmacy_id) VALUES (%s,%s,NULL,'DEVICE_ENROLLED',%s,%s)", (str(uuid4()), row["organisation_id"], identifier, row["pharmacy_id"]))
            return {"device_id": identifier, "device_token": credential, "heartbeat_interval_seconds": 30}

    @router.post("/heartbeat")
    def heartbeat(body: Heartbeat, request: Request):
        if body.monitoring_status == "ACTIVE" and body.camera_count == 0:
            fail(422, "INVALID_COVERAGE", "Active monitoring requires at least one reported camera.")
        with device_transaction(request) as (conn, device):
            digest = payload_hash(body)
            if body.sequence < device["heartbeat_sequence"]:
                fail(409, "OLD_HEARTBEAT", "This heartbeat is older than the last accepted update.")
            if body.sequence == device["heartbeat_sequence"]:
                if digest != device["heartbeat_hash"]:
                    fail(409, "HEARTBEAT_CONFLICT", "This heartbeat sequence was already used with different content.")
                return {"ok": True, "server_time": device["last_seen_at"]}
            stamp = now()
            conn.execute(f"UPDATE {SCHEMA}.devices SET last_seen_at=%s,heartbeat_sequence=%s,heartbeat_hash=%s,monitoring_status=%s,camera_count=%s,app_version=%s WHERE id=%s", (stamp, body.sequence, digest, body.monitoring_status, body.camera_count, body.app_version, device["id"]))
            return {"ok": True, "server_time": stamp}

    @router.post("/alerts", status_code=201)
    def receive_alert(body: DeviceAlert, request: Request):
        captured = body.occurred_at.astimezone(timezone.utc)
        if captured > now() + timedelta(minutes=5) or captured < now() - timedelta(days=30):
            fail(422, "INVALID_EVENT_TIME", "Use a recent observation with a valid capture time.")
        with device_transaction(request) as (conn, device):
            # A v1 withdrawal/expiry cannot be bypassed through legacy intake.
            # Never return staff data or refresh an unavailable source here.
            receipt = conn.execute(f"SELECT 1 FROM {SCHEMA}.device_sync_receipts WHERE device_id=%s AND source_event_id=%s", (device["id"], body.source_event_id)).fetchone()
            unavailable = conn.execute(f"SELECT 1 FROM {SCHEMA}.alerts r WHERE device_id=%s AND source_event_id=%s AND NOT {source_available_sql()}", (device["id"], body.source_event_id)).fetchone()
            if receipt is not None or unavailable is not None:
                fail(409, "SOURCE_UNAVAILABLE", "Use the source lifecycle endpoint; this observation cannot be reintroduced here.")
            digest = payload_hash(body)
            previous = conn.execute(f"SELECT id,payload_hash FROM {SCHEMA}.alerts WHERE device_id=%s AND source_event_id=%s", (device["id"], body.source_event_id)).fetchone()
            if previous is not None:
                if previous["payload_hash"] != digest:
                    fail(409, "EVENT_CONFLICT", "This observation was already received with different content.")
                identifier = previous["id"]
            else:
                count = conn.execute(f"SELECT count(*) AS count FROM {SCHEMA}.alerts WHERE device_id=%s AND received_at>CURRENT_TIMESTAMP-INTERVAL '1 day'", (device["id"],)).fetchone()["count"]
                if count >= 1000:
                    fail(429, "ALERT_LIMIT", "The daily observation limit has been reached. Review local alert volume.")
                identifier = str(uuid4())
                conn.execute(f"INSERT INTO {SCHEMA}.alerts(id,organisation_id,pharmacy_id,device_id,source_event_id,payload_hash,event_code,title,source_label,occurred_at,historical) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)", (identifier, device["organisation_id"], device["pharmacy_id"], device["id"], body.source_event_id, digest, body.event_code, LABELS[body.event_code], body.source_label, captured, body.historical or now()-captured > timedelta(seconds=120)))
            # Device credentials receive only their own ingestion receipt, never
            # staff review notes, incidents or the pharmacy/user directory.
            return {"id": identifier, "received": True}

    return router
