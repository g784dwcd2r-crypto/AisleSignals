# Platform support and runtime verification

The release targets existing pharmacy laptops. Actual branch hardware inventory is pending; an operating-system name alone does not establish adequate memory, processor performance, camera access or audible speakers.

| Platform | Optional model setup | Evidence obtained in this workspace |
|---|---|---|
| Mac Apple Silicon | Pinned native runtime and model downloads | Real local inference and native runtime startup exercised; client camera acceptance pending |
| Windows x64 | Pinned native runtime and model downloads | Official archive checksum, server path and native dependency layout verified; native startup is a required Windows CI job; client inference pending |
| Windows ARM64 | Pinned native runtime and model downloads | Official archive digest and upstream server layout recorded; native execution and packaged application acceptance pending |
| Intel Mac / other platforms | Explicit compatible server path required | No supported packaged runtime or client acceptance claimed |

Runtime selection follows the actual platform and processor architecture. There is no automatic emulation claim. Desktop artifacts carry their build architecture; a Windows x64 artifact is not evidence of a native Windows ARM64 build.

`scripts/local-vision.py setup` downloads the pinned Qwen model and the native runtime. `setup --runtime-only` skips weights. `scripts/smoke_vision_runtime.py` starts the native server with `--help` and checks the arguments required by the application; it does not perform inference. The pilot executable exposes the download tool through `model-setup --help` and named-account administration through `accounts --help`.

The [official Ollama v0.34.0 release](https://github.com/ollama/ollama/releases/tag/v0.34.0) supplies the runtime archives. Its [Windows build script](https://github.com/ollama/ollama/blob/v0.34.0/scripts/build_windows.ps1) packages `lib/ollama/llama-server.exe` and native libraries. AisleSignals starts that bundled server directly. It does not run the Ollama desktop application or its background service. Downloaded archive SHA-256 values and model revision are pinned in the setup script; third-party licences remain applicable.

Windows x64 archive: `a7dd1b174f39d3d1b8a25d4cbc86045d0e190b17187bfdcbe2f2ee3b5a11470e`.

Windows ARM64 archive: `a5ce957f750dbe85b34c9da81440969f4b9c7f747a3bb629ce50ac6cd2a63536`.

Mac ARM64 archive: `dd12b00bcce2d6551178e67ada90d5af9f75bdb54a118b96655250fa3e8ef734`.

The protected release writes SQLite schema version 2. Existing synthetic version 1 data upgrades additively and stays synthetic. Older binaries reject version 2; do not lower the schema marker to bypass that protection. An intentional rollback uses a preserved pre-upgrade backup in a separate directory. Pilot and demo databases cannot be converted into each other by changing an environment variable.
