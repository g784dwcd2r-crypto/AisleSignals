# Production cloud release and recovery

Prepared for Jawahir Q. · 14 September 2026

This runbook covers the AisleSignals management console, cloud APIs and
PostgreSQL. It does not commission CCTV, detection, laptop speakers or a pharmacy.
No command in this document creates a charge automatically; match the template
to the paid resources already approved in Render before applying any change.

## Production boundary

Production has a separate Render web service, PostgreSQL database, hostname and
secrets. It runs reviewed `main` commits. Staging retains its existing resources
and `python -m services.cloud.start_with_schema` behavior. Never copy the staging
database, authentication key, bootstrap token, R2 credentials or synthetic users
into production.

The reviewable target is `deployment/render-production.yaml`:

- paid Starter web service and paid Basic 256 MB PostgreSQL in Frankfurt;
- manual deploys and disabled PR previews;
- `python scripts/build_control.py` as the build command;
- `python -m services.cloud.prepare_release` as the pre-deploy command;
- `python -m services.cloud.start_checked` as the server command;
- `/health/ready` as the Render health check;
- PostgreSQL external IP allow-list empty and storage autoscaling enabled.
- Render environment network isolation and destructive-action protection enabled.

The template is not proof of current Render settings. Preview it in the existing
AisleSignals project and match resources by their actual IDs; do not create
duplicates to make the names align.

## Secrets and configuration

Set secret values only in Render's protected environment controls:

| Variable | Production rule |
| --- | --- |
| `CLOUD_ENV` | `production` |
| `CLOUD_DATABASE_SSLMODE` | `require`; production rejects `disable` |
| `DATABASE_URL` | private production PostgreSQL URL containing a password |
| `CLOUD_AUTH_KEY` | canonical base64url 32-byte key, backed up with the database recovery materials |
| `CLOUD_BOOTSTRAP_TOKEN` | independent 43–128 character URL-safe token; remove after first owner setup |
| `CLOUD_EVIDENCE_MODE` | keep `METADATA_ONLY` until the separate R2 gates pass |
| `CLOUD_ALLOWED_HOSTS` | production custom hostname only; Render adds its own service hostname |
| `RENDER_GIT_COMMIT` | provider-supplied exact deploy SHA; production refuses missing or malformed values |

Render supplies `RENDER_EXTERNAL_HOSTNAME` and `RENDER_GIT_COMMIT`; do not set or
override them. Extra custom domains must be explicit in `CLOUD_ALLOWED_HOSTS`.
Production refuses to load without its database, authentication key and valid
lowercase 40-hex release SHA. `CLOUD_RELEASE_SHA` exists only for explicit
non-Render test processes and is rejected when `RENDER=true`. Secure cookies,
same-origin mutation checks and HSTS apply to both production and staging. Secret
values must not appear in Git, command arguments, screenshots, tickets or release
evidence.

If encrypted evidence is approved later, add the complete R2 policy, keyring,
current key version, EU bucket and scoped S3 credentials atomically in one
configuration update. Follow `cloud-evidence-r2.md`; a partial evidence setup
fails closed.

## Release procedure

1. Merge reviewed changes into `main` after all required ordinary checks pass.
   Record the exact 40-character head SHA.
2. Run **Production release gate** with that SHA. It requires the SHA to still be
   the current `main`, runs application, cloud/PostgreSQL, control and browser
   suites, and emits a non-deployment checksum artifact.
3. Confirm the paid production database backup/PITR state and create a manual
   pre-release backup. Record its provider identifier, UTC time and retention.
4. Review all migrations after the deployed schema and confirm the previous
   application is compatible with the resulting schema.
5. Manually deploy the exact gated SHA. The pre-deploy process runs migrations
   under the existing advisory lock, then opens a fresh bounded connection and
   verifies the exact cumulative schema checksum. Any failure stops deployment.
6. The production server opens another fresh bounded readiness check. It never
   runs migrations. A skipped or failed pre-deploy step therefore cannot be
   hidden by application startup.
7. Record the Render deploy ID, service/database IDs, hostname, SHA and UTC time.
   Run `deployment/verify_cloud_release.py --expected-sha <gated-sha>` against
   the production HTTPS origin. It requires `/health/release` to report that
   exact backend SHA and archives the result outside the running service.
8. Sign in using a named owner and MFA for a read-only dashboard check. Run a
   labelled end-to-end observation only from an authorised commissioned test
   device under the applicable retention procedure.

Run the lightweight public monitor independently of the deployed service. Pin
the last accepted production SHA so a healthy but unintended release fails:

```sh
python deployment/check_cloud_health.py \
  --origin https://control.example.ie \
  --expected-sha 0123456789abcdef0123456789abcdef01234567
```

Configure Render deploy-failure and service-health notifications to a monitored
owner channel. The probe proves only process liveness, exact release identity and
database-schema readiness. It does not prove login, notification delivery,
camera coverage, backups or detection quality.

## Backups and measured restore drills

Paid Render PostgreSQL supplies point-in-time recovery. Before every release,
record the available recovery window and create a Render logical export. At
least quarterly, and after a schema change, restore into a new isolated drill
database. A recovery instance is a separate paid resource, so its creation is an
operator action rather than part of the Blueprint or CI.

The repository tool provides a second, explicit logical-backup and restore test.
It accepts database passwords only through environment variables and never puts
them in process arguments:

```sh
DATABASE_URL='postgresql://…?sslmode=require' \
  python scripts/cloud_recovery.py backup --output /private/encrypted/aislesignals.dump
python scripts/cloud_recovery.py verify --archive /private/encrypted/aislesignals.dump
DATABASE_URL='postgresql://source…?sslmode=require' \
RECOVERY_DATABASE_URL='postgresql://empty-drill…?sslmode=require' \
  python scripts/cloud_recovery.py restore-drill --archive /private/encrypted/aislesignals.dump
```

The dump and adjacent manifest contain sensitive customer data and metadata.
Store both on access-controlled encrypted storage, never in Git or the deployed
web service. `restore-drill` rejects the source database and any non-empty target,
uses a single transaction, then verifies the exact cumulative migration version
and checksum. Preserve the private command log, archive digest, source backup ID,
target database ID, start/end UTC times, operator and result as release evidence.
Delete the drill database only after evidence is reviewed and within the agreed
retention procedure.

## Rollback and recovery

If pre-deploy migration fails, keep the previous service release running and
inspect private logs. Never edit an installed migration, reset the schema or
delete data as deployment repair.

If migration succeeds but the application fails, use the previous immutable
application only when it understands the newly installed schema. Otherwise ship
a reviewed forward fix. Re-run readiness, anonymous-denial and authenticated
read checks after recovery.

If data integrity is in doubt, stop promotion and writes, preserve audit/provider
logs, and restore the accepted backup into a separate empty drill database. Do
not restore over production. Verify schema version/checksum and representative
authorised records, reconcile deletions and revocations newer than the backup,
then obtain controller approval for any production recovery. Rotate sessions and
device credentials after an approved restore.

Render backup retention, PITR, an isolated restore drill, exact live resource
matching, GitHub branch protection and production approval rules remain external
release gates until their evidence is recorded.

Render references: [Blueprint specification](https://render.com/docs/blueprint-spec),
[health checks](https://render.com/docs/health-checks), and
[PostgreSQL recovery and backups](https://render.com/docs/postgresql-backups).
Domain and Cloudflare setup is specified in
[`production-domain-cloudflare.md`](production-domain-cloudflare.md).
