import type { DetectionRect, LiveSourceKind } from "./liveDetectionTypes";
import type { CameraContext } from "./cameraContext";

export type InteractionAction =
  | "TAKE_PRODUCT"
  | "RETURN_PRODUCT"
  | "PLACE_IN_BASKET"
  | "POSSIBLE_CONCEALMENT"
  | "NORMAL_SHOPPING"
  | "UNCLEAR";
export type InteractionReview = "USEFUL" | "NORMAL_SHOPPING" | "UNCLEAR";
export type CameraCalibration = Readonly<{
  schema_version: "1.0";
  entrance_zone_confirmed: true;
  exit_zone_confirmed: true;
  cashier_zone_confirmed: true;
  shelf_zones_confirmed: true;
}>;
export type SavedInteraction = {
  id: string;
  version: number;
  run_id: string;
  source_kind: LiveSourceKind;
  source_label: string;
  camera_context?: CameraContext | null;
  camera_id?: string;
  camera_label?: string;
  camera_calibration?: CameraCalibration;
  camera_calibration_status?: "READY" | "MISSING";
  alarm_blocked_reason?: "CAMERA_CALIBRATION_REQUIRED";
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
  evidence_strength?:
    "STRONG_RULE_MATCH" | "PARTIAL_RULE_MATCH" | "INSUFFICIENT_RULE_MATCH";
  evidence_strength_note?: string;
  sampled_window_interpretation?: {
    schema_version: "sampled-window-v1";
    decision:
      | "OBSERVING"
      | "NORMAL_RESOLVED"
      | "REVIEW_REQUIRED"
      | "HIGH_ATTENTION"
      | "ABSTAIN";
    state:
      | "ON_SHELF"
      | "IN_HAND"
      | "IN_BASKET"
      | "CONCEALED_OBSERVED"
      | "PURCHASED"
      | "RETURNED"
      | "UNACCOUNTED";
    complete_custody_alarm_eligible: false;
    reasons: string[];
    timeline: {
      frame_indices: number[];
      assertion: InteractionAction;
      source: "single_local_vlm_response";
      explanation: string;
    }[];
    limitations: string[];
  };
  inference_ms: number;
  frames: { at_seconds: number; url: string }[];
  review: null | { outcome: InteractionReview; note: string };
};

const COMPLETE_CAMERA_CALIBRATION: CameraCalibration = Object.freeze({
  schema_version: "1.0",
  entrance_zone_confirmed: true,
  exit_zone_confirmed: true,
  cashier_zone_confirmed: true,
  shelf_zones_confirmed: true,
});
export const confirmedCameraCalibration = () => COMPLETE_CAMERA_CALIBRATION;

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
  width: number;
  height: number;
  sourceWidth: number;
  sourceHeight: number;
};
export const INTERACTION_FRESH_MS = 15_000;
export const INTERACTION_ALARM_COOLDOWN_MS = 30_000;
export const ACKNOWLEDGED_CAMERA_QUIET_MS = 120_000;

export type AttentionDecision =
  | "REQUEST_SOUND"
  | "ALREADY_DELIVERED"
  | "GLOBAL_COOLDOWN"
  | "ACKNOWLEDGED_CAMERA_QUIET"
  | "INVALID";

/** Sound suppression never discards the saved observation staff can review. */
export class InteractionAttentionPolicy {
  private static readonly MAX_DELIVERED_IDS = 1000;
  private lastSoundAt = -Infinity;
  private delivered = new Set<string>();
  private quietUntil = new Map<string, number>();

  check(id: string, camera: string, now: number): AttentionDecision {
    if (!id || !camera || !Number.isFinite(now)) return "INVALID";
    if (this.delivered.has(id)) return "ALREADY_DELIVERED";
    if (now < (this.quietUntil.get(camera) ?? -Infinity))
      return "ACKNOWLEDGED_CAMERA_QUIET";
    if (now - this.lastSoundAt < INTERACTION_ALARM_COOLDOWN_MS)
      return "GLOBAL_COOLDOWN";
    return "REQUEST_SOUND";
  }

  recordDelivery(id: string, now: number) {
    if (!id || !Number.isFinite(now) || this.delivered.has(id)) return false;
    if (this.delivered.size >= InteractionAttentionPolicy.MAX_DELIVERED_IDS) {
      const oldest = this.delivered.values().next().value;
      if (oldest !== undefined) this.delivered.delete(oldest);
    }
    this.delivered.add(id);
    this.lastSoundAt = now;
    return true;
  }

  decide(id: string, camera: string, now: number): AttentionDecision {
    const decision = this.check(id, camera, now);
    if (decision !== "REQUEST_SOUND") return decision;
    return this.recordDelivery(id, now) ? "REQUEST_SOUND" : "INVALID";
  }

  acknowledge(camera: string, now: number) {
    if (!camera || !Number.isFinite(now)) return false;
    this.quietUntil.set(camera, now + ACKNOWLEDGED_CAMERA_QUIET_MS);
    return true;
  }

  resetRun() {
    this.lastSoundAt = -Infinity;
    this.quietUntil.clear();
    // Keep the bounded recent ID history so a late result cannot replay after restart.
  }
}

/** A browser audio callback is not evidence that a member of staff heard it. */
export class InteractionAlarmCommission {
  private static readonly MAX_DELIVERED_IDS = 1000;
  private revision = 0;
  private context = "";
  private testedAt: number | null = null;
  private confirmed = false;
  private armed = false;
  private recordedAllowed = false;
  private delivered = new Set<string>();

  invalidate() {
    this.revision++;
    this.context = "";
    this.testedAt = null;
    this.confirmed = false;
    this.armed = false;
    this.recordedAllowed = false;
    // Never replay a delivered job after disarming or recommissioning.
  }
  beginTest(context: string) {
    this.invalidate();
    this.context = context;
    return this.revision;
  }
  finishTest(revision: number, context: string, now: number) {
    if (
      revision !== this.revision ||
      !context ||
      context !== this.context ||
      !Number.isFinite(now)
    )
      return false;
    this.testedAt = now;
    return true;
  }
  confirm(context: string, now: number) {
    if (
      !context ||
      context !== this.context ||
      this.testedAt === null ||
      !Number.isFinite(now) ||
      now < this.testedAt ||
      now - this.testedAt > 60_000
    )
      return false;
    this.confirmed = true;
    return true;
  }
  arm(context: string, recordedAllowed: boolean) {
    this.armed = this.confirmed && !!context && context === this.context;
    this.recordedAllowed = recordedAllowed;
    return this.armed;
  }
  matches(context: string) {
    return !!this.context && this.context === context;
  }
  currentTest(revision: number, context: string) {
    return revision === this.revision && this.matches(context);
  }
  get active() {
    return !!this.context;
  }
  checkClaim(context: string, id: string, source: LiveSourceKind) {
    if (
      !this.armed ||
      !this.confirmed ||
      !this.matches(context) ||
      !id ||
      this.delivered.has(id) ||
      (source === "RECORDED_VIDEO" && !this.recordedAllowed)
    )
      return false;
    return true;
  }
  recordClaim(id: string) {
    if (!id || this.delivered.has(id)) return false;
    if (this.delivered.size >= InteractionAlarmCommission.MAX_DELIVERED_IDS) {
      const oldest = this.delivered.values().next().value;
      if (oldest !== undefined) this.delivered.delete(oldest);
    }
    this.delivered.add(id);
    return true;
  }
  claim(context: string, id: string, source: LiveSourceKind) {
    return this.checkClaim(context, id, source) && this.recordClaim(id);
  }
}

/** Checks both gates without consuming the ID; commit only after sound playback succeeds. */
export function prepareInteractionSound(
  attention: InteractionAttentionPolicy,
  commission: InteractionAlarmCommission,
  input: {
    id: string;
    camera: string;
    now: number;
    context: string;
    source: LiveSourceKind;
  },
) {
  if (attention.check(input.id, input.camera, input.now) !== "REQUEST_SOUND")
    return null;
  if (!commission.checkClaim(input.context, input.id, input.source))
    return null;
  let pending = true;
  return () => {
    if (!pending) return false;
    pending = false;
    return (
      commission.recordClaim(input.id) &&
      attention.recordDelivery(input.id, input.now)
    );
  };
}

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
  /** Partial progress is useful before a complete sequence can be submitted. */
  snapshot(now: number): SampledFrame[] {
    const frames = this.frames.slice(-4);
    const last = frames.at(-1);
    if (
      !last ||
      !Number.isFinite(now) ||
      now < last.capturedAt ||
      now - last.capturedAt > 2000 ||
      last.at_seconds - frames[0].at_seconds > 8
    )
      return [];
    return frames.map((frame) => ({ ...frame }));
  }
  sequence(now: number): SampledFrame[] {
    const frames = this.snapshot(now);
    return frames.length === 4 ? frames : [];
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
  const escapedId = id.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  return /^[a-f0-9-]{36}$/i.test(id) &&
    new RegExp(`^/api/interactions/${escapedId}/frames/[0-5]$`, "i").test(value)
    ? value
    : null;
}

export function safeIncidentFrameUrl(
  value: string,
  incidentId: string,
): string | null {
  const escapedId = incidentId.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  return /^[a-f0-9-]{36}$/i.test(incidentId) &&
    new RegExp(
      `^/api/incidents/${escapedId}/interaction-source/frames/[0-5]/views/[a-f0-9-]{36}$`,
      "i",
    ).test(value)
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
    width: canvas.width,
    height: canvas.height,
    sourceWidth: region.width,
    sourceHeight: region.height,
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
