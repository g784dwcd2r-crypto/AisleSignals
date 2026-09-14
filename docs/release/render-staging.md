# AisleSignals management console — Render staging

Prepared for Jawahir Q. · 14 September 2026

This release replaces the infrastructure JSON root with a same-origin React management interface and PostgreSQL-backed identity and operations APIs. The console has Overview, Pharmacies, Laptops, Alerts, Incidents and Team & access views. Login requires a password and authenticator code. An owner can create pharmacies, issue scoped staff invitations and disable access. Staff review received alerts into incidents; observations are not findings of theft.

## Existing resources and intended settings

Use the existing AisleSignals / Staging resources; do not create duplicates or apply a Blueprint without reviewing resource matching.

| Setting | Value |
| --- | --- |
| Web service | aislesignals-control-staging · srv-dajigagae00c73a3rfsg |
| URL | https://aislesignals-control-staging.onrender.com/ |
| Database | aislesignals-postgres-staging · dpg-dajik4fqj5pc73dp762g-a |
| Region / runtime | Frankfurt · Python 3 · PostgreSQL 16 |
| Compute | Existing Free web and database |
| Repository branch | codex/cloud-staging |
| Root directory | Empty, repository root |
| Build command | `python scripts/build_control.py` |
| Start command | `python -m services.cloud.start_with_schema` |
| Health check | `/health/ready` |
| Automatic deploy / PR previews | Off / Off |
| Database ingress | Internal connection; external IP allow-list empty |

The manifest records intended configuration; manually created resources are not automatically Blueprint-managed. Save configuration without triggering intermediate deployments, then deploy the exact reviewed commit after CI succeeds. Settings and a checked-in manifest alone do not prove the running service uses them.

Environment: `PYTHON_VERSION=3.12.14`, `NODE_VERSION=24.14.1`, `CLOUD_ENV=staging`, `CLOUD_DATABASE_SSLMODE=require`. Preserve the existing private internal `DATABASE_URL`. Render supplies PORT, RENDER and RENDER_EXTERNAL_HOSTNAME. Additional custom hostnames require deliberate `CLOUD_ALLOWED_HOSTS` configuration.

Privately configure `CLOUD_AUTH_KEY` and `CLOUD_BOOTSTRAP_TOKEN` as described in [cloud-identity.md](cloud-identity.md). Never include these values in Git, URLs, logs or screenshots. Preserve the encryption key with database recovery material; do not replace it on redeployment. Remove the bootstrap token after the first owner has completed password and authenticator setup. The service never seeds customer accounts or default passwords.

## Schema and deployment checks

The explicit staging startup command upgrades the existing v1 schema through the identity and operations migrations under one PostgreSQL advisory lock and transaction. Cumulative checksums reject altered prior migrations and incompatible versions. A bounded readiness check must pass before the server listens. The ordinary `python -m services.cloud` command does not migrate. Use a separate reviewed migration/runtime role and backup procedure for production.

| Check | Expected |
| --- | --- |
| GET / | 200 HTML titled AisleSignals, linked self-hosted JS/CSS |
| GET /health/live | 200, management-console |
| GET /health/ready | 200 READY, scope database-schema-only |
| GET /control-api/setup/status | Configuration/setup state only, no credentials |
| GET /control-api/session without login | 401, no records |
| Browser mutations without exact Origin | Rejected |
| Staff mutations without current CSRF | Rejected |
| Local pilot / video / model routes | Absent |

Record the deployed source commit and Render deploy ID with external deployment evidence. Do not roll back to a server that only understands schema v1 after migration v3. Prefer a compatible forward fix; never delete or reset the database as deployment repair.

The management console cannot acquire footage, start detection or activate laptop speakers. `/health/ready` checks database compatibility, not MFA completion, device availability or pharmacy acceptance. All dashboard counts come from authenticated PostgreSQL records; a new workspace starts empty. Synthetic demo data belongs only in explicitly labelled local tests.

## Laptop reporting

Owners/managers can create ten-minute, single-use enrolment codes for a specific pharmacy, laptop name and platform. The device API uses hashed bearer credentials, revocation, scoped authority, durable request identifiers and heartbeat sequence checks. Devices cannot read staff notes, case records or user management. Offline and revoked devices are displayed distinctly from connected devices. Stale connection reports do not imply detection is running.

The optional attended Python 3 helper reports connection presence only and sends monitoring UNKNOWN with zero cameras. It does not capture CCTV, send alerts, start at login or run as a managed background service. The currently deployed version at51a3817 supports macOS/Linux private storage.

The next connection-tool candidate adds a native Windows NTFS credential/sequence adapter and a fixed two-file ZIP download from the Laptops view. Keep both extracted Python files together; Windows instructions use `py -3`, and Mac/Linux instructions use `python3`. Existing private files and permissions are preserved. A missing adapter, unsafe ACL, reparse point, unsupported drive or malformed state refuses connection before credentials are used. This candidate must pass native Windows CI and independent review before deployment; local policy tests or browser setup cannot establish Windows compatibility. See `windows-cloud-storage.md` for its exact trust boundary and limits.

The new bearer-authenticated `GET /device-api/identity` is a read-only prerequisite for later explicit branch mapping. It returns only the current device's eight server-bound identity fields, without refreshing activity or changing the enrolment response. See `device-identity.md`. The local confirmation flow, durable observation outbox and cloud withdrawal/retention remain unimplemented; see `cloud-sync-design.md`. Pairing this connection helper does not imply observation sync.

## Verification

Use Python 3.12, Node 24 and PostgreSQL initdb/pg_ctl available on PATH:

```sh
python3.12 -m venv .venv-cloud
.venv-cloud/bin/python -m pip install -r services/cloud/requirements-test.txt
npm ci
npm ci --prefix apps/control
npm --prefix apps/control run build
npm --prefix apps/control test
npm --prefix apps/control run format:check
CLOUD_RUN_POSTGRES_TESTS=1 .venv-cloud/bin/python -m pytest tests/cloud -q
CLOUD_TEST_PYTHON=.venv-cloud/bin/python npx playwright test -c control-playwright.config.ts
```

The suites use process-owned disposable PostgreSQL clusters and synthetic accounts. They test MFA and setup races, tenant isolation, current authority, CSRF, replay, private HTTP responses, device enrolment/revocation, alert deduplication, review/case transactions, browser journeys, session expiry and mobile accessibility. Local and CI evidence are distinct from a live deployment and branch acceptance.

## Remaining before customer operation

Free PostgreSQL expires on **13 October 2026** and has no backups. The Free web service sleeps after inactivity. No paid resource or billing change is authorized by this release. Upgrade only with owner approval, then test backup plus encryption-key restore before storing customer records. See [Render Free limits](https://render.com/docs/free).

Password changes/reset, lost-authenticator recovery, SSO, automatic invitation email, audit browsing/export, retention/offboarding and key rotation are not implemented in this milestone. Keep an additional enrolled owner and define verified recovery without bypassing MFA. Native laptop event sync and real branch pairing are not completed by the cloud UI. Camera quality, detection accuracy and physical alarms still require observed acceptance at each pharmacy; six branches remain unaccepted until evidence is recorded.
