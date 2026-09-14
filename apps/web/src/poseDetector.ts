import type { PoseDetector, PosePoint } from "./liveDetectionTypes";
import { poseFrameRegion } from "./poseFrame";

/** A dedicated worker owns the model. Only one bounded frame is in flight. */
export async function createPoseDetector(
  signal?: AbortSignal,
  mode: "VIDEO" | "IMAGE" = "VIDEO",
): Promise<PoseDetector> {
  if (signal?.aborted) throw new Error("Model loading stopped.");
  if (typeof Worker === "undefined" || typeof createImageBitmap === "undefined")
    throw new Error(
      "This browser does not support local video analysis. Use current Chrome or Edge.",
    );
  const worker = new Worker(new URL("./pose.worker.ts", import.meta.url), {
    type: "module",
  });
  let closed = false;
  let sequence = 0;
  let loadingResolve: (() => void) | null = null;
  let loadingReject: ((error: Error) => void) | null = null;
  let pending: {
    id: number;
    resolve: (poses: PosePoint[][]) => void;
    reject: (error: Error) => void;
    timer: ReturnType<typeof setTimeout>;
  } | null = null;
  let capturing = false;
  const close = () => {
    if (closed) return;
    closed = true;
    signal?.removeEventListener("abort", close);
    worker.terminate();
    loadingReject?.(new Error("Model loading stopped."));
    loadingReject = null;
    loadingResolve = null;
    if (pending) {
      clearTimeout(pending.timer);
      pending.reject(new Error("Detection stopped."));
      pending = null;
    }
  };
  const failed = (message: string) => {
    loadingReject?.(new Error(message));
    loadingReject = null;
    if (pending) {
      clearTimeout(pending.timer);
      pending.reject(new Error(message));
      pending = null;
    }
    close();
  };
  worker.onerror = () =>
    failed("The analysis worker failed. Try a current Chrome or Edge browser.");
  worker.onmessage = (event: MessageEvent) => {
    const result = event.data;
    if (closed) return;
    if (result.type === "ready") {
      loadingResolve?.();
      loadingResolve = null;
      loadingReject = null;
    } else if (result.type === "error") {
      failed(result.message ?? "Local analysis failed.");
    } else if (
      result.type === "result" &&
      pending &&
      pending.id === result.id
    ) {
      const current = pending;
      pending = null;
      clearTimeout(current.timer);
      current.resolve(result.poses);
    }
  };
  let timer: ReturnType<typeof setTimeout> | undefined;
  try {
    await new Promise<void>((resolve, reject) => {
      loadingResolve = resolve;
      loadingReject = reject;
      signal?.addEventListener("abort", close, { once: true });
      timer = setTimeout(
        () =>
          failed("The local model took too long to load. Stop and try again."),
        30000,
      );
      worker.postMessage({
        type: "init",
        baseUrl: `${location.origin}/`,
        mode,
      });
    });
  } catch (error) {
    close();
    throw error;
  } finally {
    clearTimeout(timer);
  }
  return {
    close,
    async detect(video, timestampMs, crop = null) {
      if (closed) throw new Error("The detector is closed.");
      if (pending || capturing)
        throw new Error("A frame is already being analysed.");
      if (!video.videoWidth || !video.videoHeight)
        throw new Error("The video has no decoded frame.");
      const region = poseFrameRegion(video.videoWidth, video.videoHeight, crop);
      capturing = true;
      let bitmap: ImageBitmap;
      try {
        bitmap = await createImageBitmap(
          video,
          region.x,
          region.y,
          region.width,
          region.height,
          {
            resizeWidth: region.outputWidth,
            resizeHeight: region.outputHeight,
            resizeQuality: "high",
          },
        );
      } finally {
        capturing = false;
      }
      if (closed) {
        bitmap.close();
        throw new Error("Detection stopped.");
      }
      return new Promise<PosePoint[][]>((resolve, reject) => {
        const id = ++sequence;
        const timeout = setTimeout(
          () =>
            failed(
              "Analysis is too slow on this device. Detection has stopped.",
            ),
          5000,
        );
        pending = { id, resolve, reject, timer: timeout };
        try {
          worker.postMessage({ type: "frame", id, timestampMs, bitmap }, [
            bitmap,
          ]);
        } catch {
          bitmap.close();
          failed("The video frame could not reach the detector.");
        }
      });
    },
  };
}
