"""Bounded PostgreSQL checks, with no customer tables or SQLite dependency."""

import asyncio
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from time import monotonic
from collections.abc import Awaitable, Callable

import psycopg

from .config import CloudSettings

SCHEMA_VERSION = 1
MIGRATION_SQL = (Path(__file__).parent / "migrations" / "001_control_plane.sql").read_text(encoding="utf-8")
SCHEMA_CHECKSUM = sha256(MIGRATION_SQL.encode("utf-8")).hexdigest()
PROBE_TIMEOUT_SECONDS = 2.5
CACHE_SECONDS = 1.0


def connection_options(settings: CloudSettings, *, migration: bool = False) -> dict:
    return {
        "sslmode": settings.database_sslmode,
        "connect_timeout": 3 if migration else 2,
        "application_name": "aislesignals-cloud-migrate" if migration else "aislesignals-cloud-health",
        "options": "-c statement_timeout=5000 -c lock_timeout=2000 -c idle_in_transaction_session_timeout=5000"
        if migration else "-c statement_timeout=1000 -c lock_timeout=500 -c default_transaction_read_only=on",
    }


async def check_schema(settings: CloudSettings) -> bool:
    if not settings.database_url:
        return False
    connection = None
    try:
        connection = await psycopg.AsyncConnection.connect(
            settings.database_url, autocommit=True, **connection_options(settings)
        )
        async with connection.cursor() as cursor:
            await cursor.execute("SELECT version, checksum FROM aislesignals_control.schema_version WHERE singleton = true")
            return await cursor.fetchone() == (SCHEMA_VERSION, SCHEMA_CHECKSUM)
    finally:
        if connection is not None:
            await connection.close()


@dataclass(frozen=True)
class Readiness:
    ready: bool
    code: str


class ReadinessProbe:
    """At most one database check per process; no request queue or unbounded pool.

    Cache hits are at most one second old. A concurrent cache miss returns busy
    immediately. Timeouts never reveal driver exceptions/connection strings.
    """

    def __init__(
        self,
        settings: CloudSettings,
        checker: Callable[[CloudSettings], Awaitable[bool]] = check_schema,
        *,
        clock: Callable[[], float] = monotonic,
        timeout_seconds: float = PROBE_TIMEOUT_SECONDS,
    ):
        self.settings = settings
        self.checker = checker
        self.clock = clock
        self.timeout_seconds = timeout_seconds
        self._checking = False
        self._cached: Readiness | None = None
        self._checked_at = 0.0

    async def check(self) -> Readiness:
        if not self.settings.database_url:
            return Readiness(False, "DATABASE_NOT_CONFIGURED")
        now = self.clock()
        if self._cached and 0 <= now - self._checked_at < CACHE_SECONDS:
            return self._cached
        if self._checking:
            return Readiness(False, "READINESS_BUSY")
        self._checking = True
        try:
            async with asyncio.timeout(self.timeout_seconds):
                ready = await self.checker(self.settings)
            result = Readiness(bool(ready), "READY" if ready else "DATABASE_SCHEMA_UNAVAILABLE")
        except TimeoutError:
            result = Readiness(False, "DATABASE_TIMEOUT")
        except Exception:
            result = Readiness(False, "DATABASE_UNAVAILABLE")
        finally:
            self._checking = False
        self._cached, self._checked_at = result, self.clock()
        return result
