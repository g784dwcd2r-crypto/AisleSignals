import { describe, expect, it } from "vitest";
import { AnonymousPersonTracker } from "./anonymousPersonTracker";

const person = (x: number, y = 0.1, score = 0.9) => ({
  box: { x, y, width: 0.16, height: 0.6 },
  score,
});

describe("camera-local anonymous person tracking", () => {
  it("uses motion prediction to preserve an anonymous ID through smooth movement", () => {
    const tracker = new AnonymousPersonTracker();
    const ids = [0, 200, 400, 600].map(
      (at, index) =>
        tracker.update([person(0.1 + index * 0.04)], at)[0].trackId,
    );
    expect(new Set(ids)).toEqual(new Set([1]));
  });

  it("recovers a short occlusion but marks the first association unusable for evidence", () => {
    const tracker = new AnonymousPersonTracker();
    expect(tracker.update([person(0.2)], 0)[0]).toMatchObject({
      trackId: 1,
      state: "observed",
    });
    expect(tracker.update([], 300)).toEqual([]);
    expect(tracker.update([person(0.23)], 900)[0]).toMatchObject({
      trackId: 1,
      state: "recovered",
    });
  });

  it("abstains from a tied association by issuing a fresh anonymous ID", () => {
    const tracker = new AnonymousPersonTracker();
    tracker.update([person(0.2), person(0.6)], 0);
    const middle = tracker.update([person(0.4)], 250)[0];
    expect(middle.state).toBe("ambiguous");
    expect(middle.trackId).toBe(3);
    expect([...tracker.invalidatedTrackIds()].sort()).toEqual([1, 2]);
  });

  it("marks every detection ambiguous when two detections compete for one track", () => {
    const tracker = new AnonymousPersonTracker();
    tracker.update([person(0.3)], 0);
    const split = tracker.update([person(0.29), person(0.31)], 250);
    expect(split.map((item) => item.state)).toEqual(["ambiguous", "ambiguous"]);
    expect(split.every((item) => item.trackId !== 1)).toBe(true);
    expect(tracker.invalidatedTrackIds()).toEqual([1]);
  });

  it("resets all identity state before an ID counter can overflow", () => {
    const tracker = new AnonymousPersonTracker();
    tracker.update([person(0.1)], 0);
    (tracker as unknown as { nextId: number }).nextId = Number.MAX_SAFE_INTEGER;
    expect(tracker.update([person(0.12)], 250)).toEqual([]);
    expect(tracker.update([person(0.12)], 500)[0].trackId).toBe(1);
  });

  it("never shares identity state across camera-local instances", () => {
    const left = new AnonymousPersonTracker();
    const right = new AnonymousPersonTracker();
    left.update([person(0.1)], 0);
    const rightFirst = right.update([person(0.11)], 250)[0];
    expect(rightFirst).toEqual({
      detectionIndex: 0,
      trackId: 1,
      state: "observed",
    });
  });

  it("fails closed on malformed or excessive detector output", () => {
    const tracker = new AnonymousPersonTracker();
    expect(
      tracker.update(
        [{ box: { x: -1, y: 0, width: 1, height: 1 }, score: 0.9 }],
        0,
      ),
    ).toEqual([]);
    expect(tracker.update([person(0.1, 0.1, Number.NaN)], 50)).toEqual([]);
    expect(
      tracker.update(
        Array.from({ length: 13 }, () => person(0)),
        100,
      ),
    ).toEqual([]);
  });
});
