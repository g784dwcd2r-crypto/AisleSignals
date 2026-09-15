"""Archive, manifest and optionally smoke-test an unsigned local desktop bundle."""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import os
from pathlib import Path
import platform
import subprocess
import sys
import tarfile
import tempfile
import zipfile

MANIFEST = "release-manifest.json"
MIN_ZIP_EPOCH = 315532800


def digest(path: Path) -> str:
    checksum = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            checksum.update(block)
    return checksum.hexdigest()


def inventory(bundle: Path) -> list[dict]:
    entries = []
    for path in sorted(bundle.rglob("*")):
        relative = path.relative_to(bundle).as_posix()
        if relative == MANIFEST:
            continue
        if path.is_symlink():
            target = os.readlink(path)
            # PyInstaller uses in-bundle framework symlinks on macOS. An
            # accidental absolute/outside target must never be distributed.
            if Path(target).is_absolute() or not path.resolve().is_relative_to(bundle.resolve()) or not path.exists():
                raise ValueError(f"Unsafe bundle link: {relative}")
            entries.append({"path": relative, "symlink": target})
        elif path.is_file():
            entries.append({"path": relative, "bytes": path.stat().st_size, "sha256": digest(path), "executable": bool(path.stat().st_mode & 0o111)})
    return entries


def verify_inventory(bundle: Path) -> None:
    expected = json.loads((bundle / MANIFEST).read_text(encoding="utf-8"))["files"]
    if inventory(bundle) != expected:
        raise ValueError("Bundle files, links or executable permissions differ from the release manifest")


def source_epoch(root: Path) -> int:
    supplied = os.environ.get("SOURCE_DATE_EPOCH")
    try:
        value = int(supplied) if supplied is not None else int(subprocess.check_output(
            ["git", "show", "-s", "--format=%ct", "HEAD"], cwd=root, text=True).strip())
    except (ValueError, OSError, subprocess.CalledProcessError):
        raise ValueError("SOURCE_DATE_EPOCH or the release commit timestamp is required.") from None
    if not 0 <= value <= 4_102_444_800:
        raise ValueError("The release source epoch is out of range.")
    return value


def deterministic_archive(bundle: Path, destination: Path, archive_format: str, epoch: int) -> Path:
    """Archive already-built bytes deterministically; it does not make a compiler reproducible."""
    if archive_format not in {"zip", "gztar"} or destination.exists():
        raise ValueError("Choose a new supported deterministic archive destination.")
    paths = [bundle, *sorted(bundle.rglob("*"), key=lambda item: item.relative_to(bundle).as_posix())]
    if archive_format == "zip":
        stamp = __import__("time").gmtime(max(epoch, MIN_ZIP_EPOCH))[:6]
        with zipfile.ZipFile(destination, "x", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as output:
            for path in paths:
                relative = Path(bundle.name) / path.relative_to(bundle)
                name = relative.as_posix() + ("/" if path.is_dir() else "")
                info = zipfile.ZipInfo(name, stamp)
                mode = path.lstat().st_mode
                info.create_system = 3
                info.external_attr = (mode & 0xFFFF) << 16
                if path.is_dir():
                    data = b""
                elif path.is_symlink():
                    info.external_attr = (0o120777 << 16)
                    data = os.readlink(path).encode()
                else:
                    data = path.read_bytes()
                output.writestr(info, data, compress_type=zipfile.ZIP_DEFLATED, compresslevel=9)
    else:
        with destination.open("xb") as raw:
            with gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=epoch,
                               compresslevel=9) as compressed:
                with tarfile.open(fileobj=compressed, mode="w", format=tarfile.PAX_FORMAT) as output:
                    for path in paths:
                        relative = (Path(bundle.name) / path.relative_to(bundle)).as_posix()
                        info = output.gettarinfo(str(path), arcname=relative)
                        info.uid = info.gid = 0
                        info.uname = info.gname = ""
                        info.mtime = epoch
                        if info.isfile():
                            with path.open("rb") as source:
                                output.addfile(info, source)
                        else:
                            output.addfile(info)
    return destination


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle-name", choices=["AisleSignalsPrototype", "AisleSignalsPilot"], default="AisleSignalsPrototype")
    parser.add_argument("--smoke", action="store_true", help="Extract and verify the archive, then run the packaged executable smoke test")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    bundle = root / "dist" / args.bundle_name
    if not bundle.is_dir():
        raise SystemExit("Build the selected PyInstaller bundle before archiving it.")
    mode = "pilot" if args.bundle_name == "AisleSignalsPilot" else "demo"
    executable = args.bundle_name + (".exe" if sys.platform == "win32" else "")
    lines = [
        f"{args.bundle_name} — unsigned {mode} development bundle",
        f"Run {executable}. Keep its terminal open while using the application.",
        "The local browser opens on http://127.0.0.1:8765; Ctrl+C stops the application.",
        "This is not a signed/notarised installer. Follow your organisation's installation policy.",
        "It cannot prove theft. Actual camera quality, laptop sleep, permissions, speakers and detector accuracy require site acceptance.",
        "The optional product interaction model and its runtime are installed separately; no model server is bundled.",
        "Checksums establish file integrity only, not publisher authenticity or detector accuracy.",
    ]
    if mode == "demo":
        lines += ["Use synthetic data only.", "Demo login: manager@harbour.demo / AisleDemo!2026"]
    else:
        lines += ["No demo accounts are available. On an empty workspace, use the private setup code displayed in the interactive launcher to create your first owner in the browser.", "After signing in, open Administration to add pharmacy branches and named staff. Account recovery: accounts --help.", "Use --check for launch preflight; --casework-only explicitly starts without product-model analysis.", "On macOS, Start at Login is available in the application menu. On Windows, use startup enable/status/disable; login startup never selects a source or arms monitoring.", "Use update check/download with a separately trusted Ed25519 public-key file. An update is never offered or saved until the release manifest signature and artifact hash validate, and it is never executed automatically.", "Pilot configuration and data live outside this application directory; do not replace them when updating the bundle."]
    (bundle / "READ-ME.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    revision = os.environ.get("GITHUB_SHA") or subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip()
    dirty = bool(subprocess.check_output(["git", "status", "--porcelain", "--untracked-files=no"], cwd=root, text=True).strip())
    manifest = {"format": "AISLESIGNALS_UNSIGNED_BUNDLE_V1", "bundle": args.bundle_name, "mode": mode,
                "version": json.loads((root / "package.json").read_text())["version"], "source_commit": revision,
                "source_has_uncommitted_changes": dirty, "platform": platform.system(), "architecture": platform.machine(),
                "signed": False, "files": inventory(bundle)}
    (bundle / MANIFEST).write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    verify_inventory(bundle)
    filename = root / "dist" / f"{args.bundle_name}-{platform.system()}-{platform.machine()}-unsigned"
    archive_format = "zip" if sys.platform == "win32" else "gztar"
    suffix = ".zip" if archive_format == "zip" else ".tar.gz"
    archive = deterministic_archive(bundle, Path(str(filename) + suffix), archive_format, source_epoch(root))
    archive.with_name(archive.name + ".sha256").write_text(f"{digest(archive)}  {archive.name}\n", encoding="ascii")
    if args.smoke:
        with tempfile.TemporaryDirectory(prefix="aislesignals-archive-") as directory:
            if archive_format == "zip":
                with zipfile.ZipFile(archive) as source:
                    source.extractall(directory)
            else:
                with tarfile.open(archive) as source:
                    source.extractall(directory, filter="data")
            extracted = Path(directory) / bundle.name
            verify_inventory(extracted)
            subprocess.run([sys.executable, str(root / "scripts/smoke_bundle.py"), str(extracted / executable), "--mode", mode], check=True)
    print(archive)
    print(archive.with_name(archive.name + ".sha256"))


if __name__ == "__main__":
    main()
