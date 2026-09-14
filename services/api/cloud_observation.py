"""Pure, fail-closed metadata projection; this module does not authorise or send.

Inputs must come from the local server's scoped stored entity and immutable
binding/admission snapshots, never directly from a request body. No Store,
network, clock, media, alarm or worker is accessed here.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum
from types import MappingProxyType
from uuid import UUID, uuid5

from pydantic import ValidationError

from .models import CameraContext


OBSERVATION_NAMESPACE = UUID("b00af7d7-4931-5a90-a014-8c3deca79524")
MAX_RETENTION = timedelta(hours=24)
MAX_PAYLOAD_BYTES = 2048
POSE_EVENT_CODES = frozenset({"REPEATED_HAND_TO_WAIST", "RESTRICTED_ZONE_ENTRY"})
EVENT_CODES = POSE_EVENT_CODES | {"POSSIBLE_CONCEALMENT"}
_PAYLOAD_KEYS = frozenset({"source_event_id", "event_code", "source_label", "occurred_at", "historical"})
_IDENTIFIER = re.compile(r"[A-Za-z0-9_-]{1,128}\Z")
_TIMESTAMP = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|[+-]\d{2}:\d{2})\Z")
_SOURCE_LABELS = frozenset({"Camera source · local observation", "Screen area · local observation"}) | frozenset(
    f"Camera {index} of {count} · {layout} screen grid · local observation"
    for layout, count in (("2x2", 4), ("3x2", 6), ("2x3", 6))
    for index in range(1, count + 1)
)


class Exclusion(str, Enum):
    NOT_PILOT = "NOT_PILOT"
    BINDING_MISSING = "BINDING_MISSING"
    BINDING_DISABLED = "BINDING_DISABLED"
    SCOPE_MISMATCH = "SCOPE_MISMATCH"
    ADMISSION_MISSING = "ADMISSION_MISSING"
    BINDING_CHANGED = "BINDING_CHANGED"
    BEFORE_BINDING = "BEFORE_BINDING"
    INVALID_OBSERVATION = "INVALID_OBSERVATION"
    UNSUPPORTED_SOURCE = "UNSUPPORTED_SOURCE"
    RECORDED_SOURCE = "RECORDED_SOURCE"
    NOT_COMPLETED = "NOT_COMPLETED"
    EVENT_EXCLUDED = "EVENT_EXCLUDED"
    ALARM_INELIGIBLE = "ALARM_INELIGIBLE"
    POSE_DISABLED = "POSE_DISABLED"
    INVALID_CAMERA_CONTEXT = "INVALID_CAMERA_CONTEXT"
    CLOCK_INVALID = "CLOCK_INVALID"
    EXPIRED = "EXPIRED"


def _uuid(value: object) -> str:
    if type(value) is not str or len(value) != 36:
        raise ValueError("INVALID_IDENTIFIER")
    try:
        parsed = UUID(value)
    except ValueError:
        raise ValueError("INVALID_IDENTIFIER") from None
    if str(parsed) != value or parsed.int == 0:
        raise ValueError("INVALID_IDENTIFIER")
    return value


def _identifier(value: object) -> str:
    if type(value) is not str or not _IDENTIFIER.fullmatch(value):
        raise ValueError("INVALID_IDENTIFIER")
    return value


def _generation(value: object) -> int:
    if type(value) is not int or not 1 <= value <= 2**63 - 1:
        raise ValueError("INVALID_GENERATION")
    return value


def _local_entity_id(entity_kind: object, entity_id: object) -> None:
    if entity_kind == "interaction":
        _uuid(entity_id)
    elif entity_kind == "live_event":
        if type(entity_id) is not str or not re.fullmatch(r"live-[0-9a-f]{64}", entity_id):
            raise ValueError("INVALID_IDENTIFIER")
    else:
        raise ValueError("INVALID_ENTITY_KIND")


def _utc(value: object) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("CLOCK_INVALID")
    try:
        return value.astimezone(timezone.utc)
    except (ValueError, OverflowError):
        raise ValueError("CLOCK_INVALID") from None


def _parse_time(value: object) -> datetime:
    if type(value) is not str or not _TIMESTAMP.fullmatch(value):
        raise ValueError("CLOCK_INVALID")
    try:
        return _utc(datetime.fromisoformat(value.replace("Z", "+00:00")))
    except (ValueError, OverflowError):
        raise ValueError("CLOCK_INVALID") from None


def _iso(value: datetime) -> str:
    return value.isoformat().replace("+00:00", "Z")


@dataclass(frozen=True, slots=True)
class ObservationScope:
    """Installation and SQL row scope resolved by the trusted caller."""

    installation_id: str
    organisation_id: str
    site_id: str

    def __post_init__(self):
        _uuid(self.installation_id)
        _identifier(self.organisation_id)
        _identifier(self.site_id)


@dataclass(frozen=True, slots=True)
class BindingSnapshot:
    """Current server-owned binding generation; no names or credentials."""

    installation_id: str
    binding_id: str
    generation: int
    organisation_id: str
    site_id: str
    activated_at: datetime
    enabled: bool = True
    pose_event_codes: frozenset[str] = frozenset()

    def __post_init__(self):
        _uuid(self.installation_id)
        _uuid(self.binding_id)
        _generation(self.generation)
        _identifier(self.organisation_id)
        _identifier(self.site_id)
        object.__setattr__(self, "activated_at", _utc(self.activated_at))
        if type(self.enabled) is not bool:
            raise ValueError("INVALID_POLICY")
        if type(self.pose_event_codes) is not frozenset or not self.pose_event_codes <= POSE_EVENT_CODES:
            raise ValueError("INVALID_POLICY")


@dataclass(frozen=True, slots=True)
class AdmissionSnapshot:
    """Captured when the entity/job was admitted, never reconstructed later."""

    binding_id: str
    generation: int

    def __post_init__(self):
        _uuid(self.binding_id)
        _generation(self.generation)


def source_event_id(*, installation_id: str, binding_id: str, organisation_id: str,
                    site_id: str, entity_kind: str, entity_id: str) -> str:
    """Versioned UUID5 contract shared with the outbox; never use current time.

    The JSON array is ASCII, compact, ordered and unambiguous. Binding IDs rotate
    for new targets. Generation changes gate admission, not an existing identity.
    """
    _uuid(installation_id)
    _uuid(binding_id)
    _identifier(organisation_id)
    _identifier(site_id)
    _local_entity_id(entity_kind, entity_id)
    identity = json.dumps(
        ["aislesignals-observation-v1", installation_id, binding_id,
         organisation_id, site_id, entity_kind, entity_id],
        ensure_ascii=True, separators=(",", ":"),
    )
    return str(uuid5(OBSERVATION_NAMESPACE, identity))


def validate_payload(payload: Mapping) -> Mapping[str, str | bool]:
    """Copy and freeze only the exact minimal protocol shape; errors echo no data."""
    if not isinstance(payload, Mapping) or set(payload) != _PAYLOAD_KEYS:
        raise ValueError("INVALID_PAYLOAD")
    try:
        event_id = _uuid(payload["source_event_id"])
        if UUID(event_id).version != 5:
            raise ValueError("INVALID_PAYLOAD")
        code, label, stamp = payload["event_code"], payload["source_label"], payload["occurred_at"]
        if (type(code) is not str or code not in EVENT_CODES
                or type(label) is not str or label not in _SOURCE_LABELS
                or payload["historical"] is not True
                or _iso(_parse_time(stamp)) != stamp):
            raise ValueError("INVALID_PAYLOAD")
    except (ValueError, TypeError):
        raise ValueError("INVALID_PAYLOAD") from None
    return MappingProxyType({
        "source_event_id": event_id, "event_code": code, "source_label": label,
        "occurred_at": stamp, "historical": True,
    })


def canonical_payload(payload: Mapping) -> bytes:
    """Deterministic UTF-8 JSON bytes for hashing/encryption and exact retries."""
    data = json.dumps(dict(validate_payload(payload)), sort_keys=True,
                      ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8")
    if len(data) > MAX_PAYLOAD_BYTES:
        raise ValueError("INVALID_PAYLOAD")
    return data


@dataclass(frozen=True, slots=True)
class MappedObservation:
    entity_kind: str
    entity_id: str
    payload: Mapping[str, str | bool]
    admitted_at: datetime
    deadline: datetime

    def __post_init__(self):
        _local_entity_id(self.entity_kind, self.entity_id)
        object.__setattr__(self, "payload", validate_payload(self.payload))
        object.__setattr__(self, "admitted_at", _utc(self.admitted_at))
        object.__setattr__(self, "deadline", _utc(self.deadline))
        if (self.payload["occurred_at"] != _iso(self.admitted_at)
                or not timedelta() < self.deadline - self.admitted_at <= MAX_RETENTION):
            raise ValueError("INVALID_DEADLINE")


def map_observation(*, database_mode: str, scope: ObservationScope,
                    binding: BindingSnapshot | None, admission: AdmissionSnapshot | None,
                    entity_kind: str, item: Mapping, now: datetime) -> MappedObservation | Exclusion:
    """Project a scoped persisted entity, or return one bounded exclusion code.

    Caller revalidates binding/scope and source existence in its enqueue/send
    transaction. This pure function neither authenticates a camera declaration
    nor proves physical liveness. It never accepts the flattened history DTO.
    """
    if database_mode != "pilot":
        return Exclusion.NOT_PILOT
    if binding is None:
        return Exclusion.BINDING_MISSING
    if not binding.enabled:
        return Exclusion.BINDING_DISABLED
    if (scope.installation_id, scope.organisation_id, scope.site_id) != (
        binding.installation_id, binding.organisation_id, binding.site_id
    ):
        return Exclusion.SCOPE_MISMATCH
    if admission is None:
        return Exclusion.ADMISSION_MISSING
    if (admission.binding_id, admission.generation) != (binding.binding_id, binding.generation):
        return Exclusion.BINDING_CHANGED
    if (not isinstance(item, Mapping) or type(entity_kind) is not str
            or entity_kind not in {"interaction", "live_event"}):
        return Exclusion.INVALID_OBSERVATION
    for key in ("organisation_id", "site_id"):
        if (entity_kind == "interaction" or key in item) and item.get(key) != getattr(scope, key):
            return Exclusion.SCOPE_MISMATCH
    try:
        event_id = source_event_id(
            installation_id=scope.installation_id, binding_id=binding.binding_id,
            organisation_id=scope.organisation_id, site_id=scope.site_id,
            entity_kind=entity_kind, entity_id=item.get("id"),
        )
    except ValueError:
        return Exclusion.INVALID_OBSERVATION
    source = item.get("source_kind")
    if source == "RECORDED_VIDEO" or item.get("historical") is True:
        return Exclusion.RECORDED_SOURCE
    if source not in ("SCREEN_CAPTURE", "CAMERA"):
        return Exclusion.UNSUPPORTED_SOURCE
    if entity_kind == "interaction":
        if item.get("status") != "completed":
            return Exclusion.NOT_COMPLETED
        result = item.get("result")
        if not isinstance(result, Mapping):
            return Exclusion.INVALID_OBSERVATION
        if result.get("action") != "POSSIBLE_CONCEALMENT":
            return Exclusion.EVENT_EXCLUDED
        if result.get("alarm_eligible") is not True:
            return Exclusion.ALARM_INELIGIBLE
        code = "POSSIBLE_CONCEALMENT"
    else:
        code = item.get("event_code")
        if type(code) is not str or code not in POSE_EVENT_CODES:
            return Exclusion.EVENT_EXCLUDED
        if code not in binding.pose_event_codes:
            return Exclusion.POSE_DISABLED
    try:
        clock = _utc(now)
        admitted = _parse_time(item.get("created_at"))
        if not clock - timedelta(days=30) <= admitted <= clock + timedelta(minutes=5):
            return Exclusion.CLOCK_INVALID
        if binding.activated_at > clock + timedelta(minutes=5):
            return Exclusion.CLOCK_INVALID
        if admitted < binding.activated_at:
            return Exclusion.BEFORE_BINDING
        deadline = admitted + MAX_RETENTION
        if entity_kind == "interaction":
            source_expiry = _parse_time(item.get("expires_at"))
            if source_expiry <= admitted:
                return Exclusion.CLOCK_INVALID
            deadline = min(deadline, source_expiry)
        if clock >= deadline:
            return Exclusion.EXPIRED
    except (ValueError, OverflowError):
        return Exclusion.CLOCK_INVALID
    label = "Camera source · local observation" if source == "CAMERA" else "Screen area · local observation"
    if item.get("camera_context") is not None:
        try:
            context = CameraContext.model_validate(item["camera_context"])
        except (ValidationError, ValueError, TypeError):
            return Exclusion.INVALID_CAMERA_CONTEXT
        count = 4 if context.layout == "2x2" else 6
        label = f"Camera {context.camera_index + 1} of {count} · {context.layout} screen grid · local observation"
    return MappedObservation(
        entity_kind=entity_kind, entity_id=item["id"], admitted_at=admitted, deadline=deadline,
        payload={"source_event_id": event_id, "event_code": code, "source_label": label,
                 "occurred_at": _iso(admitted), "historical": True},
    )
