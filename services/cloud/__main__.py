"""Render entry point; one bounded process, no access log or proxy trust."""

import sys

import uvicorn

from .app import create_app
from .config import CloudSettings, ConfigurationError


def main() -> int:
    try:
        settings = CloudSettings.from_env()
    except ConfigurationError as error:
        print(str(error), file=sys.stderr)
        return 1
    uvicorn.run(
        create_app(settings),
        host=settings.bind_host,
        port=settings.port,
        workers=1,
        access_log=False,
        proxy_headers=False,
        server_header=False,
        timeout_keep_alive=5,
        timeout_graceful_shutdown=10,
        limit_concurrency=32,
        h11_max_incomplete_event_size=16384,
        log_level="warning",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
