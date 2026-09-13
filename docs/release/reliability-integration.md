# AisleSignals — Reliability and reviewed-case release

Prepared for Jawahir Q. · 14 September 2026

This release combines the completed attended-monitoring reliability and reviewed-observation case workflows with the separately deployed cloud staging source. It improves the existing laptop pilot; it does not establish production detection accuracy or accepted installation at any pharmacy.

## Changes for staff

- A runtime heartbeat identifies unavailable, stale or restarted local services. Monitoring stops and sound is disarmed when an interruption is observed. Returning service health requires an explicit new monitoring run and sound commissioning.
- API and model recovery affect only processes owned by the launcher. Old sessions and old live-job contexts cannot continue publishing as the new run. Historical casework remains available where the API and session permit it.
- A staff-reviewed product observation can create one unassessed Casebook record using a staff-written title and notes. Repeated requests cannot create duplicate cases. Linked source metadata and image hashes remain visible; sampled JPEGs keep their existing expiry and are not copied into permanent evidence storage.
- The full browser test runner can use a separate configured loopback port, avoiding an existing pharmacy preview during testing.

## Scope retained

The pure one/four/six-camera scheduler is included as tested internal code, but it is not connected to live capture, per-camera model state, evidence or alarms. Staff still select and analyse a confirmed camera area. Simultaneous multi-camera processing is not a released feature.

The cloud infrastructure is isolated from the laptop API. Render's existing Free web service and PostgreSQL remain deployed from `codex/cloud-staging`; this local application release does not expose its accounts, footage or APIs publicly. Cloud login, management interfaces, enrolment and synchronisation remain pending.

Runtime recovery is not proof of healthy inference: a responding model catalogue may still have a wedged inference worker. Browser JavaScript cannot run cleanup during OS sleep. Runtime checks reject stale context on resume; cancellation against an unreachable API is best effort. Full-shift endurance, Windows/macOS permissions and physical speaker audibility need the actual pharmacy laptops.

## Verification and release procedure

The coordinator reproduces combined Python, browser and frontend checks in the isolated release checkout using synthetic data and unused ports. Independent agents reviewed API transaction/tenant/evidence handling and the launcher/browser contract. A normal-shutdown report race discovered during integration is addressed before publication so pending work sees a monitoring-disallowed runtime before graceful service cleanup.

```sh
.venv/bin/python -m pytest tests packaging/test_bundle_integrity.py -q
npm run test:web
npm run build
npm --prefix apps/web run format:check
AISLESIGNALS_E2E_PORT=60629 AISLESIGNALS_TEST_PYTHON=.venv/bin/python npm run test:e2e
```

Coordinator verification on the integrated Mac source: **513 Python tests plus 49 subtests** passed in the initial whole-suite run (five Windows-only cases skipped); after the shutdown fix, **85 launcher/health/API tests** passed, including four new shutdown regressions. **300 frontend tests and all 72 browser journeys passed**, with production build and formatting checks. The extracted Mac arm64 pilot archive passed the real executable smoke test, including private first-owner setup, the supervised runtime handshake, rejection of unfenced live jobs and unavailable-provider behavior. Windows/Linux CI and clean-commit distribution remain the subsequent release gate; the local pre-commit archive is not the distributed artifact.

Choose an unused unprivileged test port; the suite refuses an occupied server instead of adopting it. Optional real PostgreSQL integration tests use `CLOUD_RUN_POSTGRES_TESTS=1` with `initdb` and `pg_ctl` on PATH, and create only disposable owned clusters.

Release the exact reviewed candidate after the Windows, Mac and Linux CI checks pass. Verify unsigned Windows AMD64 and Mac arm64 pilot archives, checksums, embedded file manifests and extracted startup before sharing them. Build success is not code signing or client-device acceptance. Record actual commit, workflow, counts and archive verification in the release handover.

The existing user previews and private recordings are preserved. A replacement package must be installed through an explicit upgrade workflow with private backup and rollback; this release task does not migrate or restart the current pharmacy session.
