# AisleSignals: existing-laptop deployment

13 September 2026. Mandatory baseline: use the pharmacy's existing laptop and existing CCTV/cameras. No appliance, replacement laptop, GPU, capture adapter, relay, display or other hardware purchase is part of this design. The commercial target is €60 per branch; define billing period, VAT and support limits in the commercial plan. This note does not claim that every existing laptop or camera is suitable. The pilot has one Windows laptop and one Mac; both platforms are required in the first pilot. Actual OS versions and processor architectures still need inventory.

## Conservative first scope

Install a local companion with a background worker, authenticated local settings and a link to the hosted review UI. Package its runtimes; do not require pharmacy staff to operate Docker, terminals or a development database. Use a local transactional spool database and native secret storage; incident records and review remain in the hosted API in this baseline. Keep original continuous recording on the existing recorder. Start with **one or two qualified views**, manual incidents, private software sounds, review/evidence workflows and camera-health monitoring. Increase view count only after benchmarking that laptop during normal pharmacy work.

Camera access, recording and useful theft detection are separate capabilities. Local motion/zone rules can identify activity; they do not establish concealment or nonpayment. Enable a lightweight, commercially usable temporal detector only after proving its action-specific performance and resource cost on that machine. General-purpose AI video analysis, continuous cloud streaming, biometric identity, clinical decisions and unlimited camera coverage are outside the €60 baseline. Optional small report-drafting jobs must have a hard budget and a manual fallback.

## Windows and macOS packaging

| Platform | Proposed first package | Operating boundary |
|---|---|---|
| Windows | Signed installer containing the local worker, UI assets and tray launcher; register an explicit start-at-login task for the pharmacy's normal OS account. | Least-privilege user execution. Locking the screen may leave processing running; signing out stops this initial per-user deployment. A Windows service is a later alternative if unattended pre-login operation is required and tested. |
| macOS | Signed and notarized app bundle with a login/background helper; package and test Intel and Apple Silicon builds only as required by actual sites. | Use the supported ServiceManagement registration path for the selected OS. Per-user helper starts after login and remains subject to system/user background-item controls. A privileged daemon is not the default. |

Apple documents `SMAppService` for login items, agents and daemons on macOS 13 and later; registration remains subject to approval. Choose the actual supported OS versions before building packages. [Apple ServiceManagement](https://developer.apple.com/documentation/ServiceManagement/SMAppService?language=objc)

A Windows background service cannot directly display normal interactive UI; a separately authenticated user-session component would handle review and sound. Do not solve this by running the UI as LocalSystem. [Microsoft interactive services](https://learn.microsoft.com/en-us/windows/win32/services/interactive-services)

Build and test both Windows and macOS installers for the confirmed pilot, against the same contracts. Keep OS-specific startup, notifications, credentials and updates behind small adapters. Signed distribution and update costs belong in the business budget even though no equipment is purchased.

## Existing camera access paths

1. **Existing IP recorder:** request authorised channel/substream access through its documented RTSP/ONVIF or supported vendor interface. Prefer the recorder path when it already aggregates the cameras and has capacity for the additional clients.
2. **Direct IP camera:** use an authorised local stream where the camera is reachable and permits another client. Do not assume the recorder's private camera network is reachable from the laptop.
3. **Analogue cameras on an existing network DVR:** use the DVR's existing network output. Analogue wiring by itself cannot become a laptop stream without hardware; if that DVR exposes no supported output, mark live integration unavailable.
4. **Vendor desktop/cloud viewer only:** require a documented, permitted stream/API. Screen-scraping the viewer is not the production integration. Manual exported clips can support retrospective incidents, clearly separated from live monitoring.

ONVIF profiles describe capabilities, not a guarantee about every installed device. Profile T includes H.264/H.265 streaming and event capabilities; Profile S supports basic streaming on existing equipment. Verify firmware, codec, permissions and actual stream behaviour. Do not open CCTV ports to the public internet or bypass recorder authentication. [ONVIF Profile T](https://www.onvif.org/profiles/profile-t/), [Profile S](https://www.onvif.org/profiles/profile-s/)

## Sleep, closed lids, disconnection and restart

The service works only while the laptop is awake, powered, connected to the relevant network and running its worker. Trading-hour coverage is the promise to test; 24-hour coverage is not assumed.

- Recommend the existing power adapter and lid open during monitored hours. Offer an explicit trading-hours idle-sleep preference without disabling screen locking. Do not claim software can override intentional sleep, power loss or every closed-lid behaviour. Microsoft explicitly says its execution-state API cannot prevent user-initiated sleep. [Microsoft power API](https://learn.microsoft.com/en-us/windows/win32/api/winbase/nf-winbase-setthreadexecutionstate)
- On Mac, preventing automatic sleep while on power is distinct from guaranteeing operation with a closed lid. Record the actual tested configuration; do not purchase an external display to make it work. [Apple sleep settings](https://support.apple.com/en-ie/guide/mac-help/mchle41a6ccd/mac)
- Detect shutdown/suspend where possible and reconcile gaps on restart. A powered-off laptop cannot report its own outage live; the required low-bandwidth remote heartbeat can expose missed check-ins, but cannot restore coverage.
- Internet loss leaves local health/settings available, but hosted review and cloud AI are unavailable. Permitted local capture can continue only within the signed capture lease, at most 15 minutes; it then stops. Camera-network loss stops the affected views immediately when detected. On recovery, delayed events are historical and never produce stale alarm actions.
- Start automatically after the tested OS login, recover the durable queue and reconnect with backoff. Never enable automatic OS login or weaken disk encryption to avoid startup gaps. Updates occur outside trading hours where practical, with integrity checks, rollback and a post-restart health check.

## Resource and security boundaries

Prefer a recorder substream, bounded decoder queues and a single worker pool. Initial **benchmark budgets**, subject to site acceptance: no more than 25% mean total-machine CPU over a busy 30-minute sample and 1 GB application memory; ten minutes of normal dispensing/till tasks must show no material slowdown. Do not reduce temporal sampling below what the accepted detector requires merely to hit a CPU target. Pause optional analysis and display degraded coverage before interfering with pharmacy work.

Propose a 2 GB rolling media spool, separately capped incident evidence, and reserved free space of 5 GB as an initial host free-disk guard. Size these against actual event volume before activation. Expire disposable buffers first; preserve held evidence within its agreed quota and report capacity failure. Never delete pharmacy files or silently drop evidence. No continuous cloud video backup is included by default; document what survives laptop loss.

Bind the local API only to loopback. Require named application users, short session locking, role checks, strict Host/Origin validation, CSRF protection and authenticated worker/UI communication. Never rely on localhost alone for authentication. Keep CCTV passwords in Windows-protected credential storage or macOS Keychain, with redacted logs. Give runtime processes only their own application directories and read-only camera access. Exclude prescription systems, consultation views and audio collection. Remote support is explicit, scoped and logged.

## Sounds and an existing alarm

The first output is a short staff sound through the laptop's existing speakers plus a visible private alert. Test mute, Focus/Do Not Disturb, screen lock, browser/desktop permissions and acknowledgement. A sound played by software does not prove somebody heard it. Keep existing emergency arrangements independent.

An optional already-installed alarm may be controlled **only** through a documented, authorised existing API that requires no relay or additional equipment. Use separate credentials, deliberate staff activation, correct-site checks, expiring/idempotent commands and actual feedback where available. AI cannot trigger it. If no suitable existing API exists, this option is unavailable; there is no hardware workaround within scope.

## Measurable readiness gates

1. Record OS/build, CPU, memory, disk, normal concurrent pharmacy workload and permitted installation/startup access.
2. Prove each selected stream continuously for a trading day, including ordinary recorder use; verify exclusions and recording remain intact.
3. Pass the resource budget during busy work and a full-day thermal/stability run; no material pharmacy-task degradation.
4. Exercise lock/unlock, sign-out/login, restart, lid/sleep, power/battery transition, internet loss and camera-network loss. Every gap must be visible; propose reconnection within 60 seconds after login/network readiness.
5. Demonstrate authenticated settings and hosted review, cross-user restrictions, replay-safe queues, retention and a failed-disk-capacity condition using test data.
6. Measure detection separately: proposed p95 alert latency ≤10 seconds, ≥80% staged recall per enabled class/view, ≤3 nuisance alerts/day and ≤10 minutes/day review after tuning. State counts and uncertainty; a stream test cannot pass this gate.
7. Confirm branch delivery costs fit the €60 price after licences, limited cloud work, signing, support and tax treatment. No unpriced proprietary detector or always-on human monitoring is assumed.

If a gate fails, reduce the accepted camera/event scope or retain manual incident workflows. Record the limitation plainly. The remedy under this mandate is software/configuration or narrower eligibility—not buying hardware.
