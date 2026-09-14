"""Actual SIGTERM/SIGINT cleanup of an exclusively owned browser API/PG fixture.

Run with CLOUD_RUN_POSTGRES_TESTS=1 and leave isolated API port 60645 free.
No inherited services, PostgreSQL environment or customer databases are used.
"""

import json
import os
from pathlib import Path
import queue
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
from urllib.request import ProxyHandler, build_opener

import pytest


ROOT = Path(__file__).resolve().parents[2]
PORT = 60645
pytestmark = pytest.mark.skipif(
    os.name == "nt" or os.environ.get("CLOUD_RUN_POSTGRES_TESTS") != "1",
    reason="Explicit disposable POSIX PostgreSQL lifecycle test required",
)


def listening(port):
    with socket.socket() as sock:
        sock.settimeout(0.2)
        return sock.connect_ex(("127.0.0.1", port)) == 0


def alive(pid):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


def wait_until(predicate, *, timeout=10):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.05)
    return False


def lifecycle_line(process, lines):
    deadline = time.monotonic() + 45
    while time.monotonic() < deadline:
        try:
            line = lines.get(timeout=0.2)
        except queue.Empty:
            assert process.poll() is None, "Synthetic fixture exited before owning PostgreSQL"
            continue
        assert "bootstrapToken" not in line and "postgresql://" not in line
        if line.startswith("CONTROL_LIFECYCLE "):
            return json.loads(line[len("CONTROL_LIFECYCLE "):])
    pytest.fail("Synthetic fixture did not report its owned PostgreSQL")


@pytest.fixture
def postgres_parent():
    # PostgreSQL's Unix socket path must fit the OS bound; pytest's nested
    # tmp_path can exceed it on macOS. This root is fresh and exclusively ours.
    with tempfile.TemporaryDirectory(prefix="as-browser-", dir="/tmp") as directory:
        yield Path(directory).resolve()


@pytest.mark.parametrize("phase", ["startup", "running"])
@pytest.mark.parametrize("stop_signal", [signal.SIGTERM, signal.SIGINT], ids=["sigterm", "sigint"])
def test_fixture_signal_unwinds_its_owned_api_postgres_and_tempdir(postgres_parent, phase, stop_signal):
    assert shutil.which("initdb") and shutil.which("pg_ctl"), "PostgreSQL tools required on PATH"
    assert not listening(PORT), "60645 is occupied; never stop or reuse another service"
    env = {key: value for key, value in os.environ.items()
           if not key.startswith(("CLOUD_", "AISLESIGNALS_", "DATABASE_", "PG", "CONTROL_FIXTURE_"))}
    # Every database directory from this child must belong to this test's root.
    env.update(TMPDIR=str(postgres_parent), CONTROL_FIXTURE_LIFECYCLE="1")
    if phase == "startup":
        env["CONTROL_FIXTURE_STARTUP_GATE"] = "1"
    process = subprocess.Popen(
        [sys.executable, "-u", str(ROOT / "tests/control-e2e/server.py"), str(PORT)],
        cwd=ROOT, env=env, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, text=True, start_new_session=True,
    )
    lines = queue.Queue()
    output = []
    errors = []

    def read(stream, collected, forward=False):
        for line in stream:
            collected.append(line)
            if forward:
                lines.put(line)

    readers = [threading.Thread(target=read, args=(process.stdout, output, True), daemon=True),
               threading.Thread(target=read, args=(process.stderr, errors), daemon=True)]
    for reader in readers:
        reader.start()
    owned = None
    try:
        reported = lifecycle_line(process, lines)
        assert set(reported) == {"phase", "api_pid", "postgres_pid", "root", "api_port", "postgres_port"}
        assert reported["api_pid"] == process.pid and reported["api_port"] == PORT
        directory = Path(reported["root"])
        assert directory.resolve().parent == postgres_parent
        assert directory.name.startswith("as-control-pg-") and not directory.is_symlink()
        data = directory / "data"
        postgres_pid = reported["postgres_pid"]
        assert type(postgres_pid) is int and postgres_pid > 1
        assert int((data / "postmaster.pid").read_text().splitlines()[0]) == postgres_pid
        owned = (directory, data, postgres_pid)
        assert alive(postgres_pid) and listening(reported["postgres_port"])
        if phase == "running":
            opener = build_opener(ProxyHandler({}))

            def ready():
                try:
                    with opener.open(f"http://127.0.0.1:{PORT}/health/ready", timeout=0.5) as response:
                        return response.status == 200
                except OSError:
                    return False

            assert wait_until(ready, timeout=15), "Owned API did not finish startup"
        else:
            assert not listening(PORT), "Startup regression must precede API serving"
        process.send_signal(stop_signal)
        assert process.wait(timeout=25) == 128 + stop_signal
        assert wait_until(lambda: not alive(postgres_pid)), "Owned PostgreSQL survived fixture shutdown"
        assert not listening(PORT) and not listening(reported["postgres_port"])
        assert not directory.exists(), "Owned PostgreSQL temporary directory survived shutdown"
        assert not list(postgres_parent.glob("as-control-pg-*"))
    finally:
        # Failure cleanup is limited to our Popen child and its verified private
        # cluster. Never kill process groups, inherited PIDs or arbitrary roots.
        if process.poll() is None:
            process.send_signal(signal.SIGTERM)
            try:
                process.wait(timeout=25)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
        if owned is not None:
            directory, data, postgres_pid = owned
            pidfile = data / "postmaster.pid"
            if pidfile.exists() and int(pidfile.read_text().splitlines()[0]) == postgres_pid:
                subprocess.run([shutil.which("pg_ctl"), "-D", str(data), "-m", "fast", "-w", "-t", "10", "stop"],
                               env=env, capture_output=True, check=True, timeout=15)
            if directory.exists() and not alive(postgres_pid):
                shutil.rmtree(directory)
        for reader in readers:
            reader.join(timeout=2)
        process.stdout.close()
        process.stderr.close()
        assert "bootstrapToken" not in "".join(output + errors)
        assert "postgresql://" not in "".join(output + errors)
