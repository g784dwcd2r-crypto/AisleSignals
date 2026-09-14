"""Synthetic browser fixture: a process-owned cloud API and PostgreSQL cluster.

No existing database or customer environment is read. Not a production entrypoint.
"""
import json
import os
from pathlib import Path
import signal
import sys
import time

root = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(root))
sys.path.insert(0, str(root / "tests" / "cloud"))
for key in list(os.environ):
    if key.startswith(("CLOUD_", "AISLESIGNALS_", "DATABASE_", "PG")):
        del os.environ[key]
from control_test_support import disposable_postgres, reset_database
from services.cloud.app import create_app
import psycopg
import uvicorn


def lifecycle_metadata(settings, port):
    """Only this process's freshly created cluster; never connection secrets."""
    with psycopg.connect(settings.database_url, connect_timeout=3) as conn:
        data = Path(conn.execute("SHOW data_directory").fetchone()[0]).resolve()
        database_port = int(conn.execute("SHOW port").fetchone()[0])
    postgres_pid = int((data / "postmaster.pid").read_text().splitlines()[0])
    print("CONTROL_LIFECYCLE " + json.dumps({
        "phase": "postgres_ready", "api_pid": os.getpid(), "postgres_pid": postgres_pid,
        "root": str(data.parent), "api_port": port, "postgres_port": database_port,
    }), flush=True)


def main(argv=None):
    arguments = sys.argv[1:] if argv is None else argv
    port = int(arguments[0])
    if port not in {60644, 60645}:
        raise ValueError("Only isolated browser fixture ports 60644 and 60645 are permitted")
    received = None
    server = None

    def stop(signum, _frame):
        nonlocal received
        if received is None:
            received = signum
        if server is not None:
            server.should_exit = True

    # Uvicorn replays its handled signals after restoring the outer handlers.
    # Keep ours installed until the PG context has stopped and removed its own
    # cluster. A signal during initdb/pg_ctl marks shutdown without interrupting
    # their bounded subprocess call before ownership/cleanup can be established.
    previous = {sig: signal.signal(sig, stop) for sig in (signal.SIGTERM, signal.SIGINT)}
    try:
        with disposable_postgres() as settings:
            reporting = os.environ.get("CONTROL_FIXTURE_LIFECYCLE") == "1"
            if reporting:
                lifecycle_metadata(settings, port)
            if reporting and os.environ.get("CONTROL_FIXTURE_STARTUP_GATE") == "1":
                # Deterministic regression point after PG ownership but before
                # migration/API startup. Opt-in only, bounded even if no signal.
                deadline = time.monotonic() + 30
                while received is None and time.monotonic() < deadline:
                    time.sleep(0.05)
                if received is None:
                    raise RuntimeError("Synthetic fixture startup gate timed out")
            if received is None:
                reset_database(settings)
            if received is None:
                server = uvicorn.Server(uvicorn.Config(
                    create_app(settings), host="127.0.0.1", port=port,
                    access_log=False, proxy_headers=False, log_level="warning",
                    timeout_graceful_shutdown=5,
                ))
                if not reporting:
                    # Existing browser fixture IPC only; lifecycle diagnostics
                    # deliberately omit this synthetic bootstrap credential.
                    print("CONTROL_READY " + json.dumps({"url": f"http://127.0.0.1:{port}", "bootstrapToken": settings.bootstrap_token}), flush=True)
                if received is None:
                    server.run()
    finally:
        for sig, handler in previous.items():
            signal.signal(sig, handler)
    return 128 + received if received is not None else 0


if __name__ == "__main__":
    raise SystemExit(main())
