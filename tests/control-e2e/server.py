"""Synthetic browser fixture: a process-owned cloud API and PostgreSQL cluster.

No existing database or customer environment is read. Not a production entrypoint.
"""
import json
import os
from pathlib import Path
import sys

root = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(root))
sys.path.insert(0, str(root / "tests" / "cloud"))
for key in list(os.environ):
    if key.startswith(("CLOUD_", "AISLESIGNALS_", "DATABASE_", "PG")):
        del os.environ[key]
port = int(sys.argv[1])
if port not in {60644, 60645}:
    raise ValueError("Only isolated browser fixture ports 60644 and 60645 are permitted")
from control_test_support import disposable_postgres, reset_database
from services.cloud.app import create_app
import uvicorn

with disposable_postgres() as settings:
    reset_database(settings)
    print("CONTROL_READY " + json.dumps({"url": f"http://127.0.0.1:{port}", "bootstrapToken": settings.bootstrap_token}), flush=True)
    uvicorn.run(create_app(settings), host="127.0.0.1", port=port, access_log=False, proxy_headers=False, log_level="warning")
