import { validateCameraArea } from "./cameraGrid";
import type { DetectionRect, LiveTrack } from "./liveDetectionTypes";

/** The crop is applied to decoded source pixels before the pose model resizes. */
export function poseFrameRegion(
  width: number,
  height: number,
  crop: DetectionRect | null = null,
) {
  if (
    !Number.isInteger(width) ||
    !Number.isInteger(height) ||
    width < 1 ||
    height < 1
  )
    throw new Error("A decoded video frame is required for body tracking.");
  const selected = validateCameraArea(
    crop ?? { x: 0, y: 0, width: 1, height: 1 },
  );
  const x = Math.floor(selected.x * width),
    y = Math.floor(selected.y * height);
  const regionWidth = Math.min(width - x, Math.round(selected.width * width));
  const regionHeight = Math.min(
    height - y,
    Math.round(selected.height * height),
  );
  if (crop && (regionWidth < 48 || regionHeight < 48))
    throw new Error(
      "This camera area is too small. Select a larger view before tracking.",
    );
  const ratio = Math.min(1, 640 / Math.max(regionWidth, regionHeight));
  return {
    x,
    y,
    width: regionWidth,
    height: regionHeight,
    outputWidth: Math.max(1, Math.round(regionWidth * ratio)),
    outputHeight: Math.max(1, Math.round(regionHeight * ratio)),
    area: {
      x: x / width,
      y: y / height,
      width: regionWidth / width,
      height: regionHeight / height,
    },
  };
}

/** Rules run in camera coordinates; only their display is projected to the source. */
export function projectPoseTracks(
  tracks: LiveTrack[],
  area: DetectionRect,
): LiveTrack[] {
  return tracks.map((track) => ({
    ...track,
    box: {
      x: area.x + track.box.x * area.width,
      y: area.y + track.box.y * area.height,
      width: track.box.width * area.width,
      height: track.box.height * area.height,
    },
    landmarks: track.landmarks.map((point) => ({
      ...point,
      x: area.x + point.x * area.width,
      y: area.y + point.y * area.height,
    })),
  }));
}

/** A configured source zone only applies to its intersection with this camera. */
export function poseZoneInCamera(
  zone: DetectionRect | null,
  area: DetectionRect,
): DetectionRect | null {
  if (!zone) return null;
  const left = Math.max(zone.x, area.x),
    top = Math.max(zone.y, area.y);
  const right = Math.min(zone.x + zone.width, area.x + area.width);
  const bottom = Math.min(zone.y + zone.height, area.y + area.height);
  if (right <= left || bottom <= top) return null;
  return {
    x: (left - area.x) / area.width,
    y: (top - area.y) / area.height,
    width: (right - left) / area.width,
    height: (bottom - top) / area.height,
  };
}
