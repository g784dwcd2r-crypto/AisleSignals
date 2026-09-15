import type { DetectedPerson } from "./liveDetectionTypes";
import type { CameraGridLayout } from "./cameraGrid";

export const LAYOUT_ZONE_KINDS = [
  "ENTRANCE",
  "EXIT",
  "CASHIER",
  "SHELF",
  "BLIND",
  "IGNORE",
] as const;
export type LayoutZoneKind = (typeof LAYOUT_ZONE_KINDS)[number];
export type LayoutPoint = { x: number; y: number };
export type LayoutZone = {
  id: string;
  kind: LayoutZoneKind;
  label: string;
  points: LayoutPoint[];
};
export type LayoutCamera = {
  camera_index: number;
  label: string;
  crop: { x: number; y: number; width: number; height: number };
  zones: LayoutZone[];
};
export type LayoutReadiness = {
  status: "INCOMPLETE" | "READY_FOR_SITE_ACCEPTANCE";
  missing_required_kinds: LayoutZoneKind[];
  uncalibrated_camera_indices: number[];
  warnings: string[];
  /** Always false: map calibration is context, never an alarm decision. */
  alarm_authority: false;
};
export type LayoutCalibration = {
  schema_version: "1.0";
  version: number;
  layout: Exclude<CameraGridLayout, "single"> | null;
  source_label: string;
  cameras: LayoutCamera[];
  readiness: LayoutReadiness;
  updated_at?: string;
  updated_by?: string;
};

export function pointInPolygon(point: LayoutPoint, polygon: LayoutPoint[]) {
  let inside = false;
  for (let i = 0, j = polygon.length - 1; i < polygon.length; j = i++) {
    const a = polygon[i],
      b = polygon[j];
    if (
      a.y > point.y !== b.y > point.y &&
      point.x < ((b.x - a.x) * (point.y - a.y)) / (b.y - a.y) + a.x
    )
      inside = !inside;
  }
  return inside;
}

/** Blind/ignored polygons can remove rule evidence, never create it. The full
 * person box remains visible because presence is still an observed fact. */
export function applyLayoutObservationGuard(
  persons: DetectedPerson[],
  calibration: LayoutCalibration | null,
  cameraIndex: number | null,
): DetectedPerson[] {
  if (!calibration || cameraIndex === null) return persons;
  const masks =
    calibration.cameras
      .find((camera) => camera.camera_index === cameraIndex)
      ?.zones.filter(
        (zone) => zone.kind === "BLIND" || zone.kind === "IGNORE",
      ) ?? [];
  if (!masks.length) return persons;
  return persons.map((person) => {
    const foot = {
      x: person.box.x + person.box.width / 2,
      y: person.box.y + person.box.height,
    };
    return masks.some((zone) => pointInPolygon(foot, zone.points))
      ? { ...person, landmarks: null }
      : person;
  });
}

export function cameraIndexForCrop(
  calibration: LayoutCalibration | null,
  tiles: {
    index: number;
    crop: { x: number; y: number; width: number; height: number };
  }[],
  crop: { x: number; y: number; width: number; height: number } | null,
) {
  if (!calibration || !crop) return null;
  const close = (a: number, b: number) => Math.abs(a - b) < 1e-6;
  const tile = tiles.find(
    (tile) =>
      close(tile.crop.x, crop.x) &&
      close(tile.crop.y, crop.y) &&
      close(tile.crop.width, crop.width) &&
      close(tile.crop.height, crop.height),
  );
  if (!tile) return null;
  const saved = calibration.cameras.find(
    (camera) => camera.camera_index === tile.index,
  );
  return saved &&
    close(saved.crop.x, tile.crop.x) &&
    close(saved.crop.y, tile.crop.y) &&
    close(saved.crop.width, tile.crop.width) &&
    close(saved.crop.height, tile.crop.height)
    ? tile.index
    : null;
}

export function calibrationMatchesLayout(
  calibration: LayoutCalibration | null,
  layout: {
    layout: CameraGridLayout;
    tiles: {
      index: number;
      crop: { x: number; y: number; width: number; height: number };
    }[];
  } | null,
) {
  if (
    !calibration?.layout ||
    !layout ||
    calibration.layout !== layout.layout ||
    calibration.cameras.length !== layout.tiles.length
  )
    return false;
  return layout.tiles.every(
    (tile) =>
      cameraIndexForCrop(calibration, layout.tiles, tile.crop) === tile.index,
  );
}

export function rectangularZone(
  id: string,
  kind: LayoutZoneKind,
  label: string,
  start: LayoutPoint,
  end: LayoutPoint,
): LayoutZone {
  const left = Math.min(start.x, end.x),
    top = Math.min(start.y, end.y);
  const right = Math.max(start.x, end.x),
    bottom = Math.max(start.y, end.y);
  if (right - left < 0.02 || bottom - top < 0.02)
    throw new Error("Draw a zone at least 2% wide and high.");
  return {
    id,
    kind,
    label,
    points: [
      { x: left, y: top },
      { x: right, y: top },
      { x: right, y: bottom },
      { x: left, y: bottom },
    ],
  };
}
