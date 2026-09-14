"""Verify and download an AisleSignals release without executing it."""

from __future__ import annotations

import argparse
import base64
import binascii
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import ssl
import sys
import tempfile
import urllib.error
import urllib.request
from urllib.parse import urlsplit

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey


MAX_MANIFEST_BYTES = 64 * 1024
MAX_ARTIFACT_BYTES = 1024 * 1024 * 1024
PRODUCT = "AisleSignalsPilot"


class UpdateError(RuntimeError):
    pass


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise urllib.error.HTTPError(req.full_url, code, "Update redirects are not allowed", headers, fp)


def https_url(value: str, *, label: str) -> str:
    try:
        parsed = urlsplit(value)
        if (parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password
                or parsed.fragment or parsed.port == 0):
            raise ValueError
    except (TypeError, ValueError, AttributeError):
        raise UpdateError(f"{label} must be a direct HTTPS URL without credentials or a fragment.") from None
    return value


def canonical(value: dict) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def decode_public_key(text: str) -> tuple[Ed25519PublicKey, str]:
    try:
        raw = base64.b64decode(text.strip(), validate=True)
        if len(raw) != 32:
            raise ValueError
        key = Ed25519PublicKey.from_public_bytes(raw)
    except (ValueError, binascii.Error):
        raise UpdateError("The trusted update public key is invalid.") from None
    return key, hashlib.sha256(raw).hexdigest()[:16]


def current_target() -> tuple[str, str]:
    system = platform.system()
    if system not in {"Darwin", "Windows"}:
        raise UpdateError("Desktop updates are available only for macOS and Windows packages.")
    machine = platform.machine().lower()
    architecture = "arm64" if machine in {"arm64", "aarch64"} else "x86_64" if machine in {"amd64", "x86_64"} else ""
    if not architecture:
        raise UpdateError("This desktop architecture is not supported by the update channel.")
    return system, architecture


def version_tuple(value: str) -> tuple[int, ...]:
    if not isinstance(value, str) or not re.fullmatch(r"(?:0|[1-9][0-9]*)(?:\.(?:0|[1-9][0-9]*)){2,3}", value):
        raise UpdateError("Release versions must contain three or four numeric components.")
    return tuple(int(part) for part in value.split("."))


@dataclass(frozen=True)
class VerifiedRelease:
    version: str
    artifact_url: str
    artifact_bytes: int
    artifact_sha256: str
    filename: str
    published_at: str
    source_commit: str


def verify_envelope(data: bytes, public_key_text: str, *, system: str, architecture: str) -> VerifiedRelease:
    if len(data) > MAX_MANIFEST_BYTES:
        raise UpdateError("The update manifest exceeds the allowed size.")
    try:
        envelope = json.loads(data)
        if not isinstance(envelope, dict) or set(envelope) != {"schema_version", "key_id", "release", "signature"}:
            raise ValueError
        if envelope["schema_version"] != 1 or not isinstance(envelope["release"], dict):
            raise ValueError
        release = envelope["release"]
        required = {"schema_version", "product", "platform", "architecture", "version", "published_at",
                    "source_commit", "artifact_url", "artifact_bytes", "artifact_sha256", "filename"}
        if set(release) != required or release["schema_version"] != 1 or release["product"] != PRODUCT:
            raise ValueError
        key, key_id = decode_public_key(public_key_text)
        if envelope["key_id"] != key_id:
            raise ValueError
        signature = base64.b64decode(envelope["signature"], validate=True)
        key.verify(signature, canonical(release))
        version_tuple(release["version"])
        published = datetime.fromisoformat(release["published_at"].replace("Z", "+00:00"))
        if published.tzinfo is None or published > datetime.now(timezone.utc) + timedelta(minutes=10):
            raise ValueError
        if release["platform"] != system or release["architecture"] != architecture:
            raise UpdateError("The signed update targets a different operating system or architecture.")
        artifact_url = https_url(release["artifact_url"], label="The signed artifact URL")
        filename = release["filename"]
        url_name = Path(urlsplit(artifact_url).path).name
        if (not isinstance(filename, str) or filename != url_name or len(filename) > 180
                or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]+", filename)):
            raise ValueError
        byte_count = release["artifact_bytes"]
        checksum = release["artifact_sha256"]
        if type(byte_count) is not int or not 1 <= byte_count <= MAX_ARTIFACT_BYTES:
            raise ValueError
        if not isinstance(checksum, str) or not re.fullmatch(r"[0-9a-f]{64}", checksum):
            raise ValueError
        if not isinstance(release["source_commit"], str) or not re.fullmatch(r"[0-9a-f]{40}", release["source_commit"]):
            raise ValueError
    except UpdateError:
        raise
    except (ValueError, TypeError, KeyError, AttributeError, OverflowError, binascii.Error, InvalidSignature,
            UnicodeDecodeError):
        raise UpdateError("The update manifest is invalid or its signature could not be verified.") from None
    return VerifiedRelease(release["version"], artifact_url, byte_count, checksum, filename,
                           release["published_at"], release["source_commit"])


def opener():
    context = ssl.create_default_context()
    return urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect(),
                                       urllib.request.HTTPSHandler(context=context))


def bounded_fetch(url: str, *, limit: int, timeout: float = 15, client=None) -> bytes:
    url = https_url(url, label="The update manifest URL")
    request = urllib.request.Request(url, headers={"Accept": "application/json", "User-Agent": "AisleSignalsPilot-Update/1"})
    client = client or opener()
    try:
        with client.open(request, timeout=timeout) as response:
            content_length = response.headers.get("Content-Length")
            if content_length is not None and int(content_length) > limit:
                raise UpdateError("The update manifest exceeds the allowed size.")
            data = response.read(limit + 1)
    except UpdateError:
        raise
    except (OSError, ValueError, urllib.error.URLError):
        raise UpdateError("The signed update manifest could not be downloaded securely.") from None
    if len(data) > limit:
        raise UpdateError("The update manifest exceeds the allowed size.")
    return data


def check_update(manifest_url: str, public_key_text: str, current_version: str, *, client=None,
                 target: tuple[str, str] | None = None) -> tuple[str, VerifiedRelease]:
    version_tuple(current_version)
    system, architecture = target or current_target()
    release = verify_envelope(bounded_fetch(manifest_url, limit=MAX_MANIFEST_BYTES, client=client),
                              public_key_text, system=system, architecture=architecture)
    status = "available" if version_tuple(release.version) > version_tuple(current_version) else "current"
    return status, release


def download_release(release: VerifiedRelease, destination: Path, *, client=None, timeout: float = 60) -> Path:
    destination = Path(os.path.abspath(destination.expanduser()))
    if destination.exists() and (destination.is_symlink() or not destination.is_dir()):
        raise UpdateError("The update download directory is unsafe.")
    destination.mkdir(parents=True, exist_ok=True, mode=0o700)
    if os.name != "nt":
        destination.chmod(0o700)
    final = destination / release.filename
    if final.exists():
        if final.is_symlink() or not final.is_file():
            raise UpdateError("The update destination is unsafe.")
        if final.stat().st_size == release.artifact_bytes and file_digest(final) == release.artifact_sha256:
            return final
        raise UpdateError("A different file already exists at the update destination.")
    client = client or opener()
    request = urllib.request.Request(release.artifact_url,
                                     headers={"Accept": "application/octet-stream", "User-Agent": "AisleSignalsPilot-Update/1"})
    temporary = None
    try:
        descriptor, name = tempfile.mkstemp(prefix=".aislesignals-update-", dir=destination)
        temporary = Path(name)
        os.chmod(temporary, 0o600)
        count = 0
        checksum = hashlib.sha256()
        with os.fdopen(descriptor, "wb") as output, client.open(request, timeout=timeout) as response:
            content_length = response.headers.get("Content-Length")
            if content_length is not None and int(content_length) != release.artifact_bytes:
                raise UpdateError("The update size differs from the signed manifest.")
            while True:
                block = response.read(min(1024 * 1024, release.artifact_bytes - count + 1))
                if not block:
                    break
                count += len(block)
                if count > release.artifact_bytes:
                    raise UpdateError("The update exceeds the size in the signed manifest.")
                checksum.update(block)
                output.write(block)
            output.flush()
            os.fsync(output.fileno())
        if count != release.artifact_bytes or checksum.hexdigest() != release.artifact_sha256:
            raise UpdateError("The update bytes do not match the signed manifest.")
        os.replace(temporary, final)
        temporary = None
        return final
    except UpdateError:
        raise
    except (OSError, ValueError, urllib.error.URLError):
        raise UpdateError("The update could not be downloaded securely.") from None
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def file_digest(path: Path) -> str:
    checksum = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            checksum.update(block)
    return checksum.hexdigest()


def read_public_key(path: Path) -> str:
    path = Path(os.path.abspath(path.expanduser()))
    if not path.is_file() or path.is_symlink() or path.stat().st_size > 256:
        raise UpdateError("The trusted update public key file is missing or unsafe.")
    return path.read_text(encoding="ascii")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Check and download cryptographically verified desktop updates.")
    parser.add_argument("action", choices=("check", "download"))
    parser.add_argument("--manifest-url", required=True)
    parser.add_argument("--public-key-file", required=True, type=Path)
    parser.add_argument("--current-version", required=True)
    parser.add_argument("--download-dir", type=Path)
    args = parser.parse_args(argv)
    try:
        status, release = check_update(args.manifest_url, read_public_key(args.public_key_file), args.current_version)
        result = {"status": status, "version": release.version, "published_at": release.published_at,
                  "source_commit": release.source_commit}
        if args.action == "download":
            if status != "available":
                raise UpdateError("No newer verified update is available.")
            if args.download_dir is None:
                raise UpdateError("Choose --download-dir for the verified update.")
            result["downloaded_to"] = str(download_release(release, args.download_dir))
        print(json.dumps(result, sort_keys=True))
        return 0
    except UpdateError as error:
        print(str(error), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
