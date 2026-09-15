"""Cloud password + mandatory TOTP authentication and owner administration.

All browser credentials remain opaque; PostgreSQL stores credential hashes and
application-key-encrypted MFA secrets. No local pilot modules are imported.
"""

import base64
import hashlib
import hmac
import json
import secrets
import struct
import time
import threading
from contextlib import contextmanager
from urllib.parse import quote, urlencode
from uuid import UUID, uuid4

from cryptography.fernet import Fernet, InvalidToken
from fastapi import APIRouter, Depends, Request, Response

from .config import CloudSettings
from .control_models import (
    SetupBegin, ChallengeComplete, Login, Empty, PharmacyCreate, PharmacyUpdate,
    InvitationCreate, InvitationBegin, UserUpdate,
)
from .control_store import (
    COOKIE_NAME, SESSION_SECONDS, ControlError, ControlStore, Principal, audit,
    cookie_name, create_session, forbidden, owner_transaction, pharmacies_for, require_origin,
    require_owner, require_principal, session_body, token_hash, unavailable,
    validate_principal,
)

PASSWORD_ITERATIONS = 600_000
CHALLENGE_SECONDS = 300
_PASSWORD_CAPACITY = threading.BoundedSemaphore(2)


@contextmanager
def _password_slot():
    if not _PASSWORD_CAPACITY.acquire(blocking=False):
        raise ControlError(429, "RATE_LIMITED", "The sign-in service is busy. Try again shortly.")
    try:
        yield
    finally:
        _PASSWORD_CAPACITY.release()

_DUMMY_SALT = b"aislesignals-dummy-v1"
_DUMMY_HASH = hashlib.pbkdf2_hmac("sha256", b"invalid-synthetic-password", _DUMMY_SALT, PASSWORD_ITERATIONS)


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    with _password_slot():
        digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, PASSWORD_ITERATIONS)
    return "pbkdf2-sha256$600000$" + salt.hex() + "$" + digest.hex()


def verify_password(password: str, encoded: str | None) -> bool:
    valid = False
    salt, expected = _DUMMY_SALT, _DUMMY_HASH
    if encoded:
        try:
            name, rounds, salt_hex, digest_hex = encoded.split("$")
            if name == "pbkdf2-sha256" and rounds == "600000" and len(salt_hex) == 32 and len(digest_hex) == 64:
                salt, expected = bytes.fromhex(salt_hex), bytes.fromhex(digest_hex)
                valid = True
        except ValueError:
            pass
    with _password_slot():
        actual = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, PASSWORD_ITERATIONS)
    return hmac.compare_digest(actual, expected) and valid


def totp_code(secret: str, counter: int, *, digits: int = 6) -> str:
    digest = hmac.new(base64.b32decode(secret), struct.pack(">Q", counter), hashlib.sha1).digest()
    offset = digest[-1] & 15
    number = struct.unpack(">I", digest[offset:offset + 4])[0] & 0x7fffffff
    return str(number % (10 ** digits)).zfill(digits)


def verify_totp(secret: str, code: str, last_counter: int = -1, *, now: float | None = None) -> int | None:
    counter = int(time.time() if now is None else now) // 30
    if len(code) != 6 or not code.isascii() or not code.isdigit():
        return None
    for candidate in (counter, counter - 1, counter + 1):
        if candidate >= 0 and candidate > last_counter and hmac.compare_digest(totp_code(secret, candidate), code):
            return candidate
    return None


def _encrypt(settings, payload: dict) -> str:
    return Fernet(settings.auth_key.encode()).encrypt(json.dumps(payload, separators=(",", ":")).encode()).decode()


def _decrypt(settings, encrypted: str) -> dict:
    try:
        return json.loads(Fernet(settings.auth_key.encode()).decrypt(encrypted.encode()))
    except (InvalidToken, ValueError, TypeError):
        raise unavailable() from None


def _mfa_secret() -> str:
    return base64.b32encode(secrets.token_bytes(20)).decode()


def _enrolment_response(token: str, secret: str, email: str):
    label = quote("AisleSignals:" + email, safe="")
    return {"challenge_token": token, "totp_secret": secret,
            "totp_uri": f"otpauth://totp/{label}?" + urlencode({"secret": secret, "issuer": "AisleSignals", "algorithm": "SHA1", "digits": 6, "period": 30}),
            "expires_in_seconds": CHALLENGE_SECONDS}


def _challenge(conn, settings, kind: str, *, payload=None, user_id=None, user_version=None, invitation_id=None):
    # Bounded cleanup, no secret payload logging, no indefinitely valid challenge.
    conn.execute("SELECT pg_advisory_xact_lock(6802449210736)")
    conn.execute("""DELETE FROM aislesignals_control.auth_challenges WHERE id IN
        (SELECT id FROM aislesignals_control.auth_challenges WHERE expires_at<=CURRENT_TIMESTAMP OR consumed_at IS NOT NULL
         LIMIT 1000 FOR UPDATE SKIP LOCKED)""")
    if conn.execute("SELECT count(*) AS n FROM aislesignals_control.auth_challenges").fetchone()["n"] >= 1000:
        raise ControlError(429, "RATE_LIMITED", "Too many attempts. Try again later.")
    token = secrets.token_urlsafe(32)
    conn.execute("""INSERT INTO aislesignals_control.auth_challenges
        (id,token_hash,kind,user_id,user_version,invitation_id,payload_encrypted,expires_at)
        VALUES(%s,%s,%s,%s,%s,%s,%s,CURRENT_TIMESTAMP + interval '5 minutes')""",
        (uuid4(), token_hash(token), kind, user_id, user_version, invitation_id, _encrypt(settings, payload) if payload else None))
    return token


def _challenge_row(conn, token: str, kind: str):
    row = conn.execute("""SELECT *,expires_at>CURRENT_TIMESTAMP AS valid FROM aislesignals_control.auth_challenges
        WHERE token_hash=%s AND kind=%s FOR UPDATE""", (token_hash(token), kind)).fetchone()
    if not row or not row["valid"] or row["consumed_at"] or row["attempts"] >= 6:
        return ControlError(401, "CHALLENGE_EXPIRED", "Start sign-in or enrolment again.")
    conn.execute("UPDATE aislesignals_control.auth_challenges SET attempts=attempts+1 WHERE id=%s", (row["id"],))
    return row


def _consume(conn, challenge):
    conn.execute("UPDATE aislesignals_control.auth_challenges SET consumed_at=CURRENT_TIMESTAMP,payload_encrypted=NULL WHERE id=%s", (challenge["id"],))


def _session_response(settings, response: Response, token: str, body):
    response.set_cookie(cookie_name(settings), token, max_age=SESSION_SECONDS, httponly=True,
                        secure=settings.environment in {"production", "staging"}, samesite="strict", path="/")
    response.headers["Cache-Control"] = "no-store"
    return body


def _user(conn, user_id: str):
    return conn.execute("""SELECT u.id,u.name,u.email,u.role,u.active,u.version,u.created_at,
        ARRAY(SELECT up.pharmacy_id FROM aislesignals_control.user_pharmacies up WHERE up.user_id=u.id ORDER BY up.pharmacy_id) AS pharmacy_ids
        FROM aislesignals_control.users u WHERE u.id=%s""", (user_id,)).fetchone()


def _validate_scope(conn, organisation_id: str, role: str, ids: list[str], *, require_active: bool = True):
    if (role == "OWNER") != (not ids):
        raise ControlError(422, "INVALID_SCOPE", "Owners have organisation access; other roles require pharmacies.")
    count = conn.execute("SELECT count(*) AS n FROM aislesignals_control.pharmacies WHERE organisation_id=%s AND (%s=false OR active=true) AND id=ANY(%s::uuid[])", (organisation_id, require_active, ids)).fetchone()["n"]
    if count != len(ids):
        raise forbidden()


def _insert_user(conn, settings, *, organisation_id, name, email, password_hash, secret, counter, role, pharmacy_ids):
    user_id = str(uuid4())
    conn.execute("""INSERT INTO aislesignals_control.users
        (id,organisation_id,name,email,role,password_hash,totp_encrypted,last_totp_counter)
        VALUES(%s,%s,%s,%s,%s,%s,%s,%s)""", (user_id, organisation_id, name, email, role, password_hash, _encrypt(settings, {"secret": secret}), counter))
    for pharmacy_id in pharmacy_ids:
        conn.execute("INSERT INTO aislesignals_control.user_pharmacies(user_id,pharmacy_id,organisation_id) VALUES(%s,%s,%s)", (user_id, pharmacy_id, organisation_id))
    return user_id


def _check_version(row, expected: int):
    if row["version"] != expected:
        raise ControlError(409, "VERSION_CONFLICT", "This record changed. Refresh and try again.")


def create_auth_router(settings: CloudSettings, store: ControlStore | None = None) -> APIRouter:
    store = store or ControlStore(settings)
    router = APIRouter(prefix="/control-api")

    def origin(request: Request):
        require_origin(request, settings)

    @router.get("/setup/status")
    def setup_status():
        if not settings.auth_key or not settings.database_url:
            return {"configured": False, "needs_setup": False}
        with store.transaction() as conn:
            exists = conn.execute("SELECT EXISTS(SELECT 1 FROM aislesignals_control.organisations) AS yes").fetchone()["yes"]
        return {"configured": bool(exists or settings.bootstrap_token), "needs_setup": not exists and bool(settings.bootstrap_token)}

    @router.post("/setup/begin", dependencies=[Depends(origin)])
    def setup_begin(body: SetupBegin):
        if not settings.bootstrap_token:
            raise unavailable()
        store.throttle("setup", "first-owner", limit=10)
        if not hmac.compare_digest(body.token, settings.bootstrap_token):
            raise ControlError(401, "INVALID_CREDENTIALS", "The setup credentials are invalid.")
        secret = _mfa_secret()
        password = hash_password(body.password)
        with store.transaction() as conn:
            conn.execute("SELECT pg_advisory_xact_lock(6802449210735)")
            if conn.execute("SELECT EXISTS(SELECT 1 FROM aislesignals_control.organisations) AS yes").fetchone()["yes"]:
                raise ControlError(409, "SETUP_COMPLETE", "The first owner has already been configured.")
            token = _challenge(conn, settings, "SETUP", payload={"organisation_name": body.organisation_name, "name": body.name, "email": body.email,
                "password_hash": password, "secret": secret, "bootstrap_hash": token_hash(settings.bootstrap_token)})
        return _enrolment_response(token, secret, body.email)

    @router.post("/setup/complete", dependencies=[Depends(origin)])
    def setup_complete(body: ChallengeComplete, response: Response):
        if not settings.bootstrap_token:
            raise unavailable()
        store.throttle("setup-mfa", token_hash(body.challenge_token), limit=6)
        result = None
        with store.transaction() as conn:
            conn.execute("SELECT pg_advisory_xact_lock(6802449210735)")
            if conn.execute("SELECT EXISTS(SELECT 1 FROM aislesignals_control.organisations) AS yes").fetchone()["yes"]:
                raise ControlError(409, "SETUP_COMPLETE", "The first owner has already been configured.")
            row = _challenge_row(conn, body.challenge_token, "SETUP")
            if isinstance(row, ControlError):
                result = row
            else:
                data = _decrypt(settings, row["payload_encrypted"])
                counter = verify_totp(data["secret"], body.code)
                if counter is None or not hmac.compare_digest(data["bootstrap_hash"], token_hash(settings.bootstrap_token)):
                    result = ControlError(401, "INVALID_MFA", "The authenticator code is invalid or has already been used.")
                else:
                    org_id = str(uuid4())
                    conn.execute("INSERT INTO aislesignals_control.organisations(id,name) VALUES(%s,%s)", (org_id, data["organisation_name"]))
                    user_id = _insert_user(conn, settings, organisation_id=org_id, name=data["name"], email=data["email"], password_hash=data["password_hash"], secret=data["secret"], counter=counter, role="OWNER", pharmacy_ids=[])
                    _consume(conn, row)
                    token, session, principal = create_session(conn, settings, user_id)
                    audit(conn, principal, "OWNER_BOOTSTRAPPED", user_id)
                    result = (token, session)
        if isinstance(result, ControlError):
            raise result
        return _session_response(settings, response, *result)

    @router.post("/login", dependencies=[Depends(origin)])
    def login(body: Login):
        store.throttle("login", body.email, limit=10)
        with store.transaction() as conn:
            user = conn.execute("SELECT id,active,password_hash,version FROM aislesignals_control.users WHERE email=%s", (body.email,)).fetchone()
        if not verify_password(body.password, user["password_hash"] if user else None) or not user or not user["active"]:
            raise ControlError(401, "INVALID_CREDENTIALS", "The email or password is invalid.")
        with store.transaction() as conn:
            token = _challenge(conn, settings, "LOGIN", user_id=user["id"], user_version=user["version"])
        return {"mfa_required": True, "challenge_token": token, "expires_in_seconds": CHALLENGE_SECONDS}

    @router.post("/login/mfa", dependencies=[Depends(origin)])
    def login_mfa(body: ChallengeComplete, response: Response):
        store.throttle("login-mfa", token_hash(body.challenge_token), limit=6)
        result = None
        with store.transaction() as conn:
            # Resolve immutable org identity before taking locks in shared order.
            identity = conn.execute("""SELECT u.organisation_id FROM aislesignals_control.auth_challenges c
                JOIN aislesignals_control.users u ON u.id=c.user_id WHERE c.token_hash=%s AND c.kind='LOGIN'""", (token_hash(body.challenge_token),)).fetchone()
            if identity:
                conn.execute("SELECT id FROM aislesignals_control.organisations WHERE id=%s FOR SHARE", (identity["organisation_id"],))
            row = _challenge_row(conn, body.challenge_token, "LOGIN")
            if isinstance(row, ControlError):
                result = row
            else:
                user = conn.execute("SELECT * FROM aislesignals_control.users WHERE id=%s FOR UPDATE", (row["user_id"],)).fetchone()
                if not user or not user["active"] or user["version"] != row["user_version"]:
                    result = ControlError(401, "CHALLENGE_EXPIRED", "Start sign-in again.")
                else:
                    counter = verify_totp(_decrypt(settings, user["totp_encrypted"])["secret"], body.code, user["last_totp_counter"])
                    if counter is None:
                        result = ControlError(401, "INVALID_MFA", "The authenticator code is invalid or has already been used.")
                    else:
                        conn.execute("UPDATE aislesignals_control.users SET last_totp_counter=%s WHERE id=%s", (counter, user["id"]))
                        _consume(conn, row)
                        token, session, principal = create_session(conn, settings, str(user["id"]))
                        audit(conn, principal, "SIGNED_IN", str(user["id"]))
                        result = (token, session)
        if isinstance(result, ControlError):
            raise result
        return _session_response(settings, response, *result)

    @router.get("/session")
    def session(principal: Principal = Depends(require_principal)):
        with store.transaction() as conn:
            return session_body(conn, validate_principal(conn, principal))

    @router.post("/logout")
    def logout(body: Empty, response: Response, principal: Principal = Depends(require_principal)):
        with store.transaction() as conn:
            principal = validate_principal(conn, principal)
            conn.execute("UPDATE aislesignals_control.sessions SET revoked_at=CURRENT_TIMESTAMP WHERE id=%s", (principal.session_id,))
            audit(conn, principal, "SIGNED_OUT", principal.user_id)
        response.delete_cookie(cookie_name(settings), path="/", httponly=True,
                               secure=settings.environment in {"production", "staging"}, samesite="strict")
        return {"ok": True}

    @router.get("/pharmacies")
    def pharmacies(principal: Principal = Depends(require_principal)):
        with store.transaction() as conn:
            principal = validate_principal(conn, principal)
            return {"items": pharmacies_for(conn, principal, include_inactive=True)}

    @router.post("/pharmacies", status_code=201)
    def create_pharmacy(body: PharmacyCreate, principal: Principal = Depends(require_owner)):
        with owner_transaction(store, principal) as (conn, principal):
            if conn.execute("SELECT 1 FROM aislesignals_control.pharmacies WHERE organisation_id=%s AND lower(name)=lower(%s)", (principal.organisation_id, body.name)).fetchone():
                raise ControlError(409, "DUPLICATE_PHARMACY", "A pharmacy with this name already exists.")
            if conn.execute("SELECT count(*) AS n FROM aislesignals_control.pharmacies WHERE organisation_id=%s", (principal.organisation_id,)).fetchone()["n"] >= 200:
                raise ControlError(409, "LIMIT_REACHED", "The pharmacy limit has been reached.")
            row = conn.execute("""INSERT INTO aislesignals_control.pharmacies(id,organisation_id,name,address,timezone)
                VALUES(%s,%s,%s,%s,%s) RETURNING *""", (uuid4(), principal.organisation_id, body.name, body.address, body.timezone)).fetchone()
            audit(conn, principal, "PHARMACY_CREATED", str(row["id"]), str(row["id"]))
            return row

    @router.patch("/pharmacies/{pharmacy_id}")
    def update_pharmacy(pharmacy_id: UUID, body: PharmacyUpdate, principal: Principal = Depends(require_owner)):
        with owner_transaction(store, principal) as (conn, principal):
            row = conn.execute("SELECT * FROM aislesignals_control.pharmacies WHERE id=%s AND organisation_id=%s FOR UPDATE", (pharmacy_id, principal.organisation_id)).fetchone()
            if not row:
                raise ControlError(404, "NOT_FOUND", "The pharmacy was not found.")
            _check_version(row, body.expected_version)
            data = body.model_dump(exclude_unset=True)
            name, address, active = data.get("name", row["name"]), data.get("address", row["address"]), data.get("active", row["active"])
            if conn.execute("SELECT 1 FROM aislesignals_control.pharmacies WHERE organisation_id=%s AND id<>%s AND lower(name)=lower(%s)", (principal.organisation_id, pharmacy_id, name)).fetchone():
                raise ControlError(409, "DUPLICATE_PHARMACY", "A pharmacy with this name already exists.")
            updated = conn.execute("UPDATE aislesignals_control.pharmacies SET name=%s,address=%s,active=%s,version=version+1 WHERE id=%s RETURNING *", (name, address, active, pharmacy_id)).fetchone()
            if active != row["active"]:
                conn.execute("""UPDATE aislesignals_control.sessions SET revoked_at=CURRENT_TIMESTAMP
                    WHERE user_id IN (SELECT user_id FROM aislesignals_control.user_pharmacies WHERE pharmacy_id=%s) AND revoked_at IS NULL""", (pharmacy_id,))
            audit(conn, principal, "PHARMACY_UPDATED", str(pharmacy_id), str(pharmacy_id))
            return updated

    @router.get("/users")
    def users(principal: Principal = Depends(require_owner)):
        with store.transaction() as conn:
            principal = validate_principal(conn, principal)
            if principal.role != "OWNER":
                raise forbidden()
            ids = conn.execute("SELECT id FROM aislesignals_control.users WHERE organisation_id=%s ORDER BY lower(name),id LIMIT 200", (principal.organisation_id,)).fetchall()
            return {"items": [_user(conn, str(row["id"])) for row in ids]}

    @router.get("/invitations")
    def invitations(principal: Principal = Depends(require_owner)):
        with store.transaction() as conn:
            principal = validate_principal(conn, principal)
            if principal.role != "OWNER":
                raise forbidden()
            rows = conn.execute("""SELECT id,name,email,role,pharmacy_ids,expires_at,created_at
                FROM aislesignals_control.invitations
                WHERE organisation_id=%s AND consumed_at IS NULL AND revoked_at IS NULL
                AND expires_at>CURRENT_TIMESTAMP
                ORDER BY created_at DESC,id DESC LIMIT 200""", (principal.organisation_id,)).fetchall()
            return {"items": rows}

    @router.post("/invitations", status_code=201)
    def invite(body: InvitationCreate, principal: Principal = Depends(require_owner)):
        with owner_transaction(store, principal) as (conn, principal):
            _validate_scope(conn, principal.organisation_id, body.role, body.pharmacy_ids)
            if conn.execute("SELECT 1 FROM aislesignals_control.users WHERE email=%s", (body.email,)).fetchone():
                raise ControlError(409, "EMAIL_UNAVAILABLE", "This email cannot be invited.")
            if conn.execute("SELECT count(*) AS n FROM aislesignals_control.users WHERE organisation_id=%s", (principal.organisation_id,)).fetchone()["n"] >= 200:
                raise ControlError(409, "LIMIT_REACHED", "The user limit has been reached.")
            conn.execute("DELETE FROM aislesignals_control.invitations WHERE expires_at<=CURRENT_TIMESTAMP AND id NOT IN (SELECT invitation_id FROM aislesignals_control.auth_challenges WHERE invitation_id IS NOT NULL)")
            if conn.execute("SELECT count(*) AS n FROM aislesignals_control.invitations WHERE organisation_id=%s AND consumed_at IS NULL AND revoked_at IS NULL AND expires_at>CURRENT_TIMESTAMP", (principal.organisation_id,)).fetchone()["n"] >= 200:
                raise ControlError(409, "LIMIT_REACHED", "The pending invitation limit has been reached.")
            conn.execute("UPDATE aislesignals_control.invitations SET revoked_at=CURRENT_TIMESTAMP WHERE organisation_id=%s AND email=%s AND consumed_at IS NULL AND revoked_at IS NULL", (principal.organisation_id, body.email))
            token, invite_id = secrets.token_urlsafe(32), str(uuid4())
            row = conn.execute("""INSERT INTO aislesignals_control.invitations
                (id,organisation_id,created_by,token_hash,email,name,role,pharmacy_ids,expires_at)
                VALUES(%s,%s,%s,%s,%s,%s,%s,%s::uuid[],CURRENT_TIMESTAMP + interval '24 hours') RETURNING expires_at""",
                (invite_id, principal.organisation_id, principal.user_id, token_hash(token), body.email, body.name, body.role, body.pharmacy_ids)).fetchone()
            audit(conn, principal, "USER_INVITED", invite_id)
            return {"token": token, "expires_at": row["expires_at"]}

    @router.delete("/invitations/{invitation_id}")
    def revoke_invitation(invitation_id: UUID, principal: Principal = Depends(require_owner)):
        with owner_transaction(store, principal) as (conn, principal):
            row = conn.execute("""UPDATE aislesignals_control.invitations
                SET revoked_at=CURRENT_TIMESTAMP
                WHERE id=%s AND organisation_id=%s AND consumed_at IS NULL
                AND revoked_at IS NULL AND expires_at>CURRENT_TIMESTAMP
                RETURNING id""", (invitation_id, principal.organisation_id)).fetchone()
            if not row:
                raise ControlError(404, "NOT_FOUND", "The pending invitation was not found.")
            conn.execute("""UPDATE aislesignals_control.auth_challenges
                SET consumed_at=CURRENT_TIMESTAMP,payload_encrypted=NULL
                WHERE invitation_id=%s AND consumed_at IS NULL""", (invitation_id,))
            audit(conn, principal, "USER_INVITATION_REVOKED", str(invitation_id))
            return {"status": "revoked"}

    def invitation_valid(conn, hashed: str):
        return conn.execute("""SELECT i.* FROM aislesignals_control.invitations i
            JOIN aislesignals_control.users u ON u.id=i.created_by
            WHERE i.token_hash=%s AND i.consumed_at IS NULL AND i.revoked_at IS NULL
            AND i.expires_at>CURRENT_TIMESTAMP AND u.active=true AND u.role='OWNER'
            AND u.organisation_id=i.organisation_id FOR UPDATE OF i""", (hashed,)).fetchone()

    @router.post("/invitations/begin", dependencies=[Depends(origin)])
    def invite_begin(body: InvitationBegin):
        store.throttle("invite", token_hash(body.token), limit=6)
        secret, password = _mfa_secret(), hash_password(body.password)
        with store.transaction() as conn:
            identity = conn.execute("SELECT organisation_id FROM aislesignals_control.invitations WHERE token_hash=%s", (token_hash(body.token),)).fetchone()
            if identity:
                conn.execute("SELECT id FROM aislesignals_control.organisations WHERE id=%s FOR UPDATE", (identity["organisation_id"],))
            invitation = invitation_valid(conn, token_hash(body.token))
            if not invitation:
                raise ControlError(400, "INVALID_INVITATION", "The invitation is invalid or expired.")
            conn.execute("DELETE FROM aislesignals_control.auth_challenges WHERE invitation_id=%s", (invitation["id"],))
            token = _challenge(conn, settings, "INVITATION", invitation_id=invitation["id"], payload={"name": body.name, "password_hash": password, "secret": secret})
        return _enrolment_response(token, secret, invitation["email"])

    @router.post("/invitations/complete", dependencies=[Depends(origin)])
    def invite_complete(body: ChallengeComplete, response: Response):
        store.throttle("invite-mfa", token_hash(body.challenge_token), limit=6)
        result = None
        with store.transaction() as conn:
            identity = conn.execute("""SELECT i.organisation_id,i.token_hash FROM aislesignals_control.auth_challenges c
                JOIN aislesignals_control.invitations i ON i.id=c.invitation_id WHERE c.token_hash=%s AND c.kind='INVITATION'""", (token_hash(body.challenge_token),)).fetchone()
            if identity:
                # Serializes invitation consumption with owner/admin changes and
                # user limits. No stale invitation can retain an old grant.
                conn.execute("SELECT id FROM aislesignals_control.organisations WHERE id=%s FOR UPDATE", (identity["organisation_id"],))
            row = _challenge_row(conn, body.challenge_token, "INVITATION")
            invitation = invitation_valid(conn, identity["token_hash"]) if identity else None
            if isinstance(row, ControlError):
                result = row
            elif not invitation:
                result = ControlError(400, "INVALID_INVITATION", "The invitation is invalid or expired.")
            else:
                data = _decrypt(settings, row["payload_encrypted"])
                counter = verify_totp(data["secret"], body.code)
                if counter is None:
                    result = ControlError(401, "INVALID_MFA", "The authenticator code is invalid or has already been used.")
                elif conn.execute("SELECT 1 FROM aislesignals_control.users WHERE email=%s", (invitation["email"],)).fetchone():
                    result = ControlError(400, "INVALID_INVITATION", "The invitation is invalid or expired.")
                elif conn.execute("SELECT count(*) AS n FROM aislesignals_control.users WHERE organisation_id=%s", (invitation["organisation_id"],)).fetchone()["n"] >= 200:
                    result = ControlError(409, "LIMIT_REACHED", "The user limit has been reached.")
                else:
                    ids = [str(p) for p in invitation["pharmacy_ids"]]
                    _validate_scope(conn, str(invitation["organisation_id"]), invitation["role"], ids)
                    user_id = _insert_user(conn, settings, organisation_id=str(invitation["organisation_id"]), name=data["name"], email=invitation["email"], password_hash=data["password_hash"], secret=data["secret"], counter=counter, role=invitation["role"], pharmacy_ids=ids)
                    conn.execute("UPDATE aislesignals_control.invitations SET consumed_at=CURRENT_TIMESTAMP WHERE id=%s", (invitation["id"],))
                    _consume(conn, row)
                    token, session, principal = create_session(conn, settings, user_id)
                    audit(conn, principal, "INVITATION_ACCEPTED", str(invitation["id"]))
                    result = (token, session)
        if isinstance(result, ControlError):
            raise result
        return _session_response(settings, response, *result)

    @router.patch("/users/{user_id}")
    def update_user(user_id: UUID, body: UserUpdate, principal: Principal = Depends(require_owner)):
        with owner_transaction(store, principal) as (conn, principal):
            row = conn.execute("SELECT * FROM aislesignals_control.users WHERE id=%s AND organisation_id=%s FOR UPDATE", (user_id, principal.organisation_id)).fetchone()
            if not row:
                raise ControlError(404, "NOT_FOUND", "The user was not found.")
            _check_version(row, body.expected_version)
            previous = _user(conn, str(user_id))
            data = body.model_dump(exclude_unset=True)
            active, role = data.get("active", row["active"]), data.get("role", row["role"])
            ids = data.get("pharmacy_ids", [str(p) for p in previous["pharmacy_ids"]])
            _validate_scope(conn, principal.organisation_id, role, ids, require_active="pharmacy_ids" in data or role != row["role"])
            if row["active"] and row["role"] == "OWNER" and (not active or role != "OWNER"):
                others = conn.execute("SELECT count(*) AS n FROM aislesignals_control.users WHERE organisation_id=%s AND active=true AND role='OWNER' AND id<>%s", (principal.organisation_id, user_id)).fetchone()["n"]
                if not others:
                    raise ControlError(409, "LAST_OWNER", "At least one active owner must remain.")
            conn.execute("UPDATE aislesignals_control.users SET active=%s,role=%s,version=version+1 WHERE id=%s", (active, role, user_id))
            conn.execute("DELETE FROM aislesignals_control.user_pharmacies WHERE user_id=%s", (user_id,))
            for pharmacy_id in ids:
                conn.execute("INSERT INTO aislesignals_control.user_pharmacies(user_id,pharmacy_id,organisation_id) VALUES(%s,%s,%s)", (user_id, pharmacy_id, principal.organisation_id))
            conn.execute("UPDATE aislesignals_control.sessions SET revoked_at=CURRENT_TIMESTAMP WHERE user_id=%s AND revoked_at IS NULL", (user_id,))
            conn.execute("UPDATE aislesignals_control.auth_challenges SET consumed_at=CURRENT_TIMESTAMP,payload_encrypted=NULL WHERE user_id=%s AND consumed_at IS NULL", (user_id,))
            if not active or role != "OWNER":
                conn.execute("UPDATE aislesignals_control.invitations SET revoked_at=CURRENT_TIMESTAMP WHERE created_by=%s AND consumed_at IS NULL AND revoked_at IS NULL", (user_id,))
            audit(conn, principal, "USER_ACCESS_CHANGED", str(user_id))
            return _user(conn, str(user_id))

    return router
