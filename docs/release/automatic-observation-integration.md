# Automatic observation delivery release

## Staff workflow

A pilot manager creates a laptop enrolment code in the online console, opens **Cloud connection** on the pharmacy laptop, and enters the console HTTPS origin, code, laptop name and their local manager password. The app retrieves the cloud identity for explicit confirmation of both the local branch and cloud pharmacy. Confirmation saves a paused connection. The manager explicitly resumes delivery after reviewing that identity.

New eligible live interaction jobs capture their connection admission on the server. When the experimental local model completes a possible-concealment classification with alarm eligibility, the source result and encrypted metadata queue row commit together. Uploaded recordings and normal-shopping classifications remain local. Existing results created before pairing are not retroactively uploaded. The local alarm remains governed by its existing attended arming and review workflow; cloud delivery does not actuate it.

The pilot application owns one recurring sender. It rechecks the original source, immutable metadata, current binding and lease before sending. HTTPS transmission happens outside SQLite transactions in a bounded child process. Lost receipts retry the same source identity; cloud receipts and database uniqueness prevent duplicate alerts. The online console lists received alerts for staff review and case creation. Receiving metadata is not evidence of an active camera or validated theft detection.

## Deletion, recovery and maintenance

Local deletion, expiry and storage rolloff withdraw admitted metadata in the source transaction. A paused connection prevents network traffic, including withdrawals. A disconnected/restored connection does not automatically resume or manufacture a removal acknowledgement. If delivery cannot complete, the independent cloud source deadline bounds availability to at most 24 hours after the original event. Staff-authored cases and notes survive source expiry/removal, with the source-unavailable state displayed.

Pilot SQLite schema v3 owns the outbox and installation identity. Restoring a supported archive into a new destination upgrades it atomically and disables cloud delivery. It clears encrypted queued payload material and marks restored bindings for management; credentials remain outside backups. Restore cannot silently pair another laptop or replay old observations.

The cloud application starts scheduled bounded source maintenance in its actual lifespan. Read paths enforce expiry independently; background passes remove expired source metadata while preserving staff cases. Shutdown stops owned workers and reports failures rather than claiming success.

The authenticated laptop health endpoint reads a committed read-only snapshot, so a camera evidence write cannot monopolise its authentication lock. It still checks session expiry, revocation, current branch and database provenance. Shutdown retains the API/backup ownership lock until the sender, inference workers and retention work have finished; an unsuccessful sender stop keeps that lock held.

## Operator-visible status

The connection screen distinguishes pending, received, blocked and pending-removal records. Worker running means the sender thread is alive. Camera monitoring remains **UNKNOWN**: this release does not connect the local capture lifecycle to a cloud heartbeat. Missing private material and revoked credentials require explicit repair/re-enrolment; code-consumption uncertainty requires inspection of the online laptop registration before another attempt.

## Validation and limits

Tests use synthetic identities, generated images and explicit model doubles. They exercise actual local authentication, scoped SQLite/outbox transactions, owned HTTPS child processes, disposable PostgreSQL, committed cloud receipts, staff review, deletion and expiry. Browser journeys exercise actual local API pairing and secret clearing; their sender is deliberately inert because network delivery has separate HTTPS acceptance coverage.

This verifies the workflow, not theft-detection accuracy, audible speakers, customer CCTV compatibility or successful pharmacy deployment. Native packaging checks must pass on Windows and macOS before distributing new archives. The existing Free Render staging service and database remain staging infrastructure; no paid resources, production backups, real owner credentials or six-branch onboarding are provisioned by these changes.
