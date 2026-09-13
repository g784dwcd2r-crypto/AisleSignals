import { describe, expect, it } from "vitest";
import {
  poseFrameRegion,
  poseZoneInCamera,
  projectPoseTracks,
} from "./poseFrame";
import type { LiveTrack } from "./liveDetectionTypes";

describe("camera-local pose geometry", () => {
  it("crops a six-camera source before reducing the image, preserving tile detail", () => {
    expect(
      poseFrameRegion(1920, 1080, {
        x: 1 / 3,
        y: 0.5,
        width: 1 / 3,
        height: 0.5,
      }),
    ).toEqual({
      x: 640,
      y: 540,
      width: 640,
      height: 540,
      outputWidth: 640,
      outputHeight: 540,
      area: { x: 1 / 3, y: 0.5, width: 1 / 3, height: 0.5 },
    });
    expect(poseFrameRegion(1920, 1080).outputHeight).toBe(360);
  });
  it("projects boxes and joints using the actual rounded source pixels without changing quality", () => {
    const region = poseFrameRegion(601, 401, {
      x: 0.34,
      y: 0.51,
      width: 0.31,
      height: 0.48,
    });
    const track: LiveTrack = {
      id: 7,
      box: { x: 0.25, y: 0.1, width: 0.5, height: 0.8 },
      landmarks: [{ x: 0.4, y: 0.6, visibility: 0.8, presence: 0.9 }],
      status: "normal",
      label: "Person tracked",
      quality: 0.8,
    };
    const result = projectPoseTracks([track], region.area)[0];
    expect(result.box.x * 601).toBeCloseTo(region.x + region.width * 0.25);
    expect(result.landmarks[0].y * 401).toBeCloseTo(
      region.y + region.height * 0.6,
    );
    expect(result.landmarks[0].visibility).toBe(0.8);
    expect(track.landmarks[0].x).toBe(0.4);
  });
  it("only applies the intersection of a source zone with the selected camera", () => {
    const area = { x: 0.5, y: 0, width: 0.5, height: 0.5 };
    expect(
      poseZoneInCamera({ x: 0.25, y: 0.25, width: 0.5, height: 0.5 }, area),
    ).toEqual({ x: 0, y: 0.5, width: 0.5, height: 0.5 });
    expect(
      poseZoneInCamera({ x: 0, y: 0.5, width: 0.25, height: 0.5 }, area),
    ).toBeNull();
  });
  it("rejects invalid and undersized camera pixels", () => {
    expect(() => poseFrameRegion(0, 100)).toThrow();
    expect(() =>
      poseFrameRegion(600, 400, { x: 0.9, y: 0, width: 0.2, height: 1 }),
    ).toThrow();
    expect(() =>
      poseFrameRegion(600, 400, { x: 0, y: 0, width: 0.05, height: 0.05 }),
    ).toThrow("too small");
  });
});
