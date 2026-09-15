import {
  FilesetResolver,
  ObjectDetector,
  PoseLandmarker,
} from "@mediapipe/tasks-vision";
import type {
  DetectedPerson,
  DetectionRect,
  PosePoint,
} from "./liveDetectionTypes";

type Request =
  | { type: "init"; baseUrl: string; mode?: "VIDEO" | "IMAGE" }
  | { type: "frame"; id: number; bitmap: ImageBitmap; timestampMs: number };

let pose: PoseLandmarker | null = null;
let people: ObjectDetector | null = null;
let lastTimestamp = -1;
let busy = false;
let mode: "VIDEO" | "IMAGE" = "VIDEO";

const boundedBox = (
  x: number,
  y: number,
  width: number,
  height: number,
  frameWidth: number,
  frameHeight: number,
): DetectionRect | null => {
  if (
    ![x, y, width, height].every(Number.isFinite) ||
    width < 16 ||
    height < 24
  )
    return null;
  const left = Math.max(0, x),
    top = Math.max(0, y);
  const right = Math.min(frameWidth, x + width),
    bottom = Math.min(frameHeight, y + height);
  if (right <= left || bottom <= top) return null;
  return {
    x: left / frameWidth,
    y: top / frameHeight,
    width: (right - left) / frameWidth,
    height: (bottom - top) / frameHeight,
  };
};

function gatedPose(
  bitmap: ImageBitmap,
  detectorBox: DetectionRect,
): PosePoint[] | null {
  if (!pose) return null;
  const padX = detectorBox.width * 0.08,
    padY = detectorBox.height * 0.06;
  const x = Math.max(0, detectorBox.x - padX),
    y = Math.max(0, detectorBox.y - padY);
  const right = Math.min(1, detectorBox.x + detectorBox.width + padX),
    bottom = Math.min(1, detectorBox.y + detectorBox.height + padY);
  const pixelWidth = Math.max(1, Math.round((right - x) * bitmap.width));
  const pixelHeight = Math.max(1, Math.round((bottom - y) * bitmap.height));
  const ratio = Math.min(1, 384 / Math.max(pixelWidth, pixelHeight));
  const canvas = new OffscreenCanvas(
    Math.max(1, Math.round(pixelWidth * ratio)),
    Math.max(1, Math.round(pixelHeight * ratio)),
  );
  const context = canvas.getContext("2d", { alpha: false });
  if (!context) return null;
  context.drawImage(
    bitmap,
    Math.round(x * bitmap.width),
    Math.round(y * bitmap.height),
    pixelWidth,
    pixelHeight,
    0,
    0,
    canvas.width,
    canvas.height,
  );
  const landmarks = pose.detect(canvas).landmarks[0];
  if (!landmarks) return null;
  return landmarks.map(({ x: localX, y: localY, z, visibility }) => ({
    x: x + localX * (right - x),
    y: y + localY * (bottom - y),
    z,
    visibility,
  }));
}

self.onmessage = async (message: MessageEvent<Request>) => {
  const request = message.data;
  if (request.type === "init") {
    try {
      mode = request.mode === "IMAGE" ? "IMAGE" : "VIDEO";
      const files = await FilesetResolver.forVisionTasks(
        `${request.baseUrl}vision/wasm`,
        true,
      );
      [people, pose] = await Promise.all([
        ObjectDetector.createFromOptions(files, {
          baseOptions: {
            modelAssetPath: `${request.baseUrl}vision/efficientdet_lite0_uint8.tflite`,
            delegate: "CPU",
          },
          runningMode: mode,
          categoryAllowlist: ["person"],
          maxResults: 6,
          scoreThreshold: 0.4,
        }),
        PoseLandmarker.createFromOptions(files, {
          baseOptions: {
            modelAssetPath: `${request.baseUrl}vision/pose_landmarker_lite.task`,
            delegate: "CPU",
          },
          runningMode: "IMAGE",
          numPoses: 1,
          minPoseDetectionConfidence: 0.5,
          minPosePresenceConfidence: 0.5,
          minTrackingConfidence: 0.5,
          outputSegmentationMasks: false,
        }),
      ]);
      self.postMessage({ type: "ready" });
    } catch (error) {
      console.error("Local person/pose model initialization failed:", error);
      self.postMessage({
        type: "error",
        message:
          "The local person detector could not start. Rebuild vision assets or try a current Chrome or Edge browser.",
      });
    }
    return;
  }
  try {
    if (
      !people ||
      !pose ||
      busy ||
      !Number.isFinite(request.timestampMs) ||
      request.timestampMs <= lastTimestamp
    )
      throw new Error("Invalid or overlapping frame.");
    busy = true;
    lastTimestamp = request.timestampMs;
    const result =
      mode === "IMAGE"
        ? people.detect(request.bitmap)
        : people.detectForVideo(request.bitmap, request.timestampMs);
    const persons: DetectedPerson[] = result.detections
      .map((detection) => {
        const category = detection.categories.find(
          (item) =>
            item.categoryName === "person" || item.displayName === "person",
        );
        const raw = detection.boundingBox;
        const box = raw
          ? boundedBox(
              raw.originX,
              raw.originY,
              raw.width,
              raw.height,
              request.bitmap.width,
              request.bitmap.height,
            )
          : null;
        if (!category || !Number.isFinite(category.score) || !box || !raw)
          return null;
        const subjectPixels = Math.round(
          box.width * request.bitmap.width * box.height * request.bitmap.height,
        );
        const edgeTruncated =
          raw.originX <= 1 ||
          raw.originY <= 1 ||
          raw.originX + raw.width >= request.bitmap.width - 1 ||
          raw.originY + raw.height >= request.bitmap.height - 1;
        const tooSmall =
          box.width * request.bitmap.width < 32 ||
          box.height * request.bitmap.height < 72 ||
          subjectPixels < 4_000;
        const visibilityState = tooSmall
          ? "too_small"
          : edgeTruncated
            ? "edge_truncated"
            : "sufficient";
        return {
          box,
          detectorScore: category.score,
          subjectPixels,
          visibilityState,
          landmarks:
            visibilityState === "sufficient"
              ? gatedPose(request.bitmap, box)
              : null,
        };
      })
      .filter((item): item is DetectedPerson => item !== null)
      .sort((a, b) => b.detectorScore - a.detectorScore)
      .slice(0, 6);
    self.postMessage({ type: "result", id: request.id, persons });
  } catch (error) {
    console.error("Person-gated pose analysis failed:", error);
    self.postMessage({
      type: "error",
      id: request.id,
      message:
        "Person-gated analysis failed. Stop and reconnect the video source.",
    });
  } finally {
    busy = false;
    request.bitmap.close();
  }
};
