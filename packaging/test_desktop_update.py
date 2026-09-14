"""Signed desktop update checks fail closed before presenting or saving bytes."""

import base64
from datetime import datetime, timedelta, timezone
import hashlib
import io
import json
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from scripts.desktop_update import (UpdateError, canonical, check_update, download_release,
                                    verify_envelope)


class Response(io.BytesIO):
    def __init__(self, data: bytes, content_length: int | None = None):
        super().__init__(data)
        self.headers = {} if content_length is None else {"Content-Length": str(content_length)}

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()


class Client:
    def __init__(self, *responses: Response):
        self.responses = list(responses)
        self.requests = []

    def open(self, request, timeout):
        self.requests.append((request, timeout))
        return self.responses.pop(0)


def signed(artifact: bytes = b"verified update bytes", **changes):
    private = Ed25519PrivateKey.generate()
    raw = private.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    public = base64.b64encode(raw).decode()
    release = {
        "schema_version": 1,
        "product": "AisleSignalsPilot",
        "platform": "Darwin",
        "architecture": "arm64",
        "version": "0.2.0",
        "published_at": (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat().replace("+00:00", "Z"),
        "source_commit": "a" * 40,
        "artifact_url": "https://updates.example.invalid/AisleSignalsPilot-0.2.0.dmg",
        "artifact_bytes": len(artifact),
        "artifact_sha256": hashlib.sha256(artifact).hexdigest(),
        "filename": "AisleSignalsPilot-0.2.0.dmg",
    }
    release.update(changes)
    envelope = {
        "schema_version": 1,
        "key_id": hashlib.sha256(raw).hexdigest()[:16],
        "release": release,
        "signature": base64.b64encode(private.sign(canonical(release))).decode(),
    }
    return json.dumps(envelope).encode(), public, artifact


def test_check_offers_only_newer_correctly_signed_target():
    envelope, public, _ = signed()
    status, release = check_update("https://updates.example.invalid/stable.json", public, "0.1.0",
                                   client=Client(Response(envelope)), target=("Darwin", "arm64"))
    assert status == "available"
    assert release.version == "0.2.0"


@pytest.mark.parametrize("mutation", ["signature", "release", "key", "platform"])
def test_tampered_untrusted_or_wrong_target_manifest_is_rejected(mutation):
    envelope, public, _ = signed()
    document = json.loads(envelope)
    if mutation == "signature":
        document["signature"] = base64.b64encode(b"0" * 64).decode()
    elif mutation == "release":
        document["release"]["artifact_sha256"] = "0" * 64
    elif mutation == "key":
        other = Ed25519PrivateKey.generate().public_key().public_bytes(
            serialization.Encoding.Raw, serialization.PublicFormat.Raw)
        public = base64.b64encode(other).decode()
    else:
        # Re-signing proves the target check happens after authenticity.
        envelope, public, _ = signed(platform="Windows")
        document = json.loads(envelope)
    with pytest.raises(UpdateError):
        verify_envelope(json.dumps(document).encode(), public, system="Darwin", architecture="arm64")


def test_download_streams_and_verifies_before_atomic_publish(tmp_path):
    envelope, public, artifact = signed()
    release = verify_envelope(envelope, public, system="Darwin", architecture="arm64")
    result = download_release(release, tmp_path / "updates",
                              client=Client(Response(artifact, len(artifact))))
    assert result.read_bytes() == artifact
    assert result.stat().st_mode & 0o777 == 0o600
    assert not list(result.parent.glob(".aislesignals-update-*"))


@pytest.mark.parametrize("payload", [b"wrong", b"verified update bytes plus trailing bytes"])
def test_download_removes_partial_file_on_size_or_digest_failure(tmp_path, payload):
    envelope, public, _ = signed()
    release = verify_envelope(envelope, public, system="Darwin", architecture="arm64")
    with pytest.raises(UpdateError, match="size|exceeds|match"):
        download_release(release, tmp_path / "updates", client=Client(Response(payload)))
    assert list((tmp_path / "updates").iterdir()) == []


def test_redirects_http_credentials_and_existing_different_file_fail_closed(tmp_path):
    envelope, public, artifact = signed(artifact_url="https://updates.example.invalid/AisleSignalsPilot-0.2.0.dmg")
    release = verify_envelope(envelope, public, system="Darwin", architecture="arm64")
    destination = tmp_path / "updates"
    destination.mkdir()
    (destination / release.filename).write_bytes(b"other")
    with pytest.raises(UpdateError, match="different file"):
        download_release(release, destination, client=Client(Response(artifact)))
    for url in ("http://updates.example.invalid/stable.json", "https://user@updates.example.invalid/stable.json"):
        with pytest.raises(UpdateError, match="HTTPS"):
            check_update(url, public, "0.1.0", client=Client(Response(envelope)), target=("Darwin", "arm64"))
