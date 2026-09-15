import json
from unittest.mock import Mock

import pytest

from deployment.check_cloud_health import HealthFailure, check


SHA = "a" * 40


class Response:
    status = 200

    def __init__(self, value):
        self.value = value

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return None

    def read(self, size):
        return json.dumps(self.value).encode()


def opener(*values):
    return Mock(open=Mock(side_effect=[Response(value) for value in values]))


def test_monitor_requires_all_three_exact_production_contracts():
    client = opener(
        {"status": "alive", "stage": "management-console"},
        {"status": "ready", "code": "READY", "scope": "database-schema-only"},
        {"environment": "production", "release_sha": SHA},
    )
    assert check("https://control.example.ie", SHA, client)["status"] == "PASS"
    assert client.open.call_count == 3


@pytest.mark.parametrize("origin", ["http://example.ie", "https://user@example.ie", "https://example.ie/path"])
def test_monitor_rejects_unsafe_origin(origin):
    with pytest.raises(HealthFailure):
        check(origin)


def test_monitor_rejects_staging_or_wrong_release():
    client = opener(
        {"status": "alive", "stage": "management-console"},
        {"status": "ready", "code": "READY", "scope": "database-schema-only"},
        {"environment": "staging", "release_sha": SHA},
    )
    with pytest.raises(HealthFailure, match="release identity"):
        check("https://control.example.ie", SHA, client)
