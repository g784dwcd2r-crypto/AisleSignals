#!/usr/bin/env python3
"""Read-only verification of a deployed AisleSignals cloud release."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
import re
import subprocess
import tempfile
from urllib.error import HTTPError
from urllib.parse import quote, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener


MAX_RESPONSE_BYTES = 3 * 1024 * 1024
SECURITY_HEADERS = {
    "cache-control": "no-store",
    "x-content-type-options": "nosniff",
    "x-frame-options": "DENY",
    "referrer-policy": "no-referrer",
}


class AuditFailure(RuntimeError):
    pass


class _RejectRedirects(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _base_url(value: str) -> str:
    parsed = urlsplit(value)
    if (parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password
            or parsed.query or parsed.fragment or parsed.path not in {"", "/"}):
        raise AuditFailure("Base URL must be an HTTPS origin without credentials, path, query or fragment.")
    return f"https://{parsed.netloc}"


def _read_bounded(response) -> bytes:
    body = response.read(MAX_RESPONSE_BYTES + 1)
    if len(body) > MAX_RESPONSE_BYTES:
        raise AuditFailure("Deployment response exceeds the audit limit.")
    return body


def _request(opener, base_url: str, endpoint: str) -> tuple[int, dict[str, str], bytes]:
    request = Request(base_url + endpoint, headers={"User-Agent": "AisleSignals-release-audit/1"})
    try:
        with opener.open(request, timeout=8) as response:
            return response.status, {key.lower(): value for key, value in response.headers.items()}, _read_bounded(response)
    except HTTPError as error:
        try:
            return error.code, {key.lower(): value for key, value in error.headers.items()}, _read_bounded(error)
        finally:
            error.close()
    except Exception:
        raise AuditFailure(f"Deployment request failed for {endpoint}.") from None


def _json(body: bytes, endpoint: str):
    try:
        return json.loads(body)
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise AuditFailure(f"Deployment returned invalid JSON for {endpoint}.") from None


def _security(headers: dict[str, str], endpoint: str) -> None:
    for name, expected in SECURITY_HEADERS.items():
        if headers.get(name) != expected:
            raise AuditFailure(f"Required {name} header is missing or invalid for {endpoint}.")
    if "max-age=" not in headers.get("strict-transport-security", ""):
        raise AuditFailure(f"HSTS is missing for {endpoint}.")
    csp = headers.get("content-security-policy", "")
    for directive in ("default-src 'none'", "frame-ancestors 'none'", "object-src 'none'"):
        if directive not in csp:
            raise AuditFailure(f"Content Security Policy is incomplete for {endpoint}.")
    permissions = headers.get("permissions-policy", "")
    for directive in ("camera=()", "microphone=()", "display-capture=()"):
        if directive not in permissions:
            raise AuditFailure(f"Permissions Policy is incomplete for {endpoint}.")


def _checkout_sha(repo: Path, expected_sha: str | None) -> str:
    actual = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, check=True, capture_output=True, text=True
    ).stdout.strip()
    if expected_sha:
        if not re.fullmatch(r"[0-9a-f]{40}", expected_sha) or actual != expected_sha:
            raise AuditFailure("Expected release SHA does not match the checked-out commit.")
        dirty = subprocess.run(
            ["git", "status", "--porcelain", "--untracked-files=no"], cwd=repo,
            check=True, capture_output=True, text=True,
        ).stdout
        if dirty:
            raise AuditFailure("Tracked files differ from the expected release commit.")
    return actual


def audit(base_url: str, web_dist: Path, *, expected_sha: str | None = None,
          require_configured: bool = False, opener=None) -> dict:
    origin = _base_url(base_url)
    root = web_dist.resolve()
    if not root.is_dir() or root.is_symlink():
        raise AuditFailure("Built management console directory is unavailable.")
    repo = Path(__file__).resolve().parents[1]
    release_sha = _checkout_sha(repo, expected_sha)
    client = opener or build_opener(_RejectRedirects())
    expected = {
        "/health/live": (200, {"status": "alive", "stage": "management-console"}),
        "/health/ready": (200, {"status": "ready", "code": "READY", "scope": "database-schema-only"}),
        "/control-api/session": (401, {"error": {"code": "SESSION_REQUIRED", "message": "Sign in again to continue."}}),
        "/api/health": (404, {"detail": "Not Found"}),
    }
    checks = {}
    for endpoint, (expected_status, expected_body) in expected.items():
        status, headers, body = _request(client, origin, endpoint)
        _security(headers, endpoint)
        if status != expected_status or _json(body, endpoint) != expected_body:
            raise AuditFailure(f"Deployment contract mismatch for {endpoint}.")
        checks[endpoint] = status

    status, headers, body = _request(client, origin, "/health/release")
    _security(headers, "/health/release")
    deployed = _json(body, "/health/release")
    if (status != 200 or type(deployed) is not dict or set(deployed) != {"environment", "release_sha"}
            or deployed.get("environment") not in {"production", "staging"}
            or not re.fullmatch(r"[0-9a-f]{40}", deployed.get("release_sha", ""))):
        raise AuditFailure("Deployment release identity has an invalid shape.")
    if expected_sha and deployed["release_sha"] != expected_sha:
        raise AuditFailure("Deployed backend SHA does not match the expected release SHA.")
    checks["/health/release"] = status

    status, headers, body = _request(client, origin, "/control-api/setup/status")
    _security(headers, "/control-api/setup/status")
    setup = _json(body, "/control-api/setup/status")
    if (status != 200 or type(setup) is not dict or set(setup) != {"configured", "needs_setup"}
            or type(setup.get("configured")) is not bool
            or type(setup.get("needs_setup")) is not bool
            or (setup["needs_setup"] and not setup["configured"])):
        raise AuditFailure("Deployment setup status has an invalid shape.")
    if require_configured and setup != {"configured": True, "needs_setup": False}:
        raise AuditFailure("Deployment has not completed named-owner setup.")
    checks["/control-api/setup/status"] = status

    files = {}
    for local in sorted(candidate for candidate in root.rglob("*") if candidate.is_file()):
        if local.is_symlink() or local.stat().st_size > MAX_RESPONSE_BYTES:
            raise AuditFailure("Built management console contains an unsafe audit file.")
        relative = local.relative_to(root).as_posix()
        endpoint = "/" if relative == "index.html" else "/" + quote(relative, safe="/")
        status, headers, remote = _request(client, origin, endpoint)
        _security(headers, endpoint)
        digest = sha256(local.read_bytes()).hexdigest()
        if status != 200 or sha256(remote).hexdigest() != digest:
            raise AuditFailure(f"Deployed frontend differs from the local release at {relative}.")
        files[relative] = digest
    if "index.html" not in files:
        raise AuditFailure("Built management console has no index.html.")

    return {
        "schema_version": 2,
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "base_url": origin,
        "release_sha": release_sha,
        "deployed_release": deployed,
        "frontend_exact_match": True,
        "files": files,
        "public_checks": checks,
        "setup": setup,
        "limits": [
            "Public checks do not verify the Render plan, secrets, backups, restore access or database isolation.",
        ],
    }


def _write_atomic(path: Path, result: dict) -> None:
    path = path.resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", dir=path.parent, prefix=".release-audit-", delete=False) as target:
        target.write(json.dumps(result, indent=2, sort_keys=True) + "\n")
        temporary = Path(target.name)
    temporary.chmod(0o600)
    temporary.replace(path)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--web-dist", type=Path, default=Path("apps/control/dist"))
    parser.add_argument("--expected-sha")
    parser.add_argument("--require-configured", action="store_true")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    try:
        result = audit(args.base_url, args.web_dist, expected_sha=args.expected_sha,
                       require_configured=args.require_configured)
        if args.output:
            _write_atomic(args.output, result)
        print(json.dumps(result, sort_keys=True))
        return 0
    except (AuditFailure, OSError, subprocess.SubprocessError) as error:
        print(json.dumps({"status": "FAILED", "message": str(error)}, sort_keys=True))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
