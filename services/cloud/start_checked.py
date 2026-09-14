"""Verify the production schema before serving, without running migrations."""

import asyncio
import sys

from . import __main__ as server
from .config import CloudSettings
from .database import ReadinessProbe


def _fail(code: str) -> int:
    print(f"Cloud production startup stopped: {code}. Server was not started.", file=sys.stderr)
    return 1


def main() -> int:
    try:
        settings = CloudSettings.from_env()
    except Exception:
        return _fail("CONFIGURATION_INVALID")
    if settings.environment != "production":
        return _fail("PRODUCTION_ONLY")
    try:
        result = asyncio.run(ReadinessProbe(settings).check())
        if not result.ready:
            return _fail("DATABASE_NOT_READY")
    except Exception:
        return _fail("DATABASE_NOT_READY")
    print("Cloud production schema verified. No migration was run by the server.", flush=True)
    return server.main()


if __name__ == "__main__":
    raise SystemExit(main())
