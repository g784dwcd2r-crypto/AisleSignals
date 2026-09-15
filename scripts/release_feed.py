"""Create and verify deterministic Ed25519-signed desktop update envelopes."""

from __future__ import annotations

import argparse
import base64
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import sys
from urllib.parse import urlsplit

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

try:
    from desktop_update import MAX_ARTIFACT_BYTES, UpdateError, canonical, verify_envelope
    from release_identity import SHA, load_identity, validate_repository_versions
except ModuleNotFoundError:
    from scripts.desktop_update import MAX_ARTIFACT_BYTES, UpdateError, canonical, verify_envelope
    from scripts.release_identity import SHA, load_identity, validate_repository_versions


class FeedError(ValueError):
    pass


def _private_key(path: Path) -> Ed25519PrivateKey:
    path = path.expanduser().absolute()
    try:
        info = path.lstat()
        if path.is_symlink() or not stat.S_ISREG(info.st_mode) or info.st_size > 4096:
            raise ValueError
        if os.name != "nt" and info.st_mode & 0o077:
            raise ValueError
        if os.name == "nt":
            try:
                from cloud_private_windows import validate_path
            except ModuleNotFoundError:
                from scripts.cloud_private_windows import validate_path
            validate_path(path)
        key = serialization.load_pem_private_key(path.read_bytes(), password=None)
        if not isinstance(key, Ed25519PrivateKey):
            raise ValueError
        return key
    except (OSError, ValueError, TypeError):
        raise FeedError("Use an unencrypted owner-only Ed25519 private-key file.") from None


def artifact_digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def build_release(*, artifact: Path, platform_name: str, architecture: str, version: str,
                  published_at: str, source_commit: str, artifact_url: str) -> dict:
    identity = load_identity()
    validate_repository_versions(identity)
    allowed = identity.mac_architectures if platform_name == "Darwin" else (
        identity.windows_architectures if platform_name == "Windows" else ())
    try:
        timestamp = datetime.fromisoformat(published_at.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        raise FeedError("Use an ISO-8601 publication timestamp with an offset.") from None
    parsed = urlsplit(artifact_url)
    try:
        info = artifact.lstat()
    except OSError:
        raise FeedError("The release artifact is missing.") from None
    if (version != identity.version or architecture not in allowed or timestamp.tzinfo is None or
            timestamp > datetime.now(timezone.utc) + timedelta(minutes=10) or
            not SHA.fullmatch(source_commit) or parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password or
            parsed.query or parsed.fragment or Path(parsed.path).name != artifact.name or artifact.is_symlink() or
            not artifact.is_file() or not 1 <= info.st_size <= MAX_ARTIFACT_BYTES or len(artifact.name) > 180 or
            not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]+", artifact.name)):
        raise FeedError("Release artifact metadata does not match the production identity.")
    return {
        "schema_version": 1, "product": identity.update_product, "platform": platform_name,
        "architecture": architecture, "version": version, "published_at": published_at,
        "source_commit": source_commit, "artifact_url": artifact_url,
        "artifact_bytes": artifact.stat().st_size, "artifact_sha256": artifact_digest(artifact),
        "filename": artifact.name,
    }


def sign_release(release: dict, key: Ed25519PrivateKey) -> bytes:
    raw = key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    envelope = {"schema_version": 1, "key_id": hashlib.sha256(raw).hexdigest()[:16],
                "release": release, "signature": base64.b64encode(key.sign(canonical(release))).decode("ascii")}
    return canonical(envelope) + b"\n"


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    create = sub.add_parser("create")
    for name in ("artifact", "private-key-file", "output"):
        create.add_argument("--" + name, required=True, type=Path)
    create.add_argument("--platform", required=True, choices=("Darwin", "Windows"))
    create.add_argument("--architecture", required=True, choices=("arm64", "x86_64"))
    create.add_argument("--version", required=True)
    create.add_argument("--published-at", required=True)
    create.add_argument("--source-commit", required=True)
    create.add_argument("--artifact-url", required=True)
    verify = sub.add_parser("verify")
    verify.add_argument("--manifest", required=True, type=Path)
    verify.add_argument("--public-key-file", required=True, type=Path)
    verify.add_argument("--platform", required=True, choices=("Darwin", "Windows"))
    verify.add_argument("--architecture", required=True, choices=("arm64", "x86_64"))
    args = parser.parse_args(argv)
    try:
        if args.command == "create":
            output = args.output.expanduser().absolute()
            if output.exists() or output.is_symlink() or not output.parent.is_dir():
                raise FeedError("The update manifest output must be a new file in an existing directory.")
            release = build_release(artifact=args.artifact, platform_name=args.platform,
                                    architecture=args.architecture, version=args.version,
                                    published_at=args.published_at, source_commit=args.source_commit,
                                    artifact_url=args.artifact_url)
            payload = sign_release(release, _private_key(args.private_key_file))
            descriptor = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
            with os.fdopen(descriptor, "wb") as target:
                target.write(payload)
            print(output)
        else:
            public = args.public_key_file.read_text(encoding="ascii")
            identity = load_identity()
            release = verify_envelope(args.manifest.read_bytes(), public,
                                      system=args.platform, architecture=args.architecture,
                                      expected_product=identity.update_product)
            print(json.dumps({"verified": True, "version": release.version,
                              "source_commit": release.source_commit}, sort_keys=True))
        return 0
    except (FeedError, UpdateError, OSError, UnicodeError, ValueError) as error:
        print(str(error), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
