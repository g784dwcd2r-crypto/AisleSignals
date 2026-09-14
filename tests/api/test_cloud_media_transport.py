"""Bounded media HTTPS contract without external network access."""

import hashlib
import json
from uuid import uuid4

import pytest

import services.api.cloud_transport as module
from services.api.cloud_transport import CloudTransport

TOKEN = "t" * 64


class Response:
    status = 200

    def __init__(self, value):
        self.value = value

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def read(self, _limit):
        return json.dumps(self.value).encode()


class Opener:
    def __init__(self, response, calls):
        self.response, self.calls = response, calls

    def open(self, request, timeout):
        self.calls.append((request, timeout))
        return Response(self.response)


def test_manifest_and_bounded_content_use_frozen_routes_headers_and_bytes(monkeypatch):
    evidence_id, source_id, calls = str(uuid4()), str(uuid4()), []
    # UUIDv5 is required for a source event.
    source_id = "49fefed8-867e-58bd-b3a5-b5447bf72be0"
    responses = iter([
        {"evidence_id": evidence_id, "upload_required": True, "state": "PENDING"},
        {"evidence_id": evidence_id, "state": "READY"},
    ])
    monkeypatch.setattr(module, "build_opener", lambda *_: Opener(next(responses), calls))
    transport = CloudTransport("https://synthetic.example.test")
    content = b"synthetic-jpeg"
    digest = hashlib.sha256(content).hexdigest()
    manifest = {"schema_version": 1, "kind": "OVERVIEW", "content_type": "image/jpeg",
                "byte_count": len(content), "sha256": digest}
    assert transport.evidence_manifest(TOKEN, source_id, manifest)["state"] == "PENDING"
    assert transport.evidence_content(TOKEN, evidence_id, content,
        content_type="image/jpeg", sha256=digest)["state"] == "READY"
    manifest_request, content_request = calls[0][0], calls[1][0]
    assert manifest_request.full_url.endswith(f"/device-api/sync/v1/observations/{source_id}/evidence")
    assert json.loads(manifest_request.data) == manifest
    assert content_request.full_url.endswith(f"/device-api/sync/v1/evidence/{evidence_id}")
    assert content_request.method == "PUT" and content_request.data == content
    assert content_request.headers["Content-type"] == "image/jpeg"
    assert content_request.headers["X-content-sha256"] == digest


def test_content_refuses_size_digest_and_type_before_opening_socket(monkeypatch):
    opened = []
    monkeypatch.setattr(module, "build_opener", lambda *_: opened.append(True))
    transport = CloudTransport("https://synthetic.example.test")
    evidence_id = str(uuid4())
    with pytest.raises(ValueError, match="INVALID_MEDIA_CONTENT"):
        transport.evidence_content(TOKEN, evidence_id, b"x" * (350 * 1024 + 1),
            content_type="image/jpeg", sha256=hashlib.sha256(b"x").hexdigest())
    with pytest.raises(ValueError, match="INVALID_MEDIA_CONTENT"):
        transport.evidence_content(TOKEN, evidence_id, b"jpeg", content_type="image/jpeg", sha256="0" * 64)
    assert opened == []
