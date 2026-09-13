# Protected release verification

13 September 2026. Accounts and footage used in automated checks are synthetic. Existing local preview records were preserved separately; they are not published as fixtures.

## Local checks

- Python API, security, identity/administration, deployment, recovery, evaluation, coordinator and packaging checks: **363 passed, plus 49 companion subtests**. Two dependency deprecation warnings; no failures. A subsequent isolated-source launcher regression caught and fixed a missing source import during interactive setup; all **32 launcher checks** then passed and the real protected API/model launcher started successfully.
- Production interface build and TypeScript passed. **132 frontend unit tests passed**; formatter check passed. **52/52 Chromium browser journeys passed**, including real-backend administration/identity, desktop/mobile accessibility, source continuity, alarm cancellation and review conflicts.
- Actual Mac local Qwen3-VL control through a protected session: **3.901 seconds** end to end, **3,822 ms** model latency. A blank synthetic control returned `UNCLEAR`, no visible person/product and no alarm. Encrypted storage, authenticated JPEG retrieval, review and deletion passed. This does not establish pharmacy sensitivity or false-alarm performance.
- Mac native model runtime starts and advertises the required arguments. The pinned Windows x64 archive digest and server/dependency paths were checked locally; its native startup check also passed on the actual Windows CI runner.
- A rebuilt and extracted unsigned Mac pilot executable passed startup, bundled assets and account/download/recovery/coordinator commands, actual one-time owner setup and token consumption, Administration roster access, named login, scoped empty bootstrap, JPEG job processing and disabled-provider checks. It is a developer-tested application bundle; no physical camera or speaker acceptance is implied.

Commands: `python -m pytest tests packaging/test_bundle_integrity.py -q`; `npm run build`; `npm run test:web`; `npm --prefix apps/web run format:check`; `npm run test:e2e`; `python scripts/package_bundle.py --bundle-name AisleSignalsPilot --smoke`.

## Remote platform checks

The required workflow checks Python and Chromium on Linux, Windows and Mac, native pinned Windows model-runtime startup, and actual Windows/Mac prototype and pilot executables. The first protected run, [34776822181](https://github.com/g784dwcd2r-crypto/AisleSignals/actions/runs/34776822181), passed Linux Python, Linux/Mac browser journeys and native Windows model-runtime startup. It found Windows newline/path portability defects and two Mac synthetic subprocess startup timeouts; the corrections preserve strict archive/path checks and remove unnecessary fixture DNS resolution. Windows formatting now uses explicit repository line endings. The correction run [34777295024](https://github.com/g784dwcd2r-crypto/AisleSignals/actions/runs/34777295024) passed all seven application checks and the Mac desktop build. Windows completed the pilot functional smoke but exposed orphaned API/file handles after abrupt launcher termination. The launcher now uses atomic Windows Job Object containment, and the package smoke refuses a lingering API. Native Windows lifecycle cases and the final executable rerun remain required for the current source.

## Six real pharmacies

The coordinator's initial manifest has six unassigned branch/device slots: **0/6 accepted**, all **NOT_RUN**. No client camera, physical speaker, Windows/Mac permission dialog, sleep recovery or site detection-accuracy acceptance has been observed. The coordinator can collate supplied evidence and run available software checks; it cannot operate an inaccessible laptop or certify physical audibility remotely.

The general model must be evaluated on held-out authorised normal-shopping and staged interaction examples from each proposed view. Frame sampling can miss short actions and obscured products. A refreshing screen share may still show a frozen upstream CCTV feed; staff must verify the recorder timestamp/view. None of the automated counts substitutes for these checks.
