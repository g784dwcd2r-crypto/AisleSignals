"""AisleSignals: localhost-only workflow and browser pose-observation prototype.

The browser acquires selected video and reports local pose-rule metadata. Explicit
experimental interaction jobs send sampled JPEGs to a loopback vision model and
retain private review evidence briefly. No cloud inference, biometric recognition,
external alarm actuation or billing.
Run with: uvicorn services.api.app:app --host 127.0.0.1 --port 8765
"""

from __future__ import annotations
import hashlib
import html
import json
import os
import secrets
import sqlite3
import stat
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable
from urllib.parse import urlsplit
from pydantic import Field

from fastapi import Depends, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, Response
from starlette.exceptions import HTTPException

from .models import (
    Input,
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
    LiveEventInput,
    camera_provenance,
)
from .store import Store, now, ident, encode, digest, password_hash
from .interactions import install_interactions, interaction_lifespan
from .interaction_vision import MAX_BODY_BYTES

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


class SiteSwitch(Input):
    site_id: str = Field(min_length=36, max_length=36, pattern=r"^[0-9a-f-]{36}$")


def session_payload(store, conn, user, csrf):
    return dict(
        user=public_user(user), csrf_token=csrf, mode=store.mode,
        current_site_id=user["site_id"], allowed_sites=store.allowed_sites(conn, user),
    )


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
        user = request.app.state.store.resolve_session_user(conn, session)
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
            if (
                request.app.state.store.mode == "pilot"
                and request.url.path not in {"/api/logout", "/api/session/site"}
                and request.headers.get("x-aislesignals-site") != user["site_id"]
            ):
                problem(
                    409, "SITE_CONTEXT_CHANGED",
                    "The active branch changed. Stop capture and reload this branch before submitting.",
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


def runtime_health_site(request: Request):
    """Authenticate one committed WAL snapshot without competing for the writer.

    Evidence admission can legitimately hold the writer longer than a browser
    heartbeat. This GET must neither renew idle sessions nor acquire a write
    reservation just to authenticate. All authority and provenance checks share
    the same read snapshot; later heartbeats see committed revocations afresh.
    No mutable Context escapes this dependency.
    """
    token = request.cookies.get(COOKIE, "")
    if not token or len(token) > 256:
        problem(401, "AUTH_REQUIRED", "Sign in to continue.")
    store = request.app.state.store
    conn = sqlite3.connect(
        Path(os.path.abspath(store.path)).as_uri() + "?mode=ro",
        uri=True, timeout=0.25,
    )
    try:
        conn.row_factory = sqlite3.Row
        conn.execute("BEGIN")
        try:
            store.validate_provenance(conn, store.mode)
        except RuntimeError:
            problem(503, "STORAGE_UNAVAILABLE", "Local storage could not be verified. Monitoring must remain stopped.")
        session = conn.execute(
            "SELECT * FROM sessions WHERE token_hash=?", (digest(token),)
        ).fetchone()
        stamp = time.time()
        if (
            session is None
            or stamp - session["created_at"] >= 8 * 3600
            or stamp - session["last_seen"] >= 15 * 60
        ):
            # An expired heartbeat never needs a DELETE to reject authority.
            problem(
                401, "SESSION_EXPIRED",
                "Your session expired. Sign in again; unsaved input has not been submitted.",
            )
        user = store.resolve_session_user(conn, session)
        if user is None:
            problem(401, "AUTH_REQUIRED", "Sign in to continue.")
        return user["site_id"]
    finally:
        conn.close()


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
        "Human-created incident record; no finding of criminality.",
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
    payload_hash = digest(encode({
        "payload": payload, "organisation_id": ctx.user["organisation_id"],
        "site_id": ctx.user["site_id"],
    }))
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


def create_app(db_path=None, web_dist=None, mode=None):
    mode = mode or os.environ.get("AISLESIGNALS_MODE", "synthetic")
    if mode not in {"synthetic", "pilot"}:
        raise ValueError(
            "AISLESIGNALS_MODE must be synthetic or pilot."
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
        title="AisleSignals local pharmacy application",
        version="0.1.0",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        lifespan=interaction_lifespan,
    )
    application.state.store = Store(
        db_path
        or os.environ.get("AISLESIGNALS_DB_PATH")
        or ROOT / ".local" / ("aislesignals-pilot.db" if mode == "pilot" else "aislesignals.db"),
        mode=mode,
    )
    application.state.web_dist = Path(
        web_dist
        or os.environ.get("AISLESIGNALS_WEB_DIST")
        or ROOT / "apps" / "web" / "dist"
    ).resolve()
    application.state.dummy_salt = secrets.token_hex(16)
    application.state.dummy_hash = password_hash(
        "invalid-user", application.state.dummy_salt,
        600_000 if mode == "pilot" else 210_000,
    )
    # Safe browser health projection. Never expose launcher paths, free-text detail,
    # command lines or tokens. A process identity changes even in direct dev mode.
    api_id = secrets.token_hex(16)
    supervised = mode == "pilot" and os.environ.get("AISLESIGNALS_PILOT_SUPERVISED_CHILD") == "1"
    vision_interlocked = os.environ.get("AISLESIGNALS_VISION_DISABLED") == "1"
    report_path = Path(application.state.store.path).parent / "runtime-status.json"

    def read_runtime_report():
        """Read an atomically replaced launcher report across Windows sharing races."""
        last_error = None
        for attempt in range(3):
            try:
                if report_path.is_symlink():
                    raise ValueError("Report must be a regular private file")
                fd = os.open(report_path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0))
                with os.fdopen(fd, "rb") as stream:
                    if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                        raise ValueError("Invalid report file")
                    return stream.read(32769)
            except OSError as exc:
                last_error = exc
                if attempt < 2:
                    time.sleep(0.005)
        raise last_error

    def runtime_snapshot():
        value = dict(api_id=api_id, runtime_id=None, recovery_generation=0,
                     supervised=supervised, state="API_ONLY", monitoring_allowed=not supervised,
                     product_available=None, report_age_ms=None, context=None)
        if supervised:
            value["state"] = "UNAVAILABLE"
            try:
                raw = read_runtime_report()
                if len(raw) > 32768:
                    raise ValueError("Report too large")
                report = json.loads(raw)
                runtime_id = report.get("runtime_id", "")
                generation = report.get("recovery_generation")
                if (report.get("schema_version") != 1 or not isinstance(runtime_id, str)
                        or len(runtime_id) != 32 or any(c not in "0123456789abcdef" for c in runtime_id)
                        or type(generation) is not int or not 0 <= generation <= 1_000_000):
                    raise ValueError("Invalid report identity")
                stamp = datetime.fromisoformat(report["generated_at"].replace("Z", "+00:00"))
                if stamp.tzinfo is None:
                    raise ValueError("Timestamp requires timezone")
                age = (datetime.now(timezone.utc) - stamp).total_seconds()
                state = report.get("state")
                services = report["services"]
                if not isinstance(services, list) or len(services) > 2:
                    raise ValueError("Invalid services")
                by_name = {entry["name"]: entry for entry in services}
                if ((set(by_name) != {"api", "vision"} and not (vision_interlocked and set(by_name) == {"api"}))
                        or len(by_name) != len(services)):
                    raise ValueError("Missing services")
                def ready(name):
                    item = by_name[name]
                    checked = datetime.fromisoformat(item["last_checked_at"].replace("Z", "+00:00"))
                    return (checked.tzinfo is not None and item.get("health") == "READY"
                            and item.get("running") is True and item.get("disabled") is False
                            and -2 <= (datetime.now(timezone.utc) - checked).total_seconds() <= 8)
                api_ready = ready("api")
                vision_disabled = vision_interlocked and ("vision" not in by_name or by_name["vision"].get("disabled") is True)
                vision_ready = not vision_interlocked and "vision" in by_name and ready("vision")
                allowed_state = state in {"SERVICES_READY", "REARM_REQUIRED"} or (state == "DEGRADED" and vision_disabled)
                allowed = (-2 <= age <= 8 and allowed_state and api_ready and (vision_ready or vision_disabled))
                value.update(runtime_id=runtime_id, recovery_generation=generation,
                             state=state if state in {"STARTING", "SERVICES_READY", "DEGRADED", "RECOVERING", "REARM_REQUIRED", "FAILED", "STOPPED"} else "UNAVAILABLE",
                             monitoring_allowed=allowed, product_available=allowed and vision_ready,
                             report_age_ms=round(age * 1000))
            except (OSError, ValueError, TypeError, KeyError, AttributeError, OverflowError):
                pass  # A malformed/missing/stale report fails closed, without disclosing its path.
        if value["monitoring_allowed"]:
            value["context"] = digest(f"{api_id}:{value['runtime_id']}:{value['recovery_generation']}")
        return value

    from .cloud_connection import install_cloud_connection
    install_cloud_connection(application, context, problem)
    install_interactions(application, context, problem, new_incident, idempotent)
    def job_runtime_current(item):
        bound = item.get("runtime_context")
        if bound is None and not supervised:
            return True  # Direct legacy/dev API callers; no supervised coverage claimed.
        current = runtime_snapshot()
        return (isinstance(bound, str) and len(bound) == 64 and all(c in "0123456789abcdef" for c in bound) and current["monitoring_allowed"]
                and secrets.compare_digest(bound, current["context"]))
    application.state.interactions.runtime_current = job_runtime_current

    from .pilot_admin import install_pilot_admin
    install_pilot_admin(application, context, problem)

    from .xai_verifier import install_xai_verifier
    install_xai_verifier(application, context, problem)

    from .layout_calibration import install_layout_calibration
    install_layout_calibration(application, context, problem)

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
                "This application accepts localhost requests only.",
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
                body_limit = (
                    MAX_BODY_BYTES
                    if request.method == "POST" and request.url.path == "/api/interactions/jobs"
                    else 65_536
                )
                async for chunk in request.stream():
                    if len(buffered) + len(chunk) > body_limit:
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
        # Only active monitoring is fenced. Cancellation, reviewed history and
        # casework remain accessible while the model/runtime recovers.
        monitor_route = (
            request.method == "POST" and request.url.path in {"/api/interactions/jobs", "/api/live-events"}
            or request.method == "GET" and request.url.path.startswith("/api/interactions/jobs/")
            and request.url.path.count("/") == 4
        )
        supplied_context = request.headers.get("x-aislesignals-runtime")
        guarded = monitor_route and (supervised or supplied_context is not None)
        def valid_runtime():
            current = runtime_snapshot()
            return (current["monitoring_allowed"] and isinstance(supplied_context, str)
                    and len(supplied_context) == 64 and all(c in "0123456789abcdef" for c in supplied_context)
                    and secrets.compare_digest(supplied_context, current["context"]))
        if response is None and guarded and not valid_runtime():
            response = rejected(409, "RUNTIME_CONTEXT_CHANGED", "Monitoring requires a fresh healthy runtime context. Restart detection explicitly after recovery.")
        if response is None:
            response = await call_next(request)
            if guarded and response.status_code < 400 and not valid_runtime():
                response = rejected(409, "RUNTIME_CONTEXT_CHANGED", "The runtime changed during this request. No live result may be used; restart after recovery.")
        response.headers["Cache-Control"] = "no-store"
        response.headers["Pragma"] = "no-cache"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Permissions-Policy"] = (
            "camera=(self), display-capture=(self), microphone=(), geolocation=()"
        )
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self' 'wasm-unsafe-eval'; worker-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; media-src 'self' blob:; connect-src 'self'; font-src 'self'; frame-src https://www.youtube-nocookie.com; object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'"
        )
        return response

    @application.get("/api/health")
    def health():
        return dict(status="ok", mode="pilot" if mode == "pilot" else "synthetic-prototype", version="0.1.0")

    @application.get("/api/runtime/health")
    def browser_health(site_id=Depends(runtime_health_site)):
        return {**runtime_snapshot(), "site_id": site_id}

    @application.get("/api/runtime")
    def runtime():
        with application.state.store.transaction() as conn:
            ready = mode == "synthetic" or conn.execute(
                "SELECT 1 FROM account_security a JOIN memberships m ON m.user_id=a.user_id "
                "WHERE a.enabled=1 AND m.role='MANAGER' LIMIT 1"
            ).fetchone() is not None
        return dict(
            mode=mode, setup_required=not ready, local_only=True,
            authentication="local_named_password" if mode == "pilot" else "synthetic_demo",
            mfa_enabled=False,
        )

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
            security = conn.execute(
                "SELECT * FROM account_security WHERE user_id=?", (user["id"],)
            ).fetchone() if user and mode == "pilot" else None
            iterations = (
                security["password_iterations"] if security else
                (600_000 if mode == "pilot" else 210_000)
            )
            supplied_hash = password_hash(
                body.password, user["salt"] if user else application.state.dummy_salt,
                iterations,
            )
            expected_hash = (
                user["password_hash"] if user else application.state.dummy_hash
            )
            permitted_sites = application.state.store.allowed_sites(conn, user) if user else []
            valid = secrets.compare_digest(supplied_hash, expected_hash)
            if not valid or not permitted_sites or (mode == "pilot" and (not security or not security["enabled"])):
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
            user = dict(user)
            if mode == "pilot":
                selected = next((site for site in permitted_sites if site["id"] == user["site_id"]), permitted_sites[0])
                user.update(site_id=selected["id"], role=selected["role"])
                conn.execute("INSERT INTO session_scopes VALUES(?,?,?)", (
                    digest(token), user["organisation_id"], user["site_id"],
                ))
            conn.execute("DELETE FROM login_attempts WHERE email=?", (email,))
            application.state.store.audit(
                conn,
                dict(user),
                "SIGNED_IN",
                "session",
                user["id"],
                "Local named-account session created." if mode == "pilot" else "Local synthetic demo session created.",
            )
            response = JSONResponse(session_payload(application.state.store, conn, user, csrf))
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
        return session_payload(ctx.store, ctx.conn, ctx.user, ctx.csrf_token)

    @application.post("/api/session/site")
    def switch_site(body: SiteSwitch, ctx: Context = Depends(context)):
        sites = ctx.store.allowed_sites(ctx.conn, ctx.user)
        selected = next((site for site in sites if site["id"] == body.site_id), None)
        if selected is None:
            problem(404, "SITE_NOT_AVAILABLE", "This branch is not available to your account.")
        if mode == "synthetic" or body.site_id == ctx.user["site_id"]:
            return session_payload(ctx.store, ctx.conn, ctx.user, ctx.csrf_token)
        token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
        current = ctx.conn.execute("SELECT created_at FROM sessions WHERE token_hash=?", (ctx.token_hash,)).fetchone()
        # Rotation revokes pending jobs and credentials held by stale tabs.
        # Preserve absolute expiry: branch switching never extends the workday.
        ctx.conn.execute("DELETE FROM sessions WHERE token_hash=?", (ctx.token_hash,))
        ctx.conn.execute("INSERT INTO sessions VALUES(?,?,?,?,?)", (
            digest(token), ctx.user["id"], csrf, current["created_at"], time.time(),
        ))
        ctx.conn.execute("INSERT INTO session_scopes VALUES(?,?,?)", (
            digest(token), selected["organisation_id"], selected["id"],
        ))
        ctx.audit("BRANCH_LEFT", "site", ctx.user["site_id"], "Session rotated before changing active branch.")
        user = {**ctx.user, "site_id": selected["id"], "role": selected["role"]}
        ctx.store.audit(ctx.conn, user, "BRANCH_SELECTED", "site", user["site_id"], "Authorised branch selected; previous capture session revoked.")
        # The browser starts the new branch heartbeat as soon as this response
        # arrives. Commit the rotated session before exposing its cookie; yield
        # dependency cleanup may otherwise finish after a fast client request.
        ctx.conn.commit()
        response = JSONResponse(session_payload(ctx.store, ctx.conn, user, csrf))
        response.set_cookie(COOKIE, token, max_age=max(1, int(current["created_at"] + 8 * 3600 - time.time())), httponly=True, samesite="strict", secure=False, path="/")
        return response

    @application.post("/api/logout")
    def logout(ctx: Context = Depends(context)):
        ctx.conn.execute("DELETE FROM sessions WHERE token_hash=?", (ctx.token_hash,))
        ctx.audit(
            "SIGNED_OUT", "session", ctx.user["id"], "Local session revoked."
        )
        response = JSONResponse({"ok": True})
        response.delete_cookie(COOKIE, path="/", httponly=True, samesite="strict")
        return response

    @application.get("/api/bootstrap")
    def bootstrap(ctx: Context = Depends(context)):
        return dict(
            user=public_user(ctx.user),
            mode=ctx.store.mode,
            current_site_id=ctx.user["site_id"],
            allowed_sites=ctx.store.allowed_sites(ctx.conn, ctx.user),
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
            "Review shift updated. This setting alone does not establish monitoring coverage.",
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

    @application.get("/api/live-events")
    def live_events(ctx: Context = Depends(context)):
        return ctx.store.listing(ctx.conn, ctx.user, "live_event")[:100]

    @application.post("/api/live-events")
    def record_live_event(
        body: LiveEventInput, request: Request, ctx: Context = Depends(context)
    ):
        payload = body.model_dump(mode="json", exclude_none=True)
        scope = {
            "organisation_id": ctx.user["organisation_id"],
            "site_id": ctx.user["site_id"],
        }

        def run():
            # A run is local to an actor and branch. A browser-supplied ID
            # never grants access and a lost-response retry cannot log twice.
            event_id = "live-" + digest(
                encode(
                    {
                        **scope,
                        "actor_id": ctx.user["id"],
                        "run_id": body.run_id,
                        "event_id": body.event_id,
                    }
                )
            )
            previous = ctx.store.get(ctx.conn, ctx.user, "live_event", event_id)
            if previous is not None:
                if (
                    previous.get("camera_context") != payload.get("camera_context")
                    or any(previous.get(key) != value for key, value in payload.items())
                ):
                    problem(
                        409,
                        "LIVE_EVENT_CONFLICT",
                        "This pose event was already logged with different input. The original record has been preserved.",
                    )
                return previous
            descriptions = {
                "REPEATED_HAND_TO_WAIST": (
                    "Repeated hand-to-waist movement",
                    "Browser pose rules reported repeated hand movement near the waist. Review the video context; this does not establish concealment or theft.",
                ),
                "RESTRICTED_ZONE_ENTRY": (
                    "Restricted zone entry",
                    "Browser pose rules reported a tracked person entering a user-marked restricted zone. Check the zone and context; this does not establish wrongdoing.",
                ),
            }
            label, detail = descriptions[body.event_code]
            item = dict(
                **payload,
                id=event_id,
                label=label,
                detail=detail,
                created_at=now(),
                acknowledged_at=None,
                acknowledged_by=None,
            )
            item.update(camera_provenance(payload))
            ctx.put("live_event", item)
            ctx.audit(
                "LIVE_POSE_EVENT_LOGGED",
                "live_event",
                event_id,
                "Browser-reported pose-rule metadata saved from "
                + body.source_kind
                + ". Source kind is a client declaration. No footage, identity finding, incident or confirmed speaker delivery is established.",
            )
            return item

        # Cached responses must also honour a changed branch membership.
        return idempotent(ctx, request, {**payload, **scope}, run)

    @application.post("/api/live-events/{event_id}/acknowledge")
    def acknowledge_live_event(
        event_id: str,
        request: Request,
        body: Input = Input(),
        ctx: Context = Depends(context),
    ):
        # Resolve scope before consulting retry results, including after a
        # membership change or when a caller guesses another branch's ID.
        ctx.get("live_event", event_id)

        def run():
            item = ctx.get("live_event", event_id)
            if item["acknowledged_at"] is not None:
                return item
            item["acknowledged_at"] = now()
            item["acknowledged_by"] = ctx.user["name"]
            ctx.put("live_event", item)
            ctx.audit(
                "LIVE_POSE_EVENT_ACKNOWLEDGED",
                "live_event",
                event_id,
                "A staff member acknowledged the pose observation. No incident finding recorded.",
            )
            return item

        return idempotent(
            ctx,
            request,
            {
                **body.model_dump(),
                "organisation_id": ctx.user["organisation_id"],
                "site_id": ctx.user["site_id"],
            },
            run,
        )

    @application.post("/api/simulator")
    def simulator(body: Simulator, request: Request, ctx: Context = Depends(context)):
        ctx.manager()
        if mode != "synthetic":
            problem(403, "SIMULATOR_DISABLED", "Synthetic observations are disabled in pilot mode.")

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
            format="AisleSignals case record v0.1" if mode == "pilot" else "AisleSignals synthetic case record v0.1",
            provenance=("Staff-reviewed local case metadata. Linked experimental observations are not established facts; sampled JPEGs are referenced, not embedded, and retain their original expiry/deletion policy." if mode == "pilot" else "SYNTHETIC PROTOTYPE. No real video or person identity. This is not a real video evidence package."),
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
                "Content-Disposition": f'attachment; filename="{item["reference"]}-{"record" if mode == "pilot" else "synthetic-record"}.json"'
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
