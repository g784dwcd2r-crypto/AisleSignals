# AisleSignals Functional Requirements

## FR-001 Named accounts and MFA

Release: MVP | Module: Access

Authenticate staff through managed OIDC; require individual accounts and authenticator MFA. Shared hardware never creates shared identity.

Acceptance: Invalid issuer, audience, nonce, state and expired session tests fail; no browser-stored bearer token.

## FR-002 Organisation and site access

Release: MVP | Module: Access

Resolve active organisation from an authenticated membership and enforce permitted sites on every operation.

Acceptance: Client A cannot read, change, infer or export Client B data; same-organisation unauthorised-site tests also fail.

## FR-003 Invitations and role changes

Release: MVP | Module: Access

Managers invite eligible roles only within their own authority; invitations expire after seven days and are one use.

Acceptance: A setup operator cannot invite a manager; used or expired invitation fails; removal invalidates sessions and grants.

## FR-004 Shift assignment and handover

Release: MVP | Module: Access

Assign a named reviewer and alternate; hand over pending work explicitly; show missing coverage.

Acceptance: An unassigned shift shows degraded reviewer coverage and routes privately to the configured alternate.

## FR-005 Session controls

Release: MVP | Module: Access

Use server-side opaque sessions, idle lock, CSRF validation and session revocation; reauthenticate privileged actions.

Acceptance: Old tabs lose API/media access after revocation; cookie-authenticated mutations without CSRF fail.

## FR-006 Support access grants

Release: MVP | Module: Access

Grant support access to a specific site, purpose, scope and expiry; default access is operational metadata only.

Acceptance: Expired grants fail; a health grant cannot view media or export; all grant use is audited.

## FR-007 Site registry

Release: MVP | Module: Commissioning

Record legal entity, address, Europe/Dublin timezone, hours, contacts and pilot owner before activation.

Acceptance: Each site has separate policies and status; Dublin daylight-saving boundaries preserve event ordering.

## FR-008 Device enrolment

Release: MVP | Module: Commissioning

Exchange a one-use, ten-minute enrolment token and device-generated CSR for a site-bound credential.

Acceptance: Token reuse, wrong site and revoked certificate fail; no factory shared password is accepted.

## FR-009 Camera capability register

Release: MVP | Module: Commissioning

Record exact camera/recorder model, firmware, stream path reference, codec, view and approved event classes.

Acceptance: Stream accessibility and detection qualification have separate results; unsupported views show a reason.

## FR-010 Privacy masks and exclusions

Release: MVP | Module: Commissioning

Exclude consultation rooms, prescription screens, audio and unnecessary areas; version local capture policy.

Acceptance: Masked areas remain excluded in inference input and uploaded review copies; scope changes require requalification.

## FR-011 Local credentials

Release: MVP | Module: Commissioning

Keep camera secrets in the local protected credential store; cloud inventory holds opaque references.

Acceptance: Secrets do not appear in logs, support bundles, URLs, exports, screenshots or source control.

## FR-012 Activation gate

Release: MVP | Module: Commissioning

Activate a site only after role assignment, view tests, processing scope, notifications and commissioning records pass.

Acceptance: Activation is blocked with named missing checks; one site's acceptance cannot activate another site.

## FR-013 Coverage health

Release: MVP | Module: Commissioning

Measure frame freshness, camera decode, detector status, clock offset, laptop companion disk and connectivity separately.

Acceptance: A frozen frame or missing detector never displays as active detection; healthy unrelated views remain visible.

## FR-014 Maintenance and updates

Release: MVP | Module: Commissioning

Install signed, compatible laptop companion updates in stages with a previous-version rollback and local recovery procedure.

Acceptance: Invalid signature, low disk or failed health check prevents promotion; restart does not erase command deduplication.

## FR-015 Normalised observations

Release: MVP | Module: Detection

Validate source event ID, mapped camera, interval, model version, score scale, quality and evidence references.

Acceptance: Unknown camera, unsupported schema/class and cross-site evidence fail; product and score may remain unknown.

## FR-016 Idempotent intake

Release: MVP | Module: Detection

Deduplicate exact provider/device events transactionally using source identity and stable source event ID.

Acceptance: Concurrent retries create one observation; a repeated ID with different content returns conflict.

## FR-017 Qualified classes only

Release: MVP | Module: Detection

Enable concealment or sweeping only when the contracted detector and camera view pass site acceptance.

Acceptance: No generic object detector or language model is presented as a validated concealment engine.

## FR-018 Context and clip readiness

Release: MVP | Module: Detection

Send an eligible alert with available preceding context; attach following footage asynchronously with explicit readiness.

Acceptance: Notification is not delayed for the full post-event clip; absent or corrupt media is clearly shown.

## FR-019 Candidate grouping

Release: MVP | Module: Detection

Group a validated event sequence conservatively; preserve separate observations and uncertain associations.

Acceptance: Nearby unrelated customers are not merged solely by location; grouping never creates cross-visit identity.

## FR-020 Historical events

Release: MVP | Module: Detection

Preserve capture and arrival time; flag stale or uncertain-time observations as historical.

Acceptance: Buffered events after reconnection cannot trigger an immediate sounder or inflate current latency metrics.

## FR-021 Class controls

Release: MVP | Module: Detection

Version thresholds and routing by provider/model/site; allow a manager to suspend a noisy class.

Acceptance: Suspension is audited and visibly reduces coverage; model changes rerun the agreed evaluation set.

## FR-022 Incident creation

Release: MVP | Module: Review

Open a case from a reviewed candidate or a manual factual report; keep observation and incident identities separate.

Acceptance: Manual reporting works without a detector; duplicate conversion of the same candidate is controlled.

## FR-023 Review and classification

Release: MVP | Module: Review

Capture benign, insufficient evidence, suspected incident or store-confirmed loss with reasons and supporting evidence.

Acceptance: Only a manager records store-confirmed loss; no action creates a thief label or legal finding.

## FR-024 Concurrent changes

Release: MVP | Module: Review

Use expected record version for workflow, ownership, review and financial changes.

Acceptance: Two reviewers cannot silently overwrite one another; conflict response supplies the current version.

## FR-025 Outcomes and valuation

Release: MVP | Module: Review

Record loss, recovery and payment outcomes separately using EUR minor units and an explicit cost basis.

Acceptance: Unknown amount stays null; recovered goods and the same avoided loss are not counted twice.

## FR-026 Follow-up tasks

Release: MVP | Module: Review

Create tasks with assignee, due date and reason; closure identifies outstanding action ownership.

Acceptance: Overdue tasks appear in the appropriate site inbox; closing the case does not silently discard tasks.

## FR-027 Correction and reopening

Release: MVP | Module: Review

Append corrections and manager reopening reasons while preserving prior versions and original observations.

Acceptance: A corrected outcome updates reports and retains its audit trail; later evidence does not rewrite history.

## FR-028 Restricted earlier-incident link

Release: MVP | Module: Review

Permit an authorised manager to link a specific earlier case with an evidence-based reason and retention review.

Acceptance: No automatic identity matching or shared watchlist is created; prior suspicion is not a new offence.

## FR-029 Upload lifecycle

Release: MVP | Module: Evidence

Create a scoped upload intent, accept bytes, validate checksum/size/type and quarantine before publication.

Acceptance: Oversized, mismatched or malformed media stays unavailable and produces a traceable failure.

## FR-030 Original and derivative separation

Release: MVP | Module: Evidence

Preserve collected source bytes and timestamps; generate separately labelled preview/redacted variants.

Acceptance: A transformation never overwrites the original or its digest; manifest records parent and transform version.

## FR-031 Controlled playback

Release: MVP | Module: Evidence

Serve evidence through an authenticated, revocable media proxy with site/purpose checks and range support.

Acceptance: Leaked URL, expired grant and wrong-site session cannot retrieve any byte range or thumbnail.

## FR-032 Export preparation

Release: MVP | Module: Evidence

Managers choose cases, recipient reference, purpose and permitted variants; a job builds the package.

Acceptance: Export rechecks current authority and retention at execution and download; no automatic external sending occurs.

## FR-033 Evidence manifest

Release: MVP | Module: Evidence

Include selected originals/derivatives, hashes, UTC/local times, collection provenance, reviews and export history.

Acceptance: An independent verification tool detects modified bytes; digest is not described as proof of guilt or admissibility.

## FR-034 Retention and holds

Release: MVP | Module: Evidence

Apply justified retention classes, reviewed holds and expiry to media, derivatives, exports and AI drafts.

Acceptance: Concurrent hold/deletion is serialised; deletion failures block new access and alert the privacy owner.

## FR-035 Rights requests

Release: MVP | Module: Evidence

Record receipt, verified request scope, calendar-month due date, redaction review and authorised response.

Acceptance: Month-end dates calculate correctly; search stays scoped; disclosure protects unrelated people.

## FR-036 Private notification routing

Release: MVP | Module: Response

Use the staffed console first; optional push contains only a generic reference; remind at 30 seconds and escalate at 90.

Acceptance: Duplicate dispatch is deduplicated; no acknowledgement never automatically becomes a public alarm or emergency call.

## FR-037 Assistance request

Release: MVP | Module: Response

Let an authenticated staff member request help without classifying an offence; distinguish delivery and acknowledgement.

Acceptance: Repeated taps reuse one logical request; acknowledgement is not represented as responder arrival.

## FR-038 Emergency continuity

Release: MVP | Module: Response

Keep the pharmacy's established physical panic and emergency procedures independent of the cloud app.

Acceptance: Loss of cloud, laptop companion or browser does not disable the existing physical panic arrangement.

## FR-039 Commissioned attention sounder

Release: OPTION | Module: Response

Permit an individually authorised person to request a mapped attention output after reviewing current context.

Acceptance: Wrong site, missing permission, stale live context or incomplete commissioning blocks authorisation.

## FR-040 Safe command execution

Release: OPTION | Module: Response

Use one-use authorisation, signed commands, ten-second maximum TTL and a five-second maximum attention pulse.

Acceptance: Replays, reboot and crash after dispatch cannot cause a second pulse; uncertain physical state stays unknown.

## FR-041 Local stop and output testing

Release: OPTION | Module: Response

Require a tested stop control for the documented existing software interface, output feedback policy and test mode isolated from live outputs.

Acceptance: Expiry, duplicate, unavailable existing interface, watchdog and emergency-stop tests pass at each commissioned output.

## FR-042 Incident draft assistant

Release: MVP | Module: AI

Draft only from reviewed structured facts and approved evidence references; identify missing fields explicitly.

Acceptance: Schema failure or unsupported identities/amounts returns to manual completion; staff approval remains required.

## FR-043 Job permissions and limits

Release: MVP | Module: AI

Give each AI job a fixed input schema, model/prompt version, timeout, output limit and cost reservation.

Acceptance: No AI job can execute shell/SQL, activate a sounder, disclose evidence or send a message externally.

## FR-044 Spending and fallbacks

Release: MVP | Module: AI

Enforce per-tenant model budgets atomically before calls; retry at most once and retain manual workflows.

Acceptance: Budget exhaustion, timeout and supplier outage never block review, evidence capture or manual reports.

## FR-045 Quality and change control

Release: MVP | Module: AI

Evaluate report drafts on a held-out factual set before model/prompt changes and sample staff corrections.

Acceptance: Critical unsupported assertions prevent release; improvements cannot be claimed from training examples alone.

## FR-046 Approved procedure assistant

Release: P2 | Module: AI

Retrieve only current approved nonclinical procedures with source/version and a direct link.

Acceptance: Missing or conflicting procedure yields no confident instruction; revoked documents disappear from retrieval.

## FR-047 Operational dashboard

Release: MVP | Module: Reporting

Show coverage, pending reviews, task age, review time, unresolved cases and provider failures by authorised site.

Acceptance: Totals reconcile to scoped records; missing observations and downtime remain visible.

## FR-048 Financial reporting

Release: MVP | Module: Reporting

Report documented cost-basis losses and recoveries separately from candidates and suspected incidents.

Acceptance: No report totals guessed alert values as savings; reversals and corrections change the relevant period transparently.

## FR-049 Weekly digest

Release: MVP | Module: Reporting

Generate a deterministic aggregate first and optionally summarise it with AI; suppress sparse or unsupported trends.

Acceptance: The report still works without AI; no person-level risk scores or cross-customer footage are included.

## FR-050 Auditable actions

Release: MVP | Module: Governance

Append actor, purpose, resource, timestamps, request ID and relevant before/after version for sensitive actions.

Acceptance: Runtime users cannot update/delete audit records; independently stored checkpoints reveal later changes.

## FR-051 Processing controls

Release: MVP | Module: Governance

Record controller/processor responsibilities, permitted sources, offence-data safeguards and cloud processing settings.

Acceptance: Live data activation requires completed site-specific records; project approval does not fabricate a legal basis.

## FR-052 Customer offboarding

Release: MVP | Module: Governance

Export authorised records, revoke people/devices/integrations and apply deletion plus backup expiry.

Acceptance: Terminated access fails immediately online; restored backups cannot revive deleted or revoked access.

## FR-053 Sensitive feature exclusions

Release: MVP | Module: Governance

Provide no facial embeddings, cross-visit recognition, shared offender search, door-lock adapter or clinical decision tool.

Acceptance: Contract, UI, database and permissions contain no hidden enabled route to an excluded feature.

## FR-054 Durable work queue

Release: MVP | Module: Platform

Use committed jobs/outbox rows, leases, fencing tokens and bounded retries for uploads, exports and notifications.

Acceptance: Worker crashes and expired leases do not lose committed work or duplicate final business effects.

## FR-055 Database isolation

Release: MVP | Module: Platform

Use tenant-inclusive foreign keys, runtime RLS and transaction-local context; separately enforce site and action permissions.

Acceptance: Wrong-tenant references fail; connection-pool reuse cannot retain the previous request's tenant context.

## FR-056 Recovery and backups

Release: MVP | Module: Platform

Back up database/WAL and media independently; restore in isolation and reapply deletion and revocation records.

Acceptance: Demonstrate a 15-minute database RPO and four-hour core-service RTO under the scoped pilot recovery test.

## FR-057 Observability

Release: MVP | Module: Platform

Monitor latency, coverage, queue age, disk, failed uploads, model spend, permissions and export/deletion failures.

Acceptance: Alerts identify the affected site/service without exposing video, camera passwords or incident narratives in logs.

## FR-058 Release pipeline

Release: MVP | Module: Platform

Validate contracts and isolation, scan dependencies, sign immutable releases and promote through staging and a canary site.

Acceptance: A failing critical check prevents deployment; rollback keeps previously committed incidents readable.

## FR-059 Offline and degradation

Release: MVP | Module: Platform

Serve no cached sensitive media in the web service worker; laptop companion spools only within its signed capture lease and limits.

Acceptance: Internet recovery labels old events historical; expired commands never run; unsupported offline functions say unavailable.

## FR-060 Accessibility and usability

Release: MVP | Module: Platform

Support keyboard review, visible focus, readable text, non-colour status and labelled controls on the two existing laptops.

Acceptance: A pharmacist can complete the core flow using keyboard or touch; sound is supplementary to visible notifications.

## FR-061 Read-only import

Release: P2 | Module: Stock

Accept approved nonclinical SKU movements and counts through validated files or an agreed API; reject patient and payment data.

Acceptance: Wrong site, ambiguous units, duplicated batches and invalid rows are rejected or quarantined with an import report.

## FR-062 Complete reconciliation

Release: P2 | Module: Stock

Calculate expected stock from opening count and all relevant movement classes; display missing inputs and source watermark.

Acceptance: Incomplete periods cannot produce a definitive theft claim; batch context never supports live nonpayment conclusions.

## FR-063 Stock investigations

Release: P2 | Module: Stock

Create a task for recount, delivery, return, transfer, damage or expiry review and record the supported explanation.

Acceptance: A variance cannot be automatically assigned to a shopper or employee; source corrections remain versioned.

## FR-064 Expiry and recall coordination

Release: P2 | Module: Stock

Match exact approved product/batch data, assign action and require pharmacist confirmation where applicable.

Acceptance: Stale notices or absent batch data show unknown; AI cannot infer availability or order a substitution.

## FR-065 Aggregate forecasting

Release: P2 | Module: Stock

Forecast workload or incident counts by sufficiently aggregated zone/time with exposure and uncertainty.

Acceptance: Below the minimum data threshold show insufficient evidence; no personal criminality or clinical forecast is produced.

## FR-066 One branch subscription and AI budget

Release: MVP | Module: Commercial

Record exactly 6000 EUR cents monthly for one pharmacy branch; bind the subscription to its company and site, with a EUR 5 branch model allowance. Billing begins as an owner-managed record.

Acceptance: Creating a branch cannot charge a customer or enable paid capacity automatically; a separate approved subscription or explicit trial is required, and unknown tax terms cannot publish a checkout.

## FR-067 Existing laptop only

Release: MVP | Module: Commissioning

Install the companion on the pharmacy's existing laptop and connect existing supported cameras or recorder software interfaces. No appliance, capture card, GPU, relay or new camera purchase is allowed.

Acceptance: OS-specific install, startup, sleep/resume, disk/CPU guard, encrypted spool and laptop speaker tests pass; unsupported existing equipment is disclosed rather than replaced.