# AisleSignals prototype 0.1

Product owner: Jawahir Q. Built by Codex with backend, interface and laptop/readiness agents. The first prototype is a working local pharmacy incident workflow using clearly labelled synthetic observations. It is a reviewable implementation slice of the [full plan](../handover/implementation-plan.md).

## Try the workflow

1. Start the local application and open `http://127.0.0.1:8765`.
2. Sign in as `manager@harbour.demo`, password `AisleDemo!2026`. These deliberately public accounts belong only to this synthetic prototype.
3. Open the review queue. Inspect the synthetic shelf observation and its source/time; acknowledge it, then record a reason to dismiss it or open a case.
4. In the casebook, record a supported classification and outcome. Financial loss requires a manager, explicit confirmation and amounts in euro cents; unknown amounts stay unknown.
5. Add a named follow-up task. Resolve the task and outcome before closing the case. A manager can reopen with a reason; earlier activity remains visible.
6. Build a factual local report template, review it and approve it. Editing case facts invalidates the draft. Export a JSON case record with a purpose; nothing is sent to anyone.
7. Run the missing-media, historical-event and camera-outage scenarios. A missing image is explicit, an old observation is historical, and a frozen camera is unavailable.
8. Request assistance independently of any suspicion. Acknowledgement records receipt, not the arrival of a colleague.
9. Sign out and sign in as `reviewer@harbour.demo` to exercise role restrictions, or `manager@liffey.demo` to see a separate organisation.

No external account, paid API, new camera or hardware is required for this prototype. The branch price remains €60/month; the UI is a price/budget record and does not charge a customer.

## Run from source

Use Python 3.12 and Node.js 24 LTS for the development toolchain. These are developer prerequisites; desktop bundles contain the runtime and built interface.

```sh
git clone https://github.com/g784dwcd2r-crypto/AisleSignals.git
cd AisleSignals
python3 scripts/setup_prototype.py
.venv/bin/python scripts/run_prototype.py
```

On Windows use `py -3.12 scripts\setup_prototype.py`, then `.venv\Scripts\python.exe scripts\run_prototype.py`. After the first setup, `Start-Mac.command` or `Start-Windows.cmd` launches the source installation. Keep the terminal open while using the app; Ctrl+C stops it. A second running instance must use another port, for example `--port 8766`.

The server binds only to `127.0.0.1`. Source-mode data persists in ignored `.local/aislesignals.db`; startup does not reset it. To try a fresh demonstration without deleting existing work, pass `--data-dir .local/another-demo`. No customer data should be entered in the synthetic prototype.

## Desktop packaging

The GitHub workflow checks Python on Linux, Windows and Mac, runs browser journeys, then builds separate Windows and Mac development bundles. A bundle is useful only after its job and packaged-executable smoke test pass. The artifact name records the actual operating system and architecture. Mac bundles are wrapped in a tar.gz archive to preserve executable permissions and symlinks; Windows bundles use ZIP. These are unsigned development bundles, not signed/notarised pharmacy installers; actual site-device acceptance remains necessary.

For a local build after the web application is compiled:

```sh
.venv/bin/python -m pip install -r packaging/requirements.txt
.venv/bin/python -m PyInstaller --clean --noconfirm packaging/prototype.spec
.venv/bin/python scripts/smoke_bundle.py dist/AisleSignalsPrototype/AisleSignalsPrototype
```

On Windows use the virtual environment's `Scripts\python.exe` and the `.exe` bundle path. The frozen application writes its synthetic database to the current user's application-data directory, separately from the installation. `--data-dir` can override that location. Do not disable OS security checks to distribute an unsigned bundle to customers; signing and notarisation are release work.

## Validate

```sh
.venv/bin/python -m pytest tests/api tests/companion -q
npm ci
npm ci --prefix apps/web
npm run build
npm run test:web
npx playwright install chromium
AISLESIGNALS_TEST_PYTHON=.venv/bin/python npm run test:e2e
```

For Windows PowerShell, set `$env:AISLESIGNALS_TEST_PYTHON='.venv/Scripts/python.exe'` before `npm run test:e2e`. Browser tests start their own server and temporary database on port 8799 and never reset the normal prototype database. See [verification](verification.md) for the checks actually executed in this delivery.

## Deliberate prototype decisions

| Decision | Reason and boundary |
|---|---|
| Local FastAPI + SQLite + React | Runs on an existing laptop without hosted services. SQL transactions, scoped queries and version checks make a useful first workflow; this does not implement the production PostgreSQL RLS design. |
| Public synthetic accounts | Makes the manager/reviewer/two-organisation demo immediately testable. Production requires managed identity, individual accounts, MFA, account lifecycle and hardened sessions. |
| Synthetic camera observations | Allows testing the complete review flow before obtaining actual camera models, credentials and detector rights. The simulator is never described as live detection. |
| Local factual report template | Costs zero in model calls and remains deterministic. It is not AI inference. A future provider adapter needs factuality, budget and processing acceptance. |
| Explicit readiness utility | Reads laptop capabilities and, only when explicitly requested, a supported private-camera protocol response. It is not continuous surveillance, frame decoding or theft detection. |
| No external alarm route | Prevents a prototype from accidentally actuating a site system. An existing vendor software API still needs separate commissioning before this optional feature can be built. |

## What comes after this prototype

Qualify the two existing laptops and camera interfaces, then implement one authorised detector adapter against measured site evidence. In parallel, move the accepted workflow to managed authentication, PostgreSQL tenant isolation, bounded encrypted media storage, durable background work and the signed laptop companion. Complete retention, rights, recovery, privacy and operational acceptance before real pharmacy data or live monitoring. The original 67 requirements and 90 planned acceptance checks remain the complete-release scope; this prototype does not mark them all finished.

Exact routes and payloads are in [the prototype contract](contract.md). They are a separate implementation slice, not the full planned `/v1` production API. See [threat model](threat-model.md), [laptop readiness](companion.md) and [scenario coverage](scenarios.md).

Build references: [Playwright web-server testing](https://playwright.dev/docs/test-webserver), [PyInstaller bundle data](https://pyinstaller.org/en/stable/spec-files.html).
