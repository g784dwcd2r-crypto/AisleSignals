#!/usr/bin/env python3
"""Prepare and run the optional local interaction model, entirely in .local/.

Only setup downloads files. Run uses pinned, checksum-verified local weights.
No cloud inference, camera access, system service, or home-directory installation.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import secrets
import subprocess
import sys
import tarfile
import time
import urllib.request
import zipfile

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


def download(url: str, path: Path, expected: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and digest(path) == expected:
        print(f"Verified {path.name}", flush=True)
        return
    pending = path.with_suffix(path.suffix + ".part")
    print(f"Downloading {path.name}", flush=True)
    with urllib.request.urlopen(url, timeout=60) as response, pending.open("wb") as output:
        received, last = 0, time.monotonic()
        while chunk := response.read(1024 * 1024):
            output.write(chunk)
            received += len(chunk)
            if time.monotonic() - last > 10:
                print(f"  {received / 1024**2:.0f} MiB", flush=True)
                last = time.monotonic()
    if digest(pending) != expected:
        pending.unlink(missing_ok=True)
        raise ValueError(f"Checksum failed for {path.name}; the file was not installed.")
    pending.replace(path)


def setup(runtime_only=False) -> None:
    if not runtime_only:
        for name, checksum in WEIGHTS.items():
            download(MODEL_BASE + name, RUNTIME / "models" / name, checksum)
    # The bundle contains llama-server and its native dependencies. We do not run
    # Ollama itself; the selected server has no home-directory identity setup.
    spec = runtime_spec()
    if spec:
        archive = RUNTIME / spec["archive"]
        download(spec["url"], archive, spec["sha256"])
        destination = RUNTIME / spec["directory"]
        destination.mkdir(parents=True, exist_ok=True)
        if archive.suffix == ".zip":
            extract_windows_archive(archive, destination)
        else:
            with tarfile.open(archive) as bundle:
                bundle.extractall(destination, filter="data")
        if not default_server().is_file():
            raise ValueError("The runtime archive did not contain its expected server.")
        print(f"Installed pinned {sys.platform} {platform.machine()} llama-server bundle.")
    else:
        if runtime_only:
            raise ValueError("No pinned runtime for this OS/architecture. Supply a compatible --server.")
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
    (RUNTIME / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")


def token_file() -> Path:
    RUNTIME.mkdir(parents=True, exist_ok=True)
    target = RUNTIME / "api-token"
    if not target.exists():
        with open(target, "x", opener=lambda p, f: os.open(p, f, 0o600)) as stream:
            stream.write(secrets.token_urlsafe(48) + "\n")
    if os.name != "nt":
        target.chmod(0o600)
    return target


def run(server: str | None, port: int) -> int:
    executable = Path(server).expanduser().resolve() if server else default_server()
    if executable is None or not executable.is_file():
        raise ValueError("llama-server is missing. Run setup or supply --server PATH.")
    for name, expected in WEIGHTS.items():
        candidate = RUNTIME / "models" / name
        if not candidate.is_file() or digest(candidate) != expected:
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
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["setup", "run"])
    parser.add_argument("--server", help="Override the pinned compatible llama-server executable")
    parser.add_argument("--runtime-only", action="store_true", help="Setup runtime only; skip model downloads")
    parser.add_argument("--port", type=int, default=11435)
    args = parser.parse_args()
    if not 1024 <= args.port <= 65535:
        parser.error("port must be between 1024 and 65535")
    try:
        if args.command == "setup":
            setup(args.runtime_only)
            return 0
        return run(args.server, args.port)
    except (OSError, ValueError, tarfile.TarError) as error:
        print(f"Local vision: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
