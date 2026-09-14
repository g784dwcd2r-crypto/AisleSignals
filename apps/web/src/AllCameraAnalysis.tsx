import { useEffect, useRef, useState } from "react";
import type { RefObject } from "react";
import { api, ApiError, forgetAction, idempotencyKey } from "./api";
import { runtimeHealth } from "./runtimeHealth";
import { BrowserAttentionSound } from "./playbackAlerts";
import {
  captureInteractionFrame,
  claimInteractionSound,
  freshInteractionAlarm,
  InteractionAlarmCommission,
  InteractionAttentionPolicy,
  interactionLabels,
  type CameraCalibration,
  type SavedInteraction,
} from "./interactionCapture";
import {
  MultiCameraInteractions,
  cameraKey,
  cameraName,
  type AllCameraRun,
  type ProductTicket,
} from "./multiCameraInteractions";
import type { InteractionMonitorStatus } from "./interactionAnalysis";
import "./allCameraAnalysis.css";

type Job = {
  id: string;
  status: "pending" | "running" | "completed" | "cancelled" | "failed";
  result?: SavedInteraction;
  error?: string;
};
type Props = {
  run: AllCameraRun | null;
  count: number;
  videoRef: RefObject<HTMLVideoElement | null>;
  readRunning: () => boolean;
  modelReady: boolean;
  volume: number;
  muted: boolean;
  cameraCalibration: CameraCalibration | null;
  cancelRef: RefObject<(() => void) | null>;
  silenceRef: RefObject<(() => void) | null>;
  onResult: (result: SavedInteraction) => void;
  onMonitorStatus: (status: InteractionMonitorStatus) => void;
};
export default function AllCameraAnalysis(props: Props) {
  const [enabled, setEnabled] = useState(false),
    [automatic, setAutomatic] = useState(false);
  const [armed, setArmed] = useState(false),
    [stage, setStage] = useState("off");
  const [recordedAllowed, setRecordedAllowed] = useState(false);
  const [rows, setRows] = useState<
    ReturnType<MultiCameraInteractions["snapshot"]>
  >([]);
  const [submitted, setSubmitted] = useState<ProductTicket | null>(null);
  const [status, setStatus] = useState(
    "All-camera analysis is off. Enable it explicitly, then start detection.",
  );
  const [soundMessage, setSoundMessage] = useState(
    "Sound is off. Commission this camera set before arming.",
  );
  const [alerts, setAlerts] = useState<SavedInteraction[]>([]);
  const [inspected, setInspected] = useState(0);
  const controller = useRef(new MultiCameraInteractions());
  const options = useRef({
    ...props,
    enabled,
    automatic,
    armed,
    recordedAllowed,
  });
  options.current = { ...props, enabled, automatic, armed, recordedAllowed };
  const active = useRef<{ ticket: ProductTicket; id: string | null } | null>(
    null,
  );
  const previousRun = useRef<AllCameraRun | null>(null);
  const previousCalibration = useRef(props.cameraCalibration);
  const commission = useRef(new InteractionAlarmCommission());
  const speaker = useRef<BrowserAttentionSound | null>(null);
  const mounted = useRef(true);
  const attention = useRef(new InteractionAttentionPolicy());
  const progress = useRef({ media: -1, at: 0 });
  const submitRef = useRef<() => Promise<void>>(async () => {});

  function current(run = options.current.run) {
    const video = options.current.videoRef.current;
    return (
      !!run &&
      run === options.current.run &&
      runtimeHealth.canMonitor() &&
      runtimeHealth.snapshot().context === run.runtime &&
      options.current.readRunning() &&
      !!video &&
      !video.paused &&
      !video.seeking &&
      video.playbackRate === 1 &&
      !document.hidden &&
      video.videoWidth === run.cameras[0].source_width &&
      video.videoHeight === run.cameras[0].source_height
    );
  }
  function contextKey() {
    const run = options.current.run;
    return current(run)
      ? JSON.stringify([
          run!.runId,
          run!.generation,
          run!.sourceKey,
          run!.runtime,
          run!.cameras.map(cameraKey),
          options.current.cameraCalibration,
        ])
      : "";
  }
  function disarm(
    message = "All-camera sound is off. Repeat the sound check before arming.",
  ) {
    commission.current.invalidate();
    speaker.current?.disarm();
    options.current.armed = false;
    if (mounted.current) {
      setArmed(false);
      setStage("off");
      setRecordedAllowed(false);
      setSoundMessage(message);
    }
  }
  function cancel() {
    controller.current.stop(performance.now());
    options.current.enabled = options.current.automatic = false;
    if (active.current) {
      active.current.ticket.cancelled = true;
      if (active.current.id)
        void api(
          `/interactions/jobs/${active.current.id}/cancel`,
          "POST",
          {},
        ).catch(() => undefined);
    }
    disarm();
    attention.current.resetRun();
    if (mounted.current) {
      setEnabled(false);
      setAutomatic(false);
      setSubmitted(null);
      setAlerts([]);
      setRows(controller.current.snapshot(performance.now()));
      setStatus(
        "All-camera analysis stopped. Old samples and results cannot sound.",
      );
    }
  }
  function silence() {
    speaker.current?.stop();
    setSoundMessage(
      "All-camera attention sound stopped. Review the camera-labelled observations.",
    );
  }
  async function testSound() {
    const key = contextKey();
    if (
      !key ||
      !options.current.enabled ||
      options.current.muted ||
      options.current.volume <= 0
    )
      return;
    disarm();
    const revision = commission.current.beginTest(key);
    setStage("testing");
    const sound = (speaker.current ??= new BrowserAttentionSound());
    const activation = await sound.arm();
    if (
      !mounted.current ||
      !commission.current.currentTest(revision, contextKey())
    )
      return;
    if (!activation.ok) {
      disarm(activation.message);
      return;
    }
    const result = sound.play({
      volume: options.current.volume,
      durationSeconds: 2,
    });
    if (
      !result.ok ||
      !commission.current.finishTest(revision, contextKey(), performance.now())
    ) {
      disarm(result.message);
      return;
    }
    setStage("confirm");
    setSoundMessage(
      `Two-second test requested for this ${options.current.count}-camera run. Staff must confirm physical audibility.`,
    );
  }
  async function submit() {
    const config = options.current;
    if (!config.enabled || !config.modelReady || !current() || active.current)
      return;
    const ticket = controller.current.reserve(performance.now());
    if (!ticket) return;
    const pending = { ticket, id: null as string | null };
    active.current = pending;
    setSubmitted(ticket);
    setStatus(
      `${cameraName(ticket.camera)}: analysing these four frames. Other cameras keep sampling; no VLM job is queued.`,
    );
    const payload = {
      run_id: ticket.run.runId,
      source_kind: ticket.run.sourceKind,
      source_label: ticket.run.sourceLabel.slice(0, 120),
      camera_context: ticket.camera,
      frames: ticket.frames.map(({ at_seconds, jpeg_base64 }) => ({
        at_seconds,
        jpeg_base64,
      })),
      ...(config.cameraCalibration
        ? { camera_calibration: config.cameraCalibration }
        : {}),
    };
    const key = idempotencyKey("/interactions/jobs", "POST", payload);
    let outcome: "completed" | "failed" | "cancelled" = "cancelled",
      failure = "";
    try {
      const created = await api<Job>("/interactions/jobs", "POST", payload);
      pending.id = created.id;
      if (ticket.cancelled || !current(ticket.run)) {
        await api(`/interactions/jobs/${created.id}/cancel`, "POST", {}).catch(
          () => undefined,
        );
        return;
      }
      while (!ticket.cancelled && current(ticket.run)) {
        const job = await api<Job>(`/interactions/jobs/${created.id}`);
        if (!mounted.current || ticket.cancelled || !current(ticket.run))
          return;
        if (job.status === "completed" && job.result) {
          const result = job.result;
          if (
            result.id !== created.id ||
            result.run_id !== ticket.run.runId ||
            result.source_kind !== ticket.run.sourceKind ||
            !result.camera_context ||
            cameraKey(result.camera_context) !== cameraKey(ticket.camera)
          )
            throw new Error(
              "The returned observation does not match this camera. It was discarded.",
            );
          outcome = "completed";
          config.onResult(result);
          const now = performance.now();
          const fresh = freshInteractionAlarm({
            enabled: true,
            sameRun: true,
            running: current(ticket.run),
            visible: !document.hidden,
            playing: true,
            seeking: false,
            playbackRate: 1,
            now,
            lastFrameAt: ticket.frames[3].capturedAt,
            lastProgressAt: progress.current.at,
            lastAlarmAt: -Infinity,
            result,
          });
          if (fresh) {
            setAlerts((previous) =>
              [
                result,
                ...previous.filter(
                  (item) =>
                    item.camera_context?.camera_index !==
                    ticket.camera.camera_index,
                ),
              ].slice(0, 6),
            );
            if (
              options.current.armed &&
              !options.current.muted &&
              claimInteractionSound(attention.current, commission.current, {
                id: result.id,
                camera: cameraKey(ticket.camera),
                now,
                context: contextKey(),
                source: result.source_kind,
              })
            ) {
              const played = speaker.current?.play({
                volume: options.current.volume,
                durationSeconds: 8,
              });
              setSoundMessage(
                `${cameraName(ticket.camera)}: ${played?.message ?? "Sound unavailable; review the visual observation."}`,
              );
            }
          }
          setStatus(
            `${cameraName(ticket.camera)}: ${interactionLabels[result.action]}. ${fresh ? "Fresh attention; review required." : "Saved for staff review; no fresh alarm requested."}`,
          );
          return;
        }
        if (job.status === "failed" || job.status === "cancelled") {
          // A locally requested cancellation marks the ticket and exits above.
          // A server-side cancellation here is therefore an unexpected failure.
          outcome = "failed";
          failure = job.error ?? "Analysis ended.";
          return;
        }
        if (performance.now() - ticket.startedAt > 180000) {
          await api(
            `/interactions/jobs/${created.id}/cancel`,
            "POST",
            {},
          ).catch(() => undefined);
          outcome = "failed";
          failure = "Analysis exceeded its time limit. No new job was queued.";
          return;
        }
        await new Promise((resolve) => setTimeout(resolve, 750));
      }
    } catch (error) {
      outcome = "failed";
      failure = error instanceof Error ? error.message : "Analysis failed.";
      if (error instanceof ApiError && error.code === "EVIDENCE_LIMIT") {
        failure += " Review storage before enabling automatic analysis again.";
      }
    } finally {
      if (outcome === "failed" && current(ticket.run)) {
        options.current.automatic = false;
        if (mounted.current) setAutomatic(false);
        disarm(
          "Analysis failed. Automatic submissions and attention sound are paused until staff review the model status.",
        );
        failure = `${failure ?? "Analysis failed."} Automatic submissions are paused.`;
      }
      forgetAction("/interactions/jobs", "POST", payload, key);
      controller.current.settle(ticket, outcome, performance.now(), failure);
      if (active.current === pending) active.current = null;
      if (mounted.current && current(ticket.run)) {
        setSubmitted(null);
        setRows(controller.current.snapshot(performance.now()));
        if (failure) setStatus(`${cameraName(ticket.camera)}: ${failure}`);
      }
    }
  }
  submitRef.current = submit;
  useEffect(() => {
    if (previousRun.current && previousRun.current !== props.run) cancel();
    previousRun.current = props.run;
    if (props.run) controller.current.start(props.run, performance.now());
    else controller.current.stop(performance.now());
    setRows(controller.current.snapshot(performance.now()));
  }, [props.run]);
  useEffect(() => {
    if (!props.modelReady) cancel();
  }, [props.modelReady]);
  useEffect(() => {
    disarm("Volume or mute changed. Test this camera set again before arming.");
  }, [props.volume, props.muted]);
  useEffect(() => {
    const previous = previousCalibration.current;
    previousCalibration.current = props.cameraCalibration;
    if (previous && previous !== props.cameraCalibration)
      disarm(
        "Camera calibration changed. Confirm all zones and test this camera set again before arming.",
      );
  }, [props.cameraCalibration]);
  useEffect(() => {
    mounted.current = true;
    props.cancelRef.current = cancel;
    props.silenceRef.current = silence;
    const unsubscribe = runtimeHealth.onInterrupt(cancel);
    const timer = setInterval(() => {
      const config = options.current,
        video = config.videoRef.current;
      if (
        commission.current.active &&
        !commission.current.matches(contextKey())
      )
        disarm();
      if (!config.enabled || !config.modelReady || !current() || !video) return;
      const now = performance.now();
      if (video.currentTime !== progress.current.media)
        progress.current = { media: video.currentTime, at: now };
      try {
        controller.current.observe(video.currentTime, now, (camera) =>
          captureInteractionFrame(video, now, camera.crop),
        );
      } catch (error) {
        cancel();
        setStatus(error instanceof Error ? error.message : "Sampling stopped.");
        return;
      }
      setRows(controller.current.snapshot(now));
      if (config.automatic) void submitRef.current();
    }, 100);
    return () => {
      mounted.current = false;
      cancel();
      clearInterval(timer);
      unsubscribe();
      props.cancelRef.current = props.silenceRef.current = null;
      speaker.current?.dispose();
    };
  }, []);
  const running = current();
  useEffect(
    () =>
      props.onMonitorStatus({
        state: !props.modelReady
          ? "unavailable"
          : !enabled
            ? "off"
            : !running
              ? "waiting"
              : submitted
                ? "analysing"
                : "collecting",
        label: !props.modelReady
          ? "ALL-CAMERA PRODUCT MODEL UNAVAILABLE"
          : !enabled
            ? "ALL-CAMERA PRODUCT ANALYSIS OFF"
            : `ALL ${props.count} CAMERAS · ${submitted ? "ONE SEQUENCE ANALYSING" : running ? "SAMPLING" : "WAITING FOR START"}`,
        guidance:
          "Separate buffers for every confirmed camera. One model job at a time; actual intervals and missed opportunities appear below.",
      }),
    [
      props.modelReady,
      props.count,
      props.onMonitorStatus,
      enabled,
      running,
      !!submitted,
    ],
  );
  const selected = rows[inspected] ?? rows[0];
  return (
    <section
      className="all-camera-analysis"
      aria-label="All-camera product analysis"
    >
      <h3>All {props.count} confirmed cameras</h3>
      <p>
        Sampling runs independently for each camera. Product analysis rotates
        fairly: target once per camera every 10 seconds, at least 2 seconds
        between global starts, one model job and no backlog. Actual cadence
        depends on the laptop. Actions between sampled moments can be missed.
        {props.count === 6 &&
          " Six cameras require at least 12 seconds per complete rotation, even before model latency."}
      </p>
      <div className="all-camera-controls">
        <label>
          <input
            type="checkbox"
            checked={enabled}
            disabled={!props.modelReady || !runtimeHealth.snapshot().ready}
            onChange={(event) => {
              if (!event.target.checked) cancel();
              else {
                options.current.enabled = true;
                setEnabled(true);
                if (props.run)
                  controller.current.start(props.run, performance.now());
              }
            }}
          />
          Enable all-camera product analysis
        </label>
        <label>
          <input
            type="checkbox"
            checked={automatic}
            disabled={!enabled}
            onChange={(event) => {
              options.current.automatic = event.target.checked;
              setAutomatic(event.target.checked);
            }}
          />
          Analyse all cameras automatically
        </label>
        <button
          disabled={
            !enabled ||
            !running ||
            !!active.current ||
            !rows.some((row) => row.frames.length === 4 && row.nextIn === 0)
          }
          onClick={() => void submit()}
        >
          Analyse next ready camera
        </button>
        <button onClick={cancel}>Stop all-camera analysis</button>
      </div>
      <fieldset>
        <legend>All-camera sound check</legend>
        <p>
          One speaker check applies to this entire confirmed camera set and run.
          Changing any camera or restarting invalidates it.
        </p>
        <button
          disabled={!enabled || !running || props.muted || stage === "testing"}
          onClick={() => void testSound()}
        >
          Test all-camera alarm sound
        </button>
        <button
          disabled={stage !== "confirm" || !running}
          onClick={() => {
            if (commission.current.confirm(contextKey(), performance.now())) {
              speaker.current?.stop();
              setStage("confirmed");
              setSoundMessage(
                "Staff confirmed hearing the test for this camera set. Choose whether to arm.",
              );
            } else disarm();
          }}
        >
          I heard the all-camera test tone
        </button>
        {props.run?.sourceKind === "RECORDED_VIDEO" && (
          <label>
            <input
              type="checkbox"
              checked={recordedAllowed}
              disabled={stage !== "confirmed" || armed}
              onChange={(event) => setRecordedAllowed(event.target.checked)}
            />
            Allow all-camera alarm in this recorded test
          </label>
        )}
        <label>
          <input
            type="checkbox"
            checked={armed}
            disabled={
              !enabled ||
              !running ||
              !props.cameraCalibration ||
              stage !== "confirmed" ||
              (props.run?.sourceKind === "RECORDED_VIDEO" && !recordedAllowed)
            }
            onChange={(event) => {
              if (!event.target.checked) disarm();
              else {
                const value = commission.current.arm(
                  contextKey(),
                  recordedAllowed,
                );
                options.current.armed = value;
                setArmed(value);
              }
            }}
          />
          Experimental attention alarm for all confirmed cameras
        </label>
        {!props.cameraCalibration && (
          <p role="status">
            Sound cannot be armed until entrance, exit, cashier and shelf zone
            coverage is confirmed above.
          </p>
        )}
        <button onClick={() => disarm()}>
          Stop sound & disarm all cameras
        </button>
        <p role="status">{soundMessage}</p>
      </fieldset>
      <p role="status">{status}</p>
      <div className="all-camera-cards">
        {rows.map((row, index) => (
          <article
            key={cameraKey(row.camera)}
            aria-label={`${cameraName(row.camera)} processing`}
          >
            <button
              aria-pressed={inspected === index}
              onClick={() => setInspected(index)}
            >
              {cameraName(row.camera)}
            </button>
            <p>
              {row.pending
                ? "Analysing frozen sequence"
                : !enabled || !running
                  ? "Stopped"
                  : row.error
                    ? "Sample unavailable"
                    : `${row.frames.length}/4 fresh frames`}
            </p>
            <p>
              {row.started} started · {row.completed} completed · {row.failed}{" "}
              failed · {row.missed} missed target opportunities
            </p>
            <p>
              {row.lastCompletedAge === null
                ? "No completed product window"
                : `Last completion ${(row.lastCompletedAge / 1000).toFixed(1)}s ago`}
              {row.late
                ? ` · ${row.late} delayed results (no fresh alarm)`
                : ""}
            </p>
            <p>
              {row.cadence === null
                ? "Actual cadence available after two starts"
                : `Last start interval ${(row.cadence / 1000).toFixed(1)}s`}
            </p>
            {row.interval && (
              <p>
                Last analysed samples {row.interval[0].toFixed(2)}–
                {row.interval[1].toFixed(2)}s
              </p>
            )}
            {row.error && <p className="ld-error">{row.error}</p>}
          </article>
        ))}
      </div>
      <section aria-label="Inspected camera samples">
        <h4>{selected ? cameraName(selected.camera) : "Camera samples"}</h4>
        <div className="all-camera-frames">
          {selected?.frames.map((frame, index) => (
            <figure key={frame.capturedAt}>
              <img
                src={`data:image/jpeg;base64,${frame.jpeg_base64}`}
                alt={`Camera ${selected.camera.camera_index + 1} buffered frame ${index + 1}`}
              />
              <figcaption>
                {frame.at_seconds.toFixed(2)}s · JPEG {frame.width}×
                {frame.height}px
              </figcaption>
            </figure>
          ))}
        </div>
      </section>
      {submitted && (
        <section aria-label="All-camera submitted sequence">
          <h4>{cameraName(submitted.camera)} · exact submitted sequence</h4>
          <div className="all-camera-frames">
            {submitted.frames.map((frame, index) => (
              <figure key={frame.capturedAt}>
                <img
                  src={`data:image/jpeg;base64,${frame.jpeg_base64}`}
                  alt={`Camera ${submitted.camera.camera_index + 1} submitted frame ${index + 1}`}
                />
                <figcaption>
                  {frame.at_seconds.toFixed(2)}s · {frame.width}×{frame.height}
                  px
                </figcaption>
              </figure>
            ))}
          </div>
        </section>
      )}
      {alerts.map((item) => (
        <div className="interaction-alert" role="alert" key={item.id}>
          <strong>
            {cameraName(item.camera_context!)}: possible concealment — review
            required
          </strong>
          <span>
            {item.source_kind === "RECORDED_VIDEO" ? "RECORDED TEST. " : ""}
            Check the sampled frames below. Sound may be off or cooling down.
            Acknowledgement quiets repeat sound from this camera for two
            minutes; observations continue to be saved for review.
          </span>
          <button
            onClick={() => {
              silence();
              attention.current.acknowledge(
                cameraKey(item.camera_context!),
                performance.now(),
              );
              setAlerts((previous) =>
                previous.filter((row) => row.id !== item.id),
              );
            }}
          >
            Acknowledge Camera {item.camera_context!.camera_index + 1} attention
          </button>
        </div>
      ))}
      <p className="ld-hint">
        Counts describe sampled processing opportunities, not detection accuracy
        or continuous footage. Source timestamps are browser-reported; upstream
        recorder freezes cannot be ruled out. Completed camera-labelled
        observations use the shared review and case controls below.
      </p>
    </section>
  );
}
