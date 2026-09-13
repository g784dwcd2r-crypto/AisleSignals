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


def fake_server(tmp_path, *, ready=True, mode="pilot"):
    script = tmp_path / ("fake_ready.py" if ready else "fake_unready.py")
    script.write_text(textwrap.dedent(f"""
        import json, sys
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
                body = b'{{"status":"ok","mode":"{mode}"}}'
                self.send_response({200 if ready else 503})
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
    return launcher.OwnedService("api", [sys.executable, str(script), str(port)], port, "/api/health", 10)


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
    monkeypatch.setattr(supervisor.stop_event, 'wait', lambda _: len(calls) > 1)
    monkeypatch.setattr(supervisor, 'stop_service', lambda item: calls.append(('stop', item.name)))
    def start(item):
        calls.append(('start', item.name, supervisor.env['AISLESIGNALS_VISION_DISABLED']))
        return True
    monkeypatch.setattr(supervisor, 'start', start)
    assert supervisor.monitor() == 0
    assert calls == [('stop', 'api'), ('start', 'api', '1')]
    assert vision.disabled
    assert supervisor.last_state == 'DEGRADED'
