# Protected release verification

13 September 2026. Accounts and footage used in automated checks are synthetic. Existing local preview records were preserved separately; they are not published as fixtures.

## Recording-review improvements

The [detection-quality review](detection-quality-review.md) records the three supplied recordings, implemented tracking/camera/sampling fixes and a separately evaluated person-detector candidate that was not adopted.

- Coordinating lead reproduced **235 frontend unit tests**, production build, TypeScript and formatting checks.
- **All 66 browser journeys passed across the full run and one targeted retry**. The full run passed 65; the new pointer-drawn-area test used an unsuitable label locator for the select element. Using its exposed combobox role fixed the fixture, and the unchanged pointer/crop/worker assertions then passed. Actual pose bitmap dimensions and dominant camera colours match the selected product JPEGs for all three grid shapes.
- **13 live-event API tests passed**, including preserved v1 history, accepted `pose-rules-v2` provenance and rejection of unknown versions. An older invalid-input example was advanced to v3 because v2 is now explicitly supported. Scope, CSRF and authority checks are unchanged.
- Visible protected-browser verification showed 1/4 sampling progress, Camera 3-only JPEGs, local automatic model completion, an advancing current buffer and a fixed submitted four-frame strip with timestamps/dimensions. The synthetic pattern returned `UNCLEAR`; no sound was armed. This checks wiring and evidence presentation, not retail accuracy.

The preceding camera-layout source `b87f76f` subsequently passed all nine jobs in [workflow 34782199540](https://github.com/g784dwcd2r-crypto/AisleSignals/actions/runs/34782199540), including Windows/Mac executable builds. Those remote results do not yet qualify the newer recording-review changes described above. Earlier downloaded archives remain their explicitly labelled source versions.

## Camera-layout and product-readiness update

The subsequent shared-window layout change adds local 2 × 2 / 3 × 2 / 2 × 3 proposals, measured source-coordinate crops, explicit camera confirmation, manual/inset-board fallback and camera-bound cancellation. Pose and product-model readiness are displayed separately. This updates source and the local browser application; the previously downloaded executable archives still identify `c6c0b20`.

- Coordinating lead: **213 frontend unit tests**, production build, TypeScript, formatting and diff checks passed.
- **All 62 Chromium journeys passed across the full run and targeted retry**. The initial isolated-port run passed 53; seven setup fixtures lacked the local Python executable and two existing API test requests had hard-coded origins for port 8799. Setting the test Python path and deriving those request origins from the actual page fixed the test configuration; all nine affected cases passed on retry. No application authority check was weakened. The user-visible service on port 8799 was preserved.
- Eight new grid journeys exercise all three layouts, decoded JPEG camera colours/dimensions, transparent preview overlays, tile/board/source cancellation, early manual selection before decoded frames, re-detection, uncertain-frame fallback and narrow-screen accessibility. Two additional interaction journeys cover unavailable model status and explicit re-enabling after recovery.
- Visible protected-browser check: compressed synthetic six-camera recording proposed 3 × 2; Camera 2 was confirmed, sampled and submitted to the actual local model. The saved result retained the camera label and Camera 2-only frames. Inference took **1.9 seconds** and returned `UNCLEAR`, with no person/product/interaction sequence reported. No alarm was armed; this is pipeline verification, not pharmacy accuracy or physical speaker acceptance.

See [camera layout operation and limitations](camera-layouts.md). The platform results below belong to their explicitly named earlier source; subsequent CI must qualify updated executable builds separately.

## Local checks during integration

- Python API, security, identity/administration, deployment, recovery, evaluation, coordinator and packaging checks: **363 passed, plus 49 companion subtests**. Two dependency deprecation warnings; no failures. A subsequent isolated-source launcher regression caught and fixed a missing source import during interactive setup; all **32 launcher checks** then passed and the real protected API/model launcher started successfully.
- Production interface build and TypeScript passed. **132 frontend unit tests passed**; formatter check passed. **52/52 Chromium browser journeys passed**, including real-backend administration/identity, desktop/mobile accessibility, source continuity, alarm cancellation and review conflicts.
- Actual Mac local Qwen3-VL control through a protected session: **3.901 seconds** end to end, **3,822 ms** model latency. A blank synthetic control returned `UNCLEAR`, no visible person/product and no alarm. Encrypted storage, authenticated JPEG retrieval, review and deletion passed. This does not establish pharmacy sensitivity or false-alarm performance.
- Mac native model runtime starts and advertises the required arguments. The pinned Windows x64 archive digest and server/dependency paths were checked locally; its native startup check also passed on the actual Windows CI runner.
- A rebuilt and extracted unsigned Mac pilot executable passed startup, bundled assets and account/download/recovery/coordinator commands, actual one-time owner setup and token consumption, Administration roster access, named login, scoped empty bootstrap, JPEG job processing and disabled-provider checks. It is a developer-tested application bundle; no physical camera or speaker acceptance is implied.

Commands: `python -m pytest tests packaging/test_bundle_integrity.py -q`; `npm run build`; `npm run test:web`; `npm --prefix apps/web run format:check`; `npm run test:e2e`; `python scripts/package_bundle.py --bundle-name AisleSignalsPilot --smoke`.

## Remote platform checks

**All nine required jobs passed** in [workflow 34778628111](https://github.com/g784dwcd2r-crypto/AisleSignals/actions/runs/34778628111) for executable source `c6c0b209413ffd6f08a43c2d9188773684f2ea0c`.

| Check | Observed result |
|---|---|
| Python on Windows | 374 passed, 4 platform-specific skips, 49 companion subtests passed |
| Python on Mac and Linux | 373 passed, 5 native-Windows skips, 49 companion subtests passed on each |
| Interface on Windows, Mac and Linux | Build, TypeScript, formatting, 132 unit tests and 52 Chromium journeys passed on each |
| Pinned native Windows model runtime | Download integrity and actual server startup passed; pharmacy inference accuracy was not tested |
| Windows x64 and Mac arm64 executables | Prototype and protected pilot built, archived, extracted and smoke-tested successfully on their native runners |

The protected executable journey covers startup/assets, bundled account tools, private first-owner setup and code consumption, Administration access, named login, absence of demo identity, JPEG processing, disabled-provider behaviour, logout and API shutdown. The Windows Job Object tests also verify abrupt owner exit, nested containment, unrelated-process preservation and valid no-console logging streams. This closes the real orphaned-process defect found by the preceding run. No process cleanup check was weakened.

Earlier runs identified and corrected Windows path/newline portability and synthetic Mac startup timing defects. Repository line endings are explicit; recovery and coordinator archives preserve strict path checks. Windows children now enter a private kill-on-close job atomically and inherit only valid NUL standard streams.

The workflow publishes unsigned pilot archives and checksums for Windows AMD64 and Mac arm64. Their release manifests identify the executable source above and a clean source tree. A separate local Mac build from that same source also passed the extracted executable journey. Later documentation-only commits do not change the qualified executable source. Exact client OS versions and architectures still need qualification; these are not signed or notarised installers.

## Six real pharmacies

The coordinator's initial manifest has six unassigned branch/device slots: **0/6 accepted**, all **NOT_RUN**. No client camera, physical speaker, Windows/Mac permission dialog, sleep recovery or site detection-accuracy acceptance has been observed. The coordinator can collate supplied evidence and run available software checks; it cannot operate an inaccessible laptop or certify physical audibility remotely.

The general model must be evaluated on held-out authorised normal-shopping and staged interaction examples from each proposed view. Frame sampling can miss short actions and obscured products. A refreshing screen share may still show a frozen upstream CCTV feed; staff must verify the recorder timestamp/view. None of the automated counts substitutes for these checks.
