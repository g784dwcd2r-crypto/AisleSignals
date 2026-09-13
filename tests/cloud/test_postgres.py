"""Opt-in real PostgreSQL checks, using only a process-owned disposable cluster.

CLOUD_RUN_POSTGRES_TESTS=1 python -m pytest tests/cloud/test_postgres.py -q
Requires PostgreSQL initdb/pg_ctl on PATH; never uses an existing database URL.
"""

import asyncio
from concurrent.futures import ThreadPoolExecutor
import os
from pathlib import Path
import shlex
import shutil
import socket
import subprocess
import tempfile

import psycopg
import pytest

from services.cloud.config import CloudSettings
from services.cloud.database import ReadinessProbe, SCHEMA_CHECKSUM, SCHEMA_VERSION, check_schema
from services.cloud.migrate import MigrationError, migrate

pytestmark = pytest.mark.skipif(os.environ.get("CLOUD_RUN_POSTGRES_TESTS") != "1", reason="Explicit opt-in required for disposable PostgreSQL process")


@pytest.fixture(scope="module")
def postgres_settings():
    initdb, pg_ctl = shutil.which("initdb"), shutil.which("pg_ctl")
    if not initdb or not pg_ctl:
        pytest.fail("PostgreSQL initdb and pg_ctl must be installed for this explicit test run")
    if os.name == "nt":
        pytest.fail("Disposable PostgreSQL harness currently supports POSIX; unit/HTTP checks cover Windows")
    with tempfile.TemporaryDirectory(prefix="as-cloud-pg-") as directory:
        root = Path(directory)
        # Cluster and socket share the private temp root; no user data is read.
        data, password = root / "data", root / "password"
        password.write_text("synthetic-cloud-test-password\n")
        password.chmod(0o600)
        env = {key: value for key, value in os.environ.items() if not key.startswith(("PG", "DATABASE_", "CLOUD_"))}
        env["LC_ALL"] = "C"
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        subprocess.run([initdb, "-D", str(data), "-U", "cloud_test", "--no-locale", "--encoding=UTF8", "--auth-local=trust", "--auth-host=scram-sha-256", f"--pwfile={password}"], env=env, capture_output=True, check=True, timeout=30)
        started = False
        try:
            options = shlex.join(["-h", "127.0.0.1", "-p", str(port), "-k", str(root), "-c", "max_connections=10"])
            subprocess.run([pg_ctl, "-D", str(data), "-l", str(root / "server.log"), "-o", options, "-w", "-t", "10", "start"], env=env, capture_output=True, check=True, timeout=15)
            started = True
            yield CloudSettings.from_env({
                "CLOUD_ENV": "development", "CLOUD_DATABASE_SSLMODE": "disable",
                "DATABASE_URL": f"postgresql://cloud_test:synthetic-cloud-test-password@127.0.0.1:{port}/postgres",
            })
        finally:
            if started or (data / "postmaster.pid").exists():
                subprocess.run([pg_ctl, "-D", str(data), "-m", "fast", "-w", "-t", "10", "stop"], env=env, capture_output=True, check=True, timeout=15)


@pytest.fixture
def empty_database(postgres_settings):
    with psycopg.connect(postgres_settings.database_url) as connection:
        connection.execute("DROP SCHEMA IF EXISTS aislesignals_control CASCADE")
    return postgres_settings


def test_actual_missing_schema_then_idempotent_migration_and_readiness(empty_database):
    assert asyncio.run(ReadinessProbe(empty_database).check()).ready is False
    migrate(empty_database)
    migrate(empty_database)
    assert asyncio.run(check_schema(empty_database)) is True
    with psycopg.connect(empty_database.database_url) as connection:
        rows = connection.execute("SELECT version, checksum FROM aislesignals_control.schema_version").fetchall()
        assert rows == [(SCHEMA_VERSION, SCHEMA_CHECKSUM)]
        tables = connection.execute("SELECT tablename FROM pg_tables WHERE schemaname = 'aislesignals_control'").fetchall()
        assert tables == [("schema_version",)]


def test_actual_concurrent_migrations_are_serialized(empty_database):
    with ThreadPoolExecutor(max_workers=3) as executor:
        results = list(executor.map(lambda _: migrate(empty_database), range(3)))
    assert results == [None, None, None]
    assert asyncio.run(check_schema(empty_database)) is True


@pytest.mark.parametrize("field,value", [("version", 2), ("checksum", "f" * 64)])
def test_incompatible_schema_does_not_get_rewritten(empty_database, field, value):
    migrate(empty_database)
    with psycopg.connect(empty_database.database_url) as connection:
        connection.execute(psycopg.sql.SQL("UPDATE aislesignals_control.schema_version SET {} = %s").format(psycopg.sql.Identifier(field)), (value,))
    with pytest.raises(MigrationError, match="incompatible"):
        migrate(empty_database)
    assert asyncio.run(check_schema(empty_database)) is False
    with psycopg.connect(empty_database.database_url) as connection:
        assert connection.execute(psycopg.sql.SQL("SELECT {} FROM aislesignals_control.schema_version").format(psycopg.sql.Identifier(field))).fetchone() == (value,)


def test_locked_database_probe_has_bounded_failure(empty_database):
    migrate(empty_database)
    with psycopg.connect(empty_database.database_url) as blocker:
        blocker.execute("LOCK TABLE aislesignals_control.schema_version IN ACCESS EXCLUSIVE MODE")
        result = asyncio.run(ReadinessProbe(empty_database).check())
        assert result.ready is False
        assert result.code in {"DATABASE_UNAVAILABLE", "DATABASE_TIMEOUT"}
    assert asyncio.run(check_schema(empty_database)) is True
