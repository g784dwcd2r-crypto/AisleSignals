"""Archive, manifest and optionally smoke-test an unsigned local desktop bundle."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import tarfile
import tempfile
import zipfile

MANIFEST = "release-manifest.json"


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
        lines += ["No demo accounts are available. On an empty workspace, use the private setup code displayed in the interactive launcher to create your first owner in the browser.", "After signing in, open Administration to add pharmacy branches and named staff. Account recovery: accounts --help.", "Use --check for launch preflight; --casework-only explicitly starts without product-model analysis.", "Pilot configuration and data live outside this application directory; do not replace them when updating the bundle."]
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
    archive = Path(shutil.make_archive(str(filename), archive_format, root_dir=bundle.parent, base_dir=bundle.name))
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
