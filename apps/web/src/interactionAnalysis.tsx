import { useEffect, useRef, useState } from "react";
import type { RefObject } from "react";
import {
  BellRing,
  BrainCircuit,
  Check,
  RefreshCw,
  ScanEye,
  Square,
  Trash2,
  VolumeX,
} from "lucide-react";
import { api, forgetAction, idempotencyKey } from "./api";
import { BrowserAttentionSound } from "./playbackAlerts";
import type { DetectionRect, LiveSourceKind } from "./liveDetectionTypes";
import {
  captureInteractionFrame,
  freshInteractionAlarm,
  InteractionFrameBuffer,
  interactionLabels,
  interactionCropPixels,
  safeInteractionFrameUrl,
} from "./interactionCapture";
import type { InteractionReview, SavedInteraction } from "./interactionCapture";
import "./interactionAnalysis.css";

type Session = {
  runId: string;
  generation: number;
  running: boolean;
  source: { kind: LiveSourceKind; label: string } | null;
};
type ModelStatus = {
  ready: boolean;
  model: string;
  mode: string;
  message: string;
};
type Job = {
  id: string;
  status: "pending" | "running" | "completed" | "failed" | "cancelled";
  result?: SavedInteraction;
  error?: string;
};
type Props = {
  videoRef: RefObject<HTMLVideoElement | null>;
  cancelRef: RefObject<(() => void) | null>;
  silenceRef: RefObject<(() => void) | null>;
  readSession: () => Session;
  muted: boolean;
  volume: number;
  branchName: string;
};
const reviewLabels: Record<InteractionReview, string> = {
  USEFUL: "Useful",
  NORMAL_SHOPPING: "Normal shopping",
  UNCLEAR: "Unclear",
};
const sourceLabels: Record<LiveSourceKind, string> = {
  CAMERA: "Live camera",
  SCREEN_CAPTURE: "Shared CCTV screen",
  RECORDED_VIDEO: "RECORDED TEST",
};
const failureText = (failure: unknown) =>
  failure instanceof Error
    ? failure.message
    : "The analysis could not complete.";

/** Frames are sampled independently of pose detections; one job can run at a time. */
export default function InteractionAnalysis({
  videoRef,
  cancelRef,
  silenceRef,
  readSession,
  muted,
  volume,
  branchName,
}: Props) {
  const [enabled, setEnabled] = useState(false);
  const [automatic, setAutomatic] = useState(false);
  const [alarmEnabled, setAlarmEnabled] = useState(false);
  const [cropEnabled, setCropEnabled] = useState(false);
  const [crop, setCrop] = useState<DetectionRect>({
    x: 0,
    y: 0,
    width: 1,
    height: 1,
  });
  const [cropError, setCropError] = useState("");
  const [model, setModel] = useState<ModelStatus | null>(null);
  const [status, setStatus] = useState(
    "Enable analysis to sample the selected CCTV video.",
  );
  const [error, setError] = useState("");
  const [count, setCount] = useState(0);
  const [busy, setBusy] = useState(false);
  const [elapsed, setElapsed] = useState(0);
  const [history, setHistory] = useState<SavedInteraction[]>([]);
  const [refreshing, setRefreshing] = useState(false);
  const [mutation, setMutation] = useState<string | null>(null);
  const [highlight, setHighlight] = useState<string | null>(null);
  const [soundStatus, setSoundStatus] = useState(
    "Product interaction alarm is off.",
  );
  const mounted = useRef(true);
  const cropPreview = useRef<HTMLCanvasElement>(null);
  const options = useRef({
    enabled,
    automatic,
    alarmEnabled,
    muted,
    volume,
    readSession,
    cropEnabled,
    crop,
    cropError,
  });
  options.current = {
    enabled,
    automatic,
    alarmEnabled,
    muted,
    volume,
    readSession,
    cropEnabled,
    crop,
    cropError,
  };
  const buffer = useRef(new InteractionFrameBuffer());
  const generation = useRef(0);
  const samplingRun = useRef("");
  const job = useRef<{
    id: string | null;
    generation: number;
    started: number;
    cancelled: boolean;
  } | null>(null);
  const sound = useRef<BrowserAttentionSound | null>(null);
  const lastAlarm = useRef(-Infinity);
  const lastSubmitted = useRef(-Infinity);
  const lastProgress = useRef({ media: -1, wall: 0 });
  const submitRef = useRef<() => Promise<void>>(async () => {});

  function silence() {
    sound.current?.stop();
    if (mounted.current) setSoundStatus("Product interaction alarm silenced.");
  }
  function cancel() {
    generation.current++;
    buffer.current.reset();
    samplingRun.current = "";
    const preview = cropPreview.current;
    preview?.getContext("2d")?.clearRect(0, 0, preview.width, preview.height);
    const active = job.current;
    if (active) {
      active.cancelled = true;
      if (active.id)
        void api(`/interactions/jobs/${active.id}/cancel`, "POST", {}).catch(
          () => undefined,
        );
    }
    // Keep the request slot occupied until its polling loop exits; no overlapping submission.
    sound.current?.disarm();
    options.current.alarmEnabled = false;
    if (mounted.current) {
      setAlarmEnabled(false);
      setCount(0);
      setHighlight(null);
      setStatus("Analysis stopped. Pending results cannot trigger an alarm.");
      setSoundStatus(
        "Product interaction alarm is off. Enable it again for a new run.",
      );
    }
  }

  function changeCrop(nextEnabled: boolean, nextCrop: DetectionRect) {
    cancel();
    let invalid = "";
    const video = videoRef.current;
    if (nextEnabled && video?.videoWidth && video.videoHeight) {
      try {
        interactionCropPixels(video.videoWidth, video.videoHeight, nextCrop);
      } catch (failure) {
        invalid = failureText(failure);
      }
    }
    options.current.cropEnabled = nextEnabled;
    options.current.crop = nextCrop;
    options.current.cropError = invalid;
    setCropEnabled(nextEnabled);
    setCrop(nextCrop);
    setCropError(invalid);
    setStatus(
      nextEnabled
        ? "Analysis area changed. Collect four new frames; the product alarm has been disarmed."
        : "Full frame selected. Collect four new frames; the product alarm has been disarmed.",
    );
  }

  function adjustCrop(key: keyof DetectionRect, raw: string) {
    const value = Number(raw);
    if (!Number.isFinite(value)) return;
    const next = {
      ...crop,
      [key]: Math.max(
        key === "width" || key === "height" ? 0.05 : 0,
        Math.min(key === "x" || key === "y" ? 0.95 : 1, value / 100),
      ),
    };
    next.width = Math.min(next.width, 1 - next.x);
    next.height = Math.min(next.height, 1 - next.y);
    changeCrop(true, next);
  }

  async function refresh() {
    setRefreshing(true);
    const results = await Promise.allSettled([
      api<ModelStatus>("/interactions/status"),
      api<{ items: SavedInteraction[] }>("/interactions"),
    ]);
    if (!mounted.current) return;
    if (results[0].status === "fulfilled") setModel(results[0].value);
    else {
      setModel(null);
      setError(failureText(results[0].reason));
    }
    if (results[1].status === "fulfilled") setHistory(results[1].value.items);
    else setError(failureText(results[1].reason));
    if (results.every((result) => result.status === "fulfilled")) setError("");
    setRefreshing(false);
  }

  async function submit() {
    const current = options.current.readSession();
    const video = videoRef.current;
    if (
      job.current ||
      !options.current.enabled ||
      options.current.cropError ||
      !model?.ready ||
      !current.running ||
      !current.source ||
      document.hidden ||
      !video ||
      video.paused ||
      video.seeking ||
      video.playbackRate !== 1
    )
      return;
    const frames = buffer.current.sequence(performance.now());
    if (frames.length !== 4) {
      setStatus(
        "Collecting four fresh frames. Keep detection running for about four seconds.",
      );
      return;
    }
    const active = {
      id: null as string | null,
      generation: generation.current,
      started: performance.now(),
      cancelled: false,
    };
    job.current = active;
    lastSubmitted.current = active.started;
    setBusy(true);
    setElapsed(0);
    setError("");
    setStatus("Sending four sampled frames to the local interaction model…");
    const payload = {
      run_id: current.runId,
      source_kind: current.source.kind,
      source_label: options.current.cropEnabled
        ? `${current.source.label.slice(0, 100)} · selected area`
        : current.source.label,
      frames: frames.map(({ at_seconds, jpeg_base64 }) => ({
        at_seconds,
        jpeg_base64,
      })),
    };
    const requestKey = idempotencyKey("/interactions/jobs", "POST", payload);
    try {
      const created = await api<Job>("/interactions/jobs", "POST", payload);
      active.id = created.id;
      if (
        active.cancelled ||
        active.generation !== generation.current ||
        !mounted.current
      ) {
        await api(`/interactions/jobs/${created.id}/cancel`, "POST", {}).catch(
          () => undefined,
        );
        return;
      }
      while (
        !active.cancelled &&
        mounted.current &&
        active.generation === generation.current
      ) {
        const result = await api<Job>(`/interactions/jobs/${created.id}`);
        if (
          active.cancelled ||
          !mounted.current ||
          active.generation !== generation.current
        )
          return;
        const age = performance.now() - active.started;
        setElapsed(age);
        if (result.status === "completed" && result.result) {
          const saved = result.result;
          setHistory((previous) =>
            [saved, ...previous.filter((item) => item.id !== saved.id)].slice(
              0,
              50,
            ),
          );
          const latest = options.current.readSession();
          const now = performance.now();
          const eligible = freshInteractionAlarm({
            enabled:
              options.current.alarmEnabled &&
              !options.current.muted &&
              options.current.volume > 0,
            sameRun:
              latest.runId === current.runId &&
              latest.generation === current.generation &&
              saved.run_id === current.runId,
            running: latest.running,
            visible: !document.hidden,
            playing: !video.paused,
            seeking: video.seeking,
            playbackRate: video.playbackRate,
            now,
            lastFrameAt: frames.at(-1)!.capturedAt,
            lastProgressAt: lastProgress.current.wall,
            lastAlarmAt: lastAlarm.current,
            result: saved,
          });
          if (eligible) {
            lastAlarm.current = now;
            setHighlight(saved.id);
            const played = sound.current?.play({
              volume: options.current.volume,
              durationSeconds: 8,
            });
            setSoundStatus(
              played?.message ??
                "Audio is unavailable. Review the highlighted result.",
            );
          }
          const delay = Math.round((now - frames.at(-1)!.capturedAt) / 1000);
          setStatus(
            `${interactionLabels[saved.action]} · result ${delay}s after last sampled frame. ${eligible ? "Attention requested; review the sampled frames." : delay > 15 ? "Delayed result saved for review; no alarm." : "Saved for review."}`,
          );
          return;
        }
        if (result.status === "failed")
          throw new Error(
            result.error || "The local model could not analyse this sequence.",
          );
        if (result.status === "cancelled") {
          setStatus("Analysis cancelled. No alarm was triggered.");
          return;
        }
        if (age > 120_000) {
          await api(
            `/interactions/jobs/${created.id}/cancel`,
            "POST",
            {},
          ).catch(() => undefined);
          throw new Error(
            "Analysis exceeded two minutes and was cancelled. Reduce the camera view or check the local model service.",
          );
        }
        setStatus(
          `${result.status === "pending" ? "Waiting for" : "Analysing with"} the local model. ${age > 15_000 ? "This result will be for review only; the alarm freshness limit has passed." : "No further job is queued."}`,
        );
        await new Promise((resolve) => setTimeout(resolve, 1000));
      }
    } catch (failure) {
      if (
        mounted.current &&
        !active.cancelled &&
        active.generation === generation.current
      ) {
        setError(failureText(failure));
        setAutomatic(false);
        options.current.automatic = false;
        setStatus(
          "Analysis interrupted. Automatic submission is paused; check the service and retry.",
        );
      }
    } finally {
      // This sampled window is never retried after cancellation or failure. Release
      // the API helper's retry fingerprint so it cannot retain JPEGs in memory.
      forgetAction("/interactions/jobs", "POST", payload, requestKey);
      if (job.current === active) job.current = null;
      if (mounted.current) setBusy(false);
    }
  }
  submitRef.current = submit;

  async function review(item: SavedInteraction, outcome: InteractionReview) {
    setMutation(item.id);
    if (highlight === item.id) {
      silence();
      setHighlight(null);
    }
    try {
      const updated = await api<SavedInteraction>(
        `/interactions/${item.id}/review`,
        "POST",
        { outcome, note: "", expected_version: item.version },
      );
      if (mounted.current)
        setHistory((previous) =>
          previous.map((row) => (row.id === item.id ? updated : row)),
        );
    } catch (failure) {
      if (mounted.current) setError(failureText(failure));
    } finally {
      if (mounted.current) setMutation(null);
    }
  }
  async function remove(item: SavedInteraction) {
    setMutation(item.id);
    if (highlight === item.id) {
      silence();
      setHighlight(null);
    }
    try {
      await api(`/interactions/${item.id}`, "DELETE");
      if (mounted.current)
        setHistory((previous) => previous.filter((row) => row.id !== item.id));
    } catch (failure) {
      if (mounted.current) setError(failureText(failure));
    } finally {
      if (mounted.current) setMutation(null);
    }
  }

  useEffect(() => {
    mounted.current = true;
    cancelRef.current = cancel;
    silenceRef.current = silence;
    void refresh();
    const hidden = () => {
      if (document.hidden) cancel();
    };
    const pageHide = () => cancel();
    document.addEventListener("visibilitychange", hidden);
    window.addEventListener("pagehide", pageHide);
    const timer = setInterval(() => {
      const configuration = options.current;
      const current = configuration.readSession();
      const video = videoRef.current;
      if (
        !configuration.enabled ||
        configuration.cropError ||
        !current.running ||
        !current.source ||
        document.hidden ||
        !video ||
        video.paused ||
        video.seeking ||
        video.playbackRate !== 1 ||
        video.readyState < 2
      ) {
        if (buffer.current.count || (job.current && !job.current.cancelled))
          cancel();
        return;
      }
      const identity = `${current.runId}:${current.generation}`;
      if (samplingRun.current !== identity) {
        buffer.current.reset();
        samplingRun.current = identity;
        lastProgress.current = {
          media: video.currentTime,
          wall: performance.now(),
        };
      }
      const now = performance.now();
      if (video.currentTime !== lastProgress.current.media)
        lastProgress.current = { media: video.currentTime, wall: now };
      const observation = buffer.current.observe(video.currentTime, now);
      if (observation === "reset") {
        cancel();
        return;
      }
      if (observation === "capture") {
        try {
          buffer.current.add(
            captureInteractionFrame(
              video,
              now,
              configuration.cropEnabled ? configuration.crop : null,
              configuration.cropEnabled ? cropPreview.current : null,
            ),
          );
          setCount(buffer.current.sequence(now).length);
        } catch (failure) {
          cancel();
          setEnabled(false);
          options.current.enabled = false;
          setError(failureText(failure));
          return;
        }
      }
      if (now - lastProgress.current.wall > 2000) {
        cancel();
        return;
      }
      if (
        configuration.automatic &&
        !job.current &&
        now - lastSubmitted.current >= 10_000 &&
        buffer.current.sequence(now).length === 4
      )
        void submitRef.current();
    }, 250);
    return () => {
      mounted.current = false;
      cancel();
      cancelRef.current = null;
      silenceRef.current = null;
      sound.current?.dispose();
      sound.current = null;
      clearInterval(timer);
      document.removeEventListener("visibilitychange", hidden);
      window.removeEventListener("pagehide", pageHide);
    };
    // A single sampler reads current controls and source refs without restarting inference.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    if (muted || volume === 0) sound.current?.stop();
    else sound.current?.setVolume(volume);
  }, [muted, volume]);

  return (
    <section
      className="interaction-panel"
      aria-labelledby="interaction-heading"
    >
      <div className="interaction-heading">
        <div>
          <p className="ld-eyebrow">PRODUCT INTERACTIONS · EXPERIMENTAL</p>
          <h2 id="interaction-heading">
            <BrainCircuit size={21} /> Understand the sampled interaction
          </h2>
          <p>
            Analyse product pickup, return, basket placement or possible
            concealment using the selected video. Model observations need staff
            review.
          </p>
        </div>
        <span
          className={`interaction-model ${model?.ready ? "interaction-ready" : ""}`}
        >
          {model?.ready ? "Local model available" : "Local model unavailable"}
        </span>
      </div>
      <p className="interaction-service">
        {model
          ? `${model.model} · ${model.message}`
          : "Checking the local interaction service…"}
      </p>
      <div className="interaction-controls">
        <label className="ld-inline-check">
          <input
            type="checkbox"
            checked={enabled}
            onChange={(event) => {
              const next = event.target.checked;
              options.current.enabled = next;
              setEnabled(next);
              if (!next) {
                cancel();
                setAutomatic(false);
                options.current.automatic = false;
              } else
                setStatus(
                  "Enabled. Start detection and keep the selected source playing to collect four frames.",
                );
            }}
          />
          Enable product interaction analysis
        </label>
        <label className="ld-inline-check">
          <input
            type="checkbox"
            checked={automatic}
            disabled={!enabled || !model?.ready}
            onChange={(event) => {
              options.current.automatic = event.target.checked;
              setAutomatic(event.target.checked);
            }}
          />
          Analyse automatically
        </label>
        <label className="ld-inline-check">
          <input
            type="checkbox"
            checked={alarmEnabled}
            disabled={!enabled || !model?.ready}
            onChange={(event) => {
              const next = event.target.checked;
              options.current.alarmEnabled = next;
              setAlarmEnabled(next);
              if (!next) {
                sound.current?.disarm();
                setSoundStatus("Product interaction alarm is off.");
                return;
              }
              const currentGeneration = generation.current;
              const activation = (sound.current ??=
                new BrowserAttentionSound()).arm();
              void activation.then((result) => {
                if (
                  !mounted.current ||
                  generation.current !== currentGeneration ||
                  !options.current.alarmEnabled
                )
                  return;
                setSoundStatus(result.message);
                if (!result.ok) {
                  setAlarmEnabled(false);
                  options.current.alarmEnabled = false;
                }
              });
            }}
          />
          Experimental product attention alarm
        </label>
      </div>
      <div className="interaction-crop">
        <label className="ld-inline-check">
          <input
            type="checkbox"
            checked={cropEnabled}
            onChange={(event) => changeCrop(event.target.checked, crop)}
          />
          Analyse this camera/aisle area
        </label>
        <p className="ld-hint">
          {cropEnabled
            ? "Select one camera tile or shelf area. Percentages refer to the full video above. Keep the person’s hands and the product in view."
            : "The full video is analysed. For a CCTV grid, select one camera tile here to preserve more product detail."}
        </p>
        {cropEnabled && (
          <div className="interaction-crop-content">
            <div className="ld-zone-fields">
              {(["x", "y", "width", "height"] as const).map((key) => (
                <label key={key}>
                  {key === "x"
                    ? "Analysis left"
                    : key === "y"
                      ? "Analysis top"
                      : key === "width"
                        ? "Analysis width"
                        : "Analysis height"}{" "}
                  %
                  <input
                    type="number"
                    min={key === "x" || key === "y" ? 0 : 5}
                    max={key === "x" || key === "y" ? 95 : 100}
                    step={1}
                    value={Math.round(crop[key] * 100)}
                    onChange={(event) => adjustCrop(key, event.target.value)}
                  />
                </label>
              ))}
              <button
                type="button"
                onClick={() =>
                  changeCrop(true, { x: 0, y: 0, width: 1, height: 1 })
                }
              >
                Reset area
              </button>
            </div>
            <figure className="interaction-crop-preview">
              <canvas
                ref={cropPreview}
                width={320}
                height={180}
                aria-label="Selected camera area preview"
              />
              <figcaption>
                {enabled
                  ? "Selected area · updates when fresh frames are sampled"
                  : "Enable analysis and start detection to preview this area"}
              </figcaption>
            </figure>
          </div>
        )}
        {cropError && (
          <p className="ld-error" role="alert">
            {cropError}
          </p>
        )}
      </div>
      <div className="interaction-action-row">
        <button
          className="ld-primary"
          type="button"
          disabled={
            !enabled || !model?.ready || !!cropError || count < 4 || busy
          }
          onClick={() => void submit()}
        >
          <ScanEye size={17} />
          {busy ? "Analysing…" : "Analyse recent sequence"}
        </button>
        {busy && (
          <button
            type="button"
            onClick={() => {
              setAutomatic(false);
              options.current.automatic = false;
              cancel();
            }}
          >
            <Square size={16} />
            Cancel analysis
          </button>
        )}
        <span>
          {count}/4 fresh sampled frames
          {busy ? ` · ${Math.round(elapsed / 1000)}s elapsed` : ""}
        </span>
        <button
          type="button"
          disabled={refreshing}
          onClick={() => void refresh()}
        >
          <RefreshCw size={15} />
          {refreshing ? "Checking…" : "Refresh model & history"}
        </button>
      </div>
      <p className="interaction-status" role="status">
        {status}
      </p>
      <p className="ld-hint">
        Four frames cover about four seconds. They are sent to the local service
        and saved with the result for up to 24 hours; you can delete them below.
        No continuous clip is saved. Automatic analysis keeps one job in flight,
        with no queue; events between sampled frames or jobs can be missed. This
        model has not been validated for pharmacy theft detection.
      </p>
      <p className="ld-hint">
        The product alarm is separate from the movement-rule alarm above. It
        requires a clear, product-visible concealment sequence and a result
        within 15 seconds of the last sample. It shares the laptop volume/mute
        setting. Delayed or historical results never sound.
      </p>
      <p className="interaction-sound-status">{soundStatus}</p>
      {error && (
        <p className="ld-error" role="alert">
          {error}
        </p>
      )}
      {highlight && (
        <div className="interaction-alert" role="alert">
          <BellRing size={23} />
          <div>
            <strong>Possible product concealment — review required</strong>
            <p>
              {readSession().source?.kind === "RECORDED_VIDEO"
                ? "Recorded-video test. "
                : ""}
              Check the sampled frames before responding.
            </p>
          </div>
          <button type="button" onClick={silence}>
            <VolumeX size={16} />
            Silence product alarm
          </button>
        </div>
      )}
      <div className="interaction-history-heading">
        <h3>Interaction review · {branchName}</h3>
        <span>{history.length} saved results</span>
      </div>
      {!history.length && (
        <p className="interaction-empty">
          No analysed interactions yet. Connect a video, start detection and
          analyse a recent sequence.
        </p>
      )}
      {history.map((item) => (
        <article
          key={item.id}
          className={`interaction-result ${highlight === item.id ? "interaction-result-highlight" : ""}`}
        >
          <div className="interaction-result-heading">
            <div>
              <span
                className={`interaction-source ${item.source_kind === "RECORDED_VIDEO" ? "interaction-recorded" : ""}`}
              >
                {sourceLabels[item.source_kind]}
              </span>
              <h3>{interactionLabels[item.action]}</h3>
              <small>
                {new Date(item.created_at).toLocaleString()} ·{" "}
                {item.source_label}
              </small>
            </div>
            <span className="interaction-latency">
              {(item.inference_ms / 1000).toFixed(1)}s inference
              <br />
              Experimental model observation
            </span>
          </div>
          <p>{item.reason}</p>
          <div className="interaction-facts">
            <span>
              Model reports person visible: {item.person_visible ? "yes" : "no"}
            </span>
            <span>Model reports visibility: {item.visibility}</span>
            <span>
              Model reports product visible:{" "}
              {item.product_visible ? "yes" : "no"}
            </span>
            <span>
              Model reports sequence observed:{" "}
              {item.sequence_observed ? "yes" : "no"}
            </span>
          </div>
          <details>
            <summary>Sampled frames · chronological review</summary>
            <p>
              These still frames show only sampled moments. They are not a
              continuous video replay.
            </p>
            <div className="interaction-frames">
              {item.frames.map((frame, index) => {
                const url = safeInteractionFrameUrl(frame.url, item.id);
                return (
                  <figure key={index}>
                    {url ? (
                      <img
                        src={url}
                        alt={`Sampled frame ${index + 1} at ${frame.at_seconds.toFixed(2)} seconds`}
                        loading="lazy"
                      />
                    ) : (
                      <span>Frame unavailable</span>
                    )}
                    <figcaption>
                      Frame {index + 1} · {frame.at_seconds.toFixed(2)}s
                      {item.evidence_frame_indices.includes(index)
                        ? " · cited by model"
                        : ""}
                    </figcaption>
                  </figure>
                );
              })}
            </div>
          </details>
          <div className="interaction-review">
            {(Object.keys(reviewLabels) as InteractionReview[]).map(
              (outcome) => (
                <button
                  type="button"
                  key={outcome}
                  disabled={mutation !== null}
                  aria-pressed={item.review?.outcome === outcome}
                  onClick={() => void review(item, outcome)}
                >
                  {item.review?.outcome === outcome && <Check size={14} />}
                  {reviewLabels[outcome]}
                </button>
              ),
            )}
            <button
              type="button"
              className="interaction-delete"
              disabled={mutation !== null}
              onClick={() => void remove(item)}
            >
              <Trash2 size={14} />
              Delete result & frames
            </button>
          </div>
          <small className="interaction-retention">
            {item.model} · Expires {new Date(item.expires_at).toLocaleString()}.
            Feedback records staff review; it does not automatically retrain the
            model.
          </small>
        </article>
      ))}
    </section>
  );
}
