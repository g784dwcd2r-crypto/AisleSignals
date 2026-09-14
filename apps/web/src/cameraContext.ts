import type { DetectionRect } from "./liveDetectionTypes";
import { interactionCropPixels } from "./interactionCapture";

/** Browser-declared mosaic provenance; it does not confer branch access. */
export type CameraContext = Readonly<{
  source_id: string;
  epoch: number;
  layout: "2x2" | "3x2" | "2x3";
  camera_index: number;
  source_width: number;
  source_height: number;
  crop: Readonly<DetectionRect>;
}>;

export function validateCameraContext(value: unknown): CameraContext {
  const invalid = () =>
    new Error("Use a confirmed camera with valid source geometry.");
  if (!value || typeof value !== "object" || Array.isArray(value))
    throw invalid();
  const item = value as Record<string, unknown>;
  const keys = [
    "source_id",
    "epoch",
    "layout",
    "camera_index",
    "source_width",
    "source_height",
    "crop",
  ];
  if (
    Object.keys(item).length !== keys.length ||
    keys.some((key) => !Object.hasOwn(item, key))
  )
    throw invalid();
  if (
    typeof item.source_id !== "string" ||
    !/^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$/.test(
      item.source_id,
    )
  )
    throw invalid();
  for (const key of [
    "epoch",
    "camera_index",
    "source_width",
    "source_height",
  ]) {
    if (typeof item[key] !== "number" || !Number.isSafeInteger(item[key]))
      throw invalid();
  }
  const candidate = item as unknown as CameraContext;
  if (
    candidate.epoch < 1 ||
    candidate.epoch > 2147483647 ||
    !["2x2", "3x2", "2x3"].includes(candidate.layout) ||
    candidate.camera_index < 0 ||
    candidate.camera_index >= (candidate.layout === "2x2" ? 4 : 6) ||
    candidate.source_width < 48 ||
    candidate.source_width > 16384 ||
    candidate.source_height < 48 ||
    candidate.source_height > 16384
  )
    throw invalid();
  if (
    !candidate.crop ||
    typeof candidate.crop !== "object" ||
    Array.isArray(candidate.crop) ||
    Object.keys(candidate.crop).length !== 4 ||
    ["x", "y", "width", "height"].some(
      (key) => !Object.hasOwn(candidate.crop, key),
    )
  )
    throw invalid();
  if (
    Object.values(candidate.crop).some(
      (value) =>
        typeof value !== "number" ||
        !Number.isFinite(value) ||
        value < 0 ||
        value > 1,
    )
  )
    throw invalid();
  interactionCropPixels(
    candidate.source_width,
    candidate.source_height,
    candidate.crop,
  );
  return Object.freeze({
    source_id: candidate.source_id,
    epoch: candidate.epoch,
    layout: candidate.layout,
    camera_index: candidate.camera_index,
    source_width: candidate.source_width,
    source_height: candidate.source_height,
    crop: Object.freeze({ ...candidate.crop }),
  });
}

export const cameraContextLabel = (context: CameraContext) =>
  `Camera ${context.camera_index + 1} · ${context.layout}`;

export const cameraContextId = (context: CameraContext) =>
  `${context.source_id}:${context.epoch}:${context.layout}:${context.camera_index}`;

/** Include geometry as well as the epoch; an identifier alone is not freshness. */
export function cameraContextKey(value: CameraContext): string {
  const context = validateCameraContext(value);
  return JSON.stringify([
    context.source_id,
    context.epoch,
    context.layout,
    context.camera_index,
    context.source_width,
    context.source_height,
    context.crop.x,
    context.crop.y,
    context.crop.width,
    context.crop.height,
  ]);
}
