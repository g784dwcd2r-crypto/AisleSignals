import { describe, expect, it } from "vitest";
import {
  CustodyObservationLedger,
  associateHandWithProduct,
  classifyLocalObservation,
  deriveBodyInteractionRegions,
  evaluateObservationQuality,
  observeBagRegion,
  observeShelfChange,
  type LocalCustodyObservation,
  type FeatureEvidence,
  type ObjectKind,
} from "./custodyObservations";
import type { PosePoint } from "./liveDetectionTypes";

const rect = (x: number, y: number, width: number, height: number) => ({
  x,
  y,
  width,
  height,
});

const provenance = (): FeatureEvidence[] => [
  {
    kind: "PERSON_TRACK",
    source: "PERSON_DETECTOR",
    version: "test-v1",
    evidenceRef: "person:1",
  },
  {
    kind: "OBJECT_DETECTION",
    source: "OBJECT_DETECTOR",
    version: "test-v1",
    evidenceRef: "object:1",
  },
  {
    kind: "SHELF_DIFFERENCER",
    source: "SHELF_DIFFERENCER",
    version: "test-v1",
    evidenceRef: "shelf:1",
  },
  {
    kind: "HAND_OBJECT_ASSOCIATOR",
    source: "GEOMETRY_ASSOCIATOR",
    version: "test-v1",
    evidenceRef: "hand:1",
  },
  {
    kind: "BODY_OR_CONTAINER_GEOMETRY",
    source: "GEOMETRY_ASSOCIATOR",
    version: "test-v1",
    evidenceRef: "region:1",
  },
  {
    kind: "TEMPORAL_TRANSITION",
    source: "TEMPORAL_TRACKER",
    version: "test-v1",
    evidenceRef: "transition:1",
  },
  {
    kind: "AUTHENTICATED_STAFF_CONTEXT",
    source: "AUTHENTICATED_LOCAL_CONTEXT",
    version: "test-v1",
    evidenceRef: "staff:1",
  },
];

describe("source and visibility quality gates", () => {
  const input = {
    sourceWidth: 1280,
    sourceHeight: 720,
    shelf: rect(0.05, 0.1, 0.4, 0.7),
    person: rect(0.4, 0.1, 0.25, 0.75),
    product: rect(0.42, 0.35, 0.025, 0.05),
    handVisibility: 0.9,
  };
  it("accepts observable single-camera pixels", () =>
    expect(evaluateObservationQuality(input)).toMatchObject({
      ready: true,
      code: "READY",
      personHeightPixels: 540,
    }));
  it.each([
    [{ sourceWidth: 480 }, "LOW_SOURCE_RESOLUTION"],
    [{ shelf: rect(0.05, 0.1, 0.02, 0.05) }, "SHELF_ROI_TOO_SMALL"],
    [{ person: rect(0.4, 0.1, 0.03, 0.1) }, "PERSON_TOO_SMALL"],
    [{ product: rect(0.42, 0.35, 0.003, 0.005) }, "PRODUCT_TOO_SMALL"],
    [{ handVisibility: 0.4 }, "HAND_VISIBILITY_LOW"],
    [{ product: rect(-1, 0, 0.1, 0.1) }, "INVALID_GEOMETRY"],
  ])("rejects unobservable geometry %j", (change, code) =>
    expect(evaluateObservationQuality({ ...input, ...change }).code).toBe(code),
  );
});

describe("body-region contracts", () => {
  const pose = Array.from({ length: 33 }, () => ({
    x: 0.5,
    y: 0.5,
    visibility: 0,
    presence: 0,
  })) as PosePoint[];
  Object.assign(pose[11], { x: 0.4, y: 0.25, visibility: 0.9 });
  Object.assign(pose[12], { x: 0.6, y: 0.25, visibility: 0.9 });
  Object.assign(pose[23], { x: 0.43, y: 0.58, visibility: 0.9 });
  Object.assign(pose[24], { x: 0.57, y: 0.58, visibility: 0.9 });
  it("derives bounded torso, waist and pocket-proximity regions", () => {
    const regions = deriveBodyInteractionRegions(
      pose,
      rect(0.3, 0.1, 0.4, 0.8),
    );
    expect(regions).not.toBeNull();
    expect(regions!.torso.y).toBeCloseTo(0.25);
    expect(regions!.waist.y).toBeLessThan(0.58);
    expect(
      regions!.imageLeftPocket.x + regions!.imageLeftPocket.width,
    ).toBeCloseTo(0.5);
    expect(regions!.imageRightPocket.x).toBeCloseTo(0.5);
  });
  it("abstains when torso evidence is partial", () => {
    const partial = pose.map((point) => ({ ...point }));
    partial[24].visibility = 0.2;
    expect(
      deriveBodyInteractionRegions(partial, rect(0.3, 0.1, 0.4, 0.8)),
    ).toBeNull();
  });
});

describe("hand-to-product association", () => {
  const person = rect(0.3, 0.1, 0.4, 0.8);
  const object = (kind: ObjectKind, box = rect(0.49, 0.48, 0.04, 0.06)) => ({
    id: "synthetic-object",
    kind,
    box,
    confidence: 0.9,
  });
  const hand = {
    side: "RIGHT" as const,
    point: { x: 0.5, y: 0.5 },
    visibility: 0.9,
  };
  it("reports contact as proximity evidence without claiming ownership", () =>
    expect(
      associateHandWithProduct({
        hand,
        object: object("PRODUCT"),
        person,
        sourceAspectRatio: 16 / 9,
      }),
    ).toMatchObject({
      relation: "CONTACT",
      productId: "synthetic-object",
      observationalOnly: true,
    }));
  it.each(["PHONE", "WALLET", "BAG"] as const)(
    "never converts %s into a product association",
    (kind) =>
      expect(
        associateHandWithProduct({
          hand,
          object: object(kind),
          person,
          sourceAspectRatio: 16 / 9,
        }),
      ).toBeNull(),
  );
  it("rejects a distant tiny detection", () =>
    expect(
      associateHandWithProduct({
        hand,
        object: {
          ...object("PRODUCT", rect(0.05, 0.05, 0.01, 0.01)),
          confidence: 0.4,
        },
        person,
        sourceAspectRatio: 16 / 9,
      }),
    ).toBeNull());
});

describe("bag-region contract", () => {
  const person = rect(0.3, 0.1, 0.35, 0.8);
  it("requires an explicit nearby bag detection", () => {
    expect(
      observeBagRegion(
        {
          id: "bag-1",
          kind: "BAG",
          box: rect(0.55, 0.5, 0.18, 0.3),
          confidence: 0.9,
        },
        person,
        16 / 9,
      ),
    ).toMatchObject({ bagId: "bag-1", observationalOnly: true });
  });
  it.each(["PRODUCT", "PHONE", "UNKNOWN"] as const)(
    "does not invent a bag region from %s",
    (kind) =>
      expect(
        observeBagRegion(
          {
            id: "not-a-bag",
            kind,
            box: rect(0.55, 0.5, 0.18, 0.3),
            confidence: 0.95,
          },
          person,
          16 / 9,
        ),
      ).toBeNull(),
  );
});

const luma = (
  width: number,
  height: number,
  value: (x: number, y: number) => number,
) => ({
  width,
  height,
  luminance: Uint8Array.from({ length: width * height }, (_, index) =>
    value(index % width, Math.floor(index / width)),
  ),
});

describe("shelf change rejection", () => {
  const width = 320,
    height = 240,
    shelf = rect(0.5, 0.25, 0.35, 0.5);
  const textured = luma(width, height, (x, y) => ((x * 7 + y * 11) % 180) + 30);
  it("detects a localized shelf-cell change", () => {
    const changed = luma(width, height, (x, y) => {
      const base = ((x * 7 + y * 11) % 180) + 30;
      return x >= 175 && x < 225 && y >= 90 && y < 150
        ? Math.min(255, base + 70)
        : base;
    });
    expect(observeShelfChange(textured, changed, shelf)).toMatchObject({
      code: "LOCALIZED_SHELF_CHANGE",
      observationalOnly: true,
    });
  });
  it("rejects a uniform lighting change", () => {
    const before = luma(width, height, () => 80);
    const after = luma(width, height, () => 110);
    expect(observeShelfChange(before, after, shelf).code).toBe(
      "LIGHTING_CHANGE",
    );
  });
  it("rejects a whole-camera one-pixel shift", () => {
    const shifted = luma(width, height, (x, y) =>
      x === 0 ? 0 : (((x - 1) * 7 + y * 11) % 180) + 30,
    );
    expect(observeShelfChange(textured, shifted, shelf)).toMatchObject({
      code: "CAMERA_MOTION",
      estimatedShift: [1, 0],
    });
  });
  it("does not call unchanged shelves an event", () =>
    expect(observeShelfChange(textured, textured, shelf).code).toBe(
      "NO_LOCAL_CHANGE",
    ));
});

describe("hard-negative observational classifications", () => {
  const base = {
    id: "observation-1",
    cameraId: "camera-1",
    captureSessionId: "session-1",
    trackId: 7,
    sourceTimestampMs: 1000,
    atMs: 1000,
    clockSkewMs: 0,
    clockUncertaintyMs: 20,
    object: null,
    productAssociated: false,
    shelfChange: "NO_LOCAL_CHANGE" as const,
    nearPocket: false,
    nearBag: false,
    returnedToShelf: false,
    placedInBasket: false,
    confirmedStaff: false,
    bulkShelfPlacements: 0,
    qualityReady: true,
    trackContinuity: "OBSERVED_UNAMBIGUOUS" as const,
    provenance: provenance(),
  };
  const detected = (kind: ObjectKind) => ({
    id: `synthetic-${kind.toLowerCase()}`,
    kind,
    box: rect(0.4, 0.4, 0.05, 0.08),
    confidence: 0.9,
  });
  it.each(["PHONE", "WALLET"] as const)("keeps %s handling negative", (kind) =>
    expect(
      classifyLocalObservation({
        ...base,
        object: detected(kind),
        nearPocket: true,
      }).kind,
    ).toBe("PERSONAL_ITEM_HANDLING"),
  );
  it("keeps clothing adjustment negative without a product association", () =>
    expect(classifyLocalObservation({ ...base, nearPocket: true }).kind).toBe(
      "CLOTHING_ADJUSTMENT_WITHOUT_PRODUCT",
    ));
  it("keeps ordinary bag handling negative without a product association", () =>
    expect(
      classifyLocalObservation({
        ...base,
        object: detected("BAG"),
        nearBag: true,
      }).kind,
    ).toBe("BAG_HANDLING_WITHOUT_PRODUCT"));
  it("does not trust a product-association flag without a product object", () =>
    expect(
      classifyLocalObservation({
        ...base,
        productAssociated: true,
        nearPocket: true,
      }).kind,
    ).toBe("CLOTHING_ADJUSTMENT_WITHOUT_PRODUCT"));
  it("records confirmed staff bulk placement as a normal alternative", () =>
    expect(
      classifyLocalObservation({
        ...base,
        object: detected("PRODUCT"),
        productAssociated: true,
        confirmedStaff: true,
        bulkShelfPlacements: 4,
      }).kind,
    ).toBe("CONFIRMED_STAFF_BULK_SHELF_PLACEMENT"));
  it("describes product and pocket proximity without claiming concealment", () => {
    const value = classifyLocalObservation({
      ...base,
      object: detected("PRODUCT"),
      productAssociated: true,
      nearPocket: true,
    });
    expect(value.kind).toBe("PRODUCT_NEAR_WAIST_OR_POCKET");
    expect(value).not.toHaveProperty("alarmEligible");
    expect(value).not.toHaveProperty("theft");
  });
  it.each([
    { nearPocket: true, nearBag: true },
    { nearPocket: true, returnedToShelf: true },
    { returnedToShelf: true, placedInBasket: true },
  ])("rejects contradictory location claims %j", (claims) =>
    expect(() =>
      classifyLocalObservation({
        ...base,
        ...claims,
        object: detected("PRODUCT"),
        productAssociated: true,
      }),
    ).toThrow("Contradictory"),
  );
  it("requires authenticated local context for staff state", () => {
    const invalid = provenance().map((item) =>
      item.kind === "AUTHENTICATED_STAFF_CONTEXT"
        ? { ...item, source: "PERSON_DETECTOR" as const }
        : item,
    );
    expect(() =>
      classifyLocalObservation({
        ...base,
        object: detected("PRODUCT"),
        productAssociated: true,
        confirmedStaff: true,
        bulkShelfPlacements: 4,
        provenance: invalid,
      }),
    ).toThrow("bounded local feature observation");
  });
  it("abstains when tracker continuity is ambiguous", () =>
    expect(
      classifyLocalObservation({
        ...base,
        object: detected("PRODUCT"),
        productAssociated: true,
        nearPocket: true,
        trackContinuity: "AMBIGUOUS",
      }).kind,
    ).toBe("VISIBILITY_INSUFFICIENT"));
});

describe("temporal observation ledger", () => {
  const event = (
    id: string,
    atMs: number,
    kind = "PRODUCT_SHELF_CHANGE_WITH_HAND_PROXIMITY",
  ) =>
    ({
      id,
      cameraId: "camera-1",
      captureSessionId: "session-1",
      trackId: 4,
      sourceTimestampMs: atMs,
      atMs,
      clockSkewMs: 0,
      clockUncertaintyMs: 20,
      kind,
      objectId: "product-1",
      evidence: [],
      trackContinuity: "OBSERVED_UNAMBIGUOUS",
      provenance: provenance(),
      observationalOnly: true,
    }) as LocalCustodyObservation;
  it("groups only continuous same-track facts and bounds history", () => {
    const ledger = new CustodyObservationLedger(3000);
    expect(ledger.observe(event("a", 1000)).observations).toHaveLength(1);
    const continued = ledger.observe(event("b", 2500, "PRODUCT_NEAR_BAG"));
    expect(continued.id).toBe("a");
    expect(continued.observations.map((item) => item.kind)).toEqual([
      "PRODUCT_SHELF_CHANGE_WITH_HAND_PROXIMITY",
      "PRODUCT_NEAR_BAG",
    ]);
    expect(continued).not.toHaveProperty("alarmEligible");
    const later = ledger.observe(event("c", 7000));
    expect(later.id).toBe("c");
    expect(later.observations).toHaveLength(1);
  });
  it("rejects time reversal and clears camera continuity", () => {
    const ledger = new CustodyObservationLedger();
    ledger.observe(event("a", 1000));
    expect(() => ledger.observe(event("b", 900))).toThrow("chronological");
    ledger.discontinuity("camera-1");
    expect(ledger.snapshot()).toEqual([]);
  });
  it("never joins different product tokens for one person track", () => {
    const ledger = new CustodyObservationLedger();
    const first = ledger.observe(event("a", 1000));
    const second = ledger.observe({
      ...event("b", 1200),
      objectId: "product-2",
    });
    expect(first.id).toBe("a");
    expect(second.id).toBe("b");
    expect(ledger.snapshot()).toHaveLength(2);
  });
  it("never joins different capture sessions", () => {
    const ledger = new CustodyObservationLedger();
    ledger.observe(event("a", 1000));
    const second = ledger.observe({
      ...event("b", 1200),
      captureSessionId: "session-2",
    });
    expect(second.id).toBe("b");
    expect(ledger.snapshot()).toHaveLength(2);
  });
  it("rejects product observations without a stable object token", () => {
    const ledger = new CustodyObservationLedger();
    expect(() =>
      ledger.observe({ ...event("a", 1000), objectId: null }),
    ).toThrow("stable object token");
  });
  it("breaks a track episode on ambiguous continuity", () => {
    const ledger = new CustodyObservationLedger();
    ledger.observe(event("a", 1000));
    ledger.observe({
      ...event("uncertain", 1100, "VISIBILITY_INSUFFICIENT"),
      trackContinuity: "AMBIGUOUS",
    });
    expect(ledger.snapshot()).toEqual([]);
    expect(ledger.observe(event("new", 1200)).id).toBe("new");
  });
  it.each([-1, NaN, Infinity, 249, 10001])(
    "rejects invalid episode gap %s",
    (gap) =>
      expect(() => new CustodyObservationLedger(gap)).toThrow("Episode gap"),
  );
  it("rejects unsafe clocks and excess skew uncertainty", () => {
    const ledger = new CustodyObservationLedger();
    expect(() =>
      ledger.observe({ ...event("fraction", 1000), atMs: 1000.5 }),
    ).toThrow("bounded local observation");
    expect(() =>
      ledger.observe({
        ...event("skew", 1000),
        atMs: 2000,
        clockUncertaintyMs: 20,
      }),
    ).toThrow("bounded local observation");
  });
  it("bounds episodes per camera and expires old sessions", () => {
    const ledger = new CustodyObservationLedger(3000, 3, 2, 5000);
    ledger.observe(event("old", 1000));
    ledger.observe({ ...event("second", 1100), objectId: "product-2" });
    ledger.observe({ ...event("third", 1200), objectId: "product-3" });
    expect(
      ledger
        .snapshot()
        .map((item) => item.id)
        .sort(),
    ).toEqual(["second", "third"]);
    ledger.observe({ ...event("fresh", 7001), objectId: "product-4" });
    expect(ledger.snapshot().map((item) => item.id)).toEqual(["fresh"]);
  });
  it("clears only the exact camera and protects stored history from mutation", () => {
    const ledger = new CustodyObservationLedger();
    const mutable = event("a", 1000) as unknown as LocalCustodyObservation & {
      evidence: string[];
      provenance: FeatureEvidence[];
    };
    mutable.evidence = ["original"];
    mutable.provenance = provenance();
    ledger.observe(mutable);
    ledger.observe({ ...event("b", 1000), cameraId: "camera-1:sub" });
    mutable.evidence[0] = "rewritten";
    mutable.provenance[0] = {
      ...mutable.provenance[0],
      evidenceRef: "changed",
    };
    expect(ledger.snapshot()[0].observations[0].evidence).toEqual(["original"]);
    expect(ledger.snapshot()[0].observations[0].provenance[0].evidenceRef).toBe(
      "person:1",
    );
    ledger.discontinuity("camera-1");
    const remaining = ledger.snapshot();
    expect(remaining).toHaveLength(1);
    expect(remaining[0].cameraId).toBe("camera-1:sub");
    expect(remaining[0].observations[0].evidence).toEqual([]);
  });
});
