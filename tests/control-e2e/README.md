# Online management browser verification

The suite uses the production management UI build and actual FastAPI/PostgreSQL
routes. Every test starts and stops its own temporary PostgreSQL cluster and API;
it never reads `DATABASE_URL`, customer accounts, existing previews or footage.
All accounts, authenticator keys, device credentials and observation metadata are
synthetic and discarded after each test.

Prerequisites: Node 24+, installed root and `apps/control` npm dependencies,
Chromium from Playwright, Python 3.12 with `services/cloud/requirements.txt` and
pytest/httpx installed, and POSIX `initdb`/`pg_ctl` on PATH. Run PostgreSQL as a
non-root user. Build the UI before testing:

```sh
npm --prefix apps/control run build
CLOUD_TEST_PYTHON=.venv312/bin/python npx playwright test --config control-playwright.config.ts
```

`CLOUD_TEST_PYTHON` defaults to `python`. `CONTROL_E2E_PORT` defaults to `60644`;
only `60644` and `60645` are permitted to avoid the local pilot previews. The
config has one worker and no persistent `webServer`. The fixture clears inherited
cloud, database and PostgreSQL environment configuration before creating a new
private cluster.

Coverage includes real setup/MFA, scoped invitation acceptance, cookie/session
logout, one-time laptop enrolment, authenticated heartbeat/observation ingestion,
review-to-incident creation, stale review rejection, revocation, 390px layout,
Axe checks, keyboard focus restoration, offline inactivity locking across reload,
and sibling-tab logout. Clipboard is replaced by an inert test implementation;
only the negative offline logout test aborts a route. No device/video/AI response
is fabricated for these management workflows. No test starts capture or audio.

Reports, traces, failure screenshots and synthetic desktop/mobile snapshots are
written under ignored `.local/control-browser-*` and `.local/control-*.png`.
These checks establish browser/API behavior, not production deployment, physical
laptop commissioning or detection accuracy.
