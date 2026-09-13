"""Small transactional SQLite repository. Every entity read includes both scope keys.

SQLite is intentionally a local prototype dependency. Production PostgreSQL RLS,
retention workers and managed authentication are separate acceptance gates.
"""

import hashlib
import json
import os
import secrets
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone, timedelta
from pathlib import Path
from uuid import uuid4

PASSWORD = "AisleDemo!2026"


def now():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def ident():
    return str(uuid4())


def encode(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def password_hash(password, salt, iterations=210_000):
    return hashlib.pbkdf2_hmac(
        "sha256", password.encode(), salt.encode(), iterations
    ).hex()


class Store:
    @staticmethod
    def validate_provenance(conn, mode):
        version = conn.execute("PRAGMA user_version").fetchone()[0]
        if version not in (0, 1, 2):
            raise RuntimeError(
                "Unsupported prototype database schema. Preserve this database and upgrade the application."
            )
        tables = {row[0] for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )}
        recorded_mode = None
        if "runtime_settings" in tables:
            row = conn.execute(
                "SELECT value FROM runtime_settings WHERE key='mode'"
            ).fetchone()
            recorded_mode = row[0] if row else None
        legacy_data = any(
            table in tables and conn.execute(f"SELECT 1 FROM {table} LIMIT 1").fetchone()
            for table in ("users", "entities")
        )
        if recorded_mode is None and legacy_data:
            recorded_mode = "synthetic"
        if recorded_mode is not None and recorded_mode != mode:
            raise RuntimeError(
                "Database mode mismatch. Preserve this database and select a separate "
                "database for pilot or synthetic mode; automatic conversion is refused."
            )

    @classmethod
    def prepare_pilot_database(cls, path):
        """Protect account setup too, before any launcher has hardened its files.

        Do not chmod existing parent directories: a direct API/CLI caller may
        choose a database in a shared project directory. New ancestors are
        private, and the database and SQLite sidecars are private files.
        """
        path = Path(os.path.abspath(path))
        related = [path, *(Path(str(path) + suffix) for suffix in ("-wal", "-shm", "-journal"))]
        if any(part.is_symlink() for part in (*path.parents, *related)):
            raise ValueError("Pilot database paths must not traverse symbolic links.")
        if any(part.exists() and not part.is_file() for part in related):
            raise ValueError("Pilot database and SQLite sidecars must be regular files.")
        missing = []
        parent = path.parent
        while not parent.exists():
            missing.append(parent)
            parent = parent.parent
        for directory in reversed(missing):
            directory.mkdir(mode=0o700)
        if path.exists():
            # Check the actual WAL-aware database before changing its modes.
            # A mode mismatch must not harden or alter the user's demo files.
            conn = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=10)
            try:
                cls.validate_provenance(conn, "pilot")
            finally:
                conn.close()
        else:
            descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            os.close(descriptor)
        if os.name != "nt":
            for file in related:
                if file.exists():
                    file.chmod(0o600)

    def __init__(self, path, mode="synthetic"):
        if mode not in {"synthetic", "pilot"}:
            raise ValueError("Database mode must be synthetic or pilot.")
        self.mode = mode
        self.path = str(path)
        if mode == "pilot":
            self.prepare_pilot_database(self.path)
        else:
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        with self.transaction() as conn:
            # Check provenance before any schema changes. An unmarked existing
            # database belongs to the legacy synthetic build, never to a pilot.
            self.validate_provenance(conn, mode)
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS users (
                    id TEXT PRIMARY KEY, email TEXT UNIQUE NOT NULL, name TEXT NOT NULL,
                    role TEXT NOT NULL, organisation_id TEXT NOT NULL, site_id TEXT NOT NULL,
                    salt TEXT NOT NULL, password_hash TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS entities (
                    id TEXT PRIMARY KEY, kind TEXT NOT NULL, organisation_id TEXT NOT NULL,
                    site_id TEXT NOT NULL, body TEXT NOT NULL, created_at TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS entity_scope ON entities(organisation_id,site_id,kind,created_at);
                CREATE TABLE IF NOT EXISTS sessions (
                    token_hash TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id),
                    csrf_token TEXT NOT NULL, created_at REAL NOT NULL, last_seen REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS login_attempts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, ip TEXT NOT NULL,
                    email TEXT NOT NULL, at REAL NOT NULL);
                CREATE INDEX IF NOT EXISTS attempts_time ON login_attempts(at);
                CREATE TABLE IF NOT EXISTS idempotency (
                    actor_id TEXT NOT NULL, route TEXT NOT NULL, key TEXT NOT NULL,
                    payload_hash TEXT NOT NULL, result TEXT NOT NULL,
                    PRIMARY KEY(actor_id,route,key));
                CREATE TABLE IF NOT EXISTS source_events (
                    organisation_id TEXT NOT NULL, site_id TEXT NOT NULL,
                    source_event_id TEXT NOT NULL, payload_hash TEXT NOT NULL, result TEXT NOT NULL,
                    PRIMARY KEY(organisation_id,site_id,source_event_id));

                CREATE TABLE IF NOT EXISTS runtime_settings (
                    key TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS account_security (
                    user_id TEXT PRIMARY KEY REFERENCES users(id),
                    enabled INTEGER NOT NULL DEFAULT 1 CHECK(enabled IN (0,1)),
                    password_iterations INTEGER NOT NULL CHECK(password_iterations>=600000));
                CREATE TABLE IF NOT EXISTS memberships (
                    user_id TEXT NOT NULL REFERENCES users(id),
                    organisation_id TEXT NOT NULL, site_id TEXT NOT NULL,
                    role TEXT NOT NULL CHECK(role IN ('MANAGER','REVIEWER')),
                    PRIMARY KEY(user_id,site_id));
                CREATE TABLE IF NOT EXISTS session_scopes (
                    token_hash TEXT PRIMARY KEY REFERENCES sessions(token_hash) ON DELETE CASCADE,
                    organisation_id TEXT NOT NULL, site_id TEXT NOT NULL);

            """)
            conn.execute("BEGIN IMMEDIATE")
            # Older synthetic-only binaries reject v2 before they can seed a
            # demo account into a protected workspace. This is additive; an
            # intentional rollback must use a pre-upgrade backup.
            conn.execute("PRAGMA user_version=2")
            conn.execute("INSERT OR IGNORE INTO runtime_settings VALUES('mode',?)", (mode,))
            if mode == "synthetic":
                if conn.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 0:
                    self.seed(conn)
                self.ensure_mari_mina_profile(conn)

    @staticmethod
    def resolve_session_user(conn, session):
        """Resolve authority afresh for requests and asynchronous publications."""
        user = conn.execute("SELECT * FROM users WHERE id=?", (session["user_id"],)).fetchone()
        if user is None:
            return None
        mode = conn.execute("SELECT value FROM runtime_settings WHERE key='mode'").fetchone()
        if mode is None or mode[0] == "synthetic":
            return dict(user)
        security = conn.execute(
            "SELECT enabled FROM account_security WHERE user_id=?", (user["id"],)
        ).fetchone()
        if security is None or not security[0]:
            return None
        scope = conn.execute(
            "SELECT m.organisation_id,m.site_id,m.role FROM session_scopes s "
            "JOIN memberships m ON m.user_id=? AND m.site_id=s.site_id "
            "AND m.organisation_id=s.organisation_id WHERE s.token_hash=?",
            (user["id"], session["token_hash"]),
        ).fetchone()
        if scope is None or scope["organisation_id"] != user["organisation_id"]:
            return None
        resolved = {**dict(user), **dict(scope)}
        return resolved if Store.get(conn, resolved, "site", resolved["site_id"]) else None

    def allowed_sites(self, conn, user):
        if self.mode == "synthetic":
            memberships = [user]
        else:
            memberships = conn.execute(
                "SELECT organisation_id,site_id,role FROM memberships "
                "WHERE user_id=? AND organisation_id=? ORDER BY site_id",
                (user["id"], user["organisation_id"]),
            ).fetchall()
        result = []
        for membership in memberships:
            site = self.get(conn, membership, "site", membership["site_id"])
            if site:
                result.append(dict(
                    id=site["id"], name=site["name"],
                    organisation_id=membership["organisation_id"],
                    organisation_name=site["organisation_name"], role=membership["role"],
                ))
        return sorted(result, key=lambda item: (item["name"].casefold(), item["id"]))

    @contextmanager
    def transaction(self):
        # FastAPI may run a synchronous yield dependency's enter, handler and
        # exit on different worker threads. This connection is still owned by
        # one sequential request/transaction, never shared with background jobs.
        # Those open independent connections and preserve BEGIN IMMEDIATE below.
        conn = sqlite3.connect(self.path, timeout=10, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA busy_timeout=10000")
        conn.execute("PRAGMA journal_mode=WAL")
        try:
            conn.execute("BEGIN IMMEDIATE")
            yield conn
            conn.commit()
        except BaseException:
            conn.rollback()
            raise
        finally:
            conn.close()

    @staticmethod
    def put(conn, user, kind, data):
        conn.execute(
            "INSERT INTO entities(id,kind,organisation_id,site_id,body,created_at) VALUES(?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET body=excluded.body WHERE entities.organisation_id=excluded.organisation_id AND entities.site_id=excluded.site_id AND entities.kind=excluded.kind",
            (
                data["id"],
                kind,
                user["organisation_id"],
                user["site_id"],
                encode(data),
                data.get("created_at", data.get("occurred_at", now())),
            ),
        )
        return data

    @staticmethod
    def get(conn, user, kind, entity_id):
        row = conn.execute(
            "SELECT body FROM entities WHERE id=? AND kind=? AND organisation_id=? AND site_id=?",
            (entity_id, kind, user["organisation_id"], user["site_id"]),
        ).fetchone()
        return json.loads(row[0]) if row else None

    @staticmethod
    def listing(conn, user, kind):
        return [
            json.loads(row[0])
            for row in conn.execute(
                "SELECT body FROM entities WHERE kind=? AND organisation_id=? AND site_id=? ORDER BY created_at DESC,id DESC LIMIT 200",
                (kind, user["organisation_id"], user["site_id"]),
            )
        ]

    @classmethod
    def audit(cls, conn, user, action, resource_type, resource_id, detail):
        item = dict(
            id=ident(),
            at=now(),
            actor=user["name"],
            action=action,
            resource_type=resource_type,
            resource_id=resource_id,
            detail=detail,
        )
        cls.put(conn, user, "audit", item)
        return item

    @classmethod
    def candidate(cls, camera, scenario, elapsed_minutes=0):
        scenarios = {
            "SHELF_EVENT": (
                "Shelf interaction for review",
                "SHELF_INTERACTION",
                "Synthetic shelf interaction. Review context before drawing a conclusion.",
            ),
            "RETURNED_ITEM": (
                "Item returned to shelf",
                "ITEM_RETURNED",
                "Synthetic sequence includes an item being returned. No loss is established.",
            ),
            "MISSING_MEDIA": (
                "Observation with missing media",
                "SHELF_INTERACTION",
                "Synthetic observation has no review image. Record the evidence limitation.",
            ),
            "HISTORICAL_EVENT": (
                "Historical observation",
                "SHELF_INTERACTION",
                "Synthetic delayed observation. This is historical context, not a current alert.",
            ),
        }
        title, event_label, summary = scenarios[scenario]
        cid = ident()
        return dict(
            id=cid,
            title=title,
            camera_id=camera["id"],
            camera_name=camera["name"],
            zone=camera["zone"],
            occurred_at=(
                datetime.now(timezone.utc) - timedelta(minutes=elapsed_minutes)
            )
            .isoformat()
            .replace("+00:00", "Z"),
            received_at=now(),
            status="NEW",
            source="SIMULATOR",
            scenario=scenario,
            event_label=event_label,
            summary=summary,
            media_status="MISSING" if scenario == "MISSING_MEDIA" else "AVAILABLE",
            historical=scenario == "HISTORICAL_EVENT",
            version=1,
            incident_id=None,
            evidence_url=None
            if scenario == "MISSING_MEDIA"
            else f"/api/evidence/{cid}",
        )

    def seed(self, conn):
        for branch, organisation, slug in [
            ("Harbour Pharmacy", "Harbour Demo Ltd", "harbour"),
            ("Liffey Pharmacy", "Liffey Demo Ltd", "liffey"),
        ]:
            self.seed_profile(
                conn,
                branch,
                organisation,
                slug,
                roles=("MANAGER", "REVIEWER") if slug == "harbour" else ("MANAGER",),
            )

    def ensure_mari_mina_profile(self, conn):
        """Add the requested demo workspace without resetting existing records."""
        if conn.execute(
            "SELECT 1 FROM users WHERE email=?", ("manager@marimina.demo",)
        ).fetchone():
            return
        self.seed_profile(
            conn,
            "Mari Mina Pharmacy",
            "Mari Mina Pharmacy (demo)",
            "marimina",
            seed_observations=False,
        )

    def seed_profile(
        self,
        conn,
        branch,
        organisation,
        slug,
        *,
        roles=("MANAGER",),
        seed_observations=True,
    ):
        site_id, organisation_id = ident(), ident()
        scope = dict(site_id=site_id, organisation_id=organisation_id)
        self.put(
            conn,
            scope,
            "site",
            dict(
                id=site_id,
                name=branch,
                organisation_name=organisation,
                timezone="Europe/Dublin",
                monthly_price_cents=6000,
                shift_active=False,
            ),
        )
        for role in roles:
            salt = secrets.token_hex(16)
            user = dict(
                id=ident(),
                email=f"{role.lower()}@{slug}.demo",
                name=f"{branch.removesuffix(' Pharmacy')} {role.title()}",
                role=role,
                **scope,
            )
            conn.execute(
                "INSERT INTO users VALUES(?,?,?,?,?,?,?,?)",
                (
                    user["id"],
                    user["email"],
                    user["name"],
                    role,
                    organisation_id,
                    site_id,
                    salt,
                    password_hash(PASSWORD, salt),
                ),
            )
        camera = dict(
            id=ident(),
            name="Front shop · simulator",
            zone="Open retail shelves",
            status="DEMO_ONLINE",
            connection_kind="SIMULATOR",
            last_seen_at=now(),
            detail="Synthetic source only. No physical camera is connected.",
            version=1,
        )
        self.put(conn, scope, "camera", camera)
        scenarios = (
            [
                ("SHELF_EVENT", 7),
                ("RETURNED_ITEM", 19),
                ("MISSING_MEDIA", 31),
                ("HISTORICAL_EVENT", 1560),
            ]
            if seed_observations
            else []
        )
        for scenario, minutes in scenarios:
            self.put(
                conn, scope, "candidate", self.candidate(camera, scenario, minutes)
            )
        self.audit(
            conn,
            user,
            "DEMO_SEEDED",
            "site",
            site_id,
            "Synthetic prototype fixtures created. No live monitoring is active.",
        )
