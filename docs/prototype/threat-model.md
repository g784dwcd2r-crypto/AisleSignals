# Local prototype boundaries and failure design

## Assets and trust boundaries

The only supported inputs are synthetic pharmacy case facts. Even a local demo has sessions, site boundaries, records, exported files and unsaved staff input worth protecting. The browser is untrusted: role, organisation and site authority come from the authenticated server session. A guessed ID, hidden UI control, raw API request or edited request body cannot grant access.

The API listens on loopback, rejects unknown hosts and foreign origins, and uses opaque HttpOnly cookies plus a CSRF token for authenticated writes. It does not trust proxy headers. Cookie/session identifiers never enter localStorage. The UI renders server text as text and only displays evidence URLs under the local authenticated route. No third-party scripts, fonts or remote evidence URLs are needed.

SQLite persists records and password hashes locally. Passwords are public demonstration credentials; they are not protection for real customer records. A person with the local database file or the user's OS account can inspect or modify the prototype. The local file is not an encrypted production evidence vault, and its audit history is not independently tamper-proof. Those limitations are why this build accepts synthetic data only.

## Integrity and workflow

State changes execute in transactions with tenant/site checks. Record versions prevent silent overwrites; idempotency keys distinguish a retry from a second logical request. Reusing the same key with changed data is rejected. Source-event IDs provide a separate duplicate-intake boundary. Review candidates remain separate from incidents, and benign dismissal creates no allegation record.

Financial records require explicit human classification, outcome and manager authority. An alert is not money saved, a returned item is not a confirmed theft, and unknown value is not zero. Closing requires a resolved outcome and completed follow-ups. Corrections, closure and reopening append activity; editing facts invalidates a previously approved draft.

Outages do not fabricate successful changes. A failed or uncertain request retains its input and retry identity; the UI shows stale connectivity and stops treating camera coverage as known. Conflicting edits require a deliberate refresh/reconciliation. Polling never silently overwrites a dirty form. Logout and session expiry clear in-memory drafts and polling.

## Media, models and physical systems

Evidence in this release is a generated schematic marked synthetic. The route still checks the session and site and returns no-store responses. Missing evidence is an explicit review limitation. Exports require manager authority and a stated purpose, and remain a user-initiated download. No API emails Gardaí or shares a case externally.

The report engine is a deterministic local template; it does not call an AI provider or reserve real spend. There is no facial recognition, identity matching, person-level risk scoring or clinical decision. No physical-alarm, relay or door-lock endpoint exists. Staff assistance records a request/acknowledgement; it is not a monitored emergency service.

The companion utility does not silently discover cameras, access the webcam, change sleep settings or capture frames. Readiness and stream accessibility are distinct from validated detection. A sleeping or powered-off laptop cannot perform monitoring. Private-camera probe restrictions and redaction are specified in companion.md.

## Residual work before a real pilot

- Managed OIDC/MFA and named-account administration; session revocation across deployed services.
- PostgreSQL RLS, migrations, durable jobs and non-owner database roles.
- Qualified camera/detector integration, signed capture leases, encrypted bounded spooling and authenticated updates.
- Real evidence validation, playback ranges, masking/redaction, retention, holds and data-rights operations.
- Site processing records, tested recovery, distribution signing and an actual human operational contact.
- Workload, false-alert, performance and costs measured separately on the pilot Windows laptop and Mac.

This is an explicit implementation backlog. Passing the prototype tests does not establish production readiness or measured theft reduction.
