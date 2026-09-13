import { useEffect, useRef, useState } from "react";
import {
  BellRing,
  Camera,
  Check,
  CircleAlert,
  Film,
  MonitorUp,
  Play,
  RefreshCw,
  Square,
  Volume2,
  VolumeX,
} from "lucide-react";
import { api } from "./api";
import InteractionAnalysis from "./interactionAnalysis";
import type {
  InteractionMonitorStatus,
  SelectedCamera,
} from "./interactionAnalysis";
import {
  poseFrameRegion,
  poseZoneInCamera,
  projectPoseTracks,
} from "./poseFrame";
import { BrowserAttentionSound } from "./playbackAlerts";
import { createPoseDetector } from "./poseDetector";
import { watchVideoContinuity } from "./monitoringContinuity";
import { LiveBehaviourEngine, POSE_CONNECTIONS } from "./liveBehaviour";
import { validateVideoFile, validateVideoMetadata } from "./videoActivity";
import { LIVE_MODEL_VERSION, LIVE_RULE_VERSION } from "./liveDetectionTypes";
import type {
  DetectionRect,
  LiveBehaviourEvent,
  LiveDetectionSettings,
  LiveEventInput,
  LiveSourceKind,
  LiveTrack,
  PoseDetector,
  SavedLiveEvent,
} from "./liveDetectionTypes";
import "./liveDetection.css";

type Source = {
  kind: LiveSourceKind;
  label: string;
  url?: string;
  stream?: MediaStream;
  ready: boolean;
};
type PendingEvent = {
  input: LiveEventInput;
  label: string;
  detail: string;
  saving: boolean;
  error: string;
};
type Phase =
  | "empty"
  | "preparing"
  | "ready"
  | "loading"
  | "running"
  | "degraded"
  | "error";
const intervalMs = 250;
const maxResultAgeMs = 1000;
const sourceNames: Record<LiveSourceKind, string> = {
  SCREEN_CAPTURE: "Live CCTV screen",
  CAMERA: "Live camera",
  RECORDED_VIDEO: "Recorded CCTV",
};
function time(seconds: number) {
  return `${Math.floor(seconds / 60)}:${String(Math.floor(seconds % 60)).padStart(2, "0")}`;
}
function message(error: unknown) {
  return error instanceof Error
    ? error.message
    : "The operation could not complete. Please try again.";
}
function safeLabel(label: string) {
  return (
    label
      .replace(/[<>\p{C}]/gu, " ")
      .slice(0, 120)
      .trim() || "CCTV source"
  );
}

/** Only real frame observations enter the engine. Source/model lifetimes are explicit. */
export default function LiveDetection({ branchName }: { branchName: string }) {
  const [phase, setPhase] = useState<Phase>("empty");
  const [productStatus, setProductStatus] = useState<InteractionMonitorStatus>({
    state: "checking",
    label: "PRODUCT ANALYSIS CHECKING",
    guidance: "Checking the separate local product model.",
  });
  const [source, setSource] = useState<Source | null>(null);
  const [selectedCamera, setSelectedCamera] = useState<SelectedCamera | null>(
    null,
  );
  const selectedCameraRef = useRef<SelectedCamera | null>(null);
  const [status, setStatus] = useState(
    "Connect a CCTV view or choose a recording to begin.",
  );
  const [error, setError] = useState("");
  const [tracks, setTracks] = useState<LiveTrack[]>([]);
  const [metrics, setMetrics] = useState({ fps: 0, latency: 0, frames: 0 });
  const [dimensions, setDimensions] = useState({ width: 16, height: 9 });
  const [skeleton, setSkeleton] = useState(true);
  const [sensitivity, setSensitivity] =
    useState<LiveDetectionSettings["sensitivity"]>("balanced");
  const [zoneEnabled, setZoneEnabled] = useState(false);
  const [zone, setZone] = useState<DetectionRect>({
    x: 0.65,
    y: 0.1,
    width: 0.3,
    height: 0.8,
  });
  const [devices, setDevices] = useState<MediaDeviceInfo[]>([]);
  const [deviceId, setDeviceId] = useState("");
  const [volume, setVolume] = useState(0.65);
  const [muted, setMuted] = useState(false);
  const [movementAlarmEnabled, setMovementAlarmEnabled] = useState(false);
  const [soundStatus, setSoundStatus] = useState(
    "Test this laptop’s speakers before monitoring.",
  );
  const [activeAlert, setActiveAlert] = useState<{
    eventId: string;
    label: string;
    detail: string;
  } | null>(null);
  const [events, setEvents] = useState<SavedLiveEvent[]>([]);
  const [pending, setPending] = useState<PendingEvent[]>([]);
  const [logError, setLogError] = useState("");
  const [refreshing, setRefreshing] = useState(false);
  const [acknowledging, setAcknowledging] = useState<string | null>(null);
  const videoRef = useRef<HTMLVideoElement>(null);
  const interactionCancel = useRef<(() => void) | null>(null);
  const interactionSilence = useRef<(() => void) | null>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  const sourceRef = useRef<Source | null>(null);
  const detectorRef = useRef<PoseDetector | null>(null);
  const modelLoad = useRef<AbortController | null>(null);
  const loadingRef = useRef(false);
  const engineRef = useRef<LiveBehaviourEngine | null>(null);
  const soundRef = useRef<BrowserAttentionSound | null>(null);
  const sourceGeneration = useRef(0);
  const runGeneration = useRef(0);
  const runningRef = useRef(false);
  const mounted = useRef(true);
  const tickTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const continuityRef = useRef<ReturnType<typeof watchVideoContinuity> | null>(
    null,
  );
  const preparationTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const soundTimers = useRef<ReturnType<typeof setTimeout>[]>([]);
  const alarmEvent = useRef<string | null>(null);
  const lastAlarmStarted = useRef(-Infinity);
  const soundOptions = useRef({ muted, volume, movementAlarmEnabled });
  const saveInFlight = useRef(new Set<string>());
  const unsavedCount = useRef(0);
  const runId = useRef("");
  const busy =
    phase === "loading" || phase === "running" || phase === "degraded";
  soundOptions.current = { muted, volume, movementAlarmEnabled };

  function sound() {
    return (soundRef.current ??= new BrowserAttentionSound());
  }
  function silence(text = "Alarm silenced. Review the highlighted event.") {
    soundTimers.current.forEach(clearTimeout);
    soundTimers.current = [];
    soundRef.current?.stop();
    if (mounted.current) setSoundStatus(text);
  }
  function clearOverlay() {
    const canvas = canvasRef.current;
    canvas?.getContext("2d")?.clearRect(0, 0, canvas.width, canvas.height);
    if (mounted.current) setTracks([]);
  }
  function stop(reason = "Detection stopped.", releaseCapture = true) {
    interactionCancel.current?.();
    interactionSilence.current?.();
    continuityRef.current?.close();
    continuityRef.current = null;
    sourceGeneration.current++;
    runningRef.current = false;
    loadingRef.current = false;
    runGeneration.current++;
    modelLoad.current?.abort();
    modelLoad.current = null;
    if (tickTimer.current !== null) clearTimeout(tickTimer.current);
    tickTimer.current = null;
    if (preparationTimer.current !== null)
      clearTimeout(preparationTimer.current);
    preparationTimer.current = null;
    detectorRef.current?.close();
    detectorRef.current = null;
    engineRef.current?.reset();
    engineRef.current = null;
    silence("Alarm stopped. Detection is not running.");
    soundRef.current?.disarm();
    alarmEvent.current = null;
    clearOverlay();
    const current = sourceRef.current;
    const video = videoRef.current;
    if (video && !video.paused) video.pause();
    if (releaseCapture && current?.stream) {
      sourceGeneration.current++;
      current.stream.getTracks().forEach((track) => {
        track.onended = null;
        track.onmute = null;
        track.stop();
      });
      if (video) video.srcObject = null;
      sourceRef.current = null;
      if (mounted.current) setSource(null);
    }
    if (mounted.current) {
      setMetrics((previous) => ({ ...previous, fps: 0, latency: 0 }));
      setStatus(reason);
      setPhase(sourceRef.current?.ready ? "ready" : "empty");
    }
  }
  function releaseSource() {
    sourceGeneration.current++;
    stop("Source disconnected.");
    if (sourceRef.current?.url) URL.revokeObjectURL(sourceRef.current.url);
    sourceRef.current = null;
    const video = videoRef.current;
    if (video) {
      video.srcObject = null;
      video.removeAttribute("src");
      video.load();
    }
    setSource(null);
    setMetrics({ fps: 0, latency: 0, frames: 0 });
    setActiveAlert(null);
    setError("");
  }

  function chooseTrackingCamera(selection: SelectedCamera | null) {
    const current = sourceRef.current;
    const key = current?.url ?? current?.stream?.id ?? "";
    const next = selection?.sourceKey === key ? selection : null;
    if (JSON.stringify(next) === JSON.stringify(selectedCameraRef.current))
      return;
    // Recreate the pose worker on restart: VIDEO tracking state cannot cross cameras.
    if (runningRef.current || loadingRef.current)
      stop(
        "Camera area changed. Start detection again to track this camera with fresh model state.",
        false,
      );
    selectedCameraRef.current = next;
    setSelectedCamera(next);
    clearOverlay();
    setActiveAlert(null);
    setMovementAlarmEnabled(false);
  }

  function showCameraSetup() {
    document
      .getElementById("camera-layout-heading")
      ?.scrollIntoView({ block: "start", behavior: "smooth" });
  }

  async function refreshEvents() {
    setRefreshing(true);
    try {
      const loaded = await api<SavedLiveEvent[]>("/live-events");
      if (mounted.current) {
        setEvents(loaded);
        setLogError("");
      }
    } catch (failure) {
      if (mounted.current) setLogError(message(failure));
    } finally {
      if (mounted.current) setRefreshing(false);
    }
  }
  async function saveEvent(item: PendingEvent) {
    if (saveInFlight.current.has(item.input.event_id)) return;
    saveInFlight.current.add(item.input.event_id);
    setPending((previous) =>
      previous.map((row) =>
        row.input.event_id === item.input.event_id
          ? { ...row, saving: true, error: "" }
          : row,
      ),
    );
    try {
      const saved = await api<SavedLiveEvent>(
        "/live-events",
        "POST",
        item.input,
      );
      if (mounted.current) {
        unsavedCount.current = Math.max(0, unsavedCount.current - 1);
        setEvents((previous) =>
          [
            saved,
            ...previous.filter((row) => row.event_id !== saved.event_id),
          ].slice(0, 100),
        );
        setPending((previous) =>
          previous.filter((row) => row.input.event_id !== item.input.event_id),
        );
      }
    } catch (failure) {
      if (mounted.current)
        setPending((previous) =>
          previous.map((row) =>
            row.input.event_id === item.input.event_id
              ? { ...row, saving: false, error: message(failure) }
              : row,
          ),
        );
    } finally {
      saveInFlight.current.delete(item.input.event_id);
    }
  }
  function trigger(event: LiveBehaviourEvent, current: Source, offset: number) {
    if (!runningRef.current || !continuityRef.current?.isFresh(maxResultAgeMs))
      return;
    if (unsavedCount.current >= 100) {
      stop("Detection stopped: 100 event records are waiting to be saved.");
      setError(
        "Reconnect to the local service and retry the unsaved events below before continuing.",
      );
      return;
    }
    const eventId = crypto.randomUUID();
    const wantsSound =
      soundOptions.current.movementAlarmEnabled &&
      !soundOptions.current.muted &&
      soundOptions.current.volume > 0;
    let soundRequested = false;
    if (!alarmEvent.current) {
      alarmEvent.current = eventId;
      setActiveAlert({ eventId, label: event.label, detail: event.detail });
      if (
        wantsSound &&
        performance.now() - lastAlarmStarted.current >= 30_000
      ) {
        lastAlarmStarted.current = performance.now();
        const episode = runGeneration.current;
        const playBurst = () => {
          if (
            !runningRef.current ||
            !continuityRef.current?.isFresh(maxResultAgeMs) ||
            episode !== runGeneration.current ||
            alarmEvent.current !== eventId ||
            document.hidden ||
            soundOptions.current.muted ||
            !soundOptions.current.movementAlarmEnabled
          )
            return;
          soundRequested = true;
          const result = sound().play({
            volume: soundOptions.current.volume,
            durationSeconds: 8,
          });
          setSoundStatus(
            result.ok
              ? "Attention alarm requested · up to 3 bursts. Check the video."
              : result.message,
          );
        };
        playBurst();
        soundTimers.current = [
          setTimeout(playBurst, 10_000),
          setTimeout(playBurst, 20_000),
          setTimeout(() => {
            if (alarmEvent.current === eventId && mounted.current)
              silence("Alarm time limit reached. Event still needs review.");
          }, 28_200),
        ];
      } else
        setSoundStatus(
          wantsSound
            ? "Alarm cooldown active. This new event is highlighted and logged."
            : "Movement sound is off or muted. Visual alert and automatic logging remain active.",
        );
    }
    const input: LiveEventInput = {
      run_id: runId.current,
      event_id: eventId,
      source_kind: current.kind,
      source_label: current.label,
      event_code: event.code,
      track_id: event.trackId,
      source_time_seconds: Math.round(offset * 1000) / 1000,
      detected_at: new Date().toISOString(),
      model_version: LIVE_MODEL_VERSION,
      rule_version: LIVE_RULE_VERSION,
      sound_requested: soundRequested,
    };
    const item: PendingEvent = {
      input,
      label: event.label,
      detail: event.detail,
      saving: true,
      error: "",
    };
    unsavedCount.current++;
    setPending((previous) => [item, ...previous]);
    void saveEvent(item);
  }
  async function acknowledge(event: SavedLiveEvent) {
    setAcknowledging(event.id);
    if (alarmEvent.current === event.event_id) silence();
    try {
      const updated = await api<SavedLiveEvent>(
        `/live-events/${event.id}/acknowledge`,
        "POST",
        {},
      );
      if (!mounted.current) return;
      setEvents((previous) =>
        previous.map((row) => (row.id === event.id ? updated : row)),
      );
      if (
        alarmEvent.current === event.event_id ||
        activeAlert?.eventId === event.event_id
      ) {
        alarmEvent.current = null;
        setActiveAlert(null);
        setSoundStatus(
          "Event acknowledged. New observations can raise another alert.",
        );
      }
      setLogError("");
    } catch (failure) {
      if (mounted.current) setLogError(message(failure));
    } finally {
      if (mounted.current) setAcknowledging(null);
    }
  }

  useEffect(() => {
    mounted.current = true;
    void refreshEvents();
    const visibility = () => {
      if (document.hidden)
        stop(
          "Detection stopped because this tab became hidden. Return and start again.",
        );
    };
    const pageHide = () => stop("Detection stopped because the page closed.");
    document.addEventListener("visibilitychange", visibility);
    window.addEventListener("pagehide", pageHide);
    return () => {
      mounted.current = false;
      sourceGeneration.current++;
      stop();
      if (sourceRef.current?.url) URL.revokeObjectURL(sourceRef.current.url);
      sourceRef.current = null;
      soundRef.current?.dispose();
      soundRef.current = null;
      document.removeEventListener("visibilitychange", visibility);
      window.removeEventListener("pagehide", pageHide);
    };
    // Each mounted branch owns independent source, model and audio lifetimes.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    canvas.width = Math.min(dimensions.width, 1280);
    canvas.height = Math.round(
      (canvas.width * dimensions.height) / dimensions.width,
    );
    const context = canvas.getContext("2d");
    if (!context) return;
    const w = canvas.width,
      h = canvas.height;
    context.clearRect(0, 0, w, h);
    const camera =
      selectedCamera?.sourceKey === (source?.url ?? source?.stream?.id ?? "")
        ? selectedCamera
        : null;
    if (camera && (camera.crop.width < 1 || camera.crop.height < 1)) {
      const area = camera.crop;
      context.fillStyle = "rgba(0,0,0,.38)";
      context.fillRect(0, 0, w, area.y * h);
      context.fillRect(
        0,
        (area.y + area.height) * h,
        w,
        (1 - area.y - area.height) * h,
      );
      context.fillRect(0, area.y * h, area.x * w, area.height * h);
      context.fillRect(
        (area.x + area.width) * w,
        area.y * h,
        (1 - area.x - area.width) * w,
        area.height * h,
      );
      context.strokeStyle = "#fff3a7";
      context.lineWidth = 2;
      context.strokeRect(
        area.x * w,
        area.y * h,
        area.width * w,
        area.height * h,
      );
    }
    if (zoneEnabled) {
      context.fillStyle = "rgba(255,190,80,.12)";
      context.strokeStyle = "#ffbf61";
      context.lineWidth = 2;
      context.setLineDash([8, 5]);
      context.fillRect(zone.x * w, zone.y * h, zone.width * w, zone.height * h);
      context.strokeRect(
        zone.x * w,
        zone.y * h,
        zone.width * w,
        zone.height * h,
      );
      context.setLineDash([]);
      context.font = `${Math.max(13, w / 75)}px sans-serif`;
      context.fillStyle = "#ffdb9d";
      context.fillText("Restricted zone", zone.x * w + 5, zone.y * h + 20);
    }
    for (const track of tracks) {
      const color =
        track.status === "alert"
          ? "#ff7373"
          : track.status === "watch"
            ? "#ffd27a"
            : "#6cedbc";
      const b = track.box;
      context.strokeStyle = color;
      context.lineWidth = Math.max(2, w / 400);
      context.strokeRect(b.x * w, b.y * h, b.width * w, b.height * h);
      context.font = `600 ${Math.max(13, w / 68)}px sans-serif`;
      const label = `#${track.id}  ${track.label}`;
      const textWidth = context.measureText(label).width;
      const textX = Math.max(0, Math.min(b.x * w, w - textWidth - 12));
      const textY = Math.max(0, b.y * h - 27);
      context.fillStyle = color;
      context.fillRect(textX, textY, textWidth + 12, 27);
      context.fillStyle = "#10211d";
      context.fillText(label, textX + 6, textY + 19);
      if (skeleton) {
        context.strokeStyle = color;
        context.lineWidth = Math.max(1.5, w / 500);
        for (const [a, b] of POSE_CONNECTIONS) {
          const start = track.landmarks[a],
            end = track.landmarks[b];
          if (
            !start ||
            !end ||
            (start.visibility ?? 0) < 0.5 ||
            (end.visibility ?? 0) < 0.5
          )
            continue;
          context.beginPath();
          context.moveTo(start.x * w, start.y * h);
          context.lineTo(end.x * w, end.y * h);
          context.stroke();
        }
        context.fillStyle = "#d8fff1";
        track.landmarks.forEach((point) => {
          if ((point.visibility ?? 0) < 0.5) return;
          context.beginPath();
          context.arc(
            point.x * w,
            point.y * h,
            Math.max(2, w / 400),
            0,
            Math.PI * 2,
          );
          context.fill();
        });
      }
    }
  }, [tracks, skeleton, dimensions, zone, zoneEnabled, selectedCamera, source]);

  function chooseFile(file: File | undefined) {
    if (!file) return;
    const invalid = validateVideoFile(file);
    if (invalid) {
      setError(invalid);
      return;
    }
    releaseSource();
    const next: Source = {
      kind: "RECORDED_VIDEO",
      label: safeLabel(file.name),
      url: URL.createObjectURL(file),
      ready: false,
    };
    sourceRef.current = next;
    setSource(next);
    setPhase("preparing");
    setStatus("Reading this recording on your laptop…");
    const video = videoRef.current;
    if (video) {
      video.src = next.url!;
      video.load();
    }
    preparationTimer.current = setTimeout(() => {
      if (sourceRef.current === next && !next.ready) {
        releaseSource();
        setPhase("error");
        setError(
          "This recording could not load in time. Try a shorter MP4 (H.264) or WebM file.",
        );
      }
    }, 8000);
  }
  async function connect(kind: "SCREEN_CAPTURE" | "CAMERA") {
    releaseSource();
    const generation = sourceGeneration.current;
    setPhase("preparing");
    setStatus(
      kind === "SCREEN_CAPTURE"
        ? "Choose the CCTV viewer window in the browser’s sharing dialog."
        : "Choose an existing video input and allow camera access.",
    );
    try {
      if (!navigator.mediaDevices)
        throw new Error(
          "Camera and screen access need a supported browser on localhost or HTTPS.",
        );
      if (kind === "SCREEN_CAPTURE" && !navigator.mediaDevices.getDisplayMedia)
        throw new Error(
          "Screen sharing is unavailable in this browser. Open AisleSignals in Chrome or Edge, or load a recording.",
        );
      const stream =
        kind === "SCREEN_CAPTURE"
          ? await navigator.mediaDevices.getDisplayMedia({
              video: {
                frameRate: { ideal: 15, max: 30 },
                width: { ideal: 1280 },
              },
              audio: false,
            })
          : await navigator.mediaDevices.getUserMedia({
              video: {
                ...(deviceId ? { deviceId: { exact: deviceId } } : {}),
                width: { ideal: 1280 },
                height: { ideal: 720 },
                frameRate: { ideal: 15, max: 30 },
              },
              audio: false,
            });
      if (!mounted.current || generation !== sourceGeneration.current) {
        stream.getTracks().forEach((track) => track.stop());
        return;
      }
      const track = stream.getVideoTracks()[0];
      if (!track) {
        stream.getTracks().forEach((item) => item.stop());
        throw new Error("The selected source has no readable video track.");
      }
      const next: Source = {
        kind,
        label: safeLabel(track.label || sourceNames[kind]),
        stream,
        ready: false,
      };
      sourceRef.current = next;
      setSource(next);
      preparationTimer.current = setTimeout(() => {
        if (sourceRef.current === next && !next.ready) {
          stop("The selected video input did not deliver a readable frame.");
          setError(
            "Reconnect the CCTV viewer or select a different camera input.",
          );
        }
      }, 8000);
      track.onended = () => {
        if (sourceRef.current !== next) return;
        stop(
          "The video source disconnected or sharing ended. Reconnect to continue.",
        );
        setError("No live frames are being analysed.");
      };
      track.onmute = () => {
        if (sourceRef.current !== next) return;
        stop(
          "The video input stopped delivering frames. Reconnect the source to continue.",
        );
        setError(
          "Detection stopped: the camera or shared window is unavailable.",
        );
      };
      if (track.readyState !== "live" || track.muted) {
        stop("The selected video input is unavailable. Reconnect to continue.");
        setError("No live frames are being analysed.");
        return;
      }
      const video = videoRef.current;
      if (video) {
        video.srcObject = stream;
        await video.play();
      }
      if (kind === "CAMERA") {
        const available = await navigator.mediaDevices.enumerateDevices();
        if (mounted.current && generation === sourceGeneration.current)
          setDevices(available.filter((item) => item.kind === "videoinput"));
      }
    } catch (failure) {
      if (!mounted.current || generation !== sourceGeneration.current) return;
      stop("Source not connected.");
      setError(
        failure instanceof DOMException && failure.name === "NotAllowedError"
          ? "Access was cancelled or blocked. Choose the CCTV window again and allow screen or camera access in your browser and system settings."
          : message(failure),
      );
      setPhase("error");
    }
  }
  function mediaReady() {
    const video = videoRef.current,
      current = sourceRef.current;
    if (!video || !current || video.readyState < 2) return;
    if (current.kind === "RECORDED_VIDEO") {
      const invalid = validateVideoMetadata({
        duration: video.duration,
        width: video.videoWidth,
        height: video.videoHeight,
      });
      if (invalid) {
        releaseSource();
        setError(invalid);
        setPhase("error");
        return;
      }
    }
    if (!video.videoWidth || !video.videoHeight) return;
    if (preparationTimer.current !== null)
      clearTimeout(preparationTimer.current);
    preparationTimer.current = null;
    current.ready = true;
    setSource({ ...current });
    setDimensions({ width: video.videoWidth, height: video.videoHeight });
    if (!runningRef.current && !loadingRef.current) {
      setPhase("ready");
      setStatus(
        `${sourceNames[current.kind]} ready. Start detection to analyse people and their movements.`,
      );
    }
  }
  async function start() {
    const video = videoRef.current,
      current = sourceRef.current;
    if (
      !video ||
      !current?.ready ||
      runningRef.current ||
      loadingRef.current ||
      document.hidden
    )
      return;
    const soundActivation = sound().arm(); // Keep AudioContext.resume directly in this user gesture.
    const generation = ++runGeneration.current;
    loadingRef.current = true;
    const controller = new AbortController();
    modelLoad.current = controller;
    setPhase("loading");
    setError("");
    setStatus("Loading the local pose model…");
    setMetrics({ fps: 0, latency: 0, frames: 0 });
    setActiveAlert(null);
    alarmEvent.current = null;
    runId.current = crypto.randomUUID();
    let detector: PoseDetector | null = null;
    try {
      const sourceKey = current.url ?? current.stream?.id ?? "";
      const camera =
        selectedCameraRef.current?.sourceKey === sourceKey
          ? selectedCameraRef.current
          : null;
      const crop = camera?.crop ?? null;
      const region = poseFrameRegion(video.videoWidth, video.videoHeight, crop);
      const trackedSource = camera
        ? {
            ...current,
            label: `${current.label.slice(0, 70)} · ${camera.label}`.slice(
              0,
              120,
            ),
          }
        : current;
      const audioResult = await soundActivation;
      if (!mounted.current || generation !== runGeneration.current) return;
      setSoundStatus(
        muted
          ? "Sound muted. Visual alerts and logging are enabled."
          : audioResult.message,
      );
      detector = await createPoseDetector(controller.signal);
      if (!mounted.current || generation !== runGeneration.current) {
        detector.close();
        return;
      }
      detectorRef.current = detector;
      modelLoad.current = null;
      engineRef.current = new LiveBehaviourEngine({
        sensitivity,
        restrictedZone: poseZoneInCamera(
          zoneEnabled ? zone : null,
          region.area,
        ),
      });
      if (current.kind === "RECORDED_VIDEO" && video.ended)
        video.currentTime = 0;
      video.playbackRate = 1;
      const continuity = watchVideoContinuity(video, (problem) => {
        if (!mounted.current || generation !== runGeneration.current) return;
        stop(
          problem === "clock_gap"
            ? "Monitoring stopped after a laptop sleep or processing gap. Check the source and start again."
            : problem === "media_jump"
              ? "Video continuity changed. Check the source and start detection again."
              : "Video frames stopped arriving. Reconnect or restart playback.",
        );
        setError(
          "Detection is stopped. No frames or alarms are being processed.",
        );
      });
      continuityRef.current = continuity;
      setStatus("Checking fresh video frames before monitoring…");
      let playbackTimeout: ReturnType<typeof setTimeout> | undefined;
      try {
        await Promise.race([
          Promise.all([video.play(), continuity.firstFrame]),
          new Promise<never>((_, reject) => {
            playbackTimeout = setTimeout(
              () =>
                reject(
                  new Error(
                    "Video playback did not start. Check the source and try again.",
                  ),
                ),
              8000,
            );
          }),
        ]);
      } finally {
        clearTimeout(playbackTimeout);
      }
      if (!mounted.current || generation !== runGeneration.current) return;
      loadingRef.current = false;
      runningRef.current = true;
      setPhase("running");
      setStatus("Analysing frames. Waiting for a clear body pose…");
      let lastSequence = -1,
        lastSample = performance.now(),
        frames = 0,
        slowFrames = 0;
      const tick = async () => {
        if (
          !runningRef.current ||
          generation !== runGeneration.current ||
          document.hidden
        )
          return;
        const now = performance.now();
        if (video.paused || video.seeking || video.playbackRate !== 1) {
          stop(
            "Playback paused or changed. Start detection again to continue.",
            false,
          );
          return;
        }
        const presented = continuity.latest();
        if (
          video.readyState < 2 ||
          !presented ||
          presented.sequence === lastSequence ||
          !continuity.isFresh(maxResultAgeMs)
        ) {
          tickTimer.current = setTimeout(() => void tick(), intervalMs);
          return;
        }
        const mediaTime = presented.mediaTime;
        lastSequence = presented.sequence;
        try {
          const poses = await detector!.detect(video, now, crop);
          if (
            !runningRef.current ||
            generation !== runGeneration.current ||
            document.hidden ||
            video.paused ||
            video.seeking
          )
            return;
          if (!continuity.isFresh(maxResultAgeMs)) {
            stop(
              "Fresh video continuity was lost while processing. Check the source and start again.",
            );
            setError("Detection stopped: delayed results were discarded.");
            return;
          }
          const age = performance.now() - presented.observedAt;
          if (age > maxResultAgeMs) {
            setPhase("degraded");
            engineRef.current?.reset();
            clearOverlay();
            silence(
              "Inference is delayed. Stale results cannot trigger an alarm.",
            );
            setStatus(
              "Processing is too slow for fresh detections. Try a smaller CCTV view.",
            );
            slowFrames++;
            if (slowFrames >= 3) {
              stop(
                "Detection stopped: this view exceeds the laptop’s current processing capacity.",
              );
              setError(
                "Try a single CCTV camera view at a smaller size, then start again.",
              );
              return;
            }
          } else {
            setPhase("running");
            slowFrames = 0;
            const result = engineRef.current!.update(
              poses,
              mediaTime * 1000,
              region.width / region.height,
            );
            setTracks(projectPoseTracks(result.tracks, region.area));
            frames++;
            setMetrics({
              fps: Math.min(4, 1000 / Math.max(intervalMs, now - lastSample)),
              latency: age,
              frames,
            });
            setStatus(
              result.tracks.length
                ? `${result.tracks.length} usable body track${result.tracks.length === 1 ? "" : "s"}. This is not a count of everyone visible.`
                : "No clear body pose. People may still be present; use a closer camera area with a visible torso and arms.",
            );
            for (const event of result.events)
              trigger(event, trackedSource, mediaTime);
          }
          lastSample = now;
        } catch (failure) {
          if (!mounted.current || generation !== runGeneration.current) return;
          stop(
            "Detection stopped because the model could not process a frame.",
          );
          setError(message(failure));
          return;
        }
        if (runningRef.current && generation === runGeneration.current)
          tickTimer.current = setTimeout(
            () => void tick(),
            Math.max(0, intervalMs - (performance.now() - now)),
          );
      };
      void tick();
    } catch (failure) {
      if (!mounted.current || generation !== runGeneration.current) {
        detector?.close();
        return;
      }
      stop("Detection could not start.");
      setError(message(failure));
      setPhase("error");
    }
  }
  async function testSound() {
    const activation = sound().arm();
    const generation = runGeneration.current;
    const result = await activation;
    if (
      !mounted.current ||
      generation !== runGeneration.current ||
      document.hidden
    )
      return;
    if (!result.ok) {
      setSoundStatus(result.message);
      return;
    }
    setMuted(false);
    const test = sound().play({ volume: volume || 0.65, durationSeconds: 3 });
    setSoundStatus(
      test.ok
        ? "Three-second test requested. Confirm that you can hear the laptop speakers."
        : test.message,
    );
  }
  function adjustZone(key: keyof DetectionRect, value: string) {
    const percent = Number(value);
    if (!Number.isFinite(percent)) return;
    setZone((previous) => {
      const next = {
        ...previous,
        [key]: Math.min(
          1,
          Math.max(
            key === "width" || key === "height" ? 0.05 : 0,
            percent / 100,
          ),
        ),
      };
      next.x = Math.min(next.x, 0.95);
      next.y = Math.min(next.y, 0.95);
      next.width = Math.min(next.width, 1 - next.x);
      next.height = Math.min(next.height, 1 - next.y);
      return next;
    });
  }

  return (
    <div className="live-detection">
      <div className="ld-intro">
        <div>
          <p className="ld-eyebrow">{branchName} · On this laptop</p>
          <h1>Live Detection</h1>
          <p>
            Connect CCTV. See movement patterns. Respond to an attention alarm.
          </p>
        </div>
        <span className="ld-local-badge">Local pose AI · Experimental</span>
      </div>
      <section className="ld-source-panel" aria-labelledby="ld-source-heading">
        <div>
          <h2 id="ld-source-heading">1. Choose your CCTV input</h2>
          <p>Share a single camera view for the clearest body tracking.</p>
        </div>
        <div className="ld-source-actions">
          <button
            type="button"
            disabled={busy || phase === "preparing"}
            onClick={() => void connect("SCREEN_CAPTURE")}
          >
            <MonitorUp size={19} /> Share CCTV screen
          </button>
          <button
            type="button"
            disabled={busy || phase === "preparing"}
            onClick={() => void connect("CAMERA")}
          >
            <Camera size={19} /> Connect camera
          </button>
          <button
            type="button"
            disabled={busy || phase === "preparing"}
            onClick={() => inputRef.current?.click()}
          >
            <Film size={19} /> Load CCTV video
          </button>
          <input
            ref={inputRef}
            type="file"
            className="ld-file-input"
            tabIndex={-1}
            accept="video/mp4,video/webm,.mp4,.webm"
            aria-label="Choose CCTV recording"
            disabled={busy}
            onChange={(event) => {
              chooseFile(event.target.files?.[0]);
              event.target.value = "";
            }}
          />
        </div>
        {devices.length > 1 && (
          <label className="ld-camera-select">
            Existing video input
            <select
              value={deviceId}
              disabled={busy || phase === "preparing"}
              onChange={(event) => setDeviceId(event.target.value)}
            >
              <option value="">Browser default</option>
              {devices.map((device, index) => (
                <option key={device.deviceId} value={device.deviceId}>
                  {device.label || `Camera ${index + 1}`}
                </option>
              ))}
            </select>
            <small>Choose, then click Connect camera.</small>
          </label>
        )}
        <details className="ld-source-help">
          <summary>CCTV app, browser, recorder or separate monitor?</summary>
          <p>
            For a CCTV app or browser viewer, share its camera window and keep
            this detection tab visible. Select video only; source audio is not
            captured. A recorder shown only on a separate physical monitor needs
            its existing laptop viewer or a supported software feed first. An
            embedded YouTube player cannot feed this detector directly.
          </p>
          <p>
            Local recordings: MP4 or WebM, up to 250 MiB, 10 minutes and 4K.
            Pose tracking saves event metadata. Optional product interaction
            analysis sends sampled frames to the local service for review.
            Sharing permission is required each time. Keep patient screens out
            of the selected view.
          </p>
        </details>
      </section>
      {error && (
        <div className="ld-error" role="alert">
          <CircleAlert size={20} />
          <span>{error}</span>
        </div>
      )}
      {activeAlert && (
        <div className="ld-active-alert" role="alert">
          <BellRing size={27} />
          <div>
            <strong>Attention needed · {activeAlert.label}</strong>
            <p>{activeAlert.detail}</p>
          </div>
          <button type="button" onClick={() => silence()}>
            <VolumeX size={17} /> Silence alarm
          </button>
        </div>
      )}
      <section
        className={`ld-monitor ${activeAlert ? "ld-monitor-alert" : ""}`}
        aria-label="CCTV detection monitor"
      >
        <div className="ld-monitor-top">
          <span className={`ld-state ld-state-${phase}`}>
            <i />
            {phase === "running"
              ? "POSE TRACKING RUNNING"
              : phase === "degraded"
                ? "POSE ANALYSIS DELAYED"
                : phase === "loading"
                  ? "LOADING POSE MODEL"
                  : phase === "preparing"
                    ? "CONNECTING"
                    : "DETECTION STOPPED"}
          </span>
          <span>
            {source ? sourceNames[source.kind] : "No source connected"}
          </span>
        </div>
        <div
          className={`ld-product-status ld-product-status-${productStatus.state}`}
        >
          <strong role="status">{productStatus.label}</strong>
          <p>
            {productStatus.guidance}{" "}
            <a href="#interaction-heading">Product analysis settings</a>
          </p>
        </div>
        <div className="ld-camera-context">
          <div>
            <strong>
              {selectedCamera?.sourceKey ===
              (source?.url ?? source?.stream?.id ?? "")
                ? selectedCamera?.label
                : "Full source · camera area not selected"}
            </strong>
            <span>
              {selectedCamera?.sourceKey ===
              (source?.url ?? source?.stream?.id ?? "")
                ? "Body tracking and product sampling use this camera area."
                : "Choose the camera picture to exclude browser controls and improve available detail."}
            </span>
          </div>
          <button
            type="button"
            disabled={!source?.ready}
            onClick={showCameraSetup}
          >
            Choose camera area
          </button>
        </div>
        <div
          className="ld-screen"
          style={{ aspectRatio: `${dimensions.width}/${dimensions.height}` }}
        >
          <video
            ref={videoRef}
            muted
            playsInline
            controls={source?.kind === "RECORDED_VIDEO"}
            aria-label="CCTV detection video"
            onLoadedData={mediaReady}
            onResize={() => {
              const video = videoRef.current;
              if (
                runningRef.current &&
                video &&
                (video.videoWidth !== dimensions.width ||
                  video.videoHeight !== dimensions.height)
              ) {
                mediaReady();
                stop(
                  "Video dimensions changed. Check the camera area and start detection again.",
                  false,
                );
                return;
              }
              mediaReady();
            }}
            onEmptied={() => {
              if (runningRef.current || loadingRef.current)
                stop("The video source was removed. Reconnect to continue.");
            }}
            onEnded={() =>
              stop(
                "Recording finished. All generated events are listed below.",
                false,
              )
            }
            onPause={() => {
              if (runningRef.current)
                stop(
                  "Playback paused. Start detection again to continue.",
                  false,
                );
            }}
            onSeeking={() => {
              if (runningRef.current)
                stop(
                  "Playback position changed. Start detection again to analyse from here.",
                  false,
                );
            }}
            onRateChange={() => {
              if (runningRef.current && videoRef.current?.playbackRate !== 1)
                stop("Detection requires normal 1× playback speed.", false);
            }}
            onError={() => {
              if (sourceRef.current) {
                stop("Video could not be decoded.");
                setError(
                  "Try an MP4 (H.264), WebM, or reconnect the CCTV source.",
                );
              }
            }}
          />
          <canvas
            ref={canvasRef}
            aria-label="Person boxes, pose keypoints and activity labels"
          />
          {!source && (
            <div className="ld-screen-empty">
              <Camera size={48} />
              <h2>Your CCTV, with live detection</h2>
              <p>
                Connect a camera view or load a recording.
                <br />
                Person boxes and activity labels appear here.
              </p>
            </div>
          )}
          {phase === "loading" && (
            <div className="ld-loading">
              <span className="ld-spinner" />
              Loading local pose model…
            </div>
          )}
          {source && <span className="ld-source-name">{source.label}</span>}
        </div>
        <div className="ld-monitor-bottom">
          <p role="status">{status}</p>
          <div className="ld-metrics">
            <span>
              <b>{metrics.fps.toFixed(1)}</b> fps
            </span>
            <span>
              <b>{Math.round(metrics.latency)}</b> ms
            </span>
            <span>
              <b>{metrics.frames}</b> frames
            </span>
          </div>
        </div>
      </section>
      <div className="ld-control-row">
        <div className="ld-run-buttons">
          <button
            className="ld-primary"
            type="button"
            disabled={!source?.ready || busy}
            onClick={() => void start()}
          >
            <Play size={18} /> Start detection
          </button>
          <button
            type="button"
            disabled={!source && !busy && phase !== "preparing"}
            onClick={() => {
              sourceGeneration.current++;
              stop();
            }}
          >
            <Square size={17} /> Stop detection
          </button>
        </div>
        <label className="ld-inline-check">
          <input
            type="checkbox"
            checked={skeleton}
            onChange={(event) => setSkeleton(event.target.checked)}
          />
          Show body keypoints
        </label>
        <span className="ld-legend">
          <i />
          Normal pattern <i />
          Watching <i />
          Review alert
        </span>
      </div>
      <div className="ld-settings-grid">
        <section className="ld-settings" aria-labelledby="ld-rule-heading">
          <h2 id="ld-rule-heading">2. Movement rules · pose patterns</h2>
          <label>
            Sensitivity
            <select
              value={sensitivity}
              disabled={busy}
              onChange={(event) =>
                setSensitivity(
                  event.target.value as LiveDetectionSettings["sensitivity"],
                )
              }
            >
              <option value="balanced">Balanced · fewer alerts</option>
              <option value="sensitive">Sensitive · earlier alerts</option>
            </select>
          </label>
          <label className="ld-inline-check">
            <input
              type="checkbox"
              checked={zoneEnabled}
              disabled={busy}
              onChange={(event) => setZoneEnabled(event.target.checked)}
            />
            Alert on restricted-zone entry
          </label>
          {zoneEnabled && (
            <div className="ld-zone-fields">
              {(["x", "y", "width", "height"] as const).map((key) => (
                <label key={key}>
                  {key === "x"
                    ? "Left"
                    : key === "y"
                      ? "Top"
                      : key === "width"
                        ? "Width"
                        : "Height"}{" "}
                  %
                  <input
                    type="number"
                    min={key === "width" || key === "height" ? 5 : 0}
                    max={100}
                    step={1}
                    value={Math.round(zone[key] * 100)}
                    disabled={busy}
                    onChange={(event) => adjustZone(key, event.target.value)}
                  />
                </label>
              ))}
            </div>
          )}
          <p className="ld-hint">
            Experimental pose rules: repeated reach-to-waist and configured
            restricted-zone entry. Review alerts; no theft finding.
          </p>
          <details>
            <summary>What these labels mean</summary>
            <p>
              The model estimates visible body keypoints. Rules follow a person
              within this view and look for repeated hand-to-waist movement or
              entry into your marked zone. They do not recognise an item being
              stolen, establish intent, or identify returning visitors. Ordinary
              shopping can trigger a review alert. Poor angles, hidden arms and
              small figures can prevent detection.
            </p>
            <p>
              Detection runs only while this page is visible and the laptop is
              awake. Fresh browser frames are checked continuously; a gap,
              disconnected source or laptop sleep stops both analyses and sound.
              If a CCTV viewer itself shows a frozen picture while its shared
              window keeps refreshing, check its camera timestamp and reconnect
              the viewer. Stopping detection releases the model and live
              capture. Start again after a pause, seek, sleep or disconnection.
              This prototype also stops monitoring when its 15-minute login
              session expires; sign in again to continue.
            </p>
          </details>
        </section>
        <section className="ld-settings" aria-labelledby="ld-sound-heading">
          <h2 id="ld-sound-heading">3. Attention alarm</h2>
          <label className="ld-inline-check">
            <input
              type="checkbox"
              checked={movementAlarmEnabled}
              onChange={(event) => {
                const next = event.target.checked;
                soundOptions.current.movementAlarmEnabled = next;
                setMovementAlarmEnabled(next);
                if (!next)
                  silence(
                    "Movement-rule sound is off. Product interaction sound has its own control below.",
                  );
              }}
            />
            Sound on movement-rule alerts
          </label>
          <div className="ld-sound-buttons">
            <button
              type="button"
              disabled={busy}
              onClick={() => void testSound()}
            >
              <Volume2 size={18} /> Test speaker sound
            </button>
            <button
              type="button"
              aria-pressed={muted}
              onClick={() => {
                const next = !muted;
                setMuted(next);
                if (next)
                  silence(
                    "Sound muted. Visual alerts and logging remain active.",
                  );
                else setSoundStatus("Sound unmuted for the next new alert.");
              }}
            >
              {muted ? <VolumeX size={18} /> : <Volume2 size={18} />}{" "}
              {muted ? "Unmute" : "Mute"}
            </button>
            <button
              type="button"
              onClick={() => {
                silence("Sound stopped. Visual alerts remain.");
                interactionSilence.current?.();
              }}
            >
              <Square size={17} /> Stop sound
            </button>
          </div>
          <label className="ld-volume">
            Alarm volume <b>{Math.round(volume * 100)}%</b>
            <input
              type="range"
              min={0}
              max={100}
              value={Math.round(volume * 100)}
              onChange={(event) => {
                const value = Number(event.target.value) / 100;
                setVolume(value);
                soundRef.current?.setVolume(value);
                if (!value) silence("Volume is zero. Visual alerts remain.");
              }}
            />
          </label>
          <p className="ld-sound-status" role="status">
            {soundStatus}
          </p>
          <p className="ld-hint">
            An alert requests up to three 8-second attention tones over 28
            seconds. Silence or acknowledge at any time. Browser and laptop
            volume control actual loudness.
          </p>
        </section>
      </div>
      {tracks.length > 0 && (
        <section
          className="ld-track-list"
          aria-label="People currently tracked"
        >
          {tracks.map((track) => (
            <div key={track.id} className={`ld-track ld-track-${track.status}`}>
              <span>Person #{track.id}</span>
              <strong>{track.label}</strong>
              <small>Current view only</small>
            </div>
          ))}
        </section>
      )}
      <InteractionAnalysis
        videoRef={videoRef}
        cancelRef={interactionCancel}
        silenceRef={interactionSilence}
        readSession={() => ({
          runId: runId.current,
          generation: runGeneration.current,
          running:
            runningRef.current &&
            (continuityRef.current?.isFresh(maxResultAgeMs) ?? false),
          source: sourceRef.current,
        })}
        muted={muted}
        volume={volume}
        branchName={branchName}
        sourceKey={source?.url ?? source?.stream?.id ?? ""}
        onMonitorStatus={setProductStatus}
        onCameraSelection={chooseTrackingCamera}
      />
      <section className="ld-event-panel" aria-labelledby="ld-events-heading">
        <div className="ld-event-heading">
          <div>
            <h2 id="ld-events-heading">Detection event log</h2>
            <p>
              Automatically classified observations for {branchName}.
              Acknowledgement records review, not a finding of theft.
            </p>
          </div>
          <button
            type="button"
            disabled={refreshing}
            onClick={() => void refreshEvents()}
          >
            <RefreshCw size={16} /> {refreshing ? "Refreshing…" : "Refresh log"}
          </button>
        </div>
        {logError && (
          <p className="ld-error" role="alert">
            {logError}
          </p>
        )}
        {!events.length && !pending.length && (
          <div className="ld-no-events">
            <BellRing size={24} />
            <strong>No detection events yet</strong>
            <p>
              Events appear here when the running detector observes a configured
              pattern.
            </p>
          </div>
        )}
        {pending.map((item) => (
          <div className="ld-event ld-event-pending" key={item.input.event_id}>
            <div>
              <strong>{item.label}</strong>
              <p>{item.detail}</p>
              <small>
                {sourceNames[item.input.source_kind]} ·{" "}
                {time(item.input.source_time_seconds)} · Person #
                {item.input.track_id}
              </small>
              {item.error && (
                <p role="alert">
                  {item.error} Keep this tab open to retry; this event is not
                  saved yet.
                </p>
              )}
            </div>
            <span>{item.saving ? "Saving metadata…" : "Not saved"}</span>
            {!item.saving && (
              <button type="button" onClick={() => void saveEvent(item)}>
                Retry save
              </button>
            )}
          </div>
        ))}
        {events.map((event) => (
          <div
            className={`ld-event ${event.acknowledged_at ? "ld-event-reviewed" : ""}`}
            key={event.id}
          >
            <div>
              <strong>{event.label}</strong>
              <p>{event.detail}</p>
              <small>
                {new Date(event.detected_at).toLocaleString()} ·{" "}
                {sourceNames[event.source_kind]} · {event.source_label} · Person
                #{event.track_id}
              </small>
              <span className="ld-event-provenance">
                Metadata saved ·{" "}
                {event.sound_requested
                  ? "Sound requested"
                  : "Sound not requested"}{" "}
                · No video clip saved
              </span>
            </div>
            <div className="ld-event-actions">
              <span className="ld-event-time">
                {time(event.source_time_seconds)}
              </span>
              {source?.kind === "RECORDED_VIDEO" &&
                source.label === event.source_label &&
                event.run_id === runId.current && (
                  <button
                    type="button"
                    disabled={busy}
                    onClick={() => {
                      const video = videoRef.current;
                      if (video)
                        video.currentTime = Math.max(
                          0,
                          event.source_time_seconds - 3,
                        );
                    }}
                  >
                    View moment
                  </button>
                )}
              {event.acknowledged_at ? (
                <span className="ld-reviewed">
                  <Check size={15} />
                  Acknowledged
                </span>
              ) : (
                <button
                  type="button"
                  disabled={acknowledging !== null}
                  onClick={() => void acknowledge(event)}
                >
                  {acknowledging === event.id ? "Saving…" : "Acknowledge"}
                </button>
              )}
            </div>
          </div>
        ))}
      </section>
    </div>
  );
}
