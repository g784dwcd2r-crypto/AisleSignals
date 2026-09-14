"""Explicit cloud schema migration then serve, for staging only.

Run with ``python -m services.cloud.start_with_schema``. The normal server
entry point does not migrate. This is not a production migration framework.
"""

import asyncio
import sys

from . import __main__ as server
from .config import CloudSettings
from .database import ReadinessProbe
from .migrate import migrate


def _fail(code: str) -> int:
    # Codes are constant strings, never driver exceptions or environment values.
    print(f"Cloud staging startup stopped: {code}. Server was not started.", file=sys.stderr)
    return 1


def main() -> int:
    try:
        settings = CloudSettings.from_env()
    except Exception:
        return _fail("CONFIGURATION_INVALID")
    if settings.environment != "staging":
        return _fail("STAGING_ONLY")
    if not settings.database_url:
        return _fail("DATABASE_REQUIRED")
    try:
        migrate(settings)
    except Exception:
        return _fail("MIGRATION_FAILED")
    try:
        # A fresh bounded connection verifies the committed schema before the
        # process starts accepting traffic. No liveness-only success shortcut.
        result = asyncio.run(ReadinessProbe(settings).check())
        if not result.ready:
            return _fail("DATABASE_NOT_READY")
    except Exception:
        return _fail("DATABASE_NOT_READY")
    print("Cloud staging schema verified. Management access requires configured credentials and verified MFA.", flush=True)
    return server.main()


if __name__ == "__main__":
    raise SystemExit(main())
