import type { DetectionRect, LiveSourceKind } from "./liveDetectionTypes";

export type InteractionAction =
  | "TAKE_PRODUCT"
  | "RETURN_PRODUCT"
  | "PLACE_IN_BASKET"
  | "POSSIBLE_CONCEALMENT"
  | "NORMAL_SHOPPING"
  | "UNCLEAR";
export type InteractionReview = "USEFUL" | "NORMAL_SHOPPING" | "UNCLEAR";
export type SavedInteraction = {
  id: string;
  version: number;
  run_id: string;
  source_kind: LiveSourceKind;
  source_label: string;
  created_at: string;
  expires_at: string;
  model: string;
  action: InteractionAction;
  visibility: "clear" | "partial" | "poor";
  person_visible: boolean;
  product_visible: boolean;
  sequence_observed: boolean;
  reason: string;
  evidence_frame_indices: number[];
  alarm_eligible: boolean;
  inference_ms: number;
  frames: { at_seconds: number; url: string }[];
  review: null | { outcome: InteractionReview; note: string };
};

export const interactionLabels: Record<InteractionAction, string> = {
  TAKE_PRODUCT: "Product picked up",
  RETURN_PRODUCT: "Product returned",
  PLACE_IN_BASKET: "Product placed in basket",
  POSSIBLE_CONCEALMENT: "Possible product concealment",
  NORMAL_SHOPPING: "Normal shopping interaction",
  UNCLEAR: "Interaction unclear",
};
export type SampledFrame = {
  at_seconds: number;
  jpeg_base64: string;
  capturedAt: number;
};
export const INTERACTION_FRESH_MS = 15_000;

/** Bounded ephemeral history. Discontinuities discard sequences rather than joining unrelated moments. */
export class InteractionFrameBuffer {
  private frames: SampledFrame[] = [];
  private previous: { media: number; wall: number } | null = null;
  reset() {
    this.frames = [];
    this.previous = null;
  }
  observe(media: number, wall: number): "wait" | "capture" | "reset" {
    if (!Number.isFinite(media) || media < 0 || !Number.isFinite(wall)) {
      this.reset();
      return "reset";
    }
    const previous = this.previous;
    this.previous = { media, wall };
    if (previous) {
      const elapsed = wall - previous.wall;
      const progress = (media - previous.media) * 1000;
      if (
        elapsed <= 0 ||
        elapsed > 2000 ||
        progress < 0 ||
        progress > 2000 ||
        Math.abs(progress - elapsed) > 750
      ) {
        this.frames = [];
        return "reset";
      }
      if (progress === 0) return "wait";
    }
    const last = this.frames.at(-1);
    return !last || wall - last.capturedAt >= 1250 ? "capture" : "wait";
  }
  add(frame: SampledFrame) {
    const last = this.frames.at(-1);
    if (
      last &&
      (frame.at_seconds <= last.at_seconds ||
        frame.capturedAt <= last.capturedAt)
    )
      this.reset();
    this.frames.push(frame);
    this.frames = this.frames.slice(-6);
  }
  sequence(now: number): SampledFrame[] {
    const frames = this.frames.slice(-4);
    if (
      frames.length < 4 ||
      now - frames[3].capturedAt > 2000 ||
      frames[3].at_seconds - frames[0].at_seconds > 8
    )
      return [];
    return frames.map((frame) => ({ ...frame }));
  }
  get count() {
    return this.frames.length;
  }
}

/** Strict result/source gate; model eligibility alone never authorises a delayed or historical alarm. */
export function freshInteractionAlarm(input: {
  enabled: boolean;
  sameRun: boolean;
  running: boolean;
  visible: boolean;
  playing: boolean;
  seeking: boolean;
  playbackRate: number;
  now: number;
  lastFrameAt: number;
  lastProgressAt: number;
  lastAlarmAt: number;
  result: Pick<
    SavedInteraction,
    | "action"
    | "visibility"
    | "person_visible"
    | "product_visible"
    | "sequence_observed"
    | "alarm_eligible"
  >;
}): boolean {
  const age = input.now - input.lastFrameAt;
  return (
    input.enabled &&
    input.sameRun &&
    input.running &&
    input.visible &&
    input.playing &&
    !input.seeking &&
    input.playbackRate === 1 &&
    Number.isFinite(age) &&
    age >= 0 &&
    age <= INTERACTION_FRESH_MS &&
    input.now - input.lastProgressAt >= 0 &&
    input.now - input.lastProgressAt <= 2000 &&
    input.now - input.lastAlarmAt >= 30_000 &&
    input.result.alarm_eligible &&
    input.result.action === "POSSIBLE_CONCEALMENT" &&
    input.result.visibility === "clear" &&
    input.result.person_visible === true &&
    input.result.product_visible &&
    input.result.sequence_observed
  );
}

export function safeInteractionFrameUrl(
  value: string,
  id: string,
): string | null {
  return /^[a-f0-9-]{36}$/i.test(id) &&
    new RegExp(`^/api/interactions/${id}/frames/[0-5]$`, "i").test(value)
    ? value
    : null;
}

export function captureInteractionFrame(
  video: HTMLVideoElement,
  now: number,
  crop: DetectionRect | null = null,
  preview: HTMLCanvasElement | null = null,
): SampledFrame {
  if (
    video.readyState < 2 ||
    !video.videoWidth ||
    !video.videoHeight ||
    video.paused ||
    video.seeking ||
    video.playbackRate !== 1
  )
    throw new Error("A fresh playing video frame is required.");
  const region = interactionCropPixels(
    video.videoWidth,
    video.videoHeight,
    crop,
  );
  const canvas = document.createElement("canvas");
  canvas.width = region.outputWidth;
  canvas.height = region.outputHeight;
  const context = canvas.getContext("2d");
  if (!context) throw new Error("This browser cannot sample video frames.");
  context.drawImage(
    video,
    region.x,
    region.y,
    region.width,
    region.height,
    0,
    0,
    canvas.width,
    canvas.height,
  );
  if (preview) {
    const scale = Math.min(1, 320 / canvas.width);
    preview.width = Math.max(1, Math.round(canvas.width * scale));
    preview.height = Math.max(1, Math.round(canvas.height * scale));
    preview
      .getContext("2d")
      ?.drawImage(canvas, 0, 0, preview.width, preview.height);
  }
  const jpeg = canvas.toDataURL("image/jpeg", 0.72).split(",")[1];
  if (!jpeg || (jpeg.length * 3) / 4 > 350 * 1024)
    throw new Error(
      "This frame is too large to analyse. Use a smaller camera view.",
    );
  return {
    at_seconds: Math.round(video.currentTime * 1000) / 1000,
    jpeg_base64: jpeg,
    capturedAt: now,
  };
}

/** Crop source pixels before resizing, so one CCTV tile retains its available detail. */
export function interactionCropPixels(
  width: number,
  height: number,
  crop: DetectionRect | null = null,
) {
  if (
    !Number.isFinite(width) ||
    !Number.isFinite(height) ||
    width < 1 ||
    height < 1
  )
    throw new Error("A readable video size is required to select an area.");
  const selected = crop ?? { x: 0, y: 0, width: 1, height: 1 };
  if (
    Object.values(selected).some((value) => !Number.isFinite(value)) ||
    selected.x < 0 ||
    selected.y < 0 ||
    selected.width < 0.05 ||
    selected.height < 0.05 ||
    selected.x + selected.width > 1.000001 ||
    selected.y + selected.height > 1.000001
  )
    throw new Error(
      "Choose an area inside the video, at least 5% wide and high.",
    );
  const x = Math.floor(selected.x * width);
  const y = Math.floor(selected.y * height);
  const regionWidth = Math.min(width - x, Math.round(selected.width * width));
  const regionHeight = Math.min(
    height - y,
    Math.round(selected.height * height),
  );
  if (crop && (regionWidth < 48 || regionHeight < 48))
    throw new Error(
      "The selected area is too small. Include at least 48 source pixels in each direction, with hands and products visible.",
    );
  const scale = Math.min(1, 768 / Math.max(regionWidth, regionHeight));
  return {
    x,
    y,
    width: regionWidth,
    height: regionHeight,
    outputWidth: Math.max(1, Math.round(regionWidth * scale)),
    outputHeight: Math.max(1, Math.round(regionHeight * scale)),
  };
}
