# Browser runtime health and recovery

Implemented in the isolated production-readiness worktree from `789e5d6`. This connects the launcher health contract to attended browser monitoring; it does not establish model accuracy, continuous camera coverage, physical sound audibility or pharmacy acceptance.

## Operator behaviour

After named sign-in, monitoring waits for an authenticated local-service heartbeat. A failed or expired heartbeat, offline event, changed process/recovery identity, hidden page or detected clock/scheduling interruption immediately invokes the existing local stop paths when observed: pose loading/workers stop, live capture tracks are released, video playback pauses, product samples/submitted previews are cleared, pending jobs are cancelled locally (server cancellation is requested when reachable) and both sound paths are stopped/disarmed. An unreachable API cannot confirm cancellation; any later completion remains historical and cannot sound through the ended browser run. Product enable and automatic switches are reset. In-memory runtime status changes before React renders or a late result continuation can run.

A fresh successful heartbeat restores only service readiness. It does not reconnect a source, start a detector, enable automatic sampling, or arm sound. Staff reconnect/check the source, press Start detection, explicitly enable product analysis, and repeat the existing sound test/heard-confirmation/arm process. Recorded-video sources still require their explicit recorded-test sound choice. Existing source continuity, selected-camera, branch, job and alarm freshness checks remain independent.

Transient runtime failure retains the signed-in case/history view and unsaved case input when the API/session still permits it. A banner identifies the stopped monitoring and potentially stale records. API restart by the launcher still revokes sessions and requires sign-in; revoked/changed branch access clears the view as before. Successful API-only case requests never clear the monitoring interruption by themselves.

## Bounded checks and safe contract

`GET /api/runtime/health` requires a valid named/demo session and returns only:

- `api_id`: random 32-hex identity for this API process.
- `runtime_id`, `recovery_generation`: supervised launcher identity/generation, or null/zero for direct API mode.
- `context`: opaque 64-hex digest of those identities, present only while monitoring is permitted. It is a freshness fence, not an authentication credential.
- `site_id`: the authenticated active branch, checked against the browser's session context.
- `supervised`, `state`, `monitoring_allowed`, `product_available`, `report_age_ms`.

No report paths, account details, free-text launcher diagnostics, model tokens, camera frames or command lines are returned. The context stays in memory and request headers; it is not put into URLs or browser storage. The heartbeat does not extend the existing unattended session idle lifetime.

Only a supervised pilot child reads the adjacent private `runtime-status.json` beside its configured database. Reads are bounded to 32 KiB, require a regular non-symlink file, and avoid blocking special-file reads. Missing/malformed reports fail closed. The report and enabled service checks must be no older than eight seconds (at most two seconds future clock tolerance). SERVICES_READY/REARM_REQUIRED with healthy API and enabled model probes permit monitoring. A model disabled by the API process’s immutable provider interlock permits API/pose casework with a fresh healthy API probe, including the launcher’s DEGRADED casework state or an omitted vision entry. It reports product availability false; the existing model-status interlock prevents product analysis. The launcher's rearm latch is not treated as permanent operator approval or a permanent prohibition.

Direct development/test API mode reports API_ONLY and changes identity at every process start. It explicitly does not certify launcher/model process health. Supervised shipping uses the launcher contract.

Browser probes have a one-second total deadline including response-body decoding, followed by a two-second interval. There is one active fetch with an abort signal and no inference queue. A separate 250 ms watchdog enforces a four-second last-success bound and detects clock/scheduling interruptions. An ordinary API network/truncated response or server failure also invalidates monitoring. Offline notifications are synchronous; an unannounced outage is detected within the next probe/deadline (normally at most three seconds), subject to browser/OS scheduling. Suspended JavaScript cannot execute cleanup while the laptop sleeps; on resume the clock/source guards reject old context before using a result. This is not an instantaneous remote outage guarantee.

## Independent API/job fence

Supervised creation of product jobs, live job-result reads and live-event submissions require `X-AisleSignals-Runtime` matching the current healthy process/report context. Middleware checks before and after the request so a changed/degraded report cannot produce a live result response. Authentication, CSRF, branch authority and idempotency remain required separately. Cancellation and historical case/evidence/review routes do not depend on healthy inference services.

Each new browser product job persists its creation runtime context. The worker checks it before inference and before publication; an ended context cancels the result and removes its sampled JPEGs. A new runtime header cannot revive an old live-job read or retry. A previously completed observation remains accessible through authenticated historical review, without rearming or extending its original evidence expiry. Existing direct unsupervised API callers without a runtime header retain their development compatibility; they have no supervised-health claim. Supervised jobs cannot use that exception.

## Verification and remaining acceptance

Synthetic API tests cover private-field minimisation, authentication/idle lifetime, missing/oversized/malformed/symlink/stale/future/suspect reports, stale service probes, disabled-model casework, generation changes during requests, old-job reads/retries, cancellation and worker publication fences. Unit tests cover deadlines, response races, hidden/offline events, branch changes, clock gaps and explicit recovery. Browser journeys use per-test ephemeral pilot services, synthetic canvas video and a wholly silent audio fixture. They exercise real API polling, source teardown, pending-job cancellation, generation recovery, late pose results and clock discontinuities, with case-link regression journeys alongside them.

Final focused checks: 20 runtime/API-fence unit tests passed; 300 frontend unit tests passed across 14 files. The new API runtime suite passed 17 tests; the earlier combined runtime/interactions/case regression passed 80 tests, and runtime/identity/schema/evidence recovery passed 69 before the final additional history-replay assertion. Six combined real browser journeys passed (four runtime plus two case-link regression); the final four runtime journeys also passed on the rebuilt source. Build, TypeScript, owned-file Prettier and `git diff --check` passed.

```sh
npm --prefix apps/web test -- --run
npm --prefix apps/web run build
../AisleSignals/.venv/bin/python -m pytest tests/api/test_runtime_health.py -q
AISLESIGNALS_TEST_PYTHON=../AisleSignals/.venv/bin/python npx playwright test --config .local/case-link.playwright.config.ts tests/e2e/runtime-health.spec.ts tests/e2e/interaction-case-link.spec.ts
```

The ignored config disables Playwright's default web server. These two spec files each start/stop their own temporary installation on a socket-assigned ephemeral port; do not use the default port 8799 for these background tests. No existing preview, customer recording, installed model, customer database or actual alarm was used. Full-shift Windows/macOS endurance, real-camera continuity, laptop physical audio and six branch acceptance remain NOT_RUN. A responsive model catalogue still cannot prove healthy inference progress; the existing inference deadline and launcher limitations in `runtime-health.md` remain.
