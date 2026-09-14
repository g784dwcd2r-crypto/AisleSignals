"""Explicit, transactional sequential cloud migrations; no account seeding."""

import sys

import psycopg

from .config import CloudSettings, ConfigurationError
from .database import MIGRATIONS, SCHEMA_VERSION, connection_options


class MigrationError(RuntimeError):
    pass


def migrate(settings: CloudSettings) -> None:
    if not settings.database_url:
        raise MigrationError("Cloud migration requires DATABASE_URL.")
    try:
        with psycopg.connect(settings.database_url, **connection_options(settings, migration=True)) as connection:
            with connection.cursor() as cursor:
                # Serialise concurrent deploys before any schema creation.
                cursor.execute("SELECT pg_advisory_xact_lock(6802449210733)")
                cursor.execute(MIGRATIONS[0][1])
                cursor.execute("SELECT version, checksum FROM aislesignals_control.schema_version WHERE singleton = true FOR UPDATE")
                current = cursor.fetchone()
                if current is None:
                    current = (1, MIGRATIONS[0][2])
                    cursor.execute(
                        "INSERT INTO aislesignals_control.schema_version (singleton, version, checksum) VALUES (true, %s, %s)", current,
                    )
                version, checksum = current
                if version < 1 or version > len(MIGRATIONS) or checksum != MIGRATIONS[version - 1][2]:
                    raise MigrationError("Cloud schema version or checksum is incompatible; no migration applied.")
                for next_version, sql, next_checksum in MIGRATIONS[version:]:
                    cursor.execute(sql)
                    cursor.execute(
                        "UPDATE aislesignals_control.schema_version SET version=%s, checksum=%s, installed_at=CURRENT_TIMESTAMP WHERE singleton=true",
                        (next_version, next_checksum),
                    )
    except MigrationError:
        raise
    except Exception:
        raise MigrationError("Cloud migration failed; check database configuration and availability.") from None


def main() -> int:
    try:
        migrate(CloudSettings.from_env())
    except (ConfigurationError, MigrationError) as error:
        print(str(error), file=sys.stderr)
        return 1
    print(f"Cloud schema is ready (version {SCHEMA_VERSION}). No accounts were seeded.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
