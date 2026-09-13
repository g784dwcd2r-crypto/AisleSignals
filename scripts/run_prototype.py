"""Run the synthetic prototype on loopback; never a public deployment entry point."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import socket
import sys
import threading
import time
import urllib.error
import urllib.request
import webbrowser


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise urllib.error.HTTPError(req.full_url, code, "Local redirects are not allowed", headers, fp)


def source_root() -> Path:
    return Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[1]))


def default_data_dir() -> Path:
    if not getattr(sys, "frozen", False):
        return source_root() / ".local"
    if sys.platform == "win32":
        return Path(os.environ.get("LOCALAPPDATA", str(Path.home() / "AppData" / "Local"))) / "AisleSignalsPrototype"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "AisleSignalsPrototype"
    return Path(os.environ.get("XDG_DATA_HOME", str(Path.home() / ".local" / "share"))) / "AisleSignalsPrototype"


def open_when_ready(url: str) -> None:
    # No proxy: this request must never leave the laptop.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    for _ in range(60):
        try:
            with opener.open(url + "/api/health", timeout=1) as response:
                if response.status == 200:
                    webbrowser.open(url)
                    return
        except (OSError, ValueError):
            time.sleep(0.25)


def main() -> int:
    parser = argparse.ArgumentParser(description="AisleSignals local synthetic prototype. No real camera monitoring.")
    parser.add_argument("--no-browser", action="store_true", help="Do not open the browser automatically")
    parser.add_argument("--port", type=int, default=8765, help="Local port; interface is always 127.0.0.1")
    parser.add_argument("--data-dir", type=Path, help="Use a separate local prototype database directory")
    args = parser.parse_args()
    if not 1024 <= args.port <= 65535:
        parser.error("Port must be between 1024 and 65535")
    root = source_root()
    sys.path.insert(0, str(root))
    web_dist = root / "apps" / "web" / "dist"
    if not (web_dist / "index.html").is_file():
        print("The web application is not built. Run npm ci in apps/web, then npm run build there.", file=sys.stderr)
        return 1
    with socket.socket() as probe:
        try:
            probe.bind(("127.0.0.1", args.port))
        except OSError:
            print(f"Local port {args.port} is in use. Stop the previous prototype or choose --port.", file=sys.stderr)
            return 1
    data_dir = (args.data_dir or default_data_dir()).resolve()
    data_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.environ["AISLESIGNALS_DB_PATH"] = str(data_dir / "aislesignals.db")
    os.environ["AISLESIGNALS_WEB_DIST"] = str(web_dist)
    os.environ["AISLESIGNALS_PORT"] = str(args.port)
    import uvicorn
    from services.api.app import app

    url = f"http://127.0.0.1:{args.port}"
    print("AisleSignals 0.1 — synthetic local prototype", flush=True)
    print("Use demo data only. No live detection, cloud AI, payments or external alarms.", flush=True)
    print(f"Open {url} — Ctrl+C closes this local server.", flush=True)
    if not args.no_browser:
        threading.Thread(target=open_when_ready, args=(url,), daemon=True).start()
    # Forwarded headers are not trusted. Access logging is disabled to avoid URL logging.
    uvicorn.run(app, host="127.0.0.1", port=args.port, access_log=False, proxy_headers=False, log_level="warning")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
