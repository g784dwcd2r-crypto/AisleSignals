"""Scoped asynchronous local interaction jobs and short-lived sampled evidence.

One process-wide inference slot, no backlog, no database transaction during model
execution. This is an experimental review workflow, not validated theft detection.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import os
import re
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

from .interaction_vision import (
    MAX_JPEG_BYTES,
    VisionError,
    VisionProvider,
    validate_frames,
)
from .models import Input
from .store import digest, encode, ident, now

RETENTION_SECONDS = 24 * 3600
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
        "frames": [
            {
                "at_seconds": frame["at_seconds"],
                "url": f"/api/interactions/{item['id']}/frames/{index}",
            }
            for index, frame in enumerate(item["frames"])
        ],
        "review": item["review"],
        "version": item["version"],
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
        db = Path(store.path).resolve()
        self.evidence_root = db.parent / (db.name + ".interaction-evidence")
        if self.evidence_root.is_symlink():
            raise ValueError(
                "Interaction evidence directory cannot be a symbolic link."
            )
        self.evidence_root.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(self.evidence_root, 0o700)
        self.closed = threading.Event()
        self.janitor = None
        self.cleanup(recover=True)

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

    @staticmethod
    def authorized(conn, item):
        session = conn.execute(
            "SELECT * FROM sessions WHERE token_hash=? AND user_id=?",
            (item["session_hash"], item["actor_id"]),
        ).fetchone()
        user = conn.execute(
            "SELECT * FROM users WHERE id=?", (item["actor_id"],)
        ).fetchone()
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

    def worker(self, item_id, scope, cancellation):
        try:
            with self.store.transaction() as conn:
                item = self.store.get(conn, scope, "interaction", item_id)
                if not item or item["status"] == "cancelled" or expired(item):
                    return
                if not self.authorized(conn, item) or cancellation.is_set():
                    item["status"] = "cancelled"
                    item["error"] = "Analysis stopped because its authorisation ended."
                    self.remove_files(item_id)
                    item["frames"] = []
                    self.store.put(conn, scope, "interaction", item)
                    return
                item["status"] = "running"
                self.store.put(conn, scope, "interaction", item)
            # Deliberately outside all SQLite transactions and database locks.
            frames = []
            for index, frame in enumerate(item["frames"]):
                path = self.directory(item_id) / f"{index}.jpg"
                if path.is_symlink():
                    raise VisionError("The requested evidence is unavailable.")
                data = path.read_bytes()
                if hashlib.sha256(data).hexdigest() != frame["sha256"]:
                    raise VisionError("A sampled frame failed its integrity check.")
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
            INFERENCE_SLOT.release()


@asynccontextmanager
async def interaction_lifespan(app):
    app.state.interactions.start()
    try:
        yield
    finally:
        app.state.interactions.close()


def install_interactions(app, context, problem):
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
        return service.provider.status()

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
        payload_hash = digest(encode(body.model_dump(mode="json")))
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
            return JSONResponse(
                {"id": original["id"], "status": original["status"]}, status_code=202
            )
        try:
            decoded = [
                (frame.at_seconds, base64.b64decode(frame.jpeg_base64, validate=True))
                for frame in body.frames
            ]
            frames = validate_frames(decoded)
        except (binascii.Error, ValueError, VisionError):
            problem(
                422,
                "INVALID_FRAMES",
                "Use three to six complete JPEG frames, at most 350 KB and 768 pixels per edge, in chronological order over at most twelve seconds.",
            )
        site_count = ctx.conn.execute(
            "SELECT COUNT(*) FROM entities WHERE kind='interaction' AND organisation_id=? AND site_id=?",
            (ctx.user["organisation_id"], ctx.user["site_id"]),
        ).fetchone()[0]
        total_count = ctx.conn.execute(
            "SELECT COUNT(*) FROM entities WHERE kind='interaction'"
        ).fetchone()[0]
        if site_count >= 100 or total_count >= 500:
            problem(
                429,
                "EVIDENCE_LIMIT",
                "The local interaction review store is full. Delete reviewed samples before submitting another.",
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
            manifest = []
            for index, (stamp, data) in enumerate(frames):
                path = directory / f"{index}.jpg"
                with path.open("xb") as output:
                    os.chmod(path, 0o600)
                    output.write(data)
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
                "source_kind": body.source_kind,
                "source_label": body.source_label,
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
        return job_view(available(ctx, item_id))

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
            path = service.directory(str(item_id)) / f"{index}.jpg"
            if path.is_symlink() or path.stat().st_size > MAX_JPEG_BYTES:
                raise OSError
            data = path.read_bytes()
            if hashlib.sha256(data).hexdigest() != item["frames"][index]["sha256"]:
                raise OSError
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
            "Staff deleted interaction metadata and sampled JPEG evidence.",
        )
        return {"deleted": True}
