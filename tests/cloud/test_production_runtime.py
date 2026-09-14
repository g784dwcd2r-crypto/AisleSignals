import os
from pathlib import Path
import subprocess
import sys
from unittest.mock import AsyncMock, Mock

from fastapi import FastAPI
from fastapi.responses import Response
from fastapi.testclient import TestClient
import pytest
from starlette.requests import Request
import yaml

from services.cloud import prepare_release, start_checked
from services.cloud.app import SafeResponses
from services.cloud.config import CloudSettings, ConfigurationError
from services.cloud.control_auth import _session_response
from services.cloud.control_store import COOKIE_NAME, ControlError, cookie_name, require_origin
from services.cloud.database import Readiness


ROOT = Path(__file__).resolve().parents[2]
AUTH_KEY = "eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHg="


def production_settings(**overrides):
    return CloudSettings.from_env({
        "CLOUD_ENV": "production",
        "CLOUD_ALLOWED_HOSTS": "control.synthetic.invalid",
        "DATABASE_URL": "postgresql://user:synthetic-password@db.synthetic.invalid/aislesignals",
        "CLOUD_AUTH_KEY": AUTH_KEY,
        **overrides,
    })


def test_production_configuration_requires_host_database_password_auth_key_and_tls():
    settings = production_settings(RENDER="true")
    assert settings.environment == "production" and settings.bind_host == "0.0.0.0"
    assert settings.database_sslmode == "require" and cookie_name(settings) == COOKIE_NAME
    base = {
        "CLOUD_ENV": "production",
        "CLOUD_ALLOWED_HOSTS": "control.synthetic.invalid",
        "DATABASE_URL": "postgresql://user:synthetic-password@db.synthetic.invalid/aislesignals",
        "CLOUD_AUTH_KEY": AUTH_KEY,
    }
    for missing in ("CLOUD_ALLOWED_HOSTS", "DATABASE_URL", "CLOUD_AUTH_KEY"):
        with pytest.raises(ConfigurationError):
            CloudSettings.from_env({key: value for key, value in base.items() if key != missing})
    for changes in (
        {"CLOUD_DATABASE_SSLMODE": "disable"},
        {"DATABASE_URL": "postgresql://user@db.synthetic.invalid/aislesignals"},
        {"DATABASE_URL": "postgresql://user:password@db.synthetic.invalid/aislesignals?sslmode=disable"},
        {"RENDER": "true", "CLOUD_ENV": "development"},
    ):
        with pytest.raises(ConfigurationError):
            production_settings(**changes)


def test_production_http_boundary_requires_https_hsts_and_secure_host_cookie():
    settings = production_settings()
    scope = {
        "type": "http", "method": "POST", "scheme": "http", "path": "/",
        "headers": [(b"host", b"control.synthetic.invalid"),
                    (b"origin", b"http://control.synthetic.invalid")],
        "server": ("control.synthetic.invalid", 80), "client": ("127.0.0.1", 1),
        "query_string": b"", "root_path": "", "http_version": "1.1",
    }
    with pytest.raises(ControlError, match="ORIGIN_REQUIRED"):
        require_origin(Request(scope), settings)
    app = FastAPI()
    app.add_api_route("/health", lambda: {"ok": True}, methods=["GET"])
    response = TestClient(SafeResponses(app, secure=True)).get("/health")
    assert response.headers["strict-transport-security"] == "max-age=31536000"
    cookie_response = Response()
    _session_response(settings, cookie_response, "x" * 43, {})
    cookie = cookie_response.headers["set-cookie"]
    assert cookie.startswith(f"{COOKIE_NAME}=") and "Secure" in cookie and "HttpOnly" in cookie


def test_production_server_checks_schema_without_migrating(monkeypatch, capsys):
    settings = production_settings()
    steps = []
    check = AsyncMock(side_effect=lambda: steps.append("readiness") or Readiness(True, "READY"))
    monkeypatch.setattr(start_checked.CloudSettings, "from_env", lambda: settings)
    monkeypatch.setattr(start_checked, "ReadinessProbe", Mock(return_value=Mock(check=check)))
    monkeypatch.setattr(start_checked.server, "main", lambda: steps.append("serve") or 0)
    from services.cloud import migrate as migration_module
    migration = Mock(side_effect=AssertionError("production server must not migrate"))
    monkeypatch.setattr(migration_module, "migrate", migration)
    assert start_checked.main() == 0
    assert steps == ["readiness", "serve"] and migration.call_count == 0
    assert "No migration was run" in capsys.readouterr().out


@pytest.mark.parametrize("environment", ["staging", "development"])
def test_production_server_rejects_other_environments(monkeypatch, capsys, environment):
    settings = Mock(environment=environment)
    monkeypatch.setattr(start_checked.CloudSettings, "from_env", lambda: settings)
    serve = Mock()
    monkeypatch.setattr(start_checked.server, "main", serve)
    assert start_checked.main() == 1
    assert "PRODUCTION_ONLY" in capsys.readouterr().err
    serve.assert_not_called()


@pytest.mark.parametrize("result", [Readiness(False, "DATABASE_UNAVAILABLE"), RuntimeError("private-database")])
def test_production_server_fails_before_listening(monkeypatch, capsys, result):
    settings = production_settings()
    monkeypatch.setattr(start_checked.CloudSettings, "from_env", lambda: settings)
    check = AsyncMock(side_effect=result if isinstance(result, Exception) else None,
                      return_value=None if isinstance(result, Exception) else result)
    monkeypatch.setattr(start_checked, "ReadinessProbe", Mock(return_value=Mock(check=check)))
    serve = Mock()
    monkeypatch.setattr(start_checked.server, "main", serve)
    assert start_checked.main() == 1 and "DATABASE_NOT_READY" in capsys.readouterr().err
    serve.assert_not_called()


def test_predeploy_migrates_then_uses_fresh_readiness_without_serving(monkeypatch, capsys):
    settings, steps = production_settings(), []
    monkeypatch.setattr(prepare_release.CloudSettings, "from_env", lambda: settings)
    monkeypatch.setattr(prepare_release, "migrate", lambda actual: steps.append(("migrate", actual)))
    check = AsyncMock(side_effect=lambda: steps.append(("readiness", settings)) or Readiness(True, "READY"))
    monkeypatch.setattr(prepare_release, "ReadinessProbe", Mock(return_value=Mock(check=check)))
    assert prepare_release.main() == 0
    assert steps == [("migrate", settings), ("readiness", settings)]
    assert "migrated and verified" in capsys.readouterr().out


@pytest.mark.parametrize("failure_at,code", [
    ("config", "CONFIGURATION_INVALID"), ("migration", "MIGRATION_FAILED"),
    ("readiness", "DATABASE_NOT_READY"),
])
def test_predeploy_failures_are_opaque_and_stop(monkeypatch, capsys, failure_at, code):
    settings = production_settings()
    config, migration = Mock(return_value=settings), Mock()
    check = AsyncMock(return_value=Readiness(True, "READY"))
    monkeypatch.setattr(prepare_release.CloudSettings, "from_env", config)
    monkeypatch.setattr(prepare_release, "migrate", migration)
    monkeypatch.setattr(prepare_release, "ReadinessProbe", Mock(return_value=Mock(check=check)))
    {"config": config, "migration": migration, "readiness": check}[failure_at].side_effect = RuntimeError(
        "postgresql://user:private-password@private-host/database")
    assert prepare_release.main() == 1
    output = capsys.readouterr()
    assert code in output.err and "private-password" not in output.err + output.out


def test_render_production_blueprint_is_manual_paid_and_separate():
    blueprint = yaml.safe_load((ROOT / "deployment/render-production.yaml").read_text())
    environment = blueprint["projects"][0]["environments"][0]
    service, database = environment["services"][0], environment["databases"][0]
    assert environment["name"] == "Production"
    assert service["name"] == "aislesignals-control-production"
    assert service["plan"] != "free" and database["plan"] != "free"
    assert service["branch"] == "main" and service["autoDeployTrigger"] == "off"
    assert service["preDeployCommand"] == "python -m services.cloud.prepare_release"
    assert service["startCommand"] == "python -m services.cloud.start_checked"
    assert service["healthCheckPath"] == "/health/ready"
    assert database["name"] == "aislesignals-postgres-production"
    assert database["name"] != "aislesignals-postgres-staging" and database["ipAllowList"] == []
    variables = {item["key"]: item for item in service["envVars"]}
    assert variables["CLOUD_ENV"]["value"] == "production"
    assert variables["CLOUD_DATABASE_SSLMODE"]["value"] == "require"
    assert variables["CLOUD_EVIDENCE_MODE"]["value"] == "METADATA_ONLY"
    assert variables["CLOUD_AUTH_KEY"] == {"key": "CLOUD_AUTH_KEY", "sync": False}
    assert variables["CLOUD_BOOTSTRAP_TOKEN"] == {"key": "CLOUD_BOOTSTRAP_TOKEN", "sync": False}


def test_real_production_entrypoints_reject_missing_secrets_without_tracebacks():
    environment = {key: value for key, value in os.environ.items()
                   if key in {"PATH", "SYSTEMROOT", "WINDIR", "TEMP", "TMP"}}
    environment.update({"CLOUD_ENV": "production", "CLOUD_ALLOWED_HOSTS": "control.synthetic.invalid"})
    for module in ("services.cloud.prepare_release", "services.cloud.start_checked"):
        result = subprocess.run([sys.executable, "-m", module], cwd=ROOT, env=environment,
                                capture_output=True, text=True, timeout=10)
        assert result.returncode == 1 and result.stdout == ""
        assert "CONFIGURATION_INVALID" in result.stderr and "Traceback" not in result.stderr
