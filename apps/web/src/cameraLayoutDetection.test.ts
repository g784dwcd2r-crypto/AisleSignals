import { describe, expect, it } from "vitest";
import { detectCameraLayout, type LayoutFrame } from "./cameraLayoutDetection";

function scene(width = 600, height = 400): LayoutFrame {
  const data = new Uint8ClampedArray(width * height * 4);
  for (let y = 0; y < height; y++) {
    for (let x = 0; x < width; x++) {
      const at = (y * width + x) * 4;
      const value =
        55 +
        ((x * 7 + y * 11 + Math.floor(x / 17) * 23 + Math.floor(y / 13) * 19) %
          120);
      data.set(
        [value, Math.min(220, value + 11), Math.min(230, value + 23), 255],
        at,
      );
    }
  }
  return { width, height, data };
}

function band(
  frame: LayoutFrame,
  axis: "vertical" | "horizontal",
  centre: number,
  thickness = 5,
  colour = 12,
  gap = false,
) {
  const start = Math.floor(centre - thickness / 2);
  for (let y = 0; y < frame.height; y++) {
    for (let x = 0; x < frame.width; x++) {
      if (gap && (axis === "vertical" ? y : x) % 83 < 7) continue;
      const position = axis === "vertical" ? x : y;
      if (position >= start && position < start + thickness) {
        frame.data.set(
          [colour, colour, colour, 255],
          (y * frame.width + x) * 4,
        );
      }
    }
  }
}

function mosaic(columns: number, rows: number, colour = 12) {
  const frame = scene();
  for (let column = 1; column < columns; column++)
    band(frame, "vertical", (frame.width * column) / columns, 5, colour);
  for (let row = 1; row < rows; row++)
    band(frame, "horizontal", (frame.height * row) / rows, 5, colour);
  return frame;
}

describe("local, confirmation-required CCTV layout proposals", () => {
  it.each([
    [2, 2, "2x2"],
    [3, 2, "3x2"],
    [2, 3, "2x3"],
  ] as const)(
    "proposes a visible %i by %i board",
    (columns, rows, expected) => {
      const result = detectCameraLayout(mosaic(columns, rows));
      expect(result.status, JSON.stringify(result)).toBe("proposed");
      expect(result.layout).toBe(expected);
      expect(result.boundaries.vertical).toHaveLength(columns - 1);
      expect(result.boundaries.horizontal).toHaveLength(rows - 1);
      result.boundaries.vertical.forEach((value, index) =>
        expect(value).toBeCloseTo((index + 1) / columns, 2),
      );
      result.boundaries.horizontal.forEach((value, index) =>
        expect(value).toBeCloseTo((index + 1) / rows, 2),
      );
      expect(result.confidence).toBeGreaterThanOrEqual(0.71);
      expect(result.confidence).toBeLessThanOrEqual(0.9);
      expect(result.requiresConfirmation).toBe(true);
      expect(result.evidence.minimumSeparatorScore).toBeGreaterThanOrEqual(
        0.69,
      );
    },
  );

  it("supports light separator gutters too", () => {
    expect(detectCameraLayout(mosaic(3, 2, 250)).layout).toBe("3x2");
  });

  it("can see a one-pixel divider when that pixel survives capture", () => {
    const frame = scene();
    band(frame, "vertical", 300, 1);
    band(frame, "horizontal", 200, 1);
    expect(detectCameraLayout(frame).layout).toBe("2x2");
  });

  it("measures mildly uneven, interrupted separators instead of returning ideal coordinates", () => {
    const frame = scene();
    band(frame, "vertical", 207, 3, 12, true);
    band(frame, "vertical", 394, 7, 12, true);
    band(frame, "horizontal", 204, 5, 12, true);
    const result = detectCameraLayout(frame);
    expect(result.status, JSON.stringify(result)).toBe("proposed");
    expect(result.layout).toBe("3x2");
    expect(result.boundaries.vertical[0]).toBeCloseTo(207 / 600, 2);
    expect(result.boundaries.vertical[1]).toBeCloseTo(394 / 600, 2);
    expect(result.boundaries.horizontal[0]).toBeCloseTo(204 / 400, 2);
    expect(result.boundaries.vertical[0]).not.toBe(1 / 3);
  });

  it("returns source coordinates for an explicitly selected board within viewer chrome", () => {
    const inner = mosaic(2, 2);
    const frame = scene(800, 600);
    for (let y = 0; y < inner.height; y++) {
      frame.data.set(
        inner.data.subarray(y * inner.width * 4, (y + 1) * inner.width * 4),
        ((y + 80) * frame.width + 100) * 4,
      );
    }
    const board = {
      x: 100 / 800,
      y: 80 / 600,
      width: 600 / 800,
      height: 400 / 600,
    };
    const result = detectCameraLayout(frame, { board });
    expect(result.layout, JSON.stringify(result)).toBe("2x2");
    expect(result.board).toEqual(board);
    expect(result.boundaries.vertical[0]).toBeCloseTo(400 / 800, 2);
    expect(result.boundaries.horizontal[0]).toBeCloseTo(280 / 600, 2);
  });

  it.each([0, 80, 255])("abstains on a uniform %i frame", (colour) => {
    const frame = scene();
    for (let at = 0; at < frame.data.length; at += 4)
      frame.data.set([colour, colour, colour, 255], at);
    const result = detectCameraLayout(frame);
    expect(result.status).toBe("unclear");
    expect(result.reason).toBe("INSUFFICIENT_DETAIL");
    expect(result.layout).toBeNull();
    expect(result.boundaries).toEqual({ vertical: [], horizontal: [] });
  });

  it("does not mistake a transparent/unready image for a board", () => {
    const frame = mosaic(2, 2);
    for (let at = 3; at < frame.data.length; at += 4) frame.data[at] = 0;
    expect(detectCameraLayout(frame).reason).toBe("INSUFFICIENT_DETAIL");
  });

  it("does not infer single-camera certainty from a textured scene without gutters", () => {
    const result = detectCameraLayout(scene());
    expect(result.status, JSON.stringify(result)).toBe("unclear");
    expect(result.reason).toBe("NO_RELIABLE_GRID");
    expect(result.layout).toBeNull();
    expect(
      result.candidates.find((candidate) => candidate.layout === "single")
        ?.score,
    ).toBeLessThan(0.5);
  });

  it("requires both orthogonal directions, not one scene edge or split view", () => {
    const frame = scene();
    band(frame, "vertical", 300);
    const result = detectCameraLayout(frame);
    expect(result.layout).toBeNull();
    expect(result.reason).toBe("NO_RELIABLE_GRID");
  });

  it("does not treat wide black scene regions as narrow recorder gutters", () => {
    const frame = scene();
    band(frame, "vertical", 300, 60);
    band(frame, "horizontal", 200, 40);
    expect(detectCameraLayout(frame).layout).toBeNull();
  });

  it("abstains when four- and six-tile separators compete", () => {
    const frame = mosaic(3, 2);
    band(frame, "vertical", 300);
    const result = detectCameraLayout(frame);
    expect(result.status, JSON.stringify(result)).toBe("unclear");
    expect(result.layout).toBeNull();
    expect(result.reason).toBe("AMBIGUOUS_LAYOUT");
    expect(result.boundaries).toEqual({ vertical: [], horizontal: [] });
  });

  it("rejects apparent grids made only from blank/offline tiles", () => {
    const frame = scene();
    for (let at = 0; at < frame.data.length; at += 4)
      frame.data.set([160, 160, 160, 255], at);
    band(frame, "vertical", 300);
    band(frame, "horizontal", 200);
    expect(detectCameraLayout(frame).layout).toBeNull();
  });

  it("abstains when separators cover only half the board", () => {
    const frame = mosaic(2, 2);
    const original = scene();
    for (let y = 0; y < frame.height / 2; y++) {
      for (let x = 0; x < frame.width; x++) {
        const at = (y * frame.width + x) * 4;
        frame.data.set(original.data.subarray(at, at + 4), at);
      }
    }
    expect(detectCameraLayout(frame).layout).toBeNull();
  });

  it.each([
    { width: 2, height: 2, data: new Uint8Array(16) },
    { width: 600, height: 400, data: new Uint8Array(12) },
    { width: Number.NaN, height: 400, data: new Uint8Array() },
    { width: 4097, height: 400, data: new Uint8Array() },
  ])(
    "bounds invalid pixel inputs without an allocation or exception",
    (frame) => {
      expect(detectCameraLayout(frame).reason).toBe("INVALID_FRAME");
    },
  );

  it.each([
    { x: -0.1, y: 0, width: 1, height: 1 },
    { x: 0.8, y: 0, width: 0.5, height: 1 },
    { x: 0, y: Number.NaN, width: 1, height: 1 },
    { x: 0, y: 0, width: 0.01, height: 1 },
  ])("rejects invalid or unreadably small board crops", (board) => {
    expect(detectCameraLayout(scene(), { board }).reason).toBe("INVALID_BOARD");
  });

  it("does not mutate the source pixels or the caller's board", () => {
    const frame = mosaic(2, 2);
    const before = frame.data.slice();
    const board = Object.freeze({ x: 0, y: 0, width: 1, height: 1 });
    detectCameraLayout(frame, { board });
    expect(frame.data).toEqual(before);
  });
});
