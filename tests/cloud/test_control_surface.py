"""Public console boundary checks using only synthetic static content."""

import asyncio
from pathlib import Path

from fastapi.testclient import TestClient
import pytest

from services.cloud.app import MAX_BODY, SafeResponses, create_app
from services.cloud.config import CloudSettings


@pytest.fixture
def surface(tmp_path):
    web = tmp_path / "public"
    (web / "assets").mkdir(parents=True)
    (web / "index.html").write_text('<!doctype html><title>Synthetic Console</title><script src="/assets/app.js"></script>')
    (web / "assets/app.js").write_text('window.syntheticConsole = true;')
    settings = CloudSettings.from_env({"CLOUD_ENV":"staging","CLOUD_ALLOWED_HOSTS":"testserver"})
    app = create_app(settings,web_dist=web)
    with TestClient(app,base_url="https://testserver") as client:
        yield client,app,web


@pytest.mark.parametrize("path", ["/", "/assets/app.js", "/health/live", "/health/ready", "/control-api/setup/status", "/missing"])
def test_public_responses_apply_security_headers(surface, path):
    client,_,_ = surface
    response = client.get(path)
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["x-frame-options"] == "DENY"
    assert response.headers["referrer-policy"] == "no-referrer"
    assert response.headers["strict-transport-security"] == "max-age=31536000"
    assert response.headers["permissions-policy"] == "camera=(), microphone=(), geolocation=(), display-capture=()"
    csp = response.headers["content-security-policy"]
    for directive in ("default-src 'none'", "script-src 'self'", "connect-src 'self'", "frame-ancestors 'none'", "base-uri 'none'", "form-action 'self'", "object-src 'none'"):
        assert directive in csp
    assert "unsafe-inline" not in csp and "unsafe-eval" not in csp


def test_static_shell_has_no_account_or_database_dependency(surface):
    client,_,_ = surface
    response = client.get("/")
    assert response.status_code == 200 and "Synthetic Console" in response.text
    assert client.get("/assets/app.js").status_code == 200
    assert client.get("/health/ready").status_code == 503
    assert client.get("/control-api/setup/status").json() == {"configured":False,"needs_setup":False}
    assert not client.cookies


@pytest.mark.parametrize("path", ["/api/runtime", "/api/setup", "/api/auth/login", "/api/sites", "/api/cameras", "/api/incidents", "/api/media/example", "/api/interaction/jobs", "/api/model/setup", "/docs", "/openapi.json", "/redoc", "/.env", "/services/api/app.py", "/services/cloud/config.py"])
def test_local_pilot_routes_and_internal_files_are_absent(surface, path):
    client,_,_ = surface
    response = client.get(path)
    assert response.status_code == 404
    assert "text/html" not in response.headers.get("content-type", "")


def test_unknown_host_and_cross_origin_mutation_fail_closed(surface):
    client,_,_ = surface
    assert client.get("/",headers={"Host":"attacker.example"}).status_code == 400
    response = client.post("/control-api/login",json={"email":"synthetic@example.test","password":"synthetic"},headers={"Origin":"https://attacker.example"})
    assert response.status_code == 403
    assert "access-control-allow-origin" not in response.headers
    preflight = client.options("/control-api/login",headers={"Origin":"https://attacker.example","Access-Control-Request-Method":"POST"})
    assert "access-control-allow-origin" not in preflight.headers


def test_validation_and_unexpected_errors_never_echo_secrets(surface, capsys, caplog):
    client,app,_ = surface
    secret = "synthetic-private-token-that-must-not-be-echoed"
    response = client.post("/control-api/login",content='{"password":"'+secret+'","email":17}',headers={"Origin":"https://testserver","Content-Type":"application/json"})
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "INVALID_INPUT"
    assert secret not in response.text

    @app.get("/synthetic-failure")
    def broken():
        raise RuntimeError(secret)

    response = client.get("/synthetic-failure")
    assert response.status_code == 500
    assert response.json()["error"]["code"] == "INTERNAL_ERROR"
    assert secret not in response.text and secret not in caplog.text
    captured = capsys.readouterr()
    assert secret not in captured.out + captured.err


@pytest.mark.parametrize("length", [MAX_BODY+1, MAX_BODY*2])
def test_oversized_requests_are_rejected_before_authentication(surface, length):
    client,_,_ = surface
    response = client.post("/control-api/login",content=b"x"*length,headers={"Origin":"https://testserver","Content-Type":"application/json"})
    assert response.status_code == 413
    assert response.json()["error"]["code"] == "REQUEST_TOO_LARGE"


@pytest.mark.parametrize("length,expected", [(MAX_BODY,200),(MAX_BODY+1,413)])
def test_chunked_body_cap_is_independent_of_content_length(length, expected):
    messages = [{"type":"http.request","body":b"x"*(length//2),"more_body":True},{"type":"http.request","body":b"x"*(length-length//2),"more_body":False}]
    emitted,called = [],[]
    async def receive():
        return messages.pop(0) if messages else {"type":"http.disconnect"}
    async def send(message):
        emitted.append(message)
    async def app(scope, receive, send):
        called.append(True)
        message = await receive()
        assert len(message["body"]) == length
        await send({"type":"http.response.start","status":200,"headers":[]})
        await send({"type":"http.response.body","body":b"ok"})
    asyncio.run(SafeResponses(app)({"type":"http","method":"POST","path":"/synthetic","headers":[]},receive,send))
    assert emitted[0]["status"] == expected
    assert bool(called) == (expected == 200)


def test_disconnect_before_request_completion_does_not_invoke_application():
    called = []
    async def app(*args):
        called.append(True)
    async def receive():
        return {"type":"http.disconnect"}
    async def send(message):
        pytest.fail("A disconnected request must not receive a response")
    asyncio.run(SafeResponses(app)({"type":"http"},receive,send))
    assert called == []


def test_static_mount_cannot_follow_symlink_outside_public_assets(surface, tmp_path):
    client,_,web = surface
    private = tmp_path / "synthetic-private.txt"
    private.write_text("synthetic-outside-secret")
    try:
        (web/"assets"/"leak.txt").symlink_to(private)
    except (OSError,NotImplementedError):
        pytest.skip("Symbolic links unavailable on this platform")
    for path in ("/assets/leak.txt", "/assets/%2e%2e/%2e%2e/synthetic-private.txt", "/assets/%2e%2e/index.html"):
        response = client.get(path)
        assert response.status_code == 404
        assert "synthetic-outside-secret" not in response.text


def test_missing_build_is_explicit_and_companion_download_contains_no_config(tmp_path):
    settings = CloudSettings.from_env({"CLOUD_ENV":"development","CLOUD_ALLOWED_HOSTS":"testserver"})
    with TestClient(create_app(settings,web_dist=tmp_path/"missing"),base_url="http://testserver") as client:
        assert client.get("/").status_code == 503
        response = client.get("/downloads/cloud_companion.py")
        assert response.status_code == 200 and 'filename="cloud_companion.py"' in response.headers["content-disposition"]
        assert "device_token" in response.text  # field name, no configured value
        assert "CLOUD_BOOTSTRAP_TOKEN=" not in response.text and "CLOUD_AUTH_KEY=" not in response.text
        assert "strict-transport-security" not in response.headers
