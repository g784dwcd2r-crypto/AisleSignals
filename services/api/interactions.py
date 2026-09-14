"""Scoped asynchronous local interaction jobs and short-lived sampled evidence.

One process-wide inference slot, no backlog, no database transaction during model
execution. This is an experimental review workflow, not validated theft detection.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import io
import json
import os
import re
import shutil
import threading
import time
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Literal
from uuid import UUID

from fastapi import Depends, Request
from fastapi.responses import JSONResponse, Response
from pydantic import Field, StrictFloat, StrictInt, field_validator, model_validator
from PIL import Image

from .interaction_vision import (
    MAX_JPEG_BYTES,
    VisionError,
    VisionProvider,
    validate_frames,
)
from .models import CameraContext, Input, InteractionCaseCreate, camera_provenance
from .store import Store, digest, encode, ident, now
from .evidence_crypto import EvidenceCipher, PilotDatabaseLock, read_frame

RETENTION_SECONDS = 24 * 3600
SITE_LIMIT = 100
TOTAL_LIMIT = 600  # Six branches can each retain their full local allowance.
MIN_FREE_DISK_BYTES = 128 * 1024 * 1024
INFERENCE_SLOT = threading.BoundedSemaphore(1)
ACTIVE_LOCK = threading.Lock()
ACTIVE_JOBS: dict[str, tuple[object, threading.Event]] = {}


class InteractionFrame(Input):
    at_seconds: StrictFloat | StrictInt = Field(ge=0, le=7 * 86400, allow_inf_nan=False)
    jpeg_base64: str = Field(min_length=4, max_length=((MAX_JPEG_BYTES + 2) // 3) * 4)


class InteractionInput(Input):
    run_id: UUID
    source_kind: Literal["SCREEN_CAPTURE", "CAMERA", "RECORDED_VIDEO"]
    source_label: str = Field(min_length=1, max_length=120)
    camera_context: CameraContext | None = None
    frames: list[InteractionFrame] = Field(min_length=3, max_length=6)

    @field_validator("source_label")
    @classmethod
    def label(cls, value):
        if any(ord(char) < 32 or ord(char) == 127 for char in value):
            raise ValueError("Use a plain source label.")
        return value

    @model_validator(mode="after")
    def chronology(self):
        stamps = [frame.at_seconds for frame in self.frames]
        if (
            any(right <= left for left, right in zip(stamps, stamps[1:]))
            or stamps[-1] - stamps[0] > 12
        ):
            raise ValueError(
                "Frames must be chronological and span at most twelve seconds."
            )
        return self


class InteractionReview(Input):
    outcome: Literal["USEFUL", "NORMAL_SHOPPING", "UNCLEAR"]
    note: str = Field(default="", max_length=1000)
    expected_version: StrictInt | None = Field(default=None, ge=1)


def expired(item):
    try:
        return datetime.now(timezone.utc) >= datetime.fromisoformat(
            item["expires_at"].replace("Z", "+00:00")
        )
    except (KeyError, TypeError, ValueError):
        return True


def saved_view(item):
    result = item["result"]
    return {
        "id": item["id"],
        "run_id": item["run_id"],
        "source_kind": item["source_kind"],
        "source_label": item["source_label"],
        "created_at": item["created_at"],
        "expires_at": item["expires_at"],
        **result,
        **camera_provenance(item),
        "frames": [
            {
                "at_seconds": frame["at_seconds"],
                "url": f"/api/interactions/{item['id']}/frames/{index}",
            }
            for index, frame in enumerate(item["frames"])
        ],
        "review": item["review"],
        "version": item["version"],
        "incident_id": item.get("incident_id"),
        "evidence_kind": "sampled_jpeg_derivatives",
        "historical": item["source_kind"] == "RECORDED_VIDEO",
    }


def job_view(item):
    result = {"id": item["id"], "status": item["status"]}
    if item["status"] == "completed":
        result["result"] = saved_view(item)
    elif item.get("error"):
        result["error"] = item["error"]
    return result


class InteractionService:
    def __init__(self, store):
        self.store = store
        self.provider = VisionProvider()
        self.runtime_current = lambda item: True  # Installed by the owning API process.
        db = Path(store.path).resolve()
        self.evidence_root = db.parent / (db.name + ".interaction-evidence")
        if self.evidence_root.is_symlink():
            raise ValueError(
                "Interaction evidence directory cannot be a symbolic link."
            )
        self.evidence_root.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(self.evidence_root, 0o700)
        self.database_lock = None
        self.cipher = None
        if store.mode == "pilot":
            self.database_lock = PilotDatabaseLock(db)
            try:
                with store.transaction() as conn:
                    has_evidence = conn.execute("SELECT 1 FROM entities WHERE kind='interaction' LIMIT 1").fetchone()
                self.cipher = EvidenceCipher(db, create=not has_evidence)
            except BaseException:
                self.database_lock.close()
                raise
        self.closed = threading.Event()
        self.janitor = None
        try:
            self.cleanup(recover=True)
        except BaseException:
            if self.database_lock:
                self.database_lock.close()
            raise

    def directory(self, item_id):
        # No client-provided path is ever used to locate evidence.
        if str(UUID(item_id)) != item_id:
            raise VisionError("The requested evidence is unavailable.")
        directory = self.evidence_root / item_id
        if directory.is_symlink() or directory.resolve().parent != self.evidence_root:
            raise VisionError("The requested evidence is unavailable.")
        return directory

    def remove_files(self, item_id):
        directory = self.directory(item_id)
        if not directory.exists():
            return
        for path in directory.iterdir():
            # Files are our fixed numeric JPEG names. Never traverse directories.
            if path.is_file() or path.is_symlink():
                path.unlink()
        directory.rmdir()

    def cleanup(self, recover=False):
        with self.store.transaction() as conn:
            rows = conn.execute(
                "SELECT * FROM entities WHERE kind='interaction'"
            ).fetchall()
            for row in rows:
                item = json.loads(row["body"])
                user = {
                    "organisation_id": row["organisation_id"],
                    "site_id": row["site_id"],
                    "name": "Local retention worker",
                }
                if expired(item):
                    # Access is independently denied by timestamp even if disk deletion fails.
                    try:
                        self.remove_files(item["id"])
                    except (OSError, VisionError, ValueError):
                        continue
                    conn.execute(
                        "DELETE FROM entities WHERE id=? AND kind='interaction'",
                        (item["id"],),
                    )
                    self.store.audit(
                        conn,
                        user,
                        "INTERACTION_EXPIRED",
                        "interaction",
                        item["id"],
                        "Sampled evidence and interaction metadata deleted after 24 hours.",
                    )
                elif recover and item["status"] in {"pending", "running"}:
                    with ACTIVE_LOCK:
                        active = item["id"] in ACTIVE_JOBS
                    if not active:
                        item["status"] = "failed"
                        item["error"] = (
                            "Analysis was interrupted by a service restart. Submit a fresh sample to retry."
                        )
                        self.remove_files(item["id"])
                        item["frames"] = []
                        self.store.put(conn, user, "interaction", item)
            known = {
                row[0]
                for row in conn.execute(
                    "SELECT id FROM entities WHERE kind='interaction'"
                )
            }
        # Remove abandoned pre-commit files after a conservative crash grace period.
        for directory in self.evidence_root.iterdir():
            try:
                if (
                    directory.name not in known
                    and time.time() - directory.stat().st_mtime > 120
                ):
                    self.remove_files(directory.name)
            except (OSError, VisionError, ValueError):
                continue

    def start(self):
        if self.janitor is not None:
            return

        def maintain():
            while not self.closed.wait(60):
                try:
                    self.cleanup()
                except Exception:
                    # Never include evidence, model output or source labels in logs.
                    pass

        self.janitor = threading.Thread(
            target=maintain, daemon=True, name="interaction-retention"
        )
        self.janitor.start()

    def close(self):
        self.closed.set()
        with ACTIVE_LOCK:
            for service, cancellation in ACTIVE_JOBS.values():
                if service is self:
                    cancellation.set()
        if self.janitor:
            self.janitor.join(timeout=1)
        # Workers can finish a model request after shutdown. Their cancelled
        # result is never published; release only after their DB/file work ends.
        if self.database_lock:
            with ACTIVE_LOCK:
                active = any(service is self for service, _ in ACTIVE_JOBS.values())
            if not active:
                self.database_lock.close()

    @staticmethod
    def authorized(conn, item):
        session = conn.execute(
            "SELECT * FROM sessions WHERE token_hash=? AND user_id=?",
            (item["session_hash"], item["actor_id"]),
        ).fetchone()
        user = Store.resolve_session_user(conn, session) if session else None
        stamp = time.time()
        if (
            not session
            or not user
            or stamp - session["last_seen"] >= 15 * 60
            or stamp - session["created_at"] >= 8 * 3600
        ):
            return None
        if (user["organisation_id"], user["site_id"]) != (
            item["organisation_id"],
            item["site_id"],
        ):
            return None
        return dict(user)

    def make_room(self, conn, scope):
        """Roll only unreviewed low-value terminal samples at the branch limit.

        Reviewed results and possible concealment
        survive pressure until explicit deletion or the documented 24-hour expiry.
        One branch can never evict another branch's evidence.
        """
        rows = conn.execute(
            "SELECT body FROM entities WHERE kind='interaction' AND organisation_id=? AND site_id=? ORDER BY created_at,id",
            (scope["organisation_id"], scope["site_id"]),
        ).fetchall()
        count = len(rows)
        total = conn.execute("SELECT COUNT(*) FROM entities WHERE kind='interaction'").fetchone()[0]
        removed = []
        for row in rows:
            if count < SITE_LIMIT and total < TOTAL_LIMIT:
                break
            item = json.loads(row["body"])
            low_value = item["status"] in {"failed", "cancelled"} or (
                item["status"] == "completed" and
                (item.get("result") or {}).get("action") in {"NORMAL_SHOPPING", "UNCLEAR", "TAKE_PRODUCT", "RETURN_PRODUCT", "PLACE_IN_BASKET"}
                and not (item.get("result") or {}).get("alarm_eligible", False)
            )
            if item.get("review") is not None or not low_value:
                continue
            conn.execute("DELETE FROM entities WHERE kind='interaction' AND id=?", (item["id"],))
            removed.append(item["id"])
            self.store.audit(conn, scope, "INTERACTION_ROLLED_OFF", "interaction", item["id"],
                             "Unreviewed ordinary-shopping/unclear or failed/cancelled sample removed to keep local storage bounded.")
            count -= 1
            total -= 1
        return count < SITE_LIMIT and total < TOTAL_LIMIT, removed

    def worker(self, item_id, scope, cancellation):
        try:
            with self.store.transaction() as conn:
                item = self.store.get(conn, scope, "interaction", item_id)
                if not item or item["status"] == "cancelled" or expired(item):
                    return
                if not self.authorized(conn, item) or cancellation.is_set() or not self.runtime_current(item):
                    item["status"] = "cancelled"
                    item["error"] = "Analysis stopped because its runtime context or authorisation ended."
                    self.remove_files(item_id)
                    item["frames"] = []
                    self.store.put(conn, scope, "interaction", item)
                    return
                item["status"] = "running"
                self.store.put(conn, scope, "interaction", item)
            # Deliberately outside all SQLite transactions and database locks.
            frames = []
            for index, frame in enumerate(item["frames"]):
                data = read_frame(self.evidence_root, item, index, self.cipher, MAX_JPEG_BYTES)
                frames.append((frame["at_seconds"], data))
            if cancellation.is_set() or self.closed.is_set():
                return
            result = self.provider.analyze(frames)
            with self.store.transaction() as conn:
                current = self.store.get(conn, scope, "interaction", item_id)
                if not current or current["status"] == "cancelled":
                    return
                if (
                    expired(current)
                    or cancellation.is_set()
                    or self.closed.is_set()
                    or not self.authorized(conn, current)
                    or not self.runtime_current(current)
                ):
                    current["status"] = "cancelled"
                    current["error"] = (
                        "Analysis stopped before publication because its context or authorisation ended."
                    )
                    self.remove_files(item_id)
                    current["frames"] = []
                else:
                    current["status"] = "completed"
                    current["result"] = result
                    self.store.audit(
                        conn,
                        scope,
                        "INTERACTION_ANALYSED",
                        "interaction",
                        item_id,
                        "Experimental local model classification saved for staff review; no theft finding or output actuation.",
                    )
                self.store.put(conn, scope, "interaction", current)
        except Exception as error:
            # Store only our safe provider errors, never exception repr or image text.
            message = (
                error.message
                if isinstance(error, VisionError)
                else "Local analysis failed. No classification was saved. Submit a fresh sample after checking the service."
            )
            try:
                with self.store.transaction() as conn:
                    current = self.store.get(conn, scope, "interaction", item_id)
                    if current and current["status"] not in {"cancelled", "completed"}:
                        current["status"] = "failed"
                        current["error"] = message
                        self.remove_files(item_id)
                        current["frames"] = []
                        self.store.put(conn, scope, "interaction", current)
                        self.store.audit(
                            conn,
                            scope,
                            "INTERACTION_FAILED",
                            "interaction",
                            item_id,
                            "Local analysis failed; no classification was saved.",
                        )
            except Exception:
                pass
        finally:
            with ACTIVE_LOCK:
                ACTIVE_JOBS.pop(item_id, None)
            if self.closed.is_set() and self.database_lock:
                self.database_lock.close()
            INFERENCE_SLOT.release()


@asynccontextmanager
async def interaction_lifespan(app):
    app.state.interactions.start()
    try:
        yield
    finally:
        app.state.interactions.close()


def install_interactions(app, context, problem, new_incident, idempotent):
    service = app.state.interactions = InteractionService(app.state.store)

    def available(ctx, item_id):
        item = ctx.get("interaction", str(item_id))
        if expired(item):
            problem(
                410,
                "INTERACTION_EXPIRED",
                "This interaction and its sampled evidence have expired.",
            )
        return item

    @app.get("/api/interactions/status")
    def status(ctx=Depends(context)):
        # context authenticates within a short transaction; don't hold its lock
        # while the model server is contacted or the janitor opens a transaction.
        ctx.conn.commit()
        service.cleanup()
        return {**service.provider.status(), "evidence_policy": {
            "retention_seconds": RETENTION_SECONDS,
            "site_limit": SITE_LIMIT, "installation_limit": TOTAL_LIMIT,
            "encryption": "AES-256-GCM" if service.cipher else "synthetic_plaintext",
            "rolling_cleanup": "Keep up to 100 recent samples per branch. Oldest unreviewed ordinary-shopping actions, unclear results and failed/cancelled jobs roll off at capacity. Staff-reviewed samples and possible concealment require explicit deletion or expire after 24 hours.",
        }}

    @app.post("/api/interactions/jobs")
    def submit(body: InteractionInput, request: Request, ctx=Depends(context)):
        key = request.headers.get("idempotency-key", "")
        if not re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", key):
            problem(
                400,
                "IDEMPOTENCY_KEY_REQUIRED",
                "Provide a stable Idempotency-Key for this sampled interaction.",
            )
        route = (
            "/api/interactions/jobs/"
            + ctx.user["organisation_id"]
            + "/"
            + ctx.user["site_id"]
        )
        # Omitted/null camera context preserves pre-upgrade single-camera retry hashes.
        payload_hash = digest(encode(body.model_dump(mode="json", exclude_none=True)))
        previous = ctx.conn.execute(
            "SELECT * FROM idempotency WHERE actor_id=? AND route=? AND key=?",
            (ctx.user["id"], route, key),
        ).fetchone()
        if previous:
            if previous["payload_hash"] != payload_hash:
                problem(
                    409,
                    "IDEMPOTENCY_CONFLICT",
                    "This retry key was used for different frames or source information.",
                )
            result = json.loads(previous["result"])
            original = ctx.store.get(ctx.conn, ctx.user, "interaction", result["id"])
            if not original or expired(original):
                problem(
                    410,
                    "INTERACTION_EXPIRED",
                    "This previous job was deleted or expired. Use a fresh sample and action key.",
                )
            if not service.runtime_current(original):
                problem(409, "RUNTIME_CONTEXT_CHANGED", "This earlier job belongs to an ended runtime. Submit a fresh sample with a new action key.")
            return JSONResponse(
                {"id": original["id"], "status": original["status"]}, status_code=202
            )
        try:
            decoded = [
                (frame.at_seconds, base64.b64decode(frame.jpeg_base64, validate=True))
                for frame in body.frames
            ]
            frames = validate_frames(decoded)
            if body.camera_context:
                expected_size = body.camera_context.jpeg_size()
                for _, jpeg in frames:
                    with Image.open(io.BytesIO(jpeg)) as image:
                        if image.size != expected_size:
                            raise VisionError("Sample dimensions do not match the declared camera crop.")
        except (binascii.Error, ValueError, VisionError):
            problem(
                422,
                "INVALID_FRAMES",
                "Use three to six complete JPEG frames, at most 350 KB and 768 pixels per edge, in chronological order over at most twelve seconds. Camera-context samples must match the declared crop and bounded resize dimensions.",
            )
        try:
            enough_disk = shutil.disk_usage(service.evidence_root).free >= MIN_FREE_DISK_BYTES + sum(len(data) + 64 for _, data in frames)
        except OSError:
            enough_disk = False
        if not enough_disk:
            problem(503, "EVIDENCE_STORAGE_UNAVAILABLE", "Sampled analysis is paused: at least 128 MiB of free disk space plus room for this sample is required. Free space and submit a fresh sample.")
        room_available, rolled_off = service.make_room(ctx.conn, ctx.user)
        if not room_available:
            problem(
                429,
                "EVIDENCE_LIMIT",
                "The branch evidence store is full of protected review samples. Review and explicitly delete samples no longer needed; they otherwise expire after 24 hours. Analysis is paused until space is available.",
            )
        if not INFERENCE_SLOT.acquire(blocking=False):
            problem(
                429,
                "VISION_BUSY",
                "The local model is already analysing an interaction. No frames were queued; wait for completion and submit a fresh sample.",
            )
        item_id = ident()
        dispatched = False
        try:
            directory = service.directory(item_id)
            directory.mkdir(mode=0o700)
            encryption_scope = {"id": item_id, "organisation_id": ctx.user["organisation_id"], "site_id": ctx.user["site_id"]}
            manifest = []
            for index, (stamp, data) in enumerate(frames):
                path = directory / f"{index}.jpg"
                with path.open("xb") as output:
                    os.chmod(path, 0o600)
                    output.write(service.cipher.encrypt(data, encryption_scope, index) if service.cipher else data)
                manifest.append(
                    {
                        "at_seconds": stamp,
                        "sha256": hashlib.sha256(data).hexdigest(),
                        "bytes": len(data),
                    }
                )
            item = {
                "id": item_id,
                "run_id": str(body.run_id),
                "runtime_context": request.headers.get("x-aislesignals-runtime"),
                "source_kind": body.source_kind,
                "source_label": body.source_label,
                **({"camera_context": body.camera_context.model_dump(mode="json")} if body.camera_context else {}),
                "created_at": now(),
                "expires_at": (
                    datetime.now(timezone.utc) + timedelta(seconds=RETENTION_SECONDS)
                )
                .isoformat()
                .replace("+00:00", "Z"),
                "status": "pending",
                "frames": manifest,
                "review": None,
                "result": None,
                "version": 1,
                "actor_id": ctx.user["id"],
                "session_hash": ctx.token_hash,
                "organisation_id": ctx.user["organisation_id"],
                "site_id": ctx.user["site_id"],
            }
            ctx.put("interaction", item)
            ctx.audit(
                "INTERACTION_SUBMITTED",
                "interaction",
                item_id,
                "Sampled JPEG derivatives retained locally for experimental model analysis, with 24-hour expiry.",
            )
            response = {"id": item_id, "status": "pending"}
            ctx.conn.execute(
                "INSERT INTO idempotency VALUES(?,?,?,?,?)",
                (ctx.user["id"], route, key, payload_hash, encode(response)),
            )
            ctx.conn.commit()
            for old_id in rolled_off:
                try:
                    service.remove_files(old_id)
                except (OSError, ValueError, VisionError):
                    # The record is gone, so access is denied. The orphan janitor
                    # retries physical removal; never resurrect deleted metadata.
                    pass
            cancellation = threading.Event()
            with ACTIVE_LOCK:
                ACTIVE_JOBS[item_id] = (service, cancellation)
            thread = threading.Thread(
                target=service.worker,
                args=(item_id, dict(ctx.user), cancellation),
                daemon=True,
                name="local-interaction-analysis",
            )
            accepted = JSONResponse(response, status_code=202)
            thread.start()
            dispatched = True
            return accepted
        except Exception:
            if not dispatched:
                with ACTIVE_LOCK:
                    ACTIVE_JOBS.pop(item_id, None)
                INFERENCE_SLOT.release()
                ctx.conn.rollback()
                try:
                    service.remove_files(item_id)
                except (OSError, VisionError):
                    pass
                # A committed pending job must not become permanently pending
                # if the operating system cannot start the inference thread.
                with ctx.store.transaction() as conn:
                    stored = ctx.store.get(conn, ctx.user, "interaction", item_id)
                    if stored:
                        stored["status"] = "failed"
                        stored["error"] = (
                            "Local analysis could not start. Submit a fresh sample after checking the service."
                        )
                        stored["frames"] = []
                        ctx.store.put(conn, ctx.user, "interaction", stored)
                        ctx.store.audit(
                            conn,
                            ctx.user,
                            "INTERACTION_FAILED",
                            "interaction",
                            item_id,
                            "Local analysis could not be dispatched; no classification was saved.",
                        )
            problem(
                503,
                "VISION_DISPATCH_FAILED",
                "Local analysis could not start. No classification was saved.",
            )

    @app.get("/api/interactions/jobs/{item_id}")
    def job(item_id: UUID, ctx=Depends(context)):
        item = available(ctx, item_id)
        if not service.runtime_current(item):
            problem(409, "RUNTIME_CONTEXT_CHANGED", "This live job belongs to an ended runtime. Any completed observation remains available only in reviewed history.")
        return job_view(item)

    @app.post("/api/interactions/jobs/{item_id}/cancel")
    def cancel(item_id: UUID, ctx=Depends(context)):
        item = available(ctx, item_id)
        if item["status"] in {"pending", "running"}:
            with ACTIVE_LOCK:
                active = ACTIVE_JOBS.get(str(item_id))
                if active:
                    active[1].set()
            item["status"] = "cancelled"
            item["error"] = "Analysis was cancelled. No classification was published."
            service.remove_files(str(item_id))
            item["frames"] = []
            ctx.put("interaction", item)
            ctx.audit(
                "INTERACTION_CANCELLED",
                "interaction",
                item["id"],
                "Staff cancelled the sampled interaction analysis.",
            )
        return job_view(item)

    @app.get("/api/interactions")
    def listing(ctx=Depends(context)):
        ctx.conn.commit()
        service.cleanup()
        return {
            "items": [
                saved_view(item)
                for item in ctx.store.listing(ctx.conn, ctx.user, "interaction")
                if item["status"] == "completed" and not expired(item)
            ]
        }

    @app.get("/api/interactions/{item_id}/frames/{index}")
    def evidence(item_id: UUID, index: int, request: Request, ctx=Depends(context)):
        if request.url.query:
            problem(
                400,
                "URL_CREDENTIALS_REJECTED",
                "Evidence URLs do not accept query parameters.",
            )
        item = available(ctx, item_id)
        if item["status"] != "completed" or not 0 <= index < len(item["frames"]):
            problem(404, "EVIDENCE_UNAVAILABLE", "This sampled frame is not available.")
        try:
            data = read_frame(service.evidence_root, item, index, service.cipher, MAX_JPEG_BYTES)
        except (OSError, VisionError, ValueError):
            problem(
                404,
                "EVIDENCE_UNAVAILABLE",
                "This sampled frame is missing or failed its integrity check.",
            )
        return Response(
            data,
            media_type="image/jpeg",
            headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"},
        )

    @app.post("/api/interactions/{item_id}/review")
    def review(item_id: UUID, body: InteractionReview, ctx=Depends(context)):
        item = available(ctx, item_id)
        if item["status"] != "completed":
            problem(
                409,
                "INTERACTION_NOT_READY",
                "Wait for a completed analysis before reviewing it.",
            )
        # A first review may omit version for a simple client. Changes to an
        # existing review must explicitly match its current version.
        if body.expected_version != item["version"] and (
            body.expected_version is not None or item["review"] is not None
        ):
            problem(
                409,
                "VERSION_CONFLICT",
                "This review changed. Reload the latest interaction before updating it.",
                item["version"],
            )
        item["review"] = {
            "outcome": body.outcome,
            "note": body.note,
            "at": now(),
            "by": ctx.user["name"],
        }
        item["version"] += 1
        ctx.put("interaction", item)
        ctx.audit(
            "INTERACTION_REVIEWED",
            "interaction",
            item["id"],
            "Staff recorded a review outcome: "
            + body.outcome
            + ". This is not a criminality finding.",
        )
        return saved_view(item)

    @app.post("/api/interactions/{item_id}/case", status_code=201)
    def create_case(item_id: UUID, body: InteractionCaseCreate, request: Request, ctx=Depends(context)):
        # Authorisation and expiry precede even a successful idempotent replay.
        item = available(ctx, item_id)
        if ctx.user["role"] not in {"MANAGER", "REVIEWER"}:
            problem(403, "REVIEWER_REQUIRED", "An authorised pharmacy reviewer must create this case.")

        def run():
            if item.get("incident_id"):
                problem(409, "INTERACTION_ALREADY_LINKED", "This observation already has a linked case. Refresh and open that case.", item["version"])
            if item["version"] != body.expected_version:
                problem(409, "VERSION_CONFLICT", "This observation changed. Reload and review the latest version before creating a case.", item["version"])
            if item["status"] != "completed" or not item.get("result"):
                problem(409, "INTERACTION_NOT_READY", "Wait for a completed observation before creating a case.")
            if not item.get("review"):
                problem(409, "INTERACTION_REVIEW_REQUIRED", "Review the sampled frames and record a staff outcome before creating a case.")
            if not item["frames"]:
                problem(404, "EVIDENCE_UNAVAILABLE", "The sampled evidence is unavailable. No linked case was created.")
            try:
                for index in range(len(item["frames"])):
                    read_frame(service.evidence_root, item, index, service.cipher, MAX_JPEG_BYTES)
            except (OSError, ValueError, VisionError):
                problem(404, "EVIDENCE_UNAVAILABLE", "The sampled evidence is missing or failed integrity checks. No linked case was created.")
            # BEGIN IMMEDIATE in the request context serialises both records and
            # the retry receipt. No copied media or retention extension is made.
            incident = new_incident(ctx, body.title, body.notes)
            incident["interaction_source"] = {
                "id": item["id"], "version": item["version"],
                "run_id": item["run_id"], "source_kind": item["source_kind"],
                "source_label": item["source_label"], "created_at": item["created_at"],
                **camera_provenance(item),
                "expires_at": item["expires_at"], "observation": item["result"],
                "review": item["review"], "frames": item["frames"],
                "linked_at": now(), "linked_by": {"id": ctx.user["id"], "name": ctx.user["name"]},
                "evidence_kind": "sampled_jpeg_derivatives",
                "retention_notice": "Case linkage preserves this metadata snapshot only. Sampled JPEGs retain their original 24-hour expiry and can be deleted earlier. Original CCTV is not copied or preserved.",
            }
            ctx.put("incident", incident)
            item["incident_id"] = incident["id"]
            item["version"] += 1
            ctx.put("interaction", item)
            ctx.audit("INTERACTION_CASE_LINKED", "incident", incident["id"],
                      "Staff linked a reviewed product observation. Case classification remains unassessed; no identity, intent, payment or criminality finding was inferred.")
            return {"incident": incident, "interaction": saved_view(item)}

        return idempotent(ctx, request, body.model_dump(), run)

    @app.get("/api/incidents/{incident_id}/interaction-source")
    def case_source(incident_id: UUID, ctx=Depends(context)):
        incident = ctx.get("incident", str(incident_id))
        source = incident.get("interaction_source")
        if not source:
            problem(404, "INTERACTION_SOURCE_UNAVAILABLE", "This case has no linked product observation.")
        current = ctx.store.get(ctx.conn, ctx.user, "interaction", source["id"])
        state = "expired" if expired(source) or (current is not None and expired(current)) else "deleted" if current is None else "available"
        if state == "available" and (current.get("incident_id") != incident["id"] or current.get("status") != "completed"):
            state = "unavailable"
        if state == "available":
            try:
                if current["frames"] != source["frames"]:
                    raise ValueError("Linked manifest changed")
                for index in range(len(source["frames"])):
                    read_frame(service.evidence_root, current, index, service.cipher, MAX_JPEG_BYTES)
            except (OSError, ValueError, VisionError):
                state = "unavailable"
        return {"source": source, "evidence_status": state, "frames": [
            {"at_seconds": frame["at_seconds"], "url": f"/api/interactions/{source['id']}/frames/{index}"}
            for index, frame in enumerate(source["frames"])
        ] if state == "available" else []}

    @app.delete("/api/interactions/{item_id}")
    def delete(item_id: UUID, ctx=Depends(context)):
        item = ctx.get("interaction", str(item_id))
        with ACTIVE_LOCK:
            active = ACTIVE_JOBS.get(str(item_id))
            if active:
                active[1].set()
        service.remove_files(str(item_id))
        ctx.conn.execute(
            "DELETE FROM entities WHERE id=? AND kind='interaction' AND organisation_id=? AND site_id=?",
            (str(item_id), ctx.user["organisation_id"], ctx.user["site_id"]),
        )
        ctx.audit(
            "INTERACTION_DELETED",
            "interaction",
            item["id"],
            "Staff deleted the interaction and sampled JPEG evidence. Linked case metadata remains under the case record lifecycle." if item.get("incident_id") else "Staff deleted interaction metadata and sampled JPEG evidence.",
        )
        return {"deleted": True}
