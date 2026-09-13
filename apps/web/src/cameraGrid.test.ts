import { describe, expect, it } from "vitest";
import {
  CAMERA_GRID_LAYOUTS,
  cameraGridTiles,
  isCameraGridLayout,
  isCameraTileIndex,
  mapCameraGridToArea,
  resolveCameraArea,
  selectCameraTile,
  validateCameraArea,
} from "./cameraGrid";
import type {
  CameraAreaSelection,
  CameraGridLayout,
  CameraGridBoundaries,
} from "./cameraGrid";
import { interactionCropPixels } from "./interactionCapture";

describe("camera-grid geometry", () => {
  it.each([
    ["single", 1, 1],
    ["2x2", 2, 2],
    ["3x2", 3, 2],
    ["2x3", 2, 3],
  ] as const)(
    "partitions %s into %i columns and %i rows",
    (layout, columns, rows) => {
      const tiles = cameraGridTiles(layout);
      expect(tiles).toHaveLength(columns * rows);
      expect(new Set(tiles.map((tile) => tile.id)).size).toBe(tiles.length);
      expect(tiles.map((tile) => tile.label)).toEqual(
        Array.from({ length: columns * rows }, (_, i) => `Camera ${i + 1}`),
      );
      expect(tiles.map((tile) => tile.index)).toEqual(
        Array.from({ length: columns * rows }, (_, i) => i),
      );
      expect(
        tiles.reduce(
          (area, tile) => area + tile.crop.width * tile.crop.height,
          0,
        ),
      ).toBeCloseTo(1);
      for (const tile of tiles) {
        expect(tile.row).toBe(Math.floor(tile.index / columns));
        expect(tile.column).toBe(tile.index % columns);
        expect(tile.crop.x).toBeCloseTo(tile.column / columns);
        expect(tile.crop.y).toBeCloseTo(tile.row / rows);
        expect(tile.crop.x + tile.crop.width).toBeLessThanOrEqual(1);
        expect(tile.crop.y + tile.crop.height).toBeLessThanOrEqual(1);
        expect(validateCameraArea(tile.crop)).toEqual(tile.crop);
        for (const other of tiles.filter(
          (candidate) => candidate.index > tile.index,
        )) {
          const overlapWidth =
            Math.min(
              tile.crop.x + tile.crop.width,
              other.crop.x + other.crop.width,
            ) - Math.max(tile.crop.x, other.crop.x);
          const overlapHeight =
            Math.min(
              tile.crop.y + tile.crop.height,
              other.crop.y + other.crop.height,
            ) - Math.max(tile.crop.y, other.crop.y);
          expect(
            Math.max(0, overlapWidth) * Math.max(0, overlapHeight),
          ).toBeCloseTo(0);
        }
      }
    },
  );

  it("distinguishes the fourth camera in landscape and portrait six-camera grids", () => {
    expect(selectCameraTile("3x2", 3)).toEqual({
      id: "grid-3x2-camera-4",
      label: "Camera 4",
      index: 3,
      row: 1,
      column: 0,
      crop: { x: 0, y: 0.5, width: 1 / 3, height: 0.5 },
    });
    expect(selectCameraTile("2x3", 3)).toEqual({
      id: "grid-2x3-camera-4",
      label: "Camera 4",
      index: 3,
      row: 1,
      column: 1,
      crop: { x: 0.5, y: 1 / 3, width: 0.5, height: 1 / 3 },
    });
  });

  it("feeds original source coordinates directly into the existing pixel cropper", () => {
    const crop = resolveCameraArea({ kind: "tile", layout: "3x2", index: 5 });
    expect(interactionCropPixels(1920, 1080, crop)).toEqual({
      x: 1280,
      y: 540,
      width: 640,
      height: 540,
      outputWidth: 640,
      outputHeight: 540,
    });
    expect(
      interactionCropPixels(1080, 1920, selectCameraTile("2x3", 5).crop),
    ).toEqual({
      x: 540,
      y: 1280,
      width: 540,
      height: 640,
      outputWidth: 540,
      outputHeight: 640,
    });
  });

  it("keeps ids deterministic while isolating different layout contexts", () => {
    expect(cameraGridTiles("2x2")).toEqual(cameraGridTiles("2x2"));
    const firstIds = CAMERA_GRID_LAYOUTS.map(
      (layout) => selectCameraTile(layout, 0).id,
    );
    expect(new Set(firstIds).size).toBe(4);
    const changed = selectCameraTile("2x2", 0);
    changed.crop.x = 0.9;
    changed.label = "Changed";
    expect(selectCameraTile("2x2", 0).crop.x).toBe(0);
    expect(selectCameraTile("2x2", 0).label).toBe("Camera 1");
  });
});

describe("camera boards and measured separators", () => {
  const board = { x: 0.1, y: 0.2, width: 0.8, height: 0.6 };

  it("evenly partitions an offset board without shifting into tile-local coordinates", () => {
    const tiles = mapCameraGridToArea("2x2", board);
    expect(tiles).toHaveLength(4);
    expect(tiles[3]).toMatchObject({
      id: "grid-2x2-camera-4",
      label: "Camera 4",
      row: 1,
      column: 1,
    });
    expect(tiles[3].crop.x).toBeCloseTo(0.5);
    expect(tiles[3].crop.y).toBeCloseTo(0.5);
    expect(tiles[3].crop.width).toBeCloseTo(0.4);
    expect(tiles[3].crop.height).toBeCloseTo(0.3);
    expect(interactionCropPixels(1920, 1080, tiles[3].crop)).toMatchObject({
      x: 960,
      y: 540,
      width: 768,
      height: 324,
    });
    const single = mapCameraGridToArea("single", board)[0].crop;
    for (const field of ["x", "y", "width", "height"] as const)
      expect(single[field]).toBeCloseTo(board[field]);
  });

  it("uses unequal full-source separator centres and preserves geometry at shared edges", () => {
    const boundaries = { vertical: [0.3, 0.65], horizontal: [0.45] };
    const tiles = mapCameraGridToArea("3x2", board, boundaries);
    expect(tiles[0].crop.x).toBe(0.1);
    expect(tiles[0].crop.width).toBeCloseTo(0.2);
    expect(tiles[4].crop).toEqual({
      x: 0.3,
      y: 0.45,
      width: 0.65 - 0.3,
      height: 0.8 - 0.45,
    });
    expect(tiles[5].crop.x + tiles[5].crop.width).toBeCloseTo(0.9);
    expect(tiles[0].crop.x + tiles[0].crop.width).toBe(tiles[1].crop.x);
    expect(tiles[1].crop.y + tiles[1].crop.height).toBe(tiles[4].crop.y);
    expect(
      tiles.reduce(
        (total, tile) => total + tile.crop.width * tile.crop.height,
        0,
      ),
    ).toBeCloseTo(board.width * board.height);
    expect(boundaries).toEqual({ vertical: [0.3, 0.65], horizontal: [0.45] });
    expect(board).toEqual({ x: 0.1, y: 0.2, width: 0.8, height: 0.6 });
  });

  it.each([
    { vertical: [0.3], horizontal: [0.45] },
    { vertical: [0.3, 0.65], horizontal: [] },
    { vertical: [0.65, 0.3], horizontal: [0.45] },
    { vertical: [0.3, 0.3], horizontal: [0.45] },
    { vertical: [0.1, 0.65], horizontal: [0.45] },
    { vertical: [0.3, 0.9], horizontal: [0.45] },
    { vertical: [0.3, 0.65], horizontal: [0.1] },
    { vertical: [NaN, 0.65], horizontal: [0.45] },
    { vertical: [0.3, 0.65], horizontal: [Infinity] },
    { vertical: ["0.3", 0.65], horizontal: [0.45] },
    { vertical: [0.3, 0.65] },
  ])(
    "rejects malformed measured separators rather than clamping %j",
    (boundaries) => {
      expect(() =>
        mapCameraGridToArea("3x2", board, boundaries as CameraGridBoundaries),
      ).toThrow();
    },
  );

  it("rejects out-of-source boards and resulting camera rectangles too small for the sampler", () => {
    expect(() =>
      mapCameraGridToArea("2x2", { x: 0.5, y: 0, width: 0.8, height: 1 }),
    ).toThrow();
    expect(() =>
      mapCameraGridToArea("3x2", { x: 0, y: 0, width: 0.12, height: 1 }),
    ).toThrow("at least 5%");
    expect(() =>
      mapCameraGridToArea("3x2", board, {
        vertical: [0.11, 0.65],
        horizontal: [0.45],
      }),
    ).toThrow("at least 5%");
  });
});

describe("selection validation", () => {
  it.each([null, undefined, "custom", "4x4", "6", "2X3", "__proto__", {}, 4])(
    "rejects unsupported layout %j",
    (layout) => {
      expect(isCameraGridLayout(layout)).toBe(false);
      expect(isCameraTileIndex(layout, 0)).toBe(false);
      expect(() => cameraGridTiles(layout as CameraGridLayout)).toThrow(
        "supported camera-grid",
      );
    },
  );

  it.each([-1, 4, 5, 1.5, NaN, Infinity, "1", null])(
    "rejects invalid 2x2 index %j",
    (index) => {
      expect(isCameraTileIndex("2x2", index)).toBe(false);
      expect(() => selectCameraTile("2x2", index as number)).toThrow(
        "contained in the selected grid",
      );
    },
  );

  it("uses zero-based indices and rejects a previous six-grid selection in a four-grid layout", () => {
    expect(isCameraTileIndex("single", 0)).toBe(true);
    expect(isCameraTileIndex("single", 1)).toBe(false);
    expect(isCameraTileIndex("3x2", 5)).toBe(true);
    expect(isCameraTileIndex("3x2", 6)).toBe(false);
    expect(() =>
      resolveCameraArea({ kind: "tile", layout: "2x2", index: 5 }),
    ).toThrow();
  });

  it("preserves an existing custom area in full-source coordinates without nesting it", () => {
    const crop = { x: 0.6, y: 0.2, width: 0.3, height: 0.5 };
    const selected = resolveCameraArea({
      kind: "custom",
      coordinateSpace: "source",
      crop,
    });
    expect(selected).toEqual(crop);
    expect(selected).not.toBe(crop);
    expect(interactionCropPixels(1920, 1080, selected)).toMatchObject({
      x: 1152,
      y: 216,
      width: 576,
      height: 540,
    });
    selected.x = 0;
    expect(crop.x).toBe(0.6);
  });

  it("rejects tile-relative custom coordinates and ambiguous selection kinds", () => {
    const selection = {
      kind: "custom",
      coordinateSpace: "tile",
      crop: { x: 0, y: 0, width: 1, height: 1 },
    };
    expect(() => resolveCameraArea(selection as CameraAreaSelection)).toThrow(
      "full source-video coordinates",
    );
    expect(() =>
      resolveCameraArea({ kind: "unknown" } as unknown as CameraAreaSelection),
    ).toThrow("valid camera-area selection");
    expect(() =>
      resolveCameraArea(null as unknown as CameraAreaSelection),
    ).toThrow("valid camera-area selection");
  });

  it.each([
    null,
    [],
    {},
    { x: 0, y: 0, width: 1 },
    { x: "0", y: 0, width: 1, height: 1 },
    { x: NaN, y: 0, width: 1, height: 1 },
    { x: 0, y: 0, width: Infinity, height: 1 },
    { x: -0.1, y: 0, width: 1, height: 1 },
    { x: 0, y: -0.1, width: 1, height: 1 },
    { x: 0, y: 0, width: 0.01, height: 1 },
    { x: 0, y: 0, width: 1, height: 0.01 },
    { x: 0.9, y: 0, width: 0.2, height: 1 },
    { x: 0, y: 0.9, width: 1, height: 0.2 },
  ])("rejects invalid source-normalised custom area %j", (crop) => {
    expect(() => validateCameraArea(crop)).toThrow();
  });

  it("retains full-frame selection and leaves pixel-level readiness to the sampler", () => {
    expect(resolveCameraArea({ kind: "full" })).toEqual({
      x: 0,
      y: 0,
      width: 1,
      height: 1,
    });
    expect(
      resolveCameraArea({ kind: "tile", layout: "single", index: 0 }),
    ).toEqual(resolveCameraArea({ kind: "full" }));
    expect(() =>
      interactionCropPixels(96, 96, selectCameraTile("3x2", 0).crop),
    ).toThrow("48 source pixels");
  });
});
