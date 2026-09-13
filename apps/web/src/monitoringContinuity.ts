export type PresentedFrame = {
  sequence: number;
  mediaTime: number;
  observedAt: number;
};

export type ContinuityProblem = "frames_stalled" | "clock_gap" | "media_jump";

export const CONTINUITY_LIMITS = {
  frameGapMs: 3500,
  schedulerGapMs: 2000,
  mediaGapSeconds: 1.5,
  watchdogMs: 250,
} as const;

/** Fresh presentation, not an advancing media clock or changed pixels, proves
 * that the browser delivered another frame. A frozen upstream CCTV picture
 * inside an otherwise refreshing screen share still needs an operator check. */
export function watchVideoContinuity(
  video: HTMLVideoElement,
  onProblem: (problem: ContinuityProblem) => void,
) {
  if (
    typeof video.requestVideoFrameCallback !== "function" ||
    typeof video.cancelVideoFrameCallback !== "function"
  )
    throw new Error(
      "This browser cannot verify fresh video frames. Use a current Chrome, Edge or Safari browser.",
    );

  let active = true;
  let callbackId: number | null = null;
  let latest: PresentedFrame | null = null;
  let lastFrameCount = -1;
  let lastCheck = performance.now();
  let lastWallCheck = Date.now();
  let lastPresentation = lastCheck;
  let resolveFirst: (() => void) | undefined;
  let rejectFirst: ((error: Error) => void) | undefined;
  const firstFrame = new Promise<void>((resolve, reject) => {
    resolveFirst = resolve;
    rejectFirst = reject;
  });
  // The watcher can fail while the caller is still awaiting video.play().
  void firstFrame.catch(() => undefined);

  function close() {
    if (!active) return;
    active = false;
    clearInterval(watchdog);
    if (callbackId !== null) video.cancelVideoFrameCallback(callbackId);
    callbackId = null;
    rejectFirst?.(new Error("Video monitoring stopped."));
    resolveFirst = undefined;
    rejectFirst = undefined;
    latest = null;
  }
  function fail(problem: ContinuityProblem) {
    if (!active) return;
    close();
    onProblem(problem);
  }
  function clockIsContinuous() {
    const now = performance.now();
    const wallNow = Date.now();
    const elapsed = now - lastCheck;
    const wallElapsed = wallNow - lastWallCheck;
    lastCheck = now;
    lastWallCheck = wallNow;
    // Date.now also catches sleep on platforms where performance.now pauses.
    if (
      elapsed < 0 ||
      wallElapsed < -1000 ||
      elapsed > CONTINUITY_LIMITS.schedulerGapMs ||
      wallElapsed > CONTINUITY_LIMITS.schedulerGapMs
    ) {
      fail("clock_gap");
      return false;
    }
    return true;
  }
  const presented: VideoFrameRequestCallback = (_now, metadata) => {
    callbackId = null;
    if (!active || !clockIsContinuous()) return;
    const now = performance.now();
    if (
      Number.isFinite(metadata.mediaTime) &&
      Number.isFinite(metadata.presentedFrames) &&
      metadata.presentedFrames > lastFrameCount
    ) {
      if (
        latest &&
        (metadata.mediaTime < latest.mediaTime ||
          metadata.mediaTime - latest.mediaTime >
            CONTINUITY_LIMITS.mediaGapSeconds ||
          now - lastPresentation > CONTINUITY_LIMITS.frameGapMs)
      ) {
        fail("media_jump");
        return;
      }
      lastFrameCount = metadata.presentedFrames;
      lastPresentation = now;
      latest = {
        sequence: (latest?.sequence ?? 0) + 1,
        mediaTime: metadata.mediaTime,
        observedAt: now,
      };
      resolveFirst?.();
      resolveFirst = undefined;
      rejectFirst = undefined;
    }
    callbackId = video.requestVideoFrameCallback(presented);
  };
  const watchdog = setInterval(() => {
    if (!active || !clockIsContinuous()) return;
    if (performance.now() - lastPresentation >= CONTINUITY_LIMITS.frameGapMs)
      fail("frames_stalled");
  }, CONTINUITY_LIMITS.watchdogMs);
  callbackId = video.requestVideoFrameCallback(presented);
  return {
    firstFrame,
    close,
    latest: () => latest,
    // Read-only so alarm/job completions can reject a stale session even when
    // they are delivered before the watchdog's first callback after sleep.
    isFresh(maxAgeMs: number) {
      return (
        active &&
        latest !== null &&
        performance.now() - latest.observedAt <= maxAgeMs &&
        performance.now() - lastCheck <= CONTINUITY_LIMITS.schedulerGapMs &&
        Date.now() - lastWallCheck <= CONTINUITY_LIMITS.schedulerGapMs &&
        Date.now() - lastWallCheck >= -1000
      );
    },
  };
}
