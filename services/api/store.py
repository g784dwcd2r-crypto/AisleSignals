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
from functools import lru_cache
from pathlib import Path
from uuid import UUID, uuid4

from . import cloud_outbox

LOCAL_SCHEMA_VERSION = 3
_REFERENCE_OUTBOX_INSTALL = cloud_outbox.install_schema


def _cloud_schema_objects(conn):
    return {row[0]: (row[1], " ".join(row[2].split())) for row in conn.execute(
        "SELECT name,type,sql FROM sqlite_master WHERE name GLOB 'cloud_sync_*' AND sql IS NOT NULL"
    )}


@lru_cache(maxsize=1)
def _expected_cloud_schema():
    conn = sqlite3.connect(":memory:")
    try:
        conn.execute("BEGIN")
        _REFERENCE_OUTBOX_INSTALL(conn)
        return _cloud_schema_objects(conn)
    finally:
        conn.close()


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
        if version not in (0, 1, 2, LOCAL_SCHEMA_VERSION):
            raise RuntimeError(
                "Unsupported prototype database schema. Preserve this database and upgrade the application."
            )
        tables = {row[0] for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )}
        if version in (0, 1) and tables - {"sqlite_sequence"}:
            # The released v0/v1 builds used these exact two column signatures.
            # Names alone also match unrelated databases. Check before any
            # permission hardening, journal change or additive schema write.
            legacy_columns = {
                "users": ("id", "email", "name", "role", "organisation_id", "site_id", "salt", "password_hash"),
                "entities": ("id", "kind", "organisation_id", "site_id", "body", "created_at"),
            }
            for table, columns in legacy_columns.items():
                expected = [(name, "TEXT", int(index != 0), None, int(index == 0), 0)
                            for index, name in enumerate(columns)]
                actual = [(row[1], row[2].upper(), *row[3:]) for row in
                          conn.execute(f"PRAGMA table_xinfo({table})")]
                if table not in tables or actual != expected:
                    raise RuntimeError("Unrecognised local database. Select an AisleSignals database or a new path.")
            unique_email = any(
                row[1] == 1 and row[2] == 0 and
                [column[0] for column in conn.execute(
                    "SELECT name FROM pragma_index_info(?)", (row[0],))] == ["email"]
                for row in conn.execute("SELECT name,\"unique\",partial FROM pragma_index_list('users')")
            )
            if not unique_email:
                raise RuntimeError("Unrecognised local database. Select an AisleSignals database or a new path.")
        recorded_mode = None
        if "runtime_settings" in tables:
            row = conn.execute(
                "SELECT value FROM runtime_settings WHERE key='mode'"
            ).fetchone()
            recorded_mode = row[0] if row else None
        explicit_mode = recorded_mode
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
        if version >= 2 and explicit_mode is None:
            raise RuntimeError("Database provenance is missing. Preserve this database; automatic repair is refused.")
        identity = None
        if "runtime_settings" in tables:
            identity = conn.execute("SELECT value FROM runtime_settings WHERE key='installation_id'").fetchone()
        if identity is not None:
            try:
                if str(UUID(identity[0])) != identity[0] or UUID(identity[0]).int == 0:
                    raise ValueError
            except (ValueError, TypeError, AttributeError):
                raise RuntimeError("Invalid local installation identity. Preserve this database; automatic replacement is refused.") from None
        objects = _cloud_schema_objects(conn)
        if objects or version == LOCAL_SCHEMA_VERSION:
            if objects != _expected_cloud_schema():
                raise RuntimeError("Incompatible local cloud schema. Preserve this database; automatic repair is refused.")
            rows = conn.execute("SELECT version FROM cloud_sync_schema").fetchall()
            if len(rows) != 1 or rows[0][0] != cloud_outbox.SCHEMA_VERSION:
                raise RuntimeError("Unsupported local cloud schema version.")
            bindings = conn.execute("SELECT DISTINCT installation_id FROM cloud_sync_bindings").fetchall()
            if (version == LOCAL_SCHEMA_VERSION and identity is None) or any(not identity or row[0] != identity[0] for row in bindings):
                raise RuntimeError("Local cloud bindings do not match the installation identity.")
            if mode == "synthetic" and bindings:
                raise RuntimeError("Synthetic workspaces cannot contain cloud bindings.")

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
        # Do not change WAL mode until schema migration has committed. A failed
        # additive migration must roll back all DDL, identity and version changes.
        with self.transaction(initialising=True) as conn:
            # Check provenance before any schema changes. An unmarked existing
            # database belongs to the legacy synthetic build, never to a pilot.
            self.validate_provenance(conn, mode)
            base_schema = """
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

            """
            for statement in base_schema.split(";"):
                if statement.strip():
                    conn.execute(statement)
            cloud_outbox.install_schema(conn)
            conn.execute(f"PRAGMA user_version={LOCAL_SCHEMA_VERSION}")
            conn.execute("INSERT OR IGNORE INTO runtime_settings VALUES('mode',?)", (mode,))
            conn.execute("INSERT OR IGNORE INTO runtime_settings VALUES('installation_id',?)", (str(uuid4()),))
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
    def transaction(self, *, initialising=False):
        # FastAPI may run a synchronous yield dependency's enter, handler and
        # exit on different worker threads. This connection is still owned by
        # one sequential request/transaction, never shared with background jobs.
        # Those open independent connections and preserve BEGIN IMMEDIATE below.
        conn = sqlite3.connect(self.path, timeout=10, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA busy_timeout=10000")
        try:
            # Read-only provenance checks happen before journal/header writes.
            self.validate_provenance(conn, self.mode)
            if not initialising:
                conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("BEGIN IMMEDIATE")
            self.validate_provenance(conn, self.mode)
            yield conn
            conn.commit()
            if initialising:
                conn.execute("PRAGMA journal_mode=WAL")
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
