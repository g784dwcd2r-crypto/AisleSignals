/** Local pose observations; no cross-visit identity or finding of theft. */
import type { CameraContext } from "./cameraContext";
export type PosePoint = {
  x: number;
  y: number;
  z?: number;
  visibility?: number;
  presence?: number;
};
export type DetectionRect = {
  x: number;
  y: number;
  width: number;
  height: number;
};
export type LiveEventCode = "REPEATED_HAND_TO_WAIST" | "RESTRICTED_ZONE_ENTRY";
export type LiveSourceKind = "SCREEN_CAPTURE" | "CAMERA" | "RECORDED_VIDEO";
export type LiveDetectionSettings = {
  sensitivity: "balanced" | "sensitive";
  restrictedZone: DetectionRect | null;
};
export type LiveTrack = {
  id: number;
  landmarks: PosePoint[];
  box: DetectionRect;
  status: "normal" | "watch" | "alert";
  label: string;
  quality: number;
};
export type LiveBehaviourEvent = {
  id: string;
  trackId: number;
  code: LiveEventCode;
  label: string;
  detail: string;
  atMs: number;
};
export type LiveEngineResult = {
  tracks: LiveTrack[];
  events: LiveBehaviourEvent[];
};
export type PoseDetector = {
  detect: (
    video: HTMLVideoElement,
    timestampMs: number,
    crop?: DetectionRect | null,
  ) => Promise<PosePoint[][]>;
  close: () => void;
};
export const LIVE_MODEL_VERSION = "mediapipe-pose-lite-f16-v1";
export const LIVE_RULE_VERSION = "pose-rules-v2";

export type LiveEventInput = {
  run_id: string;
  event_id: string;
  source_kind: LiveSourceKind;
  source_label: string;
  camera_context?: CameraContext | null;
  event_code: LiveEventCode;
  track_id: number;
  source_time_seconds: number;
  detected_at: string;
  model_version: typeof LIVE_MODEL_VERSION;
  rule_version: typeof LIVE_RULE_VERSION;
  sound_requested: boolean;
};
export type SavedLiveEvent = Omit<LiveEventInput, "rule_version"> & {
  camera_id?: string;
  camera_label?: string;
  rule_version: "pose-rules-v1" | typeof LIVE_RULE_VERSION;
  id: string;
  label: string;
  detail: string;
  created_at: string;
  acknowledged_at: string | null;
  acknowledged_by: string | null;
};
