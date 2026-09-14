"""Owned disposable PostgreSQL, synthetic records and bounded lifecycle faults."""

from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import os
import threading
import time
from uuid import uuid4

import psycopg
import pytest

from control_test_support import bootstrap, disposable_postgres, new_client, pharmacy, reset_database
from test_control_operations import device, review_body
from services.cloud.control_store import ControlStore
from services.cloud import source_maintenance as maintenance


@pytest.fixture(scope="module")
def cluster():
    if os.environ.get("CLOUD_RUN_POSTGRES_TESTS") != "1":
        pytest.skip("Explicit disposable PostgreSQL opt-in required")
    with disposable_postgres() as settings:
        yield settings


@pytest.fixture
def workspace(cluster):
    reset_database(cluster)
    with new_client(cluster) as owner:
        bootstrap(owner, cluster)
        branch = pharmacy(owner, "Synthetic maintenance branch")
        yield cluster, owner, branch


def source(owner, laptop, *, reviewed=False):
    stamp = datetime.now(timezone.utc)
    response = owner.post("/device-api/sync/v1/observations",
                          headers={"Authorization": "Bearer " + laptop["device_token"]}, json={
        "source_event_id": str(uuid4()), "event_code": "POSSIBLE_CONCEALMENT",
        "source_label": "Synthetic source to purge", "occurred_at": stamp.isoformat(),
        "historical": True, "expires_at": (stamp + timedelta(hours=1)).isoformat()})
    assert response.status_code == 201, response.text
    identifier = response.json()["id"]
    if reviewed:
        response = owner.post("/control-api/alerts/" + identifier + "/review", json=review_body(
            title="Synthetic independently authored case", note="Synthetic staff notes must remain"))
        assert response.status_code == 200, response.text
    return identifier


def expire(settings, *, old=False):
    with psycopg.connect(settings.database_url) as conn:
        interval = "32 days" if old else "1 second"
        conn.execute("UPDATE aislesignals_control.alerts SET source_expires_at=clock_timestamp()-%s::interval", (interval,))
        conn.execute("UPDATE aislesignals_control.device_sync_receipts SET expires_at=clock_timestamp()-%s::interval", (interval,))


def wait_until(predicate, timeout=5):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(.01)
    pytest.fail("Synthetic maintenance condition did not complete")


def fast_policy(**values):
    return maintenance.MaintenancePolicy(interval_seconds=.05, max_backoff_seconds=.2, **values)


@pytest.mark.parametrize("values", [{"devices_per_pass": True}, {"devices_per_pass": 101},
    {"rows_per_device": 0}, {"rows_per_device": 501}, {"interval_seconds": 0},
    {"interval_seconds": float("inf")}, {"pass_seconds": float("nan")},
    {"statement_timeout_ms": 0}, {"lock_timeout_ms": 1001},
    {"max_backoff_seconds": 1}])
def test_invalid_policy_is_bounded(values):
    with pytest.raises(ValueError, match="^INVALID_MAINTENANCE_POLICY$"):
        maintenance.MaintenancePolicy(**values)


def test_construction_has_no_database_or_thread_work():
    class UntouchedStore(ControlStore):
        def transaction(self):
            pytest.fail("Construction must not use database")

    worker = maintenance.SourceMaintenance(UntouchedStore(None))
    assert worker.running is False and worker.last_result is None
    assert worker.stop() is True


def test_expired_sources_physically_purge_without_traffic_and_keep_staff_case(workspace):
    settings, owner, branch = workspace
    laptop = device(owner, branch)
    unreviewed = source(owner, laptop)
    reviewed = source(owner, laptop, reviewed=True)
    expire(settings)
    # From here there are no API reads/ingests that can opportunistically purge.
    worker = maintenance.SourceMaintenance(ControlStore(settings), policy=fast_policy())
    assert worker.start() is True
    try:
        wait_until(lambda: worker.last_result is not None and worker.last_result.sources_purged == 2)
    finally:
        assert worker.stop() is True
    with psycopg.connect(settings.database_url) as conn:
        assert conn.execute("SELECT id FROM aislesignals_control.alerts WHERE id=%s", (unreviewed,)).fetchone() is None
        row = conn.execute("SELECT source_label,event_code,title,occurred_at,source_purged,review_note FROM aislesignals_control.alerts WHERE id=%s", (reviewed,)).fetchone()
        assert row == (None, None, None, None, True, "Synthetic staff notes must remain")
        case = conn.execute("SELECT title,notes FROM aislesignals_control.incidents WHERE alert_id=%s", (reviewed,)).fetchone()
        assert case == ("Synthetic independently authored case", "Synthetic staff notes must remain")
        assert conn.execute("SELECT last_seen_at,monitoring_status FROM aislesignals_control.devices").fetchone() == (None, "UNKNOWN")
        assert conn.execute("SELECT count(*) FROM aislesignals_control.device_sync_receipts").fetchone()[0] == 2


def test_bounded_device_pages_and_row_batches_make_fair_progress(workspace):
    settings, owner, branch = workspace
    laptops = [device(owner, branch) for _ in range(3)]
    for laptop in laptops:
        for _ in range(3):
            source(owner, laptop)
    expire(settings, old=True)
    worker = maintenance.SourceMaintenance(ControlStore(settings),
                                            policy=maintenance.MaintenancePolicy(devices_per_pass=1, rows_per_device=1))
    for round_number in range(3):
        for _ in range(3):
            result = worker.run_once()
            assert result.status == "COMPLETED"
            assert (result.devices_seen, result.devices_cleaned, result.sources_purged, result.receipts_pruned) == (1, 1, 1, 1)
        with psycopg.connect(settings.database_url) as conn:
            counts = conn.execute("""SELECT d.id,count(a.id) FROM aislesignals_control.devices d
                LEFT JOIN aislesignals_control.alerts a ON a.device_id=d.id GROUP BY d.id""").fetchall()
            assert [count for _, count in counts] == [2-round_number] * 3
    assert worker.run_once().sources_purged == 0


def test_revoked_and_inactive_disconnected_devices_still_receive_cleanup(workspace):
    settings, owner, branch = workspace
    laptop = device(owner, branch)
    source(owner, laptop)
    assert owner.post("/control-api/devices/" + laptop["device_id"] + "/revoke", json={"expected_version": 1}).status_code == 200
    assert owner.patch("/control-api/pharmacies/" + branch["id"], json={"expected_version": 1, "active": False}).status_code == 200
    expire(settings, old=True)
    worker = maintenance.SourceMaintenance(ControlStore(settings))
    result = worker.run_once()
    assert (result.sources_purged, result.receipts_pruned) == (1, 1)
    with psycopg.connect(settings.database_url) as conn:
        assert conn.execute("SELECT count(*) FROM aislesignals_control.alerts").fetchone()[0] == 0
        assert conn.execute("SELECT revoked_at IS NOT NULL,last_seen_at FROM aislesignals_control.devices").fetchone() == (True, None)


@pytest.mark.parametrize("held", ["organisation", "device"])
def test_contended_org_or_device_is_skipped_then_revisited(workspace, held):
    settings, owner, branch = workspace
    laptop = device(owner, branch)
    source(owner, laptop)
    expire(settings)
    worker = maintenance.SourceMaintenance(ControlStore(settings))
    with psycopg.connect(settings.database_url) as lock:
        if held == "organisation":
            lock.execute("SELECT id FROM aislesignals_control.organisations WHERE id=%s FOR UPDATE", (branch["organisation_id"],))
        else:
            lock.execute("SELECT id FROM aislesignals_control.devices WHERE id=%s FOR UPDATE", (laptop["device_id"],))
        result = worker.run_once()
        assert (result.devices_busy, result.sources_purged) == (1, 0)
    assert worker.run_once().sources_purged == 1


def test_owned_advisory_lock_prevents_overlapping_instances_and_releases(workspace, monkeypatch):
    settings, owner, branch = workspace
    laptop = device(owner, branch)
    source(owner, laptop)
    expire(settings)
    first, second = maintenance.SourceMaintenance(ControlStore(settings)), maintenance.SourceMaintenance(ControlStore(settings))
    entered, release = threading.Event(), threading.Event()
    original = maintenance.cleanup_device_sources

    def gated(conn, row, *, limit):
        entered.set()
        assert release.wait(4)
        return original(conn, row, limit=limit)

    monkeypatch.setattr(maintenance, "cleanup_device_sources", gated)
    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(first.run_once)
        try:
            assert entered.wait(3)
            assert first.run_once().status == "BUSY"
            assert second.run_once().status == "BUSY"
        finally:
            release.set()
        assert pending.result(timeout=3).sources_purged == 1
    assert second.run_once().status == "COMPLETED"


def test_alert_lock_timeout_rolls_back_device_then_other_devices_progress(workspace):
    settings, owner, branch = workspace
    laptops = [device(owner, branch), device(owner, branch)]
    identifiers = [source(owner, laptop) for laptop in laptops]
    expire(settings)
    worker = maintenance.SourceMaintenance(ControlStore(settings), policy=maintenance.MaintenancePolicy(lock_timeout_ms=20))
    with psycopg.connect(settings.database_url) as lock:
        lock.execute("SELECT id FROM aislesignals_control.alerts WHERE id=%s FOR UPDATE", (identifiers[0],))
        result = worker.run_once()
        assert result.status == "PARTIAL" and result.error_code == "DEVICE_RETRY"
        assert (result.devices_busy, result.sources_purged) == (1, 1)
    assert worker.run_once().sources_purged == 1


def test_transient_database_failure_recovers_with_bounded_secret_free_backoff(workspace):
    settings, owner, branch = workspace
    source(owner, device(owner, branch))
    expire(settings)

    class FlakyStore(ControlStore):
        attempts = 0

        @contextmanager
        def transaction(self):
            self.attempts += 1
            if self.attempts == 1:
                raise RuntimeError("synthetic-secret-database-url")
            with super().transaction() as conn:
                yield conn

    store = FlakyStore(settings)
    worker = maintenance.SourceMaintenance(store, policy=fast_policy())
    assert worker.start() is True and worker.start() is False
    try:
        wait_until(lambda: worker.last_result is not None and worker.last_result.status == "UNAVAILABLE"
                   and worker.retry_delay_seconds > 0)
        assert worker.last_result.error_code == "MAINTENANCE_FAILED"
        assert "synthetic-secret" not in repr(worker.last_result)
        assert .05 <= worker.retry_delay_seconds <= .2
        wait_until(lambda: worker.last_result is not None and worker.last_result.sources_purged == 1)
        assert worker.retry_delay_seconds == .05
    finally:
        assert worker.stop() is True
    attempts = store.attempts
    assert worker.running is False and worker.stop() is True
    time.sleep(.1)
    assert store.attempts == attempts


def test_stop_cancels_only_owned_query_rolls_back_and_allows_clean_restart(workspace, monkeypatch):
    settings, owner, branch = workspace
    source(owner, device(owner, branch))
    expire(settings)
    entered = threading.Event()
    original = maintenance.cleanup_device_sources

    def slow(conn, row, *, limit):
        original(conn, row, limit=limit)
        entered.set()
        conn.execute("SELECT pg_sleep(10)")
        return {"sources_purged": 1, "receipts_pruned": 0}

    monkeypatch.setattr(maintenance, "cleanup_device_sources", slow)
    worker = maintenance.SourceMaintenance(ControlStore(settings), policy=fast_policy(statement_timeout_ms=5000))
    assert worker.start()
    try:
        assert entered.wait(3)
        assert worker.stop(timeout=3) is True
        assert worker.last_result.status == "CANCELLED"
        with psycopg.connect(settings.database_url) as conn:
            assert conn.execute("SELECT count(*) FROM aislesignals_control.alerts").fetchone()[0] == 1
            assert conn.execute("SELECT pg_try_advisory_xact_lock(%s)", (maintenance.OWNER_LOCK,)).fetchone()[0] is True
        monkeypatch.setattr(maintenance, "cleanup_device_sources", original)
        assert worker.start() is True
        wait_until(lambda: worker.last_result is not None and worker.last_result.sources_purged == 1)
    finally:
        assert worker.stop() is True


def test_missing_configuration_is_an_opaque_retry_without_work(cluster):
    worker = maintenance.SourceMaintenance(ControlStore(replace(cluster, database_url=None)))
    result = worker.run_once()
    assert result == maintenance.PassResult("UNAVAILABLE", error_code="DATABASE_UNAVAILABLE")


def test_statement_timeout_preserves_metadata_and_returns_bounded_retry(workspace, monkeypatch):
    settings, owner, branch = workspace
    source(owner, device(owner, branch))
    expire(settings)

    def timed_out(conn, row, *, limit):
        conn.execute("SELECT pg_sleep(10)")
        pytest.fail("PostgreSQL statement limit must cancel this query")

    monkeypatch.setattr(maintenance, "cleanup_device_sources", timed_out)
    worker = maintenance.SourceMaintenance(ControlStore(settings),
        policy=maintenance.MaintenancePolicy(statement_timeout_ms=30, lock_timeout_ms=20))
    result = worker.run_once()
    assert result.status == "PARTIAL" and result.error_code == "DEVICE_RETRY"
    assert result.sources_purged == 0
    with psycopg.connect(settings.database_url) as conn:
        assert conn.execute("SELECT count(*) FROM aislesignals_control.alerts").fetchone()[0] == 1
        assert conn.execute("SELECT pg_try_advisory_xact_lock(%s)", (maintenance.OWNER_LOCK,)).fetchone()[0] is True
