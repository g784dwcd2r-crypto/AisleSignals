#!/usr/bin/env python3
"""Private, read-only local readiness checks; no camera or audio access."""
from __future__ import annotations

import argparse
import ctypes
from dataclasses import dataclass
from datetime import datetime, timezone
import importlib.util
import json
import os
from pathlib import Path
import platform
import shutil
import socket
import stat
import sys
import time
import urllib.error
import urllib.request

ROOT = Path(__file__).resolve().parents[1]


def source_root() -> Path:
    return Path(getattr(sys, "_MEIPASS", ROOT))


def default_data_dir(root: Path = ROOT) -> Path:
    if not getattr(sys, "frozen", False):
        return root / ".local/pilot"
    if os.name == "nt":
        return Path(os.environ.get("LOCALAPPDATA", str(Path.home() / "AppData/Local"))) / "AisleSignalsPilot"
    if sys.platform == "darwin":
        return Path.home() / "Library/Application Support/AisleSignalsPilot"
    return Path(os.environ.get("XDG_DATA_HOME", str(Path.home() / ".local/share"))) / "AisleSignalsPilot"


class ConfigurationError(ValueError):
    """Messages are safe for operator display; never include private values."""


@dataclass(frozen=True)
class PilotConfig:
    root: Path
    api_port: int = 8765
    vision_port: int = 11435
    data_dir: Path | None = None
    python_executable: Path | None = None
    vision_server: Path | None = None
    vision_runtime_dir: Path | None = None
    vision_enabled: bool = True
    model_start_timeout_seconds: int = 120
    restart_limit: int = 2

    @property
    def runtime_dir(self) -> Path:
        return self.vision_runtime_dir or self.root / ".local" / "vision-runtime"

    @property
    def database(self) -> Path:
        return self.data_dir / "aislesignals.db"


def command_path(value: Path) -> Path:
    """CLI paths are relative to the working directory, on every entry point."""
    text = str(value)
    if not text.strip() or any(ord(c) < 32 for c in text):
        raise ConfigurationError("Provide a local filesystem path without control characters.")
    return Path(os.path.abspath(value.expanduser()))


def add_storage_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--config", type=Path, help="Configuration JSON; otherwise use config.json in --data-dir or the default data directory")
    parser.add_argument("--data-dir", type=Path, help="Private data directory; also selects config.json and defaults unspecified model storage to its vision-runtime subdirectory")
    parser.add_argument("--runtime-dir", type=Path, help="Override vision_runtime_dir for this command; use the same flag when setting up and starting the model")


def resolve_config(path: Path | None = None, *, root: Path = ROOT,
                   data_dir: Path | None = None, runtime_dir: Path | None = None) -> PilotConfig:
    """Select one config file, then validate and apply explicit storage overrides."""
    if data_dir is not None:
        data_dir = command_path(data_dir)
    if path is None:
        candidate = (data_dir if data_dir is not None else default_data_dir(root)) / "config.json"
        # An invalid existing selection must not silently become a fresh setup.
        path = candidate if candidate.exists() or candidate.is_symlink() else None
    else:
        path = command_path(path)
    return load_config(path, root=root, data_dir=data_dir, runtime_dir=runtime_dir)


def load_config(path: Path | None = None, *, root: Path = ROOT,
                data_dir: Path | None = None, runtime_dir: Path | None = None) -> PilotConfig:
    values = {}
    if path is not None:
        try:
            if path.stat().st_size > 16_384:
                raise ConfigurationError("Configuration exceeds the 16 KiB limit.")
            values = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            raise ConfigurationError("Cannot read configuration JSON; check the selected config file.") from None
        if not isinstance(values, dict):
            raise ConfigurationError("Configuration must be a JSON object.")
    allowed = {"schema_version", "api_port", "vision_port", "data_dir", "python_executable",
               "vision_server", "vision_runtime_dir", "vision_enabled", "model_start_timeout_seconds", "restart_limit"}
    if set(values) - allowed:
        raise ConfigurationError("Configuration has unknown fields; use the supplied example.")
    if type(values.get("schema_version", 1)) is not int or values.get("schema_version", 1) != 1:
        raise ConfigurationError("Unsupported configuration schema_version.")
    for key, default, minimum, maximum in [("api_port", 8765, 1024, 65535),
            ("vision_port", 11435, 1024, 65535), ("model_start_timeout_seconds", 120, 5, 300),
            ("restart_limit", 2, 0, 3)]:
        value = values.get(key, default)
        if type(value) is not int or not minimum <= value <= maximum:
            raise ConfigurationError(f"Invalid {key}; use the documented bounds.")
        values[key] = value
    if values["api_port"] == values["vision_port"]:
        raise ConfigurationError("API and vision ports must be different.")
    if type(values.get("vision_enabled", True)) is not bool:
        raise ConfigurationError("vision_enabled must be true or false.")
    paths = {}
    frozen = getattr(sys, "frozen", False)
    runtime_default = default_data_dir(root) / "vision-runtime" if frozen else root / ".local/vision-runtime"
    defaults = {"data_dir": str(default_data_dir(root)), "python_executable": sys.executable if frozen else ".venv/Scripts/python.exe" if os.name == "nt" else ".venv/bin/python",
                "vision_runtime_dir": str(runtime_default),
                "vision_server": str(runtime_default / "ollama/llama-server") if sys.platform == "darwin" and platform.machine() == "arm64" else None}
    for key, default in defaults.items():
        value = values.get(key, default)
        if value is None and key == "vision_server":
            paths[key] = None
            continue
        if not isinstance(value, str) or not value.strip() or any(ord(c) < 32 for c in value):
            raise ConfigurationError(f"Invalid {key}; provide a local filesystem path.")
        p = Path(value).expanduser()
        paths[key] = Path(os.path.abspath(p if p.is_absolute() else root / p))
    # Apply only after validating the complete selected configuration. Explicit
    # server/runtime fields are known from this same read, never a second read.
    if data_dir is not None:
        paths["data_dir"] = command_path(data_dir)
        if "vision_runtime_dir" not in values:
            paths["vision_runtime_dir"] = paths["data_dir"] / "vision-runtime"
    if runtime_dir is not None:
        paths["vision_runtime_dir"] = command_path(runtime_dir)
    if "vision_server" not in values:
        pins = model_module(root)
        if hasattr(pins, "default_server"):
            paths["vision_server"] = pins.default_server(paths["vision_runtime_dir"])
    return PilotConfig(root=root, **paths, **{key: values[key] for key in ("api_port", "vision_port", "model_start_timeout_seconds", "restart_limit")}, vision_enabled=values.get("vision_enabled", True))


def model_module(root: Path = ROOT):
    spec = importlib.util.spec_from_file_location("aislesignals_pinned_local_vision", root / "scripts" / "local-vision.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def private_path_safe(path: Path) -> bool:
    """Reject links/reparse points rather than relocate sensitive data.

    Windows directory junctions are reparse points, not Python symbolic links.
    Unrecognised reparse types also fail closed; an ordinary private directory
    is required for model credentials, application data and reports. POSIX
    ancestors must resist replacement by other users, including while a child
    service opens a validated token by path. Root/current-owned sticky temporary
    directories are safe ancestors; this does not validate Windows ACLs.
    """
    for part in (path, *path.parents):
        try:
            metadata = part.lstat()
        except FileNotFoundError:
            continue
        except OSError:
            return False
        if stat.S_ISLNK(metadata.st_mode) or (
            getattr(metadata, "st_file_attributes", 0)
            & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
        ):
            return False
        if os.name == "posix" and stat.S_ISDIR(metadata.st_mode):
            if metadata.st_uid not in {0, os.geteuid()}:
                return False
            if metadata.st_mode & 0o022 and not metadata.st_mode & stat.S_ISVTX:
                return False
    return True


def port_available(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        try:
            # Permit a terminated owned server's TIME_WAIT sockets, never an
            # active listener. Windows uses exclusive binding instead.
            if os.name == "nt":
                probe.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            else:
                probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            probe.bind(("127.0.0.1", port))
            return True
        except OSError:
            return False


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise urllib.error.HTTPError(req.full_url, code, "Redirect refused", headers, fp)


def local_json(port: int, path: str, token: str = "", *, timeout: float = 0.5):
    """Loopback-only, proxy-free, no redirects; limited response and timeout."""
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    headers = {"Accept": "application/json", "Accept-Encoding": "identity"}
    if token:
        headers["Authorization"] = "Bearer " + token
    request = urllib.request.Request(f"http://127.0.0.1:{port}{path}", headers=headers)
    try:
        with opener.open(request, timeout=timeout) as response:
            if response.status != 200 or response.headers.get("Content-Encoding", "identity") != "identity":
                return None
            raw = bytearray()
            deadline = time.monotonic() + timeout
            while True:
                if time.monotonic() > deadline:
                    return None
                chunk = response.read1(min(4096, 32_769 - len(raw)))
                if not chunk:
                    break
                raw.extend(chunk)
                if len(raw) > 32_768:
                    return None
            return json.loads(raw)
    except (OSError, ValueError, urllib.error.URLError):
        return None


def memory_bytes() -> int | None:
    try:
        if os.name == "nt":
            class MemoryStatus(ctypes.Structure):
                _fields_ = [("length", ctypes.c_ulong), ("load", ctypes.c_ulong)] + [(name, ctypes.c_ulonglong) for name in ("total", "available", "total_page", "available_page", "total_virtual", "available_virtual", "extended")]
            state = MemoryStatus()
            state.length = ctypes.sizeof(state)
            return int(state.total) if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(state)) else None
        pages, size = os.sysconf("SC_PHYS_PAGES"), os.sysconf("SC_PAGE_SIZE")
        return int(pages * size) if pages > 0 and size > 0 else None
    except (AttributeError, ValueError, OSError):
        return None


def result(check_id: str, status: str, detail: str, **values) -> dict:
    return {"id": check_id, "status": status, "detail": detail, **values}


def preflight(config: PilotConfig) -> dict:
    checks = []
    add = checks.append
    add(result("platform_inventory", "PASS", "Inventory only; physical platform acceptance is recorded separately.", os=platform.system(), os_release=platform.release(), architecture=platform.machine()))
    add(result("python_runtime", "PASS" if config.python_executable.is_file() else "FAIL", "Configured Python must exist and have the pinned API dependencies installed."))
    add(result("web_build", "PASS" if (config.root / "apps/web/dist/index.html").is_file() else "FAIL", "Build the web application before launch."))
    safe = private_path_safe(config.data_dir) and private_path_safe(config.database)
    if config.data_dir.exists() and not config.data_dir.is_dir():
        safe = False
    add(result("data_path", "PASS" if safe else "FAIL", "Private paths must reject links/reparse points and unsafe POSIX directory ownership or writable ancestors; existing database is preserved."))
    existing = next((p for p in (config.data_dir, *config.data_dir.parents) if p.exists()), config.root)
    try:
        free = shutil.disk_usage(existing).free
        add(result("free_disk", "PASS" if free >= 2 * 1024**3 else "FAIL", "At least 2 GiB free is required to start; this is not a footage-retention capacity guarantee.", free_bytes=free, minimum_bytes=2 * 1024**3))
    except OSError:
        add(result("free_disk", "FAIL", "Unable to measure free space."))
    memory = memory_bytes()
    add(result("memory_inventory", "PASS" if memory else "NOT_RUN", "Inventory only; model speed and safe camera count require measurement on this laptop.", total_bytes=memory))
    add(result("api_port_free", "PASS" if port_available(config.api_port) else "FAIL", "The launcher refuses to attach to or stop an existing service.", port=config.api_port))
    health = local_json(config.api_port, "/api/health")
    add(result("api_availability", "PASS" if isinstance(health, dict) and health.get("status") == "ok" else "NOT_RUN", "Reachability only; an existing server is not adopted and this does not prove camera monitoring."))
    if config.vision_enabled:
        add(result("vision_port_free", "PASS" if port_available(config.vision_port) else "FAIL", "The launcher owns only processes it starts.", port=config.vision_port))
        add(result("vision_runtime", "PASS" if config.vision_server and config.vision_server.is_file() and (os.name == "nt" or os.access(config.vision_server, os.X_OK)) else "FAIL", "Run the pinned local-vision setup for supported Mac ARM64 or Windows AMD64/ARM64; other runtimes require an explicitly configured compatible executable."))
        pins = model_module(config.root)
        for index, (name, expected) in enumerate(pins.WEIGHTS.items(), 1):
            path = config.runtime_dir / "models" / name
            present = path.is_file() and private_path_safe(path)
            try:
                actual = pins.digest(path) if present else None
            except OSError:
                actual = None
            add(result(f"model_weight_{index}", "PASS" if actual == expected else "FAIL", "Pinned local model checksum; model accuracy is not validated by this check.", present=present, expected_sha256=expected, actual_sha256=actual))
        token = config.runtime_dir / "api-token"
        token_safe = private_path_safe(token)
        if token.exists():
            try:
                content = token.read_text().strip() if token.stat().st_size <= 1024 else ""
                token_safe = token_safe and 32 <= len(content) <= 256 and all(33 <= ord(c) <= 126 for c in content)
            except (OSError, UnicodeError):
                token_safe = False
        add(result("vision_token_file", "PASS" if token_safe else "FAIL", "An existing token is validated privately; a missing token is created at launch."))
    else:
        add(result("vision_disabled", "NOT_RUN", "Product interaction inference is disabled; casework can run."))
    for check_id, detail in [
        ("filesystem_acl", "POSIX launch applies owner-only modes. Windows NTFS access control requires local verification."),
        ("actual_cctv", "A branch operator must select and test its authorised CCTV source."),
        ("physical_audio", "A branch operator must hear and acknowledge a speaker test."),
        ("resume_rearm", "Test sleep/wake on this laptop and explicitly reselect the source after restart."),
        ("platform_acceptance", "Execute and record the complete workflow on this physical laptop."),
        ("detection_accuracy", "Held-out branch footage and staff review are required; software checks do not validate theft recognition."),
    ]:
        add(result(check_id, "NOT_RUN", detail))
    core_failed = any(c["status"] == "FAIL" and not c["id"].startswith(("model_weight_", "vision_runtime")) for c in checks)
    model_failed = any(c["status"] == "FAIL" for c in checks)
    return {"schema_version": 1, "generated_at": datetime.now(timezone.utc).isoformat(),
            "scope": "local_laptop_readiness", "launch_status": "FAIL" if core_failed else "DEGRADED" if model_failed or not config.vision_enabled else "PASS",
            "client_rollout_accepted": False, "camera_monitoring": "NOT_RUN", "checks": checks}


def write_report(path: Path, report: dict) -> None:
    if path.suffix.lower() != ".json":
        raise ConfigurationError("Readiness reports require a .json output filename; database and token files cannot be report targets.")
    if not private_path_safe(path) or not private_path_safe(path.parent):
        raise ConfigurationError("Report output must not traverse symbolic links.")
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    import tempfile
    fd, pending = tempfile.mkstemp(prefix=".readiness-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(report, stream, indent=2)
            stream.write("\n")
        os.replace(pending, path)
    finally:
        if os.path.exists(pending):
            os.unlink(pending)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    add_storage_arguments(parser)
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    try:
        root = source_root()
        config = resolve_config(args.config, root=root, data_dir=args.data_dir, runtime_dir=args.runtime_dir)
        report = preflight(config)
        if args.report:
            write_report(args.report, report)
        print(json.dumps(report, indent=2))
        return 1 if report["launch_status"] == "FAIL" else 0
    except (ConfigurationError, OSError):
        print("Readiness could not complete. Check configuration, local file access and the installation.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
