"""Exercise an actual packaged executable, including UI assets and authenticated API."""
from __future__ import annotations

import http.cookiejar
from html.parser import HTMLParser
import json
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from urllib.parse import urlsplit


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
    executable = Path(sys.argv[1]).resolve()
    if not executable.is_file():
        raise SystemExit(f"Executable not found: {executable}")
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    origin = f"http://127.0.0.1:{port}"
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect(), urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
    with tempfile.TemporaryDirectory(prefix="aislesignals-bundle-") as data_dir:
        process = subprocess.Popen([str(executable), "--no-browser", "--port", str(port), "--data-dir", data_dir], stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        try:
            for _ in range(120):
                if process.poll() is not None:
                    raise AssertionError("Bundle exited before readiness")
                try:
                    with opener.open(origin + "/api/health", timeout=1) as response:
                        assert json.load(response)["mode"] == "synthetic-prototype"
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
            request = urllib.request.Request(origin + "/api/login", data=json.dumps({"email": "manager@harbour.demo", "password": "AisleDemo!2026"}).encode(), headers={"Content-Type": "application/json", "Origin": origin}, method="POST")
            with opener.open(request, timeout=5) as response:
                session = json.load(response)
                assert session["csrf_token"]
                assert session["user"]["role"] == "MANAGER"
            with opener.open(origin + "/api/bootstrap", timeout=5) as response:
                bootstrap = json.load(response)
                assert bootstrap["site"]["monthly_price_cents"] == 6000
                assert bootstrap["candidates"]
            request = urllib.request.Request(origin + "/api/logout", data=b"{}", headers={"Content-Type": "application/json", "Origin": origin, "X-CSRF-Token": session["csrf_token"]}, method="POST")
            with opener.open(request, timeout=5) as response:
                assert json.load(response)["ok"]
            print("PASS packaged executable: startup, assets, local login, scoped bootstrap, logout")
        finally:
            process.terminate()
            try:
                output, _ = process.communicate(timeout=15)
            except subprocess.TimeoutExpired:
                process.kill()
                output, _ = process.communicate(timeout=5)
            if process.returncode not in (0, -15, 1):
                print(output.decode(errors="replace")[-3000:])


if __name__ == "__main__":
    main()
