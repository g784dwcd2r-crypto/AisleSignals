"""The laptop app owns the sender only for an actual pilot lifecycle."""
import asyncio
import threading
from contextlib import contextmanager
from types import SimpleNamespace
from uuid import uuid4
import pytest
from services.api.evidence_crypto import EvidenceError, PilotDatabaseLock
from services.api.interactions import ACTIVE_JOBS, ACTIVE_LOCK, INFERENCE_SLOT, InteractionService, interaction_lifespan


def fixture(monkeypatch, *, mode='pilot', provider=True, starts=True, stops=True):
    calls = []
    class Worker:
        running = False
        def __init__(self, store, connection, *, source_state):
            calls.append('construct')
        def start(self):
            calls.append('start')
            return starts
        def stop(self, *, timeout):
            calls.append(('stop', threading.get_ident(), timeout))
            return stops
    monkeypatch.setattr('services.api.cloud_delivery.CloudDelivery', Worker)
    service = SimpleNamespace(start=lambda: calls.append('local-start'),
        close=lambda: calls.append('local-stop'),
        hold_cloud_delivery=lambda: calls.append('sender-hold'),
        cloud_delivery_stopped=lambda: calls.append('sender-stopped'),
        cloud_hooks=SimpleNamespace(source_state=lambda: None) if provider else None)
    app = SimpleNamespace(state=SimpleNamespace(interactions=service,
        store=SimpleNamespace(mode=mode), cloud_connection=object()))
    return app, calls


def test_sender_owned_by_lifespan_and_stop_off_loop(monkeypatch):
    app, calls = fixture(monkeypatch)
    async def run():
        loop_thread = threading.get_ident()
        async with interaction_lifespan(app):
            assert calls == ['local-start', 'sender-hold', 'construct', 'start']
            assert app.state.cloud_delivery is not None
        assert calls[-3] == 'local-stop'
        assert calls[-2][0] == 'stop' and calls[-2][1] != loop_thread and calls[-2][2] == 6
        assert calls[-1] == 'sender-stopped'
    asyncio.run(run())


@pytest.mark.parametrize('mode,provider', [('synthetic',True),('pilot',False)])
def test_no_sender_for_synthetic_or_uninstalled_provider(monkeypatch, mode, provider):
    app, calls = fixture(monkeypatch, mode=mode, provider=provider)
    async def run():
        async with interaction_lifespan(app):
            assert not hasattr(app.state, 'cloud_delivery')
    asyncio.run(run())
    assert calls == ['local-start','local-stop','sender-stopped']


@pytest.mark.parametrize('starts,stops,code', [(False,True,'START'),(True,False,'STOP')])
def test_failed_lifecycle_is_not_reported_as_success(monkeypatch, starts, stops, code):
    app, calls = fixture(monkeypatch, starts=starts, stops=stops)
    async def run():
        async with interaction_lifespan(app):
            pass
    with pytest.raises(RuntimeError, match=f'CLOUD_DELIVERY_{code}_FAILED'):
        asyncio.run(run())
    assert 'local-stop' in calls
    if stops:
        assert calls[-1] == 'sender-stopped'
    else:
        assert calls[-1][0] == 'stop'
        assert 'sender-stopped' not in calls


def test_local_shutdown_failure_still_stops_sender(monkeypatch):
    app, calls = fixture(monkeypatch)
    def fail_close():
        raise RuntimeError('SYNTHETIC_LOCAL_CLOSE_FAILURE')
    app.state.interactions.close = fail_close
    async def run():
        async with interaction_lifespan(app):
            pass
    with pytest.raises(RuntimeError, match='SYNTHETIC_LOCAL_CLOSE_FAILURE'):
        asyncio.run(run())
    assert calls[-2][0] == 'stop' and calls[-1] == 'sender-stopped'


@pytest.fixture
def locked_service(tmp_path):
    """Real lifecycle/worker methods and OS lock, without camera/model setup."""
    db = tmp_path / 'synthetic.db'
    service = InteractionService.__new__(InteractionService)
    service.database_lock = PilotDatabaseLock(db)
    service.closed = threading.Event()
    service.janitor = None
    service._retention_pending = False
    service._cloud_delivery_pending = False
    service.cloud_hooks = SimpleNamespace(source_state=lambda *args: 'AVAILABLE')
    yield service, db
    service.cloud_delivery_stopped()
    service.close()
    service.database_lock.close()


def assert_locked(db):
    with pytest.raises(EvidenceError):
        with PilotDatabaseLock(db):
            pytest.fail('The API/backup lock was released while owned work remained.')


def assert_released(db):
    with PilotDatabaseLock(db):
        pass


def run_lifespan(monkeypatch, service, stop, *, constructor_error=False):
    class Worker:
        def __init__(self, *args, **kwargs):
            if constructor_error:
                raise RuntimeError('SYNTHETIC_CONSTRUCTOR_FAILURE')
        def start(self): return True
        def stop(self, *, timeout): return stop()
    monkeypatch.setattr('services.api.cloud_delivery.CloudDelivery', Worker)
    app = SimpleNamespace(state=SimpleNamespace(interactions=service,
        store=SimpleNamespace(mode='pilot'), cloud_connection=object()))
    async def run():
        async with interaction_lifespan(app):
            assert service._cloud_delivery_pending
    asyncio.run(run())


@pytest.mark.parametrize('stops', [True, False])
def test_real_api_backup_lock_remains_held_until_sender_stops(monkeypatch, locked_service, stops):
    service, db = locked_service
    def stop():
        assert service.closed.is_set()
        assert_locked(db)
        return stops
    if stops:
        run_lifespan(monkeypatch, service, stop)
        assert_released(db)
    else:
        with pytest.raises(RuntimeError, match='CLOUD_DELIVERY_STOP_FAILED'):
            run_lifespan(monkeypatch, service, stop)
        assert_locked(db)
        # Repeated local close cannot claim the sender was stopped.
        service.close()
        assert_locked(db)


def test_sender_constructor_failure_does_not_leave_an_unowned_lock(monkeypatch, locked_service):
    service, db = locked_service
    with pytest.raises(RuntimeError, match='SYNTHETIC_CONSTRUCTOR_FAILURE'):
        run_lifespan(monkeypatch, service, lambda: True, constructor_error=True)
    assert_released(db)


def test_sender_stop_exception_retains_real_api_backup_lock(monkeypatch, locked_service):
    service, db = locked_service
    def stop():
        assert_locked(db)
        raise RuntimeError('SYNTHETIC_STOP_FAILURE')
    with pytest.raises(RuntimeError, match='SYNTHETIC_STOP_FAILURE'):
        run_lifespan(monkeypatch, service, stop)
    assert_locked(db)


@pytest.mark.parametrize('worker_finishes_first,stops', [(True, True), (False, True), (True, False), (False, False)])
def test_model_finally_obeys_sender_gate_and_releases_after_both_finish(
        monkeypatch, locked_service, worker_finishes_first, stops):
    service, db = locked_service
    entered, release = threading.Event(), threading.Event()
    @contextmanager
    def transaction():
        entered.set()
        assert release.wait(5)
        yield None
    service.store = SimpleNamespace(transaction=transaction, get=lambda *args: None)
    item_id, cancelled = str(uuid4()), threading.Event()
    assert INFERENCE_SLOT.acquire(blocking=False)
    with ACTIVE_LOCK:
        ACTIVE_JOBS[item_id] = (service, cancelled)
    thread = threading.Thread(target=service.worker, args=(item_id, {}, cancelled))
    thread.start()
    assert entered.wait(2)
    def stop():
        assert cancelled.is_set()
        if worker_finishes_first:
            release.set()
            thread.join(timeout=2)
            assert not thread.is_alive()
        assert_locked(db)
        return stops
    try:
        if stops:
            run_lifespan(monkeypatch, service, stop)
        else:
            with pytest.raises(RuntimeError, match='CLOUD_DELIVERY_STOP_FAILED'):
                run_lifespan(monkeypatch, service, stop)
        if not worker_finishes_first or not stops:
            assert_locked(db)
        release.set()
        thread.join(timeout=2)
        assert not thread.is_alive()
        if stops:
            assert_released(db)
        else:
            assert_locked(db)
    finally:
        release.set()
        thread.join(timeout=2)


def test_retention_that_outlives_join_retains_lock_until_its_work_ends(locked_service):
    service, db = locked_service
    entered, release = threading.Event(), threading.Event()
    class ImmediateRetentionWake(threading.Event):
        first = True
        def wait(self, timeout=None):
            if self.first:
                self.first = False
                return False
            return super().wait(timeout)
    service.closed = ImmediateRetentionWake()
    def cleanup():
        entered.set()
        assert release.wait(5)
    service.cleanup = cleanup
    service.start()
    assert entered.wait(2)
    try:
        service.close()
        assert service.janitor.is_alive()
        assert_locked(db)
    finally:
        release.set()
        service.janitor.join(timeout=2)
    assert not service.janitor.is_alive()
    assert_released(db)
