"""Owned fake subprocesses exercise recovery; no client camera/model is used."""
from dataclasses import replace
import hashlib
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import textwrap
import threading
import time

import pytest

from scripts import pilot_preflight as pf
from scripts import run_pilot as launcher


@pytest.fixture
def config(tmp_path):
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts/local-vision.py").write_text(
        "import hashlib\nWEIGHTS={'synthetic.gguf': '" + hashlib.sha256(b"synthetic test weights").hexdigest() + "'}\n"
        "def digest(path): return hashlib.sha256(path.read_bytes()).hexdigest()\n")
    (tmp_path / "apps/web/dist").mkdir(parents=True)
    (tmp_path / "apps/web/dist/index.html").write_text("synthetic deployment fixture")
    data = tmp_path / ".local/pilot"
    data.mkdir(parents=True)
    return replace(pf.load_config(root=tmp_path), python_executable=Path(sys.executable), data_dir=data, vision_enabled=False)


def free_port():
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def fake_server(tmp_path, *, ready=True, mode="pilot", kind="api"):
    script = tmp_path / (f"fake_ready_{kind}.py" if ready else f"fake_unready_{kind}.py")
    body = json.dumps({"status": "ok", "mode": mode} if kind == "api" else {"data": [{"id": "qwen3-vl:4b"}]})
    script.write_text(textwrap.dedent(f"""
        import json, sys, time
        from pathlib import Path
        status = Path(__file__).with_suffix('.status.json')
        status.write_text(json.dumps({{'stage':'imports'}}))
        from http.server import BaseHTTPRequestHandler, HTTPServer
        from socketserver import TCPServer
        import socket
        def unexpected_lookup(*_):
            raise AssertionError('Synthetic loopback fixture must not use hostname resolution')
        socket.getfqdn = unexpected_lookup
        class LocalHTTPServer(HTTPServer):
            def server_bind(self):
                # HTTPServer normally reverse-resolves its bound address here.
                # The actual uvicorn child needs no resolver for loopback, and
                # a hosted runner's DNS must not delay this synthetic fixture.
                TCPServer.server_bind(self)
                self.server_name = '127.0.0.1'
                self.server_port = self.server_address[1]
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                fault = Path(__file__).with_suffix('.fault')
                behavior = fault.read_text() if fault.exists() else ''
                if behavior in ('hang-once', 'transient'):
                    fault.unlink()
                if behavior in ('hang-once', 'hang'):
                    time.sleep(600)
                body = {body!r}.encode()
                self.send_response(503 if behavior == 'transient' else {200 if ready else 503})
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            def log_message(self, *_): pass
        status.write_text(json.dumps({{'stage':'binding'}}))
        server = LocalHTTPServer(('127.0.0.1', int(sys.argv[1])), Handler)
        status.write_text(json.dumps({{'stage':'listening'}}))
        server.serve_forever()
    """))
    port = free_port()
    # This is a subprocess integration test, not a two-second startup promise.
    # Deadline/cancellation tests below set their own deliberately short bounds.
    return launcher.OwnedService(kind, [sys.executable, str(script), str(port)], port,
                                 "/api/health" if kind == "api" else "/v1/models", 10)


def fake_startup_diagnostic(service):
    path = Path(service.command[1]).with_suffix('.status.json')
    return path.read_text() if path.exists() else 'Synthetic child did not reach its first Python statement'


@pytest.mark.parametrize("values", [
    {"api_port": True}, {"api_port": 80}, {"vision_port": 8765}, {"restart_limit": 99},
    {"model_start_timeout_seconds": 999}, {"vision_enabled": "false"},
    {"camera_password": "PRIVATE_SECRET"}, {"schema_version": 2}, {"data_dir": "\nPRIVATE_PATH"},
])
def test_invalid_configuration_has_safe_error(tmp_path, values):
    path = tmp_path / "config.json"
    path.write_text(json.dumps(values))
    with pytest.raises(pf.ConfigurationError) as caught:
        pf.load_config(path, root=tmp_path)
    assert "PRIVATE" not in str(caught.value)


def test_preflight_omits_identity_secrets_and_preserves_db(config):
    config.database.write_bytes(b"unchanged existing database")
    report = pf.preflight(config)
    encoded = json.dumps(report)
    assert str(config.root) not in encoded
    assert "hostname" not in encoded
    assert config.database.read_bytes() == b"unchanged existing database"
    assert report["client_rollout_accepted"] is False
    for check in ("actual_cctv", "physical_audio", "resume_rearm", "platform_acceptance", "detection_accuracy"):
        assert next(c for c in report["checks"] if c["id"] == check)["status"] == "NOT_RUN"


def test_occupied_port_blocks_without_affecting_owner(config):
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        config = replace(config, api_port=listener.getsockname()[1])
        assert not pf.port_available(config.api_port)
        report = pf.preflight(config)
        assert report["launch_status"] == "FAIL"
        assert listener.fileno() >= 0


def test_unready_model_is_degraded_not_falsely_ready(config):
    config = replace(config, vision_enabled=True, vision_server=None, api_port=free_port(), vision_port=free_port())
    report = pf.preflight(config)
    assert report["launch_status"] == "DEGRADED"
    assert next(c for c in report["checks"] if c["id"] == "model_weight_1")["status"] == "FAIL"


@pytest.mark.skipif(os.name == "nt", reason="Symlink creation requires platform-specific Windows privileges")
def test_private_symlink_refused_without_mutating_target(config, tmp_path):
    target = tmp_path / "untouched"
    target.mkdir()
    linked = tmp_path / "linked"
    linked.symlink_to(target, target_is_directory=True)
    with pytest.raises(pf.ConfigurationError):
        launcher.prepare_private_directory(linked)
    with pytest.raises(pf.ConfigurationError):
        pf.write_report(linked / "report.json", {})
    assert list(target.iterdir()) == []


def test_token_reused_never_in_commands_or_environment(config):
    path = launcher.ensure_token(config)
    original = path.read_text()
    assert launcher.ensure_token(config).read_text() == original
    env = launcher.service_environment(config, path)
    assert env["AISLESIGNALS_VISION_TOKEN_FILE"] == str(path)
    assert original.strip() not in json.dumps(env)
    assert env["AISLESIGNALS_MODE"] == "pilot"
    if os.name != "nt":
        assert path.stat().st_mode & 0o777 == 0o600


def test_environment_drops_inherited_provider_and_demo_secrets(config, monkeypatch):
    monkeypatch.setenv("AISLESIGNALS_VISION_TOKEN", "should-not-inherit")
    monkeypatch.setenv("AISLESIGNALS_MODE", "synthetic")
    env = launcher.service_environment(config, None)
    assert "AISLESIGNALS_VISION_TOKEN" not in env
    assert env["AISLESIGNALS_MODE"] == "pilot"


def test_setup_capability_never_issued_to_redirected_output(config, monkeypatch):
    from services.api import pilot_admin
    monkeypatch.setattr(launcher, "local_json", lambda *_: {"available": True})
    monkeypatch.setattr(launcher.sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(launcher.sys.stdout, "isatty", lambda: False)
    monkeypatch.setattr(pilot_admin, "issue_setup_token", lambda *_: pytest.fail("Secret issued into redirected logs"))
    messages = []
    launcher.present_first_owner_setup(config, reporter=messages.append)
    assert "local terminal" in messages[0]


def test_setup_capability_only_shown_for_empty_interactive_workspace(config, monkeypatch):
    from services.api import pilot_admin
    monkeypatch.setattr(launcher, "local_json", lambda *_: {"available": True})
    monkeypatch.setattr(launcher.sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(launcher.sys.stdout, "isatty", lambda: True)
    monkeypatch.setattr(pilot_admin, "issue_setup_token", lambda *_: {"token": "SYNTHETIC_SETUP_TEST"})
    messages = []
    launcher.present_first_owner_setup(config, reporter=messages.append)
    assert messages[-1] == "SYNTHETIC_SETUP_TEST"
    monkeypatch.setattr(launcher, "local_json", lambda *_: {"available": False})
    messages.clear()
    launcher.present_first_owner_setup(config, reporter=messages.append)
    assert not messages


def test_source_launcher_setup_imports_without_repository_on_python_path(tmp_path):
    code = """
import runpy, sys
from pathlib import Path
from dataclasses import replace
script=Path(sys.argv[1])
sys.path.insert(0, str(script.parent))
scope=runpy.run_path(str(script),run_name='source_launcher_test')
function=scope['present_first_owner_setup']
function.__globals__['local_json']=lambda *_: {'available':True}
sys.stdin.isatty=lambda:True
sys.stdout.isatty=lambda:True
config=replace(scope['load_config'](root=script.parent.parent),data_dir=Path(sys.argv[2]).resolve())
messages=[]
function(config,reporter=messages.append)
assert len(messages)==2 and len(messages[-1])>=32
assert Path(str(config.database)+'.setup-token').is_file()
print('Private interactive source setup passed')
"""
    result = subprocess.run([sys.executable, "-I", "-c", code,
                             str(Path(launcher.__file__).resolve()), str(tmp_path)],
                            cwd=tmp_path, capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "Private interactive source setup passed"


def test_real_fake_child_ready_and_cleanup(config, tmp_path):
    service = fake_server(tmp_path)
    supervisor = launcher.Supervisor(config, os.environ.copy(), reporter=lambda _: None)
    try:
        assert supervisor.start(service), fake_startup_diagnostic(service)
        child = service.process
        assert child.poll() is None
    finally:
        supervisor.shutdown()
    assert child.poll() is not None
    assert service.process is None


def test_synthetic_api_not_accepted_as_pilot(config, tmp_path):
    service = fake_server(tmp_path, mode="synthetic-prototype")
    service.timeout = 0.8
    supervisor = launcher.Supervisor(config, os.environ.copy(), reporter=lambda _: None)
    assert not supervisor.start(service)
    assert service.process is None


def test_start_timeout_reaps_child(config, tmp_path):
    service = fake_server(tmp_path, ready=False)
    service.timeout = 0.5
    supervisor = launcher.Supervisor(config, os.environ.copy(), reporter=lambda _: None)
    started = time.monotonic()
    assert not supervisor.start(service)
    assert time.monotonic() - started < 3
    assert service.process is None


def test_start_refuses_unowned_service(config, tmp_path):
    service = fake_server(tmp_path)
    supervisor = launcher.Supervisor(config, os.environ.copy(), reporter=lambda _: None)
    with socket.socket() as owner:
        owner.bind(("127.0.0.1", service.port))
        owner.listen()
        assert not supervisor.start(service)
        assert service.process is None
        assert owner.fileno() >= 0


def test_cancel_during_readiness_reaps_child(config, tmp_path):
    service = fake_server(tmp_path, ready=False)
    stop = threading.Event()
    supervisor = launcher.Supervisor(config, os.environ.copy(), stop_event=stop, reporter=lambda _: None)
    timer = threading.Timer(0.2, stop.set)
    timer.start()
    try:
        assert not supervisor.start(service)
        assert service.process is None
    finally:
        timer.cancel()
        supervisor.shutdown()


def test_restart_budget_is_finite(config, tmp_path, monkeypatch):
    service = fake_server(tmp_path)
    supervisor = launcher.Supervisor(replace(config, restart_limit=1), os.environ.copy(), reporter=lambda _: None)
    monkeypatch.setattr(supervisor.stop_event, "wait", lambda _: False)
    monkeypatch.setattr(supervisor, "start", lambda _: False)
    assert not supervisor.restart_failed(service)
    assert service.restarts == 1
    assert not supervisor.restart_failed(service)
    assert service.restarts == 1


def test_failed_owned_child_recovers_once(config, tmp_path, monkeypatch):
    service = fake_server(tmp_path)
    supervisor = launcher.Supervisor(config, os.environ.copy(), reporter=lambda _: None)
    try:
        assert supervisor.start(service), fake_startup_diagnostic(service)
        previous = service.process
        previous.terminate()
        previous.wait(timeout=5)
        original_wait = supervisor.stop_event.wait
        monkeypatch.setattr(supervisor.stop_event, "wait", lambda seconds: original_wait(min(seconds, 0.05)))
        assert supervisor.restart_failed(service), fake_startup_diagnostic(service)
        assert service.process.pid != previous.pid
        assert service.restarts == 1
    finally:
        supervisor.shutdown()


@pytest.mark.parametrize("wall_delta,monotonic_delta", [(35, 35), (35, 1), (-35, 1)])
def test_sleep_clock_or_scheduling_gap_requires_rearm(config, wall_delta, monotonic_delta):
    supervisor = launcher.Supervisor(config, {}, reporter=lambda _: None)
    assert supervisor.resumed(wall=supervisor.last_wall + wall_delta, monotonic=supervisor.last_monotonic + monotonic_delta)
    assert supervisor.rearm_required


def test_runtime_report_never_claims_camera_active(config):
    supervisor = launcher.Supervisor(config, {}, reporter=lambda _: None)
    supervisor.status("SERVICES_READY", "Services only")
    report = json.loads((config.data_dir / "runtime-status.json").read_text())
    assert report["camera_monitoring"] == "NOT_VERIFIED"
    assert str(config.root) not in json.dumps(report)


def test_report_atomic_private_and_no_clobbered_database(config):
    config.database.write_text("existing")
    report_path = config.data_dir / "readiness.json"
    pf.write_report(report_path, {"first": True})
    pf.write_report(report_path, {"second": True})
    assert json.loads(report_path.read_text()) == {"second": True}
    assert config.database.read_text() == "existing"
    assert not list(config.data_dir.glob(".readiness-*"))
    if os.name != "nt":
        assert report_path.stat().st_mode & 0o777 == 0o600


def test_report_cannot_replace_database_or_token(config):
    config.database.write_text("existing")
    with pytest.raises(pf.ConfigurationError):
        pf.write_report(config.database, {"must_not_replace": True})
    assert config.database.read_text() == "existing"
    token = launcher.ensure_token(config)
    original = token.read_bytes()
    with pytest.raises(pf.ConfigurationError):
        pf.write_report(token, {})
    assert token.read_bytes() == original


def test_permanent_model_failure_restarts_api_with_provider_interlock(config, monkeypatch):
    class Process:
        def __init__(self, result): self.result = result
        def poll(self): return self.result
    supervisor = launcher.Supervisor(replace(config, restart_limit=0), {}, reporter=lambda _: None)
    vision = launcher.OwnedService('vision', [], 11435, '/v1/models', 1, process=Process(1))
    api = launcher.OwnedService('api', [], 8765, '/api/health', 1, process=Process(None))
    supervisor.services = [vision, api]
    calls = []
    monkeypatch.setattr(supervisor, 'resumed', lambda: False)
    monkeypatch.setattr(supervisor.stop_event, 'wait', lambda _: supervisor.stop_event.is_set())
    monkeypatch.setattr(supervisor, 'stop_service', lambda item, **_: calls.append(('stop', item.name)))
    def start(item):
        calls.append(('start', item.name, supervisor.env['AISLESIGNALS_VISION_DISABLED']))
        supervisor.stop_event.set()
        return True
    monkeypatch.setattr(supervisor, 'start', start)
    assert supervisor.monitor() == 0
    assert calls == [('stop', 'api'), ('stop', 'vision'), ('start', 'api', '1')]
    assert vision.disabled
    assert supervisor.last_state == 'DEGRADED'


def accelerate_monitor(supervisor, monkeypatch):
    """Only synthetic services: shorten health cadence/deadline and backoff."""
    monkeypatch.setattr(launcher, "HEALTH_INTERVAL_SECONDS", 0.03)
    original_probe = launcher.probe_service
    monkeypatch.setattr(launcher, "probe_service", lambda *args, **kwargs:
                        original_probe(*args, **{**kwargs, "timeout": min(0.15, kwargs["timeout"])}))
    original_wait = supervisor.stop_event.wait
    monkeypatch.setattr(supervisor.stop_event, "wait", lambda seconds: original_wait(min(seconds, 0.03)))


@pytest.mark.parametrize("kind", ["api", "vision"])
def test_hung_live_process_is_recovered_and_old_api_generation_stopped(config, tmp_path, monkeypatch, kind):
    supervisor = launcher.Supervisor(config, os.environ.copy(), reporter=lambda _: None)
    service = fake_server(tmp_path, kind=kind)
    api = fake_server(tmp_path) if kind == "vision" else service
    timer = threading.Timer(12, supervisor.stop_event.set)
    try:
        assert supervisor.start(service), fake_startup_diagnostic(service)
        if api is not service:
            assert supervisor.start(api), fake_startup_diagnostic(api)
        old_process, old_api = service.process, api.process
        Path(service.command[1]).with_suffix(".fault").write_text("hang-once")
        assert old_process.poll() is None  # The old exit-only loop never handles this fault.
        accelerate_monitor(supervisor, monkeypatch)
        stopped = []
        original_stop = supervisor.stop_service
        def stop(item, **kwargs):
            stopped.append((item.name, kwargs.get("abort", False)))
            original_stop(item, **kwargs)
        monkeypatch.setattr(supervisor, "stop_service", stop)
        def report(detail):
            if "service recovered" in detail:
                supervisor.stop_event.set()
        supervisor.reporter = report
        timer.start()
        assert supervisor.monitor() == 0
        assert service.restarts == 1
        assert service.process.pid != old_process.pid
        assert old_process.poll() is not None
        assert api.process.pid != old_api.pid
        assert old_api.poll() is not None
        assert supervisor.rearm_required
        assert supervisor.last_state == "REARM_REQUIRED"
        if kind == "vision":
            assert stopped[:2] == [("api", True), ("vision", False)]
            assert api.dependency_restarts == 1
        runtime = json.loads((config.data_dir / "runtime-status.json").read_text())
        assert runtime["camera_monitoring"] == "NOT_VERIFIED"
        assert runtime["rearm_required"] is True
        assert runtime["recovery_generation"] == 1
    finally:
        timer.cancel()
        supervisor.shutdown()


def test_one_transient_http_failure_keeps_owned_process_and_resets_health_streak(config, tmp_path, monkeypatch):
    service = fake_server(tmp_path)
    supervisor = launcher.Supervisor(config, os.environ.copy(), reporter=lambda _: None)
    timer = threading.Timer(5, supervisor.stop_event.set)
    try:
        assert supervisor.start(service), fake_startup_diagnostic(service)
        old_process = service.process
        Path(service.command[1]).with_suffix(".fault").write_text("transient")
        accelerate_monitor(supervisor, monkeypatch)
        states = []
        original_status = supervisor.status
        def status(state, detail, **kwargs):
            states.append(state)
            original_status(state, detail, **kwargs)
            if state == "SERVICES_READY" and "DEGRADED" in states:
                supervisor.stop_event.set()
        monkeypatch.setattr(supervisor, "status", status)
        timer.start()
        assert supervisor.monitor() == 0
        assert states[:2] == ["DEGRADED", "SERVICES_READY"]
        assert service.process is old_process
        assert service.restarts == 0
        assert not supervisor.rearm_required
        assert service.health.consecutive_failures == 0
    finally:
        timer.cancel()
        supervisor.shutdown()


def test_persistent_hang_exhausts_restart_budget_and_leaves_no_child(config, tmp_path, monkeypatch):
    service = fake_server(tmp_path)
    supervisor = launcher.Supervisor(replace(config, restart_limit=2), os.environ.copy(), reporter=lambda _: None)
    try:
        assert supervisor.start(service), fake_startup_diagnostic(service)
        Path(service.command[1]).with_suffix(".fault").write_text("hang")
        service.timeout = 0.35
        accelerate_monitor(supervisor, monkeypatch)
        assert supervisor.monitor() == 1
        assert service.restarts == 2
        assert service.process is None
        assert supervisor.last_state == "FAILED"
        assert supervisor.rearm_required
    finally:
        supervisor.shutdown()


def test_recovery_port_taken_by_unrelated_listener_is_never_adopted_or_stopped(config, tmp_path, monkeypatch):
    service = fake_server(tmp_path)
    supervisor = launcher.Supervisor(config, os.environ.copy(), reporter=lambda _: None)
    try:
        assert supervisor.start(service), fake_startup_diagnostic(service)
        supervisor.stop_service(service)
        with socket.socket() as unrelated:
            if os.name != "nt":
                unrelated.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            unrelated.bind(("127.0.0.1", service.port))
            unrelated.listen()
            accelerate_monitor(supervisor, monkeypatch)
            assert supervisor.recover(service) == 1
            assert service.process is None
            assert service.restarts == config.restart_limit
            assert unrelated.fileno() >= 0
            with socket.create_connection(("127.0.0.1", service.port), timeout=1):
                accepted, _ = unrelated.accept()
                accepted.close()
    finally:
        supervisor.shutdown()


def test_stop_during_hung_health_check_cancels_without_spawning_recovery(config, tmp_path, monkeypatch):
    service = fake_server(tmp_path)
    supervisor = launcher.Supervisor(config, os.environ.copy(), reporter=lambda _: None)
    timer = threading.Timer(0.2, supervisor.stop_event.set)
    try:
        assert supervisor.start(service), fake_startup_diagnostic(service)
        Path(service.command[1]).with_suffix(".fault").write_text("hang")
        monkeypatch.setattr(launcher, "HEALTH_INTERVAL_SECONDS", 0.02)
        timer.start()
        before = time.monotonic()
        assert supervisor.monitor() == 0
        assert time.monotonic() - before < 1.5
        assert service.restarts == 0
    finally:
        timer.cancel()
        supervisor.shutdown()
    assert service.process is None


def test_recovery_does_not_bypass_sleep_interlock(config, tmp_path, monkeypatch):
    service = fake_server(tmp_path)
    supervisor = launcher.Supervisor(config, {}, reporter=lambda _: None)
    monkeypatch.setattr(supervisor.stop_event, "wait", lambda _: False)
    monkeypatch.setattr(supervisor, "start", lambda _: pytest.fail("Must not restart after sleep"))
    supervisor.last_wall -= 30
    assert supervisor.recover(service) == 3
    assert supervisor.resume_detected and supervisor.rearm_required


def test_fresh_api_child_revokes_session_authority_without_deleting_accounts_or_cases(tmp_path):
    from services.api.store import Store
    store = Store(str(tmp_path / "synthetic-protected.sqlite3"), mode="pilot")
    with store.transaction() as conn:
        conn.execute("INSERT INTO users VALUES (?,?,?,?,?,?,?,?)", (
            "synthetic-user", "synthetic@example.invalid", "Synthetic tester", "MANAGER",
            "synthetic-org", "synthetic-site", "synthetic-salt", "not-a-valid-password"))
        conn.execute("INSERT INTO sessions VALUES (?,?,?,?,?)", (
            "synthetic-old-session", "synthetic-user", "synthetic-csrf", time.time(), time.time()))
        conn.execute("INSERT INTO session_scopes VALUES (?,?,?)", (
            "synthetic-old-session", "synthetic-org", "synthetic-site"))
        conn.execute("INSERT INTO entities VALUES (?,?,?,?,?,?)", (
            "synthetic-case", "incident", "synthetic-org", "synthetic-site", "{}", "2026-09-13"))
    launcher.invalidate_prior_sessions(store)
    with store.transaction() as conn:
        assert conn.execute("SELECT count(*) FROM sessions").fetchone()[0] == 0
        assert conn.execute("SELECT count(*) FROM session_scopes").fetchone()[0] == 0
        assert conn.execute("SELECT count(*) FROM users").fetchone()[0] == 1
        assert conn.execute("SELECT count(*) FROM entities WHERE kind='incident'").fetchone()[0] == 1


@pytest.mark.parametrize("previous,rearm,final", [
    ("SERVICES_READY", False, "STOPPED"),
    ("FAILED", True, "FAILED"),
    ("REARM_REQUIRED", True, "REARM_REQUIRED"),
])
def test_shutdown_blocks_real_runtime_projection_before_graceful_cleanup(config, monkeypatch, previous, rearm, final):
    from datetime import datetime, timezone
    from types import SimpleNamespace
    monkeypatch.setenv("AISLESIGNALS_MODE", "pilot")
    # Importing the API module creates its default app. Give that constructor
    # its own temporary DB and close it only when this test caused the import.
    first_import = "services.api.app" not in sys.modules
    monkeypatch.setenv("AISLESIGNALS_DB_PATH", str(config.data_dir / "import-only.db"))
    monkeypatch.setenv("AISLESIGNALS_PILOT_SUPERVISED_CHILD", "1")
    monkeypatch.setenv("AISLESIGNALS_VISION_DISABLED", "0")
    from services.api.app import create_app, app as imported_app
    if first_import:
        imported_app.state.interactions.close()
    app = create_app(config.database, config.root / "apps/web/dist", mode="pilot")
    # Exercise the actual safe projection and worker fence without a network
    # listener or a fake implementation of their readiness rules.
    health = next(route.endpoint for route in app.routes if route.path == "/api/runtime/health")
    caller = SimpleNamespace(user={"site_id": "synthetic-site"})
    supervisor = launcher.Supervisor(config, {}, reporter=lambda _: None)
    stamp = datetime.now(timezone.utc).isoformat()
    for name in ("vision", "api"):
        service = launcher.OwnedService(name, [], 23456, "/api/health", 1,
                                       process=SimpleNamespace(poll=lambda: None))
        service.health.state = "READY"
        service.last_checked_at = stamp
        supervisor.services.append(service)
    supervisor.status("SERVICES_READY", "Synthetic healthy services")
    bound = {"runtime_context": health(caller)["context"]}
    assert app.state.interactions.runtime_current(bound)
    supervisor.last_state, supervisor.rearm_required = previous, rearm
    stopped = []
    def cleanup(service, **options):
        projected = health(caller)
        assert projected["state"] == "STOPPED"
        assert not projected["monitoring_allowed"] and projected["context"] is None
        assert not app.state.interactions.runtime_current(bound)
        assert supervisor.stop_event.is_set()
        assert options == {"abort": False}
        stopped.append(service.name)
        service.process = None
    monkeypatch.setattr(supervisor, "stop_service", cleanup)
    try:
        supervisor.shutdown()
        assert stopped == ["api", "vision"]
        assert supervisor.last_state == final
        assert supervisor.rearm_required is rearm
    finally:
        app.state.interactions.close()


def test_shutdown_report_failure_aborts_api_and_still_closes_owned_containment(config, monkeypatch):
    from types import SimpleNamespace
    supervisor = launcher.Supervisor(config, {}, reporter=lambda _: None)
    supervisor.last_state = "FAILED"
    supervisor.rearm_required = True
    supervisor.services = [launcher.OwnedService(name, [], 23456, "/api/health", 1)
                           for name in ("vision", "api")]
    calls = []
    supervisor.windows_job = SimpleNamespace(close=lambda: calls.append(("containment", "closed")))
    def fail_report(*_, **__):
        raise OSError("Synthetic report write failure")
    monkeypatch.setattr(supervisor, "status", fail_report)
    monkeypatch.setattr(supervisor, "stop_service", lambda item, **options: calls.append((item.name, options["abort"])))
    supervisor.shutdown()
    assert calls == [("api", True), ("vision", False), ("containment", "closed")]
    assert supervisor.last_state == "FAILED" and supervisor.rearm_required
