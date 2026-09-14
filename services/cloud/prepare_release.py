"""Explicit production pre-deploy migration followed by fresh readiness."""

import asyncio
import sys

from .config import CloudSettings
from .database import ReadinessProbe, SCHEMA_VERSION
from .migrate import migrate


def _fail(code: str) -> int:
    print(f"Cloud production pre-deploy stopped: {code}.", file=sys.stderr)
    return 1


def main() -> int:
    try:
        settings = CloudSettings.from_env()
    except Exception:
        return _fail("CONFIGURATION_INVALID")
    if settings.environment != "production":
        return _fail("PRODUCTION_ONLY")
    try:
        migrate(settings)
    except Exception:
        return _fail("MIGRATION_FAILED")
    try:
        result = asyncio.run(ReadinessProbe(settings).check())
        if not result.ready:
            return _fail("DATABASE_NOT_READY")
    except Exception:
        return _fail("DATABASE_NOT_READY")
    print(f"Cloud production schema migrated and verified (version {SCHEMA_VERSION}).", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
