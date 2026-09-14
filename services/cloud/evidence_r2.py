"""Private Cloudflare R2 implementation of the evidence blob boundary."""

from boto3 import client as boto_client
from botocore.config import Config
from botocore.exceptions import ClientError
from secrets import token_bytes
from uuid import uuid4

from .evidence_store import MAX_ENVELOPE_BYTES, EvidenceObjectMissing, EvidenceStoreError, _key


class R2EvidenceBlobStore:
    """Bounded S3-compatible access; callers never receive object URLs."""

    def __init__(self, *, account_id: str, jurisdiction: str, bucket: str,
                 access_key_id: str, secret_access_key: str, prefix: str = "", client=None):
        self.bucket = bucket
        self.prefix = prefix + "/" if prefix else ""
        self.endpoint = f"https://{account_id}.{jurisdiction}.r2.cloudflarestorage.com"
        self._client = client or boto_client(
            "s3", endpoint_url=self.endpoint, region_name="auto",
            aws_access_key_id=access_key_id, aws_secret_access_key=secret_access_key,
            config=Config(signature_version="s3v4", connect_timeout=2, read_timeout=6,
                          max_pool_connections=4, retries={"mode": "standard", "max_attempts": 3},
                          user_agent_extra="AisleSignals-evidence/1",
                          s3={"addressing_style": "path", "payload_signing_enabled": True}),
        )

    def _object(self, key: str) -> str:
        return self.prefix + _key(key)

    @staticmethod
    def _precondition(error: ClientError) -> bool:
        response = error.response or {}
        return (response.get("ResponseMetadata", {}).get("HTTPStatusCode") == 412
                or response.get("Error", {}).get("Code") in {"PreconditionFailed", "412"})

    @staticmethod
    def _missing(error: ClientError) -> bool:
        response = error.response or {}
        return response.get("Error", {}).get("Code") == "NoSuchKey"

    def put_if_absent(self, key: str, value: bytes) -> bool:
        if not isinstance(value, bytes) or len(value) > MAX_ENVELOPE_BYTES:
            raise EvidenceStoreError("Invalid evidence object.")
        object_key = self._object(key)
        try:
            self._client.put_object(
                Bucket=self.bucket, Key=object_key, Body=value, ContentLength=len(value),
                ContentType="application/octet-stream", CacheControl="no-store", IfNoneMatch="*",
            )
            return True
        except ClientError as error:
            if self._precondition(error):
                return False
            raise EvidenceStoreError("Evidence object storage is unavailable.") from None
        except Exception:
            raise EvidenceStoreError("Evidence object storage is unavailable.") from None

    def get(self, key: str) -> bytes:
        body, object_key = None, self._object(key)
        try:
            response = self._client.get_object(Bucket=self.bucket, Key=object_key)
            length, body = response.get("ContentLength"), response.get("Body")
            if type(length) is not int or not 1 <= length <= MAX_ENVELOPE_BYTES or body is None:
                raise EvidenceStoreError("Evidence object is unavailable.")
            content = body.read(MAX_ENVELOPE_BYTES + 1)
            if not isinstance(content, bytes) or len(content) != length:
                raise EvidenceStoreError("Evidence object is unavailable.")
            return content
        except EvidenceStoreError:
            raise
        except ClientError as error:
            if self._missing(error):
                raise EvidenceObjectMissing("Evidence object is unavailable.") from None
            raise EvidenceStoreError("Evidence object is unavailable.") from None
        except Exception:
            raise EvidenceStoreError("Evidence object is unavailable.") from None
        finally:
            if body is not None:
                try:
                    body.close()
                except Exception:
                    pass

    def delete(self, key: str) -> None:
        object_key = self._object(key)
        try:
            self._client.delete_object(Bucket=self.bucket, Key=object_key)
        except Exception:
            raise EvidenceStoreError("Evidence object deletion is unavailable.") from None

    def probe(self) -> bool:
        key = f"evidence/{uuid4()}.bin"
        payload = token_bytes(64)
        created = False
        try:
            created = self.put_if_absent(key, payload)
            if not created or self.put_if_absent(key, payload):
                return False
            if self.get(key) != payload:
                return False
            self.delete(key)
            created = False
            try:
                self.get(key)
            except EvidenceObjectMissing:
                return True
            return False
        except Exception:
            return False
        finally:
            if created:
                try:
                    self.delete(key)
                except EvidenceStoreError:
                    pass
