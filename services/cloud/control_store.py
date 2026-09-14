"""PostgreSQL authority, bounded connections and secret-free audit records."""

from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
import hashlib
import hmac
import secrets
import threading
from uuid import uuid4

import psycopg
from psycopg.rows import dict_row
from starlette.requests import Request

from .config import CloudSettings
from .database import SCHEMA_VERSION, SCHEMA_CHECKSUM

COOKIE_NAME = "__Host-aislesignals_session"
SESSION_SECONDS = 8 * 60 * 60
IDLE_SECONDS = 30 * 60


class ControlError(Exception):
    def __init__(self, status_code: int, code: str, message: str):
        super().__init__(code)
        self.status_code, self.code, self.message = status_code, code, message


def unavailable():
    return ControlError(503, "CONTROL_UNAVAILABLE", "The management service is unavailable.")


def unauthorized():
    return ControlError(401, "SESSION_REQUIRED", "Sign in again to continue.")


def forbidden():
    return ControlError(403, "FORBIDDEN", "You do not have access to this action.")


def cookie_name(settings: CloudSettings) -> str:
    return COOKIE_NAME if settings.environment == "staging" else "aislesignals_dev_session"


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class Principal:
    user_id: str
    organisation_id: str
    role: str
    pharmacy_ids: tuple[str, ...]
    session_id: str
    csrf_token: str


class ControlStore:
    def __init__(self, settings: CloudSettings):
        self.settings = settings
        self._capacity = threading.BoundedSemaphore(4)

    @contextmanager
    def transaction(self):
        if not self.settings.database_url or not self.settings.auth_key:
            raise unavailable()
        if not self._capacity.acquire(blocking=False):
            raise unavailable()
        try:
            with psycopg.connect(
                self.settings.database_url, sslmode=self.settings.database_sslmode,
                connect_timeout=3, row_factory=dict_row,
                application_name="aislesignals-cloud-control",
                options="-c timezone=UTC -c statement_timeout=5000 -c lock_timeout=2500 -c idle_in_transaction_session_timeout=10000",
            ) as conn:
                current = conn.execute("SELECT version,checksum FROM aislesignals_control.schema_version WHERE singleton=true").fetchone()
                if not current or (current["version"], current["checksum"]) != (SCHEMA_VERSION, SCHEMA_CHECKSUM):
                    raise unavailable()
                yield conn
        except psycopg.Error:
            raise unavailable() from None
        finally:
            self._capacity.release()

    def throttle(self, action: str, subject: str, *, limit: int = 10) -> None:
        """Persistent fixed windows; opaque bucket names and bounded cardinality.

        Counts are committed before credential verification, including failures.
        Global limit avoids unbounded work via fabricated accounts/challenges.
        """
        denied = False
        with self.transaction() as conn:
            conn.execute("SELECT pg_advisory_xact_lock(6802449210734)")
            conn.execute("DELETE FROM aislesignals_control.auth_attempts WHERE expires_at <= CURRENT_TIMESTAMP")
            count = conn.execute("SELECT count(*) AS n FROM aislesignals_control.auth_attempts").fetchone()["n"]
            if count >= 4096:
                denied = True
            else:
                for label, maximum in [("global:" + action, 240), (action + ":" + subject, limit)]:
                    bucket = hmac.new(self.settings.auth_key.encode(), label.encode(), hashlib.sha256).hexdigest()
                    row = conn.execute("""
                        INSERT INTO aislesignals_control.auth_attempts(bucket,count,window_start,expires_at)
                        VALUES(%s,1,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP + interval '15 minutes')
                        ON CONFLICT(bucket) DO UPDATE SET count=LEAST(auth_attempts.count+1,1000000)
                        RETURNING count
                    """, (bucket,)).fetchone()
                    denied = denied or row["count"] > maximum
        if denied:
            raise ControlError(429, "RATE_LIMITED", "Too many attempts. Try again in 15 minutes.")


def audit(conn, principal: Principal, action: str, subject_id: str | None = None, pharmacy_id: str | None = None):
    conn.execute("""INSERT INTO aislesignals_control.audit_entries
        (id,organisation_id,actor_user_id,action,subject_id,pharmacy_id) VALUES(%s,%s,%s,%s,%s,%s)""",
        (uuid4(), principal.organisation_id, principal.user_id, action, subject_id, pharmacy_id))


def validate_principal(conn, principal: Principal) -> Principal:
    """Recheck within caller's transaction before any read/write.

    Lock ordering: organisation then session/user. Administration takes an
    exclusive organisation lock first, so revocation serializes with actions.
    """
    org = conn.execute("SELECT id FROM aislesignals_control.organisations WHERE id=%s FOR SHARE", (principal.organisation_id,)).fetchone()
    if not org:
        raise unauthorized()
    # Lock users before sessions consistently with successful MFA login, which
    # updates the replay counter before rotating/capping that user's sessions.
    row = conn.execute("""SELECT id,organisation_id,role FROM aislesignals_control.users
        WHERE id=%s AND organisation_id=%s AND active=true FOR SHARE""",
        (principal.user_id, principal.organisation_id)).fetchone()
    if not row:
        raise unauthorized()
    session = conn.execute("""SELECT id FROM aislesignals_control.sessions
        WHERE id=%s AND user_id=%s AND revoked_at IS NULL AND expires_at>CURRENT_TIMESTAMP
        AND last_seen_at>CURRENT_TIMESTAMP-interval '30 minutes' FOR SHARE""",
        (principal.session_id, principal.user_id)).fetchone()
    if not session:
        raise unauthorized()
    pharmacies = conn.execute("""SELECT p.id FROM aislesignals_control.pharmacies p
        WHERE p.organisation_id=%s AND p.active=true AND
        (%s='OWNER' OR EXISTS(SELECT 1 FROM aislesignals_control.user_pharmacies up WHERE up.user_id=%s AND up.pharmacy_id=p.id))
        ORDER BY p.id""", (principal.organisation_id, row["role"], principal.user_id)).fetchall()
    return Principal(str(row["id"]), str(row["organisation_id"]), row["role"], tuple(str(p["id"]) for p in pharmacies), principal.session_id, principal.csrf_token)


def require_origin(request: Request, settings: CloudSettings):
    """Exact same Origin/Host; proxy forwarding is not used as authority here."""
    origin = request.headers.get("origin", "")
    host = request.headers.get("host", "")
    scheme = "https" if settings.environment == "staging" else request.url.scheme
    if not origin or origin != f"{scheme}://{host}" or request.headers.get("sec-fetch-site") == "cross-site":
        raise ControlError(403, "ORIGIN_REQUIRED", "Use the management console from its own secure address.")


def require_principal(request: Request) -> Principal:
    store: ControlStore = request.app.state.control_store
    token = request.cookies.get(cookie_name(store.settings), "")
    if len(token) != 43 or any(c not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-" for c in token):
        raise unauthorized()
    if not store.settings.auth_key:
        raise unavailable()
    csrf = hmac.new(store.settings.auth_key.encode(), ("csrf:" + token).encode(), hashlib.sha256).hexdigest()
    if request.method not in {"GET", "HEAD", "OPTIONS"}:
        require_origin(request, store.settings)
        if not hmac.compare_digest(request.headers.get("x-csrf-token", ""), csrf):
            raise ControlError(403, "CSRF_INVALID", "Refresh your session before trying again.")
    with store.transaction() as conn:
        row = conn.execute("""SELECT s.id,u.id AS user_id,u.organisation_id,u.role
            FROM aislesignals_control.sessions s JOIN aislesignals_control.users u ON u.id=s.user_id
            WHERE s.token_hash=%s""", (token_hash(token),)).fetchone()
        if not row:
            raise unauthorized()
        provisional = Principal(str(row["user_id"]), str(row["organisation_id"]), row["role"], (), str(row["id"]), csrf)
        # Acquire organisation lock before updating session activity to preserve
        # the lock order shared with administration and operation writes.
        conn.execute("SELECT id FROM aislesignals_control.organisations WHERE id=%s FOR SHARE", (provisional.organisation_id,))
        active_user = conn.execute("SELECT id FROM aislesignals_control.users WHERE id=%s AND active=true FOR SHARE", (provisional.user_id,)).fetchone()
        if not active_user:
            raise unauthorized()
        updated = conn.execute("""UPDATE aislesignals_control.sessions SET last_seen_at=CURRENT_TIMESTAMP
            WHERE id=%s AND revoked_at IS NULL AND expires_at>CURRENT_TIMESTAMP
            AND last_seen_at>CURRENT_TIMESTAMP-interval '30 minutes' RETURNING id""", (provisional.session_id,)).fetchone()
        if not updated:
            raise unauthorized()
        return validate_principal(conn, provisional)


def require_owner(request: Request) -> Principal:
    principal = require_principal(request)
    if principal.role != "OWNER":
        raise forbidden()
    return principal


@contextmanager
def owner_transaction(store: ControlStore, principal: Principal):
    with store.transaction() as conn:
        conn.execute("SELECT id FROM aislesignals_control.organisations WHERE id=%s FOR UPDATE", (principal.organisation_id,))
        refreshed = validate_principal(conn, principal)
        if refreshed.role != "OWNER":
            raise forbidden()
        yield conn, refreshed


def pharmacies_for(conn, principal: Principal, *, include_inactive: bool = False):
    return conn.execute("""SELECT id,organisation_id,name,address,timezone,active,version,created_at
        FROM aislesignals_control.pharmacies WHERE organisation_id=%s
        AND (%s OR active=true) AND (%s='OWNER' OR id=ANY(%s::uuid[])) ORDER BY lower(name),id LIMIT 200""",
        (principal.organisation_id, include_inactive and principal.role == "OWNER", principal.role, list(principal.pharmacy_ids))).fetchall()


def session_body(conn, principal: Principal):
    row = conn.execute("SELECT id,name,email,role FROM aislesignals_control.users WHERE id=%s", (principal.user_id,)).fetchone()
    org = conn.execute("SELECT id,name FROM aislesignals_control.organisations WHERE id=%s", (principal.organisation_id,)).fetchone()
    return {"user": row, "organisation": org, "pharmacies": pharmacies_for(conn, principal), "csrf_token": principal.csrf_token}


def create_session(conn, settings: CloudSettings, user_id: str):
    token = secrets.token_urlsafe(32)
    session_id = str(uuid4())
    # Keep per-user active-session count bounded without a cleanup worker.
    conn.execute("""UPDATE aislesignals_control.sessions SET revoked_at=CURRENT_TIMESTAMP
        WHERE user_id=%s AND revoked_at IS NULL AND id NOT IN
        (SELECT id FROM aislesignals_control.sessions WHERE user_id=%s AND revoked_at IS NULL
         ORDER BY created_at DESC,id DESC LIMIT 4)""", (user_id, user_id))
    conn.execute("""INSERT INTO aislesignals_control.sessions(id,token_hash,user_id,expires_at)
        VALUES(%s,%s,%s,CURRENT_TIMESTAMP + interval '8 hours')""", (session_id, token_hash(token), user_id))
    row = conn.execute("SELECT organisation_id,role FROM aislesignals_control.users WHERE id=%s", (user_id,)).fetchone()
    csrf = hmac.new(settings.auth_key.encode(), ("csrf:" + token).encode(), hashlib.sha256).hexdigest()
    principal = validate_principal(conn, Principal(user_id, str(row["organisation_id"]), row["role"], (), session_id, csrf))
    return token, session_body(conn, principal), principal
