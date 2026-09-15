"""Fail-closed production desktop release and signing preflight."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

try:
    from release_identity import ROOT, SHA, ReleaseIdentityError, load_identity, validate_repository_versions
except ModuleNotFoundError:
    from scripts.release_identity import ROOT, SHA, ReleaseIdentityError, load_identity, validate_repository_versions


class PreflightError(ValueError):
    pass


REQUIRED = {
    "Darwin": {
        "environment": ("AISLESIGNALS_APPLE_DEVELOPER_ID", "AISLESIGNALS_APPLE_TEAM_ID",
                        "AISLESIGNALS_APPLE_NOTARY_PROFILE"),
        "tools": ("codesign", "xcrun", "hdiutil", "spctl"),
    },
    "Windows": {
        "environment": ("AISLESIGNALS_WINDOWS_CERT_SHA1",),
        "tools": ("signtool", "iscc"),
    },
}


def exact_source(root: Path = ROOT, *, environment=os.environ) -> str:
    try:
        sha = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip()
        dirty = subprocess.check_output(["git", "status", "--porcelain"], cwd=root, text=True).strip()
    except (OSError, subprocess.CalledProcessError):
        raise PreflightError("The exact release source could not be established.") from None
    if not SHA.fullmatch(sha) or dirty:
        raise PreflightError("Production release source must be a clean exact Git commit.")
    expected = environment.get("AISLESIGNALS_RELEASE_SHA")
    if not expected or not SHA.fullmatch(expected):
        raise PreflightError("AISLESIGNALS_RELEASE_SHA must name the reviewed exact release commit.")
    if expected != sha:
        raise PreflightError("AISLESIGNALS_RELEASE_SHA does not match the checked-out commit.")
    return sha


def private_update_key(path: Path) -> None:
    path = path.expanduser().absolute()
    try:
        info = path.lstat()
    except OSError:
        raise PreflightError("The update signing key file is missing.") from None
    if path.is_symlink() or not stat.S_ISREG(info.st_mode) or info.st_size > 4096:
        raise PreflightError("The update signing key must be a bounded regular file.")
    if os.name != "nt" and info.st_mode & 0o077:
        raise PreflightError("The update signing key must be owner-only.")
    try:
        key = serialization.load_pem_private_key(path.read_bytes(), password=None)
    except (OSError, ValueError, TypeError):
        raise PreflightError("The update signing key must be an unencrypted Ed25519 PEM key.") from None
    if not isinstance(key, Ed25519PrivateKey):
        raise PreflightError("The update signing key must be an unencrypted Ed25519 PEM key.")


def signing(platform_name: str, *, environment=os.environ, which=shutil.which) -> dict:
    policy = REQUIRED[platform_name]
    missing_environment = [name for name in policy["environment"] if not environment.get(name)]
    missing_tools = [name for name in policy["tools"] if not which(name)]
    if missing_environment or missing_tools:
        missing = [*(f"environment:{name}" for name in missing_environment),
                   *(f"tool:{name}" for name in missing_tools)]
        raise PreflightError("Signing preflight unavailable: " + ", ".join(missing))
    if platform_name == "Windows" and not re.fullmatch(r"[0-9A-Fa-f]{40}", environment["AISLESIGNALS_WINDOWS_CERT_SHA1"]):
        raise PreflightError("The Windows signing certificate thumbprint is invalid.")
    if platform_name == "Darwin" and not re.fullmatch(r"[A-Z0-9]{10}", environment["AISLESIGNALS_APPLE_TEAM_ID"]):
        raise PreflightError("The Apple team identifier is invalid.")
    return {"platform": platform_name, "ready": True,
            "environment_names": list(policy["environment"]), "tools": list(policy["tools"])}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--platform", required=True, choices=("Darwin", "Windows"))
    parser.add_argument("--update-private-key-file", required=True, type=Path)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    try:
        identity = load_identity()
        validate_repository_versions(identity)
        sha = exact_source()
        private_update_key(args.update_private_key_file)
        result = signing(args.platform)
        result.update({"schema_version": 1, "product": identity.product,
                       "version": identity.version, "source_commit": sha,
                       "credentials_printed": False})
        print(json.dumps(result, sort_keys=True) if args.json else "Desktop release signing preflight passed.")
        return 0
    except (PreflightError, ReleaseIdentityError) as error:
        print(str(error), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
