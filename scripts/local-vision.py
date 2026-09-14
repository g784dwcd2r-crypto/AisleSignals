#!/usr/bin/env python3
"""Prepare and run the optional local interaction model in configured local storage.

Only setup downloads files. Run uses pinned, checksum-verified local weights.
No cloud inference, camera access, or system-service installation.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import secrets
import stat
import subprocess
import sys
import tarfile
import tempfile
import time
import urllib.request
import zipfile

try:
    from pilot_preflight import add_storage_arguments, private_path_safe, resolve_config
except ModuleNotFoundError:
    from scripts.pilot_preflight import add_storage_arguments, private_path_safe, resolve_config

ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT / ".local" / "vision-runtime"
MODEL_REVISION = "1cd86afb9a95c410a6038ab3b40d8b578c892266"
MODEL_BASE = f"https://huggingface.co/Qwen/Qwen3-VL-4B-Instruct-GGUF/resolve/{MODEL_REVISION}/"
WEIGHTS = {
    "Qwen3VL-4B-Instruct-Q4_K_M.gguf": "66358cb18bb6b3b1b6675aa412c7a88ef01d228f481184d13668e5201c730a0a",
    "mmproj-Qwen3VL-4B-Instruct-F16.gguf": "256f3a43bd4205ffef48d6b92715e1e70b5b0e9aef06522584967513a9985331",
}
RUNTIME_URL = "https://github.com/ollama/ollama/releases/download/v0.34.0/ollama-darwin.tgz"
RUNTIME_SHA256 = "dd12b00bcce2d6551178e67ada90d5af9f75bdb54a118b96655250fa3e8ef734"
WINDOWS_ARCHIVES = {
    "amd64": ("ollama-windows-amd64.zip", "a7dd1b174f39d3d1b8a25d4cbc86045d0e190b17187bfdcbe2f2ee3b5a11470e"),
    "arm64": ("ollama-windows-arm64.zip", "a5ce957f750dbe85b34c9da81440969f4b9c7f747a3bb629ce50ac6cd2a63536"),
}


def runtime_spec(system=None, machine=None):
    system = system or sys.platform
    machine = (machine or platform.machine()).lower()
    if system == "darwin" and machine == "arm64":
        return {"url": RUNTIME_URL, "sha256": RUNTIME_SHA256,
                "directory": "ollama", "server": "llama-server", "archive": "ollama-darwin.tgz"}
    if system == "win32" and machine in {"amd64", "x86_64", "arm64", "aarch64"}:
        arch = "arm64" if machine in {"arm64", "aarch64"} else "amd64"
        archive, checksum = WINDOWS_ARCHIVES[arch]
        return {"url": f"https://github.com/ollama/ollama/releases/download/v0.34.0/{archive}",
                "sha256": checksum, "directory": f"ollama-windows-{arch}",
                "server": "lib/ollama/llama-server.exe", "archive": archive}
    return None


def default_server(runtime_dir=None):
    spec = runtime_spec()
    return (Path(runtime_dir or RUNTIME) / spec["directory"] / spec["server"]) if spec else None


def extract_windows_archive(archive, destination):
    """Extract only pinned archives; reject traversal, links and oversized payloads."""
    destination = Path(destination).resolve()
    with zipfile.ZipFile(archive) as bundle:
        members = bundle.infolist()
        if len(members) > 10000 or sum(m.file_size for m in members) > 8 * 1024**3:
            raise ValueError("Runtime archive exceeds its resource limit.")
        for member in members:
            parts = member.filename.replace("\\", "/").split("/")
            if (any(p in {"..", "."} or ":" in p for p in parts)
                    or member.filename.startswith(("/", "\\"))
                    or (member.external_attr >> 16) & 0o170000 == 0o120000):
                raise ValueError("Runtime archive contains an unsafe path.")
            target = destination.joinpath(*parts)
            if not target.resolve().is_relative_to(destination):
                raise ValueError("Runtime extraction cannot traverse symbolic links.")
        bundle.extractall(destination)


def digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def existing_download(path: Path, expected: str) -> bool:
    if not private_path_safe(path):
        raise ValueError("Model storage requires owner-controlled directories without unsafe writable ancestors, symbolic links or reparse points.")
    if not path.exists():
        return False
    if not path.is_file() or digest(path) != expected:
        raise ValueError(f"Existing {path.name} does not match the pinned download. It was preserved; review it or choose an empty --runtime-dir.")
    return True


def download(url: str, path: Path, expected: str) -> None:
    if existing_download(path, expected):
        print(f"Verified {path.name}", flush=True)
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    print(f"Downloading {path.name}", flush=True)
    descriptor, filename = tempfile.mkstemp(prefix=f".{path.name}.download-", dir=path.parent)
    pending = Path(filename)
    try:
        with os.fdopen(descriptor, "wb") as output, urllib.request.urlopen(url, timeout=60) as response:
            received, last = 0, time.monotonic()
            while chunk := response.read(1024 * 1024):
                output.write(chunk)
                received += len(chunk)
                if time.monotonic() - last > 10:
                    print(f"  {received / 1024**2:.0f} MiB", flush=True)
                    last = time.monotonic()
        if digest(pending) != expected:
            raise ValueError(f"Checksum failed for {path.name}; the file was not installed.")
        # Hard-link publication creates a new name atomically on local NTFS and
        # POSIX filesystems. An existing target (including a competing setup)
        # is never replaced. Do not use replace()/rename() over a user file.
        os.link(pending, path)
    finally:
        pending.unlink(missing_ok=True)


def install_runtime(archive: Path, destination: Path, server: str) -> None:
    """Stage the pinned bundle; a prior installation is verified, never replaced."""
    if not private_path_safe(destination) or (destination.exists() and not destination.is_dir()):
        raise ValueError("The native runtime directory is unsafe or occupied by a file.")
    with tempfile.TemporaryDirectory(prefix=".runtime-setup-", dir=RUNTIME) as temporary:
        staged = Path(temporary) / "runtime"
        staged.mkdir()
        try:
            if archive.suffix == ".zip":
                extract_windows_archive(archive, staged)
            else:
                with tarfile.open(archive) as bundle:
                    bundle.extractall(staged, filter="data")
        except (tarfile.TarError, zipfile.BadZipFile):
            raise ValueError("The native runtime archive could not be read; existing installations were preserved.") from None
        if not (staged / server).is_file():
            raise ValueError("The runtime archive did not contain its expected server; it was not installed.")
        if not destination.exists():
            staged.rename(destination)
            return
        # A missing/changed member may belong to a different runtime. Preserve
        # it and require the operator to select a new directory or review it.
        for candidate in staged.rglob("*"):
            target = destination / candidate.relative_to(staged)
            if candidate.is_symlink():
                matches = target.is_symlink() and target.readlink() == candidate.readlink()
            elif candidate.is_dir():
                matches = not target.is_symlink() and target.is_dir()
            else:
                matches = not target.is_symlink() and target.is_file() and digest(target) == digest(candidate)
            if not matches:
                raise ValueError("Existing native runtime differs from the pinned bundle and was preserved. Review it or choose an empty --runtime-dir.")


def setup(runtime_only=False) -> None:
    if not private_path_safe(RUNTIME):
        raise ValueError("Model storage requires owner-controlled directories without unsafe writable ancestors, symbolic links or reparse points.")
    spec = runtime_spec()
    if runtime_only and spec is None:
        raise ValueError("No pinned runtime for this OS/architecture. Supply a compatible --server when running.")
    manifest_path = RUNTIME / "manifest.json"
    if not private_path_safe(manifest_path):
        raise ValueError("Model manifest cannot traverse symbolic links.")
    if manifest_path.exists():
        if not manifest_path.is_file() or manifest_path.stat().st_size > 16_384:
            raise ValueError("Existing runtime manifest was preserved; select a dedicated --runtime-dir.")
        previous = json.loads(manifest_path.read_text(encoding="utf-8"))
        if not isinstance(previous, dict) or previous.get("model") != "Qwen/Qwen3-VL-4B-Instruct-GGUF" or previous.get("revision") != MODEL_REVISION:
            raise ValueError("Existing runtime manifest identifies different content and was preserved. Select a dedicated --runtime-dir.")
    requested = [(MODEL_BASE + name, RUNTIME / "models" / name, checksum)
                 for name, checksum in WEIGHTS.items()] if not runtime_only else []
    if spec:
        requested.append((spec["url"], RUNTIME / spec["archive"], spec["sha256"]))
    # Reject any existing mismatched target before the first network request.
    missing = [item for item in requested if not existing_download(item[1], item[2])]
    RUNTIME.mkdir(parents=True, exist_ok=True, mode=0o700)
    for url, target, checksum in missing:
        download(url, target, checksum)
    # The bundle contains llama-server and its native dependencies. We do not run
    # Ollama itself; the selected server has no home-directory identity setup.
    if spec:
        archive = RUNTIME / spec["archive"]
        destination = RUNTIME / spec["directory"]
        install_runtime(archive, destination, spec["server"])
        if not default_server().is_file():
            raise ValueError("The runtime archive did not contain its expected server.")
        print(f"Installed pinned {sys.platform} {platform.machine()} llama-server bundle.")
    else:
        print("Weights ready. Supply an installed compatible llama-server using --server.")
    manifest = {
        "model": "Qwen/Qwen3-VL-4B-Instruct-GGUF",
        "revision": MODEL_REVISION,
        "weights_sha256": WEIGHTS,
        "runtime_bundle": "Ollama v0.34.0 (bundled llama-server only)",
        "runtime_archive_sha256": spec["sha256"] if spec else None,
        "runtime_platform": sys.platform,
        "runtime_architecture": platform.machine(),
        "weights_requested": not runtime_only,
        "validated_for_pharmacy_theft": False,
    }
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")


def token_file() -> Path:
    target = RUNTIME / "api-token"
    if not private_path_safe(target):
        raise ValueError("The local model token requires owner-controlled directories without unsafe writable ancestors, symbolic links or reparse points.")
    RUNTIME.mkdir(parents=True, exist_ok=True, mode=0o700)
    flags = getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    created = False
    try:
        descriptor = os.open(target, os.O_RDWR | os.O_CREAT | os.O_EXCL | flags, 0o600)
        created = True
    except FileExistsError:
        descriptor = os.open(target, os.O_RDONLY | flags)
    try:
        metadata = os.fstat(descriptor)
        if (not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1
                or (hasattr(os, "geteuid") and metadata.st_uid != os.geteuid())):
            raise ValueError("The local model token must be a regular file owned only by the current user.")
        # O_NOFOLLOW handles POSIX final-name races. On Windows, also compare
        # the opened file with the link-free path before any read/write; reject
        # all reparse points through the shared check, including junctions.
        if not private_path_safe(target) or not os.path.samestat(metadata, target.lstat()):
            raise ValueError("The local model token path changed; no model was started.")
        if created:
            with os.fdopen(os.dup(descriptor), "w", encoding="ascii") as stream:
                stream.write(secrets.token_urlsafe(48) + "\n")
            os.lseek(descriptor, 0, os.SEEK_SET)
        if os.fstat(descriptor).st_size > 1024:
            raise ValueError("The local model token has an unsupported format; the existing file was preserved.")
        content = os.read(descriptor, 1025)
        try:
            token = content.decode("ascii").strip()
        except UnicodeError:
            token = ""
        if len(content) > 1024 or not 32 <= len(token) <= 256 or any(ord(c) < 33 or ord(c) > 126 for c in token):
            raise ValueError("The local model token has an unsupported format; the existing file was preserved.")
        if os.name != "nt":
            os.fchmod(descriptor, 0o600)
    finally:
        os.close(descriptor)
    return target


def run(server: str | None, port: int) -> int:
    executable = Path(server).expanduser().resolve() if server else default_server()
    if executable is None or not executable.is_file():
        raise ValueError("llama-server is missing. Run setup or supply --server PATH.")
    for name, expected in WEIGHTS.items():
        candidate = RUNTIME / "models" / name
        if not private_path_safe(candidate) or not candidate.is_file() or digest(candidate) != expected:
            raise ValueError(f"Missing or changed model file {name}. Run setup.")
    key_path = token_file()
    print(f"Starting local experimental vision model at http://127.0.0.1:{port}", flush=True)
    print("Frames stay on this computer. Ctrl+C stops the model.", flush=True)
    args = [
        str(executable), "--model", str(RUNTIME / "models" / next(iter(WEIGHTS))),
        "--mmproj", str(RUNTIME / "models" / list(WEIGHTS)[1]),
        "--alias", "qwen3-vl:4b", "--host", "127.0.0.1", "--port", str(port),
        "--ctx-size", "8192", "--parallel", "1", "--image-max-tokens", "1024",
        "--api-key-file", str(key_path), "--cors-origins", "http://127.0.0.1:8765",
        "--no-webui", "--no-agent", "--log-disable",
    ]
    child = subprocess.Popen(args, cwd=RUNTIME)
    try:
        return child.wait()
    except KeyboardInterrupt:
        child.terminate()
        try:
            child.wait(timeout=10)
        except subprocess.TimeoutExpired:
            child.kill()
            child.wait()
        return 130


def main() -> int:
    global RUNTIME
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["setup", "run"])
    add_storage_arguments(parser)
    parser.add_argument("--server", help="Override the pinned compatible llama-server executable")
    parser.add_argument("--runtime-only", action="store_true", help="Setup runtime only; skip model downloads")
    parser.add_argument("--port", type=int, help="Override the configured vision port (default 11435)")
    args = parser.parse_args()
    if args.port is not None and not 1024 <= args.port <= 65535:
        parser.error("port must be between 1024 and 65535")
    if args.command == "setup" and (args.server is not None or args.port is not None):
        parser.error("--server and --port apply to run only; setup prepares the pinned files in --runtime-dir")
    if args.command == "run" and args.runtime_only:
        parser.error("--runtime-only applies to setup only")
    try:
        config = resolve_config(args.config, root=ROOT, data_dir=args.data_dir, runtime_dir=args.runtime_dir)
        RUNTIME = config.runtime_dir
        if not private_path_safe(RUNTIME):
            raise ValueError("Model storage requires owner-controlled directories without unsafe writable ancestors, symbolic links or reparse points.")
        if args.command == "setup":
            print(f"Model storage: {RUNTIME}", flush=True)
            print("Use the same --config, --data-dir and --runtime-dir selections when starting the model or launcher. Configuration and credentials are not changed.")
            setup(args.runtime_only)
            return 0
        return run(args.server or (str(config.vision_server) if config.vision_server is not None else None),
                   args.port if args.port is not None else config.vision_port)
    except (OSError, ValueError, tarfile.TarError) as error:
        print(f"Local vision: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
