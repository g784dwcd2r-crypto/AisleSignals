"""Strict inputs for the narrow prototype contract."""

from typing import Literal
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    AwareDatetime,
    StrictBool,
    StrictInt,
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
