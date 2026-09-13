"""Local operator provisioning for a separate, unseeded pharmacy pilot database.

Run ``python -m services.api.pilot_identity --help``. Passwords are accepted only
through a terminal's hidden prompt, never arguments, environment or config files.
This is local account administration, not an invitation service or cloud identity.
The operating-system account able to write this database is an administrator.
"""

import argparse
import getpass
import json
import re
import secrets
import sys
import unicodedata

from .store import PASSWORD, Store, ident, password_hash

PASSWORD_ITERATIONS = 600_000


def text(value, label, minimum=2, maximum=120):
    value = value.strip()
    if not minimum <= len(value) <= maximum or any(
        unicodedata.category(character).startswith("C") for character in value
    ):
        raise ValueError(f"{label} must contain {minimum} to {maximum} printable characters.")
    return value


def email_address(value):
    value = value.strip().lower()
    if (len(value) > 254 or not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", value)
            or any(unicodedata.category(character).startswith("C") for character in value)):
        raise ValueError("Provide a valid individual account email address.")
    if value.endswith(".demo"):
        raise ValueError("Demo account addresses cannot be provisioned in pilot mode.")
    return value


def validate_password(value):
    if (
        not 14 <= len(value) <= 256
        or value != value.strip()
        or len(set(value)) < 6
        or value == PASSWORD
        or any(unicodedata.category(character).startswith("C") for character in value)
    ):
        raise ValueError(
            "Choose a unique password or passphrase of 14 to 256 characters, "
            "with no leading/trailing whitespace or control characters. The demo password is forbidden."
        )
    return value


def require_pilot(store):
    if store.mode != "pilot":
        raise ValueError("Named account provisioning requires a separate pilot database.")


def site_scope(conn, site_id):
    row = conn.execute(
        "SELECT organisation_id,site_id FROM entities WHERE id=? AND kind='site' AND site_id=id",
        (site_id,),
    ).fetchone()
    if row is None:
        raise ValueError("The selected branch does not exist.")
    return dict(row)


def local_actor(scope):
    return {**scope, "name": "Local setup operator", "id": "local-setup", "role": "MANAGER"}


def add_site(store, organisation_id, branch_name):
    """Add a branch inside an existing group. Authority comes from local operator access."""
    require_pilot(store)
    branch_name = text(branch_name, "Branch name")
    with store.transaction() as conn:
        existing = conn.execute(
            "SELECT body FROM entities WHERE organisation_id=? AND kind='site' LIMIT 1",
            (organisation_id,),
        ).fetchone()
        if not existing:
            raise ValueError("The organisation does not exist; initialise a group first.")
        return _new_site(conn, store, organisation_id, json.loads(existing[0])["organisation_name"], branch_name)


def _new_site(conn, store, organisation_id, organisation_name, branch_name, *, actor=None):
    site_id = ident()
    scope = dict(organisation_id=organisation_id, site_id=site_id)
    site = dict(
        id=site_id, name=branch_name, organisation_name=organisation_name,
        timezone="Europe/Dublin", monthly_price_cents=6000, shift_active=False,
    )
    store.put(conn, scope, "site", site)
    store.audit(conn, {**actor, **scope} if actor else local_actor(scope), "BRANCH_PROVISIONED", "site", site_id,
                "Local branch created without demo footage, users or observations. Camera qualification remains required.")
    return {**site, "organisation_id": organisation_id}


def _new_user(conn, store, email, name, password, site_ids, role, *, actor=None):
    email, name, password = email_address(email), text(name, "Full name"), validate_password(password)
    if role not in {"MANAGER", "REVIEWER"}:
        raise ValueError("Account role must be MANAGER or REVIEWER.")
    site_ids = list(dict.fromkeys(site_ids))
    if not site_ids:
        raise ValueError("At least one existing branch must be assigned.")
    scopes = [site_scope(conn, site_id) for site_id in site_ids]
    if len({scope["organisation_id"] for scope in scopes}) != 1:
        raise ValueError("One account can belong only to branches of the same organisation.")
    if conn.execute("SELECT 1 FROM users WHERE email=?", (email,)).fetchone():
        raise ValueError("This account already exists. Grant access or reset its password explicitly.")
    user_id, salt = ident(), secrets.token_hex(32)
    user = dict(id=user_id, email=email, name=name, role=role, **scopes[0])
    conn.execute("INSERT INTO users VALUES(?,?,?,?,?,?,?,?)", (
        user_id, email, name, role, user["organisation_id"], user["site_id"], salt,
        password_hash(password, salt, PASSWORD_ITERATIONS),
    ))
    conn.execute("INSERT INTO account_security VALUES(?,?,?)", (user_id, 1, PASSWORD_ITERATIONS))
    for scope in scopes:
        conn.execute("INSERT INTO memberships VALUES(?,?,?,?)", (user_id, scope["organisation_id"], scope["site_id"], role))
        store.audit(conn, {**actor, **scope} if actor else local_actor(scope), "ACCOUNT_PROVISIONED", "user", user_id,
                    "Named local account assigned to this branch. No password is stored in audit records.")
    return user


def initialise(store, organisation_name, branch_name, email, name, password):
    """Atomically create the first group, branch and named manager; never overwrite."""
    require_pilot(store)
    organisation_name, branch_name = text(organisation_name, "Organisation name"), text(branch_name, "Branch name")
    with store.transaction() as conn:
        if conn.execute("SELECT 1 FROM users LIMIT 1").fetchone() or conn.execute("SELECT 1 FROM entities WHERE kind='site' LIMIT 1").fetchone():
            raise ValueError("This database is already initialised. Use explicit account or branch commands.")
        site = _new_site(conn, store, ident(), organisation_name, branch_name)
        user = _new_user(conn, store, email, name, password, [site["id"]], "MANAGER")
        return dict(site=site, user=user)


def add_user(store, email, name, password, site_ids, role="REVIEWER"):
    require_pilot(store)
    with store.transaction() as conn:
        return _new_user(conn, store, email, name, password, site_ids, role)


def find_user(conn, email):
    user = conn.execute("SELECT * FROM users WHERE email=?", (email_address(email),)).fetchone()
    if not user:
        raise ValueError("The named account does not exist.")
    return dict(user)


def grant_user(store, email, site_id, role="REVIEWER"):
    require_pilot(store)
    if role not in {"MANAGER", "REVIEWER"}:
        raise ValueError("Account role must be MANAGER or REVIEWER.")
    with store.transaction() as conn:
        user, scope = find_user(conn, email), site_scope(conn, site_id)
        if user["organisation_id"] != scope["organisation_id"]:
            raise ValueError("A branch in another organisation cannot be assigned to this account.")
        _preserve_manager(conn, user, site_id, role)
        conn.execute("INSERT INTO memberships VALUES(?,?,?,?) ON CONFLICT(user_id,site_id) DO UPDATE SET role=excluded.role", (
            user["id"], scope["organisation_id"], site_id, role,
        ))
        conn.execute("DELETE FROM sessions WHERE user_id=?", (user["id"],))
        store.audit(conn, local_actor(scope), "ACCOUNT_MEMBERSHIP_CHANGED", "user", user["id"],
                    "Branch access updated by local operator; all existing sessions revoked.")


def _preserve_manager(conn, user, site_id=None, new_role=None):
    """Do not silently orphan a branch; appoint a replacement first."""
    if new_role == "MANAGER":
        return
    memberships = conn.execute(
        "SELECT m.site_id FROM memberships m JOIN account_security a ON a.user_id=m.user_id "
        "WHERE m.user_id=? AND m.role='MANAGER' AND a.enabled=1", (user["id"],)
    ).fetchall()
    for membership in memberships:
        if site_id and membership["site_id"] != site_id:
            continue
        other = conn.execute(
            "SELECT 1 FROM memberships m JOIN account_security a ON a.user_id=m.user_id "
            "WHERE m.site_id=? AND m.user_id<>? AND m.role='MANAGER' AND a.enabled=1 LIMIT 1",
            (membership["site_id"], user["id"]),
        ).fetchone()
        if other is None:
            raise ValueError("Assign another enabled manager to this branch before removing its last manager.")


def revoke_site_access(store, email, site_id):
    require_pilot(store)
    with store.transaction() as conn:
        user, scope = find_user(conn, email), site_scope(conn, site_id)
        if user["organisation_id"] != scope["organisation_id"]:
            raise ValueError("The branch is not in this account's organisation.")
        _preserve_manager(conn, user, site_id)
        conn.execute("DELETE FROM memberships WHERE user_id=? AND site_id=?", (user["id"], site_id))
        conn.execute("DELETE FROM sessions WHERE user_id=?", (user["id"],))
        store.audit(conn, local_actor(scope), "ACCOUNT_MEMBERSHIP_REVOKED", "user", user["id"],
                    "Branch access removed; all account sessions revoked.")


def disable_user(store, email):
    require_pilot(store)
    with store.transaction() as conn:
        user = find_user(conn, email)
        _preserve_manager(conn, user)
        conn.execute("UPDATE account_security SET enabled=0 WHERE user_id=?", (user["id"],))
        conn.execute("DELETE FROM sessions WHERE user_id=?", (user["id"],))
        store.audit(conn, local_actor(user), "ACCOUNT_DISABLED", "user", user["id"],
                    "Named account disabled; all sessions revoked immediately.")


def set_password(store, email, password):
    require_pilot(store)
    password = validate_password(password)
    with store.transaction() as conn:
        user = find_user(conn, email)
        salt = secrets.token_hex(32)
        conn.execute("UPDATE users SET salt=?,password_hash=? WHERE id=?", (
            salt, password_hash(password, salt, PASSWORD_ITERATIONS), user["id"],
        ))
        conn.execute("UPDATE account_security SET password_iterations=? WHERE user_id=?", (PASSWORD_ITERATIONS, user["id"]))
        conn.execute("DELETE FROM sessions WHERE user_id=?", (user["id"],))
        # Keep failed-attempt counters: a password reset must not clear a lockout.
        store.audit(conn, local_actor(user), "PASSWORD_RESET", "user", user["id"],
                    "Local password reset; sessions revoked. Account enabled state remains unchanged.")


def enable_user(store, email):
    """Local operator explicitly restores access after reviewing current grants."""
    require_pilot(store)
    with store.transaction() as conn:
        user = find_user(conn, email)
        if not store.allowed_sites(conn, user):
            raise ValueError("Assign current branch access before enabling this account.")
        conn.execute("UPDATE account_security SET enabled=1 WHERE user_id=?", (user["id"],))
        conn.execute("DELETE FROM sessions WHERE user_id=?", (user["id"],))
        store.audit(conn, local_actor(user), "ACCOUNT_ENABLED", "user", user["id"],
                    "Local operator explicitly enabled this account after reviewing current branch access; prior sessions revoked.")


def list_access(store):
    require_pilot(store)
    with store.transaction() as conn:
        result = []
        for row in conn.execute("SELECT u.id,u.email,u.name,u.organisation_id,u.site_id,a.enabled "
                                "FROM users u JOIN account_security a ON a.user_id=u.id ORDER BY u.email"):
            user = dict(row)
            result.append({"id": user["id"], "email": user["email"], "name": user["name"],
                           "enabled": bool(user["enabled"]), "branches": store.allowed_sites(conn, user)})
        return {"accounts": result}


def prompt_password():
    if not sys.stdin.isatty():
        raise ValueError("Run account provisioning in an interactive terminal; passwords cannot be piped or passed as arguments.")
    password = getpass.getpass("New unique passphrase (at least 14 characters): ")
    if password != getpass.getpass("Repeat passphrase: "):
        raise ValueError("The passphrases did not match. No account was changed.")
    return validate_password(password)


def main(argv=None):
    class SafeParser(argparse.ArgumentParser):
        def error(self, message):
            # argparse normally echoes unknown arguments, potentially including
            # a passphrase mistakenly supplied on a command line.
            self.print_usage(sys.stderr)
            self.exit(2, "Invalid command arguments; use --help. Passwords may be entered only at the hidden prompt.\n")

    parser = SafeParser(description=__doc__)
    parser.add_argument("--db", required=True, help="Path to the separate pilot SQLite database")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("setup-token", help="Issue a one-time browser setup code for an empty pilot; interactive terminal only")
    commands.add_parser("list-access", help="Review local named accounts and current branch grants; no passwords")
    setup = commands.add_parser("init", help="Create first group, branch and manager; prompt for a passphrase")
    for argument in ("organisation", "branch", "email", "name"):
        setup.add_argument(f"--{argument}", required=True)
    site = commands.add_parser("add-site", help="Add an empty branch to an existing organisation")
    site.add_argument("--organisation-id", required=True)
    site.add_argument("--branch", required=True)
    user = commands.add_parser("add-user", help="Create a named account and prompt for a passphrase")
    for argument in ("email", "name"):
        user.add_argument(f"--{argument}", required=True)
    user.add_argument("--site-id", action="append", required=True)
    user.add_argument("--role", choices=["MANAGER", "REVIEWER"], default="REVIEWER")
    grant = commands.add_parser("grant-user", help="Grant/update branch access and revoke existing sessions")
    grant.add_argument("--email", required=True)
    grant.add_argument("--site-id", required=True)
    grant.add_argument("--role", choices=["MANAGER", "REVIEWER"], default="REVIEWER")
    revoke = commands.add_parser("revoke-site-access", help="Remove branch access and revoke sessions")
    revoke.add_argument("--email", required=True)
    revoke.add_argument("--site-id", required=True)
    for command in ("disable-user", "enable-user", "set-password"):
        sub = commands.add_parser(command)
        sub.add_argument("--email", required=True)
    args = parser.parse_args(argv)
    try:
        store = Store(args.db, mode="pilot")
        result = None
        if args.command == "setup-token":
            if not sys.stdin.isatty() or not sys.stdout.isatty():
                raise ValueError("Issue setup codes only in an interactive terminal; codes cannot be piped or redirected.")
            from .pilot_admin import issue_setup_token
            result = issue_setup_token(store)
        elif args.command == "list-access":
            result = list_access(store)
        elif args.command == "init":
            result = initialise(store, args.organisation, args.branch, args.email, args.name, prompt_password())
        elif args.command == "add-site":
            result = add_site(store, args.organisation_id, args.branch)
        elif args.command == "add-user":
            result = add_user(store, args.email, args.name, prompt_password(), args.site_id, args.role)
        elif args.command == "grant-user":
            grant_user(store, args.email, args.site_id, args.role)
        elif args.command == "revoke-site-access":
            revoke_site_access(store, args.email, args.site_id)
        elif args.command == "disable-user":
            disable_user(store, args.email)
        elif args.command == "enable-user":
            enable_user(store, args.email)
        elif args.command == "set-password":
            set_password(store, args.email, prompt_password())
        print(json.dumps(result or {"ok": True}, indent=2))
        return 0
    except (ValueError, RuntimeError) as exc:
        print(f"Setup stopped: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
