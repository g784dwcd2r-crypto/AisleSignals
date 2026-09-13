import asyncio
import importlib
import os
import socket
import subprocess
import sys
import time
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi.testclient import TestClient

from services.cloud.app import create_app
from services.cloud.config import CloudSettings, ConfigurationError
from services.cloud.database import ReadinessProbe, connection_options
from services.cloud.migrate import MigrationError, migrate


def settings(**overrides):
    return CloudSettings.from_env({
        "CLOUD_ENV": "staging",
        "RENDER_EXTERNAL_HOSTNAME": "synthetic-cloud.onrender.com",
        **overrides,
    })


def client_for(config=None, probe=None):
    return TestClient(create_app(config or settings(), probe), base_url="https://synthetic-cloud.onrender.com")


def test_no_database_is_live_but_not_ready_and_does_not_connect():
    checker = AsyncMock(side_effect=AssertionError("must not connect"))
    client = client_for(probe=ReadinessProbe(settings(), checker))
    assert client.get("/health/live").json() == {"status": "alive", "stage": "infrastructure-only"}
    ready = client.get("/health/ready")
    assert ready.status_code == 503
    assert ready.json() == {"status": "unavailable", "code": "DATABASE_NOT_CONFIGURED", "scope": "database-schema-only"}
    checker.assert_not_awaited()
    assert client.get("/").json()["event_sync"] is False


@pytest.mark.parametrize("path", ["/api/runtime", "/api/setup", "/api/admin/users", "/api/auth/login", "/api/events", "/api/evidence", "/api/sync", "/docs", "/redoc", "/openapi.json"])
def test_local_or_unimplemented_routes_absent(path):
    client = client_for()
    assert client.get(path).status_code == 404
    assert client.post(path, json={"token": "synthetic-private-token"}).status_code == 404


@pytest.mark.parametrize("method", ["post", "put", "patch", "delete", "options"])
def test_health_never_accepts_writes_or_cors(method):
    response = getattr(client_for(), method)("/health/live", headers={"Origin": "https://other.invalid"})
    assert response.status_code == 405
    assert "access-control-allow-origin" not in response.headers


@pytest.mark.parametrize("host", ["evil.invalid", "synthetic-cloud.onrender.com.evil.invalid", "localhost", "127.0.0.1"])
def test_host_allowlist_does_not_trust_forwarded_host(host):
    response = client_for().get("/health/live", headers={"Host": host, "X-Forwarded-Host": "synthetic-cloud.onrender.com"})
    assert response.status_code == 400
    assert response.headers["cache-control"] == "no-store"


def test_security_headers_do_not_set_cookies_or_redirect():
    client = client_for()
    for path in ["/", "/health/live", "/health/ready", "/absent", "/health/live/"]:
        response = client.get(path, follow_redirects=False)
        assert response.headers["cache-control"] == "no-store"
        assert response.headers["x-content-type-options"] == "nosniff"
        assert "frame-ancestors 'none'" in response.headers["content-security-policy"]
        assert "set-cookie" not in response.headers
        assert "location" not in response.headers


@pytest.mark.parametrize("env", [
    {}, {"CLOUD_ENV": "production"}, {"CLOUD_ALLOWED_HOSTS": "*"},
    {"CLOUD_ALLOWED_HOSTS": "https://cloud.invalid"}, {"CLOUD_ALLOWED_HOSTS": "cloud.invalid:443"},
    {"CLOUD_ALLOWED_HOSTS": "a..invalid"}, {"CLOUD_ALLOWED_HOSTS": "-a.invalid"},
    {"CLOUD_ENV": "development", "RENDER": "true"},
])
def test_configuration_fails_closed(env):
    with pytest.raises(ConfigurationError):
        CloudSettings.from_env(env)


@pytest.mark.parametrize("url", [
    "sqlite:///private.db", "file:///private.db", "postgresql://u:p@host/",
    "postgresql://u@host/db", "postgresql://u:p@host:bad/db",
    "postgresql://u:p@host/db?sslmode=disable", "postgresql://u:p@host/db?service=secret",
    "postgresql://u:p@host/db?sslmode=require&sslmode=disable", "postgresql://u:p@host/db#secret",
    "postgresql://u:p@host/db\n", "postgresql://u:p@host/db/other",
])
def test_database_configuration_rejects_unsafe_schemes_and_options_without_echo(url):
    with pytest.raises(ConfigurationError) as error:
        settings(DATABASE_URL=url)
    assert url not in str(error.value)


def test_render_settings_bind_and_tls_are_explicit_and_repr_omits_secret():
    config = settings(RENDER="true", PORT="12000", DATABASE_URL="postgresql://u:synthetic-secret@db.internal/staging")
    assert config.bind_host == "0.0.0.0" and config.port == 12000
    assert connection_options(config)["sslmode"] == "require"
    assert "synthetic-secret" not in repr(config)
    with pytest.raises(ConfigurationError):
        settings(CLOUD_DATABASE_SSLMODE="disable")
    config = CloudSettings.from_env({"CLOUD_ENV": "development"})
    assert config.bind_host == "127.0.0.1"
    assert config.allowed_hosts == ("localhost", "127.0.0.1")


@pytest.mark.parametrize("port", ["0", "1023", "65536", "invalid"])
def test_invalid_ports_fail_without_values(port):
    with pytest.raises(ConfigurationError):
        settings(PORT=port)


def test_readiness_exceptions_and_success_are_bounded_cached_and_secret_safe(caplog):
    async def scenario():
        now = [0.0]
        checker = AsyncMock(side_effect=[RuntimeError("postgresql://u:synthetic-secret@private-db/db"), True])
        probe = ReadinessProbe(settings(DATABASE_URL="postgresql://u:p@host/db"), checker, clock=lambda: now[0])
        assert (await probe.check()).code == "DATABASE_UNAVAILABLE"
        assert (await probe.check()).code == "DATABASE_UNAVAILABLE"
        assert checker.await_count == 1
        now[0] = 1.01
        assert (await probe.check()).ready is True
        assert checker.await_count == 2
    asyncio.run(scenario())
    assert "synthetic-secret" not in caplog.text


def test_timeout_cancels_check_and_releases_single_slot():
    async def scenario():
        cancelled = asyncio.Event()
        async def hang(_):
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()
        probe = ReadinessProbe(settings(DATABASE_URL="postgresql://u:p@host/db"), hang, timeout_seconds=0.02)
        start = time.monotonic()
        assert (await probe.check()).code == "DATABASE_TIMEOUT"
        assert time.monotonic() - start < 0.5
        assert cancelled.is_set()
        assert not probe._checking
    asyncio.run(scenario())


def test_concurrent_misses_do_not_queue_or_multiply_connections():
    async def scenario():
        entered, release = asyncio.Event(), asyncio.Event()
        count = 0
        async def checker(_):
            nonlocal count
            count += 1
            entered.set()
            await release.wait()
            return True
        probe = ReadinessProbe(settings(DATABASE_URL="postgresql://u:p@host/db"), checker)
        first = asyncio.create_task(probe.check())
        await entered.wait()
        results = await asyncio.gather(*(probe.check() for _ in range(50)))
        assert all(r.code == "READINESS_BUSY" for r in results)
        assert count == 1
        release.set()
        assert (await first).ready is True
    asyncio.run(scenario())


def test_readiness_caller_cancel_releases_slot_without_caching_success():
    async def scenario():
        entered = asyncio.Event()
        async def checker(_):
            entered.set()
            await asyncio.Event().wait()
        probe = ReadinessProbe(settings(DATABASE_URL="postgresql://u:p@host/db"), checker)
        task = asyncio.create_task(probe.check())
        await entered.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert not probe._checking and probe._cached is None
    asyncio.run(scenario())


def test_unhandled_error_is_opaque(caplog):
    app = create_app(settings())
    @app.get("/synthetic-failure")
    async def synthetic_failure():
        raise RuntimeError("synthetic-private-password")
    response = TestClient(app, base_url="https://synthetic-cloud.onrender.com").get("/synthetic-failure")
    assert response.status_code == 500
    assert response.json()["code"] == "INTERNAL_ERROR"
    assert "synthetic-private-password" not in response.text + caplog.text


def test_migration_missing_database_and_driver_errors_are_opaque(monkeypatch):
    with pytest.raises(MigrationError, match="requires DATABASE_URL"):
        migrate(settings())
    def fail(*args, **kwargs):
        raise RuntimeError("synthetic-private-password")
    monkeypatch.setattr("services.cloud.migrate.psycopg.connect", fail)
    with pytest.raises(MigrationError) as error:
        migrate(settings(DATABASE_URL="postgresql://u:p@host/db"))
    assert "synthetic-private-password" not in str(error.value)


def test_no_local_api_import_in_fresh_process():
    result = subprocess.run([sys.executable, "-c", "from services.cloud.app import create_app; import sys; assert not any(n.startswith('services.api') for n in sys.modules)"], capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr


def test_inherited_pilot_settings_cannot_enable_accounts_or_demo(monkeypatch):
    for key, value in {
        "AISLESIGNALS_MODE": "demo",
        "AISLESIGNALS_DB": "/synthetic-private/local.db",
        "AISLESIGNALS_MODEL_ENDPOINT": "http://127.0.0.1:11435",
        "AISLESIGNALS_ALLOW_REMOTE": "1",
    }.items():
        monkeypatch.setenv(key, value)
    client = client_for()
    assert client.get("/api/runtime").status_code == 404
    assert client.post("/api/setup").status_code == 404
    assert client.get("/health/ready").json()["code"] == "DATABASE_NOT_CONFIGURED"


def test_start_command_real_http_and_graceful_owned_process_exit():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    # Deliberately omit inherited database/config values: this is synthetic health-only staging.
    env = {key: value for key, value in os.environ.items() if not key.startswith(("CLOUD_", "RENDER", "DATABASE_", "PG"))}
    env.update({"CLOUD_ENV": "staging", "RENDER": "true", "RENDER_EXTERNAL_HOSTNAME": "synthetic-cloud.onrender.com", "PORT": str(port)})
    process = subprocess.Popen([sys.executable, "-m", "services.cloud"], env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        deadline = time.monotonic() + 10
        with httpx.Client(base_url=f"http://127.0.0.1:{port}", headers={"Host": "synthetic-cloud.onrender.com"}, trust_env=False, timeout=1) as client:
            while True:
                try:
                    response = client.get("/health/live")
                    break
                except httpx.ConnectError:
                    assert process.poll() is None
                    if time.monotonic() >= deadline:
                        pytest.fail("Owned cloud test server did not start")
                    time.sleep(0.05)
            assert response.status_code == 200
            assert "server" not in response.headers
            assert client.get("/health/ready").status_code == 503
            assert client.post("/api/setup", json={"password": "synthetic-private-password"}).status_code == 404
        process.terminate()
        process.wait(timeout=15)
    finally:
        if process.poll() is None:
            process.kill()
        stdout, stderr = process.communicate(timeout=5)
    assert "synthetic-private-password" not in stdout + stderr
    assert "/api/setup" not in stdout + stderr  # Access logs disabled.


def test_entrypoint_configuration_error_does_not_traceback(monkeypatch, capsys):
    entry = importlib.import_module("services.cloud.__main__")
    monkeypatch.setattr(entry.CloudSettings, "from_env", lambda: (_ for _ in ()).throw(ConfigurationError("Invalid cloud configuration.")))
    assert entry.main() == 1
    assert capsys.readouterr().err == "Invalid cloud configuration.\n"
