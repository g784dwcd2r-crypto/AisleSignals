# Implementation status

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
| Actual camera/detector | Not connected | Existing device/interface inventory and authorised software integration still required |
| Browser/app/separate-monitor CCTV paths | Specified for next release | [Automatic monitoring addendum](docs/prototype/cctv-monitoring.md) includes permitted screen capture, recorder access, freshness and resource limits; no live adapter implemented yet |
| Cloud AI / real evidence lifecycle | Not implemented | Provider evaluation, budgets, encrypted capture/storage, deletion/holds and rights workflows remain production work |
| Signed installers / pharmacy laptop acceptance | Not completed | Windows and Mac bundles are developer prototypes; exact pharmacy hardware/OS versions remain unqualified |
| Production hosting and alarm control | Not deployed/connected | No cloud accounts, physical outputs or customer data activated |

Latest local verification: **76 Python tests + 54 frontend unit tests + 24 browser tests passed** (154 tests), plus 49 companion subtests. The attention-alarm changes also pass TypeScript/build and frontend formatting. The real YouTube reference played in the Codex browser; it was not scanned. See [verification evidence](docs/prototype/verification.md).

All six remote jobs also passed for source commit `89d1dfb7c013d40db4162ecac132e0f4c183e20a`: Python on Linux, Windows and Mac; interface build/browser journeys; Windows and Mac executable bundles. The local Mac archive was extracted and its executable smoke-tested again. The subsequent documentation update records these results and adds the interface screenshot; it does not change executable source.

The full design under docs/handover remains the production roadmap. Its 90 planned acceptance checks are not all implemented by this prototype, and remain labelled NOT_RUN in that historical design pack. The next release qualifies the existing camera interfaces and implements one supported detector/companion route while preparing production identity, storage and operational controls.
