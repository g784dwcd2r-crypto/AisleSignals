"""Bounded explicit cloud device transport; no worker or automatic exports.

The caller performs I/O outside SQLite/auth transactions and revalidates local
authority afterwards. This module never stores credentials, starts monitoring,
uploads footage or creates records without an explicit caller operation.
"""

from collections.abc import Mapping
from datetime import datetime, timezone
from http.client import HTTPException
import hashlib
import json
import re
from urllib.error import HTTPError, URLError
from urllib.request import HTTPSHandler, ProxyHandler, Request, build_opener
from uuid import UUID

from scripts.cloud_companion import NoRedirect, server_url
from .cloud_observation import MAX_RETENTION, validate_payload


MAX_RESPONSE = 65536
VERSION = "pilot-cloud-sync-1"
ERROR_CODES = {
    "DEVICE_UNAUTHORIZED", "INVALID_ENROLMENT", "ENROLMENT_EXPIRED",
    "OLD_HEARTBEAT", "HEARTBEAT_CONFLICT", "EVENT_CONFLICT", "ALERT_LIMIT",
    "INVALID_EVENT_TIME", "RATE_LIMITED", "SERVICE_UNAVAILABLE", "SYNC_LIMIT",
}
WITHDRAWAL_REASONS = frozenset({"LOCAL_DELETED", "LOCAL_EXPIRED", "LOCAL_EXPORT_REMOVED"})


class CloudTransportError(Exception):
    """Only fixed categories leave the transport, never remote messages/secrets."""

    def __init__(self, code, *, retry_after=None):
        super().__init__(code)
        self.code = code
        self.retry_after = retry_after


def canonical_id(value):
    if not isinstance(value, str):
        raise ValueError("INVALID_ID")
    try:
        if str(UUID(value)) != value or UUID(value).int == 0:
            raise ValueError
    except ValueError:
        raise ValueError("INVALID_ID") from None
    return value


def token_value(value, *, enrolment=False):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_-]{43}" if enrolment else r"[A-Za-z0-9_-]{64}", value):
        raise ValueError("INVALID_CREDENTIAL")
    return value


def safe_name(value, limit):
    if (not isinstance(value, str) or not value.strip() or len(value) > limit or
            any(ord(c) < 32 or ord(c) == 127 for c in value)):
        raise ValueError("INVALID_TEXT")
    return value


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate key")
        result[key] = value
    return result


def _decode(raw):
    if len(raw) > MAX_RESPONSE:
        raise CloudTransportError("INVALID_RESPONSE")
    try:
        result = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique_object,
                            parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
    except (ValueError, UnicodeError, RecursionError):
        raise CloudTransportError("INVALID_RESPONSE") from None
    if not isinstance(result, dict):
        raise CloudTransportError("INVALID_RESPONSE")
    return result


def _retry_after(headers):
    value = headers.get("Retry-After", "") if headers else ""
    # Date-valued or malformed headers are ignored; the worker owns its clock.
    if not re.fullmatch(r"[0-9]{1,6}", value):
        return None
    return max(5, min(3600, int(value)))


def _source_id(value):
    result = canonical_id(value)
    if UUID(result).version != 5:
        raise ValueError("INVALID_SOURCE_ID")
    return result


def _deadline(value, occurred_at):
    """Serialize the stored deadline, never create or extend one at send time."""
    try:
        if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
            raise ValueError
        result = value.astimezone(timezone.utc)
        start = datetime.fromisoformat(occurred_at.replace("Z", "+00:00"))
        if not 0 < (result - start).total_seconds() <= MAX_RETENTION.total_seconds():
            raise ValueError
        return result.isoformat().replace("+00:00", "Z")
    except (ValueError, OverflowError):
        raise ValueError("INVALID_DEADLINE") from None


class CloudTransport:
    def __init__(self, origin, *, allow_local_test=False):
        if type(allow_local_test) is not bool:
            raise ValueError("INVALID_TEST_SETTING")
        self.origin = server_url(origin, allow_local=allow_local_test)

    def _call(self, route, *, method, payload=None, credential=None, expected=200):
        # These are internal fixed routes, never client-supplied paths or URLs.
        fixed = (route, method) in {("identity", "GET"), ("enrol", "POST"), ("heartbeat", "POST"),
                                   ("sync/v1/observations", "POST"), ("sync/v1/withdrawals", "POST")}
        media_manifest = method == "POST" and re.fullmatch(r"sync/v1/observations/[0-9a-f-]{36}/evidence", route)
        if not fixed and not media_manifest:
            raise ValueError("INVALID_OPERATION")
        headers = {"Accept": "application/json", "User-Agent": "AisleSignals-sync/1"}
        if credential is not None:
            headers["Authorization"] = "Bearer " + token_value(credential)
        data = None
        if payload is not None:
            data = json.dumps(payload, sort_keys=True, separators=(",", ":"),
                              allow_nan=False).encode("utf-8")
            if len(data) > 4096:
                raise ValueError("REQUEST_TOO_LARGE")
            headers["Content-Type"] = "application/json"
        request = Request(self.origin + "/device-api/" + route, data=data,
                          headers=headers, method=method)
        # No ambient proxies, cookies or redirect credential forwarding.
        opener = build_opener(ProxyHandler({}), HTTPSHandler(), NoRedirect())
        try:
            with opener.open(request, timeout=8) as response:
                expected_codes = (expected,) if type(expected) is int else expected
                if response.status not in expected_codes:
                    raise CloudTransportError("INVALID_RESPONSE")
                return _decode(response.read(MAX_RESPONSE + 1))
        except HTTPError as error:
            try:
                retry = _retry_after(error.headers) if error.code == 429 else None
                if error.code in {401, 403}:
                    raise CloudTransportError("ACCESS_REVOKED") from None
                if 300 <= error.code < 400:
                    raise CloudTransportError("REDIRECT_REFUSED") from None
                if error.code >= 500:
                    raise CloudTransportError("SERVICE_UNAVAILABLE") from None
                category = "RATE_LIMITED" if error.code == 429 else "VALIDATION_FAILED" if error.code == 422 else "REQUEST_REFUSED"
                try:
                    value = _decode(error.read(MAX_RESPONSE + 1))
                    detail = value.get("error")
                    code = detail.get("code") if isinstance(detail, dict) else None
                    if isinstance(code, str) and code in ERROR_CODES:
                        category = code
                except (CloudTransportError, OSError, HTTPException):
                    pass
                raise CloudTransportError(category, retry_after=retry) from None
            finally:
                error.close()
        except (URLError, TimeoutError, OSError, HTTPException):
            raise CloudTransportError("NETWORK_UNAVAILABLE") from None

    def identity(self, credential, *, expected_device_id):
        expected_device_id = canonical_id(expected_device_id)
        result = self._call("identity", method="GET", credential=credential)
        fields = {"device_id", "organisation_id", "organisation_name", "pharmacy_id",
                  "pharmacy_name", "name", "platform", "app_version"}
        try:
            if set(result) != fields:
                raise ValueError
            for name in ("device_id", "organisation_id", "pharmacy_id"):
                canonical_id(result[name])
            if result["device_id"] != expected_device_id:
                raise CloudTransportError("IDENTITY_MISMATCH")
            for name, limit in (("organisation_name", 120), ("pharmacy_name", 120),
                                ("name", 100), ("app_version", 64)):
                safe_name(result[name], limit)
            if result["platform"] not in {"MACOS", "WINDOWS", "OTHER"}:
                raise ValueError
        except (ValueError, TypeError):
            raise CloudTransportError("INVALID_RESPONSE") from None
        return result

    def enrol(self, code, *, name, platform):
        token_value(code, enrolment=True)
        safe_name(name, 100)
        if not isinstance(platform, str) or platform not in {"MACOS", "WINDOWS", "OTHER"}:
            raise ValueError("INVALID_PLATFORM")
        # A lost response is uncertain enrolment. There is deliberately no retry.
        result = self._call("enrol", method="POST", expected=201,
                            payload={"token": code, "name": name, "platform": platform, "app_version": VERSION})
        try:
            if set(result) != {"device_id", "device_token", "heartbeat_interval_seconds"}:
                raise ValueError
            canonical_id(result["device_id"])
            token_value(result["device_token"])
            if type(result["heartbeat_interval_seconds"]) is not int or result["heartbeat_interval_seconds"] != 30:
                raise ValueError
        except ValueError:
            raise CloudTransportError("INVALID_RESPONSE") from None
        return result

    def heartbeat(self, credential, *, sequence):
        if type(sequence) is not int or not 0 <= sequence <= 9007199254740991:
            raise ValueError("INVALID_SEQUENCE")
        result = self._call("heartbeat", method="POST", credential=credential,
                            payload={"sequence": sequence, "monitoring_status": "UNKNOWN",
                                     "camera_count": 0, "app_version": VERSION})
        try:
            if set(result) != {"ok", "server_time"} or result["ok"] is not True:
                raise ValueError
            stamp = result["server_time"]
            if not isinstance(stamp, str) or len(stamp) > 40:
                raise ValueError
            parsed = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
            if parsed.tzinfo is None or parsed.utcoffset() is None:
                raise ValueError
        except ValueError:
            raise CloudTransportError("INVALID_RESPONSE") from None
        return result

    def observation(self, credential, payload, *, expires_at, expected_source_event_id,
                    expected_receipt_id=None):
        """Send one validated immutable observation; the caller owns its lease.

        First receipts do not echo the source UUID. Bind the request to the
        caller's known source, and verify a known receipt ID on later retries.
        An unavailable/malformed response remains an uncertain send.
        """
        expected_source_event_id = _source_id(expected_source_event_id)
        if expected_receipt_id is not None:
            canonical_id(expected_receipt_id)
        body = dict(validate_payload(payload))
        if body["source_event_id"] != expected_source_event_id:
            raise ValueError("SOURCE_ID_MISMATCH")
        body["expires_at"] = _deadline(expires_at, body["occurred_at"])
        result = self._call("sync/v1/observations", method="POST", credential=credential,
                            payload=body, expected=201)
        try:
            if set(result) != {"id", "received", "source_state"} or result["received"] is not True:
                raise ValueError
            canonical_id(result["id"])
            if type(result["source_state"]) is not str or result["source_state"] not in {"AVAILABLE", "EXPIRED", "WITHDRAWN"}:
                raise ValueError
            if expected_receipt_id is not None and result["id"] != expected_receipt_id:
                raise CloudTransportError("RECEIPT_MISMATCH")
        except (ValueError, TypeError):
            raise CloudTransportError("INVALID_RESPONSE") from None
        return result

    def withdrawal(self, credential, payload, *, expected_source_event_id):
        """Send one withdrawal and require its exact source UUID acknowledgement."""
        expected_source_event_id = _source_id(expected_source_event_id)
        if not isinstance(payload, Mapping) or set(payload) != {"source_event_id", "reason"}:
            raise ValueError("INVALID_WITHDRAWAL")
        source = _source_id(payload["source_event_id"])
        reason = payload["reason"]
        if type(reason) is not str or reason not in WITHDRAWAL_REASONS:
            raise ValueError("INVALID_WITHDRAWAL")
        if source != expected_source_event_id:
            raise ValueError("SOURCE_ID_MISMATCH")
        result = self._call("sync/v1/withdrawals", method="POST", credential=credential,
                            payload={"source_event_id": source, "reason": reason})
        try:
            if set(result) != {"source_event_id", "withdrawn"} or result["withdrawn"] is not True:
                raise ValueError
            _source_id(result["source_event_id"])
            if result["source_event_id"] != expected_source_event_id:
                raise CloudTransportError("RECEIPT_MISMATCH")
        except (ValueError, TypeError):
            raise CloudTransportError("INVALID_RESPONSE") from None
        return result

    def evidence_manifest(self, credential, source_event_id, manifest):
        source_event_id = _source_id(source_event_id)
        if (not isinstance(manifest, Mapping) or set(manifest) != {"schema_version", "kind", "content_type", "byte_count", "sha256"}
                or manifest["schema_version"] != 1 or manifest["kind"] != "OVERVIEW"
                or manifest["content_type"] != "image/jpeg" or type(manifest["byte_count"]) is not int
                or not 1 <= manifest["byte_count"] <= 350 * 1024 or not isinstance(manifest["sha256"], str)
                or not re.fullmatch(r"[0-9a-f]{64}", manifest["sha256"])):
            raise ValueError("INVALID_MEDIA_MANIFEST")
        result = self._call(f"sync/v1/observations/{source_event_id}/evidence", method="POST",
                            credential=credential, payload=dict(manifest), expected=(200, 201))
        try:
            required = {"evidence_id", "upload_required", "state"}
            if not required.issubset(result) or any(key not in required | {"receipt"} for key in result):
                raise ValueError
            canonical_id(result["evidence_id"])
            if type(result["upload_required"]) is not bool or result["state"] not in {"PENDING", "READY"}:
                raise ValueError
            if (result["upload_required"] and result["state"] != "PENDING") or (not result["upload_required"] and result["state"] != "READY"):
                raise ValueError
        except (ValueError, TypeError):
            raise CloudTransportError("INVALID_RESPONSE") from None
        return result

    def evidence_content(self, credential, evidence_id, content, *, content_type, sha256):
        evidence_id = canonical_id(evidence_id)
        if (type(content) is not bytes or not 1 <= len(content) <= 350 * 1024
                or content_type != "image/jpeg" or not isinstance(sha256, str)
                or not re.fullmatch(r"[0-9a-f]{64}", sha256)
                or hashlib.sha256(content).hexdigest() != sha256):
            raise ValueError("INVALID_MEDIA_CONTENT")
        headers = {"Accept": "application/json", "User-Agent": "AisleSignals-sync/1",
                   "Authorization": "Bearer " + token_value(credential), "Content-Type": content_type,
                   "X-Content-SHA256": sha256}
        request = Request(self.origin + "/device-api/sync/v1/evidence/" + evidence_id,
                          data=content, headers=headers, method="PUT")
        opener = build_opener(ProxyHandler({}), HTTPSHandler(), NoRedirect())
        try:
            with opener.open(request, timeout=8) as response:
                if response.status not in {200, 201}:
                    raise CloudTransportError("INVALID_RESPONSE")
                result = _decode(response.read(MAX_RESPONSE + 1))
        except HTTPError as error:
            try:
                if error.code in {401, 403}:
                    raise CloudTransportError("ACCESS_REVOKED") from None
                if 300 <= error.code < 400:
                    raise CloudTransportError("REDIRECT_REFUSED") from None
                if error.code >= 500:
                    raise CloudTransportError("SERVICE_UNAVAILABLE") from None
                raise CloudTransportError("RATE_LIMITED" if error.code == 429 else "REQUEST_REFUSED",
                                          retry_after=_retry_after(error.headers) if error.code == 429 else None) from None
            finally:
                error.close()
        except (URLError, TimeoutError, OSError, HTTPException):
            raise CloudTransportError("NETWORK_UNAVAILABLE") from None
        try:
            if not {"evidence_id", "state"}.issubset(result) or any(key not in {"evidence_id", "state", "receipt"} for key in result):
                raise ValueError
            canonical_id(result["evidence_id"])
            if result["evidence_id"] != evidence_id or result["state"] != "READY":
                raise ValueError
        except (ValueError, TypeError):
            raise CloudTransportError("INVALID_RESPONSE") from None
        return result
