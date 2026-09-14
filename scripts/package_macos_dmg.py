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
import re
import shutil
import subprocess
import tempfile
from urllib.parse import urlsplit


APP_NAME = "AisleSignals Pilot.app"
EXECUTABLE = "AisleSignalsPilot"
STAGING_CLOUD_ORIGIN = "https://aislesignals-control-staging.onrender.com"


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


def select_sdk(explicit: Path | None = None) -> Path:
    """Select an installed SDK compatible with the running macOS major.

    A Command Line Tools update can leave ``xcrun`` pointing at a newer SDK
    that its own linker cannot read. Prefer that default unless it targets a
    future macOS major; in that case select the newest installed host-major SDK.
    """
    if explicit is not None:
        selected = explicit.resolve()
    else:
        selected = Path(subprocess.check_output(
            ["xcrun", "--sdk", "macosx", "--show-sdk-path"], text=True).strip()).resolve()
        try:
            host_major = int(platform.mac_ver()[0].split(".")[0])
            match = re.search(r"MacOSX(\d+)(?:\.[0-9]+)?\.sdk$", selected.name)
            sdk_major = int(match.group(1)) if match else host_major
        except (ValueError, IndexError):
            host_major = sdk_major = 0
        if host_major and sdk_major > host_major:
            choices = []
            for candidate in selected.parent.glob(f"MacOSX{host_major}*.sdk"):
                version = re.fullmatch(rf"MacOSX{host_major}(?:\.([0-9]+))?\.sdk", candidate.name)
                if version and candidate.is_dir():
                    choices.append((int(version.group(1) or 0), candidate.resolve()))
            if choices:
                selected = max(choices)[1]
    if not selected.is_dir() or not (selected / "SDKSettings.plist").is_file():
        raise SystemExit("Select an installed macOS SDK directory with --sdk.")
    return selected


def cloud_origin(value: str) -> str:
    try:
        parsed = urlsplit(value)
        if (parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password
                or parsed.path not in {"", "/"} or parsed.query or parsed.fragment or parsed.port == 0
                or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9.-]*", parsed.hostname)):
            raise ValueError
    except (TypeError, ValueError, AttributeError):
        raise SystemExit("Use a path-free HTTPS origin for --cloud-origin.") from None
    port = f":{parsed.port}" if parsed.port and parsed.port != 443 else ""
    return f"https://{parsed.hostname.lower()}{port}/"


def build_app(source: Path, destination: Path, version: str, *, sdk: Path | None = None,
              cloud: str = STAGING_CLOUD_ORIGIN) -> Path:
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
    desktop_source = Path(__file__).resolve().parents[1] / "packaging" / "macos" / "AisleSignalsDesktop.m"
    subprocess.run(
        [
            "xcrun", "clang", "-isysroot", str(select_sdk(sdk)), "-fobjc-arc", str(desktop_source), "-o", str(launcher),
            "-framework", "Cocoa", "-framework", "WebKit",
        ],
        check=True,
    )
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
        "AisleSignalsCloudOrigin": cloud_origin(cloud),
        "LSMinimumSystemVersion": "13.0",
        "LSArchitecturePriority": ["arm64"],
        "NSCameraUsageDescription": (
            "AisleSignals uses a camera only after an authorised pharmacy operator selects it for local monitoring."
        ),
        "NSAppTransportSecurity": {"NSAllowsLocalNetworking": True},
        "NSHighResolutionCapable": True,
        "NSSupportsAutomaticGraphicsSwitching": True,
    }
    with (app / "Contents" / "Info.plist").open("wb") as output:
        plistlib.dump(info, output, sort_keys=True)
    return app


def create_dmg(source: Path, output_dir: Path, version: str, smoke: bool, *, sdk: Path | None = None,
               cloud: str = STAGING_CLOUD_ORIGIN) -> Path:
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
        app = build_app(source, staging, version, sdk=sdk, cloud=cloud)
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
    parser.add_argument("--sdk", type=Path, default=None,
                        help="Explicit installed macOS SDK; otherwise select a host-compatible xcrun SDK")
    parser.add_argument("--cloud-origin", default=STAGING_CLOUD_ORIGIN,
                        help="Path-free HTTPS cloud workspace origin embedded in the application")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    version = args.version or json.loads((root / "package.json").read_text(encoding="utf-8"))["version"]
    if not isinstance(version, str) or not version or any(character not in "0123456789." for character in version):
        raise SystemExit("Use a numeric dotted application version.")
    result = create_dmg(args.bundle.resolve(), args.output_dir.resolve(), version, args.smoke,
                        sdk=args.sdk, cloud=args.cloud_origin)
    print(result)
    print(result.with_name(result.name + ".sha256"))


if __name__ == "__main__":
    main()
