# Implementation status

Protected local release candidate — 13 September 2026. Product owner: Jawahir Q. Target: six pharmacies, Tuesday 15 September. Development team: nine specialist agents and the coordinating lead.

| Area | Current implementation and limit |
|---|---|
| Administration | Browser first-owner setup using a private, expiring local code. Managers create branches/users, assign roles, disable/re-enable and reset passphrases. Backend reauthentication, branch authority, last-manager protection, session revocation and stale-edit rejection. |
| Protected identity | Separate initially empty pilot database; named passwords, branch switching, current session authority and no public demo accounts. Local accounts only; no MFA, email invitations or hosted group synchronisation. |
| LIVE DETECTION | Explicit screen/device/recording input, camera-cropped pose boxes/keypoints, stable unique display matching and conservative limb/duplicate filtering. Versioned experimental rules; restart after camera-area changes, source loss, hidden tabs, sleep or stalled frames. [Quality review](docs/release/detection-quality-review.md). |
| Product interactions | Local pretrained Qwen3-VL sampled-frame analysis for pickup, return, basket placement, possible concealment and uncertainty; evidence and human review. Actual inference exercised locally. No measured client-pharmacy accuracy or theft determination. |
| Shared-window camera layouts | Local pixel check proposes 2 × 2, 3 × 2 or 2 × 3 layouts with measured separators and explicit confirmation. Manual layout/inset-board/custom-area fallback. Product analysis processes one selected camera tile; changing camera context cancels old work and disarms sound. See [camera layouts](docs/release/camera-layouts.md). |
| Attention alarms | Speaker commissioning, explicit arming, fresh source/results, duplicate suppression, visual feedback, acknowledgement and bounded sound. Physical audibility at the six branches is untested. |
| Evidence | Authenticated encryption of pilot JPEGs bound to branch/job/frame. Rolling bounded storage, 24-hour expiry and scoped retrieval/deletion. Database metadata relies on OS file protections/disk encryption. No continuous video archive. |
| Backup and restore | Offline encrypted package with authenticated extraction and integrity checks. Restore revokes sessions, clears setup capabilities, disables accounts for access review and cancels old jobs. |
| Incident workflow | Cases, reviews, tasks, outcomes, closure/reopening, audit and manager-approved local template exports. Demo observations remain synthetic. |
| Launcher | Foreground owned API/model supervision, checksum/preflight checks, bounded restart and explicit recovery after sleep. Does not adopt or stop unrelated services. |
| Windows/Mac support | All nine required CI jobs passed for source `c6c0b20`, including native Windows runtime startup and actual Windows x64/Mac arm64 executable journeys. Unsigned packages and checksums produced. Actual branch laptop inventory and acceptance remain missing. |
| Testing coordinator | Six-slot reports, versioned/hash-checked evidence intake and per-branch gates. All six real branches are NOT_RUN; no remote laptop access or invented signoff. |
| Commercial offer | EUR 60 monthly per pharmacy branch; no new hardware. Creating a branch does not bill or activate a subscription. |

See [release verification](docs/release/verification.md), [administration](docs/release/pilot-administration.md), [installation](deployment/README.md) and [six-branch coordination](docs/release/rollout-coordination.md).

The approved next desktop-to-cloud programme is [Plan U](docs/release/plan-u.md): complete Mac/Windows applications, signed lifecycle-managed installers, qualified RTSP/ONVIF and CCTV-window capture, pharmacy layout calibration, measured detection evaluation and the commissioned CCTV-to-cloud review flow.

The approved central operations programme is [Plan B](docs/release/plan-b.md): a role-scoped multi-pharmacy dashboard, freshness-aware four/six-camera branch views, encrypted alert clips, temporary audited live access, central review and bounded remote diagnostics. Plan B is planned work; it is not implemented or commissioned yet.

This is a candidate for supervised local evaluation. Signed distribution, exact laptop/camera qualification, normal-shopping and staged-interaction evaluation, physical alarm acceptance and operator handover remain necessary before operational reliance. No facial watchlists, person-level criminality prediction, simultaneous all-camera product analysis, direct RTSP ingestion or monitoring while a laptop sleeps. The full handover design remains a roadmap.

## Historical prototype record

The following record predates the protected release above; its counts and CI links apply only to that earlier source.


Prototype 0.1 — 13 September 2026. Product owner: Jawahir Q.

| Area | Status | Evidence / boundary |
|---|---|---|
| Local application | Implemented and browser-tested | React/TypeScript + FastAPI + persistent SQLite; loopback only |
| Incident workflow | Implemented | Synthetic observation review, benign dismissal, manual case, tasks, outcomes, closure/reopening |
| Authentication and scope | Prototype implemented | Cookie sessions, CSRF/origin/host checks, manager/reviewer roles, organisation/site query isolation; managed MFA/PostgreSQL RLS remain planned |
| Report and export | Implemented | Zero-cost local factual template, staff approval, manager JSON export and integrity digest; no cloud AI |
| Failure handling | Tested locally | Idempotent retries, duplicates, stale versions, outages, expiry, prior-session responses and bounded request bodies |
| Laptop readiness utility | Implemented | 39 tests, real local Mac preflight; opt-in bounded RTSP metadata probe, no recording |
| Standalone Mac prototype | Built and smoke-tested locally | Actual executable serves JS/CSS, signs in, reads scoped data and logs out; unsigned development distribution |
| Windows and Mac automated builds | Passed in GitHub Actions | Windows x64 and Mac arm64 bundles built; actual executables passed startup, assets, login, scoped data and logout checks in [run 34762107616](https://github.com/g784dwcd2r-crypto/AisleSignals/actions/runs/34762107616) |
| Accessibility | Checked on three core browser views | Automated checks pass for login, overview and review; captions enlarged and contrast improved; not a complete accessibility certification |
| Pricing | Preserved | EUR 60 monthly per branch; owner-managed record only, no charging |
| Mari Mina Pharmacy profile | Implemented | Separate demo organisation/branch and manager sign-in; initially empty records; additive startup preserves existing workspaces |
| Local video-file test | Implemented | MP4/WebM playback, bounded local frame-change scan, timestamp review and JSON summary; no upload, trained AI, live alarm or case creation. [Guide](docs/prototype/video-test.md) |
| Recorded playback attention alarm | Implemented locally | Explicitly armed playback, bounded laptop tone, visual-change categories and persistent branch test log. [Guide](docs/prototype/attention-alarm-test.md). No trained interaction detector or verified physical audibility |
| Real pharmacy YouTube reference | Added as reference | FBI pharmacy video can be opened on YouTube or click-loaded in a reference iframe; it is not analysed. Publisher's direct file download returned 403 here; authorised local footage still needed for a real-video detection test |
| LIVE DETECTION tab | Implemented and locally tested | Explicit screen/device/file input, real local pose model, person/keypoint overlays, experimental temporal rules, automatic laptop attention sound and saved branch metadata. [Guide](docs/prototype/live-detection.md) |
| Product interaction analysis | Implemented locally, experimental | Actual local Qwen3-VL sampled-frame inference, action categories, authenticated frame evidence, review/deletion, opt-in attention sound and camera-area selection. [Guide](docs/prototype/product-interactions.md). No client-pharmacy accuracy claim |
| Interaction model evaluation | Implemented tooling | Labelled local manifests, camera/day/person split checks, per-class metrics, explicit abstention and measured-exposure requirements. [Guide](docs/prototype/interaction-evaluation.md). Authorised pharmacy evaluation data still required |
| Pharmacy detector qualification | Pending | Repeated reach-to-waist and configured zone rules are review signals; no validated theft classifier, site camera connection or measured pharmacy accuracy |
| Browser/app/separate-monitor CCTV paths | Browser screen/device slice implemented | User-selected CCTV window or browser-accessible camera; standalone monitor still needs recorder viewer/software access. Direct RTSP/grid-tile adapters remain planned |
| Sampled evidence / production media | Short-lived local JPEG evidence implemented | Scoped retrieval, integrity checks, expiry and deletion for interaction samples. Encrypted production capture/storage, holds, rights workflows and continuous clips remain production work |
| Signed installers / pharmacy laptop acceptance | Not completed | Windows and Mac bundles are developer prototypes; exact pharmacy hardware/OS versions remain unqualified |
| Production hosting and alarm control | Not deployed/connected | No cloud accounts, physical outputs or customer data activated |

Latest local verification: **88 Python tests + 75 frontend unit tests + 32 browser tests passed** (195 tests), plus 49 companion subtests. Live Detection passes TypeScript/build; real local model inference was exercised in Chromium. Deterministic synthetic pose inputs verify alarm/log/acknowledgement wiring; this does not measure theft accuracy. Current Windows and pharmacy-device acceptance remain pending. See [verification evidence](docs/prototype/verification.md).

All six remote jobs also passed for source commit `89d1dfb7c013d40db4162ecac132e0f4c183e20a`: Python on Linux, Windows and Mac; interface build/browser journeys; Windows and Mac executable bundles. The local Mac archive was extracted and its executable smoke-tested again. The subsequent documentation update records these results and adds the interface screenshot; it does not change executable source.

The full design under docs/handover remains the production roadmap. Its 90 planned acceptance checks are not all implemented by this prototype, and remain labelled NOT_RUN in that historical design pack. The next release qualifies the existing camera interfaces and implements one supported detector/companion route while preparing production identity, storage and operational controls.
