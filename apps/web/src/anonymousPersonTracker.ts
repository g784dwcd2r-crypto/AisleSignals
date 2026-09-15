import type { DetectionRect } from "./liveDetectionTypes";

export type PersonBoxObservation = Readonly<{
  box: DetectionRect;
  score: number;
}>;
export type PersonTrackAssignment = Readonly<{
  detectionIndex: number;
  trackId: number;
  state: "observed" | "recovered" | "ambiguous";
}>;

type Track = {
  id: number;
  box: DetectionRect;
  vx: number;
  vy: number;
  lastSeenAt: number;
};

const MAX_GAP_MS = 1_200;
const AMBIGUITY_MARGIN = 0.005;
const MAX_TRACKS = 24;

const center = (box: DetectionRect) => ({
  x: box.x + box.width / 2,
  y: box.y + box.height / 2,
});
const valid = (box: DetectionRect) =>
  [box.x, box.y, box.width, box.height].every(Number.isFinite) &&
  box.x >= 0 &&
  box.y >= 0 &&
  box.width > 0 &&
  box.height > 0 &&
  box.x + box.width <= 1.000001 &&
  box.y + box.height <= 1.000001;
const iou = (a: DetectionRect, b: DetectionRect) => {
  const width = Math.max(
    0,
    Math.min(a.x + a.width, b.x + b.width) - Math.max(a.x, b.x),
  );
  const height = Math.max(
    0,
    Math.min(a.y + a.height, b.y + b.height) - Math.max(a.y, b.y),
  );
  const intersection = width * height;
  return (
    intersection /
    Math.max(1e-9, a.width * a.height + b.width * b.height - intersection)
  );
};

/**
 * Camera-local, appearance-free tracker. It predicts only short motion and
 * deliberately starts a new anonymous ID when an association is ambiguous.
 * Instances must never be shared between camera crops.
 */
export class AnonymousPersonTracker {
  private tracks: Track[] = [];
  private lastAt: number | null = null;
  private nextId = 1;
  private invalidated: number[] = [];

  reset() {
    this.tracks = [];
    this.lastAt = null;
    this.invalidated = [];
  }

  invalidatedTrackIds(): readonly number[] {
    return this.invalidated;
  }

  update(observations: readonly PersonBoxObservation[], atMs: number) {
    if (this.nextId > Number.MAX_SAFE_INTEGER - observations.length) {
      this.reset();
      this.nextId = 1;
      return [] as PersonTrackAssignment[];
    }
    if (
      !Number.isFinite(atMs) ||
      atMs < 0 ||
      observations.length > 12 ||
      observations.some(
        (item) =>
          !valid(item.box) ||
          !Number.isFinite(item.score) ||
          item.score < 0 ||
          item.score > 1,
      ) ||
      (this.lastAt !== null &&
        (atMs <= this.lastAt || atMs - this.lastAt > 5_000))
    ) {
      this.reset();
      return [] as PersonTrackAssignment[];
    }
    this.lastAt = atMs;
    this.invalidated = [];
    const available = this.tracks.filter(
      (track) => atMs - track.lastSeenAt <= MAX_GAP_MS,
    );
    const predicted = available.map((track) => {
      const dt = Math.min(600, atMs - track.lastSeenAt);
      return {
        ...track.box,
        x: Math.max(
          0,
          Math.min(1 - track.box.width, track.box.x + track.vx * dt),
        ),
        y: Math.max(
          0,
          Math.min(1 - track.box.height, track.box.y + track.vy * dt),
        ),
      };
    });
    const costs = observations.map((observation) =>
      predicted.map((box) => {
        const a = center(observation.box),
          b = center(box);
        const scale = Math.max(0.04, (observation.box.height + box.height) / 2);
        const displacement = Math.hypot(a.x - b.x, a.y - b.y) / scale;
        const size = Math.abs(Math.log(observation.box.height / box.height));
        const overlap = iou(observation.box, box);
        const cost = displacement * 0.55 + size * 0.2 + (1 - overlap) * 0.25;
        return displacement <= 1.15 && size <= 0.75 ? cost : Infinity;
      }),
    );
    const assignments: PersonTrackAssignment[] = [];
    const plans = observations.map((_, detectionIndex) => {
      const ranked = costs[detectionIndex]
        .map((cost, index) => ({ cost, index }))
        .filter(({ cost }) => Number.isFinite(cost))
        .sort((a, b) => a.cost - b.cost);
      const best = ranked[0];
      const rivalForDetection = ranked[1];
      const columnBest = best
        ? costs
            .map((row, index) => ({ cost: row[best.index], index }))
            .filter(({ cost }) => Number.isFinite(cost))
            .sort((a, b) => a.cost - b.cost)[0]
        : undefined;
      return {
        best,
        candidates: ranked.map(({ index }) => index),
        ambiguous:
          !!best &&
          ((!!rivalForDetection &&
            rivalForDetection.cost - best.cost < AMBIGUITY_MARGIN) ||
            columnBest?.index !== detectionIndex),
      };
    });
    const invalidated = new Set<number>();
    for (const plan of plans)
      if (plan.ambiguous)
        plan.candidates.forEach((index) => invalidated.add(index));
    this.invalidated = [...invalidated].map((index) => available[index].id);
    const next = available.filter((_, index) => !invalidated.has(index));
    observations.forEach((observation, detectionIndex) => {
      const { best, ambiguous } = plans[detectionIndex];
      const abstained = ambiguous || (!!best && invalidated.has(best.index));
      if (!best || abstained) {
        const track: Track = {
          id: this.nextId++,
          box: { ...observation.box },
          vx: 0,
          vy: 0,
          lastSeenAt: atMs,
        };
        next.push(track);
        assignments.push({
          detectionIndex,
          trackId: track.id,
          state: abstained ? "ambiguous" : "observed",
        });
        return;
      }
      const track = available[best.index];
      const before = center(track.box),
        after = center(observation.box);
      const elapsed = Math.max(1, atMs - track.lastSeenAt);
      const recovered = elapsed > 600;
      track.vx = track.vx * 0.45 + ((after.x - before.x) / elapsed) * 0.55;
      track.vy = track.vy * 0.45 + ((after.y - before.y) / elapsed) * 0.55;
      track.box = { ...observation.box };
      track.lastSeenAt = atMs;
      assignments.push({
        detectionIndex,
        trackId: track.id,
        state: recovered ? "recovered" : "observed",
      });
    });
    this.tracks = next
      .filter((track) => atMs - track.lastSeenAt <= MAX_GAP_MS)
      .sort((a, b) => b.lastSeenAt - a.lastSeenAt)
      .slice(0, MAX_TRACKS);
    return assignments;
  }
}
