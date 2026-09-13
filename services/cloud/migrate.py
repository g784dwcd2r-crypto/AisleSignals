"""Explicit, transactional bootstrap of infrastructure metadata only."""

import sys

import psycopg

from .config import CloudSettings, ConfigurationError
from .database import MIGRATION_SQL, SCHEMA_CHECKSUM, SCHEMA_VERSION, connection_options


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
                cursor.execute(MIGRATION_SQL)
                cursor.execute("SELECT version, checksum FROM aislesignals_control.schema_version WHERE singleton = true")
                current = cursor.fetchone()
                if current is None:
                    cursor.execute(
                        "INSERT INTO aislesignals_control.schema_version (singleton, version, checksum) VALUES (true, %s, %s)",
                        (SCHEMA_VERSION, SCHEMA_CHECKSUM),
                    )
                elif current != (SCHEMA_VERSION, SCHEMA_CHECKSUM):
                    raise MigrationError("Cloud schema version or checksum is incompatible; no migration applied.")
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
    print("Cloud infrastructure schema is ready (version 1). No customer tables or accounts were created.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
