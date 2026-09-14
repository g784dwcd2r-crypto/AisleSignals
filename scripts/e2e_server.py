"""An isolated temporary database for each Playwright run; never reset user data."""
import os
from pathlib import Path
import socket
import sys
import tempfile

root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root))
test_port = int(os.environ.get("AISLESIGNALS_E2E_PORT", "8799"))
if not 1024 <= test_port <= 65535:
    raise ValueError("AISLESIGNALS_E2E_PORT must be an unprivileged TCP port")
with tempfile.TemporaryDirectory(prefix="aislesignals-e2e-") as temp, socket.socket() as unavailable_model:
    # A browser workflow test must not inherit real pilot credentials or contact
    # a developer's model service. VLM journeys explicitly mock their boundary;
    # the separate real pose negative control still loads the bundled WASM.
    for name in list(os.environ):
        if name.startswith("AISLESIGNALS_"):
            del os.environ[name]
    unavailable_model.bind(("127.0.0.1", 0))
    os.environ["AISLESIGNALS_MODE"] = "synthetic"
    os.environ["AISLESIGNALS_VISION_URL"] = f"http://127.0.0.1:{unavailable_model.getsockname()[1]}"
    os.environ["AISLESIGNALS_DB_PATH"] = str(Path(temp) / "prototype.db")
    os.environ["AISLESIGNALS_WEB_DIST"] = str(root / "apps/web/dist")
    os.environ["AISLESIGNALS_PORT"] = str(test_port)
    import uvicorn
    from services.api.app import app
    uvicorn.run(app, host="127.0.0.1", port=test_port, access_log=False, proxy_headers=False, log_level="warning")
