# AisleSignals Implementation Runbooks

These are operational procedures to implement and rehearse. They do not assert a live support contract.

## Deployment

1. Verify branch/release ownership, critical test results, dependency licences and signed image digests.
2. Confirm database migration is compatible with current and previous application/companion versions.
3. Record backup/restore point and current deletion/revocation journal position.
4. Deploy to staging with synthetic fixtures; exercise both tenant and same-tenant wrong-site checks.
5. Promote one canary site, leaving optional output control disabled unless separately commissioned.
6. Check queue age, errors, media verification, role enforcement, notification load and capture leases.
7. Stop rollout on critical regression. Roll back application/adapter/policy to compatible versions; do not blindly down-migrate data.
8. Record exactly which sites, camera classes and versions were accepted.

## Companion commissioning

1. Record owner/existing site IT contact authority, exact recorder model/firmware, permitted channels and network limits.
2. Install the signed companion for the qualified laptop OS; test user-sign-in startup, sleep/resume, disk guard and speaker self-test. Enrol using a one-use token and device-generated private key; bind a single site.
3. Enter camera secrets locally through the protected existing site IT contact flow. Verify logs and support bundle redaction.
4. Configure source stream references, excluded areas, audio exclusion and signed capture lease.
5. Measure decode and temporal detector throughput per chosen view. Qualify access and model suitability separately.
6. Test pre-event context, delayed final clip, clock drift, frozen stream and source event deduplication.
7. Disconnect network beyond the capture lease; verify degraded state, capture expiry, bounded spool and historical replay.
8. Test signed updater and rollback. Revoke temporary existing site IT contact access after manager acceptance.

## Restore

1. Stop external access and record incident awareness time and affected services.
2. Restore database/base backup plus WAL into an isolated target; restore required private media separately.
3. Apply latest deletion and revocation journals from their independently recoverable source.
4. Verify tenant-inclusive relations, sessions/device revocation, schema/app compatibility and object manifests.
5. Reconcile leased jobs and outbox. Never replay expired or uncertain physical commands.
6. Run critical identity/media/incident tests and a representative sample from both pilot organisations.
7. Record achieved metadata RPO/core RTO and remaining full-media recovery time.
8. Restore access only to verified services; communicate remaining degraded coverage through the agreed support process.

## Suspected data incident

1. Restrict affected accounts, grants, media access or integration credentials without deleting relevant evidence.
2. Preserve minimal necessary logs and identify tenants, assets, time range and disclosure paths.
3. Notify the named controller contact without undue delay; target internal escalation within one hour.
4. Support the controller's assessment of applicable notification duties; do not automatically send legal notices.
5. Rotate credentials and fix the cause, then independently test scope/isolation before reopening.
6. Record containment, decisions, affected-data assessment and follow-up actions with owners.

## Alert flood or model regression

1. Freeze the provider/model/policy version and affected site/class metrics.
2. Suspend the noisy class or private routing while leaving other qualified coverage and manual workflows visible.
3. Examine representative false alerts, missed events, scene changes and installation faults.
4. Re-evaluate on held-out benign and target actions; retain counts and reviewer disagreement.
5. Re-enable only accepted classes and document any reduced coverage or changed thresholds.

## Unknown attention-sounder state

1. Block automatic retry and retain the stable command ID plus companion execution ledger.
2. Show UNKNOWN or sent with unverified physical state; do not display confirmed activation without feedback.
3. Use local stop and the established site procedure if necessary.
4. An authorised person/existing site IT contact checks feedback or the output locally.
5. Record the physical finding; diagnose expiry, power, wiring or executor failure before another deliberate request.

## Offboarding

1. Verify the requesting customer's authority and agreed end date.
2. Prepare the authorised data export with recipient/purpose and manifest.
3. Revoke sessions, integration keys, media grants and device access; expire capture leases.
4. Detach or remove equipment through the authorised existing site IT contact and agreed ownership terms.
5. Apply retention/deletion, reviewed holds and maximum backup expiry. Update the deletion journal.
6. Test that old links, devices and restored records cannot revive access; retain minimal closure evidence.
