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
| Windows / CI Mac execution | Workflow configured; remote results are recorded separately below once available |

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

The first source push triggers the GitHub workflow. A successful configured job is required before claiming its Windows/Mac runtime or bundle passed. The final delivery record will reference the observed run, or clearly state any external limitation.
