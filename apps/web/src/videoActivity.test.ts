import { describe, expect, it } from "vitest";
import {
  VIDEO_LIMITS,
  changedPixelRatio,
  groupActivitySamples,
  rgbaToLuminance,
  validateVideoFile,
  validateVideoMetadata,
} from "./videoActivity";
import type { ActivitySample } from "./videoActivity";

const file = { name: "synthetic.mp4", type: "video/mp4", size: 1024 };
const metadata = { duration: 30, width: 1920, height: 1080 };
const sample = (time: number, changedRatio = 0.1): ActivitySample => ({
  time,
  changedRatio,
});

describe("local video admission limits", () => {
  it("allows supported extensions when the operating system omits the MIME type", () => {
    expect(
      validateVideoFile({ ...file, name: "SYNTHETIC.MP4", type: "" }),
    ).toBeNull();
    expect(
      validateVideoFile({
        ...file,
        name: "test.webm",
        type: "application/octet-stream",
      }),
    ).toBeNull();
    expect(
      validateVideoFile({
        ...file,
        name: "clip",
        type: "video/webm; codecs=vp9",
      }),
    ).toBeNull();
  });
  it("rejects empty, invalid, oversized and unsupported files before decoding", () => {
    for (const size of [0, -1, NaN, Infinity, 0.5, VIDEO_LIMITS.maxBytes + 1])
      expect(validateVideoFile({ ...file, size })).not.toBeNull();
    expect(
      validateVideoFile({ ...file, name: "clip.mov", type: "video/quicktime" }),
    ).not.toBeNull();
    expect(
      validateVideoFile({ ...file, name: "clip.mp4.exe", type: "" }),
    ).not.toBeNull();
    expect(
      validateVideoFile({ ...file, size: VIDEO_LIMITS.maxBytes }),
    ).toBeNull();
  });
  it("bounds decoded duration and frame size independently of compressed file size", () => {
    expect(
      validateVideoMetadata({ duration: 600, width: 3840, height: 2160 }),
    ).toBeNull();
    expect(
      validateVideoMetadata({ duration: 0.5, width: 2160, height: 3840 }),
    ).toBeNull();
    for (const duration of [-1, 0, 0.49, 600.1, Infinity, NaN])
      expect(validateVideoMetadata({ ...metadata, duration })).not.toBeNull();
    for (const [width, height] of [
      [0, 1080],
      [1920, -1],
      [NaN, 1080],
      [1920.5, 1080],
      [4097, 100],
      [3000, 3000],
    ])
      expect(
        validateVideoMetadata({ ...metadata, width, height }),
      ).not.toBeNull();
  });
});

describe("sampled luminance comparison", () => {
  it("preserves grayscale and gives perceptual weight to colour channels", () => {
    expect(
      Array.from(
        rgbaToLuminance(
          new Uint8ClampedArray([
            0, 0, 0, 255, 255, 255, 255, 255, 100, 100, 100, 255, 255, 0, 0,
            255, 0, 255, 0, 255, 0, 0, 255, 255,
          ]),
        ),
      ),
    ).toEqual([0, 255, 100, 77, 149, 29]);
  });
  it("ignores low-amplitude pixel noise in an otherwise static scene", () => {
    const previous = new Uint8Array(100).fill(100);
    const current = Uint8Array.from(
      previous,
      (_, index) => 100 + (index % 47) - 23,
    );
    expect(changedPixelRatio(previous, current)).toBe(0);
  });
  it("counts only pixels meeting the luminance delta threshold", () => {
    const previous = new Uint8Array(100).fill(100);
    const current = new Uint8Array(previous);
    current[0] = 124;
    current[1] = 76;
    current[2] = 200;
    current[3] = 123;
    expect(changedPixelRatio(previous, current)).toBe(0.03);
    // A lighting change also triggers: this metric does not identify behaviour.
    expect(changedPixelRatio(previous, new Uint8Array(100).fill(150))).toBe(1);
  });
  it("rejects unusable or mismatched frames instead of reporting quiet activity", () => {
    expect(() => rgbaToLuminance(new Uint8Array(0))).toThrow(RangeError);
    expect(() => rgbaToLuminance(new Uint8Array(3))).toThrow(RangeError);
    expect(() =>
      changedPixelRatio(new Uint8Array(0), new Uint8Array(0)),
    ).toThrow(RangeError);
    expect(() =>
      changedPixelRatio(new Uint8Array(3), new Uint8Array(4)),
    ).toThrow(RangeError);
  });
});

describe("bounded visual activity grouping", () => {
  it("requires two consecutive active samples and ignores isolated changes", () => {
    expect(
      groupActivitySamples([sample(0.5), sample(1, 0), sample(1.5)]),
    ).toEqual([]);
    expect(
      groupActivitySamples([sample(0.5, 0.029), sample(1, 0.029)]),
    ).toEqual([]);
    expect(groupActivitySamples([sample(0.5, 0.03), sample(1, 0.03)])).toEqual([
      { start: 0, end: 1, peakChangedRatio: 0.03 },
    ]);
  });
  it("closes after two seconds quiet and keeps short quiet intervals within the same result", () => {
    expect(
      groupActivitySamples([
        sample(0.5),
        sample(1, 0.2),
        sample(1.5, 0),
        sample(2, 0.3),
        sample(2.5, 0),
        sample(3, 0),
        sample(3.5, 0),
        sample(4, 0),
        sample(4.5),
        sample(5),
      ]),
    ).toEqual([
      { start: 0, end: 2, peakChangedRatio: 0.3 },
      { start: 4, end: 5, peakChangedRatio: 0.1 },
    ]);
  });
  it("does not join changes across missing samples or backwards seeks", () => {
    expect(groupActivitySamples([sample(0.5), sample(2)])).toEqual([]);
    expect(groupActivitySamples([sample(1), sample(0.5)])).toEqual([]);
    expect(
      groupActivitySamples([sample(0.5), sample(1), sample(4), sample(4.5)]),
    ).toEqual([
      { start: 0, end: 1, peakChangedRatio: 0.1 },
      { start: 3.5, end: 4.5, peakChangedRatio: 0.1 },
    ]);
  });
  it("does not count duplicate times or malformed samples as continuing activity", () => {
    expect(groupActivitySamples([sample(0.5), sample(0.5)])).toEqual([]);
    for (const invalid of [
      sample(NaN),
      sample(-1),
      sample(601),
      sample(1, NaN),
      sample(1, -0.1),
      sample(1, 1.1),
    ])
      expect(groupActivitySamples([sample(0.5), invalid, sample(1.5)])).toEqual(
        [],
      );
  });
  it("returns a confirmed final segment for a short or cancelled test", () => {
    expect(
      groupActivitySamples([sample(0.5), sample(1), sample(1.5, 0)]),
    ).toEqual([{ start: 0, end: 1, peakChangedRatio: 0.1 }]);
    expect(groupActivitySamples([])).toEqual([]);
  });
  it("caps results while preserving the first observed groups", () => {
    const samples = Array.from({ length: 1200 }, (_, index) =>
      sample((index + 1) * 0.5, index % 6 < 2 ? 0.1 : 0),
    );
    const results = groupActivitySamples(samples);
    expect(results).toHaveLength(VIDEO_LIMITS.maxResults);
    expect(results[0]).toEqual({ start: 0, end: 1, peakChangedRatio: 0.1 });
    expect(results[99]).toEqual({
      start: 297,
      end: 298,
      peakChangedRatio: 0.1,
    });
  });
  it("never reads beyond the bounded sample budget", () => {
    const samples = Array.from(
      { length: VIDEO_LIMITS.maxSamples },
      (_, index) => sample(index * 0.5, 0),
    );
    Object.defineProperty(samples, VIDEO_LIMITS.maxSamples, {
      get() {
        throw new Error("Read beyond the sample budget");
      },
    });
    expect(groupActivitySamples(samples)).toEqual([]);
  });
});
