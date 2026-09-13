"""Strict inputs for the narrow prototype contract."""

from datetime import datetime, timedelta, timezone
from typing import Literal
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
    event_code: Literal["REPEATED_HAND_TO_WAIST", "RESTRICTED_ZONE_ENTRY"]
    track_id: StrictInt = Field(ge=1, le=1_000_000)
    source_time_seconds: float = Field(
        strict=True, allow_inf_nan=False, ge=0, le=43_200
    )
    detected_at: AwareDatetime
    model_version: Literal["mediapipe-pose-lite-f16-v1"]
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
