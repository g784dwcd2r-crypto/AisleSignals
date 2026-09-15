"""Strict inputs for the narrow prototype contract."""

from datetime import datetime, timedelta, timezone
from typing import Literal
import math
import unicodedata
from uuid import UUID
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    AwareDatetime,
    StrictBool,
    StrictInt,
    field_validator,
    model_validator,
)


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class CameraCrop(Input):
    x: float = Field(strict=True, ge=0, le=1, allow_inf_nan=False)
    y: float = Field(strict=True, ge=0, le=1, allow_inf_nan=False)
    width: float = Field(strict=True, ge=0.05, le=1, allow_inf_nan=False)
    height: float = Field(strict=True, ge=0.05, le=1, allow_inf_nan=False)

    @model_validator(mode="after")
    def within_source(self):
        if self.x + self.width > 1.000001 or self.y + self.height > 1.000001:
            raise ValueError("Camera crop must stay within the reported source.")
        return self


class CameraContext(Input):
    """Declared mosaic provenance; never authority or a physical-camera identity."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=False)

    source_id: str = Field(strict=True, min_length=36, max_length=36)
    epoch: StrictInt = Field(ge=1, le=2147483647)
    layout: Literal["2x2", "3x2", "2x3"]
    camera_index: StrictInt = Field(ge=0, le=5)
    source_width: StrictInt = Field(ge=48, le=16384)
    source_height: StrictInt = Field(ge=48, le=16384)
    crop: CameraCrop

    @field_validator("source_id")
    @classmethod
    def source_uuid(cls, value):
        if str(UUID(value)) != value:
            raise ValueError("Use a canonical lower-case source UUID.")
        return value

    def pixel_size(self):
        # Match interactionCropPixels/JavaScript Math.round, including .5 ties.
        width = min(self.source_width - math.floor(self.crop.x * self.source_width),
                    math.floor(self.crop.width * self.source_width + 0.5))
        height = min(self.source_height - math.floor(self.crop.y * self.source_height),
                     math.floor(self.crop.height * self.source_height + 0.5))
        return width, height

    def jpeg_size(self):
        width, height = self.pixel_size()
        scale = min(1, 768 / max(width, height))
        return math.floor(width * scale + 0.5), math.floor(height * scale + 0.5)

    @model_validator(mode="after")
    def viable_camera(self):
        count = 4 if self.layout == "2x2" else 6
        if self.camera_index >= count:
            raise ValueError("Camera index must belong to the declared layout.")
        if min(self.pixel_size()) < 48:
            raise ValueError("Each camera crop requires at least 48 source pixels per edge.")
        return self


class CameraCalibration(Input):
    """Operator-declared coverage gates; these do not validate physical placement."""

    schema_version: Literal["1.0"]
    entrance_zone_confirmed: StrictBool
    exit_zone_confirmed: StrictBool
    cashier_zone_confirmed: StrictBool
    shelf_zones_confirmed: StrictBool

    @model_validator(mode="after")
    def complete(self):
        if not all((
            self.entrance_zone_confirmed,
            self.exit_zone_confirmed,
            self.cashier_zone_confirmed,
            self.shelf_zones_confirmed,
        )):
            raise ValueError("Camera calibration requires all four pharmacy zone confirmations.")
        return self


def camera_provenance(item: dict) -> dict:
    context = item.get("camera_context")
    if context is None:
        return {}
    return {
        "camera_context": context,
        "camera_id": f"{context['source_id']}:{context['epoch']}:{context['layout']}:{context['camera_index']}",
        "camera_label": f"Camera {context['camera_index'] + 1} · {context['layout']}",
    }


class Login(Input):
    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=1, max_length=256)


class Shift(Input):
    active: StrictBool


class Version(Input):
    expected_version: StrictInt = Field(ge=1)


class Reason(Version):
    reason: str = Field(min_length=5, max_length=2000)


class Review(Reason):
    decision: Literal["DISMISS", "OPEN_INCIDENT"]


class Simulator(Input):
    scenario: Literal[
        "SHELF_EVENT",
        "RETURNED_ITEM",
        "MISSING_MEDIA",
        "HISTORICAL_EVENT",
        "CAMERA_OFFLINE",
        "CAMERA_FROZEN",
        "CAMERA_RECOVERED",
    ]
    source_event_id: str = Field(
        min_length=1, max_length=100, pattern=r"^[A-Za-z0-9_-]+$"
    )


class IncidentCreate(Input):
    title: str = Field(min_length=3, max_length=120)
    notes: str = Field(min_length=5, max_length=4000)


class InteractionCaseCreate(IncidentCreate, Version):
    """An explicit staff-written case request for the version they reviewed."""


Classification = Literal[
    "UNASSESSED",
    "BENIGN",
    "INSUFFICIENT_EVIDENCE",
    "SUSPECTED_INCIDENT",
    "STORE_CONFIRMED_LOSS",
]
Outcome = Literal[
    "UNRESOLVED",
    "NO_LOSS_ESTABLISHED",
    "GOODS_RETURNED",
    "GOODS_PAID_FOR",
    "LOSS_RECORDED",
]


class IncidentPatch(Version):
    title: str | None = Field(default=None, min_length=3, max_length=120)
    notes: str | None = Field(default=None, min_length=5, max_length=4000)
    classification: Classification | None = None
    outcome: Outcome | None = None
    loss_cents: StrictInt | None = Field(default=None, ge=0, le=100_000_000)
    recovered_cents: StrictInt | None = Field(default=None, ge=0, le=100_000_000)


class TaskCreate(Version):
    title: str = Field(min_length=3, max_length=200)
    assignee: str = Field(min_length=2, max_length=120)
    due_at: AwareDatetime


class Export(Version):
    purpose: str = Field(min_length=5, max_length=500)


class AssistanceCreate(Input):
    reason: str = Field(min_length=3, max_length=500)


class AssistanceTransition(Input):
    status: Literal["ACKNOWLEDGED", "RESOLVED"]


class PlaybackEvent(Input):
    """Frame-change test metadata only; no footage, identities or incident facts."""

    run_id: str = Field(
        strict=True, min_length=1, max_length=100, pattern=r"^[A-Za-z0-9_-]+$"
    )
    event_index: StrictInt = Field(ge=0, le=99)
    category: Literal[
        "SUSTAINED_VISUAL_ACTIVITY",
        "EXTENDED_VISUAL_ACTIVITY",
        "LARGE_SCENE_CHANGE",
    ]
    video_start_seconds: float = Field(
        strict=True, allow_inf_nan=False, ge=0, le=600
    )
    video_end_seconds: float = Field(
        strict=True, allow_inf_nan=False, ge=0, le=600
    )
    peak_changed_ratio: float = Field(
        strict=True, allow_inf_nan=False, ge=0, le=1
    )
    alarm_status: Literal["SOUND_REQUESTED", "MUTED", "BLOCKED"]

    @model_validator(mode="after")
    def ordered_interval(self):
        if self.video_end_seconds < self.video_start_seconds:
            raise ValueError("Video end must be at or after video start.")
        return self


class LiveEventInput(Input):
    """Browser-reported pose observations, never footage or a finding of theft."""

    run_id: str = Field(strict=True, min_length=36, max_length=36)
    event_id: str = Field(strict=True, min_length=36, max_length=36)
    source_kind: Literal["SCREEN_CAPTURE", "CAMERA", "RECORDED_VIDEO"]
    source_label: str = Field(strict=True, min_length=1, max_length=120)
    camera_context: CameraContext | None = None
    event_code: Literal["REPEATED_HAND_TO_WAIST", "RESTRICTED_ZONE_ENTRY"]
    track_id: StrictInt = Field(ge=1, le=1_000_000)
    source_time_seconds: float = Field(
        strict=True, allow_inf_nan=False, ge=0, le=43_200
    )
    detected_at: AwareDatetime
    model_version: Literal[
        "mediapipe-pose-lite-f16-v1",
        "mediapipe-efficientdet-lite0-u8-v1+pose-lite-f16-v1",
    ]
    rule_version: Literal["pose-rules-v1", "pose-rules-v2"]
    sound_requested: StrictBool

    @field_validator("run_id", "event_id")
    @classmethod
    def canonical_uuid(cls, value):
        parsed = str(UUID(value))
        if parsed != value.lower():
            raise ValueError("Use a hyphenated UUID.")
        return parsed

    @field_validator("source_label")
    @classmethod
    def plain_source_label(cls, value):
        if any(
            character in "<>" or unicodedata.category(character).startswith("C")
            for character in value
        ):
            raise ValueError("Use a plain source label without markup or controls.")
        return value

    @field_validator("detected_at", mode="before")
    @classmethod
    def explicit_timestamp(cls, value):
        if not isinstance(value, str) or "T" not in value:
            raise ValueError("Use an ISO timestamp with a timezone.")
        return value

    @model_validator(mode="after")
    def bounded_observation(self):
        if self.source_kind == "RECORDED_VIDEO" and self.source_time_seconds > 600:
            raise ValueError("Recorded-video source time must not exceed 600 seconds.")
        stamp = datetime.now(timezone.utc)
        if not stamp - timedelta(hours=24) <= self.detected_at <= stamp + timedelta(minutes=1):
            raise ValueError("Detection time must be within the last 24 hours and no more than one minute ahead.")
        return self
