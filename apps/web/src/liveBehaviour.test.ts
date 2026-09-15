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

  it("rejects confident product-shaped landmarks without human torso and arm geometry", () => {
    const flatTorso = pose();
    for (const index of [23, 24]) flatTorso[index].y = 0.35;
    expect(new LiveBehaviourEngine().update([flatTorso], 0)).toEqual({
      tracks: [],
      events: [],
    });

    const noArms = pose();
    for (const index of [13, 14, 15, 16]) {
      noArms[index].visibility = 0.2;
      noArms[index].presence = 0.2;
    }
    expect(new LiveBehaviourEngine().update([noArms], 0)).toEqual({
      tracks: [],
      events: [],
    });
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
        Array.from({ length: 100 }, (_, index) =>
          pose("down", "down", 0.2 + (index % 20) * 0.03),
        ),
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

describe("crowded-view display stability without joined behaviour", () => {
  it("retains unique IDs for stationary nearby people while pausing overlapping hand evidence", () => {
    const engine = new LiveBehaviourEngine();
    let expected: number[] | undefined;
    for (let now = 0; now <= 5000; now += 250) {
      const phase = now % 1500 < 750 ? "reach" : "waist";
      const people = [0.45, 0.55].map((center) => ({
        box: {
          x: center - 0.16,
          y: 0.08,
          width: 0.32,
          height: 0.9,
        },
        detectorScore: 0.95,
        subjectPixels: 40_000,
        visibilityState: "sufficient" as const,
        landmarks: pose(phase, "down", center),
      }));
      const reversed = now % 500 !== 0;
      const result = engine.update(reversed ? people.reverse() : people, now);
      const ids = result.tracks.map((track) => track.id);
      expected ??= ids;
      expect(ids).toEqual(reversed ? [...expected].reverse() : expected);
      expect(
        result.tracks.every(
          (track) => track.label === "Tracking overlap · paused",
        ),
      ).toBe(true);
      expect(result.events).toEqual([]);
    }
  });

  it("collapses duplicate torso detections without composing their different hands or creating extra IDs", () => {
    const engine = new LiveBehaviourEngine();
    const first = engine.update([pose()], 0).tracks[0];
    for (let now = 250; now <= 3000; now += 250) {
      const phase = now % 1500 < 750 ? "reach" : "waist";
      const result = engine.update(
        [pose(phase), pose(phase === "reach" ? "waist" : "reach"), pose(phase)],
        now,
      );
      expect(result.tracks).toHaveLength(1);
      expect(result.tracks[0].id).toBe(first.id);
      expect(result.tracks[0].label).toBe("Tracking overlap · paused");
      expect(result.events).toEqual([]);
    }
    expect(cycle(engine, 3250)).toEqual([]);
  });

  it("keeps close but distinct torsos instead of using broad box overlap suppression", () => {
    const result = new LiveBehaviourEngine().update(
      [pose("down", "down", 0.45), pose("down", "down", 0.55)],
      0,
    );
    expect(result.tracks).toHaveLength(2);
    expect(new Set(result.tracks.map((track) => track.id)).size).toBe(2);
  });

  it("invalidates all crossing candidates before any array-order-dependent alarm can publish", () => {
    for (const reverse of [false, true]) {
      const engine = new LiveBehaviourEngine();
      cycle(engine, 0);
      for (const now of [1200, 1400, 1600]) engine.update([pose("reach")], now);
      for (const now of [1800, 2000]) engine.update([pose("waist")], now);
      const overlap = [
        pose("waist", "down", 0.49),
        pose("down", "waist", 0.54),
      ];
      const result = engine.update(reverse ? overlap.reverse() : overlap, 2200);
      expect(result.events).toEqual([]);
      expect(result.tracks.every((track) => track.status !== "alert")).toBe(
        true,
      );
      engine.update([pose()], 2400);
      engine.update([pose()], 2600);
      expect(cycle(engine, 2800)).toEqual([]);
    }
  });
});

describe("dedicated person detector gate", () => {
  it("renders the detector box even when gated pose is unavailable", () => {
    const engine = new LiveBehaviourEngine();
    const box = { x: 0.12, y: 0.08, width: 0.31, height: 0.82 };
    const result = engine.update(
      [
        {
          box,
          detectorScore: 0.91,
          subjectPixels: 32000,
          visibilityState: "sufficient",
          landmarks: null,
        },
      ],
      0,
    );
    expect(result.events).toEqual([]);
    expect(result.tracks[0]).toMatchObject({
      box,
      detectorScore: 0.91,
      label: "Person detected · pose unavailable",
    });
  });

  it("does not accept a pose without a person-detector assertion", () => {
    const engine = new LiveBehaviourEngine();
    expect(
      engine.update(
        [
          {
            box: { x: 0.1, y: 0.1, width: 0.4, height: 0.8 },
            detectorScore: 0.39,
            subjectPixels: 32_000,
            visibilityState: "sufficient",
            landmarks: pose(),
          },
        ],
        0,
      ),
    ).toEqual({ tracks: [], events: [] });
  });

  it("uses the full detector box rather than pose landmark bounds", () => {
    const engine = new LiveBehaviourEngine();
    const box = { x: 0.05, y: 0.02, width: 0.7, height: 0.95 };
    const track = engine.update(
      [
        {
          box,
          detectorScore: 0.88,
          subjectPixels: 32000,
          visibilityState: "sufficient",
          landmarks: pose(),
        },
      ],
      0,
    ).tracks[0];
    expect(track.box).toEqual(box);
  });

  it.each(["edge_truncated", "too_small"] as const)(
    "displays %s people but blocks pose behavior evidence",
    (visibilityState) => {
      const engine = new LiveBehaviourEngine();
      const result = engine.update(
        [
          {
            box: { x: 0, y: 0.1, width: 0.3, height: 0.8 },
            detectorScore: 0.9,
            subjectPixels: 20_000,
            visibilityState,
            landmarks: pose("reach"),
          },
        ],
        0,
      );
      expect(result.tracks).toHaveLength(1);
      expect(result.tracks[0].visibilityState).toBe(visibilityState);
      expect(result.events).toEqual([]);
      expect(result.tracks[0].label).toMatch(/paused/);
    },
  );

  it("rejects non-finite detector scores", () => {
    expect(
      new LiveBehaviourEngine().update(
        [
          {
            box: { x: 0.1, y: 0.1, width: 0.3, height: 0.8 },
            detectorScore: Number.NaN,
            subjectPixels: 20_000,
            visibilityState: "sufficient",
            landmarks: null,
          },
        ],
        0,
      ),
    ).toEqual({ tracks: [], events: [] });
  });

  it("shows but does not use a pose whose torso conflicts with its detector box", () => {
    const result = new LiveBehaviourEngine().update(
      [
        {
          box: { x: 0.02, y: 0.05, width: 0.24, height: 0.9 },
          detectorScore: 0.9,
          subjectPixels: 20_000,
          visibilityState: "sufficient",
          landmarks: pose("reach", "down", 0.75),
        },
      ],
      0,
    );
    expect(result.tracks[0].label).toBe("Person detected · pose unavailable");
    expect(result.events).toEqual([]);
  });

  it("blocks a nearby pose whose torso or usable arm belongs outside the person box", () => {
    const box = { x: 0.46, y: 0.05, width: 0.08, height: 0.9 };
    const result = new LiveBehaviourEngine().update(
      [
        {
          box,
          detectorScore: 0.92,
          subjectPixels: 12_000,
          visibilityState: "sufficient",
          // The pose center and many face points are inside, but the shoulders,
          // hips and arm chain belong to a different/wider subject.
          landmarks: pose("reach"),
        },
      ],
      0,
    );
    expect(result.tracks[0]).toMatchObject({
      box,
      label: "Person detected · pose unavailable",
    });
    expect(result.events).toEqual([]);
  });

  it("does not accumulate hand evidence after a rule-critical wrist leaves its detector box", () => {
    const engine = new LiveBehaviourEngine();
    const box = { x: 0.14, y: 0.05, width: 0.72, height: 0.94 };
    const detected = (points: PosePoint[]) => ({
      box,
      detectorScore: 0.92,
      subjectPixels: 30_000,
      visibilityState: "sufficient" as const,
      landmarks: points,
    });
    for (const now of [0, 200, 400])
      expect(engine.update([detected(pose("reach"))], now).events).toEqual([]);
    const outside = pose("waist");
    outside[15] = { ...outside[15], x: 0.05 };
    expect(engine.update([detected(outside)], 600).events).toEqual([]);
    for (const now of [800, 1_000, 1_200])
      expect(engine.update([detected(pose("waist"))], now).events).toEqual([]);
    const firstCleanCycle: LiveBehaviourEvent[] = [];
    const secondCleanCycle: LiveBehaviourEvent[] = [];
    for (const [cycleIndex, start] of [1_400, 2_600].entries()) {
      for (const [offset, position] of [
        [0, "reach"],
        [200, "reach"],
        [400, "reach"],
        [600, "waist"],
        [800, "waist"],
        [1_000, "waist"],
      ] as const)
        (cycleIndex === 0 ? firstCleanCycle : secondCleanCycle).push(
          ...engine.update([detected(pose(position))], start + offset).events,
        );
    }
    expect(firstCleanCycle).toEqual([]);
    expect(secondCleanCycle).toHaveLength(1);
  });
});

describe("observed-limb exclusions and display-only smoothing", () => {
  it("hides a confident distant foot instead of stretching its box and skeleton onto a counter", () => {
    const engine = new LiveBehaviourEngine();
    const initial = engine.update([pose()], 0).tracks[0];
    const stretched = pose();
    stretched[31] = { ...stretched[31], x: 0.99, y: 0.99 };
    const track = engine.update([stretched], 250).tracks[0];
    expect(track.id).toBe(initial.id);
    expect(track.landmarks[31].visibility).toBe(0);
    expect(track.box.width).toBeCloseTo(initial.box.width);
    expect(track.box.height).toBeCloseTo(initial.box.height);
    expect(stretched[31].visibility).toBe(0.99);
  });

  it("excludes a stretched elbow and its wrist from behaviour even if both have high model confidence", () => {
    const engine = new LiveBehaviourEngine();
    cycle(engine, 0);
    const corrupted = pose("reach");
    corrupted[13] = { ...corrupted[13], x: 0.99, y: 0.85 };
    const result = engine.update([corrupted], 1200);
    expect(result.tracks[0].landmarks[13].visibility).toBe(0);
    expect(result.tracks[0].landmarks[15].visibility).toBe(0);
    expect(result.events).toEqual([]);
    expect(cycle(engine, 1400)).toEqual([]);
  });

  it("damps small stationary pose jitter without smoothing the event geometry", () => {
    const engine = new LiveBehaviourEngine();
    engine.update([pose()], 0);
    const jittered = pose().map((point) => ({ ...point, x: point.x + 0.01 }));
    const result = engine.update([jittered], 250);
    expect(result.tracks[0].landmarks[11].x).toBeGreaterThan(pose()[11].x);
    expect(result.tracks[0].landmarks[11].x).toBeLessThan(jittered[11].x);
    expect(result.events).toEqual([]);
    // Existing raw 200ms evidence timings remain unchanged by display smoothing.
    const ruleEngine = new LiveBehaviourEngine();
    cycle(ruleEngine, 0);
    expect(cycle(ruleEngine, 1200)[0].atMs).toBe(2200);
  });

  it("never carries missing or insufficient-confidence landmarks forward", () => {
    const engine = new LiveBehaviourEngine();
    engine.update([pose("waist")], 0);
    const hidden = pose("reach");
    hidden[15].visibility = 0.6;
    const missing = engine.update([hidden], 250).tracks[0];
    expect(missing.landmarks[15].visibility).toBe(0);
    const returned = pose("down");
    const result = engine.update([returned], 500);
    expect(result.tracks[0].landmarks[15].x).toBe(returned[15].x);
    expect(result.tracks[0].landmarks[15].y).toBe(returned[15].y);
    expect(result.events).toEqual([]);
  });

  it("resets display smoothing across a missed observation and does not invent a tracked pose", () => {
    const engine = new LiveBehaviourEngine();
    const original = engine.update([pose()], 0).tracks[0];
    expect(engine.update([], 250).tracks).toEqual([]);
    const moved = pose("down", "down", 0.51);
    const returned = engine.update([moved], 500).tracks[0];
    expect(returned.id).toBe(original.id);
    expect(returned.landmarks[11].x).toBe(moved[11].x);
  });

  it("does not turn a smoothed wrist that remains at the waist into a third raw waist observation", () => {
    const engine = new LiveBehaviourEngine();
    cycle(engine, 0);
    for (const now of [1200, 1400, 1600]) engine.update([pose("reach")], now);
    const edge = pose("waist");
    edge[15].y = 0.697;
    engine.update([edge], 1800);
    engine.update([edge], 2000);
    const outside = pose("waist");
    outside[15].y = 0.6982;
    const result = engine.update([outside], 2200);
    // The displayed blend falls within the 0.698 waist boundary; the actual
    // current wrist is outside, so it must reset the raw dwell instead of emit.
    expect(result.tracks[0].landmarks[15].y).toBeLessThan(0.698);
    expect(result.events).toEqual([]);
  });
});

describe("sensitivity at the supported 250ms processing cadence", () => {
  it("makes sensitive timing observably earlier while keeping balanced three-sample evidence", () => {
    const times = [];
    for (const sensitivity of ["sensitive", "balanced"] as const) {
      const engine = new LiveBehaviourEngine({ sensitivity });
      const events: LiveBehaviourEvent[] = [];
      for (const start of [0, 1500])
        for (const [offset, position] of [
          [0, "reach"],
          [250, "reach"],
          [500, "reach"],
          [750, "waist"],
          [1000, "waist"],
          [1250, "waist"],
        ] as const)
          events.push(
            ...engine.update([pose(position)], start + offset).events,
          );
      expect(events).toHaveLength(1);
      times.push(events[0].atMs);
    }
    expect(times).toEqual([2500, 2750]);
  });

  it("still rejects one-sample flickers at 250ms and resets hidden evidence in sensitive mode", () => {
    const engine = new LiveBehaviourEngine({ sensitivity: "sensitive" });
    for (let now = 0; now <= 5000; now += 250)
      expect(
        engine.update([pose(now % 500 === 0 ? "reach" : "waist")], now).events,
      ).toEqual([]);
    cycle(engine, 5250);
    const hidden = pose("reach");
    hidden[15].visibility = 0.1;
    expect(engine.update([hidden], 6500).events).toEqual([]);
    expect(cycle(engine, 6750)).toEqual([]);
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
