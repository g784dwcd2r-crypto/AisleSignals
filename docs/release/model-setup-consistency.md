# Model setup and launcher storage

Requirements: FR-014 and FR-067. This change aligns optional model preparation with the attended launcher on an existing laptop. It does not qualify detection, sign an installer, start monitoring or approve a pharmacy installation.

## One selection for setup, readiness and startup

These commands share the same configuration resolver and storage flags:

- `AisleSignalsPilot model-setup` and `AisleSignalsPilot` (append `.exe` on Windows).
- `python scripts/run_pilot.py model-setup` and `python scripts/run_pilot.py` in a source installation.
- `python scripts/local-vision.py setup`, `python scripts/local-vision.py run` and `python scripts/pilot_preflight.py`.

Use the same `--config`, `--data-dir` and `--runtime-dir` selections at setup and startup. Flags apply to the current command; setup never saves configuration, enrols a workspace, changes accounts or creates/replaces an API token. It prints the resolved model-storage directory in the local terminal before preparation. Readiness JSON retains its existing privacy-minimised format.

| Selection | Precedence and behaviour |
| --- | --- |
| Config file | Explicit `--config PATH`; otherwise `config.json` inside explicit `--data-dir`; otherwise `config.json` inside the platform's default data directory. |
| Missing config | An absent implicit `config.json` uses defaults. An explicit missing file, malformed file, directory in place of a file or unsupported config fails. A selected data directory never falls back to another workspace's config. |
| Application data | `--data-dir` overrides the selected config's `data_dir`; otherwise the existing config/default applies. |
| Model storage | `--runtime-dir` overrides config `vision_runtime_dir`. If neither supplies it and `--data-dir` is explicit, use `<data-dir>/vision-runtime`. Without these explicit storage selections, retain the original source or bundled model default. |
| Native server | Explicit config `vision_server` stays unchanged, including with a runtime-directory override. Otherwise select the pinned platform server inside the resolved model directory. |
| Relative paths | CLI paths are relative to the command's working directory. Paths stored inside config JSON remain relative to the repository/application root, as before. `~` expands using the current platform's user directory. Prefer absolute private paths in bundled config. |

For compatibility, an existing config that sets only `data_dir` keeps its previous model-storage default when started without storage flags. To bind models to a particular existing directory permanently, set `vision_runtime_dir` explicitly. The source default remains `<repository>/.local/vision-runtime`; the bundled default remains `vision-runtime` inside `AisleSignalsPilot` under Mac Application Support or Windows Local App Data. No model files belong inside a bundle's temporary extraction directory.

Previously, launcher `--data-dir` changed only application data after reading the default config. It now also selects that directory's config and the unspecified model default. If an existing launch command uses `--data-dir` with models stored elsewhere, add `--runtime-dir` pointing to those already prepared models or explicitly select its existing config. Run `--check` with the matching flags before startup. No files are moved or downloaded by startup/readiness.

Examples using an explicitly selected data directory:

```sh
# Mac source installation, using its existing Python environment
.venv/bin/python scripts/run_pilot.py model-setup --data-dir "/private/operator-selected/AisleSignals"
.venv/bin/python scripts/run_pilot.py --check --data-dir "/private/operator-selected/AisleSignals"
.venv/bin/python scripts/run_pilot.py --data-dir "/private/operator-selected/AisleSignals"
```

```powershell
# Windows bundle, using a directory selected by the local operator
.\AisleSignalsPilot.exe model-setup --data-dir "C:\OperatorSelected\AisleSignals"
.\AisleSignalsPilot.exe --check --data-dir "C:\OperatorSelected\AisleSignals"
.\AisleSignalsPilot.exe --data-dir "C:\OperatorSelected\AisleSignals"
```

With a separate config file, pass `--config PATH` to all three commands. With a temporary model-directory override, also pass `--runtime-dir PATH` to all three. Setup prepares the pinned files in that directory; it does not install or relocate a separately configured external executable. `local-vision.py run --server PATH` remains an explicit run-only override, and `--port` overrides its configured vision port. `--runtime-only` applies only to setup and skips model weights.

## Existing files and interrupted setup

Setup checks existing requested model/archive checksums before its first download. Matching files are reused. A mismatched file, foreign manifest or unsafe symbolic-link path is preserved and causes an error; setup does not guess that it is safe to repair or replace another model.

Downloads use a separate temporary file and verify the pinned checksum before publication. Creating the final filename is atomic and refuses an existing target, including one created by a concurrent installer. This uses hard links on local NTFS/POSIX filesystems; an unsupported filesystem fails without replacing the target. An old `.part` file is left alone.

Native runtime archives are extracted into staging, checked for their expected server and then installed into a new runtime directory. An existing installation is compared with the staged bundle; changed or missing native members cause an error and are preserved. Verification/extraction needs temporary disk space in addition to the downloaded archive. Setup does not overwrite a changed native server to make a health check pass.

After a mismatch or interrupted installation, review the retained files locally. The simplest isolated recovery is to select an empty private runtime directory, repeat setup there, then use that same directory in the launcher/config. Existing model directories are never deleted automatically. Stop an active launcher before changing its storage selection; selecting a new directory does not migrate application data or restart/rearm monitoring.

## Local credentials and directory permissions

Direct model startup validates its existing API token before starting the native process. It refuses symbolic links, Windows junctions/reparse points, hard-linked or special files, and empty, oversized or malformed tokens. POSIX tokens must belong to the current user; reads and permission changes use the validated open file descriptor. Invalid existing files are preserved. A new runtime directory requests mode `0700`, and a new token requests mode `0600`; setup does not change the permissions of existing directories.

On POSIX systems the shared path check also requires existing directories to belong to the current user or root and rejects group/world-writable non-sticky ancestors. Appropriate sticky temporary directories remain supported. On Windows this check rejects reparse points; it does not certify the directory's access-control list or provide secure Windows cloud pairing. Those platform acceptance checks remain separate.

## Verification and remaining acceptance

`tests/deployment/test_model_setup.py` uses temporary directories, fake installers and tiny synthetic model/archive bytes. It exercises common setup/start paths, saved and explicit configs, override ordering, relative paths, spaces, native path mapping, bundled Mac/Windows defaults, actual source CLI help/imports, invalid config refusal, runtime-only dispatch, download conflicts, checksum failures and preservation of changed native files. Existing launcher health, native archive-safety and process-lifecycle tests remain applicable.

No real weights, native model process, customer configuration, account store, token or camera was used for these checks. Windows native execution of these new tests is left to CI; mocked platform selection on a Mac is not Windows hardware evidence. Signed distribution, actual model setup/inference on each customer laptop, filesystem permissions, performance, sleep/resume, camera quality and speaker audibility remain **NOT_RUN** until separately observed and recorded. This closes a configuration defect, not the full FR-014/FR-067 acceptance gate.
