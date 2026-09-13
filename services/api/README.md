# Local synthetic API

This is the implemented Prototype 0.1 API, not the complete production OpenAPI handover. It persists synthetic review work in a local SQLite database and serves the built React application from the same origin. No camera, cloud AI, physical output or billing provider is called.

From the repository root:

```sh
python -m pip install -r services/api/requirements.txt
python -m uvicorn services.api.app:app --host 127.0.0.1 --port 8765 --no-proxy-headers
python -m pytest tests/api -q
```

Use the root prototype launcher for the combined application. Pharmacy users are not expected to set up Python for the planned packaged delivery.

Configuration:

| Variable | Default | Behaviour |
| --- | --- | --- |
| `AISLESIGNALS_MODE` | `synthetic` | Any other value refuses startup. |
| `AISLESIGNALS_DB_PATH` | `.local/aislesignals.db` | Persistent local SQLite file; parent directories created. |
| `AISLESIGNALS_WEB_DIST` | `apps/web/dist` | Static application files; unknown API paths never receive SPA HTML. |
| `AISLESIGNALS_PORT` | `8765` | Exact accepted local runtime port, 1–65535; set it before importing the module when overriding Uvicorn's port. |

Only localhost/loopback Host names and explicit local browser origins are accepted. The configured runtime port and Vite's `5173` port form the allowlist. Bind the server to `127.0.0.1`; it is not a network deployment. Forwarded headers are rejected, so reverse-proxy hosting is outside this prototype. Vite's development proxy must preserve the local Origin and must not add forwarded headers.

Demo users and the deliberately public password are in `docs/prototype/contract.md`. Cookie authentication is local HTTP, `HttpOnly`, host-scoped and `SameSite=Strict`. It is not production OIDC/MFA. Session tokens are random and only their SHA256 hashes are persisted. Passwords use a salted PBKDF2 hash. Failed sign-ins are bounded by account and client address. Writes require both an allowed Origin and session CSRF token. Passive bootstrap/image polling does not renew the 15-minute idle timeout; session restoration and deliberate writes do. Sessions also expire absolutely after eight hours. No secrets are returned by bootstrap or written to the audit history.

Each database read and write binds both organisation and site from the server-side session. Writes use SQLite `BEGIN IMMEDIATE` transactions, parent versions for concurrency, and persistent idempotency keys on creates and candidate review. Simulator source event IDs also have a separate site-scoped deduplication record. Future schema versions refuse startup rather than downgrade a database; seeding is idempotent and atomic. This is application-level isolation, not production PostgreSQL RLS.

The implementation supports the login/session/bootstrap, shift, simulator, candidate acknowledgment/review, manual incident, case update/close/reopen, task, draft/approval/export, assistance and synthetic-evidence routes in the shared prototype contract. New simulated observations require a review shift; current observations also need an available simulated source. Health changes and manual work remain available when a shift is inactive. Historical events are marked explicitly.

A closed incident's facts and tasks are locked until a manager reopens it. Staff may generate and approve a final template for a closed incident. Fact, status or task changes invalidate the prior draft. Templates use structured case facts and omit free-text allegations; they cost zero and are labelled `LOCAL_TEMPLATE`, not AI inference. Manager-only JSON exports contain the structured record, history, synthetic provenance and a reproducible SHA256 digest. They never send to an external recipient.

Evidence is an authenticated, fixed SVG schematic, not footage. Access expires 72 hours after receipt; missing or expired evidence returns 404 and is shown as unavailable in bootstrap. This access window is not an implementation of production media-deletion workers. All responses, including handled errors and evidence, are marked `no-store`; the API has no public or query-token media route.

Runtime tests cover duplicate and simultaneous review, retry conflicts, stale versions, monetary constraints and roles, complete case and report flows, separate-organisation and same-organisation/different-site access, source outage/frozen/recovery, historical observations, assistance transitions, cookies/CSRF/origin/forwarded headers, idle and absolute expiry, access expiry, restart persistence, configuration, schema refusal and static path boundaries. These checks do not validate live cameras, managed MFA, production retention, encrypted at-rest evidence, backup recovery or pharmacy laptop acceptance.
