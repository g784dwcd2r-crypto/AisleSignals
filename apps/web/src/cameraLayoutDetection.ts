import type { CameraArea, CameraGridLayout } from "./cameraGrid";

/** Pixel heuristics only: a score is not a calibrated probability or a camera
 * identity. A person must confirm the board and layout before selecting tiles.
 * Borderless mosaics, recorder chrome and architectural grids are ambiguous. */
export type LayoutDetectionReason =
  | "VISIBLE_GUTTERS"
  | "AMBIGUOUS_LAYOUT"
  | "NO_RELIABLE_GRID"
  | "INSUFFICIENT_DETAIL"
  | "INVALID_FRAME"
  | "INVALID_BOARD";

export interface LayoutFrame {
  width: number;
  height: number;
  data: Uint8Array | Uint8ClampedArray;
}

export interface SeparatorEvidence {
  axis: "vertical" | "horizontal";
  /** Source-normalized separator centre, not permission to crop automatically. */
  position: number;
  expectedPosition: number;
  score: number;
  continuity: number;
  contrastCoverage: number;
  thicknessFraction: number;
}

export interface LayoutCandidate {
  layout: CameraGridLayout;
  score: number;
  detailedTiles: number;
  tileCount: number;
  separators: SeparatorEvidence[];
}

export interface CameraLayoutDetection {
  status: "proposed" | "unclear" | "invalid";
  layout: CameraGridLayout | null;
  confidence: number;
  reason: LayoutDetectionReason;
  board: CameraArea;
  boundaries: { vertical: number[]; horizontal: number[] };
  candidates: LayoutCandidate[];
  evidence: {
    luminanceRange: number;
    opaqueFraction: number;
    minimumSeparatorScore: number;
    candidateMargin: number;
  };
  requiresConfirmation: true;
}

const FULL_BOARD: CameraArea = { x: 0, y: 0, width: 1, height: 1 };
const MAX_PIXELS = 8_388_608;
const MAX_DIMENSION = 4096;
const MIN_BOARD_EDGE = 96;
const MIN_LINE_SCORE = 0.69;
const MIN_PROPOSAL_SCORE = 0.71;
const MIN_MARGIN = 0.12;
const GRIDS = [
  { layout: "2x2", columns: 2, rows: 2 },
  { layout: "3x2", columns: 3, rows: 2 },
  { layout: "2x3", columns: 2, rows: 3 },
] as const;

type RGB = [number, number, number];
type PixelBoard = { left: number; top: number; width: number; height: number };
type Profile = {
  pixel: number;
  score: number;
  continuity: number;
  contrastCoverage: number;
};

const clamp = (value: number) => Math.min(1, Math.max(0, value));
const round = (value: number) => Math.round(value * 1000) / 1000;
const median = (values: number[]) => {
  values.sort((a, b) => a - b);
  return values[Math.floor(values.length / 2)] ?? 0;
};
const difference = (a: RGB, b: RGB) =>
  (Math.abs(a[0] - b[0]) + Math.abs(a[1] - b[1]) + Math.abs(a[2] - b[2])) / 3;

function rgb(frame: LayoutFrame, x: number, y: number): RGB {
  const at = (Math.floor(y) * frame.width + Math.floor(x)) * 4;
  return [frame.data[at], frame.data[at + 1], frame.data[at + 2]];
}

function detail(frame: LayoutFrame, board: PixelBoard) {
  const luminances: number[] = [];
  let opaque = 0;
  for (let row = 0; row < 12; row++) {
    for (let column = 0; column < 16; column++) {
      const x = Math.floor(board.left + ((column + 0.5) / 16) * board.width);
      const y = Math.floor(board.top + ((row + 0.5) / 12) * board.height);
      const [r, g, b] = rgb(frame, x, y);
      luminances.push(0.2126 * r + 0.7152 * g + 0.0722 * b);
      if (frame.data[(y * frame.width + x) * 4 + 3] >= 250) opaque++;
    }
  }
  luminances.sort((a, b) => a - b);
  return {
    range:
      luminances[Math.floor(luminances.length * 0.9)] -
      luminances[Math.floor(luminances.length * 0.1)],
    opaque: opaque / luminances.length,
  };
}

function profile(
  frame: LayoutFrame,
  board: PixelBoard,
  axis: "vertical" | "horizontal",
  pixel: number,
): Profile {
  const length = axis === "vertical" ? board.width : board.height;
  const across = axis === "vertical" ? board.height : board.width;
  const flank = Math.max(3, Math.round(length * 0.018));
  const samples: { centre: RGB; before: RGB; after: RGB; opaque: boolean }[] =
    [];
  for (let index = 0; index < 112; index++) {
    // Outer recorder borders cannot supply the continuity evidence themselves.
    const cross = Math.floor(across * (0.035 + ((index + 0.5) / 112) * 0.93));
    const read = (position: number) => {
      const x = board.left + (axis === "vertical" ? position : cross);
      const y = board.top + (axis === "vertical" ? cross : position);
      return {
        colour: rgb(frame, x, y),
        opaque: frame.data[(y * frame.width + x) * 4 + 3] >= 250,
      };
    };
    const centre = read(pixel);
    const before = read(Math.max(0, pixel - flank));
    const after = read(Math.min(length - 1, pixel + flank));
    samples.push({
      centre: centre.colour,
      before: before.colour,
      after: after.colour,
      opaque: centre.opaque && before.opaque && after.opaque,
    });
  }
  const colour: RGB = [0, 1, 2].map((channel) =>
    median(samples.map((sample) => sample.centre[channel])),
  ) as RGB;
  let consistent = 0;
  let contrast = 0;
  for (const sample of samples) {
    const uniform = sample.opaque && difference(sample.centre, colour) <= 15;
    if (uniform) consistent++;
    if (
      uniform &&
      difference(sample.centre, sample.before) >= 19 &&
      difference(sample.centre, sample.after) >= 19
    )
      contrast++;
  }
  const continuity = consistent / samples.length;
  const contrastCoverage = contrast / samples.length;
  // Both sides must differ across most of the line. A single scene edge, a
  // uniformly blank image or a thick dark region is insufficient evidence.
  const score =
    continuity >= 0.8 && contrastCoverage >= 0.64
      ? clamp(continuity * 0.35 + contrastCoverage * 0.65)
      : 0;
  return { pixel, score, continuity, contrastCoverage };
}

function separator(
  frame: LayoutFrame,
  board: PixelBoard,
  axis: "vertical" | "horizontal",
  fraction: number,
): SeparatorEvidence {
  const length = axis === "vertical" ? board.width : board.height;
  const origin = axis === "vertical" ? board.left : board.top;
  const sourceLength = axis === "vertical" ? frame.width : frame.height;
  const expected = length * fraction;
  const radius = Math.max(3, Math.round(length * 0.037));
  const profiles: Profile[] = [];
  for (
    let pixel = Math.max(1, Math.round(expected) - radius);
    pixel <= Math.min(length - 2, Math.round(expected) + radius);
    pixel++
  ) {
    profiles.push(profile(frame, board, axis, pixel));
  }
  const peak = [...profiles].sort(
    (a, b) =>
      b.score - a.score ||
      Math.abs(a.pixel - expected) - Math.abs(b.pixel - expected),
  )[0];
  let first = profiles.findIndex((value) => value.pixel === peak.pixel);
  let last = first;
  while (
    first > 0 &&
    profiles[first - 1].score >= peak.score - 0.035 &&
    profiles[first - 1].score > 0
  )
    first--;
  while (
    last + 1 < profiles.length &&
    profiles[last + 1].score >= peak.score - 0.035 &&
    profiles[last + 1].score > 0
  )
    last++;
  const thickness = profiles[last].pixel - profiles[first].pixel + 1;
  const centre = (profiles[first].pixel + profiles[last].pixel) / 2;
  const offsetPenalty =
    1 - 0.08 * Math.min(1, Math.abs(centre - expected) / radius);
  const score = thickness / length <= 0.032 ? peak.score * offsetPenalty : 0;
  return {
    axis,
    position: (origin + centre + 0.5) / sourceLength,
    expectedPosition: (origin + expected) / sourceLength,
    score: round(score),
    continuity: round(peak.continuity),
    contrastCoverage: round(peak.contrastCoverage),
    thicknessFraction: round(thickness / length),
  };
}

/** Propose only a visibly separated, approximately even four/six-camera board.
 * No reliable lines means abstention, not proof that this is a single camera. */
export function detectCameraLayout(
  frame: LayoutFrame,
  options: { board?: CameraArea } = {},
): CameraLayoutDetection {
  const board = options.board ?? FULL_BOARD;
  const result: CameraLayoutDetection = {
    status: "invalid",
    layout: null,
    confidence: 0,
    reason: "INVALID_FRAME",
    board: { ...FULL_BOARD },
    boundaries: { vertical: [], horizontal: [] },
    candidates: [],
    evidence: {
      luminanceRange: 0,
      opaqueFraction: 0,
      minimumSeparatorScore: 0,
      candidateMargin: 0,
    },
    requiresConfirmation: true,
  };
  if (
    !frame ||
    !Number.isInteger(frame.width) ||
    !Number.isInteger(frame.height) ||
    frame.width < MIN_BOARD_EDGE ||
    frame.height < MIN_BOARD_EDGE ||
    frame.width > MAX_DIMENSION ||
    frame.height > MAX_DIMENSION ||
    frame.width * frame.height > MAX_PIXELS ||
    !(
      frame.data instanceof Uint8Array ||
      frame.data instanceof Uint8ClampedArray
    ) ||
    frame.data.length !== frame.width * frame.height * 4
  )
    return result;
  if (
    !board ||
    ![board.x, board.y, board.width, board.height].every(Number.isFinite) ||
    board.x < 0 ||
    board.y < 0 ||
    board.width <= 0 ||
    board.height <= 0 ||
    board.x + board.width > 1 ||
    board.y + board.height > 1
  ) {
    return { ...result, reason: "INVALID_BOARD" };
  }
  const pixels: PixelBoard = {
    left: Math.floor(board.x * frame.width),
    top: Math.floor(board.y * frame.height),
    width: Math.floor(board.width * frame.width),
    height: Math.floor(board.height * frame.height),
  };
  if (pixels.width < MIN_BOARD_EDGE || pixels.height < MIN_BOARD_EDGE)
    return { ...result, reason: "INVALID_BOARD" };
  result.board = { ...board };
  result.status = "unclear";
  const whole = detail(frame, pixels);
  result.evidence.luminanceRange = round(whole.range);
  result.evidence.opaqueFraction = round(whole.opaque);
  if (whole.range < 24 || whole.opaque < 0.98)
    return { ...result, reason: "INSUFFICIENT_DETAIL" };

  const cache = new Map<string, SeparatorEvidence>();
  const line = (axis: "vertical" | "horizontal", fraction: number) => {
    const key = `${axis}:${fraction}`;
    if (!cache.has(key))
      cache.set(key, separator(frame, pixels, axis, fraction));
    return cache.get(key)!;
  };
  for (const grid of GRIDS) {
    const separators = [
      ...Array.from({ length: grid.columns - 1 }, (_, index) =>
        line("vertical", (index + 1) / grid.columns),
      ),
      ...Array.from({ length: grid.rows - 1 }, (_, index) =>
        line("horizontal", (index + 1) / grid.rows),
      ),
    ];
    let detailedTiles = 0;
    for (let row = 0; row < grid.rows; row++) {
      for (let column = 0; column < grid.columns; column++) {
        const tile = detail(frame, {
          left: pixels.left + (pixels.width * (column + 0.12)) / grid.columns,
          top: pixels.top + (pixels.height * (row + 0.12)) / grid.rows,
          width: (pixels.width * 0.76) / grid.columns,
          height: (pixels.height * 0.76) / grid.rows,
        });
        if (tile.range >= 20 && tile.opaque >= 0.98) detailedTiles++;
      }
    }
    const minimum = Math.min(...separators.map((item) => item.score));
    const mean =
      separators.reduce((sum, item) => sum + item.score, 0) / separators.length;
    const contentFraction = detailedTiles / (grid.columns * grid.rows);
    const score =
      minimum >= MIN_LINE_SCORE && contentFraction >= 0.75
        ? Math.min(
            0.9,
            (minimum * 0.65 + mean * 0.35) * (0.85 + contentFraction * 0.1),
          )
        : Math.min(0.49, mean * 0.45 * contentFraction);
    result.candidates.push({
      layout: grid.layout,
      score: round(score),
      detailedTiles,
      tileCount: grid.columns * grid.rows,
      separators,
    });
  }
  result.candidates.sort((a, b) => b.score - a.score);
  const [best, next] = result.candidates;
  result.candidates.push({
    layout: "single",
    score: best.score < 0.5 ? 0.3 : 0.05,
    detailedTiles: 1,
    tileCount: 1,
    separators: [],
  });
  result.evidence.minimumSeparatorScore = Math.min(
    ...best.separators.map((item) => item.score),
  );
  result.evidence.candidateMargin = round(best.score - next.score);
  result.confidence = best.score;
  if (best.score < MIN_PROPOSAL_SCORE)
    return { ...result, reason: "NO_RELIABLE_GRID" };
  if (best.score - next.score < MIN_MARGIN)
    return { ...result, reason: "AMBIGUOUS_LAYOUT" };
  return {
    ...result,
    status: "proposed",
    layout: best.layout,
    reason: "VISIBLE_GUTTERS",
    boundaries: {
      vertical: best.separators
        .filter((item) => item.axis === "vertical")
        .map((item) => item.position),
      horizontal: best.separators
        .filter((item) => item.axis === "horizontal")
        .map((item) => item.position),
    },
  };
}
