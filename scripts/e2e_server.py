"""An isolated temporary database for each Playwright run; never reset user data."""
import os
from pathlib import Path
import sys
import tempfile

root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root))
with tempfile.TemporaryDirectory(prefix="aislesignals-e2e-") as temp:
    os.environ["AISLESIGNALS_DB_PATH"] = str(Path(temp) / "prototype.db")
    os.environ["AISLESIGNALS_WEB_DIST"] = str(root / "apps/web/dist")
    os.environ["AISLESIGNALS_PORT"] = "8799"
    import uvicorn
    from services.api.app import app
    uvicorn.run(app, host="127.0.0.1", port=8799, access_log=False, proxy_headers=False, log_level="warning")
