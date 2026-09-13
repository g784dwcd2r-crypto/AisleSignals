from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]


def test_free_blueprint_has_only_reviewed_health_service():
    blueprint = yaml.safe_load((ROOT / "deployment/render-staging.yaml").read_text())
    assert set(blueprint) == {"projects", "previews"}
    assert blueprint["previews"] == {"generation": "off"}
    assert len(blueprint["projects"]) == 1
    project = blueprint["projects"][0]
    assert project["name"] == "AisleSignals"
    assert len(project["environments"]) == 1
    environment = project["environments"][0]
    assert set(environment) == {"name", "services"}
    assert environment["name"] == "Staging"
    assert len(environment["services"]) == 1
    service = environment["services"][0]
    assert service["name"] == "aislesignals-control-staging"
    assert service["type"] == "web" and service["runtime"] == "python"
    assert service["plan"] == "free" and service["region"] == "frankfurt"
    assert service["autoDeployTrigger"] == "off"
    assert service["branch"] == "codex/cloud-staging"
    assert service["repo"] == "https://github.com/g784dwcd2r-crypto/AisleSignals"
    assert service["buildCommand"] == "python -m pip install -r services/cloud/requirements.txt"
    assert service["startCommand"] == "python -m services.cloud"
    assert service["healthCheckPath"] == "/health/live"
    assert "preDeployCommand" not in service and "disk" not in service
    assert service["envVars"] == [
        {"key": "PYTHON_VERSION", "value": "3.12.14"},
        {"key": "CLOUD_ENV", "value": "staging"},
        {"key": "CLOUD_DATABASE_SSLMODE", "value": "require"},
    ]


def test_runtime_requirements_are_isolated_and_pinned():
    requirements = [line for line in (ROOT / "services/cloud/requirements.txt").read_text().splitlines() if line and not line.startswith("#")]
    assert all("==" in line for line in requirements)
    assert not any(name in "\n".join(requirements).lower() for name in ["pillow", "torch", "pytest", "httpx", "-r "])
