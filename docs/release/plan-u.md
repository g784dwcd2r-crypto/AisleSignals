# Plan U — Pharmacy desktop detection release

Owner: Jawahir Q.  
Product: AisleSignals for pharmacies  
Commercial baseline: EUR 60 monthly per pharmacy branch  
Hardware rule: use the pharmacy's existing Windows or Mac laptop and existing CCTV  
Status: approved implementation plan; completion evidence is still required

## Objective

Plan U connects the existing cloud management workspace to a complete pharmacy laptop application. Its exit condition is one measured, repeatable path from an authorised CCTV source through local analysis and a commissioned attention alarm to the cloud alert queue and a recorded staff review. The path must then pass separate acceptance at every enabled branch.

Registration and a healthy heartbeat establish device identity and connectivity only. They do not establish camera coverage, active detection, alarm audibility or detection accuracy.

## Workstreams

| ID | Deliverable | Current baseline | Completion evidence |
|---|---|---|---|
| U1 | Complete downloadable macOS and Windows application | Source application, local runtime, unsigned development bundles and cloud connection companion exist separately | A clean supported Mac and Windows laptop can install, pair, launch, stop and uninstall the complete application without a development checkout or separate Node/Python installation |
| U2 | Signed installers, automatic updates and startup on login | Startup/recovery and packaging work exists, but production signing and update distribution are incomplete | Notarised macOS package and signed Windows installer; staged signed update and rollback; explicit startup setting; sleep, resume, low-disk and failed-update tests |
| U3 | Direct RTSP/ONVIF and reliable CCTV application capture | User-selected browser/window/device capture and local recording tests exist; direct RTSP is not an accepted live route | Supported recorder/camera inventory; local protected credentials; RTSP/ONVIF discovery where available; native viewer/window capture fallback; freshness, reconnect, codec and four/six-view tests on actual pharmacy equipment |
| U4 | Pharmacy layout calibration | Manual camera crop and grid confirmation exist; semantic pharmacy zones are not commissioned | Versioned per-camera entrances, exits, cashier, shelves, staff/restricted areas and privacy masks; calibration preview; change invalidation; manager acceptance for each enabled view |
| U5 | Validated detection model and measured error rates | Pose rules and sampled-frame interaction classification are experimental; client-pharmacy evaluation is NOT_RUN | Predeclared per-branch evaluation plan; independently labelled normal and staged interactions; held-out results; false alerts per normal camera-hour, missed-event rate, abstention, latency and confidence intervals or explicit limits; approved threshold/model version |
| U6 | Real CCTV-to-cloud workflow | Cloud device identity, observation intake, alerts, review and incidents exist; the installed detection runtime is not commissioned end to end | On the actual branch laptop: CCTV event → local evidence/rule → alarm decision → encrypted queued delivery → cloud alert → staff acknowledgement/review → incident or benign outcome, including offline retry, duplicate suppression and source expiry |

## Implementation order

### U0 — Freeze the supported pilot inventory

Record the exact branch, laptop OS/version/architecture, CCTV vendor/model, authorised connection method, camera count, codecs, staff reviewer and speaker for each site. Select one Harbour Pharmacy laptop and one camera view as the first integration path. Unsupported equipment stays explicitly unsupported; Plan U does not add hardware.

### U1 — Unite packaging and cloud identity

Package the local AisleSignals interface, API, model supervisor and cloud sender as one desktop product. Replace the connection-only download with OS-specific installers while retaining one-use pairing. The installer must preserve private device credentials, expose connection/camera/detector/alarm health separately and never treat a heartbeat as monitoring.

**Gate U1:** clean-machine install and pairing pass on one Mac and one Windows laptop; restart and uninstall preserve or remove data according to the operator's explicit choice.

### U2 — Add lifecycle and signed delivery

Implement opt-in startup on login, single-instance ownership, sleep/resume recovery, offline spooling, staged automatic updates and rollback. Sign Windows and macOS artifacts using owner-controlled certificates. Unsigned development packages cannot satisfy this gate.

**External dependency:** Apple Developer ID/notarisation access and a Windows code-signing certificate must be supplied before signed-installer acceptance.

**Gate U2:** signature verification, upgrade, rollback, failed-update, startup, shutdown and offline-recovery tests pass on both operating systems.

### U3 — Qualify CCTV inputs and calibrate the site

Prefer direct, read-only RTSP with ONVIF discovery when the existing equipment exposes supported interfaces. Retain explicit browser/native-window capture when direct streams are unavailable. Detect proposed four/six-camera mosaics locally, require confirmation and preserve a named mapping from each tile to its physical view. Add freshness and reconnect diagnostics.

For every enabled view, calibrate the usable camera area, entrances, exits, cashier, product shelves, staff zones, restricted areas and privacy masks. A calibration or camera-layout change disarms detection and requires requalification.

**Gate U3:** the actual pharmacy manager confirms the view, zones and privacy exclusions; frozen streams, window loss and reconnection produce correct health states and never a false "active" state.

### U4 — Evaluate and tune detection

Start with observable product interactions: take, return, basket placement, prolonged handling, possible concealment and exit-with-unresolved-item sequences. Combine person/hand/product/zone continuity over time and allow `UNCLEAR`; do not infer intent, identity or criminality from body language.

Collect authorised normal trading samples and staged actions for each camera. Freeze a held-out split before tuning. Report per-class precision/recall, missed events, abstention, alerts per normal camera-hour and action-to-alert latency. Thresholds remain per site, camera, model and rule version. Keep alarms in silent or attended evaluation until agreed limits pass.

**Gate U4:** the pharmacy manager and product owner accept the measured alert burden and miss rate for each enabled class. A passing result at one site does not qualify another site.

### U5 — Commission the complete workflow

Run the real sequence with the laptop online, offline and after recovery. Verify alarm test, arming, acknowledgement, mute/cooldown, stale-result suppression, encrypted delivery, duplicate handling, cloud display, staff review and incident creation. Confirm that a benign review does not create an incident and that historical buffered events cannot trigger a current alarm.

**Gate U5:** a signed commissioning record identifies the branch, laptop, camera, application/model/rule versions, test timestamps, alert latency, speaker result, reviewer and rollback procedure.

### U6 — Roll out branch by branch

Repeat U1–U5 for every pharmacy. Enable only accepted cameras and detection classes. Monitor alert volume, missed staged checks, downtime and review completion during a supervised pilot before wider operational reliance.

## Release rules

- The cloud dashboard may show **Online** while camera or detector status is unknown; these signals remain separate.
- No automatic theft finding is produced. The detector creates an observation for human review.
- No facial recognition, cross-visit watchlist, person-level risk score or body-language criminality inference is part of Plan U.
- CCTV credentials and source footage stay local unless a separately authorised evidence item is uploaded under the retention policy.
- A software test cannot certify physical speaker audibility or camera suitability. A person at the branch records those results.
- Failed camera, detector, freshness or calibration checks fail closed and visibly remove coverage.

## Plan U completion

Plan U is complete only when U1–U6 have evidence for at least the first supported pharmacy and every production-enabled branch has its own acceptance record. Repository checks, a successful Render deployment and a connected laptop are necessary evidence, but none alone completes Plan U.
