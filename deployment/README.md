# Supervised local pharmacy pilot

Cloud release operators should use the read-only
[cloud deployment audit](../docs/release/cloud-deployment-audit.md) to verify the
public health/authentication contract and exact built management-console files.
That check does not inspect Render billing, backups, secrets or the backend
commit, and it performs no deployment.

This launcher coordinates the API and optional local model on an existing laptop. It does not activate a camera, register an unattended service, enrol a branch, create an account, download models, or qualify theft detection. Staff must remain present with the browser open and the laptop awake. Each of the six branches needs its own physical acceptance record; successful checks here do not activate the other branches.

## Prepare a source installation

Install the pinned API requirements into the repository's `.venv`, and build `apps/web` using the repository instructions. Provision individual accounts using the pilot account setup CLI; never copy a demo database to a client. Keep real data under the ignored `.local` directory or a private operator-selected data directory.

Run the existing `scripts/local-vision.py setup` once to download and verify the pinned model files. It also prepares a pinned native runtime for Mac ARM64 and Windows AMD64/ARM64; `setup --runtime-only` prepares only the native runtime when weights already exist. Other platforms require an explicitly installed compatible `llama-server` executable. Actual Windows inference and performance remain unverified until tested on a Windows laptop. This launcher performs no downloads and installs no hardware. A source installation needs Python and the built application; a separately produced pilot bundle carries its own Python/web runtime.

The macOS CI job also wraps the verified Apple-silicon pilot bundle as `AisleSignals Pilot.app` inside an unsigned `.dmg`. Drag the application into Applications and open it only when authorised by the pharmacy's installation policy. A normal Finder launch opens a dedicated AisleSignals desktop window on **Cloud workspace**, where an existing Render account can sign in with its email, password and MFA. **Live detection** switches to the local pharmacy interface. The app runs that local service behind the window in casework-only mode, so the core pilot works before the optional model is installed. Closing the last AisleSignals window stops the service it started. Command-line arguments continue to select explicit maintenance and advanced launch modes. The pilot disk image is not Apple-signed or notarised, does not include the optional 3.3 GB interaction model, and does not replace the separate cloud connection companion. Production distribution still requires Developer ID signing, notarisation, update lifecycle checks and acceptance on the exact pharmacy Mac.

Copy `deployment/pilot-config.example.json` to `.local/pilot/config.json`. Relative paths are relative to the repository root, not the config file. Do not put credentials or camera URLs into this JSON. `vision_runtime_dir` selects the prepared folder containing `models/`. The platform's pinned server path is selected automatically within it. To use a separately prepared compatible executable, explicitly set `vision_server`, for example `"vision_server": "C:/approved/local/path/llama-server.exe"`. Python defaults to `.venv/bin/python` on Mac and `.venv/Scripts/python.exe` on Windows. Optional `python_executable` overrides that local path.

The config supports only the fields in the example plus `vision_server` and `python_executable`. Ports must be distinct and between 1024 and 65535. Model startup timeout is 5–300 seconds; restart limit is 0–3 total restart attempts per service per launch. Default recovery backoffs are 2 and 4 seconds.

## Check, then start

Use matching `--config`, `--data-dir` and `--runtime-dir` selections for model setup, readiness and startup. Explicit `--data-dir` selects that directory's config and, when no runtime override is configured, its `vision-runtime` directory. See [model setup and storage](../docs/release/model-setup-consistency.md) for precedence, existing-command compatibility and interrupted setup recovery.

Mac/source:

```sh
.venv/bin/python scripts/run_pilot.py --check --report .local/pilot/readiness.json
.venv/bin/python scripts/run_pilot.py
```

Windows/source:

```powershell
.venv\Scripts\python.exe scripts\run_pilot.py --check --report .local\pilot\readiness.json
.venv\Scripts\python.exe scripts\run_pilot.py
```

The supplied `.command` and `.cmd` files call the same foreground launcher from a source installation. `--config PATH` selects a different configuration; `--port`, `--data-dir` and `--no-browser` support isolated acceptance runs. `--casework-only` intentionally disables interaction-model access while retaining casework. This does not disable any separate browser pose experiment; the operator must keep detection stopped when testing casework only.

Bundled builds expose the same flags on their executable. Their default private data directory is `AisleSignalsPilot` under Windows Local App Data or Mac Application Support, with external model files under `vision-runtime/`; source config examples using relative paths must be adjusted to absolute private paths before using them with a bundle. Models are not embedded in the application bundle. Do not use its temporary extraction folder for data.

An empty protected workspace shows **Create the first owner account**. Start the launcher in an interactive local terminal; it displays a private setup code that expires after 15 minutes. Enter that code, the pharmacy group and first branch, your name/email and a unique passphrase in the browser. Then sign in and open **Administration** to create further branches and users. The setup code is never printed into redirected launcher logs. Restart the launcher or use the local `accounts --db PATH setup-token` command for a fresh code. Existing or restored accounts cannot be replaced through first-owner setup.

The pilot executable also includes terminal account recovery and optional model downloads, so a bundled installation does not need a separate Python installation for these steps:

```text
AisleSignalsPilot accounts --help
AisleSignalsPilot accounts --db /private/path/aislesignals.db init --organisation "Your pharmacy group" --branch "Actual branch name" --email "manager@example.ie" --name "Named manager"
AisleSignalsPilot model-setup --runtime-dir /private/path/vision-runtime
```

Use `AisleSignalsPilot.exe` on Windows and the actual private paths from that laptop's configuration. The account command requires an interactive terminal and prompts privately for a unique passphrase; the example is an alternative to browser setup and does not create an account until you execute it. `model-setup` explicitly downloads about 3.3 GB of model files plus the selected native runtime; it does not start camera monitoring. A non-default launcher configuration must use the same `vision_runtime_dir` selected here. Follow [browser administration](../docs/release/pilot-administration.md) for everyday user management and [account recovery](../docs/release/pilot-identity.md) for local operator commands.

At startup, the launcher refuses occupied requested ports without attaching to, restarting or stopping their services. Stop a previous session yourself or choose distinct unused ports. Model absence or a checksum mismatch permits explicitly degraded casework; occupied ports, invalid tokens and unsafe data paths block launch. No synthetic fallback or default pilot password is created. An incompatible or unavailable API blocks startup.

The existing database is preserved. POSIX private directories use mode `0700` and token/database files `0600`; the launcher refuses symbolic links for these locations. Windows NTFS ACL and disk encryption checks remain required on the actual laptop, and the readiness report marks them untested. Store customer records only after the branch's access and retention checks have been accepted.

## Daily operation and recovery

Open the Mac application (or the loopback URL printed by a command-line launch), sign in with a named account and explicitly select the authorised CCTV window/camera or test recording. Test view visibility, source freshness and the existing speakers with an on-site staff member. Keep the desktop application open. API/model health alone never proves that a camera is monitored or that an alarm can be heard.

On Windows 10/11, owned child processes are assigned atomically to a private kill-on-close Job Object; abrupt launcher exit terminates that contained tree. A containment failure refuses startup. On Mac, an ordinary terminal close sends SIGHUP and requests graceful shutdown; an uncatchable force-kill is outside that guarantee. Ctrl+C gracefully stops only the launcher's children, escalating to terminate/kill if necessary, and does not delete the database or model weights. If an owned process exits, recovery is bounded by `restart_limit`; a permanently failed model leaves casework degraded, while a permanently failed API shuts down the launch. If another process takes a released port, it is not adopted or killed.

A detected sleep, clock discontinuity or scheduling gap over 15 seconds ends the session and records `REARM_REQUIRED`. This is deliberately conservative and may require restart after a heavily stalled laptop. Reopen the launcher and explicitly reselect the CCTV source. The launcher cannot prevent sleep, keep browser capture permissions alive, or claim active monitoring on wake. Browser capture and sound protections still apply.

`runtime-status.json` records state transitions in the private data directory. `--check` is read-only unless `--report` explicitly requests an output file. Its JSON contains `schema_version: 1`, a `launch_status` of `PASS`, `DEGRADED` or `FAIL`, and individual `PASS`, `FAIL` or `NOT_RUN` checks. It omits hostname, username, private paths, camera details, tokens and customer records. API reachability may show `PASS` while the occupied-port launch check fails; this is intentional. `client_rollout_accepted` is always false because a local diagnostic cannot approve a client rollout.

Actual CCTV, physical audio, platform workflow acceptance, sleep/wake behaviour and detection accuracy remain `NOT_RUN` until recorded by the branch testing workflow. The report may be shared with the testing coordinator after local review. It is not an acceptance certificate and cannot substitute for a person hearing the speaker or supplying authorised camera access.
