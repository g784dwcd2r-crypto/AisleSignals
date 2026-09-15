import { describe, expect, it } from "vitest";
import {
  applyLayoutObservationGuard,
  calibrationMatchesLayout,
  cameraIndexForCrop,
  pointInPolygon,
  rectangularZone,
} from "./layoutCalibration";

const person = (x: number, y: number) => ({
  box: { x, y, width: 0.2, height: 0.4 },
  detectorScore: 0.9,
  subjectPixels: 12000,
  visibilityState: "sufficient" as const,
  landmarks: Array.from({ length: 33 }, () => ({ x, y, visibility: 0.9 })),
});
const calibration: any = {
  cameras: [
    {
      camera_index: 1,
      crop: { x: 0.5, y: 0.5, width: 0.5, height: 0.5 },
      zones: [
        {
          id: "blind-1",
          kind: "BLIND",
          label: "Recorder overlay",
          points: [
            { x: 0.4, y: 0.4 },
            { x: 0.8, y: 0.4 },
            { x: 0.8, y: 1 },
            { x: 0.4, y: 1 },
          ],
        },
      ],
    },
  ],
};

describe("layout calibration observation boundary", () => {
  it("keeps person presence but removes pose evidence inside blind/ignored zones", () => {
    const [masked, visible] = applyLayoutObservationGuard(
      [person(0.45, 0.45), person(0.05, 0.1)],
      calibration,
      1,
    );
    expect(masked.landmarks).toBeNull();
    expect(masked.box).toEqual(person(0.45, 0.45).box);
    expect(visible.landmarks).not.toBeNull();
  });
  it("cannot affect another camera and maps only an exact confirmed crop", () => {
    expect(
      applyLayoutObservationGuard([person(0.45, 0.45)], calibration, 0)[0]
        .landmarks,
    ).not.toBeNull();
    expect(
      cameraIndexForCrop(
        calibration,
        [{ index: 1, crop: { x: 0.5, y: 0.5, width: 0.5, height: 0.5 } }],
        { x: 0.5, y: 0.5, width: 0.5, height: 0.5 },
      ),
    ).toBe(1);
    expect(cameraIndexForCrop(calibration, [], null)).toBeNull();
    expect(
      calibrationMatchesLayout(
        { ...calibration, layout: "2x2" },
        {
          layout: "2x2",
          tiles: [
            {
              index: 1,
              crop: { x: 0.5, y: 0.5, width: 0.5, height: 0.5 },
            },
          ],
        },
      ),
    ).toBe(true);
    expect(
      calibrationMatchesLayout(
        { ...calibration, layout: "2x2" },
        {
          layout: "2x2",
          tiles: [
            {
              index: 1,
              crop: { x: 0.49, y: 0.5, width: 0.5, height: 0.5 },
            },
          ],
        },
      ),
    ).toBe(false);
  });
  it("validates drawn rectangles and polygon membership", () => {
    const zone = rectangularZone(
      "shelf-1",
      "SHELF",
      "Cold remedies",
      { x: 0.8, y: 0.9 },
      { x: 0.2, y: 0.3 },
    );
    expect(pointInPolygon({ x: 0.5, y: 0.5 }, zone.points)).toBe(true);
    expect(() =>
      rectangularZone(
        "x",
        "SHELF",
        "x",
        { x: 0.1, y: 0.1 },
        { x: 0.11, y: 0.5 },
      ),
    ).toThrow(/2%/);
  });
});
