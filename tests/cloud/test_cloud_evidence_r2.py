from io import BytesIO

import pytest
from botocore.exceptions import ClientError, ConnectTimeoutError
from botocore.stub import Stubber

from services.cloud.evidence_r2 import R2EvidenceBlobStore
from services.cloud.evidence_store import MAX_ENVELOPE_BYTES, EvidenceObjectMissing, EvidenceStoreError


KEY = "evidence/00000000-0000-0000-0000-000000000001.bin"


def client_error(code, status):
    return ClientError({"Error": {"Code": code, "Message": "sensitive provider detail"},
                        "ResponseMetadata": {"HTTPStatusCode": status}}, "Synthetic")


class Body(BytesIO):
    closed_by_store = False

    def close(self):
        self.closed_by_store = True
        super().close()


class FakeS3:
    def __init__(self):
        self.calls = []
        self.objects = {}
        self.fail = {}

    def _failure(self, operation):
        if error := self.fail.get(operation):
            raise error

    def put_object(self, **kwargs):
        self._failure("put")
        self.calls.append(("put", kwargs))
        object_key = (kwargs["Bucket"], kwargs["Key"])
        if object_key in self.objects:
            raise client_error("PreconditionFailed", 412)
        self.objects[object_key] = kwargs["Body"]
        return {"ResponseMetadata": {"HTTPStatusCode": 200}}

    def get_object(self, **kwargs):
        self._failure("get")
        self.calls.append(("get", kwargs))
        try:
            value = self.objects[(kwargs["Bucket"], kwargs["Key"])]
        except KeyError:
            raise client_error("NoSuchKey", 404)
        body = Body(value)
        self.last_body = body
        return {"ContentLength": len(value), "Body": body}

    def delete_object(self, **kwargs):
        self.calls.append(("delete", kwargs))
        self._failure("delete")
        self.objects.pop((kwargs["Bucket"], kwargs["Key"]), None)
        return {"ResponseMetadata": {"HTTPStatusCode": 204}}

    def head_bucket(self, **kwargs):
        self._failure("probe")
        self.calls.append(("probe", kwargs))
        return {"ResponseMetadata": {"HTTPStatusCode": 200}}


def store(client):
    return R2EvidenceBlobStore(account_id="a" * 32, jurisdiction="eu", bucket="private-evidence",
        access_key_id="A" * 32, secret_access_key="s" * 64, prefix="staging", client=client)


def test_r2_is_private_create_only_bounded_and_idempotent():
    fake, content = FakeS3(), b"encrypted-envelope"
    subject = store(fake)
    assert subject.endpoint == f"https://{'a' * 32}.eu.r2.cloudflarestorage.com"
    assert subject.put_if_absent(KEY, content) is True
    request = fake.calls[0][1]
    assert request == {"Bucket": "private-evidence", "Key": "staging/" + KEY, "Body": content,
                       "ContentLength": len(content), "ContentType": "application/octet-stream",
                       "CacheControl": "no-store", "IfNoneMatch": "*"}
    assert subject.put_if_absent(KEY, content) is False
    assert subject.get(KEY) == content and fake.last_body.closed_by_store is True
    subject.delete(KEY)
    subject.delete(KEY)
    with pytest.raises(EvidenceObjectMissing):
        subject.get(KEY)
    with pytest.raises(EvidenceStoreError):
        subject.put_if_absent(KEY, b"x" * (MAX_ENVELOPE_BYTES + 1))
    with pytest.raises(EvidenceStoreError):
        subject.get("../escape")


def test_real_sdk_model_accepts_conditional_put_and_uses_bounded_client_config():
    subject = R2EvidenceBlobStore(account_id="a" * 32, jurisdiction="eu", bucket="private-evidence",
        access_key_id="A" * 32, secret_access_key="s" * 64)
    config = subject._client.meta.config
    assert config.connect_timeout == 2 and config.read_timeout == 6 and config.max_pool_connections == 4
    assert config.retries["mode"] == "standard" and config.signature_version == "s3v4"
    content = b"encrypted"
    expected = {"Bucket": "private-evidence", "Key": KEY, "Body": content, "ContentLength": len(content),
                "ContentType": "application/octet-stream", "CacheControl": "no-store", "IfNoneMatch": "*"}
    with Stubber(subject._client) as stubber:
        stubber.add_response("put_object", {"ETag": '"synthetic"'}, expected)
        assert subject.put_if_absent(KEY, content) is True


@pytest.mark.parametrize("operation", ["put", "get", "delete"])
def test_r2_failures_are_opaque_and_never_report_success(operation):
    fake, subject = FakeS3(), None
    fake.objects[("private-evidence", "staging/" + KEY)] = b"encrypted"
    fake.fail[operation] = client_error("AccessDenied", 403)
    subject = store(fake)
    with pytest.raises(EvidenceStoreError) as caught:
        {"put": lambda: subject.put_if_absent(KEY, b"encrypted"),
         "get": lambda: subject.get(KEY), "delete": lambda: subject.delete(KEY)}[operation]()
    assert "sensitive" not in str(caught.value) and "AccessDenied" not in str(caught.value)


def test_probe_validates_create_only_put_bounded_get_delete_and_absence():
    fake, subject = FakeS3(), None
    subject = store(fake)
    assert subject.probe() is True
    assert fake.objects == {}
    assert [call[0] for call in fake.calls] == ["put", "put", "get", "delete", "get"]


def test_probe_fails_closed_for_read_only_and_delete_denied_credentials():
    read_only = FakeS3()
    read_only.fail["put"] = client_error("AccessDenied", 403)
    assert store(read_only).probe() is False and read_only.objects == {}

    delete_denied = FakeS3()
    delete_denied.fail["delete"] = client_error("AccessDenied", 403)
    assert store(delete_denied).probe() is False
    assert len(delete_denied.objects) == 1
    assert [call[0] for call in delete_denied.calls].count("delete") == 2


def test_probe_does_not_treat_bucket_or_unknown_404_as_object_absence():
    for code in ("NoSuchBucket", "UnknownProviderFailure", "404"):
        fake = FakeS3()
        fake.fail["get"] = client_error(code, 404)
        assert store(fake).probe() is False
        assert fake.objects == {}


def test_r2_transport_failure_probe_and_malformed_stream_fail_closed():
    fake, subject = FakeS3(), None
    fake.fail["put"] = ConnectTimeoutError(endpoint_url="https://private.invalid")
    subject = store(fake)
    assert subject.probe() is False
    fake.fail.clear()
    fake.get_object = lambda **kwargs: {"ContentLength": MAX_ENVELOPE_BYTES + 1, "Body": Body(b"x")}
    with pytest.raises(EvidenceStoreError):
        subject.get(KEY)
