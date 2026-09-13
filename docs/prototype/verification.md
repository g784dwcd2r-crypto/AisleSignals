# Prototype verification

13 September 2026. Root reproduced the final tests after specialist-agent implementation and independent review. These results apply to the synthetic prototype, not real pharmacy monitoring.

| Check | Actual local result |
|---|---|
| API runtime tests | 24 passed on Python 3.12.14; agent also ran on Python 3.14 |
| Companion tests | 39 passed, plus 49 subtests in the combined pytest run |
| Combined Python suite | 63 passed |
| Frontend unit tests | 23 passed across three files |
| TypeScript / production build | Passed; self-contained local JS/CSS, no external font/CDN requirement |
| Source formatting | Frontend Prettier check passed; backend correctness checks reviewed |
| Browser journeys | 10 passed in Chromium via Playwright |
| Accessibility subset | Axe checks for WCAG 2 A/AA and 2.1 AA tags found no violations on login, overview and observation review |
| Dependency audit | npm audit reports zero known vulnerabilities for root and interface dependencies at the time checked |
| Actual Mac executable | PyInstaller bundle built; startup, HTML/JS/CSS assets, local login, scoped bootstrap and logout passed |
| Actual laptop preflight | Mac arm64 / Python 3.12.14; free-disk guard passed, no camera/network probe requested; FFprobe absent reported explicitly |
| Windows / CI Mac execution | Both actual executable smoke tests passed in GitHub Actions; remote results below |

## Browser acceptance journeys

1. Dismiss a returned-item observation without creating a case.
2. Create a manual case, resolve classification/outcome, add/complete a task, close, generate/approve the final template and download its JSON export.
3. Display missing-media and historical-event limitations.
4. Preserve unsaved case input through an API outage, disable writes, then save after recovery.
5. Preserve local edits when another staff action advances the server version; require explicit reconciliation.
6. Enforce reviewer controls and prevent another organisation from viewing copied evidence URLs.
7. Keep the workspace usable at 390 × 844 pixels without document-level horizontal overflow.
8. Let a reviewer add notes to a manager-confirmed-loss case without changing its financial values.
9. Suppress a delayed prior-session export after expiry and login to the other organisation.
10. Run automated accessibility checks on the three core views.

The delayed-export test keeps the same browser document alive, intercepts a successful response, revokes the session, advances passive refresh, logs into the other organisation and only then releases the old response. It checks that no old download or success notification is emitted. The retry unit test separately covers truncated 2xx bodies and late-response cleanup of a newer session's idempotency key.

## Independent review fixes

The root and agents reviewed each other's boundaries. Fixes reproduced before delivery include: polling no longer extending idle sessions; incomplete successful-response bodies retaining retry keys; stale authentication generations suppressing old results; reviewer edits sending only changed fields; final reports on closed cases; explicit image retry; offline local view locking; bounded body consumption; account-switch token revocation; exact configured local ports; redirect refusal and actual JS/CSS checks in the packaged smoke test.

Python test output includes dependency deprecation warnings about the current Starlette test-client HTTPX bridge and AnyIO alias. They did not fail execution; dependency upgrades should preserve the existing regressions. No real camera, customer footage, paid model call, alarm, signed installer or production deployment was used in these tests.

## Remote build results

[GitHub run 34762107616](https://github.com/g784dwcd2r-crypto/AisleSignals/actions/runs/34762107616) completed with **success** on 13 September 2026 at 14:17:50 UTC for source commit [`89d1dfb7c013d40db4162ecac132e0f4c183e20a`](https://github.com/g784dwcd2r-crypto/AisleSignals/commit/89d1dfb7c013d40db4162ecac132e0f4c183e20a). All six jobs were observed as completed/success:

| Job | Observed result |
|---|---|
| Python checks — Ubuntu | Passed, Python 3.12 |
| Python checks — Windows | Passed, Python 3.12 |
| Python checks — Mac | Passed, Python 3.12 |
| Build and browser journeys | Passed, Node 24; production build, unit tests, formatting and all 10 browser journeys |
| Desktop bundle — Windows | Passed; PyInstaller build, actual executable smoke check and archive upload |
| Desktop bundle — Mac | Passed; PyInstaller build, actual executable smoke check and archive upload |

The executable checks start the built binary with a temporary database, fetch its HTML and referenced JS/CSS, sign in, read scoped bootstrap data, sign out and stop the owned process. The locally built Mac tar.gz was also extracted and its extracted executable passed the same checks. These are host/build checks; acceptance on the two pharmacy laptops remains pending.

Download the artifacts from the run's **Artifacts** section:

| Artifact | Architecture | GitHub artifact ID |
|---|---|---|
| `AisleSignalsPrototype-Windows-X64-unsigned` | Windows x64 | 10319626179 |
| `AisleSignalsPrototype-macOS-ARM64-unsigned` | Apple silicon arm64 | 10318997715 |

Artifacts expire on 27 September 2026 under the 14-day retention policy. GitHub wraps each bundle in an outer ZIP: extract that, then the inner platform archive. The workflow can be run again to regenerate packages. Intel Mac and Windows ARM packages were not built or qualified.

The subsequent documentation-only commit adds this observed evidence and a screenshot; executable source and lockfiles are unchanged from the tested commit. CI deliberately ignores documentation-only pushes.

## Mari Mina Pharmacy profile update

The named profile addition passed the 65-test Python suite (26 API, 39 companion, plus 49 subtests), the production interface build and the 10 existing browser journeys. Two added API checks verify its empty initial records, cross-pharmacy access rejection, and concurrent/repeated upgrades of the older database while preserving casework, shift state and credential hashes. An independent read-only agent review found no blocking issues. The earlier six-job run above records the initial prototype source; the profile commit triggers a fresh workflow.

## Local video-file test update

Root reproduced **65 Python tests, 37 frontend unit tests and 15 browser journeys**, all passing (117 tests plus 49 Python subtests). This includes 14 visual-activity helper checks and five real-video browser journeys. The new browser checks cover decoded activity timestamps, preview seeking, local JSON download, no video uploads or incident writes, invalid-file recovery, held-seek cancellation, discarded late results, actual pause/source removal and blob revocation on navigation/sign-out, narrow layout and automated accessibility. The production TypeScript/build and frontend formatting checks also passed. No dependency or paid model was added. The original synthetic WebM is 6 seconds, 320 × 180, no audio; it is not pharmacy footage or an AI benchmark.

The Mac executable was rebuilt for this UI update. Existing historical GitHub run references above describe their own source commits; this feature's source push triggers a new platform workflow.

## Recording alarm and event-log update

Root reproduced **76 Python tests, 54 frontend unit tests and 24 browser journeys**, all passing (154 tests plus 49 Python subtests). New coverage consists of 11 API checks, 17 rule/audio helper tests and nine playback browser journeys. TypeScript/build, frontend formatting and `git diff --check` also pass.

Actual browser decoding and forward playback trigger a single classified test record; seeks and repeated callbacks do not duplicate it. Tests cover mute, blocked audio, delayed audio activation preserving the start of a recording, stop/navigation during activation, cancellation during rewind, persisted log reads and a lost successful save response retried with the same key. The reference iframe makes no external request before an explicit click and never becomes scanner input. Audio is instrumented to remain silent in the browser tests; physical audibility is not established by automation.

The running local Harbour workspace retains its nine observations, five cases and three assistance records. In the actual Codex browser, the FBI reference iframe loaded and played pharmacy CCTV footage (approximately 25 seconds) and was then paused. No reference footage was downloaded or analysed. The publisher's offered MP4 download returned HTTP 403 from this environment; no extraction workaround was used. No third-party media is included in Git or the desktop package.

The Mac arm64 executable was rebuilt and passed startup, real JS/CSS asset loading, scoped local login/bootstrap and logout smoke checks. The latest local preview serves this source against the existing Harbour/Mari Mina database. Remote CI results above remain historical; this source update requires its own workflow run.
