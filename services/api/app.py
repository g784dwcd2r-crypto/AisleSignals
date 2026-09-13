"""AisleSignals 0.1: localhost-only, persistent synthetic workflow prototype.

No live camera input, cloud inference, biometric recognition, external alarm actuation or billing.
Run with: uvicorn services.api.app:app --host 127.0.0.1 --port 8765
"""

from __future__ import annotations
import hashlib
import html
import json
import os
import secrets
import sqlite3
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable
from urllib.parse import urlsplit

from fastapi import Depends, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, Response
from starlette.exceptions import HTTPException

from .models import (
    Login,
    Shift,
    Simulator,
    Version,
    Review,
    IncidentCreate,
    IncidentPatch,
    Reason,
    TaskCreate,
    Export,
    AssistanceCreate,
    AssistanceTransition,
    PlaybackEvent,
)
from .store import Store, now, ident, encode, digest, password_hash

COOKIE = "aislesignals_demo_session"
ROOT = Path(__file__).resolve().parents[2]
ALLOWED_HOSTS = {"127.0.0.1", "localhost", "::1"}


class Problem(Exception):
    def __init__(self, status, code, message, current_version=None):
        self.status = status
        self.error = {"code": code, "message": message}
        if current_version is not None:
            self.error["current_version"] = current_version


def problem(status, code, message, current_version=None):
    raise Problem(status, code, message, current_version)


def public_user(user):
    return {key: user[key] for key in ("id", "name", "email", "role")}


@dataclass
class Context:
    conn: sqlite3.Connection
    user: dict
    store: Store
    csrf_token: str
    token_hash: str

    def get(self, kind, entity_id):
        result = self.store.get(self.conn, self.user, kind, entity_id)
        if result is None:
            problem(404, "NOT_FOUND", "The requested resource is not available.")
        return result

    def put(self, kind, value):
        return self.store.put(self.conn, self.user, kind, value)

    def audit(self, action, kind, entity_id, detail):
        return self.store.audit(self.conn, self.user, action, kind, entity_id, detail)

    def manager(self):
        if self.user["role"] != "MANAGER":
            problem(
                403, "MANAGER_REQUIRED", "A pharmacy manager must perform this action."
            )


def context(request: Request):
    token = request.cookies.get(COOKIE, "")
    if not token or len(token) > 256:
        problem(401, "AUTH_REQUIRED", "Sign in to continue.")
    with request.app.state.store.transaction() as conn:
        session = conn.execute(
            "SELECT * FROM sessions WHERE token_hash=?", (digest(token),)
        ).fetchone()
        stamp = time.time()
        if (
            session is None
            or stamp - session["created_at"] >= 8 * 3600
            or stamp - session["last_seen"] >= 15 * 60
        ):
            if session is not None:
                conn.execute(
                    "DELETE FROM sessions WHERE token_hash=?", (digest(token),)
                )
                conn.commit()
            problem(
                401,
                "SESSION_EXPIRED",
                "Your session expired. Sign in again; unsaved input has not been submitted.",
            )
        user = conn.execute(
            "SELECT * FROM users WHERE id=?", (session["user_id"],)
        ).fetchone()
        if user is None:
            problem(401, "AUTH_REQUIRED", "Sign in to continue.")
        if request.method not in ("GET", "HEAD", "OPTIONS"):
            supplied = request.headers.get("x-csrf-token", "")
            if not secrets.compare_digest(supplied, session["csrf_token"]):
                problem(
                    403,
                    "CSRF_REJECTED",
                    "The request could not be verified. Refresh your session and retry.",
                )
        # Background refresh and image loads must not keep an unattended laptop signed in.
        if (
            request.method not in ("GET", "HEAD", "OPTIONS")
            or request.url.path == "/api/session"
        ):
            conn.execute(
                "UPDATE sessions SET last_seen=? WHERE token_hash=?",
                (stamp, digest(token)),
            )
        yield Context(
            conn,
            dict(user),
            request.app.state.store,
            session["csrf_token"],
            digest(token),
        )


def match_version(resource, expected):
    if resource["version"] != expected:
        problem(
            409,
            "VERSION_CONFLICT",
            "This record changed in another session. Reload it and review the latest facts before retrying.",
            resource["version"],
        )


def open_incident(ctx, incident_id, expected):
    item = ctx.get("incident", incident_id)
    match_version(item, expected)
    if item["status"] == "CLOSED":
        problem(
            409,
            "INCIDENT_CLOSED",
            "A manager must explicitly reopen this incident before it can change.",
        )
    return item


def history(ctx, item, action, detail, invalidate_draft=True):
    item["version"] += 1
    item["updated_at"] = now()
    entry = dict(
        id=ident(), at=now(), actor=ctx.user["name"], action=action, detail=detail
    )
    item["history"].append(entry)
    if invalidate_draft:
        item["draft"] = None
    ctx.audit(action, "incident", item["id"], detail)
    return ctx.put("incident", item)


def new_incident(ctx, title, notes, candidate_id=None):
    item_id = ident()
    count = ctx.conn.execute(
        "SELECT COUNT(*) FROM entities WHERE kind='incident' AND organisation_id=? AND site_id=?",
        (ctx.user["organisation_id"], ctx.user["site_id"]),
    ).fetchone()[0]
    item = dict(
        id=item_id,
        reference=f"AS-{count + 1:04d}",
        title=title,
        notes=notes,
        candidate_id=candidate_id,
        classification="SUSPECTED_INCIDENT" if candidate_id else "UNASSESSED",
        status="OPEN",
        outcome="UNRESOLVED",
        loss_cents=None,
        recovered_cents=None,
        version=1,
        created_at=now(),
        updated_at=now(),
        tasks=[],
        history=[],
        draft=None,
    )
    item["history"].append(
        dict(
            id=ident(),
            at=now(),
            actor=ctx.user["name"],
            action="INCIDENT_CREATED",
            detail=notes,
        )
    )
    ctx.audit(
        "INCIDENT_CREATED",
        "incident",
        item_id,
        "Human-created synthetic incident record; no finding of criminality.",
    )
    return ctx.put("incident", item)


def idempotent(ctx: Context, request: Request, payload, action: Callable):
    key = request.headers.get("idempotency-key", "")
    if not key or len(key) > 128 or not all(c.isalnum() or c in "-_:." for c in key):
        problem(
            400,
            "IDEMPOTENCY_KEY_REQUIRED",
            "Provide an Idempotency-Key of 1 to 128 letters, numbers or - _ : . characters.",
        )
    route = request.url.path
    payload_hash = digest(encode(payload))
    previous = ctx.conn.execute(
        "SELECT payload_hash,result FROM idempotency WHERE actor_id=? AND route=? AND key=?",
        (ctx.user["id"], route, key),
    ).fetchone()
    if previous:
        if previous["payload_hash"] != payload_hash:
            problem(
                409,
                "IDEMPOTENCY_CONFLICT",
                "This retry key was used for different input. Review the action before submitting a new key.",
            )
        return json.loads(previous["result"])
    result = action()
    ctx.conn.execute(
        "INSERT INTO idempotency VALUES(?,?,?,?,?)",
        (ctx.user["id"], route, key, payload_hash, encode(result)),
    )
    return result


def check_financials(item):
    loss, recovered = item["loss_cents"], item["recovered_cents"]
    if loss is not None and loss > 0:
        if (
            item["classification"] != "STORE_CONFIRMED_LOSS"
            or item["outcome"] != "LOSS_RECORDED"
        ):
            problem(
                422,
                "LOSS_NOT_CONFIRMED",
                "A positive recorded loss requires manager-confirmed loss and the LOSS_RECORDED outcome.",
            )
    if item["classification"] == "BENIGN" or item["outcome"] == "NO_LOSS_ESTABLISHED":
        if (loss or 0) > 0 or (recovered or 0) > 0:
            problem(
                422,
                "NO_LOSS_CONFLICT",
                "Benign or no-loss records cannot retain a financial loss or recovery.",
            )
    if recovered is not None and (loss is None or recovered > loss):
        problem(
            422,
            "RECOVERY_EXCEEDS_LOSS",
            "Recovery needs a recorded loss and cannot exceed it.",
        )
    if item["outcome"] == "LOSS_RECORDED" and (
        item["classification"] != "STORE_CONFIRMED_LOSS" or loss is None or loss <= 0
    ):
        problem(
            422,
            "LOSS_AMOUNT_REQUIRED",
            "A loss-recorded outcome requires a positive manager-confirmed amount.",
        )


def candidate_view(item):
    """Expire access without claiming that production retention deletion exists."""
    try:
        age = datetime.now(timezone.utc) - datetime.fromisoformat(
            item["received_at"].replace("Z", "+00:00")
        )
        expired = age.total_seconds() >= 72 * 3600
    except (KeyError, ValueError, TypeError):
        expired = True
    if expired:
        return {**item, "media_status": "MISSING", "evidence_url": None}
    return item


def create_app(db_path=None, web_dist=None):
    if os.environ.get("AISLESIGNALS_MODE", "synthetic") != "synthetic":
        raise ValueError(
            "This build supports AISLESIGNALS_MODE=synthetic only. It cannot run a live pharmacy service."
        )
    try:
        runtime_port = int(os.environ.get("AISLESIGNALS_PORT", "8765"))
        if not 1 <= runtime_port <= 65535:
            raise ValueError
    except ValueError as exc:
        raise ValueError(
            "AISLESIGNALS_PORT must be a port number from 1 to 65535."
        ) from exc
    allowed_ports = {runtime_port, 5173}
    allowed_origins = {
        f"http://{host}:{port}"
        for host in ("127.0.0.1", "localhost", "[::1]")
        for port in allowed_ports
    }
    application = FastAPI(
        title="AisleSignals synthetic prototype",
        version="0.1.0",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    application.state.store = Store(
        db_path
        or os.environ.get("AISLESIGNALS_DB_PATH")
        or ROOT / ".local" / "aislesignals.db"
    )
    application.state.web_dist = Path(
        web_dist
        or os.environ.get("AISLESIGNALS_WEB_DIST")
        or ROOT / "apps" / "web" / "dist"
    ).resolve()
    application.state.dummy_salt = secrets.token_hex(16)
    application.state.dummy_hash = password_hash(
        "invalid-demo-user", application.state.dummy_salt
    )

    @application.exception_handler(Problem)
    async def handle_problem(request, exc):
        return JSONResponse({"error": exc.error}, status_code=exc.status)

    @application.exception_handler(RequestValidationError)
    async def handle_validation(request, exc):
        # Never echo passwords or submitted identifying text in validation errors.
        fields = sorted({str(error["loc"][-1]) for error in exc.errors()})
        return JSONResponse(
            {
                "error": {
                    "code": "INVALID_INPUT",
                    "message": "Check the required format and allowed values for: "
                    + ", ".join(fields),
                }
            },
            status_code=422,
        )

    @application.exception_handler(HTTPException)
    async def handle_http(request, exc):
        return JSONResponse(
            {
                "error": {
                    "code": "NOT_FOUND"
                    if exc.status_code == 404
                    else "REQUEST_REJECTED",
                    "message": "The requested resource is not available.",
                }
            },
            status_code=exc.status_code,
        )

    @application.exception_handler(sqlite3.Error)
    async def handle_database(request, exc):
        return JSONResponse(
            {
                "error": {
                    "code": "STORAGE_UNAVAILABLE",
                    "message": "Local storage is temporarily unavailable. No success is confirmed; retain your input and retry with the same action key.",
                }
            },
            status_code=503,
        )

    @application.middleware("http")
    async def local_boundary(request, call_next):
        def rejected(status, code, message):
            return JSONResponse(
                {"error": {"code": code, "message": message}}, status_code=status
            )

        response = None
        try:
            parsed = urlsplit("http://" + request.headers.get("host", ""))
            allowed_host = (
                len(request.headers.getlist("host")) == 1
                and parsed.hostname in ALLOWED_HOSTS
                and parsed.port in allowed_ports
                and parsed.username is None
                and parsed.password is None
                and not parsed.path
                and not parsed.query
                and not parsed.fragment
            )
        except ValueError:
            allowed_host = False
        if not allowed_host:
            response = rejected(
                400,
                "LOCAL_HOST_REQUIRED",
                "This synthetic prototype accepts localhost requests only.",
            )
        elif any(
            name in request.headers
            for name in (
                "forwarded",
                "x-forwarded-host",
                "x-forwarded-proto",
                "x-forwarded-for",
            )
        ):
            response = rejected(
                400,
                "FORWARDED_REQUEST_REJECTED",
                "A proxy cannot change the prototype's local trust boundary.",
            )
        elif len(request.headers.getlist("origin")) > 1 or (
            request.headers.get("origin") is not None
            and request.headers["origin"] not in allowed_origins
        ):
            response = rejected(
                403,
                "ORIGIN_REJECTED",
                "Requests must originate from the local AisleSignals application.",
            )
        elif request.method not in (
            "GET",
            "HEAD",
            "OPTIONS",
        ) and not request.headers.get("origin"):
            response = rejected(
                403,
                "ORIGIN_REQUIRED",
                "Local writes require the application's Origin header.",
            )
        elif request.headers.get("sec-fetch-site") == "cross-site":
            response = rejected(
                403, "CROSS_SITE_REJECTED", "Cross-site requests are not accepted."
            )
        elif request.url.path.startswith("/api/evidence/") and request.url.query:
            response = rejected(
                400,
                "URL_CREDENTIALS_REJECTED",
                "Evidence routes do not accept query parameters or URL credentials.",
            )
        elif request.method in ("POST", "PATCH", "PUT"):
            if (
                not request.headers.get("content-type", "")
                .split(";", 1)[0]
                .strip()
                .lower()
                == "application/json"
            ):
                response = rejected(
                    415, "JSON_REQUIRED", "Use an application/json request body."
                )
            else:
                # Check each ASGI chunk before copying it. Content-Length is not
                # trusted: chunked clients and dishonest lengths have the same cap.
                buffered = bytearray()
                async for chunk in request.stream():
                    if len(buffered) + len(chunk) > 65_536:
                        response = rejected(
                            413,
                            "BODY_TOO_LARGE",
                            "The request is larger than this prototype supports.",
                        )
                        break
                    buffered.extend(chunk)
                if response is None:
                    # Starlette's pinned _CachedRequest forwards this bounded
                    # body to FastAPI, preserving its normal JSON validation.
                    request._body = bytes(buffered)
        if response is None:
            response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["Pragma"] = "no-cache"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Permissions-Policy"] = (
            "camera=(), microphone=(), geolocation=()"
        )
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; media-src 'self' blob:; connect-src 'self'; font-src 'self'; frame-src https://www.youtube-nocookie.com; object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'"
        )
        return response

    @application.get("/api/health")
    def health():
        return dict(status="ok", mode="synthetic-prototype", version="0.1.0")

    @application.post("/api/login")
    def login(body: Login, request: Request):
        stamp = time.time()
        email = body.email.lower()
        ip = request.client.host if request.client else "local"
        with application.state.store.transaction() as conn:
            conn.execute("DELETE FROM login_attempts WHERE at<?", (stamp - 900,))
            conn.execute(
                "DELETE FROM sessions WHERE created_at<? OR last_seen<?",
                (stamp - 8 * 3600, stamp - 900),
            )
            per_email = conn.execute(
                "SELECT COUNT(*) FROM login_attempts WHERE email=?", (email,)
            ).fetchone()[0]
            per_ip = conn.execute(
                "SELECT COUNT(*) FROM login_attempts WHERE ip=?", (ip,)
            ).fetchone()[0]
            if per_email >= 5 or per_ip >= 20:
                problem(
                    429,
                    "LOGIN_RATE_LIMITED",
                    "Too many failed sign-in attempts. Wait fifteen minutes before trying again.",
                )
            user = conn.execute(
                "SELECT * FROM users WHERE email=?", (email,)
            ).fetchone()
            supplied_hash = password_hash(
                body.password, user["salt"] if user else application.state.dummy_salt
            )
            expected_hash = (
                user["password_hash"] if user else application.state.dummy_hash
            )
            if not secrets.compare_digest(supplied_hash, expected_hash):
                conn.execute(
                    "INSERT INTO login_attempts(ip,email,at) VALUES(?,?,?)",
                    (ip, email, stamp),
                )
                conn.commit()
                problem(
                    401,
                    "INVALID_CREDENTIALS",
                    "The email or password was not recognised.",
                )
            token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
            old_token = request.cookies.get(COOKIE, "")
            if old_token:
                conn.execute(
                    "DELETE FROM sessions WHERE token_hash=?", (digest(old_token),)
                )
            conn.execute(
                "INSERT INTO sessions VALUES(?,?,?,?,?)",
                (digest(token), user["id"], csrf, stamp, stamp),
            )
            conn.execute("DELETE FROM login_attempts WHERE email=?", (email,))
            application.state.store.audit(
                conn,
                dict(user),
                "SIGNED_IN",
                "session",
                user["id"],
                "Local synthetic demo session created.",
            )
            response = JSONResponse(dict(user=public_user(user), csrf_token=csrf))
            response.set_cookie(
                COOKIE,
                token,
                max_age=8 * 3600,
                httponly=True,
                samesite="strict",
                secure=False,
                path="/",
            )
            return response

    @application.get("/api/session")
    def session(ctx: Context = Depends(context)):
        return dict(user=public_user(ctx.user), csrf_token=ctx.csrf_token)

    @application.post("/api/logout")
    def logout(ctx: Context = Depends(context)):
        ctx.conn.execute("DELETE FROM sessions WHERE token_hash=?", (ctx.token_hash,))
        ctx.audit(
            "SIGNED_OUT", "session", ctx.user["id"], "Local demo session revoked."
        )
        response = JSONResponse({"ok": True})
        response.delete_cookie(COOKIE, path="/", httponly=True, samesite="strict")
        return response

    @application.get("/api/bootstrap")
    def bootstrap(ctx: Context = Depends(context)):
        return dict(
            user=public_user(ctx.user),
            site=ctx.get("site", ctx.user["site_id"]),
            cameras=ctx.store.listing(ctx.conn, ctx.user, "camera"),
            candidates=[
                candidate_view(item)
                for item in ctx.store.listing(ctx.conn, ctx.user, "candidate")
            ],
            incidents=ctx.store.listing(ctx.conn, ctx.user, "incident"),
            assistance=ctx.store.listing(ctx.conn, ctx.user, "assistance"),
            audit=ctx.store.listing(ctx.conn, ctx.user, "audit"),
            budget=dict(
                monthly_cap_cents=500,
                spent_cents=0,
                engine="LOCAL_TEMPLATE",
                cloud_enabled=False,
            ),
        )

    @application.post("/api/shift")
    def shift(body: Shift, ctx: Context = Depends(context)):
        site = ctx.get("site", ctx.user["site_id"])
        site["shift_active"] = body.active
        ctx.audit(
            "SHIFT_ACTIVATED" if body.active else "SHIFT_ENDED",
            "site",
            site["id"],
            "Synthetic review shift updated. This is not live monitoring coverage.",
        )
        return ctx.put("site", site)

    @application.get("/api/playback-events")
    def playback_events(ctx: Context = Depends(context)):
        return ctx.store.listing(ctx.conn, ctx.user, "playback_event")[:100]

    @application.post("/api/playback-events")
    def record_playback_event(
        body: PlaybackEvent, request: Request, ctx: Context = Depends(context)
    ):
        payload = body.model_dump()
        scope = {
            "organisation_id": ctx.user["organisation_id"],
            "site_id": ctx.user["site_id"],
        }

        def run():
            # Stable identity also deduplicates a lost response retried with a
            # different HTTP key. BEGIN IMMEDIATE serialises concurrent writers.
            event_id = "playback-" + digest(
                encode(
                    {
                        **scope,
                        "actor_id": ctx.user["id"],
                        "run_id": body.run_id,
                        "event_index": body.event_index,
                    }
                )
            )
            previous = ctx.store.get(
                ctx.conn, ctx.user, "playback_event", event_id
            )
            if previous is not None:
                if any(previous.get(key) != value for key, value in payload.items()):
                    problem(
                        409,
                        "PLAYBACK_EVENT_CONFLICT",
                        "This recording test event was already logged with different input. The original record has been preserved.",
                    )
                return previous
            stamp = now()
            item = dict(
                **payload,
                id=event_id,
                source="RECORDED_PLAYBACK_TEST",
                provenance="RULE_BASED_VISUAL_CHANGE_V1",
                created_at=stamp,
                recorded_at=stamp,
                recorded_by=ctx.user["name"],
            )
            ctx.put("playback_event", item)
            ctx.audit(
                "PLAYBACK_TEST_EVENT_LOGGED",
                "playback_event",
                event_id,
                "Recorded-video visual-change test metadata saved. No live incident, identity finding or confirmed speaker delivery is established.",
            )
            return item

        # Include resolved authority in the retry hash so a membership change
        # cannot reveal an earlier branch's cached response for the same actor.
        return idempotent(ctx, request, {**payload, **scope}, run)

    @application.post("/api/simulator")
    def simulator(body: Simulator, request: Request, ctx: Context = Depends(context)):
        ctx.manager()

        def run():
            payload_hash = digest(encode(body.model_dump()))
            previous = ctx.conn.execute(
                "SELECT payload_hash,result FROM source_events WHERE organisation_id=? AND site_id=? AND source_event_id=?",
                (
                    ctx.user["organisation_id"],
                    ctx.user["site_id"],
                    body.source_event_id,
                ),
            ).fetchone()
            if previous:
                if previous["payload_hash"] != payload_hash:
                    problem(
                        409,
                        "SOURCE_EVENT_CONFLICT",
                        "This source event identifier already belongs to a different simulation.",
                    )
                return json.loads(previous["result"])
            camera = ctx.store.listing(ctx.conn, ctx.user, "camera")[0]
            if body.scenario.startswith("CAMERA_"):
                camera["status"] = {
                    "CAMERA_OFFLINE": "OFFLINE",
                    "CAMERA_FROZEN": "FROZEN",
                    "CAMERA_RECOVERED": "DEMO_ONLINE",
                }[body.scenario]
                camera["detail"] = {
                    "OFFLINE": "Simulated disconnection. Coverage is unavailable.",
                    "FROZEN": "Simulated frozen image. Coverage is unavailable.",
                    "DEMO_ONLINE": "Synthetic source only. No physical camera is connected.",
                }[camera["status"]]
                if body.scenario == "CAMERA_RECOVERED":
                    camera["last_seen_at"] = now()
                camera["version"] += 1
                ctx.put("camera", camera)
                ctx.audit(
                    "SIMULATOR_HEALTH_CHANGED", "camera", camera["id"], camera["detail"]
                )
                result = {"ok": True}
            else:
                if not ctx.get("site", ctx.user["site_id"])["shift_active"]:
                    problem(
                        409,
                        "SHIFT_REQUIRED",
                        "Start a synthetic review shift before generating a new observation.",
                    )
                if (
                    camera["status"] != "DEMO_ONLINE"
                    and body.scenario != "HISTORICAL_EVENT"
                ):
                    problem(
                        409,
                        "CAMERA_UNAVAILABLE",
                        "Recover the simulated camera before generating current observations.",
                    )
                result = ctx.store.candidate(
                    camera,
                    body.scenario,
                    1500 if body.scenario == "HISTORICAL_EVENT" else 0,
                )
                ctx.put("candidate", result)
                ctx.audit(
                    "SIMULATOR_OBSERVATION",
                    "candidate",
                    result["id"],
                    "Synthetic historical context; no current alert."
                    if result["historical"]
                    else "Synthetic observation created for human review.",
                )
            ctx.conn.execute(
                "INSERT INTO source_events VALUES(?,?,?,?,?)",
                (
                    ctx.user["organisation_id"],
                    ctx.user["site_id"],
                    body.source_event_id,
                    payload_hash,
                    encode(result),
                ),
            )
            return result

        return idempotent(ctx, request, body.model_dump(), run)

    @application.post("/api/candidates/{candidate_id}/acknowledge")
    def acknowledge(candidate_id: str, body: Version, ctx: Context = Depends(context)):
        item = ctx.get("candidate", candidate_id)
        match_version(item, body.expected_version)
        if item["status"] != "NEW":
            problem(
                409, "CANDIDATE_STATE", "Only a new observation can be acknowledged."
            )
        item["status"], item["version"] = "ACKNOWLEDGED", item["version"] + 1
        ctx.audit(
            "CANDIDATE_ACKNOWLEDGED",
            "candidate",
            candidate_id,
            "A staff member acknowledged the review item. No conclusion recorded.",
        )
        return ctx.put("candidate", item)

    @application.post("/api/candidates/{candidate_id}/review")
    def review(
        candidate_id: str,
        body: Review,
        request: Request,
        ctx: Context = Depends(context),
    ):
        ctx.get(
            "candidate", candidate_id
        )  # Scope checked even on an idempotent replay.

        def run():
            item = ctx.get("candidate", candidate_id)
            match_version(item, body.expected_version)
            if item["status"] not in ("NEW", "ACKNOWLEDGED"):
                problem(
                    409,
                    "CANDIDATE_TERMINAL",
                    "This observation already has a final review decision.",
                )
            item["status"] = "DISMISSED" if body.decision == "DISMISS" else "CONVERTED"
            if body.decision == "OPEN_INCIDENT":
                incident = new_incident(ctx, item["title"], body.reason, candidate_id)
                item["incident_id"] = incident["id"]
            item["version"] += 1
            ctx.audit(
                "CANDIDATE_" + item["status"], "candidate", candidate_id, body.reason
            )
            return ctx.put("candidate", item)

        return idempotent(ctx, request, body.model_dump(), run)

    @application.post("/api/incidents")
    def create_incident(
        body: IncidentCreate, request: Request, ctx: Context = Depends(context)
    ):
        return idempotent(
            ctx,
            request,
            body.model_dump(),
            lambda: new_incident(ctx, body.title, body.notes),
        )

    @application.patch("/api/incidents/{incident_id}")
    def patch_incident(
        incident_id: str, body: IncidentPatch, ctx: Context = Depends(context)
    ):
        item = open_incident(ctx, incident_id, body.expected_version)
        updates = body.model_dump(exclude_unset=True)
        del updates["expected_version"]
        if not updates:
            problem(422, "EMPTY_UPDATE", "Choose a field to update.")
        if any(
            updates.get(key) is None
            for key in ("title", "notes", "classification", "outcome")
            if key in updates
        ):
            problem(
                422,
                "REQUIRED_VALUE",
                "Text, classification and outcome fields cannot be cleared to null.",
            )
        if (
            "loss_cents" in updates
            or "recovered_cents" in updates
            or updates.get("classification") == "STORE_CONFIRMED_LOSS"
        ):
            ctx.manager()
        item.update(updates)
        check_financials(item)
        return history(
            ctx,
            item,
            "INCIDENT_UPDATED",
            "Updated fields: "
            + ", ".join(sorted(updates))
            + ". Previous report draft invalidated.",
        )

    @application.post("/api/incidents/{incident_id}/close")
    def close(incident_id: str, body: Reason, ctx: Context = Depends(context)):
        item = open_incident(ctx, incident_id, body.expected_version)
        if item["outcome"] == "UNRESOLVED":
            problem(
                409,
                "OUTCOME_REQUIRED",
                "Record an outcome before closing the incident.",
            )
        if any(not task["done"] for task in item["tasks"]):
            problem(
                409,
                "TASKS_INCOMPLETE",
                "Complete the outstanding follow-up tasks before closing.",
            )
        check_financials(item)
        item["status"] = "CLOSED"
        return history(ctx, item, "INCIDENT_CLOSED", body.reason)

    @application.post("/api/incidents/{incident_id}/reopen")
    def reopen(incident_id: str, body: Reason, ctx: Context = Depends(context)):
        item = ctx.get("incident", incident_id)
        ctx.manager()
        match_version(item, body.expected_version)
        if item["status"] != "CLOSED":
            problem(409, "INCIDENT_ALREADY_OPEN", "This incident is already open.")
        item["status"] = "OPEN"
        return history(ctx, item, "INCIDENT_REOPENED", body.reason)

    @application.post("/api/incidents/{incident_id}/tasks")
    def create_task(
        incident_id: str,
        body: TaskCreate,
        request: Request,
        ctx: Context = Depends(context),
    ):
        ctx.get("incident", incident_id)

        def run():
            item = open_incident(ctx, incident_id, body.expected_version)
            if len(item["tasks"]) >= 100:
                problem(
                    409,
                    "TASK_LIMIT",
                    "This prototype supports at most 100 tasks per case.",
                )
            item["tasks"].append(
                dict(
                    id=ident(),
                    title=body.title,
                    assignee=body.assignee,
                    due_at=body.due_at.isoformat(),
                    done=False,
                )
            )
            return history(ctx, item, "TASK_CREATED", body.title)

        return idempotent(ctx, request, body.model_dump(mode="json"), run)

    @application.post("/api/incidents/{incident_id}/tasks/{task_id}/complete")
    def complete_task(
        incident_id: str, task_id: str, body: Version, ctx: Context = Depends(context)
    ):
        item = open_incident(ctx, incident_id, body.expected_version)
        task = next((task for task in item["tasks"] if task["id"] == task_id), None)
        if task is None:
            problem(404, "NOT_FOUND", "The requested task is not available.")
        if task["done"]:
            problem(409, "TASK_ALREADY_COMPLETE", "This task is already complete.")
        task["done"] = True
        return history(ctx, item, "TASK_COMPLETED", task["title"])

    @application.post("/api/incidents/{incident_id}/draft")
    def draft(incident_id: str, body: Version, ctx: Context = Depends(context)):
        item = ctx.get("incident", incident_id)
        match_version(item, body.expected_version)
        site = ctx.get("site", ctx.user["site_id"])
        # Deliberately excludes free-text notes/title, appearance and identity allegations.
        money = lambda value: (
            "Not established" if value is None else f"EUR {value / 100:.2f}"
        )
        text = "\n".join(
            [
                "SYNTHETIC PROTOTYPE · LOCAL FACTUAL TEMPLATE · STAFF REVIEW REQUIRED",
                f"Case: {item['reference']}",
                f"Pharmacy: {site['name']}",
                f"Created: {item['created_at']}",
                f"Classification selected by staff: {item['classification']}",
                f"Recorded outcome: {item['outcome']}",
                f"Status: {item['status']}",
                f"Recorded loss: {money(item['loss_cents'])}",
                f"Recorded recovery: {money(item['recovered_cents'])}",
                f"Follow-up tasks: {sum(t['done'] for t in item['tasks'])} completed of {len(item['tasks'])}.",
                "This template does not establish a person's identity, intent or criminality. No real footage was processed.",
            ]
        )
        item["draft"] = dict(
            text=text, engine="LOCAL_TEMPLATE", approved=False, created_at=now()
        )
        return history(
            ctx,
            item,
            "DRAFT_CREATED",
            "Zero-cost local template generated from structured reviewed fields; no AI inference or external request.",
            invalidate_draft=False,
        )

    @application.post("/api/incidents/{incident_id}/draft/approve")
    def approve_draft(incident_id: str, body: Version, ctx: Context = Depends(context)):
        item = ctx.get("incident", incident_id)
        match_version(item, body.expected_version)
        if not item["draft"]:
            problem(
                409, "DRAFT_REQUIRED", "Generate a current draft before approving it."
            )
        if item["draft"]["approved"]:
            problem(
                409,
                "DRAFT_ALREADY_APPROVED",
                "The current draft has already been approved.",
            )
        item["draft"]["approved"] = True
        return history(
            ctx,
            item,
            "DRAFT_APPROVED",
            "A staff member reviewed and approved the current factual template.",
            invalidate_draft=False,
        )

    @application.post("/api/incidents/{incident_id}/export")
    def export(incident_id: str, body: Export, ctx: Context = Depends(context)):
        item = ctx.get("incident", incident_id)
        ctx.manager()
        match_version(item, body.expected_version)
        payload = dict(
            format="AisleSignals synthetic case record v0.1",
            provenance="SYNTHETIC PROTOTYPE. No real video or person identity. This is not a real video evidence package.",
            exported_at=now(),
            exported_by=public_user(ctx.user),
            purpose=body.purpose,
            site=ctx.get("site", ctx.user["site_id"]),
            incident=item,
        )
        canonical = encode(payload)
        manifest = {
            "record": payload,
            "sha256": hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
            "digest_format": "SHA256 of UTF-8 JSON record, keys sorted, separators comma/colon, ensure_ascii=false",
        }
        ctx.audit("INCIDENT_EXPORTED", "incident", incident_id, body.purpose)
        return Response(
            encode(manifest),
            media_type="application/json",
            headers={
                "Content-Disposition": f'attachment; filename="{item["reference"]}-synthetic-record.json"'
            },
        )

    @application.post("/api/assistance")
    def assistance(
        body: AssistanceCreate, request: Request, ctx: Context = Depends(context)
    ):
        def run():
            item = dict(
                id=ident(),
                reason=body.reason,
                status="REQUESTED",
                created_at=now(),
                requested_by=ctx.user["name"],
            )
            ctx.audit(
                "ASSISTANCE_REQUESTED",
                "assistance",
                item["id"],
                body.reason
                + " Request recorded locally; no emergency service or alarm was contacted.",
            )
            return ctx.put("assistance", item)

        return idempotent(ctx, request, body.model_dump(), run)

    @application.post("/api/assistance/{assistance_id}/transition")
    def transition_assistance(
        assistance_id: str, body: AssistanceTransition, ctx: Context = Depends(context)
    ):
        item = ctx.get("assistance", assistance_id)
        valid = (item["status"], body.status) in (
            ("REQUESTED", "ACKNOWLEDGED"),
            ("REQUESTED", "RESOLVED"),
            ("ACKNOWLEDGED", "RESOLVED"),
        )
        if not valid:
            problem(
                409,
                "ASSISTANCE_STATE",
                "This assistance request cannot make the selected state transition.",
            )
        item["status"] = body.status
        ctx.audit(
            "ASSISTANCE_" + body.status,
            "assistance",
            assistance_id,
            "Acknowledged does not mean a colleague has arrived."
            if body.status == "ACKNOWLEDGED"
            else "Assistance request resolved by staff.",
        )
        return ctx.put("assistance", item)

    @application.get("/api/evidence/{candidate_id}")
    def evidence(candidate_id: str, ctx: Context = Depends(context)):
        item = candidate_view(ctx.get("candidate", candidate_id))
        if item["media_status"] != "AVAILABLE":
            problem(
                404,
                "EVIDENCE_UNAVAILABLE",
                "This synthetic observation has missing or expired media.",
            )
        title = html.escape(item["title"])
        scene = f"""<svg xmlns="http://www.w3.org/2000/svg" width="960" height="540" viewBox="0 0 960 540" role="img" aria-label="Synthetic schematic, not camera footage"><rect width="960" height="540" fill="#102d2b"/><path d="M0 365L480 260 960 365V540H0Z" fill="#1e4942"/><g stroke="#6b9b8b" stroke-width="4" fill="#234d45"><path d="M75 130H330V395H75Z M630 130H885V395H630Z"/><path d="M75 215H330M75 300H330M630 215H885M630 300H885"/></g><g fill="#99c9ae"><rect x="104" y="155" width="40" height="52" rx="5"/><rect x="167" y="161" width="46" height="46" rx="5"/><rect x="235" y="150" width="48" height="57" rx="5"/><rect x="673" y="156" width="44" height="51" rx="5"/><rect x="748" y="158" width="59" height="49" rx="5"/></g><rect x="24" y="24" width="912" height="50" rx="10" fill="#f4d578"/><text x="44" y="57" fill="#183a30" font-family="sans-serif" font-size="22" font-weight="700">SYNTHETIC SCHEMATIC · NO REAL CAMERA FOOTAGE</text><text x="40" y="458" fill="#e4f2e8" font-family="sans-serif" font-size="24">{title}</text><text x="40" y="497" fill="#a2c4b5" font-family="sans-serif" font-size="18">Human review workflow demonstration. No person or loss inferred.</text></svg>"""
        return Response(scene, media_type="image/svg+xml")

    @application.get("/{path:path}")
    def static(path: str):
        if path == "api" or path.startswith("api/"):
            problem(404, "NOT_FOUND", "The requested API route does not exist.")
        base = application.state.web_dist
        target = (base / path).resolve()
        if not target.is_relative_to(base) or any(
            part.startswith(".") for part in Path(path).parts
        ):
            problem(404, "NOT_FOUND", "The requested file is not available.")
        if target.is_file():
            return FileResponse(target)
        if Path(path).suffix or path.startswith("assets/"):
            problem(404, "NOT_FOUND", "The requested asset does not exist.")
        index = base / "index.html"
        if not index.is_file():
            return JSONResponse(
                {
                    "error": {
                        "code": "WEB_BUILD_REQUIRED",
                        "message": "Build apps/web before opening the prototype. The API is running.",
                    }
                },
                status_code=503,
            )
        return FileResponse(index)

    return application


app = create_app()
