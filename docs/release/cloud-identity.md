# Cloud identity and owner administration

Implemented for the separate AisleSignals management console, using PostgreSQL. The console does not mount the laptop API, read its SQLite database, receive video or activate camera monitoring. This document describes implementation and synthetic test evidence; it does not establish production operational readiness.

## Deployment configuration

The existing cloud host, TLS/database and exact-host checks remain required. Two additional secrets are configured privately in the hosting environment:

- `CLOUD_AUTH_KEY`: a canonical, padded URL-safe base64 encoding of 32 random bytes. Generate with `cryptography.fernet.Fernet.generate_key()` in a trusted administrative environment. Preserve this key securely with the database recovery materials: it encrypts authenticator secrets and enrolment challenges. An arbitrary replacement key will make existing MFA secrets unreadable; automatic key rotation is not implemented.
- `CLOUD_BOOTSTRAP_TOKEN`: an independently generated URL-safe random token of at least 32 bytes (`secrets.token_urlsafe(32)` produces 43 characters). It authorizes first-owner setup only. It is never included in HTML, a status response, a URL query, source control or application logs. Remove it from the deployment environment after first-owner setup succeeds. Removing or changing it invalidates unfinished setup challenges.

Without an auth key or database, identity/control actions fail closed. Without a bootstrap token, a previously empty service cannot create its first owner. Existing configured owners can sign in after the bootstrap token is removed. `/health/ready` checks schema readiness; it does not certify that account setup, recovery, deployment billing or laptop commissioning is complete.

Schema migrations are explicit. The existing `001_control_plane.sql` remains byte-for-byte compatible with the deployed v1 checksum. `002_identity.sql` adds empty identity tables; `003_operations.sql` adds the management records. Later migration checksums include their predecessor checksum, detecting edits to an older migration even when the database is already at the latest version. Migration and readiness reject an unknown version or checksum. Upgrades run in one PostgreSQL transaction under an advisory lock, with statement/lock timeouts. No migration seeds an organisation, pharmacy or user.

The existing explicit staging command, `python -m services.cloud.start_with_schema`, performs the migration and verifies readiness before serving. The ordinary `python -m services.cloud` entry point still does not migrate automatically. Use a backed-up, reviewed migration procedure before storing real customer records; an expiring Free staging database is not a durable customer deployment.

## First owner and invited users

The route contract is in [control-api.md](control-api.md). All browser mutations require an exact same-origin `Origin`. Authenticated mutations additionally require `X-CSRF-Token` from the current authenticated session.

1. The owner enters the private bootstrap token, organisation name, their name, email and a password of 12–128 characters.
2. The service returns a five-minute, opaque enrolment challenge and a new authenticator secret/`otpauth` URI. A PostgreSQL row stores the challenge token hash and an encrypted pending payload, including the password hash. No user or organisation exists yet.
3. The owner enters a valid six-digit authenticator code. An atomic first-owner claim creates the organisation and enabled owner, consumes the challenge and issues a fresh session. Concurrent successful claim attempts create exactly one organisation and owner.
4. The owner creates pharmacies and privately shares expiring invitation links with staff. Invitations are issued manually; the application sends no email. Each invitation fixes its organisation, role and permitted pharmacy IDs on the server.
5. The invited person chooses a password and configures their authenticator before the invitation can create an account. Invitations expire after 24 hours and are consumed once, at successful MFA completion. Reissuing an invitation revokes the previous pending invitation for that email. Disabling or demoting the issuing owner invalidates their pending invitations.

An invitation is a bearer capability. The UI presents it once for deliberate private sharing and keeps it out of query strings and persistent browser storage. No shared/default passwords are supplied. An account requires authenticator verification on every new sign-in; password verification alone grants no session or access to management records.

## Passwords, MFA and sessions

Passwords use independently salted PBKDF2-HMAC-SHA256 with 600,000 iterations. Unknown accounts perform a dummy password derivation and return the same credential failure message. A process semaphore limits password derivation to two concurrent operations; PostgreSQL fixed-window attempt counters are committed before credential verification, so failure counters survive restarts. Per-account/challenge limits and a per-action global limit bound attempts and work. Counter keys are HMAC digests and the counter table has a 4,096-bucket ceiling. A limit failure returns HTTP 429; unbounded random subjects do not grow this table indefinitely.

TOTP follows RFC 6238 using a random 160-bit secret, SHA-1, six digits and 30-second steps, with at most one step of clock tolerance. The service stores the last accepted counter and rejects code reuse, including concurrent attempts against different challenges. Authenticator secrets and pending enrolment payloads are encrypted with Fernet. Codes and challenge tokens are never audited. Each challenge permits at most six verification attempts and expires after five minutes. Identity/access changes invalidate outstanding login challenges.

Staging sessions use the host-only `__Host-aislesignals_session` cookie with `Secure`, `HttpOnly`, `SameSite=Strict` and `Path=/`. Tokens contain 32 random bytes; only SHA-256 token hashes are stored in PostgreSQL. CSRF tokens are session-specific keyed digests, returned only after authentication. The browser keeps CSRF in memory. Sessions expire after eight hours and after 30 minutes without an authenticated request. Logout revokes the session server-side. Creating a new session retains at most five unrevoked sessions per user.

The explicit local `development` environment uses the separate `aislesignals_dev_session` cookie without `Secure` to support loopback HTTP browser tests. Render rejects `CLOUD_ENV=development`; this exception is not used in staging.

## Authority and concurrent changes

`OWNER` has organisation-wide access. `MANAGER` and `REVIEWER` receive only their assigned active pharmacies. Only owners can administer pharmacies, users and invitations in this milestone. Composite foreign keys prevent cross-organisation user/pharmacy memberships. Client-provided organisation, role or nested metadata fields do not establish authority.

User and pharmacy edits require the version seen by the editor. A stale update returns `VERSION_CONFLICT` without changing the record. At least one active owner must remain. Access/role/disable edits revoke that user's sessions and pending sign-in challenges. Deactivating a pharmacy revokes member sessions; subsequent authority checks exclude inactive pharmacies.

`ControlStore.transaction()` supplies a bounded PostgreSQL connection with dictionary rows, UTC timestamps, statement/lock timeouts and an exact schema check. `require_principal(request)` authenticates the cookie and loads current permissions. Operations must call `validate_principal(conn, principal)` inside the transaction that reads or writes records and use the returned authority. Lock order is organisation, user, then session. Administrative writes acquire an exclusive organisation lock; ordinary actions acquire a shared lock. A permission change cannot silently race an action that used an earlier request snapshot.

Audit entries contain organisation ID, actor ID, an action code, optional record/pharmacy IDs and the timestamp. They do not contain passwords, OTPs, session tokens, invitation tokens, bootstrap tokens or secret payloads. SQL/driver failures and validation errors are mapped to safe error envelopes rather than returned verbatim.

## Tested and remaining

The auth suite includes real PostgreSQL tests for initial v1 upgrade, migration integrity, setup and invitation races, replayed MFA, expired tokens, persistent throttling, current tenant authority, CSRF/origin exclusions, last-owner concurrency, version conflicts, server-side session expiry/revocation and secret-free audit entries. It uses only a process-owned disposable PostgreSQL cluster and synthetic accounts. Run:

```sh
CLOUD_RUN_POSTGRES_TESTS=1 PATH="$(pg_config --bindir):$PATH" .venv312/bin/python -m pytest tests/cloud/test_control_auth.py -q
```

Unit tests include the published RFC 6238 SHA-1 test vectors. Implementation references: [RFC 6238](https://datatracker.ietf.org/doc/html/rfc6238), [OWASP password storage guidance](https://cheatsheetseries.owasp.org/cheatsheets/Password_Storage_Cheat_Sheet.html), and [cryptography Fernet documentation](https://cryptography.io/en/latest/fernet/).

This milestone has no password change/reset, lost-authenticator recovery, recovery codes, SSO or automated email delivery. Do not remove MFA, modify a database password hash or reopen first-owner setup as a recovery shortcut. Keep an additional enrolled owner and define a verified recovery process before customer rollout. Auth key rotation and a tested database-plus-key restore procedure also remain operational work. These controls do not validate camera quality, detection accuracy, physical alarms or laptop availability.
