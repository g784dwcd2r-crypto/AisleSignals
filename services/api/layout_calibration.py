"""Branch-scoped pharmacy map calibration.

Calibration is operator-authored context.  It can suppress observations in
declared blind/ignored regions, but it never grants alarm authority.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal

from fastapi import Depends
from pydantic import BaseModel, ConfigDict, Field, StrictInt, field_validator, model_validator


ZoneKind = Literal["ENTRANCE", "EXIT", "CASHIER", "SHELF", "BLIND", "IGNORE"]


class StrictInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class Point(StrictInput):
    x: float = Field(strict=True, ge=0, le=1, allow_inf_nan=False)
    y: float = Field(strict=True, ge=0, le=1, allow_inf_nan=False)


class Crop(StrictInput):
    x: float = Field(strict=True, ge=0, le=1, allow_inf_nan=False)
    y: float = Field(strict=True, ge=0, le=1, allow_inf_nan=False)
    width: float = Field(strict=True, ge=.05, le=1, allow_inf_nan=False)
    height: float = Field(strict=True, ge=.05, le=1, allow_inf_nan=False)

    @model_validator(mode="after")
    def within_source(self):
        if self.x + self.width > 1.000001 or self.y + self.height > 1.000001:
            raise ValueError("Camera crop must stay inside the CCTV source.")
        return self


def cross(a: Point, b: Point, c: Point) -> float:
    return (b.x - a.x) * (c.y - a.y) - (b.y - a.y) * (c.x - a.x)


def intersects(a: Point, b: Point, c: Point, d: Point) -> bool:
    values = cross(a, b, c), cross(a, b, d), cross(c, d, a), cross(c, d, b)
    if values[0] * values[1] < 0 and values[2] * values[3] < 0:
        return True
    def on_segment(p, q, r):
        return (abs(cross(p, q, r)) <= 1e-12
                and min(p.x, q.x) <= r.x <= max(p.x, q.x)
                and min(p.y, q.y) <= r.y <= max(p.y, q.y))
    return (on_segment(a, b, c) or on_segment(a, b, d)
            or on_segment(c, d, a) or on_segment(c, d, b))


class Zone(StrictInput):
    id: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]{0,47}$")
    kind: ZoneKind
    label: str = Field(min_length=1, max_length=80)
    points: list[Point] = Field(min_length=3, max_length=12)

    @model_validator(mode="after")
    def valid_polygon(self):
        if len({(p.x, p.y) for p in self.points}) != len(self.points):
            raise ValueError("Zone polygon points must be unique.")
        area = abs(sum(
            p.x * self.points[(i + 1) % len(self.points)].y
            - self.points[(i + 1) % len(self.points)].x * p.y
            for i, p in enumerate(self.points)
        )) / 2
        if area < 0.0004:
            raise ValueError("Zone polygon must cover a visible area.")
        count = len(self.points)
        for i in range(count):
            for j in range(i + 1, count):
                if abs(i - j) in (0, 1) or {i, j} == {0, count - 1}:
                    continue
                if intersects(self.points[i], self.points[(i + 1) % count],
                              self.points[j], self.points[(j + 1) % count]):
                    raise ValueError("Zone polygon cannot cross itself.")
        return self


class CameraMap(StrictInput):
    camera_index: StrictInt = Field(ge=0, le=5)
    label: str = Field(min_length=1, max_length=80)
    crop: Crop
    zones: list[Zone] = Field(default_factory=list, max_length=40)

    @model_validator(mode="after")
    def unique_zones(self):
        if len({zone.id for zone in self.zones}) != len(self.zones):
            raise ValueError("Zone identifiers must be unique within a camera.")
        return self


class CalibrationWrite(StrictInput):
    schema_version: Literal["1.0"]
    expected_version: StrictInt = Field(ge=0)
    layout: Literal["2x2", "3x2", "2x3"]
    source_label: str = Field(min_length=1, max_length=120)
    cameras: list[CameraMap] = Field(min_length=4, max_length=6)

    @field_validator("source_label")
    @classmethod
    def no_source_address(cls, value):
        lowered = value.lower()
        if "://" in lowered or "password=" in lowered or "token=" in lowered:
            raise ValueError("Use a display label, never a camera URL or credential.")
        return value

    @model_validator(mode="after")
    def complete_camera_set(self):
        expected = 4 if self.layout == "2x2" else 6
        indices = [camera.camera_index for camera in self.cameras]
        if len(self.cameras) != expected or sorted(indices) != list(range(expected)):
            raise ValueError("Calibration must contain every camera in the selected layout exactly once.")
        all_zone_ids = [zone.id for camera in self.cameras for zone in camera.zones]
        if len(set(all_zone_ids)) != len(all_zone_ids):
            raise ValueError("Zone identifiers must be unique across the calibration.")
        for index, left in enumerate(self.cameras):
            for right in self.cameras[index + 1:]:
                overlap_width = min(left.crop.x + left.crop.width, right.crop.x + right.crop.width) - max(left.crop.x, right.crop.x)
                overlap_height = min(left.crop.y + left.crop.height, right.crop.y + right.crop.height) - max(left.crop.y, right.crop.y)
                if overlap_width > 0.000001 and overlap_height > 0.000001:
                    raise ValueError("Confirmed camera crops cannot overlap.")
        return self


def readiness(cameras: list[CameraMap]):
    kinds = {zone.kind for camera in cameras for zone in camera.zones}
    missing = [kind for kind in ("ENTRANCE", "EXIT", "CASHIER", "SHELF") if kind not in kinds]
    empty = [camera.camera_index for camera in cameras if not camera.zones]
    warnings = [f"Add at least one {kind.lower()} zone." for kind in missing]
    if empty:
        warnings.append("Mark visible coverage or a blind/ignored area on every camera: "
                        + ", ".join(str(index + 1) for index in empty) + ".")
    return {
        "status": "READY_FOR_SITE_ACCEPTANCE" if not warnings else "INCOMPLETE",
        "missing_required_kinds": missing,
        "uncalibrated_camera_indices": empty,
        "warnings": warnings,
        "alarm_authority": False,
    }


def install_layout_calibration(application, context, problem):
    resource_id = lambda site: f"layout-calibration-{site}"

    @application.get("/api/layout-calibration")
    def get_layout(ctx=Depends(context)):
        item = ctx.store.get(ctx.conn, ctx.user, "layout_calibration", resource_id(ctx.user["site_id"]))
        if item is None:
            return {
                "schema_version": "1.0", "version": 0, "layout": None,
                "source_label": "", "cameras": [],
                "readiness": {
                    "status": "INCOMPLETE",
                    "missing_required_kinds": ["ENTRANCE", "EXIT", "CASHIER", "SHELF"],
                    "uncalibrated_camera_indices": [],
                    "warnings": ["Confirm a four-camera or six-camera layout to begin."],
                    "alarm_authority": False,
                },
            }
        return item

    @application.put("/api/layout-calibration")
    def put_layout(body: CalibrationWrite, ctx=Depends(context)):
        ctx.manager()
        key = resource_id(ctx.user["site_id"])
        current = ctx.store.get(ctx.conn, ctx.user, "layout_calibration", key)
        version = current["version"] if current else 0
        if body.expected_version != version:
            problem(409, "VERSION_CONFLICT",
                    "This calibration changed in another session. Reload it before saving.", version)
        stamp = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        cameras = body.cameras
        value = {
            "id": key,
            "schema_version": "1.0",
            "version": version + 1,
            "layout": body.layout,
            "source_label": body.source_label,
            "cameras": [camera.model_dump() for camera in cameras],
            "readiness": readiness(cameras),
            "updated_at": stamp,
            "updated_by": ctx.user["name"],
            "created_at": current.get("created_at", stamp) if current else stamp,
        }
        ctx.put("layout_calibration", value)
        ctx.audit("LAYOUT_CALIBRATION_SAVED", "layout_calibration", key,
                  f"Camera layout calibration version {value['version']} saved; alarm authority remains disabled.")
        return value
