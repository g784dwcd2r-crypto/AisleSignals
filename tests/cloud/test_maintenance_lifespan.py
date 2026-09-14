"""Actual app lifespan ownership without inherited database or worker threads."""

import threading

from fastapi.testclient import TestClient
import pytest

from services.cloud.app import create_app
from services.cloud.config import CloudSettings
from services.cloud.source_maintenance import SourceMaintenance
from test_source_maintenance import cluster, workspace, source, expire, fast_policy, wait_until
from test_control_operations import device


def fixture_app(*, start=True, stop=True):
    calls = []

    class Worker:
        def __init__(self, store):
            calls.append(('construct', store))

        def start(self):
            calls.append(('start', threading.get_ident()))
            if isinstance(start, Exception):
                raise start
            return start

        def stop(self, *, timeout):
            calls.append(('stop', threading.get_ident(), timeout))
            return stop

    app = create_app(CloudSettings.from_env({'CLOUD_ENV': 'development', 'CLOUD_ALLOWED_HOSTS': 'testserver'}),
                     maintenance_factory=Worker)
    assert calls == []  # Import/construction must not start background work.
    return app, calls


def test_lifespan_owns_one_worker_and_joins_off_event_loop():
    app, calls = fixture_app()
    with TestClient(app) as client:
        assert client.get('/health/live').status_code == 200
        assert [row[0] for row in calls] == ['construct', 'start']
        assert calls[0][1] is app.state.control_store
    assert [row[0] for row in calls] == ['construct', 'start', 'stop']
    assert calls[1][1] != calls[2][1] and calls[2][2] == 6


@pytest.mark.parametrize('start', [False, RuntimeError('SYNTHETIC_START_FAILURE')], ids=['refused', 'exception'])
def test_failed_start_still_stops_any_partially_started_worker(start):
    app, calls = fixture_app(start=start)
    with pytest.raises(RuntimeError):
        with TestClient(app):
            pytest.fail('A failed worker start cannot report a started application.')
    assert [row[0] for row in calls] == ['construct', 'start', 'stop']


def test_shutdown_failure_is_not_reported_as_success():
    app, calls = fixture_app(stop=False)
    with pytest.raises(RuntimeError, match='SOURCE_MAINTENANCE_STOP_FAILED'):
        with TestClient(app):
            pass
    assert calls[-1][0] == 'stop'


def test_actual_app_lifespan_purges_sources_without_new_device_traffic(workspace):
    import psycopg
    settings, owner, branch = workspace
    laptop = device(owner, branch)
    identifier = source(owner, laptop, reviewed=True)
    expire(settings)
    app = create_app(settings, maintenance_factory=lambda store: SourceMaintenance(store, policy=fast_policy()))

    def purged():
        with psycopg.connect(settings.database_url) as conn:
            return conn.execute('SELECT source_purged FROM aislesignals_control.alerts WHERE id=%s', (identifier,)).fetchone()[0]

    with TestClient(app):
        wait_until(purged)
        assert app.state.source_maintenance.running
        cases = owner.get('/control-api/incidents').json()['items']
        assert len(cases) == 1 and cases[0]['source_unavailable'] is True
        assert cases[0]['notes'] == 'Synthetic staff notes must remain'
    assert app.state.source_maintenance.running is False
