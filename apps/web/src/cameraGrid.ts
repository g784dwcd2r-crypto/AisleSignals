import type { DetectionRect } from "./liveDetectionTypes";

/** Layout names are columns × rows, in the original captured video. */
export const CAMERA_GRID_LAYOUTS = ["single", "2x2", "3x2", "2x3"] as const;
export type CameraGridLayout = (typeof CAMERA_GRID_LAYOUTS)[number];
/** The existing interaction sampler expects source-normalised coordinates. */
export type CameraArea = DetectionRect;

export type CameraTile = {
  id: string;
  label: string;
  /** Zero-based positions; numbering proceeds left to right, then down. */
  index: number;
  row: number;
  column: number;
  /** Always relative to the full source video, never to another tile. */
  crop: CameraArea;
};

export type CameraAreaSelection =
  | { kind: "full" }
  | { kind: "tile"; layout: CameraGridLayout; index: number }
  | { kind: "custom"; coordinateSpace: "source"; crop: CameraArea };

export type CameraGridBoundaries = {
  /** Internal separator centres only, in full-source coordinates. */
  vertical: number[];
  horizontal: number[];
};

const dimensions: Record<CameraGridLayout, { columns: number; rows: number }> =
  {
    single: { columns: 1, rows: 1 },
    "2x2": { columns: 2, rows: 2 },
    "3x2": { columns: 3, rows: 2 },
    "2x3": { columns: 2, rows: 3 },
  };

export function isCameraGridLayout(value: unknown): value is CameraGridLayout {
  return (
    typeof value === "string" &&
    CAMERA_GRID_LAYOUTS.some((layout) => layout === value)
  );
}

export function isCameraTileIndex(
  layout: unknown,
  index: unknown,
): index is number {
  if (!isCameraGridLayout(layout)) return false;
  const { rows, columns } = dimensions[layout];
  return (
    typeof index === "number" &&
    Number.isInteger(index) &&
    index >= 0 &&
    index < rows * columns
  );
}

/** Even grid geometry only; borders, viewer controls and irregular camera
 * arrangements must be excluded by a confirmed custom source area. */
export function cameraGridTiles(layout: CameraGridLayout): CameraTile[] {
  if (!isCameraGridLayout(layout))
    throw new Error("Choose a supported camera-grid layout.");
  const { rows, columns } = dimensions[layout];
  return Array.from({ length: rows * columns }, (_, index) => {
    const row = Math.floor(index / columns);
    const column = index % columns;
    return {
      id: `grid-${layout}-camera-${index + 1}`,
      label: `Camera ${index + 1}`,
      index,
      row,
      column,
      crop: {
        x: column / columns,
        y: row / rows,
        width: 1 / columns,
        height: 1 / rows,
      },
    };
  });
}

export function selectCameraTile(
  layout: CameraGridLayout,
  index: number,
): CameraTile {
  if (!isCameraGridLayout(layout))
    throw new Error("Choose a supported camera-grid layout.");
  if (!isCameraTileIndex(layout, index))
    throw new Error("Choose a camera contained in the selected grid.");
  return cameraGridTiles(layout)[index];
}

/** Preserve custom full-source coordinates. Pixel-size validation still belongs
 * to interactionCropPixels once the actual source dimensions are available. */
export function validateCameraArea(value: unknown): CameraArea {
  if (!value || typeof value !== "object" || Array.isArray(value))
    throw new Error("Choose a valid area within the full source video.");
  const candidate = value as Record<string, unknown>;
  const fields = ["x", "y", "width", "height"] as const;
  if (
    fields.some(
      (key) =>
        typeof candidate[key] !== "number" || !Number.isFinite(candidate[key]),
    )
  )
    throw new Error("Camera-area coordinates must be finite numbers.");
  const { x, y, width, height } = candidate as CameraArea;
  if (
    x < 0 ||
    y < 0 ||
    width < 0.05 ||
    height < 0.05 ||
    x + width > 1.000001 ||
    y + height > 1.000001
  )
    throw new Error(
      "Choose an area inside the full source video, at least 5% wide and high.",
    );
  return { x, y, width, height };
}

/** Map a confirmed camera board onto the original source. Optional measured
 * separator centres exclude the outer board edges; they need not be even. */
export function mapCameraGridToArea(
  layout: CameraGridLayout,
  board: CameraArea,
  boundaries?: CameraGridBoundaries,
): CameraTile[] {
  const tiles = cameraGridTiles(layout);
  const area = validateCameraArea(board);
  const { columns, rows } = dimensions[layout];
  if (
    boundaries !== undefined &&
    (!boundaries || typeof boundaries !== "object" || Array.isArray(boundaries))
  )
    throw new Error("Provide the internal camera separators for both axes.");

  function edges(
    start: number,
    length: number,
    count: number,
    supplied: unknown,
  ) {
    let internal: number[];
    if (boundaries === undefined) {
      internal = Array.from(
        { length: count - 1 },
        (_, index) => start + ((index + 1) * length) / count,
      );
    } else {
      if (
        !Array.isArray(supplied) ||
        supplied.length !== count - 1 ||
        supplied.some(
          (position) =>
            typeof position !== "number" || !Number.isFinite(position),
        )
      )
        throw new Error(
          "Camera separator counts and coordinates must match the selected layout.",
        );
      internal = [...supplied];
    }
    let previous = start;
    for (const position of internal) {
      if (position <= previous || position >= start + length)
        throw new Error(
          "Camera separators must be strictly ordered inside the selected board.",
        );
      previous = position;
    }
    return [start, ...internal, start + length];
  }

  const horizontalEdges = edges(
    area.x,
    area.width,
    columns,
    boundaries?.vertical,
  );
  const verticalEdges = edges(
    area.y,
    area.height,
    rows,
    boundaries?.horizontal,
  );
  return tiles.map((tile) => ({
    ...tile,
    crop: validateCameraArea({
      x: horizontalEdges[tile.column],
      y: verticalEdges[tile.row],
      width: horizontalEdges[tile.column + 1] - horizontalEdges[tile.column],
      height: verticalEdges[tile.row + 1] - verticalEdges[tile.row],
    }),
  }));
}

/** Call the sampler with this result directly. Custom crops are not nested
 * inside a selected tile; their explicit coordinate space prevents ambiguity. */
export function resolveCameraArea(selection: CameraAreaSelection): CameraArea {
  if (!selection || typeof selection !== "object")
    throw new Error("Choose a valid camera-area selection.");
  switch (selection.kind) {
    case "full":
      return { x: 0, y: 0, width: 1, height: 1 };
    case "tile":
      return selectCameraTile(selection.layout, selection.index).crop;
    case "custom":
      if (selection.coordinateSpace !== "source")
        throw new Error(
          "Custom camera areas must use full source-video coordinates.",
        );
      return validateCameraArea(selection.crop);
    default:
      throw new Error("Choose a valid camera-area selection.");
  }
}
