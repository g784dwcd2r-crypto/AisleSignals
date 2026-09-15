import json
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request

import pytest

from deployment.verify_cloud_release import AuditFailure, audit


HEADERS = {
    "Cache-Control": "no-store",
    "Strict-Transport-Security": "max-age=31536000",
    "Content-Security-Policy": "default-src 'none'; frame-ancestors 'none'; object-src 'none'",
    "Permissions-Policy": "camera=(), microphone=(), display-capture=()",
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
}
RELEASE_SHA = "a" * 40


class Response:
    def __init__(self, status, body, headers=None):
        self.status = status
        self._body = body
        self.headers = headers or HEADERS

    def read(self, size=-1):
        return self._body[:size] if size >= 0 else self._body

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return None

    def close(self):
        return None


class Opener:
    def __init__(self, files, changes=None):
        self.files = files
        self.changes = changes or {}

    def open(self, request: Request, timeout):
        endpoint = "/" + request.full_url.split("/", 3)[-1] if request.full_url.count("/") >= 3 else "/"
        endpoint = endpoint if endpoint != "//" else "/"
        contracts = {
            "/health/live": (200, {"status": "alive", "stage": "management-console"}),
            "/health/ready": (200, {"status": "ready", "code": "READY", "scope": "database-schema-only"}),
            "/health/release": (200, {"environment": "production", "release_sha": RELEASE_SHA}),
            "/control-api/session": (401, {"error": {"code": "SESSION_REQUIRED", "message": "Sign in again to continue."}}),
            "/api/health": (404, {"detail": "Not Found"}),
            "/control-api/setup/status": (200, {"configured": True, "needs_setup": False}),
        }
        if endpoint in self.files:
            result = (200, self.files[endpoint])
        else:
            status, value = contracts[endpoint]
            result = (status, json.dumps(value, separators=(",", ":")).encode())
        status, body = self.changes.get(endpoint, result)
        if status >= 400:
            raise HTTPError(request.full_url, status, "synthetic", HEADERS, Response(status, body))
        return Response(status, body)


def fixture(tmp_path):
    root = tmp_path / "dist"
    (root / "assets").mkdir(parents=True)
    (root / "index.html").write_bytes(b"<title>AisleSignals</title>")
    (root / "assets/app.js").write_bytes(b"console.log('synthetic')")
    return root, {"/": (root / "index.html").read_bytes(), "/assets/app.js": (root / "assets/app.js").read_bytes()}


def test_audit_matches_exact_frontend_and_public_fail_closed_contract(tmp_path, monkeypatch):
    root, files = fixture(tmp_path)
    monkeypatch.setattr("deployment.verify_cloud_release._checkout_sha",
                        lambda repo, expected: RELEASE_SHA)
    result = audit("https://control.example.test", root, expected_sha=RELEASE_SHA,
                   require_configured=True, opener=Opener(files))
    assert result["frontend_exact_match"] is True
    assert result["setup"] == {"configured": True, "needs_setup": False}
    assert set(result["files"]) == {"index.html", "assets/app.js"}
    assert result["public_checks"]["/control-api/session"] == 401
    assert result["deployed_release"] == {"environment": "production", "release_sha": RELEASE_SHA}


def test_audit_accepts_owner_bootstrap_pending_when_named_owner_is_not_required(tmp_path, monkeypatch):
    root, files = fixture(tmp_path)
    monkeypatch.setattr("deployment.verify_cloud_release._checkout_sha",
                        lambda repo, expected: RELEASE_SHA)
    pending = {"/control-api/setup/status": (
        200, b'{"configured":true,"needs_setup":true}'
    )}
    result = audit("https://control.example.test", root, expected_sha=RELEASE_SHA,
                   opener=Opener(files, pending))
    assert result["setup"] == {"configured": True, "needs_setup": True}


def test_audit_requires_named_owner_when_requested(tmp_path, monkeypatch):
    root, files = fixture(tmp_path)
    monkeypatch.setattr("deployment.verify_cloud_release._checkout_sha",
                        lambda repo, expected: RELEASE_SHA)
    pending = {"/control-api/setup/status": (
        200, b'{"configured":true,"needs_setup":true}'
    )}
    with pytest.raises(AuditFailure, match="named-owner setup"):
        audit("https://control.example.test", root, expected_sha=RELEASE_SHA,
              require_configured=True, opener=Opener(files, pending))


@pytest.mark.parametrize("state", [
    b'{"configured":false,"needs_setup":false}',
    b'{"configured":false,"needs_setup":true}',
])
def test_audit_rejects_unusable_setup_state(tmp_path, state):
    root, files = fixture(tmp_path)
    impossible = {"/control-api/setup/status": (200, state)}
    with pytest.raises(AuditFailure, match="invalid shape"):
        audit("https://control.example.test", root, opener=Opener(files, impossible))


@pytest.mark.parametrize("base_url", [
    "http://control.example.test", "https://user@control.example.test",
    "https://control.example.test/path", "https://control.example.test/?token=secret",
])
def test_audit_rejects_unsafe_origins(tmp_path, base_url):
    root, files = fixture(tmp_path)
    with pytest.raises(AuditFailure):
        audit(base_url, root, opener=Opener(files))


@pytest.mark.parametrize("endpoint,change", [
    ("/health/ready", (503, b'{"status":"unavailable"}')),
    ("/health/release", (404, b'{"detail":"Not Found"}')),
    ("/health/release", (200, b'{"environment":"production","release_sha":"invalid"}')),
    ("/control-api/session", (200, b'{}')),
    ("/api/health", (200, b'{"status":"ok"}')),
    ("/assets/app.js", (200, b"changed")),
])
def test_audit_rejects_unready_auth_mount_and_asset_drift(tmp_path, endpoint, change):
    root, files = fixture(tmp_path)
    with pytest.raises(AuditFailure):
        audit("https://control.example.test", root, opener=Opener(files, {endpoint: change}))


def test_audit_rejects_backend_sha_that_differs_from_expected_release(tmp_path, monkeypatch):
    root, files = fixture(tmp_path)
    monkeypatch.setattr("deployment.verify_cloud_release._checkout_sha",
                        lambda repo, expected: RELEASE_SHA)
    changed = {"/health/release": (200, json.dumps({
        "environment": "production", "release_sha": "b" * 40,
    }).encode())}
    with pytest.raises(AuditFailure, match="Deployed backend SHA"):
        audit("https://control.example.test", root, expected_sha=RELEASE_SHA,
              opener=Opener(files, changed))


def test_audit_rejects_incomplete_security_headers(tmp_path):
    root, files = fixture(tmp_path)
    opener = Opener(files)
    response = Response(200, b'{}', {**HEADERS, "X-Frame-Options": "SAMEORIGIN"})
    opener.open = lambda request, timeout: response
    with pytest.raises(AuditFailure, match="x-frame-options"):
        audit("https://control.example.test", root, opener=opener)
