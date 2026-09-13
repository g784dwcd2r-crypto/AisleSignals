"""Local first-owner claim and manager-scoped pharmacy account administration."""

import json
import os
import secrets
import time
from pathlib import Path
from typing import Literal
from uuid import UUID

from fastapi import Depends
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator

from .evidence_crypto import private_open
from .pilot_identity import (
    PASSWORD_ITERATIONS, _new_site, _new_user, _preserve_manager,
    email_address, require_pilot, text, validate_password,
)
from .store import digest, ident, password_hash

SETUP_TTL = 15 * 60
SETUP_KEY = "first_owner_setup"


def setup_available(conn):
    return not (conn.execute("SELECT 1 FROM users LIMIT 1").fetchone()
                or conn.execute("SELECT 1 FROM entities WHERE kind='site' LIMIT 1").fetchone())


def setup_token_path(store):
    return Path(store.path + ".setup-token")


def issue_setup_token(store):
    """OS-administrator capability; callers must display it only privately.

    This function never prints. The CLI requires an interactive terminal and the
    launcher decides whether it has a suitable terminal. Reissuing rotates the
    code, and the database stores only its hash and absolute expiry.
    """
    require_pilot(store)
    path = setup_token_path(store)
    if any(part.is_symlink() for part in (path, *path.parents)):
        raise ValueError("Setup code storage must not traverse symbolic links.")
    temporary = path.with_name(path.name + "." + secrets.token_hex(8))
    with store.transaction() as conn:
        if not setup_available(conn):
            raise ValueError("Browser setup is unavailable for an initialised workspace. Use named manager access or local account recovery.")
        token = secrets.token_urlsafe(32)
        expires_at = time.time() + SETUP_TTL
        state = {"token_hash": digest(token), "expires_at": expires_at}
        try:
            with private_open(temporary) as output:
                output.write(json.dumps({"token": token, "expires_at": expires_at}).encode())
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)
        conn.execute("INSERT INTO runtime_settings VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                     (SETUP_KEY, json.dumps(state)))
    return {"token": token, "expires_at": expires_at, "expires_in_seconds": SETUP_TTL}


class AdminInput(BaseModel):
    model_config = ConfigDict(extra="forbid")


class FirstOwner(AdminInput):
    organisation_name: str = Field(min_length=2, max_length=120)
    branch_name: str = Field(min_length=2, max_length=120)
    name: str = Field(min_length=2, max_length=120)
    email: str = Field(min_length=3, max_length=254)
    password: SecretStr = Field(min_length=14, max_length=256)
    setup_token: SecretStr = Field(min_length=1, max_length=256)


class Reauthenticated(AdminInput):
    manager_password: SecretStr = Field(min_length=1, max_length=256)


class CreateSite(Reauthenticated):
    name: str = Field(min_length=2, max_length=120)


class BranchRole(AdminInput):
    site_id: UUID
    role: Literal["MANAGER", "REVIEWER"]


class CreateUser(Reauthenticated):
    name: str = Field(min_length=2, max_length=120)
    email: str = Field(min_length=3, max_length=254)
    password: SecretStr = Field(min_length=14, max_length=256)
    branches: list[BranchRole] = Field(min_length=1, max_length=100)

    @field_validator("branches")
    @classmethod
    def distinct_branches(cls, branches):
        if len({b.site_id for b in branches}) != len(branches):
            raise ValueError("Each branch can be assigned only once.")
        return branches


class ChangeAccess(Reauthenticated):
    site_id: UUID
    role: Literal["MANAGER", "REVIEWER"] | None
    expected_version: str = Field(pattern=r"^[0-9a-f]{64}$")


class ChangeEnabled(Reauthenticated):
    enabled: bool = Field(strict=True)
    expected_version: str = Field(pattern=r"^[0-9a-f]{64}$")


class ChangePassword(Reauthenticated):
    password: SecretStr = Field(min_length=14, max_length=256)
    expected_version: str = Field(pattern=r"^[0-9a-f]{64}$")


def install_pilot_admin(app, context, problem):
    def pilot():
        if app.state.store.mode != "pilot":
            problem(403, "PILOT_REQUIRED", "Account administration requires a protected pilot workspace.")

    def manager(ctx):
        pilot()
        ctx.manager()
        return {site["id"]: site for site in ctx.store.allowed_sites(ctx.conn, ctx.user) if site["role"] == "MANAGER"}

    def reauthenticate(ctx, supplied):
        namespace = "__admin_reauth__:" + ctx.user["id"]
        stamp = time.time()
        ctx.conn.execute("DELETE FROM login_attempts WHERE at<?", (stamp - 900,))
        attempts = ctx.conn.execute("SELECT COUNT(*) FROM login_attempts WHERE email=?", (namespace,)).fetchone()[0]
        if attempts >= 5:
            problem(429, "REAUTH_RATE_LIMITED", "Too many incorrect manager passphrases. Wait fifteen minutes before trying again.")
        security = ctx.conn.execute("SELECT password_iterations FROM account_security WHERE user_id=? AND enabled=1", (ctx.user["id"],)).fetchone()
        value = password_hash(supplied.get_secret_value(), ctx.user["salt"], security[0] if security else PASSWORD_ITERATIONS)
        if not security or not secrets.compare_digest(value, ctx.user["password_hash"]):
            ctx.conn.execute("INSERT INTO login_attempts(ip,email,at) VALUES(?,?,?)", ("admin-reauth", namespace, stamp))
            ctx.conn.commit()  # Failed verification must remain limited across retries/restarts.
            problem(401, "REAUTH_REQUIRED", "Enter your current manager passphrase to confirm this change.")

    def memberships(conn, user_id):
        return [dict(row) for row in conn.execute("SELECT site_id,role FROM memberships WHERE user_id=? ORDER BY site_id", (user_id,))]

    def target(ctx, user_id, sites):
        row = ctx.conn.execute("SELECT u.*,a.enabled FROM users u JOIN account_security a ON a.user_id=u.id WHERE u.id=? AND u.organisation_id=?",
                               (str(user_id), ctx.user["organisation_id"])).fetchone()
        if row is None or not any(m["site_id"] in sites for m in memberships(ctx.conn, row["id"])):
            problem(404, "ACCOUNT_NOT_AVAILABLE", "This account is not available to your management scope.")
        return dict(row)

    def public_account(ctx, user, sites):
        grants = memberships(ctx.conn, user["id"])
        salt = ctx.conn.execute("SELECT salt FROM users WHERE id=?", (user["id"],)).fetchone()[0]
        # An opaque change marker; salt changes on reset but is never exposed.
        version = digest(json.dumps([bool(user["enabled"]), grants, salt], sort_keys=True))
        return {"id": user["id"], "name": user["name"], "email": user["email"],
                "enabled": bool(user["enabled"]), "version": version,
                "branches": [{"site_id": grant["site_id"], "site_name": sites[grant["site_id"]]["name"], "role": grant["role"]}
                             for grant in grants if grant["site_id"] in sites],
                "can_manage_account": bool(grants) and all(grant["site_id"] in sites for grant in grants)}

    def current_version(ctx, user, sites, expected):
        if not secrets.compare_digest(public_account(ctx, user, sites)["version"], expected):
            problem(409, "ADMIN_ACCOUNT_CHANGED", "This account changed after you loaded it. Reload the current account and review the change before submitting again.")

    def global_authority(ctx, user, sites):
        if not public_account(ctx, user, sites)["can_manage_account"]:
            problem(403, "ACCOUNT_AUTHORITY_REQUIRED", "Account-wide changes require manager access to every branch assigned to this account.")

    def validate(call):
        try:
            return call()
        except ValueError as exc:
            if "last manager" in str(exc):
                problem(409, "LAST_MANAGER_REQUIRED", "Appoint another enabled manager before removing this branch's last manager.")
            problem(422, "INVALID_ACCOUNT_INPUT", str(exc))

    def audit(ctx, user, action, detail, site_id=None):
        ctx.store.audit(ctx.conn, {**ctx.user, "site_id": site_id or ctx.user["site_id"]}, action, "user", user["id"], detail)

    @app.get("/api/setup/status")
    def setup_status():
        pilot()
        with app.state.store.transaction() as conn:
            return {"available": setup_available(conn), "token_required": True, "token_ttl_seconds": SETUP_TTL}

    @app.post("/api/setup")
    def claim_setup(body: FirstOwner):
        pilot()
        store = app.state.store
        with store.transaction() as conn:
            if not setup_available(conn):
                problem(409, "SETUP_UNAVAILABLE", "This workspace already has accounts or branches. Sign in or use local account recovery.")
            stamp = time.time()
            conn.execute("DELETE FROM login_attempts WHERE at<?", (stamp - 900,))
            attempts = conn.execute("SELECT COUNT(*) FROM login_attempts WHERE email='__first_owner_setup__'").fetchone()[0]
            if attempts >= 5:
                problem(429, "SETUP_RATE_LIMITED", "Too many incorrect setup codes. Wait fifteen minutes before trying again.")
            row = conn.execute("SELECT value FROM runtime_settings WHERE key=?", (SETUP_KEY,)).fetchone()
            try:
                state = json.loads(row[0]) if row else {}
                valid = (isinstance(state.get("expires_at"), (int, float)) and state["expires_at"] > stamp
                         and isinstance(state.get("token_hash"), str)
                         and secrets.compare_digest(digest(body.setup_token.get_secret_value()), state["token_hash"]))
            except (ValueError, TypeError):
                valid = False
            if not valid:
                conn.execute("INSERT INTO login_attempts(ip,email,at) VALUES(?,?,?)", ("first-owner", "__first_owner_setup__", stamp))
                conn.commit()
                problem(400, "SETUP_TOKEN_INVALID", "The setup code is invalid or expired. Obtain a current code from this laptop's authorised local operator.")
            organisation = validate(lambda: text(body.organisation_name, "Organisation name"))
            branch = validate(lambda: text(body.branch_name, "Branch name"))
            # Validate everything before creating either entity; the enclosing
            # write transaction still makes competing first-owner claims atomic.
            email = validate(lambda: email_address(body.email))
            name = validate(lambda: text(body.name, "Full name"))
            password = validate(lambda: validate_password(body.password.get_secret_value()))
            site = _new_site(conn, store, ident(), organisation, branch)
            user = _new_user(conn, store, email, name, password, [site["id"]], "MANAGER")
            store.audit(conn, user, "FIRST_OWNER_CREATED", "user", user["id"], "One-time local setup code consumed; named manager created. No credential material retained in audit records.")
            conn.execute("DELETE FROM runtime_settings WHERE key=?", (SETUP_KEY,))
        try:
            setup_token_path(store).unlink(missing_ok=True)
        except OSError:
            pass  # The persisted claim has already invalidated any remaining code file.
        return JSONResponse({"created": True, "sign_in_required": True}, status_code=201)

    @app.get("/api/admin/sites")
    def sites(ctx=Depends(context)):
        return {"sites": list(manager(ctx).values())}

    @app.get("/api/admin/users")
    def users(ctx=Depends(context)):
        managed = manager(ctx)
        visible = []
        for row in ctx.conn.execute("SELECT u.*,a.enabled FROM users u JOIN account_security a ON a.user_id=u.id WHERE u.organisation_id=? ORDER BY u.email", (ctx.user["organisation_id"],)):
            if any(m["site_id"] in managed for m in memberships(ctx.conn, row["id"])):
                visible.append(public_account(ctx, dict(row), managed))
        return {"users": visible}

    @app.post("/api/admin/sites")
    def create_site(body: CreateSite, ctx=Depends(context)):
        manager(ctx)
        reauthenticate(ctx, body.manager_password)
        name = validate(lambda: text(body.name, "Branch name"))
        existing = ctx.conn.execute("SELECT body FROM entities WHERE kind='site' AND organisation_id=?", (ctx.user["organisation_id"],)).fetchall()
        if any(json.loads(row[0])["name"].casefold() == name.casefold() for row in existing):
            problem(409, "BRANCH_CONFLICT", "A branch with this name already exists in the group.")
        organisation_name = ctx.get("site", ctx.user["site_id"])["organisation_name"]
        site = _new_site(ctx.conn, ctx.store, ctx.user["organisation_id"], organisation_name, name, actor=ctx.user)
        ctx.conn.execute("INSERT INTO memberships VALUES(?,?,?,?)", (ctx.user["id"], ctx.user["organisation_id"], site["id"], "MANAGER"))
        audit(ctx, ctx.user, "MANAGER_BRANCH_GRANTED", "Creator assigned manager access to the new branch; existing active branch remains selected.", site["id"])
        return JSONResponse({"site": {"id": site["id"], "name": site["name"], "organisation_id": ctx.user["organisation_id"], "organisation_name": organisation_name, "role": "MANAGER"}}, status_code=201)

    @app.post("/api/admin/users")
    def create_user(body: CreateUser, ctx=Depends(context)):
        managed = manager(ctx)
        reauthenticate(ctx, body.manager_password)
        if any(str(branch.site_id) not in managed for branch in body.branches):
            problem(404, "SITE_NOT_AVAILABLE", "Only branches you manage can be assigned.")
        email = validate(lambda: email_address(body.email))
        if ctx.conn.execute("SELECT 1 FROM users WHERE email=?", (email,)).fetchone():
            problem(409, "ACCOUNT_CONFLICT", "This account address is not available. Check existing accounts or use a different individual address.")
        user = validate(lambda: _new_user(ctx.conn, ctx.store, email, body.name, body.password.get_secret_value(),
                                        [str(b.site_id) for b in body.branches], body.branches[0].role, actor=ctx.user))
        for branch in body.branches:
            ctx.conn.execute("UPDATE memberships SET role=? WHERE user_id=? AND site_id=?", (branch.role, user["id"], str(branch.site_id)))
        return JSONResponse({"user": public_account(ctx, {**user, "enabled": True}, managed)}, status_code=201)

    @app.post("/api/admin/users/{user_id}/access")
    def change_access(user_id: UUID, body: ChangeAccess, ctx=Depends(context)):
        managed = manager(ctx)
        reauthenticate(ctx, body.manager_password)
        user = target(ctx, user_id, managed)
        current_version(ctx, user, managed, body.expected_version)
        site_id = str(body.site_id)
        if site_id not in managed:
            problem(404, "SITE_NOT_AVAILABLE", "Only branches you manage can be changed.")
        if body.role is None and len(memberships(ctx.conn, user["id"])) == 1:
            problem(409, "BRANCH_REQUIRED", "Keep at least one branch assigned. Disable the account to remove all access, or assign its replacement branch first.")
        validate(lambda: _preserve_manager(ctx.conn, user, site_id, body.role))
        if body.role is None:
            ctx.conn.execute("DELETE FROM memberships WHERE user_id=? AND site_id=?", (user["id"], site_id))
        else:
            ctx.conn.execute("INSERT INTO memberships VALUES(?,?,?,?) ON CONFLICT(user_id,site_id) DO UPDATE SET role=excluded.role", (user["id"], ctx.user["organisation_id"], site_id, body.role))
        ctx.conn.execute("DELETE FROM sessions WHERE user_id=?", (user["id"],))
        audit(ctx, user, "ACCOUNT_ACCESS_UPDATED", "A branch manager changed this branch's account access; all target account sessions revoked.", site_id)
        return {"user": public_account(ctx, user, managed)}

    @app.post("/api/admin/users/{user_id}/enabled")
    def change_enabled(user_id: UUID, body: ChangeEnabled, ctx=Depends(context)):
        managed = manager(ctx)
        reauthenticate(ctx, body.manager_password)
        user = target(ctx, user_id, managed)
        global_authority(ctx, user, managed)
        current_version(ctx, user, managed, body.expected_version)
        if not body.enabled:
            validate(lambda: _preserve_manager(ctx.conn, user))
        ctx.conn.execute("UPDATE account_security SET enabled=? WHERE user_id=?", (int(body.enabled), user["id"]))
        ctx.conn.execute("DELETE FROM sessions WHERE user_id=?", (user["id"],))
        audit(ctx, user, "ACCOUNT_ENABLED" if body.enabled else "ACCOUNT_DISABLED", "Authorised manager changed account access state; all target account sessions revoked.")
        return {"user": public_account(ctx, {**user, "enabled": body.enabled}, managed)}

    @app.post("/api/admin/users/{user_id}/password")
    def reset_password(user_id: UUID, body: ChangePassword, ctx=Depends(context)):
        managed = manager(ctx)
        reauthenticate(ctx, body.manager_password)
        user = target(ctx, user_id, managed)
        global_authority(ctx, user, managed)
        current_version(ctx, user, managed, body.expected_version)
        password = validate(lambda: validate_password(body.password.get_secret_value()))
        salt = secrets.token_hex(32)
        ctx.conn.execute("UPDATE users SET salt=?,password_hash=? WHERE id=?", (salt, password_hash(password, salt, PASSWORD_ITERATIONS), user["id"]))
        ctx.conn.execute("UPDATE account_security SET password_iterations=? WHERE user_id=?", (PASSWORD_ITERATIONS, user["id"]))
        ctx.conn.execute("DELETE FROM sessions WHERE user_id=?", (user["id"],))
        audit(ctx, user, "PASSWORD_RESET", "Authorised manager reset the local passphrase; sessions revoked and enabled state unchanged.")
        return {"ok": True, "sign_in_required": user["id"] == ctx.user["id"]}
