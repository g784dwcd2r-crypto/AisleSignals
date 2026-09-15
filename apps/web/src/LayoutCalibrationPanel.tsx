import { useEffect, useRef, useState } from "react";
import type { PointerEvent, RefObject } from "react";
import { api, ApiError } from "./api";
import type { ConfirmedCameraLayout } from "./CameraLayoutPicker";
import {
  LAYOUT_ZONE_KINDS,
  calibrationMatchesLayout,
  rectangularZone,
  type LayoutCalibration,
  type LayoutPoint,
  type LayoutZoneKind,
} from "./layoutCalibration";
import "./layoutCalibration.css";

const labels: Record<LayoutZoneKind, string> = {
  ENTRANCE: "Entrance",
  EXIT: "Exit",
  CASHIER: "Cashier",
  SHELF: "Shelf",
  BLIND: "Blind area",
  IGNORE: "Ignored area",
};
type Draft = Pick<LayoutCalibration, "layout" | "source_label" | "cameras">;

export default function LayoutCalibrationPanel({
  confirmedLayout,
  videoRef,
  sourceKey,
  manager,
  onCalibration,
}: {
  confirmedLayout: ConfirmedCameraLayout | null;
  videoRef: RefObject<HTMLVideoElement | null>;
  sourceKey: string;
  manager: boolean;
  onCalibration: (value: LayoutCalibration | null) => void;
}) {
  const [saved, setSaved] = useState<LayoutCalibration | null>(null);
  const [draft, setDraft] = useState<Draft | null>(null);
  const [camera, setCamera] = useState(0);
  const [kind, setKind] = useState<LayoutZoneKind>("SHELF");
  const [zoneLabel, setZoneLabel] = useState("Shelf area");
  const [message, setMessage] = useState("Loading saved pharmacy map…");
  const [saving, setSaving] = useState(false);
  const [drawing, setDrawing] = useState(false);
  const [acceptedSourceKey, setAcceptedSourceKey] = useState("");
  const origin = useRef<(LayoutPoint & { pointer: number }) | null>(null);
  const canvas = useRef<HTMLCanvasElement>(null);

  useEffect(() => {
    let alive = true;
    api<LayoutCalibration>("/layout-calibration")
      .then((value) => {
        if (!alive) return;
        if (
          value.readiness?.alarm_authority !== false ||
          !Number.isInteger(value.version)
        )
          throw new Error("The saved calibration response was not valid.");
        setSaved(value);
        if (value.layout)
          setDraft({
            layout: value.layout,
            source_label: value.source_label,
            cameras: value.cameras,
          });
        setMessage(
          value.version
            ? "Saved pharmacy map loaded."
            : "Confirm a camera layout, then map each camera.",
        );
      })
      .catch(
        (error) =>
          alive &&
          setMessage(
            error instanceof Error
              ? error.message
              : "Could not load the pharmacy map.",
          ),
      );
    return () => {
      alive = false;
    };
  }, []);

  useEffect(() => {
    const compatible = calibrationMatchesLayout(saved, confirmedLayout);
    onCalibration(compatible && acceptedSourceKey === sourceKey ? saved : null);
  }, [saved, confirmedLayout, sourceKey, acceptedSourceKey, onCalibration]);

  useEffect(() => setAcceptedSourceKey(""), [sourceKey]);

  function beginLayout() {
    if (!confirmedLayout || confirmedLayout.layout === "single") return;
    setDraft({
      layout: confirmedLayout.layout,
      source_label: "Pharmacy CCTV monitor",
      cameras: confirmedLayout.tiles.map((tile) => ({
        camera_index: tile.index,
        label: tile.label,
        crop: tile.crop,
        zones: [],
      })),
    });
    setCamera(0);
    setMessage("Unsaved map created. Mark the areas visible on each camera.");
    capture();
  }
  function capture() {
    const video = videoRef.current,
      output = canvas.current;
    const tile = confirmedLayout?.tiles.find((item) => item.index === camera);
    if (!video || !output || !tile || video.readyState < 2) {
      setMessage("A decoded CCTV frame is required for the map preview.");
      return;
    }
    output.width = 800;
    output.height = Math.max(
      300,
      Math.round(
        (800 * tile.crop.height * video.videoHeight) /
          (tile.crop.width * video.videoWidth),
      ),
    );
    output
      .getContext("2d")
      ?.drawImage(
        video,
        tile.crop.x * video.videoWidth,
        tile.crop.y * video.videoHeight,
        tile.crop.width * video.videoWidth,
        tile.crop.height * video.videoHeight,
        0,
        0,
        output.width,
        output.height,
      );
    setMessage(
      `Current frame captured for Camera ${camera + 1}. Draw a rectangle around one area.`,
    );
  }
  function point(event: PointerEvent<HTMLDivElement>): LayoutPoint {
    const rect = event.currentTarget.getBoundingClientRect();
    return {
      x: Math.max(0, Math.min(1, (event.clientX - rect.left) / rect.width)),
      y: Math.max(0, Math.min(1, (event.clientY - rect.top) / rect.height)),
    };
  }
  function startDraw(event: PointerEvent<HTMLDivElement>) {
    if (!drawing || !draft) return;
    origin.current = { ...point(event), pointer: event.pointerId };
    event.currentTarget.setPointerCapture(event.pointerId);
  }
  function finishDraw(event: PointerEvent<HTMLDivElement>) {
    const start = origin.current;
    origin.current = null;
    if (!start || start.pointer !== event.pointerId || !draft) return;
    if (event.currentTarget.hasPointerCapture(event.pointerId))
      event.currentTarget.releasePointerCapture(event.pointerId);
    try {
      const zone = rectangularZone(
        crypto.randomUUID().toLowerCase(),
        kind,
        zoneLabel.trim() || labels[kind],
        start,
        point(event),
      );
      setDraft({
        ...draft,
        cameras: draft.cameras.map((item) =>
          item.camera_index === camera
            ? { ...item, zones: [...item.zones, zone] }
            : item,
        ),
      });
      setDrawing(false);
      setMessage(
        `${labels[kind]} added to Camera ${camera + 1}. Save after checking all cameras.`,
      );
    } catch (error) {
      setMessage(
        error instanceof Error ? error.message : "Zone could not be added.",
      );
    }
  }
  function remove(zoneId: string) {
    if (!draft) return;
    setDraft({
      ...draft,
      cameras: draft.cameras.map((item) => ({
        ...item,
        zones: item.zones.filter((zone) => zone.id !== zoneId),
      })),
    });
  }
  async function save() {
    if (!draft || !confirmedLayout || draft.layout !== confirmedLayout.layout)
      return;
    setSaving(true);
    try {
      const value = await api<LayoutCalibration>("/layout-calibration", "PUT", {
        schema_version: "1.0",
        expected_version: saved?.version ?? 0,
        ...draft,
      });
      setSaved(value);
      setAcceptedSourceKey(sourceKey);
      setMessage(
        value.readiness.status === "INCOMPLETE"
          ? `Saved as incomplete. ${value.readiness.warnings.join(" ")}`
          : "Saved. The map is ready for an on-site acceptance check; it still has no alarm authority.",
      );
    } catch (error) {
      setMessage(
        error instanceof ApiError && error.code === "VERSION_CONFLICT"
          ? "Another manager changed this map. Reload the page before editing again."
          : error instanceof Error
            ? error.message
            : "Map could not be saved.",
      );
    } finally {
      setSaving(false);
    }
  }

  const current = draft?.cameras.find((item) => item.camera_index === camera);
  return (
    <section
      className="layout-calibration"
      aria-labelledby="layout-calibration-heading"
    >
      <h2 id="layout-calibration-heading">Pharmacy layout calibration</h2>
      <p>
        Mark entrances, exits, cashier, shelves and areas the camera cannot
        reliably observe. This map can remove unsafe evidence; it cannot trigger
        or authorise an alarm.
      </p>
      <p className="layout-calibration-status" role="status">
        {message}
      </p>
      {saved?.version ? (
        <p>
          <strong>
            {saved.readiness.status === "INCOMPLETE"
              ? "Incomplete"
              : "Ready for site acceptance"}
          </strong>{" "}
          · version {saved.version} · alarm authority off
        </p>
      ) : null}
      {saved?.version &&
      sourceKey &&
      calibrationMatchesLayout(saved, confirmedLayout) &&
      acceptedSourceKey !== sourceKey ? (
        <button
          type="button"
          onClick={() => {
            setAcceptedSourceKey(sourceKey);
            setMessage(
              "Saved map attached to this connected source for the current session.",
            );
          }}
        >
          Use saved map with this source
        </button>
      ) : null}
      {!confirmedLayout || confirmedLayout.layout === "single" ? (
        <p>
          Confirm a four-camera or six-camera layout above before editing this
          map.
        </p>
      ) : !draft || draft.layout !== confirmedLayout.layout ? (
        manager ? (
          <button type="button" onClick={beginLayout}>
            Start map for {confirmedLayout.tiles.length} cameras
          </button>
        ) : (
          <p>A manager must create the map for this confirmed layout.</p>
        )
      ) : (
        <>
          <div className="layout-calibration-toolbar">
            <label>
              Camera
              <select
                value={camera}
                onChange={(event) => {
                  setCamera(Number(event.target.value));
                  setDrawing(false);
                }}
              >
                {draft.cameras.map((item) => (
                  <option key={item.camera_index} value={item.camera_index}>
                    {item.label}
                  </option>
                ))}
              </select>
            </label>
            <button type="button" onClick={capture}>
              Capture current frame
            </button>
            {manager && (
              <>
                <label>
                  Area type
                  <select
                    value={kind}
                    onChange={(event) => {
                      const next = event.target.value as LayoutZoneKind;
                      setKind(next);
                      setZoneLabel(labels[next]);
                    }}
                  >
                    {LAYOUT_ZONE_KINDS.map((value) => (
                      <option key={value} value={value}>
                        {labels[value]}
                      </option>
                    ))}
                  </select>
                </label>
                <label>
                  Area name
                  <input
                    value={zoneLabel}
                    maxLength={80}
                    onChange={(event) => setZoneLabel(event.target.value)}
                  />
                </label>
                <button
                  type="button"
                  aria-pressed={drawing}
                  onClick={() => setDrawing(!drawing)}
                >
                  {drawing ? "Cancel drawing" : "Draw area"}
                </button>
              </>
            )}
          </div>
          <div
            className={`layout-calibration-preview${drawing ? " is-drawing" : ""}`}
            onPointerDown={startDraw}
            onPointerUp={finishDraw}
            onPointerCancel={() => {
              origin.current = null;
            }}
          >
            <canvas
              ref={canvas}
              aria-label={`Calibration frame for Camera ${camera + 1}`}
            />
            {current?.zones.map((zone) => {
              const xs = zone.points.map((p) => p.x),
                ys = zone.points.map((p) => p.y);
              return (
                <span
                  key={zone.id}
                  className={`layout-zone zone-${zone.kind.toLowerCase()}`}
                  style={{
                    left: `${Math.min(...xs) * 100}%`,
                    top: `${Math.min(...ys) * 100}%`,
                    width: `${(Math.max(...xs) - Math.min(...xs)) * 100}%`,
                    height: `${(Math.max(...ys) - Math.min(...ys)) * 100}%`,
                  }}
                >
                  <b>{zone.label}</b>
                </span>
              );
            })}
          </div>
          <ul className="layout-zone-list">
            {current?.zones.map((zone) => (
              <li key={zone.id}>
                <span>
                  <b>{labels[zone.kind]}</b> · {zone.label}
                </span>
                {manager && (
                  <button type="button" onClick={() => remove(zone.id)}>
                    Remove
                  </button>
                )}
              </li>
            ))}
          </ul>
          {manager && (
            <div className="layout-calibration-actions">
              <label>
                Monitor label
                <input
                  value={draft.source_label}
                  maxLength={120}
                  onChange={(event) =>
                    setDraft({ ...draft, source_label: event.target.value })
                  }
                />
              </label>
              <button
                type="button"
                disabled={saving || !draft.source_label.trim()}
                onClick={() => void save()}
              >
                {saving ? "Saving…" : "Save pharmacy map"}
              </button>
            </div>
          )}
        </>
      )}
    </section>
  );
}
