import type {
  DetectedPerson,
  DetectionRect,
  LiveBehaviourEvent,
  LiveDetectionSettings,
  LiveEngineResult,
  LiveEventCode,
  LiveTrack,
  PosePoint,
} from "./liveDetectionTypes";
import { AnonymousPersonTracker } from "./anonymousPersonTracker";

/** MediaPipe's body landmarks, without face connections or identity features. */
export const POSE_CONNECTIONS: readonly (readonly [number, number])[] = [
  [11, 12],
  [11, 13],
  [13, 15],
  [12, 14],
  [14, 16],
  [11, 23],
  [12, 24],
  [23, 24],
  [23, 25],
  [25, 27],
  [24, 26],
  [26, 28],
  [27, 29],
  [29, 31],
  [28, 30],
  [30, 32],
];

const MIN_VISIBILITY = 0.65;
const MAX_FRAME_GAP_MS = 1_200;
const TRACK_RETENTION_MS = MAX_FRAME_GAP_MS;
const CYCLE_WINDOW_MS = 14_000;
const REACH_TIMEOUT_MS = 5_000;
const COOLDOWN_MS = 20_000;
const ALERT_DISPLAY_MS = 4_000;
const MAX_POSES = 12;
const TORSO = [11, 12, 23, 24] as const;
const ARM_CHAINS = [
  [11, 13, 15],
  [12, 14, 16],
] as const;

type Point = { x: number; y: number };
type Observation = {
  landmarks: PosePoint[];
  center: Point;
  hips: Point;
  scale: number;
  quality: number;
  box: DetectionRect;
  duplicate: boolean;
  detectorScore: number;
  poseUsable: boolean;
  visibilityState: DetectedPerson["visibilityState"];
};
type HandState = {
  phase: "idle" | "reaching" | "returning" | "waist" | "leave";
  since: number;
  samples: number;
  reachedAt: number;
  cycles: number[];
};
type TrackState = {
  id: number;
  observed: Observation;
  displayed: PosePoint[];
  lastSeenAt: number;
  hands: [HandState, HandState];
  zoneSince: number | null;
  zoneSamples: number;
  zoneLatched: boolean;
  lastEvents: Partial<Record<LiveEventCode, number>>;
  alertUntil: number;
  alertLabel: string;
};

const distance = (a: Point, b: Point, aspectRatio: number) =>
  Math.hypot((a.x - b.x) * aspectRatio, a.y - b.y);
const midpoint = (a: Point, b: Point): Point => ({
  x: (a.x + b.x) / 2,
  y: (a.y + b.y) / 2,
});

function quality(point: PosePoint | undefined): number {
  if (
    !point ||
    !Number.isFinite(point.x) ||
    !Number.isFinite(point.y) ||
    point.x < 0 ||
    point.x > 1 ||
    point.y < 0 ||
    point.y > 1 ||
    !Number.isFinite(point.visibility) ||
    point.visibility! < 0 ||
    point.visibility! > 1 ||
    (point.presence !== undefined &&
      (!Number.isFinite(point.presence) ||
        point.presence < 0 ||
        point.presence > 1))
  )
    return 0;
  return Math.min(point.visibility!, point.presence ?? 1);
}

function insideDetectorBox(
  point: PosePoint | undefined,
  box: DetectionRect,
  margin = 0.08,
): boolean {
  if (quality(point) < MIN_VISIBILITY) return false;
  const marginX = box.width * margin;
  const marginY = box.height * margin;
  return (
    point!.x >= box.x - marginX &&
    point!.x <= box.x + box.width + marginX &&
    point!.y >= box.y - marginY &&
    point!.y <= box.y + box.height + marginY
  );
}

function observe(
  landmarks: PosePoint[],
  aspectRatio: number,
  detectorBox?: DetectionRect,
  detectorScore = 1,
): Observation | null {
  if (!Array.isArray(landmarks) || landmarks.length < 33) return null;
  const torsoQuality = TORSO.map((index) => quality(landmarks[index]));
  if (torsoQuality.some((value) => value < MIN_VISIBILITY)) return null;
  const shoulders = midpoint(landmarks[11], landmarks[12]);
  const hips = midpoint(landmarks[23], landmarks[24]);
  const torsoHeight = distance(shoulders, hips, aspectRatio);
  const shoulderWidth = distance(landmarks[11], landmarks[12], aspectRatio);
  const hipWidth = distance(landmarks[23], landmarks[24], aspectRatio);
  const verticalTorso = hips.y - shoulders.y;

  // PoseLandmarker can occasionally fit high-confidence landmarks to shelving,
  // products or screen furniture. Require a coherent upright torso and at
  // least one observed arm before presenting a result as a person. This is a
  // conservative person gate, not an identity or intent classifier.
  if (
    torsoHeight < 0.045 ||
    torsoHeight > 0.65 ||
    verticalTorso < torsoHeight * 0.55 ||
    Math.abs(landmarks[11].y - landmarks[12].y) > torsoHeight * 0.35 ||
    Math.abs(landmarks[23].y - landmarks[24].y) > torsoHeight * 0.35 ||
    shoulderWidth < torsoHeight * 0.22 ||
    shoulderWidth > torsoHeight * 1.5 ||
    hipWidth < torsoHeight * 0.14 ||
    hipWidth > torsoHeight * 1.35
  )
    return null;
  const coherentArm = ARM_CHAINS.some(([shoulder, elbow, wrist]) => {
    if (
      [shoulder, elbow, wrist].some(
        (index) => quality(landmarks[index]) < MIN_VISIBILITY,
      )
    )
      return false;
    const upper = distance(landmarks[shoulder], landmarks[elbow], aspectRatio);
    const lower = distance(landmarks[elbow], landmarks[wrist], aspectRatio);
    return (
      upper >= torsoHeight * 0.12 &&
      upper <= torsoHeight * 1.45 &&
      lower >= torsoHeight * 0.12 &&
      lower <= torsoHeight * 1.45
    );
  });
  if (!coherentArm) return null;
  const scale = Math.max(torsoHeight, shoulderWidth);
  // Very small/degenerate poses cannot support reliable hand-to-body geometry.
  if (scale < 0.045 || scale > 0.7) return null;
  const accepted = landmarks
    .slice(0, 33)
    .map((point) =>
      quality(point) >= MIN_VISIBILITY
        ? { ...point }
        : { ...point, visibility: 0, presence: 0 },
    );
  const hide = (index: number) => {
    accepted[index] = { ...accepted[index], visibility: 0, presence: 0 };
  };
  // Generous torso-relative bone limits reject a confident foot/elbow attached
  // to a distant counter. Rejected parents also suppress downstream joints.
  // These are observed-point exclusions, never reconstructed body positions.
  for (const [parent, child, maximum] of [
    [11, 13, 1.3],
    [12, 14, 1.3],
    [13, 15, 1.3],
    [14, 16, 1.3],
    [15, 17, 0.5],
    [15, 19, 0.5],
    [15, 21, 0.5],
    [16, 18, 0.5],
    [16, 20, 0.5],
    [16, 22, 0.5],
    [23, 25, 1.8],
    [24, 26, 1.8],
    [25, 27, 1.8],
    [26, 28, 1.8],
    [27, 29, 0.7],
    [27, 31, 0.7],
    [28, 30, 0.7],
    [28, 32, 0.7],
  ] as const) {
    if (
      quality(accepted[parent]) < MIN_VISIBILITY ||
      quality(accepted[child]) < MIN_VISIBILITY ||
      distance(accepted[parent], accepted[child], aspectRatio) > scale * maximum
    )
      hide(child);
  }
  for (let index = 0; index <= 10; index++) {
    if (
      quality(accepted[index]) < MIN_VISIBILITY ||
      distance(accepted[index], shoulders, aspectRatio) > scale * 1.15
    )
      hide(index);
  }
  return {
    landmarks: accepted,
    center: midpoint(shoulders, hips),
    hips,
    scale,
    quality:
      torsoQuality.reduce((total, value) => total + value, 0) / TORSO.length,
    box: detectorBox ?? bounds(accepted, scale, aspectRatio),
    duplicate: false,
    detectorScore,
    poseUsable: true,
    visibilityState: "sufficient",
  };
}

function observePerson(
  person: DetectedPerson,
  aspectRatio: number,
): Observation | null {
  const { box, detectorScore, subjectPixels, visibilityState } = person;
  if (
    ![box.x, box.y, box.width, box.height, detectorScore].every(
      Number.isFinite,
    ) ||
    box.x < 0 ||
    box.y < 0 ||
    box.width <= 0 ||
    box.height <= 0 ||
    box.x + box.width > 1.000001 ||
    box.y + box.height > 1.000001 ||
    detectorScore < 0.4 ||
    detectorScore > 1 ||
    !Number.isSafeInteger(subjectPixels) ||
    subjectPixels < 1 ||
    subjectPixels > 16_384 * 16_384 ||
    !["sufficient", "edge_truncated", "too_small"].includes(visibilityState)
  )
    return null;
  if (person.landmarks && visibilityState === "sufficient") {
    const observed = observe(person.landmarks, aspectRatio, box, detectorScore);
    if (!observed)
      return {
        landmarks: [],
        center: { x: box.x + box.width / 2, y: box.y + box.height / 2 },
        hips: { x: box.x + box.width / 2, y: box.y + box.height * 0.6 },
        scale: box.height,
        quality: detectorScore,
        box: { ...box },
        duplicate: false,
        detectorScore,
        poseUsable: false,
        visibilityState,
      };
    const torsoInside = TORSO.every((index) =>
      insideDetectorBox(observed.landmarks[index], box),
    );
    const armInside = ARM_CHAINS.some((chain) =>
      chain.every((index) =>
        insideDetectorBox(observed.landmarks[index], box),
      ),
    );
    const shoulderSpan = Math.abs(
      observed.landmarks[11].x - observed.landmarks[12].x,
    );
    const torsoSpan = Math.abs(
      midpoint(observed.landmarks[11], observed.landmarks[12]).y -
        midpoint(observed.landmarks[23], observed.landmarks[24]).y,
    );
    if (
      !insideDetectorBox(
        { ...observed.center, visibility: 1, presence: 1 },
        box,
      ) ||
      !torsoInside ||
      !armInside ||
      torsoSpan < box.height * 0.14 ||
      torsoSpan > box.height * 0.75 ||
      shoulderSpan < box.width * 0.1 ||
      shoulderSpan > box.width * 0.95
    )
      observed.poseUsable = false;
    observed.visibilityState = visibilityState;
    return observed;
  }
  return {
    landmarks: [],
    center: { x: box.x + box.width / 2, y: box.y + box.height / 2 },
    hips: { x: box.x + box.width / 2, y: box.y + box.height * 0.6 },
    scale: box.height,
    quality: detectorScore,
    box: { ...box },
    duplicate: false,
    detectorScore,
    poseUsable: false,
    visibilityState,
  };
}

function bounds(
  landmarks: PosePoint[],
  scale: number,
  aspectRatio: number,
): DetectionRect {
  const visible = landmarks.filter((point) => quality(point) >= MIN_VISIBILITY);
  const left = Math.max(
    0,
    Math.min(...visible.map((point) => point.x)) - (scale * 0.12) / aspectRatio,
  );
  const top = Math.max(
    0,
    Math.min(...visible.map((point) => point.y)) - scale * 0.12,
  );
  const right = Math.min(
    1,
    Math.max(...visible.map((point) => point.x)) + (scale * 0.12) / aspectRatio,
  );
  const bottom = Math.min(
    1,
    Math.max(...visible.map((point) => point.y)) + scale * 0.12,
  );
  return { x: left, y: top, width: right - left, height: bottom - top };
}

function distinctObservations(
  observations: Observation[],
  aspectRatio: number,
): Observation[] {
  const result: Observation[] = [];
  for (const observation of observations) {
    const duplicateIndex = result.findIndex((other) => {
      const scale = Math.min(observation.scale, other.scale);
      const ratio = observation.scale / other.scale;
      return (
        ratio >= 0.9 &&
        ratio <= 1.1 &&
        TORSO.every(
          (index) =>
            distance(
              observation.landmarks[index],
              other.landmarks[index],
              aspectRatio,
            ) <=
            scale * 0.1,
        )
      );
    });
    if (duplicateIndex < 0) result.push(observation);
    else {
      // Keep one whole observation; combining different wrists would fabricate
      // a sequence. Duplicate/occluded torsos are display-only until separated.
      const previous = result[duplicateIndex];
      const best =
        observation.quality > previous.quality ? observation : previous;
      result[duplicateIndex] = { ...best, duplicate: true };
    }
  }
  return result;
}

function displayLandmarks(
  observation: Observation,
  previous: PosePoint[],
  elapsed: number,
  aspectRatio: number,
): PosePoint[] {
  const alpha = 1 - Math.exp(-Math.max(0, elapsed) / 200);
  return observation.landmarks.map((point, index) => {
    const before = previous[index];
    if (quality(point) < MIN_VISIBILITY)
      return { ...point, visibility: 0, presence: 0 };
    if (
      !before ||
      quality(before) < MIN_VISIBILITY ||
      elapsed <= 0 ||
      elapsed > 500 ||
      distance(point, before, aspectRatio) > observation.scale * 0.35
    )
      return { ...point };
    // Display smoothing only. Matching, dwell and alarm geometry use the
    // current accepted observation above, never these interpolated points.
    return {
      ...point,
      x: before.x + alpha * (point.x - before.x),
      y: before.y + alpha * (point.y - before.y),
    };
  });
}

function newHand(): HandState {
  return { phase: "idle", since: 0, samples: 0, reachedAt: 0, cycles: [] };
}

function clearEvidence(track: TrackState) {
  track.hands = [newHand(), newHand()];
  track.zoneSince = null;
  track.zoneSamples = 0;
  track.zoneLatched = false;
  track.alertUntil = 0;
}

function validZone(
  zone: DetectionRect | null | undefined,
): DetectionRect | null {
  if (!zone) return null;
  const { x, y, width, height } = zone;
  if (
    ![x, y, width, height].every(Number.isFinite) ||
    x < 0 ||
    y < 0 ||
    width <= 0 ||
    height <= 0 ||
    x + width > 1 ||
    y + height > 1
  )
    return null;
  return { x, y, width, height };
}

function contains(zone: DetectionRect, point: Point): boolean {
  return (
    point.x >= zone.x &&
    point.x <= zone.x + zone.width &&
    point.y >= zone.y &&
    point.y <= zone.y + zone.height
  );
}

/**
 * Experimental observable pose rules, not a theft/concealment classifier.
 * One instance belongs to one source. Anonymous IDs expire within this source;
 * occlusion and ambiguous crossings discard evidence instead of joining people.
 */
export class LiveBehaviourEngine {
  private readonly settings: LiveDetectionSettings;
  private readonly personTracker = new AnonymousPersonTracker();
  private tracks: TrackState[] = [];
  private timestamp: number | null = null;
  private eventSequence = 0;
  private aspectRatio = 1;

  constructor(settings: Partial<LiveDetectionSettings> = {}) {
    this.settings = {
      sensitivity:
        settings.sensitivity === "sensitive" ? "sensitive" : "balanced",
      restrictedZone: validZone(settings.restrictedZone),
    };
  }

  reset(): void {
    this.tracks = [];
    this.personTracker.reset();
    this.timestamp = null;
    this.aspectRatio = 1;
    // IDs stay monotonic within the instance, including after a seek/reset.
  }

  update(
    input: PosePoint[][] | DetectedPerson[],
    timestampMs: number,
    frameAspectRatio = 1,
  ): LiveEngineResult {
    if (
      !Number.isFinite(timestampMs) ||
      timestampMs < 0 ||
      !Number.isFinite(frameAspectRatio) ||
      frameAspectRatio < 0.1 ||
      frameAspectRatio > 10
    ) {
      this.reset();
      return { tracks: [], events: [] };
    }
    if (
      frameAspectRatio !== this.aspectRatio ||
      (this.timestamp !== null &&
        (timestampMs <= this.timestamp ||
          timestampMs - this.timestamp > MAX_FRAME_GAP_MS))
    )
      this.reset();
    this.timestamp = timestampMs;
    this.aspectRatio = frameAspectRatio;
    const detectorInput =
      input.length === 0 || !Array.isArray(input[0])
        ? (input as DetectedPerson[])
        : null;
    const observations = detectorInput
      ? detectorInput
          .slice(0, MAX_POSES)
          .map((person) => observePerson(person, frameAspectRatio))
          .filter((value): value is Observation => value !== null)
      : distinctObservations(
          (input as PosePoint[][])
            .slice(0, MAX_POSES)
            .map((landmarks) => observe(landmarks, frameAspectRatio))
            .filter((value): value is Observation => value !== null),
          frameAspectRatio,
        );
    const previous = this.tracks.filter(
      (track) => timestampMs - track.lastSeenAt <= TRACK_RETENTION_MS,
    );
    const overlapping = observations.map(
      (observation, index) =>
        observation.duplicate ||
        observations.some(
          (other, otherIndex) =>
            otherIndex !== index &&
            distance(observation.center, other.center, frameAspectRatio) <
              Math.min(observation.scale, other.scale) * 0.7,
        ),
    );
    const assignments = this.personTracker.update(
      observations.map((observation) => ({
        box: observation.box,
        score: observation.detectorScore,
      })),
      timestampMs,
    );
    const invalidated = new Set(this.personTracker.invalidatedTrackIds());
    for (const track of previous)
      if (invalidated.has(track.id)) clearEvidence(track);
    const used = new Set<number>();
    const visible: LiveTrack[] = [];
    const events: LiveBehaviourEvent[] = [];
    const next: TrackState[] = [];

    observations.forEach((observation, observationIndex) => {
      const assignment = assignments[observationIndex];
      if (!assignment) return;
      const existing = previous.find((item) => item.id === assignment.trackId);
      const track =
        existing ?? this.newTrack(assignment.trackId, observation, timestampMs);
      if (existing) used.add(existing.id);
      const uncertain =
        assignment.state !== "observed" ||
        overlapping[observationIndex] ||
        !observation.poseUsable;
      track.displayed = displayLandmarks(
        observation,
        existing ? track.displayed : [],
        timestampMs - track.lastSeenAt,
        frameAspectRatio,
      );
      track.observed = observation;
      track.lastSeenAt = timestampMs;
      let watch = false;
      if (uncertain) clearEvidence(track);
      else {
        for (const hand of [0, 1] as const)
          watch = this.updateHand(track, hand, timestampMs, events) || watch;
        watch = this.updateZone(track, timestampMs, events) || watch;
      }
      const alert = !uncertain && timestampMs < track.alertUntil;
      visible.push({
        id: track.id,
        landmarks: track.displayed,
        box: { ...observation.box },
        quality: observation.quality,
        detectorScore: observation.detectorScore,
        trackingState: assignment.state,
        visibilityState: observation.visibilityState,
        status: alert ? "alert" : watch || uncertain ? "watch" : "normal",
        label: alert
          ? track.alertLabel
          : overlapping[observationIndex]
            ? "Tracking overlap · paused"
            : assignment.state === "ambiguous"
              ? "Association uncertain · paused"
              : assignment.state === "recovered"
                ? "Recovered after occlusion · paused"
                : observation.visibilityState === "edge_truncated"
                  ? "Person partly outside view · paused"
                  : observation.visibilityState === "too_small"
                    ? "Person too small for pose · paused"
                    : !observation.poseUsable
                      ? "Person detected · pose unavailable"
                      : watch
                        ? "Movement under review"
                        : "Person tracked",
      });
      next.push(track);
    });
    // Retaining an ID briefly does not retain a partly observed behaviour.
    previous.forEach((track) => {
      if (!used.has(track.id) && !invalidated.has(track.id)) {
        clearEvidence(track);
        track.displayed = [];
        next.push(track);
      }
    });
    this.tracks = next.slice(0, MAX_POSES * 2);
    return { tracks: visible, events };
  }

  private newTrack(
    id: number,
    observed: Observation,
    timestampMs: number,
  ): TrackState {
    return {
      id,
      observed,
      displayed: [],
      lastSeenAt: timestampMs,
      hands: [newHand(), newHand()],
      zoneSince: null,
      zoneSamples: 0,
      zoneLatched: false,
      lastEvents: {},
      alertUntil: 0,
      alertLabel: "",
    };
  }

  private updateHand(
    track: TrackState,
    handIndex: 0 | 1,
    now: number,
    events: LiveBehaviourEvent[],
  ): boolean {
    const state = track.hands[handIndex];
    const observation = track.observed;
    const wrist = observation.landmarks[15 + handIndex];
    const elbow = observation.landmarks[13 + handIndex];
    const shoulder = observation.landmarks[11 + handIndex];
    if (
      !insideDetectorBox(wrist, observation.box) ||
      !insideDetectorBox(elbow, observation.box) ||
      !insideDetectorBox(shoulder, observation.box)
    ) {
      track.hands[handIndex] = newHand();
      return false;
    }
    const scale = observation.scale;
    const aspectRatio = this.aspectRatio;
    const hipWidth =
      Math.abs(observation.landmarks[23].x - observation.landmarks[24].x) *
      aspectRatio;
    const atWaist =
      Math.abs(wrist.x - observation.hips.x) * aspectRatio <=
        hipWidth / 2 + scale * 0.35 &&
      Math.abs(wrist.y - observation.hips.y) <= scale * 0.35;
    const reaching =
      distance(wrist, observation.hips, aspectRatio) >= scale * 1.05 &&
      distance(wrist, shoulder, aspectRatio) >= scale * 0.6 &&
      wrist.y <= observation.hips.y + scale * 0.25 &&
      (Math.abs(wrist.x - observation.hips.x) * aspectRatio >= scale * 0.7 ||
        wrist.y < observation.hips.y - scale * 1.1);
    const sensitive = this.settings.sensitivity === "sensitive";
    const dwell = sensitive ? 250 : 350;
    const minimumSamples = sensitive ? 2 : 3;
    state.cycles = state.cycles.filter((at) => now - at <= CYCLE_WINDOW_MS);
    if (
      (state.phase === "returning" || state.phase === "waist") &&
      now - state.reachedAt > REACH_TIMEOUT_MS
    ) {
      state.phase = "idle";
      state.samples = 0;
    }
    if (state.phase === "leave") {
      if (!atWaist) state.phase = "idle";
      else return state.cycles.length > 0;
    }
    if (state.phase === "idle") {
      if (reaching) {
        state.phase = "reaching";
        state.since = now;
        state.samples = 1;
      }
    } else if (state.phase === "reaching") {
      if (!reaching) {
        state.phase = "idle";
        state.samples = 0;
      } else if (
        ++state.samples >= minimumSamples &&
        now - state.since >= dwell
      ) {
        state.phase = "returning";
        state.reachedAt = now;
      }
    } else if (state.phase === "returning") {
      if (atWaist) {
        state.phase = "waist";
        state.since = now;
        state.samples = 1;
      }
    } else if (state.phase === "waist") {
      if (!atWaist) {
        state.phase = "returning";
        state.samples = 0;
      } else if (
        ++state.samples >= minimumSamples &&
        now - state.since >= dwell
      ) {
        state.cycles.push(now);
        state.phase = "leave";
        const requiredCycles = 2;
        if (state.cycles.length >= requiredCycles) {
          this.emit(
            track,
            "REPEATED_HAND_TO_WAIST",
            now,
            events,
            "Repeated reach toward waist",
            "Two sustained reach-to-waist sequences observed on the same hand. Experimental pose rule; review the video. This does not establish item concealment or theft.",
          );
          state.cycles = [];
        }
      }
    }
    return (
      state.phase === "returning" ||
      state.phase === "waist" ||
      state.cycles.length > 0
    );
  }

  private updateZone(
    track: TrackState,
    now: number,
    events: LiveBehaviourEvent[],
  ): boolean {
    const zone = this.settings.restrictedZone;
    const inside =
      zone &&
      contains(zone, track.observed.center) &&
      contains(zone, track.observed.hips);
    if (!inside) {
      track.zoneSince = null;
      track.zoneSamples = 0;
      track.zoneLatched = false;
      return false;
    }
    if (track.zoneSince === null) track.zoneSince = now;
    track.zoneSamples++;
    if (
      !track.zoneLatched &&
      track.zoneSamples >= 4 &&
      now - track.zoneSince >= 2_000
    ) {
      track.zoneLatched = true;
      this.emit(
        track,
        "RESTRICTED_ZONE_ENTRY",
        now,
        events,
        "Restricted zone · review",
        "Visible torso and hips stayed inside the configured zone for at least two seconds. Review access and context; the rule cannot determine permission or intent.",
      );
    }
    return !track.zoneLatched;
  }

  private emit(
    track: TrackState,
    code: LiveEventCode,
    atMs: number,
    events: LiveBehaviourEvent[],
    label: string,
    detail: string,
  ) {
    const previous = track.lastEvents[code];
    if (previous !== undefined && atMs - previous < COOLDOWN_MS) return;
    track.lastEvents[code] = atMs;
    track.alertUntil = atMs + ALERT_DISPLAY_MS;
    track.alertLabel = label;
    events.push({
      id: `pose-${track.id}-${++this.eventSequence}-${Math.round(atMs)}`,
      trackId: track.id,
      code,
      label,
      detail,
      atMs,
    });
  }
}
