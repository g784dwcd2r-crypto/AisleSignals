#!/usr/bin/env python3
"""Wrap a verified macOS pilot bundle in an unsigned application DMG."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import plistlib
import platform
import shutil
import subprocess
import tempfile


APP_NAME = "AisleSignals Pilot.app"
EXECUTABLE = "AisleSignalsPilot"


def digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def require_macos() -> None:
    if platform.system() != "Darwin":
        raise SystemExit("A macOS runner is required to create and verify the DMG.")
    if not shutil.which("hdiutil"):
        raise SystemExit("hdiutil is required to create the DMG.")


def build_app(source: Path, destination: Path, version: str) -> Path:
    executable = source / EXECUTABLE
    manifest = source / "release-manifest.json"
    if not source.is_dir() or not executable.is_file() or not os.access(executable, os.X_OK):
        raise SystemExit("Use an extracted macOS AisleSignalsPilot bundle.")
    if not manifest.is_file():
        raise SystemExit("The verified pilot release manifest is missing.")
    release = json.loads(manifest.read_text(encoding="utf-8"))
    if release.get("bundle") != "AisleSignalsPilot" or release.get("platform") != "Darwin":
        raise SystemExit("The release manifest is not a macOS pilot bundle.")

    app = destination / APP_NAME
    macos = app / "Contents" / "MacOS"
    resources = app / "Contents" / "Resources"
    runtime = resources / "AisleSignalsPilot"
    macos.mkdir(parents=True)
    shutil.copytree(source, runtime, symlinks=True)
    launcher = macos / EXECUTABLE
    launcher.write_text(
        "#!/bin/sh\n"
        'contents="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"\n'
        'exec "$contents/Resources/AisleSignalsPilot/AisleSignalsPilot" "$@"\n',
        encoding="utf-8",
    )
    launcher.chmod(0o755)
    info = {
        "CFBundleDevelopmentRegion": "en",
        "CFBundleDisplayName": "AisleSignals Pilot",
        "CFBundleExecutable": EXECUTABLE,
        "CFBundleIdentifier": "ie.aislesignals.pilot",
        "CFBundleInfoDictionaryVersion": "6.0",
        "CFBundleName": "AisleSignals Pilot",
        "CFBundlePackageType": "APPL",
        "CFBundleShortVersionString": version,
        "CFBundleVersion": version,
        "LSArchitecturePriority": ["arm64"],
        "NSCameraUsageDescription": (
            "AisleSignals uses a camera only after an authorised pharmacy operator selects it for local monitoring."
        ),
        "NSHighResolutionCapable": True,
        "NSSupportsAutomaticGraphicsSwitching": True,
    }
    with (app / "Contents" / "Info.plist").open("wb") as output:
        plistlib.dump(info, output, sort_keys=True)
    return app


def create_dmg(source: Path, output_dir: Path, version: str, smoke: bool) -> Path:
    require_macos()
    output_dir.mkdir(parents=True, exist_ok=True)
    architecture = platform.machine().lower()
    filename = f"AisleSignalsPilot-macOS-{architecture}-v{version}-unsigned.dmg"
    destination = output_dir / filename
    if destination.exists():
        raise SystemExit(f"Refusing to replace existing output: {destination}")
    with tempfile.TemporaryDirectory(prefix="aislesignals-dmg-") as directory:
        staging = Path(directory) / "AisleSignals Pilot"
        staging.mkdir()
        app = build_app(source, staging, version)
        (staging / "Applications").symlink_to("/Applications")
        readme = source / "READ-ME.txt"
        if readme.is_file():
            shutil.copy2(readme, staging / "READ ME.txt")
        subprocess.run(
            [
                "hdiutil",
                "create",
                "-quiet",
                "-volname",
                "AisleSignals Pilot",
                "-srcfolder",
                str(staging),
                "-format",
                "UDZO",
                str(destination),
            ],
            check=True,
        )
        if smoke:
            subprocess.run([str(app / "Contents" / "MacOS" / EXECUTABLE), "--help"], check=True)
    subprocess.run(["hdiutil", "verify", str(destination)], check=True)
    checksum = destination.with_name(destination.name + ".sha256")
    checksum.write_text(f"{digest(destination)}  {destination.name}\n", encoding="ascii")
    return destination


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, default=Path("dist"))
    parser.add_argument("--version", default=None)
    parser.add_argument("--smoke", action="store_true")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    version = args.version or json.loads((root / "package.json").read_text(encoding="utf-8"))["version"]
    if not isinstance(version, str) or not version or any(character not in "0123456789." for character in version):
        raise SystemExit("Use a numeric dotted application version.")
    result = create_dmg(args.bundle.resolve(), args.output_dir.resolve(), version, args.smoke)
    print(result)
    print(result.with_name(result.name + ".sha256"))


if __name__ == "__main__":
    main()
