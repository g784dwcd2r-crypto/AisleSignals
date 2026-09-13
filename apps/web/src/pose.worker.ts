import { FilesetResolver, PoseLandmarker } from "@mediapipe/tasks-vision";
import type { PosePoint } from "./liveDetectionTypes";

type Request =
  | { type: "init"; baseUrl: string }
  | { type: "frame"; id: number; bitmap: ImageBitmap; timestampMs: number };

let model: PoseLandmarker | null = null;
let lastTimestamp = -1;
let busy = false;

self.onmessage = async (message: MessageEvent<Request>) => {
  const request = message.data;
  if (request.type === "init") {
    try {
      const files = await FilesetResolver.forVisionTasks(
        `${request.baseUrl}vision/wasm`,
        true,
      );
      model = await PoseLandmarker.createFromOptions(files, {
        baseOptions: {
          modelAssetPath: `${request.baseUrl}vision/pose_landmarker_lite.task`,
          delegate: "CPU",
        },
        runningMode: "VIDEO",
        numPoses: 4,
        minPoseDetectionConfidence: 0.5,
        minPosePresenceConfidence: 0.5,
        minTrackingConfidence: 0.5,
        outputSegmentationMasks: false,
      });
      self.postMessage({ type: "ready" });
    } catch (error) {
      console.error("Local pose model initialization failed:", error);
      self.postMessage({
        type: "error",
        message:
          "The local pose model could not start. Rebuild vision assets or try a current Chrome or Edge browser.",
      });
    }
    return;
  }
  try {
    if (
      !model ||
      busy ||
      !Number.isFinite(request.timestampMs) ||
      request.timestampMs <= lastTimestamp
    )
      throw new Error("Invalid or overlapping frame.");
    busy = true;
    lastTimestamp = request.timestampMs;
    const result = model.detectForVideo(request.bitmap, request.timestampMs);
    const poses: PosePoint[][] = result.landmarks.map((landmarks) =>
      landmarks.map(({ x, y, z, visibility }) => ({ x, y, z, visibility })),
    );
    self.postMessage({ type: "result", id: request.id, poses });
  } catch {
    self.postMessage({
      type: "error",
      id: request.id,
      message: "Pose analysis failed. Stop and reconnect the video source.",
    });
  } finally {
    busy = false;
    request.bitmap.close();
  }
};
