import os
from pathlib import Path
import subprocess
import sys
from unittest.mock import AsyncMock, Mock

import pytest

from services.cloud import start_with_schema as startup
from services.cloud.config import CloudSettings
from services.cloud.database import Readiness


@pytest.fixture
def dependencies(monkeypatch):
    settings = CloudSettings.from_env({
        "CLOUD_ENV": "staging",
        "RENDER_EXTERNAL_HOSTNAME": "synthetic-cloud.onrender.com",
        "DATABASE_URL": "postgresql://synthetic:synthetic-password@db.invalid/staging",
    })
    config = Mock(return_value=settings)
    migrate = Mock()
    check = AsyncMock(return_value=Readiness(True, "READY"))
    probe = Mock(return_value=Mock(check=check))
    serve = Mock(return_value=0)
    monkeypatch.setattr(startup.CloudSettings, "from_env", config)
    monkeypatch.setattr(startup, "migrate", migrate)
    monkeypatch.setattr(startup, "ReadinessProbe", probe)
    monkeypatch.setattr(startup.server, "main", serve)
    return settings, config, migrate, probe, check, serve


def test_explicit_staging_start_migrates_then_verifies_then_serves(dependencies, capsys):
    settings, _, migrate, probe, check, serve = dependencies
    steps = []
    migrate.side_effect = lambda actual: steps.append(("migrate", actual))
    async def verified():
        steps.append(("verify", settings))
        return Readiness(True, "READY")
    check.side_effect = verified
    serve.side_effect = lambda: steps.append(("serve", settings)) or 0
    assert startup.main() == 0
    assert steps == [("migrate", settings), ("verify", settings), ("serve", settings)]
    probe.assert_called_once_with(settings)
    output = capsys.readouterr()
    assert "synchronisation remain unavailable" in output.out
    assert "synthetic-password" not in output.out + output.err


@pytest.mark.parametrize("environment", ["development", "production"])
def test_non_staging_mode_never_migrates_or_serves(dependencies, environment, capsys):
    _, config, migrate, probe, _, serve = dependencies
    config.return_value = Mock(environment=environment)
    assert startup.main() == 1
    assert "STAGING_ONLY" in capsys.readouterr().err
    migrate.assert_not_called()
    probe.assert_not_called()
    serve.assert_not_called()


def test_missing_database_never_migrates_or_serves(dependencies, capsys):
    _, config, migrate, probe, _, serve = dependencies
    config.return_value = Mock(environment="staging", database_url=None)
    assert startup.main() == 1
    assert "DATABASE_REQUIRED" in capsys.readouterr().err
    migrate.assert_not_called()
    probe.assert_not_called()
    serve.assert_not_called()


@pytest.mark.parametrize("failure_at,code", [("config", "CONFIGURATION_INVALID"), ("migration", "MIGRATION_FAILED"), ("readiness", "DATABASE_NOT_READY")])
def test_errors_do_not_leak_or_start_server(dependencies, failure_at, code, capsys):
    _, config, migrate, probe, check, serve = dependencies
    failing = {"config": config, "migration": migrate, "readiness": check}[failure_at]
    failing.side_effect = RuntimeError("synthetic-private-password postgres://private-host/db")
    assert startup.main() == 1
    output = capsys.readouterr()
    assert code in output.err
    assert "synthetic-private-password" not in output.err + output.out
    assert "private-host" not in output.err + output.out
    assert "Traceback" not in output.err
    serve.assert_not_called()
    if failure_at == "config":
        migrate.assert_not_called()
    if failure_at != "readiness":
        probe.assert_not_called()


@pytest.mark.parametrize("code", ["DATABASE_SCHEMA_UNAVAILABLE", "DATABASE_UNAVAILABLE", "DATABASE_TIMEOUT", "READINESS_BUSY"])
def test_post_migration_database_failure_does_not_become_liveness_success(dependencies, code, capsys):
    _, _, migrate, _, check, serve = dependencies
    check.return_value = Readiness(False, code)
    assert startup.main() == 1
    migrate.assert_called_once()
    check.assert_awaited_once()
    serve.assert_not_called()
    assert "DATABASE_NOT_READY" in capsys.readouterr().err


def test_server_exit_code_is_preserved(dependencies):
    *_, serve = dependencies
    serve.return_value = 3
    assert startup.main() == 3


def test_default_entrypoint_does_not_migrate(monkeypatch):
    from services.cloud import __main__ as default_server
    settings = CloudSettings.from_env({"CLOUD_ENV": "development"})
    monkeypatch.setattr(default_server.CloudSettings, "from_env", lambda: settings)
    run = Mock()
    monkeypatch.setattr(default_server.uvicorn, "run", run)
    migrate = Mock(side_effect=AssertionError("Default start must not migrate"))
    monkeypatch.setattr(startup, "migrate", migrate)
    assert default_server.main() == 0
    run.assert_called_once()
    migrate.assert_not_called()


def test_real_module_missing_database_exits_without_listener_or_traceback():
    # No inherited DATABASE_URL, PG settings, pilot configuration or credentials.
    env = {key: value for key, value in os.environ.items() if key in {"PATH", "SYSTEMROOT", "WINDIR", "TEMP", "TMP"}}
    env.update({"CLOUD_ENV": "staging", "CLOUD_ALLOWED_HOSTS": "synthetic-cloud.invalid"})
    result = subprocess.run(
        [sys.executable, "-m", "services.cloud.start_with_schema"],
        cwd=Path(__file__).resolve().parents[2], env=env,
        capture_output=True, text=True, timeout=10,
    )
    assert result.returncode == 1
    assert result.stdout == ""
    assert result.stderr == "Cloud staging startup stopped: DATABASE_REQUIRED. Server was not started.\n"
