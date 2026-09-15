#!/usr/bin/env python3
"""Bounded external production health probe for an uptime monitor."""

import argparse
from datetime import datetime, timezone
import json
import re
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener


class HealthFailure(RuntimeError):
    pass


class RejectRedirects(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def check(origin: str, expected_sha: str | None = None, opener=None) -> dict:
    parsed = urlsplit(origin)
    if (parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password
            or parsed.path not in {"", "/"} or parsed.query or parsed.fragment):
        raise HealthFailure("Production origin must be a plain HTTPS origin.")
    if expected_sha is not None and not re.fullmatch(r"[0-9a-f]{40}", expected_sha):
        raise HealthFailure("Expected release SHA must be lowercase full-length hex.")
    client = opener or build_opener(RejectRedirects())
    results = {}
    for path in ("/health/live", "/health/ready", "/health/release"):
        try:
            with client.open(Request(origin.rstrip("/") + path, headers={"User-Agent": "AisleSignals-health/1"}), timeout=8) as response:
                body = response.read(16_385)
                if response.status != 200 or len(body) > 16_384:
                    raise HealthFailure(f"Health contract failed for {path}.")
                payload = json.loads(body)
        except (HTTPError, OSError, ValueError, json.JSONDecodeError):
            raise HealthFailure(f"Health contract failed for {path}.") from None
        results[path] = payload
    if results["/health/live"] != {"status": "alive", "stage": "management-console"}:
        raise HealthFailure("Liveness response is incompatible.")
    if results["/health/ready"] != {"status": "ready", "code": "READY", "scope": "database-schema-only"}:
        raise HealthFailure("Readiness response is incompatible.")
    release = results["/health/release"]
    if (type(release) is not dict or set(release) != {"environment", "release_sha"}
            or release.get("environment") != "production"
            or not re.fullmatch(r"[0-9a-f]{40}", release.get("release_sha", ""))
            or (expected_sha and release["release_sha"] != expected_sha)):
        raise HealthFailure("Production release identity is incompatible.")
    return {"status": "PASS", "checked_at": datetime.now(timezone.utc).isoformat(),
            "origin": origin.rstrip("/"), "release_sha": release["release_sha"],
            "scope": "public-health-and-schema"}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--origin", required=True)
    parser.add_argument("--expected-sha")
    args = parser.parse_args()
    try:
        print(json.dumps(check(args.origin, args.expected_sha), sort_keys=True))
        return 0
    except HealthFailure as error:
        print(json.dumps({"status": "FAIL", "message": str(error)}, sort_keys=True))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
