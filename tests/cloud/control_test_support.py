"""Process-owned disposable PostgreSQL and synthetic identity helpers.

No inherited database connection or real customer account is ever used.
"""

from contextlib import contextmanager
from dataclasses import replace
import os
from pathlib import Path
import secrets
import shlex
import shutil
import socket
import subprocess
import tempfile
import time

import psycopg
import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from services.cloud.config import CloudSettings
from services.cloud.control_auth import totp_code
from services.cloud.migrate import migrate


@contextmanager
def disposable_postgres():
    initdb, pg_ctl = shutil.which("initdb"), shutil.which("pg_ctl")
    if not initdb or not pg_ctl or os.name == "nt":
        pytest.fail("Explicit real PostgreSQL run requires POSIX initdb and pg_ctl on PATH")
    with tempfile.TemporaryDirectory(prefix="as-control-pg-") as directory:
        root = Path(directory)
        data, password_file = root / "data", root / "password"
        password = secrets.token_urlsafe(32)
        password_file.write_text(password + "\n")
        password_file.chmod(0o600)
        env = {k: v for k, v in os.environ.items() if not k.startswith(("PG", "DATABASE_", "CLOUD_", "AISLESIGNALS_"))}
        env["LC_ALL"] = "C"
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        subprocess.run([initdb, "-D", str(data), "-U", "control_test", "--no-locale", "--encoding=UTF8", "--auth-local=trust", "--auth-host=scram-sha-256", f"--pwfile={password_file}"], env=env, capture_output=True, check=True, timeout=30)
        started = False
        try:
            options = shlex.join(["-h", "127.0.0.1", "-p", str(port), "-k", str(root), "-c", "max_connections=16"])
            subprocess.run([pg_ctl, "-D", str(data), "-l", str(root / "server.log"), "-o", options, "-w", "-t", "10", "start"], env=env, capture_output=True, check=True, timeout=15)
            started = True
            yield CloudSettings.from_env({"CLOUD_ENV": "development", "CLOUD_DATABASE_SSLMODE": "disable", "CLOUD_ALLOWED_HOSTS": "testserver,localhost,127.0.0.1",
                "DATABASE_URL": f"postgresql://control_test:{password}@127.0.0.1:{port}/postgres", "CLOUD_AUTH_KEY": Fernet.generate_key().decode(), "CLOUD_BOOTSTRAP_TOKEN": secrets.token_urlsafe(32)})
        finally:
            if started or (data / "postmaster.pid").exists():
                subprocess.run([pg_ctl, "-D", str(data), "-m", "fast", "-w", "-t", "10", "stop"], env=env, capture_output=True, check=True, timeout=15)


def reset_database(settings):
    with psycopg.connect(settings.database_url) as conn:
        conn.execute("DROP SCHEMA IF EXISTS aislesignals_control CASCADE")
    migrate(settings)


class NonRecurringMaintenance:
    """Explicit fixture boundary; scheduler behavior has its own real-PG tests."""
    def __init__(self, store):
        self.store = store

    def start(self):
        return True

    def stop(self, *, timeout):
        return True


def new_client(settings):
    from services.cloud.app import create_app
    return TestClient(create_app(settings, maintenance_factory=NonRecurringMaintenance), base_url="https://testserver", headers={"Origin": "https://testserver"})


def bootstrap(client, settings, *, name="Synthetic Owner", email="owner@example.test"):
    response = client.post("/control-api/setup/begin", json={"token": settings.bootstrap_token, "organisation_name": "Synthetic Pharmacy Group", "name": name, "email": email, "password": "Synthetic-only-password-123"})
    assert response.status_code == 200, response.text
    challenge = response.json()
    response = client.post("/control-api/setup/complete", json={"challenge_token": challenge["challenge_token"], "code": totp_code(challenge["totp_secret"], int(time.time()) // 30)})
    assert response.status_code == 200, response.text
    session = response.json()
    client.headers["X-CSRF-Token"] = session["csrf_token"]
    return session, challenge["totp_secret"]


def pharmacy(client, name="Synthetic Harbour"):
    response = client.post("/control-api/pharmacies", json={"name": name, "address": "Synthetic address", "timezone": "Europe/Dublin"})
    assert response.status_code == 201, response.text
    return response.json()


def invited_client(owner, settings, *, role="REVIEWER", pharmacy_ids=(), email="reviewer@example.test"):
    response = owner.post("/control-api/invitations", json={"email": email, "name": "Synthetic Staff", "role": role, "pharmacy_ids": list(pharmacy_ids)})
    assert response.status_code == 201, response.text
    invitation = response.json()
    client = new_client(settings)
    response = client.post("/control-api/invitations/begin", json={"token": invitation["token"], "name": "Synthetic Staff", "password": "Synthetic-only-password-123"})
    assert response.status_code == 200, response.text
    challenge = response.json()
    response = client.post("/control-api/invitations/complete", json={"challenge_token": challenge["challenge_token"], "code": totp_code(challenge["totp_secret"], int(time.time()) // 30)})
    assert response.status_code == 200, response.text
    client.headers["X-CSRF-Token"] = response.json()["csrf_token"]
    return client, response.json(), challenge["totp_secret"], invitation
