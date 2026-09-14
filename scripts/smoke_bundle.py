"""Exercise a real packaged executable, without hardware, footage or a model server."""
from __future__ import annotations

import argparse
import base64
import http.cookiejar
from html.parser import HTMLParser
import io
import json
import os
from pathlib import Path
import socket
import secrets
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from urllib.parse import urlsplit
from uuid import uuid4

from PIL import Image


def wait_for_server_exit(port: int, timeout: float = 10) -> None:
    """An exited launcher must not leave its API serving from the temp bundle."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        with socket.socket() as probe:
            probe.settimeout(0.2)
            if probe.connect_ex(("127.0.0.1", port)) != 0:
                return
        time.sleep(0.1)
    raise AssertionError("The packaged launcher exited but its API is still running")


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise urllib.error.HTTPError(req.full_url, code, "Local redirects are not allowed", headers, fp)


class AssetReferences(HTMLParser):
    def __init__(self):
        super().__init__()
        self.urls = []

    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        if tag == "script" and values.get("src"):
            self.urls.append(values["src"])
        if tag == "link" and values.get("rel") == "stylesheet" and values.get("href"):
            self.urls.append(values["href"])


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("executable", type=Path)
    parser.add_argument("--mode", choices=["demo", "pilot"], default="demo")
    args = parser.parse_args()
    executable = args.executable.resolve()
    if not executable.is_file():
        raise SystemExit(f"Executable not found: {executable}")
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    origin = f"http://127.0.0.1:{port}"
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect(), urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))

    def request(path, *, method="GET", body=None, csrf=None, key=None, site=None, runtime=None):
        headers = {"Content-Type": "application/json", "Origin": origin}
        if csrf:
            headers["X-CSRF-Token"] = csrf
        if key:
            headers["Idempotency-Key"] = key
        if site:
            headers["X-AisleSignals-Site"] = site
        if runtime:
            headers["X-AisleSignals-Runtime"] = runtime
        value = urllib.request.Request(origin + path, data=json.dumps(body).encode() if body is not None else None, headers=headers, method=method)
        with opener.open(value, timeout=8) as response:
            return json.load(response)

    # A bound, non-listening socket reserves an unavailable loopback model port.
    # Never contact or reuse the developer's actual optional inference service.
    with socket.socket() as unavailable, tempfile.TemporaryDirectory(prefix="aislesignals-bundle-") as data_dir:
        unavailable.bind(("127.0.0.1", 0))
        env = {key: value for key, value in os.environ.items() if not key.startswith("AISLESIGNALS_")}
        env["AISLESIGNALS_VISION_URL"] = f"http://127.0.0.1:{unavailable.getsockname()[1]}"
        env["AISLESIGNALS_VISION_MODEL"] = "synthetic-unavailable-smoke-model"
        # macOS exposes its own temporary directory through /var -> /private/var.
        # Use its canonical path so the launcher's no-symlink data rule still holds.
        command = [str(executable), "--no-browser", "--port", str(port), "--data-dir", str(Path(data_dir).resolve())]
        if args.mode == "pilot":
            command.append("--casework-only")
            # This exact executable must spawn its packaged sender module and
            # reap the child at the authorization gate. No credentials, request
            # or API process exists yet; this catches a missing freeze_support
            # dispatch or a missing frozen sender dependency on both CI hosts.
            sender = subprocess.run([str(executable), "--sender-spawn-smoke"],
                                    cwd=Path(data_dir).resolve(), env=env,
                                    capture_output=True, timeout=20)
            assert sender.returncode == 0, "Packaged sender spawn/exit check failed"
            assert len(sender.stdout) <= 1024 and not sender.stderr, "Unexpected sender smoke output"
            assert json.loads(sender.stdout) == {
                "check": "frozen-sender-spawn-v1", "ready": True,
                "request_sent": False, "child_reaped": True,
            }, "Packaged sender did not confirm its real READY and bounded child exit"
            assert not list(Path(data_dir).iterdir()), "Sender smoke unexpectedly created local state"
            for subcommand in ("accounts", "model-setup", "backup", "rollout", "startup", "update"):
                check = subprocess.run([str(executable), subcommand, "--help"],
                                       capture_output=True, timeout=30)
                assert check.returncode == 0, f"Packaged {subcommand} command is missing"
        # A file avoids a blocked stdout PIPE if startup emits substantial output.
        with tempfile.TemporaryFile() as logs:
            process = subprocess.Popen(command, env=env, stdout=logs, stderr=subprocess.STDOUT)
            try:
                deadline = time.monotonic() + 60
                while time.monotonic() < deadline:
                    if process.poll() is not None:
                        raise AssertionError("Bundle exited before readiness")
                    try:
                        health = request("/api/health")
                        assert health["mode"] == ("pilot" if args.mode == "pilot" else "synthetic-prototype")
                        break
                    except OSError:
                        time.sleep(0.25)
                else:
                    raise AssertionError("Bundle did not become ready")
                with opener.open(origin, timeout=5) as response:
                    html = response.read().decode()
                    assert '<div id="root"' in html
                assets = AssetReferences()
                assets.feed(html)
                assert any(url.endswith(".js") for url in assets.urls), "No bundled application JavaScript"
                assert any(url.endswith(".css") for url in assets.urls), "No bundled styles"
                for asset in assets.urls:
                    parsed = urlsplit(asset)
                    assert not parsed.scheme and not parsed.netloc and asset.startswith("/assets/")
                    with opener.open(origin + asset, timeout=5) as response:
                        assert response.status == 200
                        assert "text/html" not in response.headers.get("Content-Type", "")
                        assert len(response.read()) > 0
                # Model/runtime files are actually served in the frozen bundle.
                for path, content_type in [("/vision/pose_landmarker_lite.task", "text/html"), ("/vision/wasm/vision_wasm_internal.wasm", "text/html")]:
                    with opener.open(origin + path, timeout=5) as response:
                        assert content_type not in response.headers.get("Content-Type", "")
                        assert len(response.read()) > 1000
                if args.mode == "pilot":
                    for path, kwargs in [("/api/bootstrap", {}), ("/api/login", {"method": "POST", "body": {"email": "manager@harbour.demo", "password": "AisleDemo!2026"}})]:
                        try:
                            request(path, **kwargs)
                        except urllib.error.HTTPError as error:
                            assert error.code == 401
                        else:
                            raise AssertionError("Unprovisioned pilot accepted a demo account or private bootstrap")
                    # Issue a private local capability without printing it, then
                    # claim the first owner through the actual frozen setup API.
                    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
                    from services.api.store import Store
                    from services.api.pilot_admin import issue_setup_token
                    password = secrets.token_urlsafe(24)
                    store = Store(str(Path(data_dir).resolve() / "aislesignals.db"), mode="pilot")
                    issued = issue_setup_token(store)
                    assert request("/api/setup/status")["available"] is True
                    setup = request("/api/setup", method="POST", body={
                        "organisation_name": "Synthetic packaging group", "branch_name": "Synthetic packaging branch",
                        "email": "packaging@example.invalid", "name": "Synthetic package operator",
                        "password": password, "setup_token": issued["token"],
                    })
                    assert setup["created"] is True and setup["sign_in_required"] is True
                    assert request("/api/setup/status")["available"] is False
                    session = request("/api/login", method="POST", body={"email": "packaging@example.invalid", "password": password})
                    assert request("/api/bootstrap")["candidates"] == []
                    users = request("/api/admin/users")
                    assert len(users["users"]) == 1
                    assert users["users"][0]["email"] == "packaging@example.invalid"
                    status = request("/api/interactions/status")
                    assert status["ready"] is False and status["mode"] == "disabled"
                    # Exercise the real packaged launcher-to-API report contract,
                    # including the casework-only provider interlock.
                    runtime_deadline = time.monotonic() + 10
                    while time.monotonic() < runtime_deadline:
                        runtime = request("/api/runtime/health", site=session["current_site_id"])
                        if runtime["monitoring_allowed"]:
                            break
                        time.sleep(0.1)
                    assert runtime["supervised"] is True and runtime["monitoring_allowed"] is True
                    assert runtime["product_available"] is False
                    assert runtime["site_id"] == session["current_site_id"]
                    assert len(runtime["context"]) == 64 and len(runtime["runtime_id"]) == 32
                    output = io.BytesIO()
                    Image.new("RGB", (64, 64), "blue").save(output, "JPEG")
                    frame = base64.b64encode(output.getvalue()).decode()
                    job_body = {
                        "run_id": str(uuid4()), "source_kind": "RECORDED_VIDEO", "source_label": "Synthetic packaging pixels",
                        "frames": [{"at_seconds": at, "jpeg_base64": frame} for at in [0, 2, 4]],
                    }
                    try:
                        request("/api/interactions/jobs", method="POST", csrf=session["csrf_token"],
                                site=session["current_site_id"], key=str(uuid4()), body=job_body)
                    except urllib.error.HTTPError as error:
                        assert error.code == 409
                    else:
                        raise AssertionError("Supervised bundle accepted a live job without runtime context")
                    job = request("/api/interactions/jobs", method="POST", csrf=session["csrf_token"],
                                  site=session["current_site_id"], runtime=runtime["context"], key=str(uuid4()), body=job_body)
                    for _ in range(80):
                        result = request("/api/interactions/jobs/" + job["id"], site=session["current_site_id"], runtime=runtime["context"])
                        if result["status"] not in {"pending", "running"}:
                            break
                        time.sleep(0.1)
                    assert result["status"] == "failed" and "result" not in result
                    request("/api/logout", method="POST", body={}, csrf=session["csrf_token"], site=session["current_site_id"])
                    print("PASS packaged pilot: frozen sender spawn/READY/exit without a request, startup, assets, account tools, private owner setup, administration, no demo identity, named login, supervised runtime fence, JPEG job and disabled provider; no physical camera/model acceptance")
                    return
                session = request("/api/login", method="POST", body={"email": "manager@harbour.demo", "password": "AisleDemo!2026"})
                assert session["csrf_token"] and session["user"]["role"] == "MANAGER"
                bootstrap = request("/api/bootstrap")
                assert bootstrap["site"]["monthly_price_cents"] == 6000 and bootstrap["candidates"]
                status = request("/api/interactions/status")
                assert status["ready"] is False, "Missing optional model must never appear ready"
                output = io.BytesIO()
                Image.new("RGB", (64, 64), "blue").save(output, "JPEG")
                frame = base64.b64encode(output.getvalue()).decode()
                # Accepting real JPEG bytes proves the frozen Pillow JPEG plugin
                # loads. The absent model then fails explicitly without a result.
                job = request("/api/interactions/jobs", method="POST", csrf=session["csrf_token"], key=str(uuid4()), body={
                    "run_id": str(uuid4()), "source_kind": "RECORDED_VIDEO", "source_label": "Synthetic bundle smoke shapes",
                    "frames": [{"at_seconds": at, "jpeg_base64": frame} for at in [0, 2, 4]],
                })
                for _ in range(80):
                    result = request("/api/interactions/jobs/" + job["id"])
                    if result["status"] not in {"pending", "running"}:
                        break
                    time.sleep(0.1)
                assert result["status"] == "failed" and "result" not in result and result["error"]
                assert not list(Path(data_dir).rglob("*.jpg")), "Failed model job left sampled footage behind"
                request("/api/interactions/" + job["id"], method="DELETE", csrf=session["csrf_token"])
                assert request("/api/logout", method="POST", body={}, csrf=session["csrf_token"])["ok"]
                print("PASS packaged demo: startup, assets, login, JPEG decoding, unavailable-model recovery, scoped bootstrap, logout")
            except BaseException:
                logs.seek(0)
                print(logs.read().decode(errors="replace")[-4000:])
                raise
            finally:
                process.terminate()
                try:
                    process.wait(timeout=15)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
                wait_for_server_exit(port)


if __name__ == "__main__":
    main()
