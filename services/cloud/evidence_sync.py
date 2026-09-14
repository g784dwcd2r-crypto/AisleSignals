"""Branch-scoped encrypted evidence intake and private staff retrieval."""

from datetime import datetime, timezone
from hashlib import sha256
import json
import re
from typing import Literal
from uuid import UUID, uuid4

from fastapi import APIRouter, Body, Depends, Request, Response
from pydantic import BaseModel, ConfigDict, Field, StrictInt, field_validator, model_validator

from .control_operations import SCHEMA, device_transaction, fail, now, pharmacy_ids, transaction
from .control_store import audit, require_principal
from .evidence_crypto import EvidenceCryptoError, open_envelope, seal
from .evidence_store import EvidenceBlobStore, EvidenceStoreError, FilesystemEvidenceBlobStore


JPEG_LIMIT = 350 * 1024
CLIP_LIMIT = 8 * 1024 * 1024
PENDING_SECONDS = 15 * 60


class EvidenceManifest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=False)
    schema_version: Literal[1]
    kind: Literal["OVERVIEW", "INTERACTION_CROP", "CLIP"]
    content_type: Literal["image/jpeg", "video/mp4", "video/webm"]
    byte_count: StrictInt = Field(ge=1, le=CLIP_LIMIT)
    sha256: str = Field(min_length=64, max_length=64)
    duration_ms: StrictInt | None = Field(default=None, ge=1, le=20_000)

    @field_validator("sha256")
    @classmethod
    def digest(cls, value):
        if not re.fullmatch(r"[0-9a-f]{64}", value):
            raise ValueError("Use a lower-case SHA-256 digest.")
        return value

    @model_validator(mode="after")
    def media_shape(self):
        if self.kind == "CLIP":
            if self.content_type not in {"video/mp4", "video/webm"} or self.duration_ms is None:
                raise ValueError("A clip requires MP4 or WebM content and a duration.")
        elif self.content_type != "image/jpeg" or self.byte_count > JPEG_LIMIT or self.duration_ms is not None:
            raise ValueError("Overview and interaction crop evidence must be JPEG under 350 KiB.")
        return self


def canonical_manifest(body: EvidenceManifest) -> bytes:
    return json.dumps(body.model_dump(mode="json"), sort_keys=True, separators=(",", ":")).encode()


def device_evidence_receipt(row):
    ready = row["state"] == "AVAILABLE"
    return {"evidence_id": str(row["id"]), "upload_required": not ready, "state": "READY" if ready else "PENDING"}


def _aad(row) -> bytes:
    values = ["aislesignals-evidence-v1", str(row["organisation_id"]), str(row["pharmacy_id"]),
              str(row["device_id"]), str(row["id"]), row["kind"], row["content_type"],
              str(row["expected_bytes"]), row["sha256"], row["expires_at"].astimezone(timezone.utc).isoformat()]
    return json.dumps(values, separators=(",", ":")).encode()


class EvidenceService:
    def __init__(self, settings, store: EvidenceBlobStore | None = None):
        self.settings = settings
        self.enabled = settings.evidence_mode == "ENCRYPTED"
        self._keks = dict(settings.evidence_keks)
        if self.enabled:
            if (not settings.evidence_policy or not settings.evidence_kek_version
                    or settings.evidence_kek_version not in self._keks):
                raise EvidenceStoreError("Cloud evidence policy is incomplete.")
            if store is not None:
                self.store = store
            elif settings.evidence_store_backend == "FILESYSTEM":
                self.store = FilesystemEvidenceBlobStore(settings.evidence_store_path)
            elif settings.evidence_store_backend == "R2":
                from .evidence_r2 import R2EvidenceBlobStore
                self.store = R2EvidenceBlobStore(
                    account_id=settings.r2_account_id, jurisdiction=settings.r2_jurisdiction,
                    bucket=settings.r2_bucket, access_key_id=settings.r2_access_key_id,
                    secret_access_key=settings.r2_secret_access_key, prefix=settings.r2_prefix)
            else:
                raise EvidenceStoreError("Cloud evidence storage is not configured.")
        else:
            self.store = None

    def require(self):
        if not self.enabled or self.store is None:
            fail(503, "EVIDENCE_DISABLED", "Cloud evidence is not enabled for this service.")

    def encrypt(self, row, content: bytes) -> bytes:
        kek = self._keks.get(row["kek_version"])
        if kek is None:
            raise EvidenceCryptoError("Evidence write key is unavailable.")
        return seal(content, kek=kek, kek_version=row["kek_version"], aad=_aad(row))

    def decrypt(self, row, envelope: bytes) -> bytes:
        kek = self._keks.get(row["kek_version"])
        if kek is None:
            raise EvidenceCryptoError("Evidence read key is unavailable.")
        return open_envelope(envelope, kek=kek,
                             expected_version=row["kek_version"], aad=_aad(row))

    def probe(self) -> bool:
        return not self.enabled or (self.store is not None and self.store.probe() is True)


def mark_due_evidence(conn, device, *, limit=100):
    rows = conn.execute(f"""SELECT id,pharmacy_id,state FROM {SCHEMA}.evidence_objects
        WHERE device_id=%s AND state IN ('PENDING','AVAILABLE') AND
        (expires_at<=clock_timestamp() OR (state='PENDING' AND created_at<clock_timestamp()-INTERVAL '15 minutes'))
        ORDER BY created_at,id LIMIT %s FOR UPDATE""", (device["id"], limit)).fetchall()
    for row in rows:
        reason = "EVIDENCE_PENDING_EXPIRED" if row["state"] == "PENDING" else "EVIDENCE_EXPIRED"
        conn.execute(f"UPDATE {SCHEMA}.evidence_objects SET state='REVOKED',revoked_at=clock_timestamp() WHERE id=%s", (row["id"],))
        conn.execute(f"""INSERT INTO {SCHEMA}.audit_entries(id,organisation_id,actor_user_id,action,subject_id,pharmacy_id)
            VALUES(%s,%s,NULL,%s,%s,%s)""", (str(uuid4()), device["organisation_id"], reason, row["id"], row["pharmacy_id"]))
    return len(rows)


def revoke_source_evidence(conn, device, source_event_id):
    rows = conn.execute(f"""UPDATE {SCHEMA}.evidence_objects SET state='REVOKED',revoked_at=clock_timestamp()
        WHERE device_id=%s AND source_event_id=%s AND state IN ('PENDING','AVAILABLE') RETURNING id,pharmacy_id""",
        (device["id"], source_event_id)).fetchall()
    for row in rows:
        conn.execute(f"""INSERT INTO {SCHEMA}.audit_entries(id,organisation_id,actor_user_id,action,subject_id,pharmacy_id)
            VALUES(%s,%s,NULL,'EVIDENCE_SOURCE_WITHDRAWN',%s,%s)""",
            (str(uuid4()), device["organisation_id"], row["id"], row["pharmacy_id"]))
    return len(rows)


def create_evidence_router(service: EvidenceService):
    router = APIRouter()

    @router.post("/device-api/sync/v1/observations/{source_event_id}/evidence", status_code=201)
    def manifest(source_event_id: UUID, body: EvidenceManifest, request: Request):
        service.require()
        manifest_digest = sha256(canonical_manifest(body)).hexdigest()
        with device_transaction(request) as (conn, device):
            mark_due_evidence(conn, device)
            receipt = conn.execute(f"""SELECT r.*,a.id AS alert_id FROM {SCHEMA}.device_sync_receipts r
                JOIN {SCHEMA}.alerts a ON a.device_id=r.device_id AND a.source_event_id=r.source_event_id
                WHERE r.device_id=%s AND r.source_event_id=%s FOR UPDATE OF r,a""", (device["id"], source_event_id)).fetchone()
            if (receipt is None or receipt["withdrawn_at"] is not None or receipt["expires_at"] is None
                    or receipt["expires_at"] <= now()):
                fail(409, "SOURCE_UNAVAILABLE", "Evidence requires an available received observation.")
            existing = conn.execute(f"SELECT * FROM {SCHEMA}.evidence_objects WHERE device_id=%s AND source_event_id=%s AND kind=%s",
                                    (device["id"], source_event_id, body.kind)).fetchone()
            if existing:
                if existing["manifest_hash"] != manifest_digest:
                    fail(409, "EVIDENCE_CONFLICT", "This evidence kind already has a different manifest.")
                if existing["state"] in {"REVOKED", "DELETED"}:
                    fail(409, "EVIDENCE_UNAVAILABLE", "This evidence manifest is no longer available.")
                return device_evidence_receipt(existing)
            identifier = str(uuid4())
            row = conn.execute(f"""INSERT INTO {SCHEMA}.evidence_objects
                (id,organisation_id,pharmacy_id,device_id,alert_id,source_event_id,kind,content_type,
                 expected_bytes,sha256,duration_ms,manifest_hash,object_key,kek_version,expires_at)
                VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *""",
                (identifier, device["organisation_id"], device["pharmacy_id"], device["id"], receipt["alert_id"],
                 source_event_id, body.kind, body.content_type, body.byte_count, body.sha256,
                 body.duration_ms, manifest_digest,
                 f"evidence/{identifier}.bin", service.settings.evidence_kek_version, receipt["expires_at"])).fetchone()
            conn.execute(f"""INSERT INTO {SCHEMA}.audit_entries(id,organisation_id,actor_user_id,action,subject_id,pharmacy_id)
                VALUES(%s,%s,NULL,'EVIDENCE_MANIFEST_RECEIVED',%s,%s)""",
                (str(uuid4()), device["organisation_id"], identifier, device["pharmacy_id"]))
            return device_evidence_receipt(row)

    @router.put("/device-api/sync/v1/evidence/{evidence_id}")
    def upload(evidence_id: UUID, request: Request, content: bytes = Body()):
        service.require()
        content_type = request.headers.get("content-type", "")
        with device_transaction(request) as (conn, device):
            mark_due_evidence(conn, device)
            row = conn.execute(f"SELECT * FROM {SCHEMA}.evidence_objects WHERE id=%s AND device_id=%s FOR UPDATE",
                               (evidence_id, device["id"])).fetchone()
            if row is None:
                fail(404, "NOT_FOUND", "This evidence upload is not available.")
            if row["state"] in {"REVOKED", "DELETED"} or row["expires_at"] <= now():
                fail(409, "EVIDENCE_UNAVAILABLE", "This evidence upload is no longer available.")
            if content_type != row["content_type"] or len(content) != row["expected_bytes"] or sha256(content).hexdigest() != row["sha256"]:
                fail(409, "EVIDENCE_CONFLICT", "Evidence bytes do not match the accepted manifest.")
            original = dict(row)

        try:
            if original["state"] == "AVAILABLE":
                if service.decrypt(original, service.store.get(original["object_key"])) != content:
                    fail(409, "EVIDENCE_CONFLICT", "Stored evidence differs from this retry.")
            else:
                envelope = service.encrypt(original, content)
                created = service.store.put_if_absent(original["object_key"], envelope)
                if not created and service.decrypt(original, service.store.get(original["object_key"])) != content:
                    fail(409, "EVIDENCE_CONFLICT", "Stored evidence differs from this upload.")
        except (EvidenceStoreError, EvidenceCryptoError):
            fail(503, "EVIDENCE_UNAVAILABLE", "Evidence storage is unavailable.")

        unavailable = False
        with device_transaction(request) as (conn, device):
            current = conn.execute(f"SELECT * FROM {SCHEMA}.evidence_objects WHERE id=%s AND device_id=%s FOR UPDATE",
                                   (evidence_id, device["id"])).fetchone()
            if current is None:
                fail(404, "NOT_FOUND", "This evidence upload is not available.")
            immutable = ("organisation_id", "pharmacy_id", "device_id", "alert_id", "source_event_id",
                         "kind", "content_type", "expected_bytes", "sha256", "manifest_hash", "object_key",
                         "kek_version", "expires_at")
            if any(current[key] != original[key] for key in immutable):
                fail(409, "EVIDENCE_CONFLICT", "The evidence manifest changed during upload.")
            unavailable = current["state"] in {"REVOKED", "DELETED"} or current["expires_at"] <= now()
            if not unavailable and current["state"] == "PENDING":
                current = conn.execute(f"""UPDATE {SCHEMA}.evidence_objects SET state='AVAILABLE',uploaded_at=clock_timestamp()
                    WHERE id=%s AND state='PENDING' RETURNING *""", (evidence_id,)).fetchone()
                conn.execute(f"""INSERT INTO {SCHEMA}.audit_entries(id,organisation_id,actor_user_id,action,subject_id,pharmacy_id)
                    VALUES(%s,%s,NULL,'EVIDENCE_UPLOADED',%s,%s)""",
                    (str(uuid4()), device["organisation_id"], evidence_id, device["pharmacy_id"]))
        if unavailable:
            try:
                service.store.delete(original["object_key"])
            except EvidenceStoreError:
                pass
            fail(409, "EVIDENCE_UNAVAILABLE", "This evidence upload is no longer available.")
        return {"evidence_id": str(evidence_id), "state": "READY"}

    @router.get("/control-api/alerts/{alert_id}/evidence/{evidence_id}")
    def download(alert_id: UUID, evidence_id: UUID, request: Request, principal=Depends(require_principal)):
        service.require()
        with transaction(request, principal) as (conn, current):
            permitted = pharmacy_ids(conn, current)
            row = conn.execute(f"""SELECT * FROM {SCHEMA}.evidence_objects WHERE id=%s AND alert_id=%s AND organisation_id=%s
                AND pharmacy_id=ANY(%s::uuid[]) AND state='AVAILABLE' AND expires_at>clock_timestamp() FOR SHARE""",
                (evidence_id, alert_id, current.organisation_id, permitted)).fetchone()
            if row is None:
                fail(404, "NOT_FOUND", "This evidence is not available.")
            original = dict(row)
        try:
            content = service.decrypt(original, service.store.get(original["object_key"]))
        except (EvidenceStoreError, EvidenceCryptoError):
            fail(503, "EVIDENCE_UNAVAILABLE", "Evidence cannot be authenticated.")
        if len(content) != original["expected_bytes"] or sha256(content).hexdigest() != original["sha256"]:
            fail(503, "EVIDENCE_UNAVAILABLE", "Evidence cannot be authenticated.")
        with transaction(request, principal) as (conn, current):
            permitted = pharmacy_ids(conn, current)
            refreshed = conn.execute(f"""SELECT * FROM {SCHEMA}.evidence_objects WHERE id=%s AND alert_id=%s AND organisation_id=%s
                AND pharmacy_id=ANY(%s::uuid[]) AND state='AVAILABLE' AND expires_at>clock_timestamp() FOR SHARE""",
                (evidence_id, alert_id, current.organisation_id, permitted)).fetchone()
            if refreshed is None or refreshed["manifest_hash"] != original["manifest_hash"] or refreshed["object_key"] != original["object_key"]:
                fail(404, "NOT_FOUND", "This evidence is not available.")
            audit(conn, current, "EVIDENCE_VIEWED", str(evidence_id), str(refreshed["pharmacy_id"]))
        return Response(content, media_type=original["content_type"], headers={
            "Cache-Control": "private, no-store", "Content-Disposition": "inline",
            "X-Content-Type-Options": "nosniff",
        })

    return router
