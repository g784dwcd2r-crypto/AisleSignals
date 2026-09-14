# Cloud deployment audit and release evidence

Prepared for Jawahir Q. · 14 September 2026

This is a read-only cloud/release audit. It does not deploy, merge, change Render
settings, access customer records or certify pharmacy CCTV and alarm operation.

## Observed state

Repository `origin/main` was `486e836a6721560dfde37981525926ca8ac4f564`.
There were no open pull requests. The latest application workflow on that commit
failed only in the macOS browser job: 92 journeys passed and the linked-case
journey timed out after clicking **Open linked case**. The same Ubuntu and Windows
browser jobs, all three Python jobs and the pinned Windows model-runtime job
passed. The latest cloud workflow applicable to the current cloud sources passed
at `6ee1fa1f2e3531e946601f51ab11d8aa14b173ec`; the later commits changed desktop
packaging paths and did not trigger that path-filtered workflow.

The public staging origin
`https://aislesignals-control-staging.onrender.com` returned:

| Check | Observed result |
| --- | --- |
| `/health/live` | 200 with the management-console contract |
| `/health/ready` | 200 `READY`, explicitly database-schema-only |
| `/control-api/setup/status` | 200, configured owner present and setup closed |
| anonymous `/control-api/session` | 401 `SESSION_REQUIRED` |
| local pilot `/api/health` | 404 |
| browser hardening | HSTS, no-store, CSP, frame denial, MIME denial and camera/microphone/display-capture denial present |
| frontend release | Exact SHA-256 match for `index.html`, JavaScript and CSS built from the audited main checkout |

This proves public availability, the narrow readiness contract, frontend source
parity and anonymous denial at the observation time. That historical deployment
did not expose the release identity route added by this change, so its backend
commit remains unverified until a new candidate is deployed and audited.

## Repository fixes in this change

`deployment/verify_cloud_release.py` makes that public audit repeatable. It accepts
only a clean HTTPS origin, applies bounded requests without redirects, verifies
the public fail-closed API contracts and security headers, and compares every
file in a locally built management console byte-for-byte with the deployed files.
The `/health/release` contract contains only the validated environment and
40-character commit SHA. With `--expected-sha`, the verifier requires a clean
tracked checkout at that exact commit and an exact match from the deployed
backend. Its optional JSON record contains no cookies, credentials or response
content.

The manual **Production release gate** workflow checks out only the exact current
head of `main`, runs application, cloud/PostgreSQL, control and browser suites,
then records dependency, migration and staging-manifest hashes. It never deploys.
GitHub branch protection must require the ordinary checks before merging and an
operator must run this manual gate against the final main SHA before deployment.

The prerequisite linked-case fix preserves the selected case when a background
refresh overlaps asynchronous navigation and includes a deterministic overlap
test. This deployment workstream adds no timeout workaround and keeps the case,
evidence, deletion, accessibility and idempotency assertions unchanged.

Build and audit staging from a clean candidate checkout:

```sh
python scripts/build_control.py
python deployment/verify_cloud_release.py \
  --base-url https://aislesignals-control-staging.onrender.com \
  --web-dist apps/control/dist \
  --expected-sha "$(git rev-parse HEAD)" \
  --require-configured \
  --output .local/staging-release-audit.json
```

## External production blockers

The historical staging manifest still names a Free web service, Free PostgreSQL
and `codex/cloud-staging`, so it cannot prove the reported paid upgrade. This
release adds a separate paid production template and production runtime; neither
has been applied to Render. No production Render hostname, service ID, database
ID, deploy ID, environment inventory, backup policy or restore result is recorded
in the repository or exposed by the public staging service.

Before a production deployment, an authorised operator must provide or record:

1. The exact production and staging Render service/database IDs, hostnames,
   plans, regions, deploy branches and current deploy SHAs.
2. Database backup retention, encryption, point-in-time recovery and an actual
   isolated restore-drill result.
3. Separate production secrets, production R2 resources and KEK custody; secret
   values must never enter the audit record.
4. GitHub main-branch protection and a protected production environment with
   required reviewers. The audit found main unprotected, no repository ruleset,
   and no GitHub Actions environment.
5. A successful post-deploy audit proving that `/health/release`, the frontend
   files and the clean local checkout all match the exact gated SHA.

Until these are observed, staging is healthy but production deployment and
recovery remain unverified.
