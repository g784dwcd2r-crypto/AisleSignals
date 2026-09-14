import CameraContextDetails from "./CameraContextDetails";
import AllCameraAnalysis from "./AllCameraAnalysis";
import type { AllCameraRun } from "./multiCameraInteractions";
import type { ConfirmedCameraLayout } from "./CameraLayoutPicker";
import { runtimeHealth } from "./runtimeHealth";
import { useEffect, useRef, useState, useSyncExternalStore } from "react";
import type { RefObject } from "react";
import {
  BellRing,
  BrainCircuit,
  Check,
  ClipboardList,
  RefreshCw,
  ScanEye,
  Square,
  Trash2,
  VolumeX,
} from "lucide-react";
import { api, ApiError, forgetAction, idempotencyKey } from "./api";
import { BrowserAttentionSound } from "./playbackAlerts";
import type { DetectionRect, LiveSourceKind } from "./liveDetectionTypes";
import {
  captureInteractionFrame,
  prepareInteractionSound,
  confirmedCameraCalibration,
  freshInteractionAlarm,
  InteractionFrameBuffer,
  InteractionAlarmCommission,
  InteractionAttentionPolicy,
  interactionLabels,
  interactionCropPixels,
  safeInteractionFrameUrl,
  type CameraCalibration,
} from "./interactionCapture";
import type {
  InteractionReview,
  SampledFrame,
  SavedInteraction as SampledInteraction,
} from "./interactionCapture";
import "./interactionAnalysis.css";
import CameraLayoutPicker from "./CameraLayoutPicker";
import { validateCameraArea } from "./cameraGrid";
import type { Incident } from "./types";

type SavedInteraction = SampledInteraction & { incident_id?: string | null };

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
  evidence_policy?: {
    retention_seconds: number;
    site_limit: number;
    installation_limit: number;
    encryption: string;
    rolling_cleanup: string;
  };
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
  sourceKey: string;
  onMonitorStatus: (status: InteractionMonitorStatus) => void;
  onCameraSelection?: (selection: SelectedCamera | null) => void;
  onOpenInteractionCase?: (id: string) => Promise<void>;
  allMode?: boolean;
  allRun?: AllCameraRun | null;
  allCameraCount?: number;
  onConfirmedLayout?: (layout: ConfirmedCameraLayout | null) => void;
};
export type SelectedCamera = {
  sourceKey: string;
  crop: DetectionRect;
  label: string;
};
export type InteractionMonitorStatus = {
  state:
    | "checking"
    | "unavailable"
    | "off"
    | "waiting"
    | "collecting"
    | "ready"
    | "analysing";
  label: string;
  guidance: string;
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

type SubmittedWindow = {
  frames: SampledFrame[];
  label: string;
  state: "analysing" | "completed" | "failed";
  resultId?: string;
  expiresAt?: number;
};
function FrameStrip({
  frames,
  kind,
}: {
  frames: SampledFrame[];
  kind: "Buffered" | "Submitted";
}) {
  return (
    <div className="interaction-sample-strip">
      {frames.map((frame, index) => (
        <figure key={`${frame.capturedAt}:${index}`}>
          <img
            src={`data:image/jpeg;base64,${frame.jpeg_base64}`}
            alt={`${kind} sample ${index + 1} at ${frame.at_seconds.toFixed(2)} seconds`}
          />
          <figcaption>
            <strong>
              Frame {index + 1} · {frame.at_seconds.toFixed(2)}s
            </strong>
            <span>
              JPEG {frame.width} × {frame.height} px
            </span>
            <span>
              Source crop {frame.sourceWidth} × {frame.sourceHeight} px
            </span>
          </figcaption>
        </figure>
      ))}
    </div>
  );
}

/** Frames are sampled independently of pose detections; one job can run at a time. */
export default function InteractionAnalysis({
  videoRef,
  cancelRef,
  silenceRef,
  readSession,
  muted,
  volume,
  branchName,
  sourceKey,
  onMonitorStatus,
  onCameraSelection,
  onOpenInteractionCase,
  allMode = false,
  allRun = null,
  allCameraCount = 0,
  onConfirmedLayout,
}: Props) {
  const health = useSyncExternalStore(
    runtimeHealth.subscribe,
    runtimeHealth.snapshot,
  );
  const allCancel = useRef<(() => void) | null>(null);
  const allSilence = useRef<(() => void) | null>(null);
  const allModeRef = useRef(allMode);
  allModeRef.current = allMode;
  const layoutCallback = useRef(onConfirmedLayout);
  layoutCallback.current = onConfirmedLayout;
  const [enabled, setEnabled] = useState(false);
  const [automatic, setAutomatic] = useState(false);
  const [alarmEnabled, setAlarmEnabled] = useState(false);
  const [commissionStage, setCommissionStage] = useState<
    "off" | "testing" | "confirm" | "confirmed"
  >("off");
  const [recordedAlarmAllowed, setRecordedAlarmAllowed] = useState(false);
  const [calibrationChecks, setCalibrationChecks] = useState({
    entrance: false,
    exit: false,
    cashier: false,
    shelves: false,
  });
  const calibrationReady = Object.values(calibrationChecks).every(Boolean);
  const cameraCalibration: CameraCalibration | null = calibrationReady
    ? confirmedCameraCalibration()
    : null;
  const [sourceReady, setSourceReady] = useState(false);
  const [recordedSource, setRecordedSource] = useState(false);
  const [cropEnabled, setCropEnabled] = useState(false);
  const [crop, setCrop] = useState<DetectionRect>({
    x: 0,
    y: 0,
    width: 1,
    height: 1,
  });
  const [cropError, setCropError] = useState("");
  const [cameraChoice, setCameraChoice] = useState({
    sourceKey: "",
    confirmed: false,
    custom: false,
    label: "",
  });
  const cameraReady =
    cameraChoice.confirmed &&
    cameraChoice.sourceKey === sourceKey &&
    !!sourceKey;
  const [model, setModel] = useState<ModelStatus | null>(null);
  const [modelChecked, setModelChecked] = useState(false);
  const [status, setStatus] = useState(
    "Enable analysis to sample the selected CCTV video.",
  );
  const [error, setError] = useState("");
  const [samples, setSamples] = useState<SampledFrame[]>([]);
  const count = samples.length;
  const [submittedWindow, setSubmittedWindow] =
    useState<SubmittedWindow | null>(null);
  const [lastAnalysed, setLastAnalysed] = useState<{
    start: number;
    end: number;
    action: string;
  } | null>(null);
  const [nextSubmissionIn, setNextSubmissionIn] = useState<number | null>(null);
  const submittedPreview = useRef(submittedWindow);
  submittedPreview.current = submittedWindow;
  const [busy, setBusy] = useState(false);
  const [elapsed, setElapsed] = useState(0);
  const [history, setHistory] = useState<SavedInteraction[]>([]);
  const [refreshing, setRefreshing] = useState(false);
  const [mutation, setMutation] = useState<string | null>(null);
  const [caseDraft, setCaseDraft] = useState<{
    id: string;
    version: number;
    title: string;
    notes: string;
  } | null>(null);
  const caseWrite = useRef(false);
  const caseRequest = useRef<{
    path: string;
    payload: { expected_version: number; title: string; notes: string };
    key: string;
  } | null>(null);
  function clearCaseRequest() {
    const pending = caseRequest.current;
    if (pending)
      forgetAction(pending.path, "POST", pending.payload, pending.key);
    caseRequest.current = null;
  }
  const [highlight, setHighlight] = useState<string | null>(null);
  const [soundStatus, setSoundStatus] = useState(
    "Product interaction alarm is off.",
  );
  const mounted = useRef(true);
  const commission = useRef(new InteractionAlarmCommission());
  const attention = useRef(new InteractionAttentionPolicy());
  const historyRevision = useRef(0);
  const refreshRevision = useRef(0);
  const cropPreview = useRef<HTMLCanvasElement>(null);
  const selectionCallback = useRef(onCameraSelection);
  selectionCallback.current = onCameraSelection;
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
    modelReady: model?.ready === true,
    cameraReady,
    cameraLabel: cameraChoice.label,
    sourceKey,
    cameraCalibration,
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
    modelReady: model?.ready === true,
    cameraReady,
    cameraLabel: cameraChoice.label,
    sourceKey,
    cameraCalibration,
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
  const lastSubmitted = useRef(-Infinity);
  const lastProgress = useRef({ media: -1, wall: 0 });
  const submitRef = useRef<() => Promise<void>>(async () => {});

  function contextKey() {
    const current = options.current.readSession();
    const video = videoRef.current;
    if (
      !current.running ||
      !options.current.cameraReady ||
      !current.source ||
      !video ||
      video.paused ||
      video.seeking ||
      video.playbackRate !== 1 ||
      document.hidden
    )
      return "";
    return JSON.stringify([
      branchName,
      current.runId,
      current.generation,
      current.source.kind,
      current.source.label,
      options.current.cropEnabled ? options.current.crop : null,
      options.current.sourceKey,
      options.current.cameraLabel,
      options.current.cameraCalibration,
    ]);
  }
  function disarmAlarm(
    message = "Product interaction alarm is off. Test the sound again before arming.",
  ) {
    commission.current.invalidate();
    sound.current?.disarm();
    options.current.alarmEnabled = false;
    if (mounted.current) {
      setAlarmEnabled(false);
      setCommissionStage("off");
      setRecordedAlarmAllowed(false);
      setSoundStatus(message);
    }
  }
  async function testSound() {
    const context = contextKey();
    if (!context || options.current.muted || options.current.volume <= 0)
      return;
    disarmAlarm();
    attention.current.resetRun();
    const revision = commission.current.beginTest(context);
    setCommissionStage("testing");
    const speaker = (sound.current ??= new BrowserAttentionSound());
    const activation = await speaker.arm();
    if (
      !mounted.current ||
      !commission.current.currentTest(revision, contextKey()) ||
      context !== contextKey()
    )
      return;
    if (!activation.ok) {
      disarmAlarm(activation.message);
      return;
    }
    const played = speaker.play({
      volume: options.current.volume,
      durationSeconds: 2,
    });
    if (
      !played.ok ||
      !commission.current.finishTest(revision, contextKey(), performance.now())
    ) {
      disarmAlarm(played.message);
      return;
    }
    setCommissionStage("confirm");
    setSoundStatus(
      "Two-second test tone requested. A member of staff must confirm it was clearly audible at this laptop.",
    );
  }

  function confirmSound() {
    if (!commission.current.confirm(contextKey(), performance.now())) {
      disarmAlarm(
        "Sound confirmation expired or the source changed. Run the sound test again.",
      );
      return;
    }
    sound.current?.stop();
    setCommissionStage("confirmed");
    setSoundStatus(
      "Staff confirmed hearing the test tone for this run and volume. Choose whether to arm the product attention alarm.",
    );
  }

  function silence() {
    allSilence.current?.();
    if (!options.current.alarmEnabled) {
      disarmAlarm(
        "Product sound stopped. Run the sound check again before arming.",
      );
      return;
    }
    sound.current?.stop();
    if (mounted.current) setSoundStatus("Product interaction alarm silenced.");
  }
  function cancel() {
    allCancel.current?.();
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
    disarmAlarm();
    if (mounted.current) {
      setSamples([]);
      setSubmittedWindow(null);
      setLastAnalysed(null);
      setNextSubmissionIn(null);
      setHighlight(null);
      setStatus("Analysis stopped. Pending results cannot trigger an alarm.");
    }
  }

  useEffect(
    () =>
      runtimeHealth.onInterrupt((reason) => {
        options.current.enabled = false;
        options.current.automatic = false;
        setEnabled(false);
        setAutomatic(false);
        cancel();
        setStatus(
          reason + " Enable analysis again after restarting detection.",
        );
      }),
    [],
  );

  function invalidateCameraLayout() {
    layoutCallback.current?.(null);
    selectionCallback.current?.(null);
    cancel();
    options.current.cameraReady = false;
    options.current.cameraLabel = "";
    options.current.cropEnabled = false;
    options.current.cropError = "";
    options.current.crop = { x: 0, y: 0, width: 1, height: 1 };
    setCropEnabled(false);
    setCropError("");
    setCrop(options.current.crop);
    setCameraChoice({ sourceKey, confirmed: false, custom: false, label: "" });
    setCalibrationChecks({
      entrance: false,
      exit: false,
      cashier: false,
      shelves: false,
    });
    setStatus(
      "Choose or confirm the camera layout before sampling product interactions. Earlier camera samples and alarms were cleared.",
    );
  }
  function changeCrop(
    nextEnabled: boolean,
    nextCrop: DetectionRect,
    tileLabel?: string,
  ) {
    if (!tileLabel) layoutCallback.current?.(null);
    selectionCallback.current?.(null);
    cancel();
    let invalid = "";
    const video = videoRef.current;
    if (nextEnabled) {
      try {
        validateCameraArea(nextCrop);
        if (video?.videoWidth && video.videoHeight)
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
    setCalibrationChecks({
      entrance: false,
      exit: false,
      cashier: false,
      shelves: false,
    });
    const label = tileLabel ?? "custom selected area";
    options.current.cameraReady = nextEnabled && !!sourceKey;
    options.current.cameraLabel = label;
    setCameraChoice({
      sourceKey,
      confirmed: nextEnabled,
      custom: nextEnabled && !tileLabel,
      label,
    });
    if (nextEnabled && sourceKey && !invalid)
      selectionCallback.current?.({ sourceKey, crop: { ...nextCrop }, label });
    setStatus(
      nextEnabled
        ? "Analysis area changed. Collect four new frames; the product alarm has been disarmed."
        : "No camera area selected. Confirm a camera layout, select Single camera, or enable a custom area before sampling. The product alarm has been disarmed.",
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

  async function refresh(preserveError = false) {
    const requestRevision = ++refreshRevision.current;
    const startingHistory = historyRevision.current;
    setRefreshing(true);
    const results = await Promise.allSettled([
      api<ModelStatus>("/interactions/status"),
      api<{ items: SavedInteraction[] }>("/interactions"),
    ]);
    if (!mounted.current || requestRevision !== refreshRevision.current) return;
    if (results[0].status === "fulfilled") {
      setModel(results[0].value);
    } else {
      setModel(null);
      setError(failureText(results[0].reason));
    }
    const ready = results[0].status === "fulfilled" && results[0].value.ready;
    options.current.modelReady = ready;
    setModelChecked(true);
    if (!ready) {
      options.current.enabled = false;
      options.current.automatic = false;
      setEnabled(false);
      setAutomatic(false);
      cancel();
      setStatus(
        "Product analysis is unavailable. No product frames are being sampled or classified. Check the local model, then select Refresh model & history.",
      );
    }
    if (results[1].status === "fulfilled") {
      if (startingHistory === historyRevision.current)
        setHistory(results[1].value.items);
    } else setError(failureText(results[1].reason));
    if (
      !preserveError &&
      results.every((result) => result.status === "fulfilled")
    )
      setError("");
    setRefreshing(false);
  }

  async function submit() {
    const current = options.current.readSession();
    const video = videoRef.current;
    if (
      allModeRef.current ||
      job.current ||
      !options.current.enabled ||
      !options.current.cameraReady ||
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
    setSubmittedWindow({
      frames,
      label: options.current.cameraLabel,
      state: "analysing",
    });
    setStatus("Sending four sampled frames to the local interaction model…");
    const payload = {
      run_id: current.runId,
      source_kind: current.source.kind,
      source_label:
        `${current.source.label.slice(0, 70)} · ${options.current.cameraLabel}`.slice(
          0,
          120,
        ),
      frames: frames.map(({ at_seconds, jpeg_base64 }) => ({
        at_seconds,
        jpeg_base64,
      })),
      ...(options.current.cameraCalibration
        ? { camera_calibration: options.current.cameraCalibration }
        : {}),
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
          setSubmittedWindow({
            frames,
            label: options.current.cameraLabel,
            state: "completed",
            resultId: saved.id,
            expiresAt: Date.parse(saved.expires_at),
          });
          setLastAnalysed({
            start: frames[0].at_seconds,
            end: frames.at(-1)!.at_seconds,
            action: interactionLabels[saved.action],
          });
          historyRevision.current++;
          setHistory((previous) =>
            [saved, ...previous.filter((item) => item.id !== saved.id)].slice(
              0,
              50,
            ),
          );
          const latest = options.current.readSession();
          const now = performance.now();
          const visibleAttention = freshInteractionAlarm({
            enabled: true,
            sameRun:
              latest.runId === current.runId &&
              latest.generation === current.generation &&
              saved.run_id === current.runId &&
              latest.source?.kind === current.source.kind &&
              latest.source?.label === current.source.label &&
              saved.source_kind === current.source.kind,
            running: latest.running,
            visible: !document.hidden,
            playing: !video.paused,
            seeking: video.seeking,
            playbackRate: video.playbackRate,
            now,
            lastFrameAt: frames.at(-1)!.capturedAt,
            lastProgressAt: lastProgress.current.wall,
            lastAlarmAt: -Infinity,
            result: saved,
          });
          if (visibleAttention) setHighlight(saved.id);
          const soundReservation =
            visibleAttention &&
            options.current.alarmEnabled &&
            !options.current.muted &&
            options.current.volume > 0 &&
            prepareInteractionSound(attention.current, commission.current, {
              id: saved.id,
              camera: contextKey(),
              now,
              context: contextKey(),
              source: saved.source_kind,
            });
          let sounded = false;
          let soundFailure = "";
          if (soundReservation) {
            const played = sound.current?.play({
              volume: options.current.volume,
              durationSeconds: 8,
            });
            if (played?.ok && soundReservation()) {
              sounded = true;
              setSoundStatus(played.message);
            } else {
              soundFailure =
                played?.message ??
                "Audio is unavailable. Review the highlighted result.";
              options.current.automatic = false;
              setAutomatic(false);
              disarmAlarm(
                `${soundFailure} Automatic analysis is paused; repeat the sound check before re-enabling it.`,
              );
            }
          }
          const delay = Math.round((now - frames.at(-1)!.capturedAt) / 1000);
          setStatus(
            `${interactionLabels[saved.action]} · result ${delay}s after last sampled frame. ${sounded ? "Attention requested; review the sampled frames." : soundFailure ? "Visual attention remains; sound failed and automatic analysis is paused." : delay > 15 ? "Delayed result saved for review; no alarm." : visibleAttention ? "Visual attention requested; sound was not triggered." : "Saved for review."}`,
          );
          return;
        }
        if (result.status === "failed")
          throw new Error(
            result.error || "The local model could not analyse this sequence.",
          );
        if (result.status === "cancelled")
          throw new Error(
            "The local service cancelled analysis unexpectedly. No alarm was triggered.",
          );
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
        setSubmittedWindow({
          frames,
          label: options.current.cameraLabel,
          state: "failed",
        });
        disarmAlarm(
          "Analysis failed. Product alarm is off; check the service before recommissioning.",
        );
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
    historyRevision.current++;
    setMutation(item.id);
    setError("");
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
      if (mounted.current) {
        historyRevision.current++;
        setHistory((previous) =>
          previous.map((row) =>
            row.id === item.id && row.version <= updated.version
              ? updated
              : row,
          ),
        );
      }
    } catch (failure) {
      if (mounted.current) {
        setError(
          failure instanceof ApiError && failure.status === 409
            ? "Another reviewer changed this result. The latest review has been loaded; check it before choosing an outcome again."
            : failureText(failure),
        );
        if (failure instanceof ApiError && failure.status === 409)
          await refresh(true);
      }
    } finally {
      if (mounted.current) setMutation(null);
    }
  }
  async function remove(item: SavedInteraction) {
    historyRevision.current++;
    setMutation(item.id);
    if (highlight === item.id) {
      silence();
      setHighlight(null);
    }
    try {
      await api(`/interactions/${item.id}`, "DELETE");
      if (mounted.current) {
        setSubmittedWindow((previous) =>
          previous?.resultId === item.id ? null : previous,
        );
        historyRevision.current++;
        setHistory((previous) => previous.filter((row) => row.id !== item.id));
      }
    } catch (failure) {
      if (mounted.current) setError(failureText(failure));
    } finally {
      if (mounted.current) setMutation(null);
    }
  }

  async function createCase() {
    if (!caseDraft || caseWrite.current || mutation) return;
    caseWrite.current = true;
    setMutation(caseDraft.id);
    setError("");
    historyRevision.current++;
    const path = `/interactions/${caseDraft.id}/case`;
    const payload = {
      expected_version: caseDraft.version,
      title: caseDraft.title.trim(),
      notes: caseDraft.notes.trim(),
    };
    if (
      caseRequest.current &&
      (caseRequest.current.path !== path ||
        JSON.stringify(caseRequest.current.payload) !== JSON.stringify(payload))
    )
      clearCaseRequest();
    caseRequest.current = {
      path,
      payload,
      key: idempotencyKey(path, "POST", payload),
    };
    try {
      const result = await api<{
        incident: Incident;
        interaction: SavedInteraction;
      }>(path, "POST", payload);
      clearCaseRequest();
      if (!mounted.current) return;
      historyRevision.current++;
      setHistory((previous) =>
        previous.map((item) =>
          item.id === result.interaction.id ? result.interaction : item,
        ),
      );
      setCaseDraft(null);
      setStatus(
        `Case ${result.incident.reference} created from your reviewed observation. It remains unassessed; no loss or criminality conclusion was added.`,
      );
    } catch (failure) {
      if (!mounted.current) return;
      if (
        failure instanceof ApiError &&
        [404, 409, 410].includes(failure.status)
      ) {
        clearCaseRequest();
        setCaseDraft(null);
        setError(
          failure.status === 409
            ? "This observation changed or already has a case. The latest record has been loaded; review it before choosing a new action."
            : failureText(failure),
        );
        await refresh(true);
      } else setError(failureText(failure));
    } finally {
      caseWrite.current = false;
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
      if (allModeRef.current) return;
      const configuration = options.current;
      if (
        submittedPreview.current?.expiresAt &&
        Date.now() >= submittedPreview.current.expiresAt
      )
        setSubmittedWindow(null);
      const current = configuration.readSession();
      const video = videoRef.current;
      setNextSubmissionIn(
        configuration.automatic
          ? Math.max(
              0,
              Math.ceil(
                (10_000 - (performance.now() - lastSubmitted.current)) / 1000,
              ),
            )
          : null,
      );
      const ready =
        !!current.running &&
        !!current.source &&
        !!video &&
        !video.paused &&
        !video.seeking &&
        video.playbackRate === 1 &&
        video.readyState >= 2 &&
        !document.hidden;
      setSourceReady(ready);
      setRecordedSource(current.source?.kind === "RECORDED_VIDEO");
      if (
        commission.current.active &&
        !commission.current.matches(contextKey())
      )
        disarmAlarm(
          "Source context changed. Test the product alarm sound again before arming.",
        );
      if (
        !configuration.enabled ||
        !configuration.cameraReady ||
        !configuration.modelReady ||
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
          setSamples(buffer.current.snapshot(now));
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
      clearCaseRequest();
      selectionCallback.current?.(null);
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
    disarmAlarm(
      "Test the current volume and confirm it is audible before arming.",
    );
    sound.current?.setVolume(volume);
    // A previous confirmation applies only to the sound settings actually tested.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [muted, volume]);

  const monitorState: InteractionMonitorStatus["state"] = !modelChecked
    ? "checking"
    : !model?.ready
      ? "unavailable"
      : !enabled
        ? "off"
        : !cameraReady || !sourceReady || !!cropError
          ? "waiting"
          : busy && !job.current?.cancelled
            ? "analysing"
            : count < 4
              ? "collecting"
              : "ready";
  const monitorLabel = {
    checking: "PRODUCT ANALYSIS CHECKING",
    unavailable: "PRODUCT ANALYSIS UNAVAILABLE",
    off: "PRODUCT ANALYSIS OFF",
    waiting: "PRODUCT ANALYSIS WAITING",
    collecting: `PRODUCT ANALYSIS ${automatic ? "AUTOMATIC" : "MANUAL"} · COLLECTING`,
    ready: `PRODUCT ANALYSIS ${automatic ? "AUTOMATIC" : "MANUAL"} · READY`,
    analysing: "PRODUCT ANALYSIS ANALYSING",
  }[monitorState];
  const monitorGuidance =
    monitorState === "unavailable"
      ? model?.mode === "disabled"
        ? "Product classification is disabled in this application session. Restart with the local interaction model, then select Refresh model & history. Body tracking can run separately."
        : "The local product model is unavailable. Check that it is running, then select Refresh model & history. Body tracking can run separately."
      : monitorState === "checking"
        ? "Checking the local product model. Body tracking and product classification have separate readiness states."
        : monitorState === "off"
          ? "The product model is available but analysis is off. Enable product interaction analysis below, then choose automatic analysis or analyse a recent sequence."
          : monitorState === "waiting"
            ? !cameraReady
              ? "Choose and confirm one camera tile, select Single camera, or set a custom area below. An unconfirmed CCTV mosaic is not product-analysed."
              : cropError ||
                "Product analysis is enabled but is waiting for a fresh playing video. Start detection to collect samples."
            : monitorState === "analysing"
              ? "The local product model is analysing one sampled sequence. Body keypoints are a separate movement overlay."
              : monitorState === "collecting"
                ? `${automatic ? "Automatic" : "Manual"} product analysis: collecting ${count}/4 fresh sampled frames. No product classification has been requested for this window yet.`
                : automatic
                  ? `Automatic product analysis has fresh frames. ${nextSubmissionIn ? `Next submission in ${nextSubmissionIn}s.` : "Waiting for the next submission slot."} Only one model job runs at a time.`
                  : "Four fresh frames are ready. Select Analyse recent sequence, or enable Analyse automatically. Sound requires its separate staff sound check.";
  useEffect(() => {
    if (allMode) return;
    onMonitorStatus({
      state: monitorState,
      label: monitorLabel,
      guidance: monitorGuidance,
    });
  }, [onMonitorStatus, monitorState, monitorLabel, monitorGuidance, allMode]);

  useEffect(() => {
    options.current.enabled = options.current.automatic = false;
    setEnabled(false);
    setAutomatic(false);
    setCalibrationChecks({
      entrance: false,
      exit: false,
      cashier: false,
      shelves: false,
    });
    cancel();
  }, [allMode]);

  useEffect(() => {
    setCalibrationChecks({
      entrance: false,
      exit: false,
      cashier: false,
      shelves: false,
    });
    disarmAlarm(
      "Camera source changed. Confirm zone coverage and repeat the sound check.",
    );
  }, [sourceKey]);

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
          {!modelChecked
            ? "Checking local model"
            : model?.ready
              ? "Local model available"
              : "Local model unavailable"}
        </span>
      </div>
      <p className="interaction-service">
        {model
          ? `${model.model} · ${model.message}`
          : "Checking the local interaction service…"}
      </p>
      <fieldset className="interaction-commission">
        <legend>Pharmacy camera calibration</legend>
        <p>
          Confirm that the current camera selection covers each operating zone.
          This is staff-declared metadata for alarm gating, not proof that the
          camera placement or detection model is accurate.
        </p>
        {(
          [
            ["entrance", "Entrance zone is visible"],
            ["exit", "Exit zone is visible"],
            ["cashier", "Cashier zone is visible"],
            ["shelves", "Relevant shelf zones are visible"],
          ] as const
        ).map(([key, label]) => (
          <label className="ld-inline-check" key={key}>
            <input
              type="checkbox"
              checked={calibrationChecks[key]}
              onChange={(event) => {
                disarmAlarm(
                  "Camera calibration changed. Repeat the sound check before arming.",
                );
                setCalibrationChecks((current) => ({
                  ...current,
                  [key]: event.target.checked,
                }));
              }}
            />
            {label}
          </label>
        ))}
        <p role="status">
          {calibrationReady
            ? "Calibration metadata complete for this camera selection. Alarm commissioning is available after the speaker check."
            : "Analysis and recorded demonstrations remain available. Automatic attention sound stays blocked until all four zones are confirmed."}
        </p>
      </fieldset>
      {!allMode && (
        <>
          <p className="interaction-readiness" role="status">
            {monitorGuidance}
          </p>
          <div className="interaction-controls">
            <label className="ld-inline-check">
              <input
                type="checkbox"
                checked={enabled}
                disabled={!model?.ready || !health.ready}
                onChange={(event) => {
                  const next = event.target.checked;
                  if (
                    next &&
                    (!options.current.modelReady || !runtimeHealth.canMonitor())
                  )
                    return;
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
                disabled={
                  !enabled ||
                  !model?.ready ||
                  !sourceReady ||
                  !cameraCalibration ||
                  commissionStage !== "confirmed" ||
                  (recordedSource && !recordedAlarmAllowed)
                }
                onChange={(event) => {
                  const next = event.target.checked;
                  if (!next) {
                    disarmAlarm();
                    return;
                  }
                  const armed = commission.current.arm(
                    contextKey(),
                    recordedAlarmAllowed,
                  );
                  options.current.alarmEnabled = armed;
                  setAlarmEnabled(armed);
                  setSoundStatus(
                    armed
                      ? recordedSource
                        ? "RECORDED TEST alarm armed for this run. Fresh test observations may request an eight-second tone."
                        : "Product attention alarm armed for this run. Fresh eligible observations may request an eight-second tone."
                      : "The source changed. Test the sound again before arming.",
                  );
                }}
              />
              Experimental product attention alarm
            </label>
          </div>
          <fieldset className="interaction-commission">
            <legend>Product alarm · staff sound check</legend>
            <p>
              Start detection, test the laptop speakers, then confirm that you
              heard the tone. This check applies to the current source, area,
              run and volume. Physical audibility is confirmed by staff; browser
              audio activation alone cannot verify it.
            </p>
            <div className="interaction-commission-actions">
              <button
                type="button"
                disabled={
                  !enabled ||
                  !cameraReady ||
                  !model?.ready ||
                  !sourceReady ||
                  muted ||
                  volume <= 0 ||
                  commissionStage === "testing"
                }
                onClick={() => void testSound()}
              >
                Test product alarm sound
              </button>
              <button
                type="button"
                disabled={commissionStage !== "confirm" || !sourceReady}
                onClick={confirmSound}
              >
                I heard the test tone
              </button>
              <button
                type="button"
                onClick={() =>
                  disarmAlarm("Sound check cancelled. Product alarm is off.")
                }
              >
                Stop sound & disarm product alarm
              </button>
            </div>
            {recordedSource && (
              <label className="ld-inline-check">
                <input
                  type="checkbox"
                  checked={recordedAlarmAllowed}
                  disabled={commissionStage !== "confirmed" || alarmEnabled}
                  onChange={(event) =>
                    setRecordedAlarmAllowed(event.target.checked)
                  }
                />
                Allow alarm during this recorded-video test
              </label>
            )}
            <p className="interaction-sound-status" role="status">
              {soundStatus}
            </p>
          </fieldset>
        </>
      )}
      <CameraLayoutPicker
        videoRef={videoRef}
        allCameras={allMode}
        onConfirmedLayout={onConfirmedLayout}
        sourceKey={sourceKey}
        customArea={cameraReady && cameraChoice.custom}
        onInvalidate={invalidateCameraLayout}
        onSelect={(area, tileLabel) => changeCrop(true, area, tileLabel)}
      />
      {!allMode && (
        <>
          <div className="interaction-crop">
            <h3>Custom area controls</h3>
            <label className="ld-inline-check">
              <input
                type="checkbox"
                checked={cropEnabled}
                onChange={(event) => changeCrop(event.target.checked, crop)}
              />
              Analyse this camera/aisle area
            </label>
            <p className="ld-hint">
              {cameraReady && cameraChoice.custom
                ? "Custom selected area. Percentages refer to the full source above; include one camera tile or shelf area with hands and products visible."
                : cameraReady
                  ? `${cameraChoice.label} is selected. Editing these percentages switches to a custom area.`
                  : "Choose a layout above, or explicitly enable and set a custom area here. No product frames are sampled until an area is selected."}
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
                        onChange={(event) =>
                          adjustCrop(key, event.target.value)
                        }
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
                !enabled ||
                !model?.ready ||
                !cameraReady ||
                !!cropError ||
                count < 4 ||
                busy
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
          <section
            className="interaction-sampling"
            aria-label="Current sampled frames"
          >
            <div className="interaction-sampling-heading">
              <h3>Current sampled frames</h3>
              <span>{automatic ? "Automatic monitoring" : "Manual test"}</span>
            </div>
            <p>
              {cameraReady
                ? `${cameraChoice.label}. `
                : "Choose a camera area to begin. "}
              {samples.length
                ? "These local previews update as fresh frames arrive. They are not a continuous recording."
                : "No fresh samples yet. Enable analysis, confirm a camera and start detection."}
            </p>
            <FrameStrip frames={samples} kind="Buffered" />
            <p className="interaction-schedule" role="status">
              {!automatic
                ? "Manual test: select Analyse recent sequence when four fresh frames are ready."
                : busy
                  ? "One sequence is being analysed. New samples continue locally; no other model job is queued."
                  : !enabled || !cameraReady || !sourceReady || !!cropError
                    ? "Automatic monitoring is waiting for enabled analysis, a confirmed camera and fresh playing video."
                    : nextSubmissionIn
                      ? `Next automatic submission in ${nextSubmissionIn}s, once four fresh frames are ready.`
                      : count < 4
                        ? "Automatic monitoring is collecting four fresh frames for its next submission."
                        : "Automatic monitoring is ready for its next submission slot."}
            </p>
            <p className="interaction-last-interval">
              {lastAnalysed
                ? `Last analysed interval: ${lastAnalysed.start.toFixed(2)}–${lastAnalysed.end.toFixed(2)}s of source video · ${lastAnalysed.action}. Only the sampled moments were analysed.`
                : "No completed analysis in this camera session yet."}
            </p>
          </section>
          {submittedWindow && (
            <section
              className="interaction-sampling interaction-submitted"
              aria-label="Submitted sequence"
            >
              <div className="interaction-sampling-heading">
                <h3>Submitted sequence</h3>
                <span>
                  {submittedWindow.state === "analysing"
                    ? "Analysing these four frames"
                    : submittedWindow.state === "completed"
                      ? "Analysis completed"
                      : "Analysis failed"}
                </span>
              </div>
              <p>
                {submittedWindow.label} ·{" "}
                {submittedWindow.frames[0].at_seconds.toFixed(2)}–
                {submittedWindow.frames.at(-1)!.at_seconds.toFixed(2)}s of
                source video. These exact submitted JPEGs stay fixed while the
                current samples above advance.
              </p>
              <FrameStrip frames={submittedWindow.frames} kind="Submitted" />
            </section>
          )}
          <p className="interaction-status" role="status">
            {status}
          </p>
          <p className="ld-hint">
            Four frames cover about four seconds. They are sent to the local
            service and saved with the result under the retention policy below;
            you can delete them here. No continuous clip is saved. Automatic
            analysis keeps one job in flight, with no queue; events between
            sampled frames or jobs can be missed. This model has not been
            validated for pharmacy theft detection.
          </p>
        </>
      )}
      {allMode && (
        <>
          <button disabled={refreshing} onClick={() => void refresh()}>
            Refresh model & history
          </button>
          <AllCameraAnalysis
            run={allRun}
            count={allCameraCount}
            videoRef={videoRef}
            readRunning={() => options.current.readSession().running}
            modelReady={model?.ready === true}
            cameraCalibration={cameraCalibration}
            muted={muted}
            volume={volume}
            cancelRef={allCancel}
            silenceRef={allSilence}
            onMonitorStatus={onMonitorStatus}
            onResult={(result) => {
              historyRevision.current++;
              setHistory((previous) =>
                [
                  result,
                  ...previous.filter((item) => item.id !== result.id),
                ].slice(0, 50),
              );
            }}
          />
        </>
      )}
      <div className="interaction-evidence-policy">
        <strong>Sampled evidence storage</strong>
        {model?.evidence_policy ? (
          <>
            <p>
              Maximum retention:{" "}
              {Math.round(model.evidence_policy.retention_seconds / 3600)}{" "}
              hours. Capacity: {model.evidence_policy.site_limit} jobs per
              branch, {model.evidence_policy.installation_limit} per
              installation.{" "}
              {model.evidence_policy.encryption === "AES-256-GCM"
                ? "Frames are encrypted locally with AES-256-GCM."
                : "Synthetic workspace: sampled frames are not encrypted. Use pilot mode for client evidence."}
            </p>
            <p>{model.evidence_policy.rolling_cleanup}</p>
          </>
        ) : (
          <p>
            Evidence policy could not be verified. Refresh the local service
            before collecting client footage. Earlier ordinary samples may roll
            off at capacity; this is not an archive.
          </p>
        )}
      </div>
      <p className="ld-hint">
        The product alarm is separate from the movement-rule alarm above. It
        requires a clear, product-visible concealment sequence and a result
        within 15 seconds of the last sample. It shares the laptop volume/mute
        setting. Delayed or historical results never sound.
      </p>
      {error && (
        <p className="ld-error" role="alert">
          {error}
        </p>
      )}
      {highlight && (
        <div className="interaction-alert" role="alert" aria-atomic="true">
          <BellRing size={23} />
          <div>
            <strong>Possible product concealment — review required</strong>
            <p>
              {history.find((item) => item.id === highlight)?.source_kind ===
              "RECORDED_VIDEO"
                ? "Recorded-video test. "
                : ""}
              Check the sampled frames before responding.
            </p>
          </div>
          <button type="button" onClick={silence}>
            <VolumeX size={16} />
            Silence product alarm
          </button>
          <button
            type="button"
            onClick={() => {
              silence();
              attention.current.acknowledge(contextKey(), performance.now());
              setHighlight(null);
              setStatus(
                "Attention acknowledged. Review the sampled frames and record Useful, Normal shopping or Unclear below.",
              );
            }}
          >
            <Check size={16} /> Acknowledge attention
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
          <CameraContextDetails context={item.camera_context} />
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
                {item.camera_context &&
                  ` · Camera ${item.camera_context.camera_index + 1} · ${item.camera_context.layout}`}
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
              Camera calibration:{" "}
              {item.camera_calibration
                ? "operator-declared complete"
                : "missing"}
            </span>
            {item.alarm_blocked_reason === "CAMERA_CALIBRATION_REQUIRED" && (
              <span>
                Automatic alarm blocked: camera zone calibration required
              </span>
            )}
            {item.evidence_strength && (
              <span title={item.evidence_strength_note}>
                Evidence rule strength:{" "}
                {item.evidence_strength.replaceAll("_", " ").toLowerCase()}
                {" · not a probability"}
              </span>
            )}
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
            {item.incident_id ? (
              <button
                type="button"
                disabled={mutation !== null || !onOpenInteractionCase}
                onClick={() => {
                  cancel();
                  void onOpenInteractionCase?.(item.incident_id!);
                }}
              >
                <ClipboardList size={14} /> Open linked case
              </button>
            ) : (
              <button
                type="button"
                disabled={
                  mutation !== null ||
                  !item.review ||
                  Date.parse(item.expires_at) <= Date.now()
                }
                onClick={() => {
                  clearCaseRequest();
                  setCaseDraft({
                    id: item.id,
                    version: item.version,
                    title:
                      `Review follow-up: ${interactionLabels[item.action]}`.slice(
                        0,
                        120,
                      ),
                    notes: "",
                  });
                  setError("");
                }}
              >
                <ClipboardList size={14} /> Create case from reviewed
                observation
              </button>
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
          {!item.review && (
            <p className="ld-hint">
              Review the sampled frames and choose Useful, Normal shopping or
              Unclear before creating a case.
            </p>
          )}
          {item.incident_id && (
            <p className="ld-hint">
              The linked case retains its reviewed notes and source metadata.
              Deleting this result removes the sampled images; it does not
              delete the case.
            </p>
          )}
          {caseDraft?.id === item.id && (
            <form
              className="interaction-case-form"
              aria-label="Create case from observation"
              onSubmit={(event) => {
                event.preventDefault();
                void createCase();
              }}
            >
              <h4>Create a staff-reviewed case</h4>
              <p>
                The case starts unassessed. Write your own reviewed facts; the
                model observation is retained separately as unverified source
                context. Sampled images retain their original expiry and
                deletion policy.
              </p>
              <label>
                Case title
                <input
                  required
                  minLength={3}
                  maxLength={120}
                  value={caseDraft.title}
                  disabled={mutation !== null}
                  onChange={(event) =>
                    setCaseDraft({ ...caseDraft, title: event.target.value })
                  }
                />
              </label>
              <label>
                Staff reviewed notes
                <textarea
                  required
                  minLength={5}
                  maxLength={4000}
                  rows={4}
                  value={caseDraft.notes}
                  disabled={mutation !== null}
                  onChange={(event) =>
                    setCaseDraft({ ...caseDraft, notes: event.target.value })
                  }
                />
              </label>
              <p>
                Do not enter patient information or identifying allegations.
                This action does not confirm theft or a financial loss.
              </p>
              <div className="interaction-review">
                <button type="submit" disabled={mutation !== null}>
                  Create reviewed case
                </button>
                <button
                  type="button"
                  disabled={mutation !== null}
                  onClick={() => {
                    clearCaseRequest();
                    setCaseDraft(null);
                  }}
                >
                  Cancel case creation
                </button>
              </div>
            </form>
          )}
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
