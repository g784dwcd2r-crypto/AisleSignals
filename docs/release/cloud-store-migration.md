# Local cloud storage migration and offline recovery

This change installs storage prerequisites only. It does not pair a laptop, start a sender, contact the cloud, enable an API route or establish that camera monitoring works.

## Schema and installation identity

The local SQLite `user_version` is **3**. The outbox module retains its independent schema version **1**. Fresh workspaces and supported v2 workspaces create the outbox tables, indexes, immutability triggers and `runtime_settings.installation_id` inside the Store's `BEGIN IMMEDIATE` transaction. The installation ID is one canonical, nonzero UUID and is preserved on reopen and recovery. Synthetic workspaces receive an identity but no cloud bindings; a synthetic database containing a cloud binding is refused.

Base DDL uses individual statements rather than `executescript`, which could implicitly commit the caller's transaction. Failure during installation rolls back the base/outbox DDL, runtime settings and version change. Existing entity, staff, evidence, session and configuration records are preserved during normal migration. The journal mode is not changed before a successful initial migration.

Provenance is checked before schema and journal writes, and before hardening an existing pilot database's file permissions. A pilot/synthetic mode mismatch is refused. Version 2 and 3 require an explicit mode marker. Missing/invalid v3 installation IDs, incompatible outbox definitions/version, unknown future local versions and bindings tied to a different installation are refused, rather than silently repaired. The released v2 reader accepts only versions 0/1/2 and therefore refuses v3; downgrading the application against an upgraded database is unsupported. Keep the original offline backup for rollback to that older application.

## Recovery contract

Passphrase-encrypted recovery archives accept protected pilot schema v2 or v3. Existing archive authentication, exact file allowlists, size limits, media authentication and branch/account consistency checks remain in force. A v2 archive is upgraded only in the private, unpublished staging directory. Failure in migration or recovery fencing leaves the archive unchanged and publishes no destination.

The backup includes the evidence encryption key so sampled evidence remains recoverable. It excludes private sync encryption keys and device credentials; nearby private files are not swept into the archive. No substitute sync key is generated. Schema v3 may contain encrypted outbox bytes in the archive itself, but the recovered database removes those unusable payloads and their mutable payload hashes before publication. The bounded staged database is then rebuilt with `VACUUM` and its WAL truncated, so old payloads in SQLite free cells/pages do not enter the published copy. This is not a secure-erasure claim about the original encrypted archive, disk snapshots or filesystem.

`cloud_outbox.mark_restored(conn, now=aware_datetime)` is a keyless, offline operation requiring the caller's transaction. It:

- Sets each binding to `RESTORED` and advances its generation, preserving immutable installation, routing and key identities. Repeating the fence is idempotent. Exhausted generations refuse recovery rather than wrap.
- Clears sender capacity, leases, claim generations and settled-claim tokens. Old acknowledgements/retries cannot regain authority; even a caller retaining the original key cannot claim work from a restored binding.
- Cancels observations that were never attempted. Potentially delivered observations or unresolved withdrawals become payloadless `BLOCKED` withdrawal obligations with `RESTORED_REQUIRES_MANAGEMENT` and no terminal cleanup timestamp.
- Retains existing opaque source/routing IDs, immutable observation hashes, attempt counts and actual receipt markers. Previously acknowledged withdrawals remain acknowledged; the restore does not invent a cloud withdrawal/deletion receipt.

Existing recovery protections still revoke sessions, remove first-owner setup state, disable recovered staff accounts and cancel in-flight model jobs. Unexpired sampled evidence and independently authored staff records remain subject to the existing evidence policy. Expired sampled evidence is removed.

The returned report has `cloud_sync_resumed: false`, a `cloud_recovery` object containing `bindings_restored` and `blocked_obligations` counts, and `requires_cloud_management_review` when uncertain deliveries exist. Automatic withdrawal **cannot resume** without the excluded original credentials/key; this release does not provide a recovery reauthorisation workflow. Explicit management of the prior cloud device/source records is required. The cloud observation deadline is at most 24 hours after its reported occurrence, but elapsed time is not evidence of an acknowledged withdrawal. Unresolved payloadless obligations are excluded from automatic terminal pruning; they are bounded by the existing outbox admission limits and require an explicit future recovery decision.

## Verification

Run from the repository root with the project Python environment:

```sh
python -m pytest tests/api/test_cloud_store_migration.py tests/api/test_schema_release.py tests/api/test_cloud_outbox.py tests/api/test_evidence_recovery.py tests/api/test_pilot_identity.py tests/api/test_pilot_admin.py tests/security/test_pilot_filesystem.py -q
```

The tests use disposable SQLite databases, synthetic account/camera metadata and synthetic images. They cover additive v2/legacy migration, stable installation IDs, transaction rollback, incompatible provenance and mode preservation, the v2 format rejection gate, keyless sender fencing, uncertain-delivery retention, archive exclusion of unrelated private files, evidence/account preservation and recovery failure without a published destination. Native platform acceptance and an enabled end-to-end cloud sender remain separate checks.
