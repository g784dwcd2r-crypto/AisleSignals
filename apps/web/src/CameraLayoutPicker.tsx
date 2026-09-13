import { useEffect, useRef, useState } from "react";
import type { RefObject } from "react";
import { mapCameraGridToArea, validateCameraArea } from "./cameraGrid";
import type { CameraArea, CameraGridLayout, CameraTile } from "./cameraGrid";
import { detectCameraLayout } from "./cameraLayoutDetection";
import "./cameraLayoutPicker.css";

const fullBoard = { x: 0, y: 0, width: 1, height: 1 };
const names: Record<CameraGridLayout, string> = {
  single: "Single camera",
  "2x2": "4 cameras · 2 × 2",
  "3x2": "6 cameras · 3 × 2",
  "2x3": "6 cameras · 2 × 3",
};
type Props = {
  videoRef: RefObject<HTMLVideoElement | null>;
  sourceKey: string;
  customArea: boolean;
  onInvalidate: () => void;
  onSelect: (crop: CameraArea, label: string) => void;
};

export default function CameraLayoutPicker({
  videoRef,
  sourceKey,
  customArea,
  onInvalidate,
  onSelect,
}: Props) {
  const [boardFields, setBoardFields] = useState({
    x: "0",
    y: "0",
    width: "100",
    height: "100",
  });
  const [layout, setLayout] = useState<CameraGridLayout | "">("");
  const [tiles, setTiles] = useState<CameraTile[]>([]);
  const [confirmed, setConfirmed] = useState(false);
  const [selected, setSelected] = useState(0);
  const [message, setMessage] = useState(
    "Connect a CCTV source to choose a camera layout.",
  );
  const [error, setError] = useState("");
  const [previewReady, setPreviewReady] = useState(false);
  const [dimensions, setDimensions] = useState({ width: 16, height: 9 });
  const canvas = useRef<HTMLCanvasElement>(null);
  const callbacks = useRef({ onInvalidate, onSelect });
  callbacks.current = { onInvalidate, onSelect };
  const board = useRef<CameraArea>(fullBoard);
  const boardValid = useRef(true);
  const scan = useRef({
    attempts: 0,
    done: false,
    lastMedia: -1,
    lastPresentation: -1,
  });
  const presentation = useRef(0);
  const previewAvailable = useRef(false);
  const chosenLayout = useRef<CameraGridLayout | "">("");
  const currentTiles = useRef<CameraTile[]>([]);
  const currentConfirmed = useRef(false);

  function invalidate() {
    callbacks.current.onInvalidate(); // Cancel old camera work before any render or asynchronous operation.
    currentConfirmed.current = false;
    setConfirmed(false);
    setSelected(0);
  }
  function setGeometry(next: CameraGridLayout, mapped: CameraTile[]) {
    chosenLayout.current = next;
    currentTiles.current = mapped;
    setLayout(next);
    setTiles(mapped);
  }
  function selectTile(index: number, explicitLayout = false) {
    const tile = currentTiles.current[index];
    if (
      !tile ||
      !chosenLayout.current ||
      (!explicitLayout && (!currentConfirmed.current || customArea))
    )
      return;
    callbacks.current.onInvalidate();
    currentConfirmed.current = true;
    setConfirmed(true);
    setSelected(index);
    scan.current.done = true;
    const label = `${tile.label} · ${chosenLayout.current === "single" ? "single camera" : `${chosenLayout.current} grid`}`;
    callbacks.current.onSelect(tile.crop, label);
    setMessage(
      `${label} selected. Only this camera is product-analysed; the other tiles are not.`,
    );
  }
  function chooseLayout(next: CameraGridLayout) {
    invalidate();
    scan.current.done = true;
    setError("");
    if (!boardValid.current) {
      setError(
        "Enter a valid camera board inside the source before choosing a layout.",
      );
      return;
    }
    try {
      setGeometry(next, mapCameraGridToArea(next, board.current));
      setMessage(
        next === "single"
          ? "Single camera explicitly selected."
          : "Manual layout preview. Check the boxes against the camera boundaries, then select Use this layout.",
      );
      if (next === "single") selectTile(0, true);
    } catch (failure) {
      setError(
        failure instanceof Error
          ? failure.message
          : "The layout does not fit this board.",
      );
    }
  }
  function detectAgain() {
    invalidate();
    setError("");
    chosenLayout.current = "";
    currentTiles.current = [];
    setLayout("");
    setTiles([]);
    scan.current = {
      attempts: 0,
      done: false,
      lastMedia: -1,
      lastPresentation: -1,
    };
    setMessage(
      "Checking the current video frame again. Confirm its layout before product analysis resumes.",
    );
  }
  function changeBoard(key: keyof CameraArea, value: string) {
    invalidate();
    const fields = { ...boardFields, [key]: value };
    setBoardFields(fields);
    setError("");
    currentTiles.current = [];
    chosenLayout.current = "";
    setTiles([]);
    setLayout("");
    try {
      if (Object.values(fields).some((field) => field.trim() === ""))
        throw new Error("Complete all four camera-board percentages.");
      board.current = validateCameraArea(
        Object.fromEntries(
          Object.entries(fields).map(([field, raw]) => [
            field,
            Number(raw) / 100,
          ]),
        ),
      );
      boardValid.current = true;
      scan.current = {
        attempts: 0,
        done: false,
        lastMedia: -1,
        lastPresentation: -1,
      };
      setMessage(
        "Camera board changed. Checking the local frame; confirm a layout again before analysis.",
      );
    } catch (failure) {
      boardValid.current = false;
      scan.current.done = true;
      setError(
        failure instanceof Error ? failure.message : "Invalid camera board.",
      );
    }
  }

  useEffect(() => {
    const video = videoRef.current;
    if (!video) return;
    let alive = true,
      callback: number | null = null;
    let knownSize = "";
    function reset() {
      callbacks.current.onInvalidate();
      board.current = fullBoard;
      boardValid.current = true;
      chosenLayout.current = "";
      currentTiles.current = [];
      currentConfirmed.current = false;
      scan.current = {
        attempts: 0,
        done: false,
        lastMedia: -1,
        lastPresentation: -1,
      };
      presentation.current = 0;
      previewAvailable.current = false;
      setBoardFields({ x: "0", y: "0", width: "100", height: "100" });
      setLayout("");
      setTiles([]);
      setConfirmed(false);
      setSelected(0);
      setError("");
      setPreviewReady(false);
      setMessage(
        sourceKey
          ? "Checking this source locally for visible camera separators. Product analysis waits for your camera selection."
          : "Connect a CCTV source to choose a camera layout.",
      );
      canvas.current
        ?.getContext("2d")
        ?.clearRect(0, 0, canvas.current.width, canvas.current.height);
    }
    reset();
    const presented = () => {
      if (!alive) return;
      presentation.current++;
      callback = video.requestVideoFrameCallback(presented);
    };
    if (typeof video.requestVideoFrameCallback === "function")
      callback = video.requestVideoFrameCallback(presented);
    const timer = setInterval(() => {
      if (
        !alive ||
        !sourceKey ||
        !boardValid.current ||
        (scan.current.done && previewAvailable.current) ||
        video.readyState < 2 ||
        !video.videoWidth ||
        !video.videoHeight ||
        video.seeking ||
        document.hidden
      )
        return;
      const state = scan.current;
      if (
        state.attempts > 0 &&
        video.currentTime === state.lastMedia &&
        presentation.current === state.lastPresentation
      )
        return;
      const output = canvas.current;
      if (!output) return;
      const scale = Math.min(
        1,
        960 / Math.max(video.videoWidth, video.videoHeight),
      );
      output.width = Math.max(1, Math.round(video.videoWidth * scale));
      output.height = Math.max(1, Math.round(video.videoHeight * scale));
      setDimensions({ width: output.width, height: output.height });
      const context = output.getContext("2d", { willReadFrequently: true });
      if (!context) {
        state.done = true;
        setMessage(
          "Frame preview is unavailable. Choose a manual layout or custom area.",
        );
        return;
      }
      try {
        context.drawImage(video, 0, 0, output.width, output.height);
        previewAvailable.current = true;
        setPreviewReady(true);
        // A manual choice may precede the first decoded frame. It ends automatic
        // detection, but must still get its preview when pixels become available.
        if (state.done) return;
        const result = detectCameraLayout(
          context.getImageData(0, 0, output.width, output.height),
          { board: board.current },
        );
        state.attempts++;
        state.lastMedia = video.currentTime;
        state.lastPresentation = presentation.current;
        setPreviewReady(true);
        if (result.status === "proposed" && result.layout) {
          setGeometry(
            result.layout,
            mapCameraGridToArea(result.layout, result.board, result.boundaries),
          );
          state.done = true;
          setMessage(
            `Possible ${names[result.layout].toLowerCase()} layout from visible separators. Check the measured boxes and select Use this layout. This is a layout suggestion, not detection accuracy.`,
          );
        } else if (
          result.reason === "INSUFFICIENT_DETAIL" &&
          state.attempts < 3
        ) {
          setMessage(
            "The first frame has too little detail. Play the source for another local check, or choose its layout manually.",
          );
        } else {
          state.done = true;
          setMessage(
            "No reliable camera layout was found. Choose Single camera, a manual grid, or the custom area below. Product analysis stays paused until you choose.",
          );
        }
      } catch {
        state.done = true;
        setMessage(
          "This frame could not be checked locally. Choose a manual layout or the custom area below.",
        );
      }
    }, 250);
    const resized = () => {
      if (!video.videoWidth || !video.videoHeight) return;
      const size = `${video.videoWidth}:${video.videoHeight}`;
      if (knownSize && knownSize !== size) reset();
      knownSize = size;
    };
    resized();
    video.addEventListener("resize", resized);
    return () => {
      alive = false;
      clearInterval(timer);
      if (callback !== null) video.cancelVideoFrameCallback?.(callback);
      video.removeEventListener("resize", resized);
    };
  }, [sourceKey, videoRef]);

  return (
    <section
      className="camera-layout-picker"
      aria-labelledby="camera-layout-heading"
    >
      <h3 id="camera-layout-heading">Choose the camera to analyse</h3>
      <p>
        Body keypoints cover the full source. Product analysis uses one selected
        camera tile only; this does not monitor every tile simultaneously.
      </p>
      <p className="camera-layout-status" role="status">
        {customArea
          ? "Custom area selected. Its percentages below control product analysis; choose and confirm a layout to return to a camera tile."
          : message}
      </p>
      <label>
        Camera layout
        <select
          value={layout}
          disabled={!sourceKey}
          onChange={(event) =>
            chooseLayout(event.target.value as CameraGridLayout)
          }
        >
          <option value="" disabled>
            Choose a layout
          </option>
          {(Object.keys(names) as CameraGridLayout[]).map((value) => (
            <option value={value} key={value}>
              {names[value]}
            </option>
          ))}
        </select>
      </label>
      <details className="camera-board-options">
        <summary>Exclude recorder or browser borders</summary>
        <p>
          Set the board that contains the camera pictures, as percentages of the
          full source. Changing it requires a new layout confirmation.
        </p>
        <div className="camera-board-fields">
          {(["x", "y", "width", "height"] as const).map((key) => (
            <label key={key}>
              Camera board {key === "x" ? "left" : key === "y" ? "top" : key} %
              <input
                type="number"
                value={boardFields[key]}
                min={key === "width" || key === "height" ? 5 : 0}
                max={100}
                step={1}
                disabled={!sourceKey}
                onChange={(event) => changeBoard(key, event.target.value)}
              />
            </label>
          ))}
        </div>
      </details>
      <div
        className="camera-layout-preview"
        style={{ aspectRatio: `${dimensions.width}/${dimensions.height}` }}
      >
        <canvas ref={canvas} aria-label="Local camera layout preview" />
        {!previewReady && (
          <span className="camera-layout-placeholder">
            Video preview appears when a decoded frame is available.
          </span>
        )}
        {tiles.map((tile) => (
          <button
            type="button"
            key={tile.id}
            className={`camera-layout-tile ${confirmed && !customArea && selected === tile.index ? "camera-layout-tile-selected" : ""}`}
            style={{
              left: `${tile.crop.x * 100}%`,
              top: `${tile.crop.y * 100}%`,
              width: `${tile.crop.width * 100}%`,
              height: `${tile.crop.height * 100}%`,
            }}
            aria-label={`Select ${tile.label}`}
            aria-pressed={confirmed && !customArea && selected === tile.index}
            disabled={!confirmed || customArea}
            onClick={() => selectTile(tile.index)}
          >
            <span>{tile.label}</span>
          </button>
        ))}
      </div>
      <div className="camera-layout-actions">
        <button
          type="button"
          disabled={
            !layout ||
            !tiles.length ||
            !boardValid.current ||
            (layout !== "single" && !previewReady)
          }
          onClick={() => selectTile(0, true)}
        >
          Use this layout
        </button>
        <button
          type="button"
          disabled={!sourceKey || !boardValid.current}
          onClick={detectAgain}
        >
          Detect layout again
        </button>
        <span>
          {customArea
            ? "Custom area"
            : confirmed
              ? `${tiles[selected]?.label ?? "Camera"} · ${layout === "single" ? "single camera" : `${layout} grid`} confirmed`
              : "Awaiting camera selection"}
        </span>
      </div>
      {error && (
        <p className="ld-error" role="alert">
          {error}
        </p>
      )}
    </section>
  );
}
