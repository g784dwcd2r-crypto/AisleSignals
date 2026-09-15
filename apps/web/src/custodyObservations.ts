import type { DetectionRect, PosePoint } from "./liveDetectionTypes";

/** Local visual facts only. None of these observations is theft or alarm authority. */
export type ObjectKind =
  "PRODUCT" | "PHONE" | "WALLET" | "BAG" | "BASKET" | "UNKNOWN";
const OBJECT_KINDS = new Set<ObjectKind>([
  "PRODUCT",
  "PHONE",
  "WALLET",
  "BAG",
  "BASKET",
  "UNKNOWN",
]);
export type TrackedObject = Readonly<{
  id: string;
  kind: ObjectKind;
  box: DetectionRect;
  confidence: number;
}>;
export type HandObservation = Readonly<{
  side: "LEFT" | "RIGHT";
  point: Readonly<{ x: number; y: number }>;
  visibility: number;
}>;
export type BodyInteractionRegions = Readonly<{
  torso: DetectionRect;
  waist: DetectionRect;
  imageLeftPocket: DetectionRect;
  imageRightPocket: DetectionRect;
}>;

export type SourceQualityCode =
  | "READY"
  | "INVALID_GEOMETRY"
  | "LOW_SOURCE_RESOLUTION"
  | "SHELF_ROI_TOO_SMALL"
  | "PERSON_TOO_SMALL"
  | "PRODUCT_TOO_SMALL"
  | "HAND_VISIBILITY_LOW";

export type SourceQuality = Readonly<{
  ready: boolean;
  code: SourceQualityCode;
  sourcePixels: number;
  shelfPixels: number;
  personHeightPixels: number;
  productPixels: number;
}>;

const finiteRect = (value: DetectionRect) =>
  [value.x, value.y, value.width, value.height].every(Number.isFinite) &&
  value.x >= 0 &&
  value.y >= 0 &&
  value.width > 0 &&
  value.height > 0 &&
  value.x + value.width <= 1.000001 &&
  value.y + value.height <= 1.000001;

const pixelArea = (box: DetectionRect, width: number, height: number) =>
  Math.max(0, Math.round(box.width * width)) *
  Math.max(0, Math.round(box.height * height));

export function evaluateObservationQuality(input: {
  sourceWidth: number;
  sourceHeight: number;
  shelf: DetectionRect;
  person: DetectionRect;
  product: DetectionRect;
  handVisibility: number;
}): SourceQuality {
  const { sourceWidth: width, sourceHeight: height } = input;
  const validDimensions =
    Number.isInteger(width) &&
    Number.isInteger(height) &&
    width >= 1 &&
    height >= 1 &&
    width <= 8192 &&
    height <= 8192;
  const geometry =
    validDimensions &&
    finiteRect(input.shelf) &&
    finiteRect(input.person) &&
    finiteRect(input.product) &&
    Number.isFinite(input.handVisibility) &&
    input.handVisibility >= 0 &&
    input.handVisibility <= 1;
  const values = {
    sourcePixels: validDimensions ? width * height : 0,
    shelfPixels: geometry ? pixelArea(input.shelf, width, height) : 0,
    personHeightPixels: geometry ? Math.round(input.person.height * height) : 0,
    productPixels: geometry ? pixelArea(input.product, width, height) : 0,
  };
  const code: SourceQualityCode = !geometry
    ? "INVALID_GEOMETRY"
    : width < 640 || height < 360
      ? "LOW_SOURCE_RESOLUTION"
      : Math.round(input.shelf.width * width) < 96 ||
          Math.round(input.shelf.height * height) < 96
        ? "SHELF_ROI_TOO_SMALL"
        : values.personHeightPixels < 120 ||
            Math.round(input.person.width * width) < 40
          ? "PERSON_TOO_SMALL"
          : values.productPixels < 64 ||
              Math.round(input.product.width * width) < 6 ||
              Math.round(input.product.height * height) < 6
            ? "PRODUCT_TOO_SMALL"
            : input.handVisibility < 0.65
              ? "HAND_VISIBILITY_LOW"
              : "READY";
  return { ready: code === "READY", code, ...values };
}

const pointQuality = (point: PosePoint | undefined) =>
  !!point &&
  Number.isFinite(point.x) &&
  Number.isFinite(point.y) &&
  point.x >= 0 &&
  point.x <= 1 &&
  point.y >= 0 &&
  point.y <= 1 &&
  Number.isFinite(point.visibility) &&
  point.visibility! >= 0.65;

const boundedRect = (
  left: number,
  top: number,
  right: number,
  bottom: number,
) => ({
  x: Math.max(0, left),
  y: Math.max(0, top),
  width: Math.max(0.001, Math.min(1, right) - Math.max(0, left)),
  height: Math.max(0.001, Math.min(1, bottom) - Math.max(0, top)),
});

/** Regions are pose geometry, not pockets or concealed-item detection. */
export function deriveBodyInteractionRegions(
  landmarks: readonly PosePoint[],
  person: DetectionRect,
): BodyInteractionRegions | null {
  if (!finiteRect(person) || landmarks.length < 33) return null;
  const required = [11, 12, 23, 24];
  if (required.some((index) => !pointQuality(landmarks[index]))) return null;
  const shoulderY = (landmarks[11].y + landmarks[12].y) / 2;
  const hipY = (landmarks[23].y + landmarks[24].y) / 2;
  const left = Math.min(
    landmarks[11].x,
    landmarks[12].x,
    landmarks[23].x,
    landmarks[24].x,
  );
  const right = Math.max(
    landmarks[11].x,
    landmarks[12].x,
    landmarks[23].x,
    landmarks[24].x,
  );
  const torsoWidth = Math.max(right - left, person.width * 0.25);
  const torsoHeight = hipY - shoulderY;
  if (torsoHeight < person.height * 0.12 || torsoWidth < person.width * 0.12)
    return null;
  const waistTop = hipY - torsoHeight * 0.2;
  const waistBottom = hipY + torsoHeight * 0.28;
  const middle = (left + right) / 2;
  return Object.freeze({
    torso: boundedRect(left, shoulderY, right, hipY),
    waist: boundedRect(
      left - torsoWidth * 0.12,
      waistTop,
      right + torsoWidth * 0.12,
      waistBottom,
    ),
    imageLeftPocket: boundedRect(
      left - torsoWidth * 0.08,
      waistTop,
      middle,
      waistBottom,
    ),
    imageRightPocket: boundedRect(
      middle,
      waistTop,
      right + torsoWidth * 0.08,
      waistBottom,
    ),
  });
}

export type BagRegionObservation = Readonly<{
  bagId: string;
  region: DetectionRect;
  relationToPerson: "OVERLAPS_PERSON" | "ADJACENT_TO_PERSON";
  observationalOnly: true;
}>;

const rectanglesOverlap = (left: DetectionRect, right: DetectionRect) =>
  left.x < right.x + right.width &&
  left.x + left.width > right.x &&
  left.y < right.y + right.height &&
  left.y + left.height > right.y;

/** A bag region requires an explicit object detection; pose geometry cannot invent it. */
export function observeBagRegion(
  object: TrackedObject,
  person: DetectionRect,
  sourceAspectRatio: number,
): BagRegionObservation | null {
  if (
    object.kind !== "BAG" ||
    !object.id ||
    object.confidence < 0.65 ||
    object.confidence > 1 ||
    !finiteRect(object.box) ||
    !finiteRect(person) ||
    !Number.isFinite(sourceAspectRatio) ||
    sourceAspectRatio <= 0
  )
    return null;
  const bagCenter = {
      x: object.box.x + object.box.width / 2,
      y: object.box.y + object.box.height / 2,
    },
    personCenter = {
      x: person.x + person.width / 2,
      y: person.y + person.height / 2,
    };
  const distance = Math.hypot(
    (bagCenter.x - personCenter.x) * sourceAspectRatio,
    bagCenter.y - personCenter.y,
  );
  if (distance > person.height * 0.8) return null;
  return Object.freeze({
    bagId: object.id,
    region: { ...object.box },
    relationToPerson: rectanglesOverlap(object.box, person)
      ? "OVERLAPS_PERSON"
      : "ADJACENT_TO_PERSON",
    observationalOnly: true,
  });
}

const pointRectDistance = (
  point: { x: number; y: number },
  box: DetectionRect,
  aspect: number,
) => {
  const dx = Math.max(box.x - point.x, 0, point.x - box.x - box.width) * aspect;
  const dy = Math.max(box.y - point.y, 0, point.y - box.y - box.height);
  return Math.hypot(dx, dy);
};

export type HandProductAssociation = Readonly<{
  hand: "LEFT" | "RIGHT";
  productId: string;
  relation: "CONTACT" | "NEAR";
  distanceInPersonHeights: number;
  observationalOnly: true;
}>;

/** Geometric proximity only; it does not establish possession or ownership. */
export function associateHandWithProduct(input: {
  hand: HandObservation;
  object: TrackedObject;
  person: DetectionRect;
  sourceAspectRatio: number;
}): HandProductAssociation | null {
  const { hand, object, person, sourceAspectRatio: aspect } = input;
  if (
    object.kind !== "PRODUCT" ||
    !object.id ||
    object.confidence < 0.55 ||
    object.confidence > 1 ||
    hand.visibility < 0.65 ||
    hand.visibility > 1 ||
    !Number.isFinite(hand.point.x) ||
    !Number.isFinite(hand.point.y) ||
    hand.point.x < 0 ||
    hand.point.x > 1 ||
    hand.point.y < 0 ||
    hand.point.y > 1 ||
    !finiteRect(object.box) ||
    !finiteRect(person) ||
    !Number.isFinite(aspect) ||
    aspect <= 0
  )
    return null;
  const distance = pointRectDistance(hand.point, object.box, aspect);
  const scale = Math.max(person.height, person.width / aspect);
  if (distance > scale * 0.18) return null;
  return Object.freeze({
    hand: hand.side,
    productId: object.id,
    relation: distance === 0 ? "CONTACT" : "NEAR",
    distanceInPersonHeights: distance / Math.max(person.height, 0.001),
    observationalOnly: true,
  });
}

export type ShelfChangeCode =
  | "NO_LOCAL_CHANGE"
  | "LOCALIZED_SHELF_CHANGE"
  | "LIGHTING_CHANGE"
  | "CAMERA_MOTION"
  | "SCENE_ACTIVITY_UNRESOLVED"
  | "INVALID_FRAME";
const SHELF_CHANGE_CODES = new Set<ShelfChangeCode>([
  "NO_LOCAL_CHANGE",
  "LOCALIZED_SHELF_CHANGE",
  "LIGHTING_CHANGE",
  "CAMERA_MOTION",
  "SCENE_ACTIVITY_UNRESOLVED",
  "INVALID_FRAME",
]);
export type ShelfChangeObservation = Readonly<{
  code: ShelfChangeCode;
  changedRatioInside: number;
  changedRatioOutside: number;
  meanLuminanceShift: number;
  estimatedShift: readonly [number, number];
  observationalOnly: true;
}>;

type LumaFrame = Readonly<{
  width: number;
  height: number;
  luminance: Uint8Array;
}>;

function meanAbsoluteDifference(
  previous: Uint8Array,
  current: Uint8Array,
  width: number,
  height: number,
  dx = 0,
  dy = 0,
) {
  let total = 0,
    count = 0;
  for (let y = Math.max(0, -dy); y < Math.min(height, height - dy); y += 2)
    for (let x = Math.max(0, -dx); x < Math.min(width, width - dx); x += 2) {
      total += Math.abs(
        current[(y + dy) * width + x + dx] - previous[y * width + x],
      );
      count++;
    }
  return count ? total / count : Infinity;
}

/** Compare a shelf ROI while rejecting uniform lighting and whole-frame shifts. */
export function observeShelfChange(
  previous: LumaFrame,
  current: LumaFrame,
  shelf: DetectionRect,
): ShelfChangeObservation {
  const invalid =
    previous.width !== current.width ||
    previous.height !== current.height ||
    previous.width < 64 ||
    previous.height < 64 ||
    previous.width > 4096 ||
    previous.height > 2160 ||
    previous.width * previous.height > 3840 * 2160 ||
    previous.luminance.length !== previous.width * previous.height ||
    current.luminance.length !== current.width * current.height ||
    !finiteRect(shelf) ||
    Math.round(shelf.width * current.width) < 96 ||
    Math.round(shelf.height * current.height) < 96;
  const empty = (code: ShelfChangeCode): ShelfChangeObservation => ({
    code,
    changedRatioInside: 0,
    changedRatioOutside: 0,
    meanLuminanceShift: 0,
    estimatedShift: [0, 0],
    observationalOnly: true,
  });
  if (invalid) return empty("INVALID_FRAME");
  const { width, height } = current;
  let signed = 0,
    sameDirection = 0;
  for (let index = 0; index < current.luminance.length; index++) {
    const delta = current.luminance[index] - previous.luminance[index];
    signed += delta;
    if (Math.abs(delta) >= 8) sameDirection += Math.sign(delta);
  }
  const meanShift = signed / current.luminance.length;
  const uniformDirection = Math.abs(sameDirection) / current.luminance.length;
  let residual = 0;
  for (let index = 0; index < current.luminance.length; index++)
    residual += Math.abs(
      current.luminance[index] - previous.luminance[index] - meanShift,
    );
  residual /= current.luminance.length;
  if (Math.abs(meanShift) >= 10 && uniformDirection >= 0.75 && residual <= 8)
    return { ...empty("LIGHTING_CHANGE"), meanLuminanceShift: meanShift };

  const baseline = meanAbsoluteDifference(
    previous.luminance,
    current.luminance,
    width,
    height,
  );
  let best = baseline,
    shiftX = 0,
    shiftY = 0;
  for (let dy = -2; dy <= 2; dy++)
    for (let dx = -2; dx <= 2; dx++) {
      if (dx === 0 && dy === 0) continue;
      const score = meanAbsoluteDifference(
        previous.luminance,
        current.luminance,
        width,
        height,
        dx,
        dy,
      );
      if (score < best) {
        best = score;
        shiftX = dx;
        shiftY = dy;
      }
    }
  if ((shiftX || shiftY) && baseline >= 5 && best <= baseline * 0.55)
    return {
      ...empty("CAMERA_MOTION"),
      meanLuminanceShift: meanShift,
      estimatedShift: [shiftX, shiftY],
    };

  const left = Math.floor(shelf.x * width),
    top = Math.floor(shelf.y * height),
    right = Math.ceil((shelf.x + shelf.width) * width),
    bottom = Math.ceil((shelf.y + shelf.height) * height);
  let inside = 0,
    insideChanged = 0,
    outside = 0,
    outsideChanged = 0;
  for (let y = 0; y < height; y++)
    for (let x = 0; x < width; x++) {
      const index = y * width + x;
      const changed =
        Math.abs(
          current.luminance[index] - previous.luminance[index] - meanShift,
        ) >= 22;
      if (x >= left && x < right && y >= top && y < bottom) {
        inside++;
        insideChanged += Number(changed);
      } else {
        outside++;
        outsideChanged += Number(changed);
      }
    }
  const insideRatio = insideChanged / Math.max(inside, 1),
    outsideRatio = outsideChanged / Math.max(outside, 1);
  const code: ShelfChangeCode =
    insideRatio >= 0.04 && outsideRatio <= 0.025
      ? "LOCALIZED_SHELF_CHANGE"
      : outsideRatio > 0.025
        ? "SCENE_ACTIVITY_UNRESOLVED"
        : "NO_LOCAL_CHANGE";
  return {
    code,
    changedRatioInside: insideRatio,
    changedRatioOutside: outsideRatio,
    meanLuminanceShift: meanShift,
    estimatedShift: [0, 0],
    observationalOnly: true,
  };
}

export type LocalObservationKind =
  | "PRODUCT_SHELF_CHANGE_WITH_HAND_PROXIMITY"
  | "PRODUCT_NEAR_WAIST_OR_POCKET"
  | "PRODUCT_NEAR_BAG"
  | "PRODUCT_RETURNED_TO_SHELF"
  | "PRODUCT_PLACED_IN_BASKET"
  | "PERSONAL_ITEM_HANDLING"
  | "CLOTHING_ADJUSTMENT_WITHOUT_PRODUCT"
  | "BAG_HANDLING_WITHOUT_PRODUCT"
  | "CONFIRMED_STAFF_BULK_SHELF_PLACEMENT"
  | "VISIBILITY_INSUFFICIENT";

export type LocalCustodyObservation = Readonly<{
  id: string;
  cameraId: string;
  captureSessionId: string;
  trackId: number;
  sourceTimestampMs: number;
  atMs: number;
  clockSkewMs: number;
  clockUncertaintyMs: number;
  kind: LocalObservationKind;
  objectId: string | null;
  evidence: readonly string[];
  trackContinuity: TrackContinuity;
  provenance: readonly FeatureEvidence[];
  observationalOnly: true;
}>;

export type TrackContinuity =
  "OBSERVED_UNAMBIGUOUS" | "AMBIGUOUS" | "RECOVERED" | "SWITCHED";
export type FeatureEvidence = Readonly<{
  kind:
    | "PERSON_TRACK"
    | "OBJECT_DETECTION"
    | "SHELF_DIFFERENCER"
    | "HAND_OBJECT_ASSOCIATOR"
    | "BODY_OR_CONTAINER_GEOMETRY"
    | "TEMPORAL_TRANSITION"
    | "AUTHENTICATED_STAFF_CONTEXT";
  source:
    | "PERSON_DETECTOR"
    | "OBJECT_DETECTOR"
    | "SHELF_DIFFERENCER"
    | "GEOMETRY_ASSOCIATOR"
    | "TEMPORAL_TRACKER"
    | "AUTHENTICATED_LOCAL_CONTEXT";
  version: string;
  evidenceRef: string;
}>;

const FEATURE_KINDS = new Set<FeatureEvidence["kind"]>([
  "PERSON_TRACK",
  "OBJECT_DETECTION",
  "SHELF_DIFFERENCER",
  "HAND_OBJECT_ASSOCIATOR",
  "BODY_OR_CONTAINER_GEOMETRY",
  "TEMPORAL_TRANSITION",
  "AUTHENTICATED_STAFF_CONTEXT",
]);
const FEATURE_SOURCES = new Set<FeatureEvidence["source"]>([
  "PERSON_DETECTOR",
  "OBJECT_DETECTOR",
  "SHELF_DIFFERENCER",
  "GEOMETRY_ASSOCIATOR",
  "TEMPORAL_TRACKER",
  "AUTHENTICATED_LOCAL_CONTEXT",
]);
const TRACK_CONTINUITY = new Set<TrackContinuity>([
  "OBSERVED_UNAMBIGUOUS",
  "AMBIGUOUS",
  "RECOVERED",
  "SWITCHED",
]);
const boundedToken = (value: unknown, maximum = 100) =>
  typeof value === "string" &&
  value.length >= 1 &&
  value.length <= maximum &&
  !/[\x00-\x1f\x7f]/.test(value);
const expectedFeatureSource: Readonly<
  Record<FeatureEvidence["kind"], FeatureEvidence["source"]>
> = Object.freeze({
  PERSON_TRACK: "PERSON_DETECTOR",
  OBJECT_DETECTION: "OBJECT_DETECTOR",
  SHELF_DIFFERENCER: "SHELF_DIFFERENCER",
  HAND_OBJECT_ASSOCIATOR: "GEOMETRY_ASSOCIATOR",
  BODY_OR_CONTAINER_GEOMETRY: "GEOMETRY_ASSOCIATOR",
  TEMPORAL_TRANSITION: "TEMPORAL_TRACKER",
  AUTHENTICATED_STAFF_CONTEXT: "AUTHENTICATED_LOCAL_CONTEXT",
});
const validFeatureEvidence = (item: FeatureEvidence) =>
  !!item &&
  FEATURE_KINDS.has(item.kind) &&
  FEATURE_SOURCES.has(item.source) &&
  expectedFeatureSource[item.kind] === item.source &&
  typeof item.version === "string" &&
  /^[A-Za-z0-9_.-]{1,64}$/.test(item.version) &&
  typeof item.evidenceRef === "string" &&
  /^[A-Za-z0-9_.:-]{1,100}$/.test(item.evidenceRef);

const LOCAL_OBSERVATION_KINDS = new Set<LocalObservationKind>([
  "PRODUCT_SHELF_CHANGE_WITH_HAND_PROXIMITY",
  "PRODUCT_NEAR_WAIST_OR_POCKET",
  "PRODUCT_NEAR_BAG",
  "PRODUCT_RETURNED_TO_SHELF",
  "PRODUCT_PLACED_IN_BASKET",
  "PERSONAL_ITEM_HANDLING",
  "CLOTHING_ADJUSTMENT_WITHOUT_PRODUCT",
  "BAG_HANDLING_WITHOUT_PRODUCT",
  "CONFIRMED_STAFF_BULK_SHELF_PLACEMENT",
  "VISIBILITY_INSUFFICIENT",
]);

export function classifyLocalObservation(input: {
  id: string;
  cameraId: string;
  captureSessionId: string;
  trackId: number;
  sourceTimestampMs: number;
  atMs: number;
  clockSkewMs: number;
  clockUncertaintyMs: number;
  object: TrackedObject | null;
  productAssociated: boolean;
  shelfChange: ShelfChangeCode;
  nearPocket: boolean;
  nearBag: boolean;
  returnedToShelf: boolean;
  placedInBasket: boolean;
  confirmedStaff: boolean;
  bulkShelfPlacements: number;
  qualityReady: boolean;
  trackContinuity: TrackContinuity;
  provenance: readonly FeatureEvidence[];
}): LocalCustodyObservation {
  const validProvenance =
    Array.isArray(input.provenance) &&
    input.provenance.length >= 1 &&
    input.provenance.length <= 8 &&
    input.provenance.every(validFeatureEvidence);
  if (
    !boundedToken(input.id) ||
    !boundedToken(input.cameraId) ||
    !boundedToken(input.captureSessionId) ||
    !Number.isSafeInteger(input.trackId) ||
    input.trackId < 1 ||
    !Number.isSafeInteger(input.sourceTimestampMs) ||
    input.sourceTimestampMs < 0 ||
    !Number.isSafeInteger(input.atMs) ||
    input.atMs < 0 ||
    !Number.isSafeInteger(input.clockSkewMs) ||
    Math.abs(input.clockSkewMs) > 60_000 ||
    !Number.isSafeInteger(input.clockUncertaintyMs) ||
    input.clockUncertaintyMs < 0 ||
    input.clockUncertaintyMs > 250 ||
    Math.abs(input.atMs - input.sourceTimestampMs - input.clockSkewMs) >
      input.clockUncertaintyMs ||
    !Number.isSafeInteger(input.bulkShelfPlacements) ||
    input.bulkShelfPlacements < 0 ||
    input.bulkShelfPlacements > 100 ||
    !SHELF_CHANGE_CODES.has(input.shelfChange) ||
    !TRACK_CONTINUITY.has(input.trackContinuity) ||
    !validProvenance ||
    [
      input.productAssociated,
      input.nearPocket,
      input.nearBag,
      input.returnedToShelf,
      input.placedInBasket,
      input.confirmedStaff,
      input.qualityReady,
    ].some((value) => typeof value !== "boolean") ||
    (input.object !== null &&
      (!input.object.id ||
        !OBJECT_KINDS.has(input.object.kind) ||
        !finiteRect(input.object.box) ||
        !Number.isFinite(input.object.confidence) ||
        input.object.confidence < 0 ||
        input.object.confidence > 1))
  )
    throw new Error("A bounded local feature observation is required.");
  const featureKinds = new Set(input.provenance.map((item) => item.kind));
  const requires = (kind: FeatureEvidence["kind"]) => {
    if (!featureKinds.has(kind))
      throw new Error(`Missing required ${kind} provenance.`);
  };
  requires("PERSON_TRACK");
  if (input.object) requires("OBJECT_DETECTION");
  if (input.productAssociated) requires("HAND_OBJECT_ASSOCIATOR");
  if (input.shelfChange !== "NO_LOCAL_CHANGE") requires("SHELF_DIFFERENCER");
  if (input.nearPocket || input.nearBag) requires("BODY_OR_CONTAINER_GEOMETRY");
  if (input.returnedToShelf || input.placedInBasket)
    requires("TEMPORAL_TRANSITION");
  if (input.confirmedStaff) {
    requires("AUTHENTICATED_STAFF_CONTEXT");
    const context = input.provenance.find(
      (item) => item.kind === "AUTHENTICATED_STAFF_CONTEXT",
    );
    if (context?.source !== "AUTHENTICATED_LOCAL_CONTEXT")
      throw new Error(
        "Staff context must come from authenticated local state.",
      );
  }
  const locationClaims = [
    input.nearPocket,
    input.nearBag,
    input.returnedToShelf,
    input.placedInBasket,
  ].filter(Boolean).length;
  if (locationClaims > 1)
    throw new Error(
      "Contradictory product location observations are rejected.",
    );
  if (
    (input.returnedToShelf || input.placedInBasket) &&
    (!input.productAssociated || input.object?.kind !== "PRODUCT")
  )
    throw new Error(
      "A terminal product transition requires an associated product.",
    );
  if (
    input.confirmedStaff &&
    input.bulkShelfPlacements >= 3 &&
    locationClaims > 0
  )
    throw new Error(
      "Bulk shelf placement cannot share a terminal/location claim.",
    );
  let kind: LocalObservationKind;
  const objectId = input.object?.id ?? null;
  const hasAssociatedProduct =
    input.productAssociated && input.object?.kind === "PRODUCT";
  if (!input.qualityReady || input.trackContinuity !== "OBSERVED_UNAMBIGUOUS")
    kind = "VISIBILITY_INSUFFICIENT";
  else if (input.object?.kind === "PHONE" || input.object?.kind === "WALLET")
    kind = "PERSONAL_ITEM_HANDLING";
  else if (
    input.confirmedStaff &&
    hasAssociatedProduct &&
    input.bulkShelfPlacements >= 3
  )
    kind = "CONFIRMED_STAFF_BULK_SHELF_PLACEMENT";
  else if (input.returnedToShelf && hasAssociatedProduct)
    kind = "PRODUCT_RETURNED_TO_SHELF";
  else if (input.placedInBasket && hasAssociatedProduct)
    kind = "PRODUCT_PLACED_IN_BASKET";
  else if (input.nearBag && !hasAssociatedProduct)
    kind = "BAG_HANDLING_WITHOUT_PRODUCT";
  else if (input.nearPocket && !hasAssociatedProduct)
    kind = "CLOTHING_ADJUSTMENT_WITHOUT_PRODUCT";
  else if (input.nearBag && hasAssociatedProduct) kind = "PRODUCT_NEAR_BAG";
  else if (input.nearPocket && hasAssociatedProduct)
    kind = "PRODUCT_NEAR_WAIST_OR_POCKET";
  else if (
    hasAssociatedProduct &&
    input.shelfChange === "LOCALIZED_SHELF_CHANGE"
  )
    kind = "PRODUCT_SHELF_CHANGE_WITH_HAND_PROXIMITY";
  else kind = "VISIBILITY_INSUFFICIENT";
  return Object.freeze({
    id: input.id,
    cameraId: input.cameraId,
    captureSessionId: input.captureSessionId,
    trackId: input.trackId,
    sourceTimestampMs: input.sourceTimestampMs,
    atMs: input.atMs,
    clockSkewMs: input.clockSkewMs,
    clockUncertaintyMs: input.clockUncertaintyMs,
    kind,
    objectId,
    evidence: Object.freeze([
      `shelf:${input.shelfChange}`,
      `product-associated:${hasAssociatedProduct}`,
    ]),
    trackContinuity: input.trackContinuity,
    provenance: Object.freeze(
      input.provenance.map((item) => Object.freeze({ ...item })),
    ),
    observationalOnly: true,
  });
}

export type ObservationEpisode = Readonly<{
  id: string;
  cameraId: string;
  captureSessionId: string;
  trackId: number;
  objectId: string | null;
  startedAt: number;
  lastObservedAt: number;
  observations: readonly LocalCustodyObservation[];
  observationalOnly: true;
}>;

/** Bounded temporal grouping. It preserves facts and never scores theft or sound. */
export class CustodyObservationLedger {
  private readonly episodes = new Map<
    string,
    Map<string, ObservationEpisode>
  >();
  private readonly maxGapMs: number;
  private readonly maxEpisodes: number;
  private readonly maxEpisodesPerCamera: number;
  private readonly retentionMs: number;
  constructor(
    maxGapMs = 3_000,
    maxEpisodes = 512,
    maxEpisodesPerCamera = 128,
    retentionMs = 60_000,
  ) {
    if (!Number.isFinite(maxGapMs) || maxGapMs < 250 || maxGapMs > 10_000)
      throw new Error(
        "Episode gap must be between 250 and 10000 milliseconds.",
      );
    this.maxGapMs = maxGapMs;
    if (
      !Number.isSafeInteger(maxEpisodes) ||
      maxEpisodes < 1 ||
      maxEpisodes > 4096 ||
      !Number.isSafeInteger(maxEpisodesPerCamera) ||
      maxEpisodesPerCamera < 1 ||
      maxEpisodesPerCamera > maxEpisodes ||
      !Number.isSafeInteger(retentionMs) ||
      retentionMs < maxGapMs ||
      retentionMs > 3_600_000
    )
      throw new Error("Episode bounds and retention are invalid.");
    this.maxEpisodes = maxEpisodes;
    this.maxEpisodesPerCamera = maxEpisodesPerCamera;
    this.retentionMs = retentionMs;
  }

  observe(value: LocalCustodyObservation): ObservationEpisode {
    if (
      !value.id ||
      !boundedToken(value.id) ||
      !boundedToken(value.cameraId) ||
      !boundedToken(value.captureSessionId) ||
      !Number.isSafeInteger(value.trackId) ||
      value.trackId < 1 ||
      !Number.isSafeInteger(value.sourceTimestampMs) ||
      value.sourceTimestampMs < 0 ||
      !Number.isSafeInteger(value.atMs) ||
      value.atMs < 0 ||
      !Number.isSafeInteger(value.clockSkewMs) ||
      Math.abs(value.clockSkewMs) > 60_000 ||
      !Number.isSafeInteger(value.clockUncertaintyMs) ||
      value.clockUncertaintyMs < 0 ||
      value.clockUncertaintyMs > 250 ||
      Math.abs(value.atMs - value.sourceTimestampMs - value.clockSkewMs) >
        value.clockUncertaintyMs ||
      !value.observationalOnly ||
      !LOCAL_OBSERVATION_KINDS.has(value.kind) ||
      !Array.isArray(value.evidence) ||
      value.evidence.length > 8 ||
      value.evidence.some(
        (item) =>
          typeof item !== "string" ||
          item.length < 1 ||
          item.length > 128 ||
          /[\x00-\x1f\x7f]/.test(item),
      ) ||
      !TRACK_CONTINUITY.has(value.trackContinuity) ||
      !Array.isArray(value.provenance) ||
      value.provenance.length < 1 ||
      value.provenance.length > 8 ||
      !value.provenance.every(validFeatureEvidence) ||
      (value.objectId !== null && !boundedToken(value.objectId))
    )
      throw new Error("A bounded local observation is required.");
    if (value.trackContinuity !== "OBSERVED_UNAMBIGUOUS") {
      this.deleteTrack(value.cameraId, value.trackId);
      return Object.freeze({
        id: value.id,
        cameraId: value.cameraId,
        captureSessionId: value.captureSessionId,
        trackId: value.trackId,
        objectId: value.objectId,
        startedAt: value.atMs,
        lastObservedAt: value.atMs,
        observations: Object.freeze([this.copyObservation(value)]),
        observationalOnly: true as const,
      });
    }
    const productKinds = new Set<LocalObservationKind>([
      "PRODUCT_SHELF_CHANGE_WITH_HAND_PROXIMITY",
      "PRODUCT_NEAR_WAIST_OR_POCKET",
      "PRODUCT_NEAR_BAG",
      "PRODUCT_RETURNED_TO_SHELF",
      "PRODUCT_PLACED_IN_BASKET",
    ]);
    if (productKinds.has(value.kind) && !value.objectId)
      throw new Error("Product observations require a stable object token.");
    this.expireBefore(value.atMs - this.retentionMs);
    const cameraEpisodes = this.episodes.get(value.cameraId) ?? new Map();
    this.episodes.set(value.cameraId, cameraEpisodes);
    const key = `${value.captureSessionId}\0${value.trackId}\0${value.objectId ?? "NO_OBJECT"}`;
    const previous = cameraEpisodes.get(key);
    if (previous && value.atMs <= previous.lastObservedAt)
      throw new Error("Observations must be strictly chronological per track.");
    const continued =
      previous && value.atMs - previous.lastObservedAt <= this.maxGapMs;
    const accepted = this.copyObservation(value);
    const observations = continued
      ? [...previous.observations, accepted].slice(-32)
      : [accepted];
    const episode = Object.freeze({
      id: continued ? previous.id : value.id,
      cameraId: value.cameraId,
      captureSessionId: value.captureSessionId,
      trackId: value.trackId,
      objectId: value.objectId,
      startedAt: continued ? previous.startedAt : value.atMs,
      lastObservedAt: value.atMs,
      observations: Object.freeze(observations),
      observationalOnly: true as const,
    });
    cameraEpisodes.set(key, episode);
    this.enforceBounds(cameraEpisodes);
    return episode;
  }

  private expireBefore(cutoff: number) {
    for (const [cameraId, camera] of this.episodes) {
      for (const [key, episode] of camera)
        if (episode.lastObservedAt < cutoff) camera.delete(key);
      if (!camera.size) this.episodes.delete(cameraId);
    }
  }

  private enforceBounds(camera: Map<string, ObservationEpisode>) {
    while (camera.size > this.maxEpisodesPerCamera) {
      const oldest = [...camera.entries()].sort(
        (left, right) => left[1].lastObservedAt - right[1].lastObservedAt,
      )[0];
      camera.delete(oldest[0]);
    }
    while (this.snapshot().length > this.maxEpisodes) {
      const oldest = [...this.episodes.entries()]
        .flatMap(([cameraId, episodes]) =>
          [...episodes.entries()].map(([key, episode]) => ({
            cameraId,
            key,
            at: episode.lastObservedAt,
          })),
        )
        .sort((left, right) => left.at - right.at)[0];
      const episodes = this.episodes.get(oldest.cameraId)!;
      episodes.delete(oldest.key);
      if (!episodes.size) this.episodes.delete(oldest.cameraId);
    }
  }

  private copyObservation(value: LocalCustodyObservation) {
    return Object.freeze({
      ...value,
      evidence: Object.freeze([...value.evidence]),
      provenance: Object.freeze(
        value.provenance.map((item) => Object.freeze({ ...item })),
      ),
    });
  }

  private deleteTrack(cameraId: string, trackId: number) {
    const camera = this.episodes.get(cameraId);
    if (!camera) return;
    for (const [key, episode] of camera)
      if (episode.trackId === trackId) camera.delete(key);
    if (!camera.size) this.episodes.delete(cameraId);
  }

  discontinuity(cameraId?: string) {
    if (!cameraId) this.episodes.clear();
    else this.episodes.delete(cameraId);
  }

  snapshot(): readonly ObservationEpisode[] {
    return Object.freeze(
      [...this.episodes.values()].flatMap((camera) => [...camera.values()]),
    );
  }
}
