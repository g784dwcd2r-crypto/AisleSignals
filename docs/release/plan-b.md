# Plan B — Central pharmacy monitoring

Owner: Jawahir Q.  
Product: AisleSignals for pharmacies  
Status: approved implementation plan; not yet implemented or commissioned

## Objective

Plan B gives authorised administrators one operations view across every pharmacy in their permitted company scope. It combines device and camera health, live alerts, incident evidence, staff responses and temporary live support access without sending continuous CCTV video to the cloud by default.

The pharmacy laptop remains the primary CCTV and detection runtime. It analyses authorised feeds locally and sends health data, observations and encrypted incident clips to the central service. A connected laptop does not mean its cameras or detector are active; the interface must show those states separately.

## Access model

| Role | Permitted scope |
|---|---|
| Platform owner | All customer organisations for service operations, with audited and time-limited access to customer video |
| Company administrator | Every pharmacy owned by their company |
| Pharmacy manager | Their assigned pharmacy branches |
| Staff reviewer | Assigned alerts, clips and incidents within their branch scope |
| Support operator | Diagnostics granted for a stated reason and expiry; live video requires a separate support grant |

Every API, background job, stream, clip, export and remote command must derive organisation and branch authority from the authenticated session. A branch identifier supplied by a browser or laptop never grants access.

## Workstreams

| ID | Deliverable | Completion evidence |
|---|---|---|
| B1 | Multi-pharmacy operations dashboard | Administrators can filter permitted branches and see laptop, source, camera, detector, alarm and delivery status independently, including last healthy time and actionable faults |
| B2 | Branch camera wall | A branch view renders confirmed four-camera or six-camera layouts, preserves camera names and never presents stale images as live |
| B3 | Secure temporary live view | An authorised user requests a short-lived session; the branch laptop consents according to policy; encrypted media is relayed with strict scope, expiry, concurrency and audit controls |
| B4 | Alert and incident clips | A qualifying local observation uploads an encrypted bounded clip, including configurable pre-event and post-event context, and links it to one alert and its review history |
| B5 | Central alert operations | New alerts appear in real time with severity, camera, observable action and confidence; staff can acknowledge, classify, escalate, mute according to role and create or dismiss an incident |
| B6 | Remote diagnostics and controls | Authorised commands can request health refresh, restart an owned AisleSignals process, reconnect a source or run an alarm test; commands are signed, expiring, idempotent and fully audited |
| B7 | Reliability and observability | Stream loss, frozen frames, laptop sleep, queue delay, storage failure and notification failure produce separate health states and recovery records |
| B8 | Privacy, retention and audit | Access purpose, viewer, branch, timestamps and actions are recorded; clip retention, deletion, export and legal hold follow configured policy and branch authority |

## Operating flow

1. The branch laptop maintains a secure outbound connection to the cloud and reports separate laptop, camera, detector and alarm health.
2. Local analysis creates an observation from visible product interactions. It does not make a criminality or identity finding.
3. A fresh qualifying observation triggers the commissioned local attention alarm and creates one cloud alert with duplicate suppression.
4. The laptop encrypts and queues the bounded incident clip. Offline items remain local and retry after reconnection.
5. The authorised dashboard receives the alert and displays the clip when available. A staff member acknowledges and reviews it.
6. The review records an observable outcome such as benign, unclear or incident opened. Every change remains in the audit history.
7. When troubleshooting requires live video, an authorised administrator starts a temporary, audited session for selected cameras. The session ends automatically at expiry.

## Privacy and bandwidth design

- Continuous cloud streaming is disabled by default. Camera feeds and ordinary footage remain on the pharmacy laptop.
- Alerts upload only bounded encrypted clips under the configured retention policy.
- Temporary live view uses short-lived credentials, selected cameras, visible session status, bandwidth limits and automatic expiry.
- Platform support access requires a customer-scoped grant with a reason and expiry.
- The product does not use facial recognition, cross-visit watchlists, body-language criminality scores or automated theft findings.
- Failed authorisation, stale health or lost source freshness fails closed and removes the live indicator.

## Implementation sequence

### B0 — Confirm policy and network constraints

Record the company hierarchy, branch administrators, support-access policy, clip duration and retention, permitted live-view purposes, available upload bandwidth and recorder restrictions. Confirm the first Harbour Pharmacy camera route and reviewer.

### B1 — Separate health and central inventory

Extend the cloud contract so every registered laptop reports application, camera, detector, alarm and queue health independently. Build the all-pharmacy dashboard, branch filters, fault summaries and last-known timestamps.

**Gate B1:** a simulated source loss and detector failure are distinguishable from a disconnected laptop, and tenant isolation tests pass for every role.

### B2 — Deliver alerts and incident evidence

Connect the Plan U local detector outbox to cloud alert creation. Add bounded encrypted clips, retry, duplicate protection, expiry, review actions and incident linkage.

**Gate B2:** the tested sequence is detection → local alarm → cloud alert → clip → acknowledgement → benign dismissal or incident creation, including an offline retry.

### B3 — Add the camera wall

Display the confirmed branch layout using freshness-aware previews. The wall shows four or six camera positions only when the corresponding local sources are healthy. Recorded or stale frames receive clear labels.

**Gate B3:** actual four-camera and six-camera layouts pass mapping, source-loss, stale-frame and reconnection tests without mixing branches or camera identities.

### B4 — Add temporary remote live access

Use an outbound laptop connection and time-limited session negotiation so no inbound pharmacy firewall port is required. Enforce viewer role, branch scope, reason, expiry, concurrency and audit before relaying selected camera media.

**Gate B4:** unauthorised and expired sessions fail; approved sessions terminate at expiry; bandwidth degradation is visible; access and termination appear in the audit log.

### B5 — Add bounded remote operations

Add signed commands for health refresh, reconnection, application restart and alarm testing. A command cannot operate unrelated processes or physical security systems.

**Gate B5:** duplicate, expired, replayed and out-of-scope commands are rejected; successful and failed attempts are auditable; local recovery remains available.

### B6 — Commission branch by branch

Run privacy, access, camera mapping, clip, live-view, alarm and recovery acceptance at each branch. Activate only accepted cameras and roles, then monitor alert load and service health during the supervised pilot.

## Relationship to Plan U

[Plan U](plan-u.md) builds the complete pharmacy laptop application and its real CCTV-to-cloud detection path. Plan B builds the authorised central control centre above that path. B1 can begin with current heartbeat data, while B2–B6 depend on the relevant Plan U camera, detector, packaging and commissioning gates.

## Completion rule

Plan B is complete only when at least one real branch has passed the central alert, encrypted clip, staff review, temporary live-view and recovery journeys, tenant-isolation tests pass, and each production-enabled branch has its own signed acceptance record. A dashboard mock-up, connected laptop or successful cloud deployment does not complete Plan B.
