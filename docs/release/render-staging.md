# AisleSignals — Render staging setup

Prepared for Jawahir Q. · 13 September 2026

The staging infrastructure consists of one Free Python web service and one Free PostgreSQL 16 database in Frankfurt. The database is for infrastructure metadata only. Cloud staff login, pharmacy administration, laptop enrolment, synchronisation and evidence storage remain unimplemented. CCTV capture, model processing and recordings stay on the pharmacy laptops.

## Existing resources

| Resource                 | Value                                                             |
| ------------------------ | ----------------------------------------------------------------- |
| Project / environment    | AisleSignals / Staging                                            |
| Web service              | `aislesignals-control-staging` — `srv-dajigagae00c73a3rfsg`       |
| Database                 | `aislesignals-postgres-staging` — `dpg-dajik4fqj5pc73dp762g-a`    |
| Region                   | Frankfurt (EU Central), both resources                            |
| Public service URL       | https://aislesignals-control-staging.onrender.com                 |
| PostgreSQL               | Major version 16, Free, fixed 1 GB storage                        |
| Database expiry          | **13 October 2026**; upgrade before expiry if it must be retained |
| External database access | Blocked; PostgreSQL inbound IP list is empty                      |

The database and web service were created in the existing project through the dashboard. The checked-in `deployment/render-staging.yaml` records their intended configuration; it has not adopted these manually created resources as a managed Blueprint. Review Render's resource-matching preview before applying it, and do not create duplicates. [Render Blueprint reference](https://render.com/docs/blueprint-spec).

## Web service configuration

| Setting                        | Exact value                                                |
| ------------------------------ | ---------------------------------------------------------- |
| Repository                     | `https://github.com/g784dwcd2r-crypto/AisleSignals`        |
| Branch                         | `codex/cloud-staging`                                      |
| Runtime / compute              | Python 3 / Free                                            |
| Root directory                 | Empty: repository root                                     |
| Build command                  | `python -m pip install -r services/cloud/requirements.txt` |
| Start command                  | `python -m services.cloud.start_with_schema`               |
| Pre-deploy command             | Empty; unavailable on Free                                 |
| Health check                   | `/health/ready`                                            |
| Automatic deploy / PR previews | Off / Off                                                  |

Use only a reviewed commit that includes the explicit `start_with_schema` module. Changing environment variables with **Save only** does not update the running process; deploy the reviewed commit after saving all settings.

| Environment variable                         | Configuration                                                                  |
| -------------------------------------------- | ------------------------------------------------------------------------------ |
| `PYTHON_VERSION`                             | `3.12.14`                                                                      |
| `CLOUD_ENV`                                  | `staging`                                                                      |
| `CLOUD_DATABASE_SSLMODE`                     | `require`                                                                      |
| `DATABASE_URL`                               | The database's **internal connection URL**, saved privately in Render          |
| `PORT`, `RENDER`, `RENDER_EXTERNAL_HOSTNAME` | Supplied by Render                                                             |
| `CLOUD_ALLOWED_HOSTS`                        | Optional exact additional hostnames for deliberately configured custom domains |

The manifest's `fromDatabase.connectionString` reference contains no credential. For the current manual setup, the internal URL is stored directly in the web service's secret environment configuration. If credentials are rotated later, update this value and redeploy before revoking the old credential. Never commit URLs, passwords or laptop databases. Do not copy pharmacy camera credentials to Render.

Same-region Render services can connect through the internal URL even when public database access is blocked. TLS is required with `sslmode=require`; Render's internal database certificates are self-signed, so this encrypts transport without `verify-full` identity verification. [Render connection guidance](https://render.com/docs/postgresql-creating-connecting).

## Schema startup and health

Free web services have no dashboard shell, one-off jobs or pre-deploy command. The explicitly selected `start_with_schema` entry point therefore performs this sequence on each process start:

1. Require staging configuration and a database URL.
2. Run the existing transactional metadata migration, with an advisory lock and version/checksum validation.
3. Verify the committed schema with a fresh bounded readiness check.
4. Start the unchanged cloud server only after those steps succeed.

A failure exits with a fixed error code before serving; it does not print credentials or driver exceptions. Repeated startup is idempotent. An incompatible schema is rejected without overwriting it. The normal `python -m services.cloud` entry point still does not migrate. This explicit Free staging path is not a general production migration framework.

The only schema is `aislesignals_control.schema_version`, version 1 and its checksum. No customer, pharmacy, user or evidence tables are created. A future production deployment needs separate migration/runtime roles and a versioned migration process; the current metadata scaffold uses one credential.

| Request                                                                      | Expected result                                                                  |
| ---------------------------------------------------------------------------- | -------------------------------------------------------------------------------- |
| `GET /`                                                                      | 200; explicitly states infrastructure-only, customer access/enrolment/sync false |
| `GET /health/live`                                                           | 200; process is alive                                                            |
| `GET /health/ready`                                                          | 200 `READY`, scoped to **database-schema-only**, when the database is compatible |
| Absent/unavailable/incompatible database                                     | 503 readiness with an opaque error code                                          |
| Local pilot, login, administration, event, evidence, sync and API-doc routes | 404                                                                              |

Readiness permits one in-flight check per process, a 2.5-second outer deadline, short database timeouts and a one-second cache. It makes no detection, full-product or branch-acceptance claim. Hostnames are explicitly allowed, access logs and API docs are disabled, and responses use no-store/security headers and no cookies. No local pilot routes are imported or exposed.

After deployment, verify the actual HTTPS liveness and readiness responses and denied application routes. Record the source commit and Render deploy ID outside Git with the deployment evidence. A rollback must retain a compatible schema; never delete a database to repair a deployment.

## Free-plan limits

Both selected compute plans are Free. No paid resource, storage autoscaling, replica, connection pool or background worker is enabled by this configuration. Shared workspace usage limits still apply, and Free compute does not guarantee a zero workspace invoice. Billing and unrelated services were not changed.

**Free PostgreSQL expires after 30 days and has no backups.** The web service sleeps after inactivity. These resources are staging infrastructure, not production hosting or storage for customer data. Upgrade and verify backup/restore before customer use. [Render Free service limits](https://render.com/docs/free).

For a later paid deployment, review the actual plan quote and gain authorization before upgrading. Use a separate pre-deploy migration step and the normal non-migrating server start after production migration controls are implemented. A paid upgrade alone does not implement cloud accounts, device sync or validated detection.

## Reproduce checks

Use Python 3.12 in an isolated environment:

```sh
python3.12 -m venv .venv-cloud
.venv-cloud/bin/python -m pip install -r services/cloud/requirements-test.txt
.venv-cloud/bin/python -m pytest tests/cloud -q
.venv-cloud/bin/python -m pip check
```

On Windows use `.venv-cloud\Scripts\python.exe`. Five optional integration cases require PostgreSQL `initdb` and `pg_ctl` on a POSIX PATH:

```sh
CLOUD_RUN_POSTGRES_TESTS=1 .venv-cloud/bin/python -m pytest tests/cloud -q
```

These tests start and stop a disposable owned cluster on an unused loopback port with synthetic credentials. They never use an inherited database URL. They cover actual migration, concurrency, version/checksum refusal and lock recovery. The suite also tests startup failure sequencing, safe errors, health bounds, configuration, real HTTP startup and excluded routes. Local verification: **76 passed**, including five real PostgreSQL cases.

Validate the manifest against the official schema:

```sh
mkdir -p .local/cloud-validation
curl --fail --silent --show-error https://render.com/schema/render.yaml.json -o .local/cloud-validation/render.schema.json
.venv-cloud/bin/python tests/cloud/validate_blueprint.py .local/cloud-validation/render.schema.json
```

Schema validation checks syntax and structure, not resource adoption, account eligibility or live deployment. The separate `Cloud staging checks` workflow runs the cloud suite with PostgreSQL integration enabled.

## Application work still required

1. Staff identity, MFA/invitations, sessions, organisation and branch authority, and audit with cross-tenant denial tests.
2. Manager-authorised device enrolment, local device keys, credential expiry/revocation and replay protection.
3. Durable offline outbox and tenant-bound event ingestion, idempotent retries, acknowledgements and bounded quotas. Historical retries cannot raise a live alarm.
4. Authenticated central management interfaces, retention, support and offboarding; separately authorised evidence transfer and tested backup/restore/deletion.

These remain product-development milestones. No public first-visitor owner claim, facial watchlist, automatic footage transfer or production readiness is introduced by hosting setup.
