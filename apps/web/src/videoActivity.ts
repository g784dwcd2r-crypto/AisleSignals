/** Bounded visual-change testing only: no object, identity or theft inference. */
export const VIDEO_LIMITS = Object.freeze({
  maxBytes: 250 * 1024 * 1024,
  maxDuration: 600,
  maxPixels: 3840 * 2160,
  maxDimension: 4096,
  sampleInterval: 0.5,
  maxSamples: 1200,
  maxResults: 100,
  pixelDelta: 24,
  activeChangedRatio: 0.03,
  quietSeconds: 2,
  maxSampleGap: 0.75,
});

export interface ActivitySample {
  /** Seconds within this file, rather than wall-clock time. */
  time: number;
  changedRatio: number;
}

export interface ActivitySegment {
  start: number;
  end: number;
  peakChangedRatio: number;
}

export function validateVideoFile(file: {
  name: string;
  size: number;
  type: string;
}): string | null {
  if (!Number.isSafeInteger(file.size) || file.size <= 0)
    return "Choose a video file that is not empty.";
  if (file.size > VIDEO_LIMITS.maxBytes)
    return "Choose a video no larger than 250 MiB.";
  const type = file.type.split(";", 1)[0].trim().toLowerCase();
  if (
    !/\.(mp4|webm)$/i.test(file.name) &&
    type !== "video/mp4" &&
    type !== "video/webm"
  )
    return "Choose an MP4 or WebM video. Codec support depends on your browser.";
  return null;
}

export function validateVideoMetadata(video: {
  duration: number;
  width: number;
  height: number;
}): string | null {
  if (!Number.isFinite(video.duration) || video.duration < 0.5)
    return "The video needs a readable duration of at least half a second.";
  if (video.duration > VIDEO_LIMITS.maxDuration)
    return "Choose a video lasting no more than 10 minutes.";
  if (
    !Number.isSafeInteger(video.width) ||
    !Number.isSafeInteger(video.height) ||
    video.width <= 0 ||
    video.height <= 0
  )
    return "The browser could not read valid video dimensions.";
  if (
    video.width > VIDEO_LIMITS.maxDimension ||
    video.height > VIDEO_LIMITS.maxDimension ||
    video.width * video.height > VIDEO_LIMITS.maxPixels
  )
    return "Choose a video up to 3840 × 2160 pixels, with neither side over 4096 pixels.";
  return null;
}

/** Convert a small, already-decoded browser frame; callers should downsample first. */
export function rgbaToLuminance(
  rgba: Uint8ClampedArray | Uint8Array,
): Uint8Array {
  if (
    rgba.length === 0 ||
    rgba.length % 4 !== 0 ||
    rgba.length > VIDEO_LIMITS.maxPixels * 4
  )
    throw new RangeError("Expected a bounded, nonempty RGBA frame.");
  const luminance = new Uint8Array(rgba.length / 4);
  for (
    let pixel = 0, offset = 0;
    pixel < luminance.length;
    pixel++, offset += 4
  )
    luminance[pixel] =
      (77 * rgba[offset] +
        150 * rgba[offset + 1] +
        29 * rgba[offset + 2] +
        128) >>
      8;
  return luminance;
}

/** Lighting, camera movement and ordinary activity can all increase this ratio. */
export function changedPixelRatio(
  previous: Uint8Array,
  current: Uint8Array,
): number {
  if (
    previous.length === 0 ||
    previous.length !== current.length ||
    previous.length > VIDEO_LIMITS.maxPixels
  )
    throw new RangeError("Expected equally sized, bounded luminance frames.");
  let changed = 0;
  for (let pixel = 0; pixel < previous.length; pixel++)
    if (Math.abs(current[pixel] - previous[pixel]) >= VIDEO_LIMITS.pixelDelta)
      changed++;
  return changed / previous.length;
}

/**
 * Group sampled visual changes without treating missing samples as observation.
 * Two adjacent active samples start a result; two seconds quiet close it.
 * A seek, gap or invalid sample flushes a confirmed result and resets continuity.
 * A segment spans the first comparison's start through the last active sample.
 * The final confirmed open segment is included for partial/cancelled test results.
 */
export function groupActivitySamples(
  samples: readonly ActivitySample[],
): ActivitySegment[] {
  const segments: ActivitySegment[] = [];
  let pending: ActivitySegment | null = null;
  let consecutiveActive = 0;
  let confirmed = false;
  let previousTime: number | null = null;

  const flush = () => {
    if (pending && confirmed && segments.length < VIDEO_LIMITS.maxResults)
      segments.push(pending);
    pending = null;
    consecutiveActive = 0;
    confirmed = false;
  };

  for (
    let index = 0;
    index < Math.min(samples.length, VIDEO_LIMITS.maxSamples);
    index++
  ) {
    if (segments.length >= VIDEO_LIMITS.maxResults) break;
    const { time, changedRatio } = samples[index];
    if (
      !Number.isFinite(time) ||
      time < 0 ||
      time > VIDEO_LIMITS.maxDuration ||
      !Number.isFinite(changedRatio) ||
      changedRatio < 0 ||
      changedRatio > 1
    ) {
      flush();
      previousTime = null;
      continue;
    }
    if (
      previousTime !== null &&
      (time <= previousTime || time - previousTime > VIDEO_LIMITS.maxSampleGap)
    )
      flush();
    previousTime = time;

    if (changedRatio >= VIDEO_LIMITS.activeChangedRatio) {
      if (!pending)
        pending = {
          start: Math.max(0, time - VIDEO_LIMITS.sampleInterval),
          end: time,
          peakChangedRatio: changedRatio,
        };
      else {
        pending.end = time;
        pending.peakChangedRatio = Math.max(
          pending.peakChangedRatio,
          changedRatio,
        );
      }
      consecutiveActive++;
      if (consecutiveActive >= 2) confirmed = true;
    } else if (!confirmed) {
      pending = null;
      consecutiveActive = 0;
    } else if (pending && time - pending.end >= VIDEO_LIMITS.quietSeconds) {
      flush();
    }
  }
  flush();
  return segments;
}
