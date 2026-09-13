# AisleSignals cloud staging

Prepared for Jawahir Q. · 13 September 2026

This is a separate, deployable **infrastructure scaffold**. It exposes process health and optional PostgreSQL schema readiness. It does not provide cloud login, pharmacy administration, laptop enrolment, event synchronisation, evidence storage or detection. The existing pharmacy application and CCTV/model processing stay on the laptops. A successful Render deployment does not establish a working central management product.

## Current free deployment

`deployment/render-staging.yaml` proposes exactly one **Free** Python web service in Frankfurt. It creates no database, disk, worker or account, and disables automatic deploys and previews. The coordinator has already created the AisleSignals project and Staging environment; service activation and live acceptance are separate from this source change. Review the existing project and service names before applying a Blueprint so unrelated resources are not adopted. [Render Blueprint reference](https://render.com/docs/blueprint-spec).

| Render setting              | Exact value                                                                     |
| --------------------------- | ------------------------------------------------------------------------------- |
| Project / environment       | AisleSignals / Staging                                                          |
| Service name                | `aislesignals-control-staging`                                                  |
| Repository                  | `https://github.com/g784dwcd2r-crypto/AisleSignals`                             |
| Branch                      | `codex/cloud-staging`, after the coordinator publishes the reviewed cloud files |
| Runtime / region / instance | Python 3 / Frankfurt / Free                                                     |
| Root directory              | Empty: repository root                                                          |
| Build command               | `python -m pip install -r services/cloud/requirements.txt`                      |
| Start command               | `python -m services.cloud`                                                      |
| Pre-deploy command          | Empty                                                                           |
| Health check path           | `/health/live`                                                                  |
| Automatic deploy            | Off; select the reviewed commit manually                                        |

The Python entry point starts one Uvicorn process, binds Render's `PORT` on `0.0.0.0`, and uses a ten-second graceful shutdown window. It does not start laptop companions or models. This follows Render's native [FastAPI deployment pattern](https://render.com/docs/deploy-fastapi).

| Environment variable       | Value / behaviour                                                                                                              |
| -------------------------- | ------------------------------------------------------------------------------------------------------------------------------ |
| `PYTHON_VERSION`           | `3.12.14`, explicitly pinned in the manifest                                                                                   |
| `CLOUD_ENV`                | `staging`                                                                                                                      |
| `CLOUD_DATABASE_SSLMODE`   | `require`                                                                                                                      |
| `DATABASE_URL`             | **Unset** for this milestone. Never paste a laptop SQLite path here.                                                           |
| `PORT`                     | Render supplies it; default `10000` for local checks                                                                           |
| `RENDER`                   | Render supplies `true`; public container binding requires it                                                                   |
| `RENDER_EXTERNAL_HOSTNAME` | Render supplies the exact service hostname, which becomes an allowed Host                                                      |
| `CLOUD_ALLOWED_HOSTS`      | Optional comma-separated exact additional hostnames, only when deliberately adding custom domains; no URLs, ports or wildcards |

Render supports a fully specified [`PYTHON_VERSION`](https://render.com/docs/python-version). Its [default environment variables](https://render.com/docs/environment-variables) include the service hostname. Configure any custom domain before using it: Render may also use that domain as the health-check Host.

## What the endpoints mean

| Request                                                      | Result                                                                                                         |
| ------------------------------------------------------------ | -------------------------------------------------------------------------------------------------------------- |
| `GET /`                                                      | Static, public status describing this infrastructure-only stage; customer access, enrolment and sync are false |
| `GET /health/live`                                           | `200`, process alive; makes no database or product-readiness claim                                             |
| `GET /health/ready`, no DB                                   | `503`, `DATABASE_NOT_CONFIGURED`                                                                               |
| `GET /health/ready`, compatible DB                           | `200`, `READY`, explicitly scoped to `database-schema-only`                                                    |
| Unavailable, incompatible or timed-out DB                    | `503`, opaque status code; no hostname, credentials or driver exception                                        |
| Laptop API, account, sync, evidence and documentation routes | `404`                                                                                                          |
| Writes to health endpoints                                   | `405`                                                                                                          |

Readiness allows one in-flight check per process, no queue or persistent connection pool, a 2.5-second outer deadline, short PostgreSQL query/connection timeouts and a one-second result cache. Concurrent cache misses receive `READINESS_BUSY`. These bounds fit within Render's five-second [HTTP health-check deadline](https://render.com/docs/health-checks). In this deliberately database-free stage, Render checks **liveness**, while the distinct readiness endpoint stays unavailable. After a database-backed feature is introduced, change the deployment health path to `/health/ready` only after its schema and failure checks pass.

The runtime disables access logs, proxy-header trust, interactive API documentation and cookie/session creation. Responses are non-cacheable, do not permit CORS, and carry restrictive content/security headers. Hostnames are explicitly allowed; configuration errors omit values. There is no file mount, SQLite import, model package or dependency on `services.api`. These are infrastructure protections, not a substitute for authenticated product access.

## Later PostgreSQL step — not activated by this manifest

The driver and explicit migration command are implemented and tested. There is currently **no Render database** provisioned by this workstream. A later approved database should be in Frankfurt with the application, use its internal connection string, and set `ipAllowList: []` to deny external database connections. Keep the URL in Render's secret environment configuration. Internal connections support `sslmode=require` with Render's self-signed certificates; this encrypts transport but does not verify server identity with `verify-full`. [Render PostgreSQL connection guidance](https://render.com/docs/postgresql-creating-connecting).

The proposed paid minimum is a web instance `0.5c-512mb` plus PostgreSQL `0.1c-256mb`, PostgreSQL major version **16** (the locally tested major), `diskSizeGB: 1`, and storage autoscaling disabled. These are a review proposal, not an instruction to activate them. Do not add a free expiring database for customer data. The current [Render pricing](https://render.com/pricing) lists $7/month web compute and $6/month database compute; database storage is listed separately at $0.30/GB/month in Render's [cost guide](https://render.com/articles/how-much-does-cloud-application-hosting-cost-for-small-businesses). Budget roughly **US$13.30/month** for this minimal pair with 1 GB storage, subject to the actual checkout quote, workspace charges, taxes, currency conversion and usage. It is not a production capacity estimate.

For a paid web service, configure the pre-deploy command as:

```sh
python -m services.cloud.migrate
```

Render runs [pre-deploy commands](https://render.com/docs/deploys#pre-deploy-command) separately before starting the new version; this facility is not available on Free web services. The migration never runs automatically during app startup. It takes a PostgreSQL transaction/advisory lock, creates only `aislesignals_control.schema_version`, and records version 1 and its migration checksum. Repeated and concurrent runs are safe. An incompatible version/checksum fails without rewriting it. There is no automatic downgrade or destructive reset. Readiness remains unavailable for an absent or incompatible schema.

Before storing any customer data, separate migration and runtime database roles, verify backup/restore and retention, and implement the product access controls below. This metadata-only scaffold uses one database credential; it is not a completed tenancy schema or migration framework.

## Free-plan limits and spending boundary

The checked-in service has $0 compute, but this does **not** guarantee a $0 workspace invoice. Free services consume shared free hours, bandwidth and build allowances; excess usage can incur charges where billing is enabled, or cause suspension according to account limits. Review the workspace's limits before activation. Free services sleep after inactivity and are not production hosting. A Free PostgreSQL database expires after 30 days and has no backups; this manifest does not create one. [Render Free service limits](https://render.com/docs/free).

The current workspace payment warning is an account prerequisite for the owner to resolve if Render prevents the requested free setup. Do not change unrelated services, enter payment details, create paid resources or upgrade the workspace as part of this scaffold.

## Reproduce checks

Use Python 3.12 in an isolated environment. Dependencies are separate from the laptop application and are pinned; Psycopg's [binary distribution](https://www.psycopg.org/psycopg3/docs/basic/install.html) provides its runtime library without a system compiler/libpq build step.

```sh
python3.12 -m venv .venv-cloud
.venv-cloud/bin/python -m pip install -r services/cloud/requirements-test.txt
.venv-cloud/bin/python -m pytest tests/cloud -q
.venv-cloud/bin/python -m pip check
```

On Windows use `.venv-cloud\Scripts\python.exe`. The ordinary suite covers configuration, real HTTP startup, absent routes, local-API isolation, safe errors, caching, cancellation, timeouts and concurrent probe bounds. Five optional tests require PostgreSQL `initdb` and `pg_ctl` on a POSIX PATH:

```sh
CLOUD_RUN_POSTGRES_TESTS=1 .venv-cloud/bin/python -m pytest tests/cloud -q
```

The opt-in tests create a disposable, process-owned PostgreSQL cluster with synthetic credentials on an unused loopback port and stop it afterward. They never connect to an inherited `DATABASE_URL` or the user's existing database. They cover actual migration, repeated/concurrent execution, version/checksum refusal and blocked-query recovery. Native verification here used PostgreSQL 16.15 and Python 3.12.14: **62 passed**, including the five database checks; the ordinary run skips only those five. Existing test-client dependency deprecation warnings do not change the result.

Validate the manifest against a separately downloaded official schema, without using a Render account:

```sh
mkdir -p .local/cloud-validation
curl --fail --silent --show-error https://render.com/schema/render.yaml.json -o .local/cloud-validation/render.schema.json
.venv-cloud/bin/python tests/cloud/validate_blueprint.py .local/cloud-validation/render.schema.json
```

The schema validation passed. It checks structure, not account access, project adoption, branch publication, available capacity or successful deployment. After an authorised deployment, verify the actual URL, expected 200/503 split and denied laptop/account routes. Record the deployed commit. A rollback must select a compatible schema version; never delete a database to repair a failed health check.

## Subsequent secure product milestones

1. **Staff identity and branch authority:** managed OIDC/MFA, explicit owner provisioning, expiring invitations, server-side sessions and CSRF, organisation/branch enforcement and audit. No public first-visitor claim and no reuse of laptop bootstrap codes as cloud credentials. Test cross-organisation and revoked-access denials before enabling account routes.
2. **Device enrolment:** locally generated device keys, one-time manager-authorised enrolment, site-bound credentials, expiry/revocation and replay protection. Camera passwords remain on laptops. Test stolen/reused enrolment codes and revoked devices.
3. **Event synchronisation:** versioned metadata contract, durable local outbox, idempotent tenant-bound ingestion, acknowledgements/conflicts, retry/backoff, offline recovery and bounded quotas. Historical retries cannot trigger a live alarm. Test concurrent replay and branch mismatch; no footage upload by default.
4. **Central workflow and evidence scope:** authenticated, scoped manager UI; agreed event retention; minimisation and masks before any separately authorised evidence transfer; encrypted storage and verified restore/deletion. Reconcile central and laptop authority explicitly before deploying to six branches.

These milestones are planned, not implemented by this scaffold. No user-facing cloud management, continuous monitoring, detection accuracy or branch acceptance is claimed.
