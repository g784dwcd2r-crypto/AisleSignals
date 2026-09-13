# AisleSignals six-pharmacy release plan

Owner: Jawahir Q. · Target: Tuesday, 15 September 2026

## Confirmed scope

Six pharmacies use a mix of existing MacBooks and Windows laptops. No additional hardware is required or purchased. Exact branch names, laptop specifications, camera interfaces and authorised test footage have not been supplied. The development budget restriction has been relaxed; no paid cloud service or model entitlement has been purchased.

The implementation target is a protected, staff-supervised local release: named access, group/branch isolation, supported video input, experimental local interaction analysis, reviewed alerts, private sampled evidence, reliable startup/shutdown and recorded commissioning checks. The reference classifier is a general pretrained model. Software completion does not establish pharmacy theft-detection accuracy, uninterrupted monitoring while laptops sleep, or acceptance of a client camera that has not been tested.

## Ten specialist workstreams

The team comprises nine specialist agents plus the coordinating lead: ten agents in total. They cover the ten workstreams in bounded waves with three simultaneous specialist slots. The monitoring specialist takes the testing-coordinator assignment after finishing monitoring work. This respects the environment's total agent limit. A completed workstream does not imply that the agent remains running or provides unattended operational support.

| Workstream | Owned responsibility | Completion evidence |
|---|---|---|
| 1. Identity and branch access | Protected pilot mode, named accounts, memberships and branch switching | Authentication, stale-tab, revocation and cross-branch tests |
| 2. Release and regression checks | Diagnose failed Linux browser run; stable checks and executable packaging | Actual CI logs, reproducible tests, artifact checksums |
| 3. Installation and process recovery | Local launcher, model/API lifecycle, preflight reports | Owned-process cleanup, port conflict and startup tests |
| 4. Monitoring runtime | Frame continuity, session/visibility recovery, explicit source health | Stall, source change, pause, resume and stale-result tests |
| 5. Evidence protection and recovery | Protected local media, bounded storage, expiry and backup/restore tooling | Confidentiality, integrity, scope and recovery tests |
| 6. Alarm and review workflow | Supervised alert commissioning, conspicuous feedback, acknowledgement | Alarm cancellation, duplicate suppression and review tests |
| 7. Pharmacy-group interface | Non-demo sign-in, allowed branch selector, clear setup state | Browser account/branch journeys and accessibility |
| 8. Independent security review | Review authentication, media, requests, secrets and release defaults | Reproduced findings and regression checks |
| 9. Detection evaluation | Evaluate observable actions, negatives and limitations; prevent unsupported claims | Labelled-data checks, benchmark tooling and actual inference evidence |
| 10. Six-pharmacy test coordination | Collect per-site reports, retain unresolved checks, produce rollout decisions | Six-slot readiness matrix; no invented site acceptance |

## Release decisions

Each pharmacy is accepted independently. A successful software build is necessary but insufficient for turning on operational alerts. The test coordinator can execute software checks and collate reports; a person at each pharmacy must supply authorised access, check the camera view and verify physical speaker output. There is no remote access to the six laptops in this workspace.

| Gate | Required evidence | If missing |
|---|---|---|
| Reproducible software | Required source tests and platform build checks pass | Do not publish a release candidate as accepted |
| Protected workspace | Named users, branch isolation, no demonstration accounts in pilot data | Keep client data out of the demonstration workspace |
| Laptop readiness | Supported OS/architecture, free space, model startup, measured inference latency | Mark that laptop NOT_READY; preserve diagnostics |
| Camera readiness | Authorised view, readable product interaction, fresh frames, recovery checked | Do not claim that source is monitored |
| Detection evaluation | Held-out normal and staged action examples; missed events and false-alert workload measured | Continue controlled/silent evaluation; no reliability claim |
| Attention output | Staff-enabled speakers, audible test, mute/ack/stop and stale-result checks | Visual review only; no physical delivery claim |
| Evidence and recovery | Scope, deletion, expiry and restore drill verified | Resolve failures before client evidence collection |
| Operator handover | Named on-site reviewer, tested startup/shutdown and escalation procedure | No unattended operational reliance |

The current group model is local to one installation. Membership switching is not a hosted central dashboard or live synchronisation between six independent laptops. A shared service would require an explicitly configured deployment, transport security, credentials and operational ownership.

## Verification record

Implementation and acceptance results are recorded as they are actually obtained. The earlier prototype counts are historical. The prior Linux browser frame-readiness race was reproduced and fixed; the current local browser suite passed. See verification.md for integrated counts, inference evidence and the current-source remote run. Each branch evaluation must identify its model, prompt, application revision and platform.
