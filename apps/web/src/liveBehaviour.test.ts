import { describe, expect, it } from "vitest";
import { LiveBehaviourEngine, POSE_CONNECTIONS } from "./liveBehaviour";
import type { LiveBehaviourEvent, PosePoint } from "./liveDetectionTypes";

type HandPosition = "down" | "reach" | "waist";

/** Synthetic geometry tests exercise the rules, not model accuracy. */
function pose(
  left: HandPosition = "down",
  right: HandPosition = "down",
  center = 0.5,
): PosePoint[] {
  const points = Array.from({ length: 33 }, () => ({
    x: center,
    y: 0.2,
    visibility: 0.99,
    presence: 0.99,
  }));
  const set = (index: number, x: number, y: number) => {
    points[index] = { ...points[index], x, y };
  };
  set(11, center - 0.08, 0.32);
  set(12, center + 0.08, 0.32);
  set(13, center - 0.11, 0.48);
  set(14, center + 0.11, 0.48);
  set(23, center - 0.06, 0.6);
  set(24, center + 0.06, 0.6);
  set(25, center - 0.07, 0.77);
  set(26, center + 0.07, 0.77);
  for (const index of [27, 29, 31]) set(index, center - 0.08, 0.95);
  for (const index of [28, 30, 32]) set(index, center + 0.08, 0.95);
  for (const [index, side, position] of [
    [15, -1, left],
    [16, 1, right],
  ] as const) {
    if (position === "reach") set(index, center + side * 0.38, 0.4);
    else if (position === "waist") set(index, center + side * 0.04, 0.61);
    else set(index, center + side * 0.12, 0.82);
  }
  return points;
}

function cycle(
  engine: LiveBehaviourEngine,
  start: number,
  hand: "left" | "right" = "left",
) {
  const events: LiveBehaviourEvent[] = [];
  for (const [offset, position] of [
    [0, "reach"],
    [200, "reach"],
    [400, "reach"],
    [600, "waist"],
    [800, "waist"],
    [1_000, "waist"],
  ] as const) {
    const points = hand === "left" ? pose(position) : pose("down", position);
    events.push(...engine.update([points], start + offset).events);
  }
  return events;
}

describe("observable live pose rules", () => {
  it("requires two sustained cycles on one hand in balanced mode and marks that track", () => {
    const engine = new LiveBehaviourEngine();
    expect(cycle(engine, 0)).toEqual([]);
    const events = cycle(engine, 1_200);
    expect(events).toHaveLength(1);
    expect(events[0]).toMatchObject({
      code: "REPEATED_HAND_TO_WAIST",
      label: "Repeated reach toward waist",
      atMs: 2_200,
    });
    expect(events[0].detail).toContain(
      "does not establish item concealment or theft",
    );
    const result = engine.update([pose("waist")], 2_400);
    expect(result.events).toEqual([]);
    expect(result.tracks[0]).toMatchObject({
      status: "alert",
      id: events[0].trackId,
    });
  });

  it("does not call a stationary hand at the waist or a single ordinary reach suspicious", () => {
    const stationary = new LiveBehaviourEngine();
    for (let now = 0; now <= 15_000; now += 200)
      expect(stationary.update([pose("waist", "waist")], now).events).toEqual(
        [],
      );
    const singleReach = new LiveBehaviourEngine();
    expect(cycle(singleReach, 0)).toEqual([]);
    for (let now = 1_200; now <= 16_000; now += 200)
      expect(singleReach.update([pose("waist")], now).events).toEqual([]);
  });

  it("keeps left and right hand evidence separate", () => {
    const engine = new LiveBehaviourEngine();
    expect(cycle(engine, 0, "left")).toEqual([]);
    expect(cycle(engine, 1_200, "right")).toEqual([]);
    expect(cycle(engine, 2_400, "left")).toHaveLength(1);
  });

  it("uses shorter sensitive dwell but still requires two sustained cycles", () => {
    for (const sensitivity of ["balanced", "sensitive"] as const) {
      const engine = new LiveBehaviourEngine({ sensitivity });
      const events: LiveBehaviourEvent[] = [];
      for (const start of [0, 1_200]) {
        for (const offset of [0, 150, 300])
          events.push(...engine.update([pose("reach")], start + offset).events);
        for (const offset of [450, 600, 750])
          events.push(...engine.update([pose("waist")], start + offset).events);
        if (start === 0) expect(events).toEqual([]);
      }
      expect(events).toHaveLength(sensitivity === "sensitive" ? 1 : 0);
    }
  });

  it("does not alert for a single phone-like reach in either mode", () => {
    for (const sensitivity of ["balanced", "sensitive"] as const) {
      const engine = new LiveBehaviourEngine({ sensitivity });
      expect(cycle(engine, 0)).toEqual([]);
      for (let now = 1_200; now <= 16_000; now += 200)
        expect(engine.update([pose("waist")], now).events).toEqual([]);
    }
  });

  it("rejects brief arm flickers, incomplete returns and stale cycle evidence", () => {
    const engine = new LiveBehaviourEngine();
    for (let now = 0; now < 8_000; now += 200)
      expect(
        engine.update([pose(now % 400 ? "reach" : "waist")], now).events,
      ).toEqual([]);
    expect(cycle(engine, 8_000)).toEqual([]);
    for (let now = 9_200; now <= 24_000; now += 200)
      expect(engine.update([pose()], now).events).toEqual([]);
    expect(cycle(engine, 24_200)).toEqual([]);
  });

  it("does not accumulate reach evidence while wrists or elbows are hidden", () => {
    for (const landmark of [13, 15]) {
      const engine = new LiveBehaviourEngine();
      cycle(engine, 0);
      const hidden = pose("reach");
      hidden[landmark].visibility = 0.2;
      expect(engine.update([hidden], 1_200).events).toEqual([]);
      expect(cycle(engine, 1_400)).toEqual([]);
    }
  });

  it("does not turn invalid or low-quality body coordinates into tracks or events", () => {
    for (const patch of [
      { visibility: 0.2 },
      { presence: 0.1 },
      { visibility: NaN },
      { visibility: 1.2 },
      { x: Infinity },
      { x: -0.1 },
      { y: 1.1 },
    ]) {
      const engine = new LiveBehaviourEngine();
      const invalid = pose("reach");
      Object.assign(invalid[23], patch);
      expect(engine.update([invalid], 0)).toEqual({ tracks: [], events: [] });
    }
    const missingConfidence = pose().map(({ x, y }) => ({ x, y }));
    expect(
      new LiveBehaviourEngine().update([missingConfidence], 0).tracks,
    ).toEqual([]);
    expect(new LiveBehaviourEngine().update([[]], 0).tracks).toEqual([]);
  });

  it("resets partial evidence on occlusion, dropped frames, seeks and duplicate timestamps", () => {
    for (const discontinuity of [
      "occlusion",
      "long-gap",
      "seek",
      "duplicate",
    ] as const) {
      const engine = new LiveBehaviourEngine();
      cycle(engine, 0);
      let restart = 1_400;
      if (discontinuity === "occlusion") engine.update([], 1_200);
      if (discontinuity === "long-gap") restart = 5_000;
      if (discontinuity === "seek") {
        engine.update([pose()], 0);
        restart = 200;
      }
      if (discontinuity === "duplicate") engine.update([pose()], 1_000);
      expect(cycle(engine, restart)).toEqual([]);
    }
  });

  it("clears evidence on invalid timestamps and explicit reset without reusing event IDs", () => {
    const engine = new LiveBehaviourEngine();
    cycle(engine, 0);
    const first = cycle(engine, 1_200)[0];
    engine.reset();
    cycle(engine, 0);
    const second = cycle(engine, 1_200)[0];
    expect(second.id).not.toBe(first.id);
    for (const timestamp of [-1, NaN, Infinity]) {
      engine.update([pose("waist")], timestamp);
      expect(cycle(engine, 0)).toEqual([]);
    }
  });

  it("rate-limits repeat events and does not raise another event from a static alert pose", () => {
    const engine = new LiveBehaviourEngine();
    const events: LiveBehaviourEvent[] = [];
    for (let start = 0; start < 24_000; start += 1_200)
      events.push(...cycle(engine, start));
    expect(events).toHaveLength(2);
    expect(events[1].atMs - events[0].atMs).toBeGreaterThanOrEqual(20_000);
    for (let now = 24_000; now < 50_000; now += 200)
      expect(engine.update([pose("waist")], now).events).toEqual([]);
  });
});

describe("source aspect-ratio geometry", () => {
  function framedPose(position: HandPosition, aspectRatio: number) {
    // The same physical body fits the square, landscape and portrait frames.
    return pose(position).map((point) => ({
      ...point,
      x: 0.5 + ((point.x - 0.5) * 0.55) / aspectRatio,
      y: 0.5 + (point.y - 0.5) * 0.55,
    }));
  }

  it("preserves cycle classification and physical box padding across landscape and portrait", () => {
    const results = [1, 16 / 9, 9 / 16].map((aspectRatio) => {
      const engine = new LiveBehaviourEngine();
      const events: LiveBehaviourEvent[] = [];
      for (const start of [0, 1_200]) {
        for (const [offset, position] of [
          [0, "reach"],
          [200, "reach"],
          [400, "reach"],
          [600, "waist"],
          [800, "waist"],
          [1_000, "waist"],
        ] as const)
          events.push(
            ...engine.update(
              [framedPose(position, aspectRatio)],
              start + offset,
              aspectRatio,
            ).events,
          );
      }
      const track = engine.update(
        [framedPose("waist", aspectRatio)],
        2_400,
        aspectRatio,
      ).tracks[0];
      return { events, track, physicalWidth: track.box.width * aspectRatio };
    });
    for (const result of results) {
      expect(result.events).toHaveLength(1);
      expect(result.events[0]).toMatchObject({
        code: "REPEATED_HAND_TO_WAIST",
        atMs: 2_200,
      });
      expect(result.track.status).toBe("alert");
      expect(result.physicalWidth).toBeCloseTo(results[0].physicalWidth);
      expect(result.track.box.y).toBeCloseTo(results[0].track.box.y);
    }
  });

  it("discards evidence when source dimensions change", () => {
    const engine = new LiveBehaviourEngine();
    cycle(engine, 0);
    const aspectRatio = 16 / 9;
    for (const [offset, position] of [
      [0, "reach"],
      [200, "reach"],
      [400, "reach"],
      [600, "waist"],
      [800, "waist"],
      [1_000, "waist"],
    ] as const)
      expect(
        engine.update(
          [framedPose(position, aspectRatio)],
          1_200 + offset,
          aspectRatio,
        ).events,
      ).toEqual([]);
  });

  it("rejects invalid aspect ratios and clears preceding evidence", () => {
    for (const aspectRatio of [0, -1, 0.09, 10.01, NaN, Infinity]) {
      const engine = new LiveBehaviourEngine();
      cycle(engine, 0);
      expect(engine.update([pose("reach")], 1_200, aspectRatio)).toEqual({
        tracks: [],
        events: [],
      });
      expect(cycle(engine, 1_400)).toEqual([]);
    }
  });
});

describe("anonymous per-source track association", () => {
  it("assigns one-to-one IDs independently of detector array ordering", () => {
    const engine = new LiveBehaviourEngine();
    const first = engine.update(
      [pose("down", "down", 0.25), pose("down", "down", 0.75)],
      0,
    );
    const second = engine.update(
      [pose("down", "down", 0.74), pose("down", "down", 0.26)],
      200,
    );
    expect(second.tracks.map((track) => track.id)).toEqual([
      first.tracks[1].id,
      first.tracks[0].id,
    ]);
    expect(new Set(second.tracks.map((track) => track.id)).size).toBe(2);
    expect(second.events).toEqual([]);
  });

  it("discards histories when people cross instead of joining their hands", () => {
    const engine = new LiveBehaviourEngine();
    cycle(engine, 0);
    const crossing = engine.update(
      [pose("waist", "down", 0.47), pose("down", "waist", 0.53)],
      1_200,
    );
    expect(crossing.events).toEqual([]);
    expect(
      crossing.tracks.every(
        (track) => track.label === "Tracking overlap · paused",
      ),
    ).toBe(true);
    expect(new Set(crossing.tracks.map((track) => track.id)).size).toBe(2);
    // Resolve the overlap and settle the new anonymous association.
    engine.update([pose()], 1_400);
    engine.update([pose()], 1_600);
    expect(cycle(engine, 1_800)).toEqual([]);
  });

  it("expires a departed track and bounds untrusted detector output", () => {
    const engine = new LiveBehaviourEngine();
    const first = engine.update([pose()], 0).tracks[0];
    engine.update([], 600);
    const returned = engine.update([pose()], 1_400).tracks[0];
    expect(returned.id).not.toBe(first.id);
    expect(
      engine.update(
        Array.from({ length: 100 }, () => pose()),
        1_600,
      ).tracks,
    ).toHaveLength(12);
  });

  it("emits normalized quality/boxes and only valid body connection indices", () => {
    const points = pose();
    const before = JSON.stringify(points);
    const track = new LiveBehaviourEngine().update([points], 0).tracks[0];
    expect(track.quality).toBeCloseTo(0.99);
    expect(track.box.x).toBeGreaterThanOrEqual(0);
    expect(track.box.y + track.box.height).toBeLessThanOrEqual(1);
    expect(JSON.stringify(points)).toBe(before);
    expect(POSE_CONNECTIONS.every(([from, to]) => from >= 11 && to < 33)).toBe(
      true,
    );
  });
});

describe("configured restricted-zone dwell", () => {
  const zone = { x: 0.3, y: 0.25, width: 0.4, height: 0.5 };

  it("requires two continuous seconds and latches one event until exit", () => {
    const engine = new LiveBehaviourEngine({ restrictedZone: zone });
    for (let now = 0; now < 2_000; now += 200)
      expect(engine.update([pose()], now).events).toEqual([]);
    const result = engine.update([pose()], 2_000);
    expect(result.events).toHaveLength(1);
    expect(result.events[0].code).toBe("RESTRICTED_ZONE_ENTRY");
    expect(result.tracks[0].status).toBe("alert");
    for (let now = 2_200; now < 30_000; now += 200)
      expect(engine.update([pose()], now).events).toEqual([]);
  });

  it("has no default zone and ignores malformed rectangles", () => {
    for (const restrictedZone of [
      null,
      { ...zone, width: NaN },
      { ...zone, x: -0.1 },
      { ...zone, width: 0.9 },
    ]) {
      const engine = new LiveBehaviourEngine({ restrictedZone });
      for (let now = 0; now <= 3_000; now += 200)
        expect(engine.update([pose()], now).events).toEqual([]);
    }
  });

  it("resets a partly observed dwell when a person leaves, is obscured or time jumps", () => {
    for (const kind of ["leave", "occlude", "gap"] as const) {
      const engine = new LiveBehaviourEngine({ restrictedZone: zone });
      for (let now = 0; now <= 1_600; now += 200) engine.update([pose()], now);
      if (kind === "leave") engine.update([pose("down", "down", 0.75)], 1_800);
      if (kind === "occlude") engine.update([], 1_800);
      const restart = kind === "gap" ? 4_000 : 2_000;
      for (let now = restart; now < restart + 2_000; now += 200)
        expect(engine.update([pose()], now).events).toEqual([]);
    }
  });
});
