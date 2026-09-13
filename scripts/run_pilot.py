#!/usr/bin/env python3
"""Run an attended local pilot. No install, downloads, camera access or service registration."""
from __future__ import annotations

import argparse
from dataclasses import dataclass, replace
from datetime import datetime, timezone
import os
from pathlib import Path
import secrets
import signal
import subprocess
import sys
import threading
import time
import webbrowser

try:
    from pilot_preflight import (ConfigurationError, PilotConfig, default_data_dir, load_config,
        local_json, model_module, port_available, preflight, private_path_safe, source_root, write_report)
except ModuleNotFoundError:
    from scripts.pilot_preflight import (ConfigurationError, PilotConfig, default_data_dir, load_config,
        local_json, model_module, port_available, preflight, private_path_safe, source_root, write_report)


def prepare_private_directory(path: Path) -> None:
    if not private_path_safe(path):
        raise ConfigurationError("Private data cannot traverse symbolic links.")
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    if os.name != "nt":
        path.chmod(0o700)


def ensure_token(config: PilotConfig) -> Path:
    prepare_private_directory(config.runtime_dir)
    target = config.runtime_dir / "api-token"
    if not private_path_safe(target):
        raise ConfigurationError("The model token must be a regular private file.")
    if not target.exists():
        descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as output:
            output.write(secrets.token_urlsafe(48) + "\n")
    if os.name != "nt":
        target.chmod(0o600)
    content = target.read_text().strip() if target.stat().st_size <= 1024 else ""
    if not 32 <= len(content) <= 256 or any(ord(c) < 33 or ord(c) > 126 for c in content):
        raise ConfigurationError("The local model token has an unsupported format.")
    return target


def service_environment(config: PilotConfig, token_path: Path | None) -> dict:
    # Do not inherit accidental cloud/provider settings or prototype credentials.
    env = {key: value for key, value in os.environ.items() if not key.startswith("AISLESIGNALS_")}
    env.update({"AISLESIGNALS_MODE": "pilot", "AISLESIGNALS_DB_PATH": str(config.database),
                "AISLESIGNALS_WEB_DIST": str(config.root / "apps/web/dist"),
                "AISLESIGNALS_PORT": str(config.api_port), "AISLESIGNALS_VISION_BACKEND": "llamacpp",
                "AISLESIGNALS_VISION_URL": f"http://127.0.0.1:{config.vision_port}",
                "AISLESIGNALS_VISION_MODEL": "qwen3-vl:4b", "PYTHONDONTWRITEBYTECODE": "1"})
    if token_path is not None:
        env["AISLESIGNALS_VISION_TOKEN_FILE"] = str(token_path)
    env["AISLESIGNALS_PILOT_SUPERVISED_CHILD"] = "1"
    return env


def vision_command(config: PilotConfig) -> list[str]:
    pins = model_module(config.root)
    names = list(pins.WEIGHTS)
    return [str(config.vision_server), "--model", str(config.runtime_dir / "models" / names[0]),
            "--mmproj", str(config.runtime_dir / "models" / names[1]), "--alias", "qwen3-vl:4b",
            "--host", "127.0.0.1", "--port", str(config.vision_port), "--ctx-size", "8192",
            "--parallel", "1", "--image-max-tokens", "1024", "--api-key-file", str(config.runtime_dir / "api-token"),
            "--cors-origins", f"http://127.0.0.1:{config.api_port}", "--no-webui", "--no-agent", "--log-disable"]


def api_command(config: PilotConfig) -> list[str]:
    if getattr(sys, "frozen", False):
        return [sys.executable, "--api-child"]
    return [str(config.python_executable), str(config.root / "scripts/run_pilot.py"), "--api-child"]


def run_api_child() -> int:
    if os.environ.get("AISLESIGNALS_PILOT_SUPERVISED_CHILD") != "1" or os.environ.get("AISLESIGNALS_MODE") != "pilot":
        print("Start the application through the pilot launcher.", file=sys.stderr)
        return 1
    sys.path.insert(0, str(source_root()))
    import uvicorn
    from services.api.app import app
    uvicorn.run(app, host="127.0.0.1", port=int(os.environ["AISLESIGNALS_PORT"]),
                access_log=False, proxy_headers=False, log_level="error")
    return 0


def present_first_owner_setup(config: PilotConfig, *, reporter=print) -> None:
    """Issue an owner capability only to a local interactive operator, never logs."""
    state = local_json(config.api_port, "/api/setup/status")
    if not isinstance(state, dict) or state.get("available") is not True:
        return
    if not (sys.stdin.isatty() and sys.stdout.isatty()):
        reporter("First-owner setup is available. Open this launcher in a local terminal to receive a private 15-minute setup code, or run accounts setup-token --help.")
        return
    sys.path.insert(0, str(source_root()))
    from services.api.store import Store
    from services.api.pilot_admin import issue_setup_token
    try:
        issued = issue_setup_token(Store(str(config.database), mode="pilot"))
    except ValueError:
        # Another local operator may have completed setup after the status read.
        reporter("Setup status changed. Refresh the browser to sign in or retry local setup.")
        return
    reporter("Private first-owner setup code (expires in 15 minutes; enter only in this laptop's setup form):")
    reporter(issued["token"])


@dataclass
class OwnedService:
    name: str
    command: list[str]
    port: int
    ready_path: str
    timeout: float
    process: subprocess.Popen | None = None
    restarts: int = 0
    token: str = ""
    disabled: bool = False


class Supervisor:
    def __init__(self, config: PilotConfig, env: dict, *, stop_event=None, reporter=print):
        self.config = config
        self.env = env
        self.stop_event = stop_event or threading.Event()
        self.reporter = reporter
        self.services: list[OwnedService] = []
        self.rearm_required = False
        self.last_state = "STARTING"
        self.last_wall = time.time()
        self.last_monotonic = time.monotonic()
        self.windows_job = None
        self.stopped = False

    def status(self, state: str, detail: str) -> None:
        self.last_state = state
        write_report(self.config.data_dir / "runtime-status.json", {
            "schema_version": 1, "generated_at": datetime.now(timezone.utc).isoformat(),
            "state": state, "detail": detail, "camera_monitoring": "NOT_VERIFIED",
            "rearm_required": self.rearm_required,
            "services": [{"name": s.name, "running": bool(s.process and s.process.poll() is None),
                          "restarts": s.restarts, "disabled": s.disabled} for s in self.services]})
        self.reporter(detail)

    def resumed(self, *, wall=None, monotonic=None) -> bool:
        wall = time.time() if wall is None else wall
        monotonic = time.monotonic() if monotonic is None else monotonic
        elapsed = monotonic - self.last_monotonic
        gap = abs((wall - self.last_wall) - elapsed)
        self.last_wall, self.last_monotonic = wall, monotonic
        if elapsed > 15 or gap > 15 or elapsed < 0:
            self.rearm_required = True
        return self.rearm_required

    def start(self, service: OwnedService) -> bool:
        if self.stopped or self.stop_event.is_set() or not port_available(service.port):
            return False
        try:
            if os.name == "nt":
                if self.windows_job is None:
                    try:
                        from windows_process_job import WindowsProcessJob
                    except ModuleNotFoundError:
                        from scripts.windows_process_job import WindowsProcessJob
                    self.windows_job = WindowsProcessJob()
                service.process = self.windows_job.spawn(service.command, cwd=self.config.root, env=self.env)
            else:
                service.process = subprocess.Popen(service.command, cwd=self.config.root, env=self.env,
                    stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    start_new_session=True)
        except (OSError, ValueError):
            return False
        if service not in self.services:
            self.services.append(service)
        deadline = time.monotonic() + service.timeout
        while time.monotonic() < deadline and not self.stop_event.is_set():
            if self.resumed() or service.process.poll() is not None:
                break
            health = local_json(service.port, service.ready_path, service.token)
            if service.process.poll() is None and isinstance(health, dict):
                if service.name == "api" and health.get("status") == "ok" and health.get("mode") in {"pilot", "supervised-pilot", "local-pilot"}:
                    return True
                models = health.get("data")
                if service.name == "vision" and isinstance(models, list) and any(isinstance(m, dict) and m.get("id") == "qwen3-vl:4b" for m in models):
                    return True
            self.stop_event.wait(0.2)
        self.stop_service(service)
        return False

    @staticmethod
    def stop_service(service: OwnedService) -> None:
        child = service.process
        if child is None:
            return
        if child.poll() is None:
            try:
                if os.name != "nt":
                    os.killpg(child.pid, signal.SIGTERM)
                else:
                    child.terminate()
                child.wait(timeout=5)
            except ProcessLookupError:
                pass
            except subprocess.TimeoutExpired:
                if os.name != "nt":
                    try:
                        os.killpg(child.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                else:
                    child.kill()
                child.wait(timeout=5)
        else:
            child.wait()
        service.process = None

    def shutdown(self) -> None:
        self.stopped = True
        try:
            for service in reversed(self.services):
                self.stop_service(service)
        finally:
            # On Windows the OS also closes this non-inherited handle if the
            # launcher is killed abruptly, terminating only its contained tree.
            if self.windows_job is not None:
                self.windows_job.close()

    def restart_failed(self, service: OwnedService) -> bool:
        if service.disabled or service.restarts >= self.config.restart_limit:
            return False
        self.stop_service(service)
        service.restarts += 1
        self.status("RECOVERING", f"The {service.name} service stopped. Attempting bounded recovery {service.restarts}.")
        if self.stop_event.wait(min(2 ** service.restarts, 8)) or self.resumed():
            return False
        return self.start(service)

    def monitor(self) -> int:
        while not self.stop_event.wait(1):
            if self.resumed():
                self.status("REARM_REQUIRED", "Sleep, clock change or a long scheduling pause detected. Monitoring must be restarted and its source explicitly reselected.")
                return 3
            for service in self.services:
                if service.disabled or service.process is None or service.process.poll() is None:
                    continue
                recovered = False
                while service.restarts < self.config.restart_limit and not self.stop_event.is_set():
                    if self.restart_failed(service):
                        recovered = True
                        break
                    if self.rearm_required:
                        return 3
                if recovered:
                    self.status("SERVICES_READY", f"The {service.name} service recovered. Check the browser and explicitly confirm source readiness; camera monitoring is not verified by the launcher.")
                elif service.name == "api":
                    self.status("FAILED", "Application recovery failed. Restart the launcher after checking the local installation.")
                    return 1
                else:
                    service.disabled = True
                    # The already running API has an immutable environment.
                    # Restart our API with an explicit provider interlock so a
                    # later unrelated service on the model port cannot receive frames.
                    self.env["AISLESIGNALS_VISION_DISABLED"] = "1"
                    self.env.pop("AISLESIGNALS_VISION_TOKEN_FILE", None)
                    self.env["AISLESIGNALS_VISION_TOKEN"] = secrets.token_urlsafe(48)
                    api = next((item for item in self.services if item.name == "api"), None)
                    if api is not None:
                        self.stop_service(api)
                        if not self.start(api):
                            self.status("FAILED", "The model failed and casework could not restart with analysis disabled.")
                            return 1
                    self.status("DEGRADED", "Local vision recovery failed. Casework remains available; product interaction analysis is unavailable.")
        return 0


def main() -> int:
    if len(sys.argv) > 1 and sys.argv[1] == "rollout":
        sys.path.insert(0, str(source_root()))
        from scripts.coordinate_rollout import main as rollout_main
        return rollout_main(sys.argv[2:])
    if len(sys.argv) > 1 and sys.argv[1] == "backup":
        sys.path.insert(0, str(source_root()))
        from scripts.pilot_backup import main as backup_main
        return backup_main(sys.argv[2:])
    if len(sys.argv) > 1 and sys.argv[1] == "accounts":
        sys.path.insert(0, str(source_root()))
        from services.api.pilot_identity import main as accounts_main
        return accounts_main(sys.argv[2:])
    if len(sys.argv) > 1 and sys.argv[1] == "model-setup":
        setup_parser = argparse.ArgumentParser(description="Download and verify the optional local model/runtime into private external storage.")
        setup_parser.add_argument("--runtime-dir", type=Path)
        setup_parser.add_argument("--runtime-only", action="store_true")
        setup_args = setup_parser.parse_args(sys.argv[2:])
        try:
            config = load_config(None, root=source_root())
            runtime_dir = setup_args.runtime_dir or config.runtime_dir
            prepare_private_directory(runtime_dir)
            pins = model_module(source_root())
            pins.RUNTIME = runtime_dir.resolve()
            pins.setup(runtime_only=setup_args.runtime_only)
            return 0
        except (OSError, ValueError, ConfigurationError):
            print("Model setup failed. Check disk space, download connectivity and the selected private runtime directory.", file=sys.stderr)
            return 1
    parser = argparse.ArgumentParser(description=__doc__, epilog="Account management: accounts --help. Optional model download: model-setup --help. Offline recovery: backup --help. Six-branch readiness: rollout --help.")
    parser.add_argument("--config", type=Path)
    parser.add_argument("--check", action="store_true", help="Read-only readiness report; do not start services")
    parser.add_argument("--report", type=Path, help="Write privacy-minimised readiness JSON")
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--port", type=int)
    parser.add_argument("--data-dir", type=Path)
    parser.add_argument("--casework-only", action="store_true", help="Disable product interaction analysis for this launch")
    parser.add_argument("--api-child", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.api_child:
        return run_api_child()
    supervisor = None
    try:
        root = source_root()
        default = default_data_dir(root) / "config.json"
        config = load_config(args.config or (default if default.is_file() else None), root=root)
        if args.port is not None:
            if not 1024 <= args.port <= 65535 or args.port == config.vision_port:
                raise ConfigurationError("Choose distinct local ports between 1024 and 65535.")
            config = replace(config, api_port=args.port)
        if args.data_dir:
            config = replace(config, data_dir=Path(os.path.abspath(args.data_dir.expanduser())))
        if args.casework_only:
            config = replace(config, vision_enabled=False)
        report = preflight(config)
        if args.report:
            write_report(args.report, report)
        if args.check:
            import json
            print(json.dumps(report, indent=2))
            return 1 if report["launch_status"] == "FAIL" else 0
        for check in report["checks"]:
            if check["status"] == "FAIL":
                print(f"{check['id']}: {check['detail']}")
        if report["launch_status"] == "FAIL":
            print("Launch refused. Resolve the failed readiness checks; existing services and data were not changed.")
            return 1
        prepare_private_directory(config.data_dir)
        for suffix in ("", "-wal", "-shm"):
            database_file = Path(str(config.database) + suffix)
            if database_file.exists():
                if not private_path_safe(database_file):
                    raise ConfigurationError("Database files must not traverse symbolic links.")
                if os.name != "nt":
                    database_file.chmod(0o600)
        token_path = ensure_token(config) if config.vision_enabled else None
        env = service_environment(config, token_path)
        supervisor = Supervisor(config, env)
        shutdown_signals = [signal.SIGINT, signal.SIGTERM]
        if os.name != "nt" and hasattr(signal, "SIGHUP"):
            shutdown_signals.append(signal.SIGHUP)
        for sig in shutdown_signals:
            signal.signal(sig, lambda *_: supervisor.stop_event.set())
        if os.name != "nt":
            os.umask(0o077)
        model_usable = config.vision_enabled and not any(c["status"] == "FAIL" for c in report["checks"] if c["id"].startswith(("model_weight_", "vision_runtime")))
        vision_ready = False
        if model_usable:
            pins = model_module(config.root)
            env["AISLESIGNALS_VISION_MODEL_DIGEST"] = next(iter(pins.WEIGHTS.values()))
            supervisor.status("STARTING", "Starting the pinned local vision service; the source is not connected by this launcher.")
            vision = OwnedService("vision", vision_command(config), config.vision_port, "/v1/models", config.model_start_timeout_seconds,
                                  token=token_path.read_text().strip())
            vision_ready = supervisor.start(vision)
            if not vision_ready:
                vision.disabled = True
        if supervisor.stop_event.is_set() or supervisor.rearm_required:
            return 3 if supervisor.rearm_required else 0
        if not vision_ready:
            # Prevent accidental attachment to a later unrelated local provider.
            env["AISLESIGNALS_VISION_DISABLED"] = "1"
            env["AISLESIGNALS_VISION_TOKEN"] = secrets.token_urlsafe(48)
            env.pop("AISLESIGNALS_VISION_TOKEN_FILE", None)
        else:
            env["AISLESIGNALS_VISION_DISABLED"] = "0"
        api = OwnedService("api", api_command(config), config.api_port, "/api/health", 30)
        supervisor.status("STARTING", "Starting the local pilot application with named-account access.")
        if not supervisor.start(api):
            supervisor.status("FAILED", "Application did not become ready. Check pilot account setup, API dependencies and local database compatibility.")
            return 1
        state = "SERVICES_READY" if vision_ready else "DEGRADED"
        supervisor.status(state, "Application available. Select and test the CCTV source in the browser; camera monitoring is not verified." if vision_ready else "Application available for casework. Product interaction analysis is unavailable or disabled.")
        url = f"http://127.0.0.1:{config.api_port}/#live-detection"
        print(f"Open {url}. Keep this launcher open and the laptop awake. Ctrl+C stops its services.")
        present_first_owner_setup(config)
        if not args.no_browser:
            webbrowser.open(url)
        return supervisor.monitor()
    except ConfigurationError as error:
        print(str(error), file=sys.stderr)
        return 1
    except (OSError, ValueError):
        print("Local pilot startup failed. Check configuration and private file permissions; no credentials were logged.", file=sys.stderr)
        return 1
    finally:
        if supervisor is not None:
            supervisor.shutdown()
            try:
                supervisor.status("REARM_REQUIRED" if supervisor.rearm_required else "FAILED" if supervisor.last_state == "FAILED" else "STOPPED", "Launcher stopped its owned services. Reopen it and explicitly select the CCTV source to resume.")
            except OSError:
                pass


if __name__ == "__main__":
    raise SystemExit(main())
